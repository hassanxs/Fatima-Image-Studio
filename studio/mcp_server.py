"""Fatima Image Studio as an MCP server, so AI agents (Claude Code, Codex, Antigravity, Hermes, …) can drive it.

The tools call Fatima Image Studio's own HTTP API, so the same server works two ways:
  * built in:  http://127.0.0.1:<port>/mcp  (streamable HTTP; header  Authorization: Bearer <api key>)
  * stdio:     python studio_mcp.py         (for agents that only launch local commands)

Guard rails: agents can't download models or delete anything; non-commercial models are refused unless
allowed in Settings; reference images are only read from the folders listed in Settings; everything an
agent saves goes into the Exports folder from Settings.
"""
import asyncio
import io
import json
import re
from pathlib import Path

import httpx
from mcp.server.fastmcp import FastMCP, Image
from mcp.server.transport_security import TransportSecuritySettings
from PIL import Image as PILImage

INSTRUCTIONS = """Fatima Image Studio generates images on this PC's GPU (FLUX.2 klein, Z-Image Turbo).
Typical flow: list_models / list_presets / list_references -> create_batch -> wait_for_batch -> view_images
-> fix weak images (regenerate_image, edit_image) -> upscale -> export_batch.
Use generate_image for a single quick image. Batches run one image at a time in queue order.
Reference images keep characters consistent; only FLUX.2 models use them. Files you pass must be inside the
folders allowed in Settings (see get_settings). Exports always go to the Exports folder from Settings."""

MAX_REF_BYTES = 30 * 1024 * 1024


class Studio:
    """Thin client for Fatima Image Studio's HTTP API."""

    def __init__(self, base: str, api_key):
        self.base = base.rstrip("/")
        self._key = api_key  # a string, or a function returning the current key (it can change in Settings)

    def client(self, timeout: float = 60) -> httpx.AsyncClient:
        key = self._key() if callable(self._key) else self._key
        return httpx.AsyncClient(base_url=self.base, timeout=timeout,
                                 headers={"X-Studio": "1", "Authorization": f"Bearer {key}"})

    async def call(self, method: str, path: str, timeout: float = 60, **kw):
        async with self.client(timeout) as c:
            r = await c.request(method, path, **kw)
        if r.status_code >= 400:
            try:
                detail = r.json().get("detail")
            except ValueError:
                detail = r.text
            raise ValueError(detail if isinstance(detail, str) else json.dumps(detail))
        return r.json() if r.headers.get("content-type", "").startswith("application/json") else r.content

    # ---- lookups ------------------------------------------------------------

    async def settings(self) -> dict:
        return await self.call("GET", "/api/settings")

    async def batch(self, ref: str) -> dict:
        """A batch by id or (case-insensitive) name; 'latest' means the newest."""
        batches = await self.call("GET", "/api/batches")
        if not batches:
            raise ValueError("There are no batches yet.")
        if ref.lower() in ("latest", "last", "newest"):
            hit = batches[0]
        else:
            hit = next((b for b in batches if b["id"] == ref or b["name"].lower() == ref.lower()), None)
        if not hit:
            raise ValueError(f"No batch called {ref!r}. Use list_batches to see names.")
        return await self.call("GET", f"/api/batches/{hit['id']}")

    async def model(self, name: str | None) -> str | None:
        """Resolve a model id/name to its key, refusing non-commercial ones unless allowed."""
        if not name:
            return None
        state = await self.call("GET", "/api/state")
        models = [m for m in state["models"] if m["installed"]]
        n = name.lower()
        hit = next((m for m in models if n in (m["key"], m["label"].lower(), m.get("api_id", ""))), None) \
            or next((m for m in models if n in m["label"].lower() or n in m["key"]), None)
        if not hit:
            raise ValueError(f"No installed model matches {name!r}. Installed: {', '.join(m['key'] for m in models)}")
        if hit["noncommercial"] and not (await self.settings()).get("agents_noncommercial"):
            raise ValueError(f"{hit['label']} has a non-commercial licence and agents aren't allowed to use it "
                             "(Connect page). Pick another model.")
        return hit["key"]

    async def image_bytes(self, source: str) -> bytes:
        """Reference image from: a named reference, 'batch/file.png', an http(s) link, or an allowed local path."""
        source = source.strip()
        if re.match(r"^https?://", source):
            async with httpx.AsyncClient(follow_redirects=True, timeout=60) as c:
                r = await c.get(source)
                r.raise_for_status()
            if len(r.content) > MAX_REF_BYTES:
                raise ValueError(f"{source} is larger than 30 MB.")
            return r.content
        refs = await self.call("GET", "/api/references")
        if any(x["name"] == source.lower() for x in refs):
            return await self.call("GET", f"/api/references/{source.lower()}/image")
        if "/" in source.replace("\\", "/") and not Path(source).is_absolute():
            batch_ref, _, file = source.replace("\\", "/").rpartition("/")
            try:
                b = await self.batch(batch_ref)
                return await self.call("GET", f"/api/batches/{b['id']}/files/{file}")
            except ValueError:
                pass
        path = Path(source).expanduser()
        if not path.is_absolute():
            raise ValueError(f"Can't find {source!r}: not a named reference, batch image, link or absolute path.")
        s = await self.settings()
        allowed = [Path(d) for d in [*s.get("agent_read_dirs", []), s["batches_dir"], s["exports_dir"]]]
        real = path.resolve()
        if not any(real == a.resolve() or a.resolve() in real.parents for a in allowed):
            raise ValueError(f"{path} isn't in a folder agents may read. Allowed: {', '.join(map(str, allowed))} "
                             "(change them on the Connect page).")
        if not real.is_file():
            raise ValueError(f"No file at {path}.")
        if real.stat().st_size > MAX_REF_BYTES:
            raise ValueError(f"{path} is larger than 30 MB.")
        return real.read_bytes()


def preview(png: bytes, size: int = 640) -> Image:
    img = PILImage.open(io.BytesIO(png)).convert("RGB")
    img.thumbnail((size, size))
    out = io.BytesIO()
    img.save(out, "JPEG", quality=82)
    return Image(data=out.getvalue(), format="jpeg")


def item_view(b: dict, it: dict, folder: str) -> dict:
    prompt = next((p["text"] for p in b["prompts"] if p["n"] == it["prompt"]), "")
    up = it.get("upscale") or {}
    out = {"file": it["file"], "status": it["status"], "seed": it["seed"], "path": str(Path(folder) / it["file"]),
           "prompt": prompt}
    if it.get("kind"):
        out.update(kind=it["kind"], made_from=it["source"], change=it.get("edit_prompt"))
    if it.get("error"):
        out["error"] = it["error"]
    if up:
        out["upscale"] = {"status": up["status"], "factor": up["factor"],
                          **({"path": str(Path(folder) / up["file"])} if up["status"] == "done" else {})}
    return out


def summary(b: dict, items: bool = False) -> dict:
    out = {k: b.get(k) for k in ("id", "name", "status", "total", "done", "failed", "cancelled", "remaining",
                                 "upscaled", "upscale_pending", "eta_seconds", "elapsed_seconds", "folder")}
    s = b["settings"]
    out["settings"] = {"model": s["model"], "size": f"{s['width']}x{s['height']}", "images_per_prompt": s["per_prompt"],
                       "seed": s.get("seed"), "style": (s.get("style") or {}).get("text"),
                       "loras": [f"{l['name']} {l['strength']}" for l in s.get("loras") or []]}
    if items and "items" in b:
        out["images"] = [item_view(b, it, b["folder"]) for it in b["items"]]
    return out


def build(base: str, api_key, *, host_port: int | None = None) -> FastMCP:
    studio = Studio(base, api_key)
    security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=[f"127.0.0.1:{host_port}", f"localhost:{host_port}"] if host_port else ["127.0.0.1:*", "localhost:*"],
        allowed_origins=[f"http://127.0.0.1:{host_port}", f"http://localhost:{host_port}"] if host_port else [])
    mcp = FastMCP("Fatima Image Studio", instructions=INSTRUCTIONS, stateless_http=True, json_response=True,
                  streamable_http_path="/mcp", transport_security=security)

    # ---- discovery ------------------------------------------------------------------

    @mcp.tool()
    async def list_models() -> list[dict]:
        """Installed image models with what each is good at, its licence, and whether it takes reference images."""
        state = await studio.call("GET", "/api/state")
        catalog = {m["key"]: m for m in await studio.call("GET", "/api/models")}
        s = await studio.settings()
        return [{"model": m["key"], "name": m["label"], "default": m["key"] == state["default_model"],
                 "good_for": catalog[m["key"]]["about"], "licence": catalog[m["key"]]["license"],
                 "agents_may_use": not m["noncommercial"] or bool(s.get("agents_noncommercial")),
                 "reference_images": m["refs"], "steps": catalog[m["key"]]["steps"],
                 "loaded": state["engine"]["model"] == m["key"]}
                for m in state["models"] if m["installed"]]

    @mcp.tool()
    async def list_presets() -> list[dict]:
        """Saved presets (size, images per prompt, model, seed, upscale, style, LoRAs, pinned reference count)."""
        return [{"name": p["name"], "settings": p["values"], "pinned_references": p.get("refs", 0)}
                for p in await studio.call("GET", "/api/presets")]

    @mcp.tool()
    async def list_loras() -> list[dict]:
        """LoRA style/character add-ons, the model family each works with, and their trigger words."""
        r = await studio.call("GET", "/api/loras")
        return [{"name": l["name"], "for": r["families"].get(l["family"], "unknown"), "triggers": l["triggers"],
                 "default_strength": l["strength"]} for l in r["items"]]

    @mcp.tool()
    async def list_references() -> list[str]:
        """Named reference images saved in Fatima Image Studio; pass a name wherever a reference image is accepted."""
        return [x["name"] for x in await studio.call("GET", "/api/references")]

    @mcp.tool()
    async def add_reference(name: str, source: str) -> str:
        """Save an image under a short name (e.g. 'egg-character') for later use as a reference.
        source: an http(s) link, a 'batch/file.png', or a file in an allowed folder."""
        data = await studio.image_bytes(source)
        r = await studio.call("POST", "/api/references", files={"file": ("ref.png", data)}, data={"name": name})
        return f"Saved reference {r['name']!r}."

    @mcp.tool()
    async def get_settings() -> dict:
        """Where exports go, which folders reference images may be read from, and the defaults."""
        s = await studio.settings()
        return {"exports_folder": s["exports_dir"], "batches_folder": s["batches_dir"],
                "agent_read_folders": s.get("agent_read_dirs", []), "default_model": s["default_model"],
                "agents_may_use_noncommercial_models": bool(s.get("agents_noncommercial"))}

    # ---- making images --------------------------------------------------------------

    async def lora_settings(loras: list[dict]) -> list[dict]:
        """[{"name", "strength"}] from an agent -> the library ids the app expects."""
        r = await studio.call("GET", "/api/loras")
        out = []
        for e in loras:
            hit = next((l for l in r["items"] if l["name"].lower() == str(e.get("name", "")).lower()), None)
            if not hit:
                raise ValueError(f"No LoRA called {e.get('name')!r}. Use list_loras.")
            out.append({"id": hit["id"], "strength": e.get("strength", hit["strength"]),
                        "use_triggers": e.get("use_triggers", True)})
        return out

    @mcp.tool()
    async def generate_image(prompt: str, size: str = "1024x1024", model: str | None = None, seed: int | None = None,
                             references: list[str] | None = None, loras: list[dict] | None = None,
                             style: str | None = None, images: int = 1) -> list:
        """Make a single image now (made next, ahead of queued batches) and wait for it. It is saved in today's
        Singles batch (named like '2026-10-07_Singles'), so get_batch, edit_image, regenerate_image, upscale
        and export_batch work on it with the returned batch name and file. Returns path(s) and previews.
        size: 'WIDTHxHEIGHT' (multiples of 16, 256-2048). images: 1-4 variants (seed, seed+1, ...).
        references: up to 4 (named references, 'batch/file.png', links or allowed file paths) — with references
        the prompt can be an instruction like 'make it night' to edit that image. loras: [{"name": ..., "strength": 0.8}].
        style: text added before the prompt."""
        try:
            width, height = map(int, size.lower().split("x"))
        except ValueError:
            raise ValueError("size must be 'WIDTHxHEIGHT', e.g. '1024x1024'.")
        if not 1 <= images <= 4:
            raise ValueError("images must be 1-4.")
        settings: dict = {"width": width, "height": height, "count": images, "seed": seed}
        # The default model goes through the same check, so agents never get a non-commercial one unasked.
        settings["model"] = await studio.model(model or (await studio.call("GET", "/api/state"))["default_model"])
        if style:
            settings["style"] = {"text": style, "position": "before"}
        if loras:
            settings["loras"] = await lora_settings(loras)
        files = []
        if references:
            if len(references) > 4:
                raise ValueError("Use at most 4 reference images.")
            files = [(f"ref_{i}", (f"ref{i}.png", await studio.image_bytes(r))) for i, r in enumerate(references)]
        r = await studio.call("POST", "/api/singles", data={"spec": json.dumps({"text": prompt, "settings": settings})},
                              files=files or None)
        batch_id, wanted = r["batch"]["id"], set(r["items"])
        deadline = asyncio.get_running_loop().time() + 900
        while True:
            d = await studio.call("GET", f"/api/batches/{batch_id}")
            mine = [it for it in d["items"] if it["id"] in wanted]
            if all(it["status"] not in ("queued", "running") for it in mine):
                break
            if asyncio.get_running_loop().time() > deadline:
                raise ValueError(f"Still generating after 15 minutes; check get_batch('{d['name']}') later.")
            await asyncio.sleep(1)
        out: list = []
        info = []
        for it in mine:
            entry = {"batch": d["name"], "file": it["file"], "seed": it["seed"], "status": it["status"]}
            if it["status"] == "done":
                entry["path"] = str(Path(d["folder"]) / it["file"])
                out.append(preview(await studio.call("GET", f"/api/batches/{batch_id}/files/{it['file']}")))
            else:
                entry["error"] = it.get("error")
            info.append(entry)
        return [json.dumps(info[0] if len(info) == 1 else info)] + out

    @mcp.tool()
    async def create_batch(prompts: list, name: str | None = None, preset: str | None = None,
                           style: str | None = None, style_position: str = "before", size: str | None = None,
                           images_per_prompt: int | None = None, model: str | None = None, seed: int | None = None,
                           pinned_references: list[str] | None = None, loras: list[dict] | None = None,
                           upscale: str | None = None) -> dict:
        """Queue a batch of images; returns its id and name (use wait_for_batch next).
        prompts: strings, or objects {"text", "size", "images", "seed", "reference"} for per-prompt overrides.
        preset: a preset name to start from (its pinned references are used unless you pass your own).
        style: added to every prompt. pinned_references: up to 4 images every prompt uses (keep a character
        consistent). loras: [{"name", "strength"}]. upscale: '2x' or '4x', optionally ' detailed'
        (e.g. '2x detailed'); runs after generation. Unset values fall back to the preset, then the defaults."""
        if not prompts:
            raise ValueError("Give at least one prompt.")
        settings: dict = {}
        pinned: list[bytes] = []
        if preset:
            presets = await studio.call("GET", "/api/presets")
            p = next((x for x in presets if x["name"].lower() == preset.lower()), None)
            if not p:
                raise ValueError(f"No preset called {preset!r}. Presets: {', '.join(x['name'] for x in presets) or 'none'}")
            v = p["values"]
            if v.get("size"):
                settings["width"], settings["height"] = map(int, v["size"].split("x"))
            settings.update({k2: v[k1] for k1, k2 in (("per_prompt", "per_prompt"), ("model", "model")) if v.get(k1)})
            if v.get("seed") not in (None, ""):
                settings["seed"] = int(v["seed"])
            if v.get("style_text"):
                settings["style"] = {"text": v["style_text"], "position": v.get("style_position", "before")}
            if v.get("upscale"):
                f, m = v["upscale"].split(":")
                settings["upscale"] = {"factor": int(f), "model": m}
            if v.get("loras"):
                settings["loras"] = v["loras"]
            if not pinned_references:
                pinned = [await studio.call("GET", f"/api/presets/{p['id']}/ref/{i}") for i in range(p.get("refs", 0))]
        if size:
            settings["width"], settings["height"] = map(int, size.lower().split("x"))
        if images_per_prompt:
            settings["per_prompt"] = images_per_prompt
        if model:
            settings["model"] = await studio.model(model)
        elif settings.get("model"):
            settings["model"] = await studio.model(settings["model"])
        if seed is not None:
            settings["seed"] = seed
        if style:
            settings["style"] = {"text": style, "position": style_position}
        if upscale:
            m = re.match(r"^\s*([24])x\s*(detailed|illustration)?\s*$", upscale.lower())
            if not m:
                raise ValueError("upscale must be '2x' or '4x', optionally followed by 'detailed'.")
            settings["upscale"] = {"factor": int(m[1]), "model": m[2] or "illustration"}
        if loras:
            settings["loras"] = await lora_settings(loras)
        if pinned_references:
            if len(pinned_references) > 4:
                raise ValueError("Pin at most 4 reference images.")
            pinned = [await studio.image_bytes(r) for r in pinned_references]

        spec_prompts, files = [], []
        for i, p in enumerate(prompts):
            p = {"text": p} if isinstance(p, str) else dict(p)
            entry = {"text": str(p.get("text", ""))}
            if p.get("size"):
                entry["width"], entry["height"] = map(int, str(p["size"]).lower().split("x"))
            if p.get("images"):
                entry["per_prompt"] = int(p["images"])
            if p.get("seed") is not None:
                entry["seed"] = int(p["seed"])
            if p.get("reference"):
                files.append((f"ref_{i}", (f"ref{i}.png", await studio.image_bytes(p["reference"]))))
            spec_prompts.append(entry)
        files += [(f"ref_batch_{k}", (f"pin{k}.png", data)) for k, data in enumerate(pinned)]
        spec = {"name": name, "prompts": spec_prompts, "settings": settings}
        b = await studio.call("POST", "/api/batches", data={"spec": json.dumps(spec)}, files=files or None)
        return summary(b)

    @mcp.tool()
    async def list_batches(limit: int = 20) -> list[dict]:
        """Recent batches, newest first."""
        return [summary(b) for b in (await studio.call("GET", "/api/batches"))[:limit]]

    @mcp.tool()
    async def get_batch(batch: str, include_images: bool = True) -> dict:
        """A batch's status and, with include_images, every image's file path, status, seed and prompt.
        batch: id or name ('latest' for the newest)."""
        return summary(await studio.batch(batch), items=include_images)

    @mcp.tool()
    async def wait_for_batch(batch: str, timeout_seconds: int = 600) -> dict:
        """Wait until a batch has finished (including upscales) or the timeout passes (max 1800 s).
        Returns the summary with image paths; if it's still running, call again."""
        deadline = asyncio.get_running_loop().time() + min(max(timeout_seconds, 5), 1800)
        while True:
            b = await studio.batch(batch)
            if b["status"] not in ("running", "queued") or asyncio.get_running_loop().time() > deadline:
                out = summary(b, items=True)
                out["finished"] = b["status"] not in ("running", "queued")
                return out
            await asyncio.sleep(3)

    @mcp.tool()
    async def view_images(batch: str, files: list[str] | None = None, limit: int = 6) -> list:
        """Look at a batch's images (small previews) to check quality. files: names like '01_a.png';
        default: the first finished ones, up to limit (max 8)."""
        b = await studio.batch(batch)
        done = [it for it in b["items"] if it["status"] == "done"]
        chosen = [it for it in done if not files or it["file"] in files][:min(limit, 8)]
        if not chosen:
            raise ValueError("No finished images match.")
        out: list = []
        for it in chosen:
            png = await studio.call("GET", f"/api/batches/{b['id']}/files/{it['file']}")
            prompt = next((p["text"] for p in b["prompts"] if p["n"] == it["prompt"]), "")
            out += [f"{it['file']} (seed {it['seed']}): {it.get('edit_prompt') or prompt}", preview(png, 512)]
        return out

    @mcp.tool()
    async def edit_image(batch: str, file: str, kind: str, prompt: str = "", strength: float = 0.3,
                         mask: str | None = None) -> dict:
        """Make a new version of a batch image, saved next to it (e.g. 01_a_e1.png).
        kind 'edit': describe a change ('make it night'); layout and style are kept (FLUX.2 only).
        kind 'vary': repaint the same shot; strength 0.1-0.7 = how much may change; prompt optional.
        kind 'inpaint': mask = an image (white = area to change, same size) from an allowed folder or link;
        prompt = what goes there."""
        b = await studio.batch(batch)
        it = next((x for x in b["items"] if x["file"] == file), None)
        if not it:
            raise ValueError(f"{file} isn't in {b['name']}.")
        files = {"mask": ("mask.png", await studio.image_bytes(mask))} if mask else None
        r = await studio.call("POST", f"/api/batches/{b['id']}/items/{it['id']}/edit", files=files,
                              data={"kind": kind, "prompt": prompt, "strength": str(strength)})
        made = next(x for x in (await studio.batch(b["id"]))["items"] if x["id"] == r["item"])
        return {"queued": made["file"], "path": str(Path(b["folder"]) / made["file"]),
                "note": "Use wait_for_batch, then view_images to check it."}

    @mcp.tool()
    async def regenerate_image(batch: str, file: str, new_seed: bool = True) -> str:
        """Generate one batch image again (new_seed=False repeats the same seed). Replaces the file when done."""
        b = await studio.batch(batch)
        it = next((x for x in b["items"] if x["file"] == file), None)
        if not it:
            raise ValueError(f"{file} isn't in {b['name']}.")
        await studio.call("POST", f"/api/batches/{b['id']}/items/{it['id']}/regenerate", json={"new_seed": new_seed})
        return f"Queued {file} to regenerate."

    @mcp.tool()
    async def retry_failed(batch: str) -> str:
        """Queue every failed or cancelled image in a batch again."""
        b = await studio.batch(batch)
        await studio.call("POST", f"/api/batches/{b['id']}/retry")
        return f"Queued {b['failed'] + b['cancelled']} image(s) again."

    @mcp.tool()
    async def upscale(batch: str, files: list[str] | None = None, factor: int = 2, upscaler: str = "illustration") -> str:
        """Upscale finished images 2x or 4x into the batch's upscaled folder (originals kept).
        upscaler: 'illustration' (sharp, fast — cartoons/art) or 'detailed' (photos). files: default all."""
        b = await studio.batch(batch)
        ids = None if files is None else [x["id"] for x in b["items"] if x["file"] in files]
        r = await studio.call("POST", f"/api/batches/{b['id']}/upscale", json={"factor": factor, "model": upscaler, "items": ids})
        return f"Queued {r['queued']} image(s) to upscale {factor}x."

    @mcp.tool()
    async def export_batch(batch: str, format: str = "folder", content: str = "originals",
                           file_names: str = "keep", include_metadata: bool = True) -> dict:
        """Export into the Exports folder from Settings. format: 'folder' (copies), 'zip', 'sheet_pdf' or
        'sheet_png' (contact sheet). content: 'originals', 'upscaled' or 'both'. file_names: 'keep' or
        'prompt' (adds prompt words). include_metadata: batch.json + prompts.txt."""
        b = await studio.batch(batch)
        return await studio.call("POST", f"/api/exports/{b['id']}", timeout=600,
                                 json={"format": format, "content": content, "names": file_names, "extras": include_metadata})

    # ---- queue control ---------------------------------------------------------------

    @mcp.tool()
    async def control_batch(batch: str, action: str) -> str:
        """action: 'pause' (current image finishes), 'resume' or 'cancel' (queued images)."""
        if action not in ("pause", "resume", "cancel"):
            raise ValueError("action must be pause, resume or cancel.")
        b = await studio.batch(batch)
        await studio.call("POST", f"/api/batches/{b['id']}/{action}")
        return f"{action.capitalize()}d {b['name']}." if action != "cancel" else f"Cancelled {b['name']}."

    @mcp.tool()
    async def rename_batch(batch: str, new_name: str) -> str:
        """Rename a batch (its folder is renamed too)."""
        b = await studio.batch(batch)
        r = await studio.call("PATCH", f"/api/batches/{b['id']}", json={"name": new_name})
        return f"Renamed to {r['name']!r}."

    @mcp.tool()
    async def move_in_queue(batch: str, position: str = "next") -> list[str]:
        """Change queue order. position: 'next' (after the batch generating now), 'last', or a 1-based number."""
        state = await studio.call("GET", "/api/state")
        b = await studio.batch(batch)
        ids = [x for x in state["queue"] if x != b["id"]]
        running = (state.get("current") or {}).get("batch")
        if position == "next":
            at = 1 if running and running in ids and ids[0] == running else 0
        elif position == "last":
            at = len(ids)
        else:
            at = max(0, int(position) - 1)
        ids.insert(min(at, len(ids)), b["id"])
        await studio.call("POST", "/api/queue/order", json={"ids": ids})
        names = {x["id"]: x["name"] for x in await studio.call("GET", "/api/batches")}
        return [names.get(i, i) for i in ids]

    @mcp.tool()
    async def load_model(model: str) -> str:
        """Load a model onto the GPU ahead of time (otherwise it loads on the first image)."""
        key = await studio.model(model)
        await studio.call("POST", "/api/engine/load", timeout=600, json={"model": key})
        return f"{key} is loaded."

    @mcp.tool()
    async def unload_model() -> str:
        """Free GPU memory (refused while images are generating)."""
        await studio.call("POST", "/api/engine/unload", timeout=120)
        return "Model unloaded."

    return mcp
