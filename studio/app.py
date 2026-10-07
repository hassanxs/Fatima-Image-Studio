"""HTTP layer: the studio UI's /api routes and the OpenAI-compatible /v1 routes."""
import asyncio
import base64
import contextlib
import datetime as dt
import io
import json
import logging
import os
import random
import shutil
import time
import uuid
import zipfile
from pathlib import Path

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from starlette.background import BackgroundTask
from fastapi.staticfiles import StaticFiles
from PIL import Image
from pydantic import BaseModel, Field

from . import APP_NAME, REPO_URL, __version__, config
from .engine import Engine, EngineError
from . import autostart, hardware, presets
from .downloads import Downloads
from .loras import Loras
from .mcp_server import build as build_mcp
from .references import References, pick_folder
from .store import (MAX_PINNED, PROMPT_OVERRIDES, Store, batch_status, pinned_refs, prompt_of, prompt_refs,
                    settings_for, slug, to_png)
from .worker import Worker

log = logging.getLogger("studio")
WEB = Path(__file__).parent / "web"
THUMBS = config.DATA / "thumbs"
API_OUT = config.DATA / "api_outputs"
EXPORTS = config.DATA / "exports"
MAX_PROMPTS = 2000


def create_app(cfg: dict) -> FastAPI:
    store = Store(cfg["batches_dir"])
    engine = Engine(cfg)
    worker = Worker(store, engine, cfg)
    downloads = Downloads(cfg)
    loras = Loras(cfg)
    references = References()
    mcp = build_mcp(f"http://127.0.0.1:{cfg['port']}", lambda: cfg["api_key"], host_port=cfg["port"])

    @contextlib.asynccontextmanager
    async def lifespan(app):
        async def idle_watch():
            while True:
                await asyncio.sleep(30)
                await engine.unload_if_idle()
        tasks = [asyncio.create_task(worker.run()), asyncio.create_task(idle_watch())]
        log.info("Fatima Image Studio on http://%s:%s  (batches: %s)", cfg["host"], cfg["port"], store.root)
        async with mcp.session_manager.run():  # MCP endpoint for AI agents at /mcp
            yield
        for t in tasks:
            t.cancel()
        await engine.shutdown()

    app = FastAPI(title=APP_NAME, version=__version__, lifespan=lifespan,
                  description="OpenAI-compatible image generation on this machine's GPU, plus bulk batches. "
                              "Authenticate /v1 calls with `Authorization: Bearer <api key>`.")
    # Browser apps may call /v1 with the API key. The UI-only header below is deliberately
    # not allowed cross-origin, which keeps other websites from driving /api.
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"],
                       allow_headers=["Authorization", "Content-Type"])

    @app.middleware("http")
    async def ui_only(request: Request, call_next):
        if request.url.path.startswith("/mcp") and request.headers.get("authorization") != f"Bearer {cfg['api_key']}":
            return JSONResponse({"detail": "Invalid or missing API key (Authorization: Bearer <key>)"}, status_code=401)
        if (request.url.path.startswith("/api/") and request.method not in ("GET", "HEAD")
                and request.headers.get("x-studio") != "1"):
            return JSONResponse({"detail": "Missing X-Studio header"}, status_code=403)
        response = await call_next(request)
        if request.url.path == "/" or request.url.path.endswith((".html", ".js", ".css")):
            response.headers["Cache-Control"] = "no-cache"  # revalidate, so an updated app never runs stale scripts
        return response

    def get_batch(batch_id: str) -> dict:
        b = store.batches.get(batch_id)
        if not b:
            raise HTTPException(404, "No such batch")
        return b

    def summarize(b: dict, full: bool = False) -> dict:
        items = b["items"]
        done = [it for it in items if it["status"] == "done"]
        remaining = sum(it["status"] in ("queued", "running") for it in items)
        avg = sum(it["duration"] for it in done) / len(done) if done else None
        status = batch_status(b)
        out = {
            "id": b["id"], "name": b["name"], "created": b["created"], "status": status,
            "elapsed_seconds": elapsed_seconds(items, running=status == "running"),
            "paused": b["paused"], "settings": b["settings"], "total": len(items),
            "done": len(done), "failed": sum(it["status"] == "failed" for it in items),
            "cancelled": sum(it["status"] == "cancelled" for it in items),
            "remaining": remaining, "avg_seconds": round(avg, 2) if avg else None,
            "eta_seconds": round(avg * remaining) if avg and remaining else None,
            "prompt_count": len(b["prompts"]), "first_prompt": b["prompts"][0]["text"],
            "has_refs": bool(pinned_refs(b)) or any(prompt_refs(p) for p in b["prompts"]),
            "kind": b.get("kind", "batch"),
            "edits": sum(1 for it in items if it.get("kind")),
            "mixed": any(k in p for p in b["prompts"] for k in PROMPT_OVERRIDES),
            "upscaled": sum((it.get("upscale") or {}).get("status") == "done" for it in items),
            "upscale_pending": sum((it.get("upscale") or {}).get("status") in ("queued", "running") for it in items),
            "upscale_failed": sum((it.get("upscale") or {}).get("status") == "failed" for it in items),
            "thumbs": [it["file"] + "?v=" + (it["finished"] or "") for it in done[:4]],
            "folder": str(store.folder(b)),
        }
        if full:
            out.update(prompts=b["prompts"], items=items, batch_refs=pinned_refs(b))
        return out

    # ---- UI API -------------------------------------------------------------

    @app.get("/api/state")
    def state():
        installed = config.installed_models(cfg)
        cur = worker.current
        status = engine.status()
        status["label"] = config.MODELS[engine.model]["label"] if engine.model else None
        return {
            "engine": status,
            "models": [{"key": k, "label": m["label"], "short": m["short"], "installed": k in installed,
                        "refs": m["refs"], "noncommercial": bool(m.get("noncommercial")), "family": m["family"]}
                       for k, m in config.MODELS.items()],
            "default_model": cfg["default_model"],
            "setup_ready": cfg["engine"] in config.installed_engines(cfg) and cfg["default_model"] in installed,
            "upscalers": [{"key": k, "label": u["label"], "hint": u["hint"], "installed": k in config.installed_upscalers(cfg)}
                          for k, u in config.UPSCALERS.items()],
            "task": "upscale" if cur and (cur[1].get("upscale") or {}).get("status") == "running" else ("generate" if cur else None),
            "api_base": f"http://{cfg['host']}:{cfg['port']}/v1",
            "current": {"batch": cur[0]["id"], "item": cur[1]["id"]} if cur else None,
            "api_busy": worker.api_busy,
            "queue": [b["id"] for b in sorted(store.batches.values(), key=lambda b: b["order"])
                      if batch_status(b) in ("running", "queued", "paused")],
        }

    def batches_active() -> bool:
        return bool(worker.current) or any(batch_status(b) in ("running", "queued") for b in store.batches.values())

    async def load_model(key: str) -> dict:
        if key not in config.installed_models(cfg):
            raise HTTPException(400, f"Model '{key}' is not installed")
        try:
            await engine.load(key)
        except EngineError as e:
            raise HTTPException(500, str(e))
        return engine.status()

    async def unload_model() -> dict:
        if batches_active() or worker.api_busy:
            raise HTTPException(409, "Images are still being generated. Pause or finish the batches first.")
        await engine.stop()
        return engine.status()

    class LoadRequest(BaseModel):
        model: str

    @app.post("/api/engine/load")
    async def ui_load(body: LoadRequest):
        return await load_model(body.model)

    @app.post("/api/engine/unload")
    async def ui_unload():
        return await unload_model()

    @app.get("/api/batches")
    def list_batches():
        return [summarize(b) for b in sorted(store.batches.values(), key=lambda b: b["created"], reverse=True)]

    @app.get("/api/batches/{batch_id}")
    def batch_detail(batch_id: str):
        return summarize(get_batch(batch_id), full=True)

    @app.post("/api/batches")
    async def create_batch(request: Request):
        form = await request.form()
        try:
            spec = json.loads(form["spec"])
        except (KeyError, ValueError):
            raise HTTPException(400, "Missing batch spec")
        raw = spec.get("prompts", [])
        if not any(str(p.get("text", "")).strip() for p in raw):
            raise HTTPException(400, "Add at least one prompt.")
        if len(raw) > MAX_PROMPTS:
            raise HTTPException(400, f"At most {MAX_PROMPTS} prompts per batch.")
        settings = validate_settings(spec.get("settings", {}))
        batch_refs = [data for key in ("ref_batch", *(f"ref_batch_{k}" for k in range(MAX_PINNED)))
                      if (data := await read_upload(form.get(key)))][:MAX_PINNED]
        prompts, prompt_refs = [], {}
        for i, p in enumerate(raw):
            text = str(p.get("text", "")).strip()
            if not text:
                continue
            entry = {"text": text}
            where = f"Prompt {len(prompts) + 1}: "
            if p.get("width") is not None or p.get("height") is not None:
                entry["width"], entry["height"] = check_size(p.get("width"), p.get("height"), where)
            if p.get("seed") not in (None, ""):
                entry["seed"] = check_seed(p["seed"], where)
            if p.get("per_prompt") not in (None, ""):
                entry["per_prompt"] = check_per_prompt(p["per_prompt"], where)
            if data := await read_upload(form.get(f"ref_{i}")):
                prompt_refs[len(prompts)] = data
            prompts.append(entry)
        if (batch_refs or prompt_refs) and not config.MODELS[settings["model"]]["refs"]:
            raise HTTPException(400, f"{config.MODELS[settings['model']]['label']} doesn't use reference images. "
                                     "Remove them, or pick a FLUX.2 model.")
        try:
            b = store.create(name=spec.get("name"), prompts=prompts, settings=settings,
                             batch_refs=batch_refs, prompt_refs=prompt_refs)
        except ValueError as e:
            raise HTTPException(400, str(e))
        worker.notify()
        return summarize(b, full=True)

    @app.post("/api/singles")
    async def create_single(request: Request):
        """One image (or a few variants) from one prompt, added to today's Singles batch and made next."""
        form = await request.form()
        try:
            spec = json.loads(form["spec"])
        except (KeyError, ValueError):
            raise HTTPException(400, "Missing spec")
        text = str(spec.get("text") or "").strip()
        if not text:
            raise HTTPException(400, "Describe the image first.")
        if len(text) > 10000:
            raise HTTPException(400, "That prompt is too long.")
        raw = dict(spec.get("settings") or {})
        raw["per_prompt"] = raw.get("count", 1)
        settings = validate_settings(raw)
        refs = [data for k in range(MAX_PINNED) if (data := await read_upload(form.get(f"ref_{k}")))]
        if refs and not config.MODELS[settings["model"]]["refs"]:
            raise HTTPException(400, f"{config.MODELS[settings['model']]['label']} doesn't use reference images. "
                                     "Remove them, or pick a FLUX.2 model.")
        try:
            b, items = store.add_single(text=text, settings=settings, refs=refs,
                                        count=settings["per_prompt"], seed=settings["seed"])
        except ValueError as e:
            raise HTTPException(400, str(e))
        worker.notify()
        return {"batch": summarize(b), "items": [it["id"] for it in items]}

    def validate_settings(s: dict) -> dict:
        installed = config.installed_models(cfg)
        model = s.get("model") or cfg["default_model"]
        if model not in installed:
            raise HTTPException(400, f"Model '{model}' is not installed.")
        width, height = check_size(s.get("width", 1024), s.get("height", 1024))
        per_prompt = check_per_prompt(s.get("per_prompt", 1))
        seed = None if s.get("seed") in (None, "", -1) else check_seed(s["seed"])
        steps = int(s.get("steps") or config.steps_for(cfg, model))
        if not 1 <= steps <= 50:
            raise HTTPException(400, "Steps must be 1–50.")
        out = {"model": model, "width": width, "height": height, "per_prompt": per_prompt, "seed": seed, "steps": steps}
        style = s.get("style") or {}
        if str(style.get("text") or "").strip():
            text = str(style["text"]).strip()
            if len(text) > 2000:
                raise HTTPException(400, "The style text is too long (2000 characters max).")
            out["style"] = {"text": text, "position": "after" if style.get("position") == "after" else "before"}
        if s.get("upscale"):
            out["upscale"] = check_upscale(s["upscale"].get("factor"), s["upscale"].get("model"))
        if s.get("loras"):
            out["loras"] = check_loras(s["loras"], model)
        return out

    def check_loras(entries: list, model: str) -> list[dict]:
        if len(entries) > config.MAX_LORAS:
            raise HTTPException(400, f"Use at most {config.MAX_LORAS} LoRAs per batch.")
        family = config.MODELS[model]["family"]
        out = []
        for e in entries:
            lora = loras.get(str((e or {}).get("id", "")))
            if not lora:
                raise HTTPException(400, "A chosen LoRA is no longer in the library.")
            if lora["family"] != family:
                needs = config.FAMILIES.get(lora["family"], "an unknown model")
                raise HTTPException(400, f"“{lora['name']}” is made for {needs}, not {config.MODELS[model]['label']}."
                                    if lora["family"] else f"Set the base model of “{lora['name']}” on the Models page (LoRA library) first.")
            try:
                strength = round(min(2.0, max(-1.0, float(e.get("strength", lora["strength"])))), 2)
            except (TypeError, ValueError):
                raise HTTPException(400, "LoRA strength must be a number.")
            out.append({"id": lora["id"], "name": lora["name"], "file": lora["file"], "strength": strength,
                        "triggers": lora["triggers"] if e.get("use_triggers", True) else ""})
        return out

    def check_upscale(factor, model) -> dict:
        if factor not in (2, 4):
            raise HTTPException(400, "Upscale must be 2× or 4×.")
        if model not in config.installed_upscalers(cfg):
            raise HTTPException(400, f"Upscaler '{model}' is not installed. Download it on the Models page.")
        return {"factor": factor, "model": model}

    class UpscaleRequest(BaseModel):
        factor: int = 2
        model: str = "illustration"
        items: list[str] | None = None  # None = every finished image

    @app.post("/api/batches/{batch_id}/upscale")
    def upscale_batch(batch_id: str, body: UpscaleRequest):
        b = get_batch(batch_id)
        up = check_upscale(body.factor, body.model)
        items = [it for it in b["items"] if body.items is None or it["id"] in body.items]
        queued = worker.upscale(b, items, up["factor"], up["model"])
        return summarize(b) | {"queued": queued}

    class Rename(BaseModel):
        name: str

    @app.patch("/api/batches/{batch_id}")
    def rename_batch(batch_id: str, body: Rename):
        b = get_batch(batch_id)
        try:
            store.rename(b, body.name)
        except ValueError as e:
            raise HTTPException(409, str(e))
        return summarize(b)

    @app.post("/api/batches/{batch_id}/{action}")
    def batch_action(batch_id: str, action: str):
        b = get_batch(batch_id)
        if action == "pause":
            b["paused"] = True
        elif action == "resume":
            b["paused"] = False
            worker.notify()
        elif action == "cancel":
            worker.cancel(b)
        elif action == "retry":
            worker.retry(b)
        elif action == "rerun":
            if b.get("kind") == "singles":
                raise HTTPException(400, "Single images can't be re-run as a batch. Regenerate them one by one in the viewer.")
            nb = store.rerun(b)
            worker.notify()
            return summarize(nb)
        elif action == "open":
            os.startfile(store.folder(b))
        else:
            raise HTTPException(404, "Unknown action")
        store.save(b)
        return summarize(b)

    def remove_batch(b: dict) -> None:
        if worker.is_busy_with(b) or batch_status(b) in ("running", "queued"):
            raise ValueError("still generating — cancel it first")
        store.delete(b)  # to the Recycle Bin
        shutil.rmtree(THUMBS / b["id"], ignore_errors=True)

    @app.delete("/api/batches/{batch_id}")
    def delete_batch(batch_id: str):
        b = get_batch(batch_id)
        try:
            remove_batch(b)
        except ValueError:
            raise HTTPException(409, "This batch is still generating. Cancel it first, then delete it.")
        except OSError as e:
            raise HTTPException(409, str(e))
        return {"deleted": b["name"]}

    class BulkDelete(BaseModel):
        ids: list[str] = Field(min_length=1, max_length=1000)

    @app.post("/api/batches-delete")
    def delete_batches(body: BulkDelete):
        """Delete several batches at once; ones still generating (or locked by Windows) are skipped."""
        deleted, skipped = [], []
        for batch_id in dict.fromkeys(body.ids):
            b = store.batches.get(batch_id)
            if not b:
                continue
            try:
                remove_batch(b)
                deleted.append(b["name"])
            except (ValueError, OSError) as e:
                skipped.append({"name": b["name"], "reason": str(e)})
        return {"deleted": deleted, "skipped": skipped}

    def get_item(b: dict, item_id: str) -> dict:
        it = next((it for it in b["items"] if it["id"] == item_id), None)
        if not it:
            raise HTTPException(404, "No such image")
        return it

    @app.delete("/api/batches/{batch_id}/items/{item_id}")
    def delete_item(batch_id: str, item_id: str):
        b = get_batch(batch_id)
        it = get_item(b, item_id)
        if worker.is_busy_with(b, it):
            raise HTTPException(409, "That image is being generated right now.")
        try:
            store.delete_item(b, it)
        except OSError as e:
            raise HTTPException(409, str(e))
        (THUMBS / b["id"] / (it["file"] + ".jpg")).unlink(missing_ok=True)
        return summarize(b)

    @app.post("/api/batches/{batch_id}/items/{item_id}/retry")
    def retry_item(batch_id: str, item_id: str):
        b = get_batch(batch_id)
        worker.retry(b, item_id)
        return summarize(b)

    class Regenerate(BaseModel):
        new_seed: bool = False

    @app.post("/api/batches/{batch_id}/items/{item_id}/regenerate")
    def regenerate_item(batch_id: str, item_id: str, body: Regenerate):
        b = get_batch(batch_id)
        try:
            worker.regenerate(b, get_item(b, item_id), body.new_seed)
        except ValueError as e:
            raise HTTPException(409, str(e))
        return summarize(b)

    @app.get("/api/batches/{batch_id}/files/{rel:path}")
    def batch_file(batch_id: str, rel: str, thumb: int = 0):
        b = get_batch(batch_id)
        path = store.file_path(b, rel)
        if not path:
            raise HTTPException(404, "No such file")
        if not thumb:
            return FileResponse(path)
        cached = THUMBS / b["id"] / (rel.replace("/", "_") + ".jpg")
        if not cached.exists() or cached.stat().st_mtime < path.stat().st_mtime:
            cached.parent.mkdir(parents=True, exist_ok=True)
            with Image.open(path) as img:
                img = img.convert("RGB")
                img.thumbnail((384, 384))
                img.save(cached, "JPEG", quality=85)
        return FileResponse(cached)

    # ---- edits, queue order, export ------------------------------------------

    @app.post("/api/batches/{batch_id}/items/{item_id}/edit")
    async def edit_item(batch_id: str, item_id: str, request: Request):
        """Make a new image from this one: 'edit' (instruction), 'vary' (image-to-image) or 'inpaint' (mask)."""
        b = get_batch(batch_id)
        source = get_item(b, item_id)
        if source["status"] != "done":
            raise HTTPException(409, "That image isn't finished yet.")
        form = await request.form()
        kind = str(form.get("kind") or "")
        prompt = str(form.get("prompt") or "").strip()
        if kind not in ("edit", "vary", "inpaint"):
            raise HTTPException(400, "Unknown edit type")
        if kind in ("edit", "inpaint") and not prompt:
            raise HTTPException(400, "Describe the change you want.")
        if kind == "edit" and not config.MODELS[settings_for(b, prompt_of(b, source))["model"]]["refs"]:
            raise HTTPException(400, "This batch's model can't edit by instruction — use Vary or Inpaint.")
        try:
            strength = min(0.95, max(0.05, float(form.get("strength") or 0.3)))
        except ValueError:
            raise HTTPException(400, "Strength must be a number.")
        mask = await read_upload(form.get("mask"))
        if kind == "inpaint" and not mask:
            raise HTTPException(400, "Paint over the area to change first.")
        it = worker.add_edit(b, source, kind=kind, prompt=prompt, strength=strength, mask=mask)
        return summarize(b) | {"item": it["id"]}

    class QueueOrder(BaseModel):
        ids: list[str]

    @app.post("/api/queue/order")
    def queue_order(body: QueueOrder):
        """Reorder batches: the given ids take the queue slots they already occupy, in the new order."""
        chosen = [store.batches[i] for i in body.ids if i in store.batches]
        slots = sorted(b["order"] for b in chosen)
        for b, order in zip(chosen, slots):
            b["order"] = order
            store.save(b)
        worker.notify()
        return {"ok": True}

    def export_name(b: dict, it: dict, rel: str, names: str) -> str:
        if names != "prompt":
            return rel
        prompt = next((p["text"] for p in b["prompts"] if p["n"] == it["prompt"]), "")
        folder, _, file = rel.rpartition("/")
        stem, _, ext = file.rpartition(".")
        return (folder + "/" if folder else "") + f"{stem}_{slug(prompt, 6)}.{ext}"

    @app.get("/api/batches/{batch_id}/export.zip")
    def export_zip(batch_id: str, content: str = "originals", names: str = "keep", extras: int = 1):
        b = get_batch(batch_id)
        folder = store.folder(b)
        EXPORTS.mkdir(parents=True, exist_ok=True)
        path = EXPORTS / f"{b['id']}-{uuid.uuid4().hex[:6]}.zip"
        count = 0
        with zipfile.ZipFile(path, "w", zipfile.ZIP_STORED) as z:  # PNGs are already compressed
            for it in b["items"]:
                files = []
                if content in ("originals", "both") and it["status"] == "done":
                    files.append(it["file"])
                up = it.get("upscale") or {}
                if content in ("upscaled", "both") and up.get("status") == "done":
                    files.append(up["file"])
                for rel in files:
                    if (folder / rel).exists():
                        z.write(folder / rel, export_name(b, it, rel, names))
                        count += 1
            if extras:
                z.write(folder / "batch.json", "batch.json")
                z.writestr("prompts.txt", "\n".join(p["text"] for p in b["prompts"]) + "\n")
        if not count:
            path.unlink(missing_ok=True)
            raise HTTPException(404, "Nothing to export with those options.")
        return FileResponse(path, filename=f"{b['name']}.zip", media_type="application/zip",
                            background=BackgroundTask(path.unlink, missing_ok=True))

    @app.get("/api/batches/{batch_id}/contact-sheet.{fmt}")
    def contact_sheet(batch_id: str, fmt: str):
        if fmt not in ("png", "pdf"):
            raise HTTPException(404, "Use .png or .pdf")
        b = get_batch(batch_id)
        data = render_contact_sheet(b, store.folder(b), fmt)
        return Response(data, media_type="application/pdf" if fmt == "pdf" else "image/png",
                        headers={"Content-Disposition": f'attachment; filename="{b["name"]} contact sheet.{fmt}"'})

    # ---- LoRA library ---------------------------------------------------------

    def queued_settings():
        """Settings of every image still waiting or running."""
        for b in store.batches.values():
            for it in b["items"]:
                if it["status"] in ("queued", "running"):  # paused batches count: they'll resume
                    yield settings_for(b, prompt_of(b, it))

    def lora_in_use(lora_id: str) -> bool:
        return any(any(l["id"] == lora_id for l in s.get("loras") or []) for s in queued_settings())

    @app.get("/api/loras")
    def list_loras():
        return {"items": loras.all(), "jobs": loras.jobs, "families": config.FAMILIES}

    class LoraUrl(BaseModel):
        url: str
        name: str | None = None
        triggers: str | None = None

    @app.post("/api/loras/resolve")
    async def resolve_lora(body: LoraUrl):
        try:
            return await loras.resolve_hf(body.url)
        except ValueError as e:
            raise HTTPException(400, str(e))
        except httpx.HTTPError as e:
            raise HTTPException(502, f"Couldn't reach Hugging Face: {e}")

    @app.post("/api/loras/import")
    async def import_lora(body: LoraUrl):
        found = await resolve_lora(body)
        return {"job": loras.start_import(found, body.name, body.triggers)}

    @app.post("/api/loras/upload")
    async def upload_lora(request: Request):
        form = await request.form()
        upload = form.get("file")
        if not hasattr(upload, "read"):
            raise HTTPException(400, "Choose a .safetensors file.")
        try:
            return loras.add_upload(upload.filename or "lora.safetensors", await upload.read(),
                                    str(form.get("name") or "").strip(), str(form.get("triggers") or "").strip())
        except ValueError as e:
            raise HTTPException(400, str(e))

    @app.patch("/api/loras/{lora_id}")
    async def edit_lora(lora_id: str, request: Request):
        try:
            return loras.update(lora_id, await request.json())
        except KeyError:
            raise HTTPException(404, "No such LoRA")
        except (TypeError, ValueError):
            raise HTTPException(400, "Strength must be a number.")

    @app.delete("/api/loras/{lora_id}")
    def delete_lora(lora_id: str):
        if lora_in_use(lora_id):
            raise HTTPException(409, "A batch in the queue uses this LoRA. Finish or cancel it first.")
        loras.delete(lora_id)
        return {"deleted": lora_id}

    # ---- model manager ------------------------------------------------------

    @app.get("/api/models")
    def list_models():
        return downloads.status()

    @app.post("/api/models/{key}/download")
    async def download_model(key: str):  # async: the download runs as a task on this event loop
        try:
            downloads.start(key)
        except ValueError as e:
            raise HTTPException(404, str(e))
        return downloads.status()

    @app.post("/api/models/{key}/cancel")
    async def cancel_download(key: str):
        downloads.cancel(key)
        return downloads.status()

    async def reload_for_upscaler():
        if engine.model and not worker.current and not worker.api_busy:
            await engine.stop()  # the next image restarts it and it picks up the new upscaler

    downloads.on_upscaler_installed = reload_for_upscaler

    @app.get("/api/upscalers")
    def list_upscalers():
        return downloads.upscalers()

    @app.post("/api/upscalers/{key}/{action}")
    async def upscaler_action(key: str, action: str):
        if key not in config.UPSCALERS:
            raise HTTPException(404, "Unknown upscaler")
        try:
            if action == "download":
                downloads.start_upscaler(key)
            elif action == "cancel":
                downloads.cancel("upscaler:" + key)
            elif action in ("discard", "remove"):
                if any((it.get("upscale") or {}).get("status") in ("queued", "running") and
                       (it.get("upscale") or {}).get("model") == key
                       for b in store.batches.values() for it in b["items"]):
                    raise HTTPException(409, "Upscales in the queue use it. Let them finish first.")
                downloads.delete_upscaler(key)
            else:
                raise HTTPException(404, "Unknown action")
        except RuntimeError as e:
            raise HTTPException(409, str(e))
        return downloads.upscalers()

    @app.delete("/api/models/{key}/partial")
    async def discard_download(key: str):
        if key not in config.MODELS:
            raise HTTPException(404, "Unknown model")
        try:
            removed = downloads.discard(key)
        except RuntimeError as e:
            raise HTTPException(409, str(e))
        return {"removed": removed, "models": downloads.status()}

    @app.delete("/api/models/{key}")
    async def delete_model(key: str):
        if key not in config.MODELS:
            raise HTTPException(404, "Unknown model")
        if key == cfg["default_model"]:
            raise HTTPException(409, "That's the default model. Pick another default in Settings first.")
        if any(s["model"] == key for s in queued_settings()):
            raise HTTPException(409, "A batch in the queue uses this model. Finish or cancel it first.")
        if engine.model == key:
            await engine.stop()
        return {"removed": downloads.delete(key), "models": downloads.status()}

    # ---- setup: hardware, engine build, recommended model, speed test ------

    def setup_state() -> dict:
        hw = hardware.detect()
        eng = cfg["engine"]
        rec_engine = hardware.recommended_engine(hw)
        installed_eng = config.installed_engines(cfg)
        rec_model = hardware.recommended_model(hw, eng)
        models = {m["key"]: m for m in downloads.status()}
        free = hardware.disk_free(cfg)
        mode = cfg.get("low_vram", "auto")
        return {
            "hardware": hw, "gpu": hardware.main_gpu(hw), "disk_free": free,
            "warnings": hardware.warnings(hw, cfg, eng),
            "engine": eng, "engine_release": config.ENGINE_RELEASE,
            "engines": [{"key": k, "label": e["label"], "about": e["about"],
                         "size": sum(z[1] for z in e["zips"]), "installed": k in installed_eng,
                         "active": k == eng and k in installed_eng, "recommended": k == rec_engine,
                         "partial": downloads.engine_partial(k), "job": downloads.jobs.get("engine:" + k)}
                        for k, e in config.ENGINES.items()],
            "models": [models[k] | {"fit": hardware.model_fit(k, hw, eng), "recommended": k == rec_model,
                                    "default": k == cfg["default_model"],
                                    "enough_disk": free is None or models[k]["installed"] or free > models[k]["to_download"] + 1e9}
                       for k in config.MODELS],
            "low_vram": mode,
            "low_vram_active": mode == "on" or (mode == "auto" and hardware.low_vram(hw, eng)),
            "benchmark": cfg.get("benchmark"),
            "steps": {"engine": eng in installed_eng, "model": cfg["default_model"] in config.installed_models(cfg),
                      "benchmark": bool(cfg.get("benchmark"))},
            "ready": eng in installed_eng and cfg["default_model"] in config.installed_models(cfg),
        }

    @app.get("/api/setup")
    def get_setup(refresh: bool = False):
        if refresh:
            hardware.detect(refresh=True)
        return setup_state()

    @app.post("/api/setup/engines/{key}/{action}")
    async def engine_action(key: str, action: str):
        if key not in config.ENGINES:
            raise HTTPException(404, "Unknown engine")
        if action == "download":
            downloads.start_engine(key)
        elif action == "cancel":
            downloads.cancel("engine:" + key)
        elif action == "discard":
            try:
                downloads.discard_engine(key)
            except RuntimeError as e:
                raise HTTPException(409, str(e))
        elif action == "use":
            if key not in config.installed_engines(cfg):
                raise HTTPException(400, "Download that engine first.")
            if key != cfg["engine"]:
                await engine.stop()  # waits for the current image, then the next one starts on the new engine
                cfg["engine"] = key
                cfg.pop("benchmark", None)  # speeds were measured on the old engine
                config.save(cfg)
        elif action == "remove":
            if key == cfg["engine"]:
                raise HTTPException(409, "That engine is in use. Switch to another one first.")
            await asyncio.to_thread(downloads.delete_engine, key)
        else:
            raise HTTPException(404, "Unknown action")
        return setup_state()

    @app.post("/api/setup/benchmark")
    async def benchmark():
        model = cfg["default_model"]
        if model not in config.installed_models(cfg):
            raise HTTPException(400, "Download a model first.")
        if batches_active():
            raise HTTPException(409, "Wait for the running batches to finish, or pause them, then test.")
        try:
            async def one(size: int) -> float:
                t0 = time.monotonic()
                await worker.submit(model=model, prompt="a lighthouse on a rocky coast at sunset, detailed",
                                    width=size, height=size, seed=42, steps=config.steps_for(cfg, model))
                return time.monotonic() - t0

            t0 = time.monotonic()
            await engine.load(model)
            await one(1024)  # the first image reads the weights and sizes the buffers, so warm up first
            load_s = time.monotonic() - t0
            times = {size: await one(size) for size in (512, 1024)}
        except EngineError as e:
            raise HTTPException(500, str(e))
        cfg["benchmark"] = {"model": model, "engine": cfg["engine"], "load_s": round(load_s, 1),
                            "s512": round(times[512], 1), "s1024": round(times[1024], 1),
                            "low_vram": setup_state()["low_vram_active"], "at": int(time.time())}
        config.save(cfg)
        return setup_state()

    @app.get("/api/settings")
    def get_settings():
        return {k: cfg[k] for k in sorted(config.EDITABLE)} | {"engine_dir": str(config.engine_dir(cfg)),
                                                              "models_dir": cfg["models_dir"],
                                                              "start_with_windows": autostart.enabled(),
                                                              "python_exe": autostart.python_console(),
                                                              "mcp_script": str(config.ROOT / "studio_mcp.py"),
                                                              "version": __version__, "repo_url": REPO_URL}

    @app.put("/api/settings")
    async def put_settings(request: Request):
        body = await request.json()
        if "start_with_windows" in body and bool(body["start_with_windows"]) != autostart.enabled():
            autostart.set_enabled(bool(body["start_with_windows"]))
        changes = {k: v for k, v in body.items() if k in config.EDITABLE and v != cfg[k]}
        if "default_model" in changes and changes["default_model"] not in config.MODELS:
            raise HTTPException(400, "Unknown model")
        if "api_key" in changes:
            changes["api_key"] = str(changes["api_key"]).strip()
            if len(changes["api_key"]) < 8 or any(c.isspace() for c in changes["api_key"]):
                raise HTTPException(400, "The API key needs at least 8 characters and no spaces.")
        if "batches_dir" in changes:
            if batches_active():
                raise HTTPException(409, "Finish or cancel running batches before moving the batches folder.")
            try:
                store.set_root(changes["batches_dir"])
            except OSError as e:
                raise HTTPException(400, f"Can't use that folder: {e}")
        for key in ("notify", "agents_noncommercial"):
            if key in changes:
                changes[key] = bool(changes[key])
        if "exports_dir" in changes:
            try:
                Path(changes["exports_dir"]).mkdir(parents=True, exist_ok=True)
            except OSError as e:
                raise HTTPException(400, f"Can't use that exports folder: {e}")
        if "agent_read_dirs" in changes:
            dirs = changes["agent_read_dirs"]
            if not isinstance(dirs, list) or not all(isinstance(d, str) and d.strip() for d in dirs):
                raise HTTPException(400, "Agent folders must be a list of folder paths.")
            changes["agent_read_dirs"] = list(dict.fromkeys(d.strip() for d in dirs))
        for key in ("steps", "idle_unload_minutes", "port"):
            if key in changes:
                changes[key] = int(changes[key])
        if "low_vram" in changes and changes["low_vram"] not in ("auto", "on", "off"):
            raise HTTPException(400, "low_vram must be auto, on or off")
        cfg.update(changes)
        config.save(cfg)
        if "low_vram" in changes and engine.model:
            await engine.stop()  # the next image starts the engine with the new memory flags
        return get_settings() | {"restart_needed": "port" in changes}

    @app.get("/api/presets")
    def list_presets():
        return presets.load()

    @app.post("/api/presets")
    async def save_preset(request: Request):
        form = await request.form()
        try:
            spec = json.loads(form["spec"])
        except (KeyError, ValueError):
            raise HTTPException(400, "Missing preset spec")
        refs = [to_png(data) for k in range(MAX_PINNED) if (data := await read_upload(form.get(f"ref_{k}")))]
        try:
            return presets.save(str(spec.get("name", "")), spec.get("values") or {}, refs)
        except ValueError as e:
            raise HTTPException(400, str(e))

    @app.delete("/api/presets/{preset_id}")
    def delete_preset(preset_id: str):
        presets.delete(preset_id)
        return {"deleted": preset_id}

    @app.get("/api/presets/{preset_id}/ref/{n}")
    def preset_ref(preset_id: str, n: int):
        files = presets.ref_files(preset_id)
        if not 0 <= n < len(files):
            raise HTTPException(404, "No reference image")
        return FileResponse(files[n], headers={"Cache-Control": "no-cache"})

    def lora_ids(entries: list) -> list[dict]:
        """[{name or id, strength}] -> [{id, strength}] using the library."""
        out = []
        for e in entries:
            e = e if isinstance(e, dict) else {"name": str(e)}
            match = next((l for l in loras.all() if e.get("id") == l["id"]
                          or str(e.get("name", "")).lower() == l["name"].lower()), None)
            if not match:
                raise HTTPException(400, f"No LoRA called {e.get('name') or e.get('id')!r} in the library")
            out.append({"id": match["id"], "strength": e.get("strength", match["strength"]),
                        "use_triggers": e.get("use_triggers", True)})
        return out

    # ---- folders, named references, export to folder ------------------------

    @app.post("/api/browse-folder")
    async def browse_folder(request: Request):
        """Open the Windows folder picker on this PC (a web page can't see full paths)."""
        start = str((await request.json()).get("start") or "")
        return {"path": await asyncio.to_thread(pick_folder, start)}

    @app.get("/api/references")
    def list_references():
        return references.all()

    @app.post("/api/references")
    async def add_reference(request: Request):
        form = await request.form()
        upload = form.get("file")
        if not hasattr(upload, "read"):
            raise HTTPException(400, "Choose an image.")
        try:
            return references.add(str(form.get("name") or ""), to_png(await upload.read()))
        except ValueError as e:
            raise HTTPException(400, str(e))

    @app.delete("/api/references/{name}")
    def delete_reference(name: str):
        references.delete(name)
        return {"deleted": name}

    @app.get("/api/references/{name}/image")
    def reference_image(name: str):
        path = references.path(name)
        if not path:
            raise HTTPException(404, f"No reference called {name!r}")
        return FileResponse(path, headers={"Cache-Control": "no-cache"})

    class ExportRequest(BaseModel):
        format: str = "folder"  # folder | zip | sheet_pdf | sheet_png
        content: str = "originals"
        names: str = "keep"
        extras: bool = True

    @app.post("/api/exports/{batch_id}")
    def export_to_folder(batch_id: str, body: ExportRequest):
        """Write an export into the Exports folder from Settings (what agents use)."""
        b = get_batch(batch_id)
        root = Path(cfg["exports_dir"])
        root.mkdir(parents=True, exist_ok=True)
        folder = store.folder(b)
        if body.format in ("sheet_pdf", "sheet_png"):
            fmt = body.format.split("_")[1]
            path = root / f"{b['name']} contact sheet.{fmt}"
            path.write_bytes(render_contact_sheet(b, folder, fmt))
            return {"path": str(path)}
        files = []
        for it in b["items"]:
            up = it.get("upscale") or {}
            if body.content in ("originals", "both") and it["status"] == "done":
                files.append((it, it["file"]))
            if body.content in ("upscaled", "both") and up.get("status") == "done":
                files.append((it, up["file"]))
        if not files:
            raise HTTPException(404, "Nothing to export with those options.")
        if body.format == "zip":
            path = root / f"{b['name']}.zip"
            with zipfile.ZipFile(path, "w", zipfile.ZIP_STORED) as z:
                for it, rel in files:
                    z.write(folder / rel, export_name(b, it, rel, body.names))
                if body.extras:
                    z.write(folder / "batch.json", "batch.json")
                    z.writestr("prompts.txt", "\n".join(p["text"] for p in b["prompts"]) + "\n")
            return {"path": str(path), "files": len(files)}
        out = root / b["name"]
        out.mkdir(parents=True, exist_ok=True)
        for it, rel in files:
            target = out / export_name(b, it, rel, body.names)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(folder / rel, target)
        if body.extras:
            shutil.copy2(folder / "batch.json", out / "batch.json")
            (out / "prompts.txt").write_text("\n".join(p["text"] for p in b["prompts"]) + "\n", encoding="utf-8")
        return {"path": str(out), "files": len(files)}

    # ---- OpenAI-compatible API ----------------------------------------------

    def require_key(authorization: str | None = Header(None)):
        if authorization != f"Bearer {cfg['api_key']}":
            raise HTTPException(401, "Invalid or missing API key")

    def model_key(model: str | None) -> str:
        if not model:
            key = cfg["default_model"]
        else:  # our ids, or short names ("...-q4" / "...-q8")
            name = model.lower()
            key = next((k for k, m in config.MODELS.items() if name in (k, m["api_id"])), None)                 or ("q8" if name.endswith("q8") else "q4")
        if key not in config.installed_models(cfg):
            raise HTTPException(400, f"Model '{model}' is not installed")
        return key

    def parse_size(size: str | None, fallback: tuple[int, int] = (1024, 1024)) -> tuple[int, int]:
        if not size or size == "auto":
            return fallback
        try:
            w, h = (int(v) for v in size.lower().split("x"))
        except ValueError:
            raise HTTPException(400, "size must be 'WIDTHxHEIGHT' or 'auto'")
        if not (256 <= w <= 2048 and 256 <= h <= 2048):
            raise HTTPException(400, "size must be between 256 and 2048 on each side")
        return w, h

    async def run_api(n: int, response_format: str, seed: int | None, **params) -> dict:
        data = []
        for i in range(n):
            s = random.randint(0, 2**31 - 1) if seed in (None, -1) else seed + i
            try:
                png = await worker.submit(seed=s, **params)
            except EngineError as e:
                raise HTTPException(500, str(e))
            if response_format == "url":
                API_OUT.mkdir(parents=True, exist_ok=True)
                name = f"{uuid.uuid4().hex}.png"
                (API_OUT / name).write_bytes(png)
                data.append({"url": f"http://{cfg['host']}:{cfg['port']}/files/{name}", "seed": s})
            else:
                data.append({"b64_json": base64.b64encode(png).decode(), "seed": s})
        return {"created": int(time.time()), "data": data}

    class GenerationParams(BaseModel):
        prompt: str = Field(min_length=1, max_length=10000)
        model: str | None = None
        n: int = Field(1, ge=1, le=4)
        size: str | None = None
        seed: int | None = Field(None, ge=-1, lt=2**31)
        steps: int | None = Field(None, ge=1, le=50)
        response_format: str = Field("b64_json", pattern="^(b64_json|url)$")
        loras: list[dict] | None = None  # [{"name" or "id", "strength"}] from the LoRA library

    @app.get("/v1/health")
    def health():
        return {"status": "ok", "engine": engine.state, "model": engine.model}

    @app.get("/v1/models", dependencies=[Depends(require_key)])
    def models():
        return {"object": "list", "data": [
            {"id": config.MODELS[k]["api_id"], "object": "model", "owned_by": "local", "label": config.MODELS[k]["label"]}
            for k in config.installed_models(cfg)]}

    @app.post("/v1/models/{model_id}/load", dependencies=[Depends(require_key)])
    async def api_load(model_id: str):
        return await load_model(model_key(model_id))

    @app.post("/v1/engine/unload", dependencies=[Depends(require_key)])
    async def api_unload():
        return await unload_model()

    @app.post("/v1/images/generations", dependencies=[Depends(require_key)])
    async def generations(p: GenerationParams):
        w, h = parse_size(p.size)
        key = model_key(p.model)
        picked = check_loras(lora_ids(p.loras or []), key) if p.loras else []
        text = ", ".join([*(l["triggers"] for l in picked if l["triggers"]), p.prompt])
        return await run_api(p.n, p.response_format, p.seed, model=key, prompt=text, loras=picked,
                             width=w, height=h, steps=p.steps or config.steps_for(cfg, key), refs=[])

    @app.post("/v1/images/edits", dependencies=[Depends(require_key)])
    async def edits(request: Request):
        form = await request.form()
        prompt = str(form.get("prompt") or "").strip()
        if not prompt:
            raise HTTPException(400, "prompt is required")
        uploads = [f for key in ("image", "image[]") for f in form.getlist(key) if hasattr(f, "read")]
        if not uploads:
            raise HTTPException(400, "Send at least one image (field 'image' or 'image[]')")
        try:
            refs = [to_png(await f.read()) for f in uploads]
        except ValueError as e:
            raise HTTPException(400, str(e))
        with Image.open(io.BytesIO(refs[0])) as first:  # 'auto' size follows the first image
            fallback = tuple(max(256, min(2048, round(v / 16) * 16)) for v in first.size)
        w, h = parse_size(form.get("size"), fallback)
        seed = form.get("seed")
        key = model_key(form.get("model"))
        if not config.MODELS[key]["refs"]:
            raise HTTPException(400, f"{config.MODELS[key]['label']} doesn't use reference images; pick a FLUX.2 model.")
        try:
            wanted = json.loads(form.get("loras") or "[]")
        except ValueError:
            raise HTTPException(400, "loras must be a JSON list")
        picked = check_loras(lora_ids(wanted), key) if wanted else []
        prompt = ", ".join([*(l["triggers"] for l in picked if l["triggers"]), prompt])
        return await run_api(max(1, min(4, int(form.get("n") or 1))),
                             "url" if form.get("response_format") == "url" else "b64_json",
                             int(seed) if seed not in (None, "") else None, model=key, loras=picked,
                             prompt=prompt, width=w, height=h,
                             steps=int(form.get("steps") or config.steps_for(cfg, model_key(form.get("model")))),
                             refs=refs)

    @app.get("/files/{name}")
    def api_file(name: str):
        path = (API_OUT / name).resolve()
        if path.parent != API_OUT.resolve() or not path.is_file():
            raise HTTPException(404, "No such file")
        return FileResponse(path)

    app.router.routes.extend(mcp.streamable_http_app().routes)  # POST/GET /mcp, exact path (no redirect)
    app.mount("/", StaticFiles(directory=WEB, html=True), name="web")
    return app


def render_contact_sheet(b: dict, folder: Path, fmt: str) -> bytes:
    """All finished images in a grid with their file names; PNG is one long page, PDF is A4 pages of 12."""
    from PIL import ImageDraw, ImageFont
    items = [it for it in b["items"] if it["status"] == "done" and (folder / it["file"]).exists()]
    if not items:
        raise HTTPException(404, "No finished images yet.")
    prompts = {p["n"]: p["text"] for p in b["prompts"]}
    try:
        font, small, title = (ImageFont.truetype("segoeui.ttf", n) for n in (22, 18, 34))
    except OSError:
        font = small = title = ImageFont.load_default()
    cols, cell_w, pad = 4, 560, 28
    with Image.open(folder / items[0]["file"]) as first:
        cell_h = round(cell_w * first.height / first.width)
    row_h = cell_h + 86
    per_page = 12 if fmt == "pdf" else len(items)
    pages = []
    for start in range(0, len(items), per_page):
        chunk = items[start:start + per_page]
        rows = -(-len(chunk) // cols)
        page = Image.new("RGB", (cols * cell_w + (cols + 1) * pad, 110 + rows * (row_h + pad)), "#fcfaf5")
        d = ImageDraw.Draw(page)
        d.text((pad, 34), b["name"], fill="#20221e", font=title)
        d.text((page.width - pad, 44), f"{len(items)} images · page {start // per_page + 1}", fill="#74766b", font=small, anchor="ra")
        for i, it in enumerate(chunk):
            x, y = pad + (i % cols) * (cell_w + pad), 110 + (i // cols) * (row_h + pad)
            with Image.open(folder / it["file"]) as img:
                img = img.convert("RGB")
                img.thumbnail((cell_w, cell_h))
                page.paste(img, (x, y))
            d.text((x, y + cell_h + 12), it["file"], fill="#20221e", font=font)
            text = prompts.get(it["prompt"], "")
            if it.get("kind"):  # edited images say what was done to them
                text = f"{it['kind'].capitalize()} of {it['source']}: {it.get('edit_prompt') or text}"
            d.text((x, y + cell_h + 46), text[:62] + ("…" if len(text) > 62 else ""), fill="#74766b", font=small)
        pages.append(page)
    out = io.BytesIO()
    if fmt == "pdf":
        pages[0].save(out, "PDF", resolution=150, save_all=True, append_images=pages[1:])
    else:
        pages[0].save(out, "PNG", optimize=True)
    return out.getvalue()


def check_size(width, height, where: str = "") -> tuple[int, int]:
    try:
        w, h = int(width), int(height)
    except (TypeError, ValueError):
        raise HTTPException(400, f"{where}size needs both a width and a height.")
    if not (256 <= w <= 2048 and 256 <= h <= 2048):
        raise HTTPException(400, f"{where}width and height must be between 256 and 2048.")
    return w, h


def check_seed(seed, where: str = "") -> int:
    try:
        seed = int(seed)
    except (TypeError, ValueError):
        raise HTTPException(400, f"{where}seed must be a whole number.")
    if not 0 <= seed <= 2_000_000_000:
        raise HTTPException(400, f"{where}seed must be between 0 and 2000000000.")
    return seed


def check_per_prompt(n, where: str = "") -> int:
    try:
        n = int(n)
    except (TypeError, ValueError):
        n = 0
    if not 1 <= n <= 8:
        raise HTTPException(400, f"{where}images per prompt must be 1–8.")
    return n


def elapsed_seconds(items: list[dict], running: bool) -> int | None:
    """Time actually spent on the batch: the union of each image's working time, so idle gaps
    (a pause, or regenerating one image hours later) don't count."""
    spans = []
    now = time.time()
    for it in items:
        end = dt.datetime.fromisoformat(it["finished"]).timestamp() if it.get("finished") else None
        if it.get("started"):
            start = dt.datetime.fromisoformat(it["started"]).timestamp()
        elif end and it.get("duration"):  # batches made before "started" was recorded
            start = end - it["duration"]
        else:
            continue
        if it["status"] == "running" or (running and not end) or (end and end < start):
            end = now  # in progress (or an old finish time from before a regenerate)
        if end:
            spans.append((start, end))
    if not spans:
        return None
    total, (cur_start, cur_end) = 0.0, sorted(spans)[0]
    for start, end in sorted(spans)[1:]:
        if start <= cur_end + 5:  # back-to-back images (a few seconds of saving between them)
            cur_end = max(cur_end, end)
        else:
            total += cur_end - cur_start
            cur_start, cur_end = start, end
    return round(total + cur_end - cur_start)


async def read_upload(field) -> bytes | None:
    if field is None or not hasattr(field, "read"):
        return None
    data = await field.read()
    return data or None
