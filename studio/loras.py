"""LoRA library: files in models/loras/, details in data/loras.json.

LoRAs come from a Hugging Face link, an uploaded .safetensors file, or files dropped into the folder.
Each is checked for the base model it was trained for, and FLUX.2 LoRAs are rewritten so every layer
applies in this engine (see lora_convert.py).
"""
import asyncio
import json
import logging
import re
import uuid
from pathlib import Path

import httpx

from . import config
from .lora_convert import FUSED_HIDDEN, convert, detect_family, header_only, needs_conversion
from .store import slug

log = logging.getLogger("studio.loras")
FILE = config.DATA / "loras.json"
HF = "https://huggingface.co"


# A model card's base_model -> family (most specific first; the file's own layers still win).
BASE_MODEL_HINTS = [("flux2-klein-4b", "klein-4b"), ("flux2-klein-9b", "klein-9b"), ("flux2-dev", "flux.2-dev"),
                    ("qwen-image-2.1", "qwen-image-2.1"), ("qwen-image", "qwen-image"), ("z-image", "z-image"),
                    ("chroma", "chroma"), ("flux1", "flux.1"), ("sdxl", "stable-diffusion-xl"), ("sdxl", "sdxl"),
                    ("sd15", "stable-diffusion-v1-5"), ("sd15", "stable-diffusion-v1"), ("sd15", "sd-1.5")]


class Loras:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.jobs: dict[str, dict] = {}  # import id -> {status, name, done, total, error}

    @property
    def folder(self) -> Path:
        return config.lora_dir(self.cfg)

    # ---- library -------------------------------------------------------------

    def _load(self) -> list[dict]:
        try:
            return json.loads(FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []

    def _save(self, items: list[dict]) -> None:
        FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(items, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(FILE)

    def all(self) -> list[dict]:
        """The library, after picking up files added to (or removed from) the folder by hand."""
        self.folder.mkdir(parents=True, exist_ok=True)
        items = [x for x in self._load() if (self.folder / x["file"]).exists()]
        known = {x["file"] for x in items}
        for path in sorted(self.folder.glob("*.safetensors")):
            if path.name not in known:
                items.append(self._register(path, name=path.stem.replace("_", " "), source=None))
        self._save(items)
        return items

    def get(self, lora_id: str) -> dict | None:
        return next((x for x in self.all() if x["id"] == lora_id), None)

    def _register(self, path: Path, *, name: str, source: str | None, triggers: str = "",
                  license_: str | None = None, family: str | None = None) -> dict:
        header, meta = header_only(path)
        fixed = 0
        if needs_conversion(header):
            tmp = path.with_suffix(".converting")
            fixed = convert(path, tmp, hidden=FUSED_HIDDEN.get(detect_family(header) or family, 3072))
            tmp.replace(path)
            header, meta = header_only(path)
        return {
            "id": uuid.uuid4().hex[:10], "name": name[:60], "file": path.name,
            # the file's own layer shapes beat what a model card claims
            "family": detect_family(header) or family, "detected": detect_family(header),
            "triggers": triggers or "", "strength": 1.0,
            "source": source, "license": license_, "size": path.stat().st_size, "fixed_layers": fixed,
        }

    def update(self, lora_id: str, changes: dict) -> dict:
        items = self.all()
        item = next((x for x in items if x["id"] == lora_id), None)
        if not item:
            raise KeyError(lora_id)
        if "name" in changes and str(changes["name"]).strip():
            item["name"] = str(changes["name"]).strip()[:60]
        if "triggers" in changes:
            item["triggers"] = str(changes["triggers"]).strip()[:300]
        if "strength" in changes:
            item["strength"] = round(min(2.0, max(0.0, float(changes["strength"]))), 2)
        if "family" in changes and changes["family"] in config.FAMILIES:
            item["family"] = changes["family"]
        self._save(items)
        return item

    def delete(self, lora_id: str) -> None:
        items = self.all()
        item = next((x for x in items if x["id"] == lora_id), None)
        if item:
            (self.folder / item["file"]).unlink(missing_ok=True)
            self._save([x for x in items if x["id"] != lora_id])

    def _target(self, name: str) -> Path:
        base = slug(name, 8) or "lora"
        path, i = self.folder / f"{base}.safetensors", 2
        while path.exists():
            path, i = self.folder / f"{base}-{i}.safetensors", i + 1
        return path

    # ---- adding ------------------------------------------------------------------

    def add_upload(self, filename: str, data: bytes, name: str, triggers: str) -> dict:
        if not filename.lower().endswith(".safetensors"):
            raise ValueError("Upload a .safetensors LoRA file.")
        self.folder.mkdir(parents=True, exist_ok=True)
        path = self._target(name or Path(filename).stem)
        path.write_bytes(data)
        try:
            item = self._register(path, name=name or Path(filename).stem, source=None, triggers=triggers)
        except Exception as e:
            path.unlink(missing_ok=True)
            raise ValueError("That file isn't a readable LoRA (.safetensors).") from e
        self._save(self.list_without(item["file"]) + [item])
        return item

    def list_without(self, file: str) -> list[dict]:
        return [x for x in self._load() if x["file"] != file]

    async def resolve_hf(self, url: str) -> dict:
        """Look up a Hugging Face LoRA: which file to fetch, its base model, licence and trigger words."""
        m = re.match(r"^(?:https?://huggingface\.co/)?([\w.-]+/[\w.-]+)(?:/(?:blob|resolve)/[^/]+/(.+?))?/?(?:\?.*)?$", url.strip())
        if not m:
            raise ValueError("Paste a Hugging Face link like https://huggingface.co/owner/lora-name")
        repo, wanted = m[1], m[2]
        async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
            r = await client.get(f"{HF}/api/models/{repo}")
            if r.status_code == 404:
                raise ValueError(f"Hugging Face has no model called {repo}.")
            r.raise_for_status()
            info = r.json()
        if info.get("gated"):
            raise ValueError(f"{repo} requires accepting terms on Hugging Face, so it can't be downloaded here. "
                             "Download the file in your browser and use Upload instead.")
        files = [s["rfilename"] for s in info.get("siblings", []) if s["rfilename"].endswith(".safetensors")]
        if wanted:
            files = [f for f in files if f == wanted] or files
        if not files:
            raise ValueError(f"{repo} has no .safetensors file to download.")
        # ComfyUI exports apply most completely in this engine; otherwise take the single/top-level file.
        file = next((f for f in files if "comfy" in f.lower()), None) or sorted(files, key=lambda f: (f.count("/"), len(f)))[0]
        card = info.get("cardData") or {}
        bases = card.get("base_model") or []
        bases = [bases] if isinstance(bases, str) else bases
        family = next((fam for b in bases for fam, key in BASE_MODEL_HINTS if key in b.lower()), None)
        triggers = card.get("instance_prompt") or ""
        if not triggers and card.get("widget"):
            first = str((card["widget"][0] or {}).get("text", ""))
            triggers = first.split(",")[0].strip() if "," in first else ""
        if len(triggers) > 60:  # some cards put a whole example caption here, not a trigger word
            triggers = ""
        return {"repo": repo, "file": file, "family": family, "license": card.get("license"),
                "triggers": triggers, "name": repo.split("/")[-1].replace("-", " ").replace("_", " ")}

    def start_import(self, found: dict, name: str | None, triggers: str | None) -> str:
        job_id = uuid.uuid4().hex[:8]
        self.jobs[job_id] = {"status": "downloading", "name": name or found["name"], "done": 0, "total": 0, "error": None}
        asyncio.create_task(self._import(job_id, found, name or found["name"], triggers if triggers is not None else found["triggers"]))
        return job_id

    async def _import(self, job_id: str, found: dict, name: str, triggers: str) -> None:
        job = self.jobs[job_id]
        self.folder.mkdir(parents=True, exist_ok=True)
        path = self._target(name)
        part = path.with_suffix(".part")
        try:
            url = f"{HF}/{found['repo']}/resolve/main/{found['file']}"
            async with httpx.AsyncClient(follow_redirects=True, timeout=httpx.Timeout(30, read=300)) as client:
                async with client.stream("GET", url) as r:
                    r.raise_for_status()
                    job["total"] = int(r.headers.get("content-length") or 0)
                    with open(part, "wb") as f:
                        async for chunk in r.aiter_bytes(1 << 20):
                            f.write(chunk)
                            job["done"] += len(chunk)
            part.replace(path)
            job["status"] = "converting"
            item = await asyncio.to_thread(self._register, path, name=name, triggers=triggers,
                                           source=f"{HF}/{found['repo']}", license_=found["license"], family=found["family"])
            self._save(self.list_without(item["file"]) + [item])
            job.update(status="done", lora=item["id"])
            log.info("Imported LoRA %s from %s", name, found["repo"])
        except Exception as e:
            log.exception("LoRA import failed")
            part.unlink(missing_ok=True)
            path.unlink(missing_ok=True)
            job.update(status="failed", error=str(e) or e.__class__.__name__)
