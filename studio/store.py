"""Batches on disk: one folder per batch holding its images, refs/ and batch.json.

The folder name IS the batch name, so renaming a batch renames its folder (and a folder
renamed in Explorer shows up under its new name after a restart).
"""
import datetime as dt
import io
import json
import os
import random
import re
import time
import uuid
from collections import Counter
from pathlib import Path

from PIL import Image, ImageOps

from .trash import to_recycle_bin

INVALID_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
STOP_WORDS = {"a", "an", "the", "of", "in", "on", "at", "with", "and", "to", "for", "by", "from", "into"}
VARIANTS = "abcdefgh"
MAX_PINNED = 4  # pinned reference images per batch
PROMPT_OVERRIDES = ("width", "height", "seed", "per_prompt")  # per-prompt settings that beat the batch's
# Single images (Create → Single image) go into one "Singles" batch per day. Each single is a prompt of
# that batch carrying its own settings (model, size, LoRAs, style, upscale…) and its own reference images.
SINGLE_SETTINGS = ("model", "width", "height", "steps", "style", "loras", "upscale")


def clean_name(name: str) -> str:
    """Make a name safe as a Windows folder name: slashes become '-', other invalid characters go."""
    name = INVALID_CHARS.sub("", re.sub(r"[/\\|]", "-", name))
    name = re.sub(r"\s+", " ", name).strip()[:100].rstrip(". ")
    if not name or name.upper() in RESERVED:
        raise ValueError("That name can't be used as a folder name.")
    return name


def slug(text: str, words: int = 4) -> str:
    kept = [w for w in re.findall(r"[a-z0-9]+", text.lower()) if w not in STOP_WORDS]
    return "-".join(kept[:words]) or "batch"


def default_name(first_prompt: str, now: dt.datetime) -> str:
    return now.strftime("%Y-%m-%d_%H%M_") + slug(first_prompt)


def to_png(data: bytes) -> bytes:
    """Validate an uploaded image and normalise it to PNG (respecting EXIF rotation)."""
    try:
        img = ImageOps.exif_transpose(Image.open(io.BytesIO(data)))
    except Exception as e:
        raise ValueError("One of the reference files is not a readable image.") from e
    if img.mode not in ("RGB", "RGBA"):
        img = img.convert("RGBA" if "A" in img.getbands() else "RGB")
    out = io.BytesIO()
    img.save(out, "PNG")
    return out.getvalue()


def compose_prompt(text: str, style: dict | None) -> str:
    """Add the batch style to a prompt, before or after it."""
    if not style or not style.get("text"):
        return text
    return f"{text}, {style['text']}" if style.get("position") == "after" else f"{style['text']}, {text}"


def settings_for(b: dict, prompt: dict | None) -> dict:
    """The settings an image of this prompt is made with: the batch's, plus a single image's own."""
    return b["settings"] | ((prompt or {}).get("settings") or {})


def prompt_of(b: dict, item: dict) -> dict | None:
    return next((p for p in b["prompts"] if p["n"] == item["prompt"]), None)


def prompt_refs(prompt: dict) -> list[str]:
    """A prompt's own reference images (singles can have several; batch prompts have at most one)."""
    return prompt.get("refs") or ([prompt["ref"]] if prompt.get("ref") else [])


def pinned_refs(b: dict) -> list[str]:
    """The batch's pinned reference images (older batches stored a single "batch_ref")."""
    return b.get("batch_refs") or ([b["batch_ref"]] if b.get("batch_ref") else [])


def batch_status(b: dict) -> str:
    c = Counter(it["status"] for it in b["items"])
    c.update((it.get("upscale") or {}).get("status") for it in b["items"])  # queued upscales keep a batch busy
    if c["running"]:
        return "running"
    if c["queued"]:
        return "paused" if b["paused"] else "queued"
    if c["cancelled"] and not c["done"]:
        return "cancelled"
    return "done"


class Store:
    def __init__(self, root: str):
        self.set_root(root)

    def set_root(self, root: str) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.batches: dict[str, dict] = {}
        for folder in self.root.iterdir():
            meta = folder / "batch.json"
            if not (folder.is_dir() and meta.exists()):
                continue
            try:
                b = json.loads(meta.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            b["name"] = folder.name
            for it in b["items"]:
                if it["status"] == "running":  # app closed mid-image
                    it["status"] = "queued"
            self.batches[b["id"]] = b

    def folder(self, b: dict) -> Path:
        return self.root / b["name"]

    def save(self, b: dict) -> None:
        meta = self.folder(b) / "batch.json"
        tmp = meta.with_suffix(".tmp")
        tmp.write_text(json.dumps(b, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(meta)

    def _unique(self, name: str) -> str:
        candidate, i = name, 2
        while (self.root / candidate).exists():
            candidate, i = f"{name}-{i}", i + 1
        return candidate

    def create(self, *, name: str | None, prompts: list[dict], settings: dict,
               batch_refs: list[bytes], prompt_refs: dict[int, bytes]) -> dict:
        """prompts: [{"text", optional overrides width/height/seed/per_prompt}];
        batch_refs: pinned reference images used by every prompt (up to MAX_PINNED);
        prompt_refs: {prompt index (0-based): image bytes}, used on top of the pinned ones."""
        now = dt.datetime.now()
        name = self._unique(clean_name(name) if name and name.strip() else default_name(prompts[0]["text"], now))
        folder = self.root / name
        (folder / "refs").mkdir(parents=True)

        digits = max(2, len(str(len(prompts))))
        pinned = []
        for k, data in enumerate(batch_refs[:MAX_PINNED], 1):
            pinned.append(f"refs/batch_{k}.png")
            (folder / pinned[-1]).write_bytes(to_png(data))
        out_prompts, items = [], []
        # One seed for the whole batch (random unless given) keeps a look consistent across prompts;
        # variant b, c… of a prompt use seed+1, seed+2… so they still differ.
        settings = dict(settings)
        if settings.get("seed") is None:
            settings["seed"], settings["seed_random"] = random.randint(0, 2**31 - 1 - len(VARIANTS)), True
        seed = settings["seed"]
        for i, p in enumerate(prompts):
            n = i + 1
            ref = None
            if i in prompt_refs:
                ref = f"refs/{n:0{digits}d}.png"
                (folder / ref).write_bytes(to_png(prompt_refs[i]))
            overrides = {k: p[k] for k in PROMPT_OVERRIDES if p.get(k) is not None}
            out_prompts.append({"n": n, "text": p["text"], "ref": ref, **overrides})
            base = overrides.get("seed", seed)
            for v in range(overrides.get("per_prompt", settings["per_prompt"])):
                items.append({
                    "id": f"{n}{VARIANTS[v]}", "prompt": n, "file": f"{n:0{digits}d}_{VARIANTS[v]}.png",
                    "seed": base + v,
                    "status": "queued", "error": None, "duration": None, "started": None, "finished": None,
                })
        b = {
            "id": uuid.uuid4().hex[:12], "name": name, "created": now.isoformat(timespec="seconds"),
            "order": time.time(), "paused": False, "settings": settings,
            "batch_refs": pinned,
            "prompts": out_prompts, "items": items,
        }
        self.batches[b["id"]] = b
        self.save(b)
        return b

    def singles_today(self) -> dict | None:
        today = dt.date.today().isoformat()
        return next((b for b in self.batches.values() if b.get("kind") == "singles" and b.get("day") == today), None)

    def add_single(self, *, text: str, settings: dict, refs: list[bytes], count: int, seed: int | None) -> tuple[dict, list[dict]]:
        """Add one single image (or a few variants of it) to today's Singles batch, creating it if needed."""
        now = dt.datetime.now()
        b = self.singles_today()
        if b is None or not self.folder(b).exists():
            name = self._unique(now.strftime("%Y-%m-%d_Singles"))
            (self.root / name / "refs").mkdir(parents=True)
            b = {"id": uuid.uuid4().hex[:12], "name": name, "created": now.isoformat(timespec="seconds"),
                 "order": time.time(), "paused": False, "kind": "singles", "day": now.date().isoformat(),
                 "settings": {**{k: settings.get(k) for k in SINGLE_SETTINGS}, "per_prompt": 1, "seed": None},
                 "batch_refs": [], "prompts": [], "items": []}
            self.batches[b["id"]] = b
        n = max((p["n"] for p in b["prompts"]), default=0) + 1
        ref_paths = []
        for k, data in enumerate(refs[:MAX_PINNED], 1):
            ref_paths.append(f"refs/{n:03d}_{k}.png")
            (self.folder(b) / ref_paths[-1]).write_bytes(to_png(data))
        if seed is None:
            seed = random.randint(0, 2**31 - 1 - len(VARIANTS))
        b["prompts"].append({"n": n, "text": text, "ref": None, "refs": ref_paths, "seed": seed,
                             "per_prompt": count, "created": now.isoformat(timespec="seconds"),
                             "settings": {k: settings[k] for k in SINGLE_SETTINGS if settings.get(k) is not None}})
        items = [{"id": f"{n}{VARIANTS[v]}", "prompt": n, "file": f"{n:03d}_{VARIANTS[v]}.png", "seed": seed + v,
                  "status": "queued", "error": None, "duration": None, "started": None, "finished": None}
                 for v in range(count)]
        b["items"].extend(items)
        b["paused"] = False
        self.save(b)
        return b, items

    def rename(self, b: dict, new_name: str) -> None:
        new_name = clean_name(new_name)
        if new_name == b["name"]:
            return
        # A case-only change is the same folder on Windows; don't treat it as a clash.
        target = new_name if new_name.lower() == b["name"].lower() else self._unique(new_name)
        try:
            os.rename(self.folder(b), self.root / target)
        except OSError as e:
            raise ValueError("Windows wouldn't rename the folder. Close any Explorer window "
                             "or app that has it open, then try again.") from e
        b["name"] = target
        self.save(b)

    def rerun(self, b: dict) -> dict:
        src = self.folder(b)
        read = lambda rel: (src / rel).read_bytes() if rel and (src / rel).exists() else None
        # Same settings, including the batch seed (random or typed), so a re-run reproduces the batch.
        return self.create(
            name=None, settings=dict(b["settings"]),
            prompts=[{k: p[k] for k in ("text", *PROMPT_OVERRIDES) if k in p} for p in b["prompts"]],
            batch_refs=[data for rel in pinned_refs(b) if (data := read(rel))],
            prompt_refs={i: data for i, p in enumerate(b["prompts"]) if (data := read(p["ref"]))},
        )

    def delete(self, b: dict) -> None:
        """Move the batch folder to the Recycle Bin and forget the batch."""
        folder = self.folder(b)
        if folder.exists():
            to_recycle_bin(folder)
        self.batches.pop(b["id"], None)

    def delete_item(self, b: dict, item: dict) -> None:
        """Move one image to the Recycle Bin and drop it from the batch."""
        path = self.folder(b) / item["file"]
        if path.exists():
            to_recycle_bin(path)
        self.drop_upscale(b, item)
        if item.get("mask"):
            (self.folder(b) / item["mask"]).unlink(missing_ok=True)
        b["items"].remove(item)
        self.save(b)

    def drop_upscale(self, b: dict, item: dict) -> None:
        """Forget an image's upscaled copy (it's derived, so it's simply removed)."""
        up = item.pop("upscale", None)
        if up and up.get("file"):
            (self.folder(b) / up["file"]).unlink(missing_ok=True)

    def add_edit(self, b: dict, source: dict, *, kind: str, prompt: str, strength: float,
                 mask: bytes | None, seed: int) -> dict:
        """Queue a new image made from an existing one, placed right after it in the batch."""
        n = 1 + sum(1 for it in b["items"] if it.get("source") == source["id"])
        stem = source["file"].rsplit(".", 1)[0]
        with Image.open(self.folder(b) / source["file"]) as img:
            width, height = img.size
        item = {
            "id": f"{source['id']}-e{n}", "prompt": source["prompt"], "file": f"{stem}_e{n}.png", "seed": seed,
            "status": "queued", "error": None, "duration": None, "started": None, "finished": None,
            "kind": kind, "source": source["id"], "edit_prompt": prompt, "strength": strength,
            "width": width, "height": height, "mask": None,
        }
        if mask:
            item["mask"] = f"refs/mask_{stem}_e{n}.png"
            (self.folder(b) / item["mask"]).write_bytes(to_png(mask))
        siblings = [i for i, x in enumerate(b["items"]) if x is source or x.get("source") == source["id"]]
        b["items"].insert(siblings[-1] + 1, item)  # after the source and its earlier edits
        self.save(b)
        return item

    def file_path(self, b: dict, rel: str) -> Path | None:
        folder = self.folder(b).resolve()
        path = (folder / rel).resolve()
        if folder not in path.parents or not path.is_file():
            return None
        return path
