"""Saved composer settings ("presets"), kept in data/presets.json with up to 4 pinned reference images."""
import json
import re
import uuid

from . import config

FILE = config.DATA / "presets.json"
REFS = config.DATA / "presets"
FIELDS = {"size", "per_prompt", "model", "seed", "upscale", "style_text", "style_position", "loras"}


def load() -> list[dict]:
    try:
        presets = json.loads(FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    for p in presets:
        p["refs"] = len(ref_files(p["id"]))
    return presets


def _save(presets: list[dict]) -> None:
    FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(presets, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(FILE)


def ref_files(preset_id: str) -> list:
    if not re.fullmatch(r"[0-9a-f]{10}", preset_id):
        return []
    legacy = REFS / f"{preset_id}.png"  # presets saved before multiple references
    return ([legacy] if legacy.exists() else []) + sorted(REFS.glob(f"{preset_id}_*.png"))


def save(name: str, values: dict, refs: list[bytes]) -> dict:
    """Create a preset, or overwrite the one with the same name."""
    name = re.sub(r"\s+", " ", name).strip()[:60]
    if not name:
        raise ValueError("Give the preset a name.")
    values = {k: v for k, v in values.items() if k in FIELDS and isinstance(v, (str, int, float, type(None)))
              or k == "loras" and isinstance(v, list) and len(v) <= 3 and all(isinstance(x, dict) for x in v)}
    if len(str(values.get("style_text") or "")) > 2000:
        raise ValueError("The style text is too long (2000 characters max).")
    presets = load()
    existing = next((p for p in presets if p["name"].lower() == name.lower()), None)
    preset = existing or {"id": uuid.uuid4().hex[:10]}
    preset.update(name=name, values=values)
    for old in ref_files(preset["id"]):
        old.unlink()
    REFS.mkdir(parents=True, exist_ok=True)
    for i, data in enumerate(refs, 1):
        (REFS / f"{preset['id']}_{i}.png").write_bytes(data)
    preset["refs"] = len(refs)
    preset.pop("has_ref", None)
    if not existing:
        presets.append(preset)
    presets.sort(key=lambda p: p["name"].lower())
    _save(presets)
    return preset


def delete(preset_id: str) -> None:
    _save([p for p in load() if p["id"] != preset_id])
    for path in ref_files(preset_id):
        path.unlink()
