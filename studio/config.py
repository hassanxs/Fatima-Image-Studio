"""Settings (data/config.json), model presets and the engine's launch flags."""
import ctypes
import json
import os
import secrets
from pathlib import Path

from . import APP_NAME

ROOT = Path(__file__).resolve().parent.parent  # the code
# The installer drops an "installed" marker next to the code. An installed copy keeps models, engine and
# settings in %LOCALAPPDATA% and images in Pictures, so updating or uninstalling the app never touches them.
# Run from source, everything stays inside this folder.
INSTALLED = (ROOT / "installed").exists()


def _pictures() -> Path:
    """The user's Pictures folder, wherever it really is (it may be moved or synced by OneDrive)."""
    import uuid
    folder_id = (ctypes.c_byte * 16).from_buffer_copy(uuid.UUID("33E28130-4E1E-4676-835A-98395C3BC3BB").bytes_le)
    buf = ctypes.c_wchar_p()
    try:
        if ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(folder_id), 0, None, ctypes.byref(buf)) == 0:
            path = Path(buf.value)
            ctypes.windll.ole32.CoTaskMemFree(buf)
            return path
    except (AttributeError, OSError):
        pass
    return Path.home() / "Pictures"


HOME = Path(os.environ.get("LOCALAPPDATA", Path.home())) / APP_NAME if INSTALLED else ROOT
PICTURES = _pictures() / APP_NAME if INSTALLED else ROOT
DATA = HOME / "data"
CONFIG_FILE = DATA / "config.json"

DEFAULTS = {
    "host": "127.0.0.1",
    "port": 9820,
    "api_key": None,  # generated on first run
    "batches_dir": str(PICTURES / ("Batches" if INSTALLED else "batches")),
    "exports_dir": str(PICTURES / ("Exports" if INSTALLED else "exports")),  # where agents (MCP) save images and exports
    # folders agents may read reference images from (plus batches and exports, always)
    "agent_read_dirs": [str(Path.home() / d) for d in ("Pictures", "Downloads", "Desktop")],
    "agents_noncommercial": False,  # may agents use non-commercial models?
    "engine": "cuda",  # which engine build runs the models (ENGINES); picked on the Setup page
    "low_vram": "auto",  # auto | on | off: keep weights in RAM and decode in tiles (slower, fits small GPUs)
    "models_dir": str(HOME / "models"),
    "default_model": "q4",
    "steps": 0,  # 0 = each model's own default
    "notify": True,
    "check_updates": True,  # installed copies look for a newer GitHub release at start and every 6 hours
    "idle_unload_minutes": 10,
    "image_timeout_s": 600,
}

# stable-diffusion.cpp builds, from one pinned release so every engine behaves the same.
ENGINE_RELEASE = "master-929-3f8527a"
_GH = f"https://github.com/leejet/stable-diffusion.cpp/releases/download/{ENGINE_RELEASE}/"
ENGINES = {
    "cuda": {"label": "NVIDIA · CUDA", "about": "The fastest option for NVIDIA graphics cards (GeForce RTX / GTX 16 and newer).",
             "zips": [(_GH + "sd-master-3f8527a-bin-win-cuda12-x64.zip", 337915440),
                      (_GH + "cudart-sd-bin-win-cu12-x64.zip", 563452046)]},
    "vulkan": {"label": "AMD / Intel · Vulkan", "about": "For AMD Radeon and Intel Arc cards. Also works on NVIDIA, but slower than CUDA.",
               "zips": [(_GH + "sd-master-3f8527a-bin-win-vulkan-x64.zip", 30067605)]},
    "cpu": {"label": "CPU only", "about": "No graphics card needed. Works anywhere, but an image takes minutes instead of seconds.",
            "zips": [(_GH + "sd-master-3f8527a-bin-win-cpu-x64.zip", 17486440)]},
}


def engine_dir(cfg: dict, key: str | None = None) -> Path:
    return HOME / "engine" / (key or cfg["engine"])


def installed_engines(cfg: dict) -> list[str]:
    return [k for k in ENGINES if (engine_dir(cfg, k) / "sd-server.exe").exists()]


HF = "https://huggingface.co/"
# Every file a model can need; shared files (text encoders, VAEs) are downloaded once.
FILES = {
    "flux-2-klein-4b-Q4_0.gguf": (HF + "leejet/FLUX.2-klein-4B-GGUF/resolve/main/flux-2-klein-4b-Q4_0.gguf", 2460378560),
    "flux-2-klein-4b-Q8_0.gguf": (HF + "leejet/FLUX.2-klein-4B-GGUF/resolve/main/flux-2-klein-4b-Q8_0.gguf", 4300629440),
    "flux-2-klein-9b-Q4_0.gguf": (HF + "leejet/FLUX.2-klein-9B-GGUF/resolve/main/flux-2-klein-9b-Q4_0.gguf", 5616208032),
    "z_image_turbo-Q4_0.gguf": (HF + "leejet/Z-Image-Turbo-GGUF/resolve/main/z_image_turbo-Q4_0.gguf", 3683370944),
    "Qwen3-4B-Q4_K_M.gguf": (HF + "unsloth/Qwen3-4B-GGUF/resolve/main/Qwen3-4B-Q4_K_M.gguf", 2497281312),
    "Qwen3-4B-Q8_0.gguf": (HF + "unsloth/Qwen3-4B-GGUF/resolve/main/Qwen3-4B-Q8_0.gguf", 4280405792),
    "Qwen3-8B-Q4_K_M.gguf": (HF + "unsloth/Qwen3-8B-GGUF/resolve/main/Qwen3-8B-Q4_K_M.gguf", 5027784512),
    "flux2-vae.safetensors": (HF + "Comfy-Org/flux2-klein-4B/resolve/main/split_files/vae/flux2-vae.safetensors", 336211292),
    "ae.safetensors": (HF + "Comfy-Org/z_image_turbo/resolve/main/split_files/vae/ae.safetensors", 335304388),
}

_FLUX2 = ["--diffusion-fa", "--sage-attn", "--vae-conv-direct", "--cfg-scale", "1.0", "--sampling-method", "euler"]
MODELS = {
    # The two the PoC benchmarked (poc/RESULTS.md).
    "q4": {"label": "FLUX.2 klein 4B · Q4", "short": "FLUX.2 klein 4B · Q4 · fast", "api_id": "flux2-klein-4b-q4", "family": "flux2-klein-4b",
           "diffusion": "flux-2-klein-4b-Q4_0.gguf", "llm": "Qwen3-4B-Q4_K_M.gguf", "vae": "flux2-vae.safetensors",
           "steps": 4, "flags": _FLUX2, "refs": True, "license": "Apache 2.0 — commercial use OK",
           "about": "The default. Fast, good at following prompts and at editing with reference images."},
    "q8": {"label": "FLUX.2 klein 4B · Q8", "short": "FLUX.2 klein 4B · Q8 · quality", "api_id": "flux2-klein-4b-q8", "family": "flux2-klein-4b",
           "diffusion": "flux-2-klein-4b-Q8_0.gguf", "llm": "Qwen3-4B-Q8_0.gguf", "vae": "flux2-vae.safetensors",
           "steps": 4, "flags": _FLUX2, "refs": True, "license": "Apache 2.0 — commercial use OK",
           "about": "Same model at higher precision: slightly cleaner detail, somewhat slower."},
    "zimage": {"label": "Z-Image Turbo · Q4", "short": "Z-Image Turbo · photoreal", "api_id": "z-image-turbo-q4", "family": "z-image",
               "diffusion": "z_image_turbo-Q4_0.gguf", "llm": "Qwen3-4B-Q4_K_M.gguf", "vae": "ae.safetensors",
               "steps": 8, "flags": ["--diffusion-fa", "--cfg-scale", "1.0"], "refs": False,
               "license": "Apache 2.0 — commercial use OK",
               "about": "Strong at photorealism and lettering. No reference images (image-to-image and inpainting still work)."},
    "klein9b": {"label": "FLUX.2 klein 9B · Q4", "short": "FLUX.2 klein 9B · best quality", "api_id": "flux2-klein-9b-q4", "family": "flux2-klein-9b",
                "diffusion": "flux-2-klein-9b-Q4_0.gguf", "llm": "Qwen3-8B-Q4_K_M.gguf", "vae": "flux2-vae.safetensors",
                "steps": 4, "flags": _FLUX2, "refs": True, "noncommercial": True,
                "license": "FLUX Non-Commercial — not for monetized or client work",
                "about": "Bigger, more detailed FLUX.2. Slower, and tight on 8 GB of GPU memory."},
}


# LoRAs only work on the base model they were trained for; models sharing a family can share LoRAs.
FAMILIES = {"flux2-klein-4b": "FLUX.2 klein 4B", "flux2-klein-9b": "FLUX.2 klein 9B", "z-image": "Z-Image"}
MAX_LORAS = 3  # per batch


def lora_dir(cfg: dict) -> Path:
    return Path(cfg["models_dir"]) / "loras"


def steps_for(cfg: dict, model: str) -> int:
    """The Settings override if set, else the model's own tuned step count."""
    return int(cfg.get("steps") or 0) or MODELS[model]["steps"]


def model_files(key: str) -> list[str]:
    m = MODELS[key]
    return [m["diffusion"], m["llm"], m["vae"]]


# Real-ESRGAN upscalers (BSD-3-Clause) in models/upscalers; the file stem is the engine's upscaler name.
# Both are 4x models. Downloaded from the Models page when wanted.
_ESRGAN = "https://github.com/xinntao/Real-ESRGAN/releases/download/"
UPSCALERS = {
    "illustration": {"label": "Illustration", "hint": "Sharp lines, fast — best for cartoons and art",
                     "name": "RealESRGAN_x4plus_anime_6B",
                     "url": _ESRGAN + "v0.2.2.4/RealESRGAN_x4plus_anime_6B.pth", "size": 17938799},
    "detailed": {"label": "Detailed", "hint": "Keeps texture, slower — best for photos",
                 "name": "RealESRGAN_x4plus",
                 "url": _ESRGAN + "v0.1.0/RealESRGAN_x4plus.pth", "size": 67040989},
}


def upscaler_path(cfg: dict, key: str) -> Path:
    return Path(cfg["models_dir"]) / "upscalers" / f"{UPSCALERS[key]['name']}.pth"


def installed_upscalers(cfg: dict) -> list[str]:
    return [k for k in UPSCALERS if upscaler_path(cfg, k).exists()]


EDITABLE = {"batches_dir", "default_model", "steps", "idle_unload_minutes", "port", "api_key", "notify",
            "exports_dir", "agent_read_dirs", "agents_noncommercial", "low_vram", "check_updates"}


def load() -> dict:
    cfg = dict(DEFAULTS)
    if CONFIG_FILE.exists():
        saved = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        if saved.get("steps") == 4 and "notify" not in saved:  # older configs pinned 4; models now bring their own
            saved["steps"] = 0
        saved.pop("engine_dir", None)  # now engine/<engine>, chosen on the Setup page
        cfg.update(saved)
    if not cfg["api_key"]:
        cfg["api_key"] = "sk-local-" + secrets.token_urlsafe(24)
        save(cfg)
    return cfg


def save(cfg: dict) -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    tmp.replace(CONFIG_FILE)


def installed_models(cfg: dict) -> list[str]:
    models_dir = Path(cfg["models_dir"])
    return [key for key in MODELS if all((models_dir / f).exists() for f in model_files(key))]
