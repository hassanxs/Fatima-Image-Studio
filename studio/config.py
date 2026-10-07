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
    "check_updates": True,
    "hf_token": "",  # optional Hugging Face token: faster Xet downloads, higher rate limits, gated models
    "hf_user": "",   # the account that token belongs to (shown in Settings)  # installed copies look for a newer GitHub release at start and every 6 hours
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
    # Qwen-Image 2.1 (ComfyUI-format GGUF, "base" branch), its text encoder, VAE and the encoder's vision part
    "qwen-image-2.1-Q4_K_M.gguf": (HF + "abenzerps/Qwen-Image-2.1-GGUF/resolve/base/qwen-image-2.1-Q4_K_M.gguf", 4604557984),
    "Qwen3VL-8B-Instruct-Q4_K_M.gguf": (HF + "Qwen/Qwen3-VL-8B-Instruct-GGUF/resolve/main/Qwen3VL-8B-Instruct-Q4_K_M.gguf", 5027784800),
    "mmproj-Qwen3VL-8B-Instruct-F16.gguf": (HF + "Qwen/Qwen3-VL-8B-Instruct-GGUF/resolve/main/mmproj-Qwen3VL-8B-Instruct-F16.gguf", 1159029824),
    "qwen_image_2.1_vae_bf16.safetensors": (HF + "Comfy-Org/Qwen-Image-2.1/resolve/main/vae/qwen_image_2.1_vae_bf16.safetensors", 675509688),
    # More models: Qwen-Image / Qwen-Image-Edit (+ Qwen2.5-VL 7B), Z-Image, Chroma, FLUX.1, SDXL, SD 1.5, FLUX.2-dev
    "Qwen_Image-Q4_K_M.gguf": (HF + "QuantStack/Qwen-Image-GGUF/resolve/main/Qwen_Image-Q4_K_M.gguf", 13065746976),
    "qwen-image-edit-2511-Q4_K_M.gguf": (HF + "unsloth/Qwen-Image-Edit-2511-GGUF/resolve/main/qwen-image-edit-2511-Q4_K_M.gguf", 13244758624),
    "Qwen2.5-VL-7B-Instruct.Q4_K_M.gguf": (HF + "mradermacher/Qwen2.5-VL-7B-Instruct-GGUF/resolve/main/Qwen2.5-VL-7B-Instruct.Q4_K_M.gguf", 4683072512),
    "Qwen2.5-VL-7B-Instruct.mmproj-f16.gguf": (HF + "mradermacher/Qwen2.5-VL-7B-Instruct-GGUF/resolve/main/Qwen2.5-VL-7B-Instruct.mmproj-f16.gguf", 1354162912),
    "qwen_image_vae.safetensors": (HF + "Comfy-Org/Qwen-Image_ComfyUI/resolve/main/split_files/vae/qwen_image_vae.safetensors", 253806246),
    "z-image-Q4_K_M.gguf": (HF + "unsloth/Z-Image-GGUF/resolve/main/z-image-Q4_K_M.gguf", 5066995776),
    "Chroma1-HD-Q4_0.gguf": (HF + "silveroxides/Chroma-GGUF/resolve/main/Chroma1-HD/Chroma1-HD-Q4_0.gguf", 5432053920),
    "clip_l.safetensors": (HF + "comfyanonymous/flux_text_encoders/resolve/main/clip_l.safetensors", 246144152),
    "t5xxl_fp8_e4m3fn.safetensors": (HF + "comfyanonymous/flux_text_encoders/resolve/main/t5xxl_fp8_e4m3fn.safetensors", 4893934904),
    "flux1-schnell-q4_0.gguf": (HF + "leejet/FLUX.1-schnell-gguf/resolve/main/flux1-schnell-q4_0.gguf", 6884606880),
    "flux1-kontext-dev-Q4_K_M.gguf": (HF + "QuantStack/FLUX.1-Kontext-dev-GGUF/resolve/main/flux1-kontext-dev-Q4_K_M.gguf", 6931817760),
    "sd_xl_base_1.0.safetensors": (HF + "stabilityai/stable-diffusion-xl-base-1.0/resolve/main/sd_xl_base_1.0.safetensors", 6938078334),
    "sdxl_vae.safetensors": (HF + "madebyollin/sdxl-vae-fp16-fix/resolve/main/sdxl_vae.safetensors", 334641162),
    "v1-5-pruned-emaonly.safetensors": (HF + "stable-diffusion-v1-5/stable-diffusion-v1-5/resolve/main/v1-5-pruned-emaonly.safetensors", 4265146304),
    "flux2-dev-Q4_K_S.gguf": (HF + "city96/FLUX.2-dev-gguf/resolve/main/flux2-dev-Q4_K_S.gguf", 19299128288),
    "Mistral-Small-3.2-24B-Instruct-2506-Q4_K_M.gguf": (HF + "unsloth/Mistral-Small-3.2-24B-Instruct-2506-GGUF/resolve/main/Mistral-Small-3.2-24B-Instruct-2506-Q4_K_M.gguf", 14333922848),
    # Gated on Hugging Face (accept the terms on the model's page; needs a token): SD 3.5, Ideogram 4
    "sd3.5_medium.safetensors": (HF + "stabilityai/stable-diffusion-3.5-medium/resolve/main/sd3.5_medium.safetensors", 5107104286),
    "sd3.5_large.safetensors": (HF + "stabilityai/stable-diffusion-3.5-large/resolve/main/sd3.5_large.safetensors", 16460379262),
    "sd3.5_large_turbo.safetensors": (HF + "stabilityai/stable-diffusion-3.5-large-turbo/resolve/main/sd3.5_large_turbo.safetensors", 16460374454),
    "clip_g.safetensors": (HF + "stabilityai/stable-diffusion-3.5-medium/resolve/main/text_encoders/clip_g.safetensors", 1389382176),
    "ideogram4_fp8.safetensors": (HF + "ideogram-ai/ideogram-4-fp8/resolve/main/transformer/diffusion_pytorch_model.safetensors", 9289792888),
    "ideogram4_uncond_fp8.safetensors": (HF + "ideogram-ai/ideogram-4-fp8/resolve/main/unconditional_transformer/diffusion_pytorch_model.safetensors", 9289792888),
    # Public GGUF of FLUX.1 dev (the official repo is gated and 24 GB)
    "flux1-dev-q4_0.gguf": (HF + "leejet/FLUX.1-dev-gguf/resolve/main/flux1-dev-q4_0.gguf", 6925526176),
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
           "steps": 4, "flags": _FLUX2, "refs": True, "license": "Apache 2.0 — commercial use OK", "speed": 1.3,
           "about": "Same model at higher precision: slightly cleaner detail, somewhat slower."},
    "zimage": {"label": "Z-Image Turbo · Q4", "short": "Z-Image Turbo · photoreal", "api_id": "z-image-turbo-q4", "family": "z-image",
               "diffusion": "z_image_turbo-Q4_0.gguf", "llm": "Qwen3-4B-Q4_K_M.gguf", "vae": "ae.safetensors",
               "steps": 8, "flags": ["--diffusion-fa", "--cfg-scale", "1.0"], "refs": False,
               "license": "Apache 2.0 — commercial use OK",
               "about": "Strong at photorealism and lettering. No reference images (image-to-image and inpainting still work)."},
    "klein9b": {"label": "FLUX.2 klein 9B · Q4", "short": "FLUX.2 klein 9B · best quality", "api_id": "flux2-klein-9b-q4", "family": "flux2-klein-9b",
                "diffusion": "flux-2-klein-9b-Q4_0.gguf", "llm": "Qwen3-8B-Q4_K_M.gguf", "vae": "flux2-vae.safetensors",
                "steps": 4, "flags": _FLUX2, "refs": True, "noncommercial": True, "speed": 2.5,
                "license": "FLUX Non-Commercial — not for monetized or client work",
                "about": "Bigger, more detailed FLUX.2. Slower, and tight on 8 GB of GPU memory."},
    # Optional per model: "vision" (text encoder's image part, for references), "cfg" (default 1.0),
    # "align" (sizes are rendered at a multiple of this; default 16).
    "qwen21": {"label": "Qwen-Image 2.1 · Q4", "short": "Qwen-Image 2.1 · text & detail", "api_id": "qwen-image-2.1-q4",
               "family": "qwen-image-2.1", "diffusion": "qwen-image-2.1-Q4_K_M.gguf",
               "llm": "Qwen3VL-8B-Instruct-Q4_K_M.gguf", "vision": "mmproj-Qwen3VL-8B-Instruct-F16.gguf",
               "vae": "qwen_image_2.1_vae_bf16.safetensors", "steps": 20, "cfg": 6.0, "align": 32,
               "speed": 20,  # time per image relative to FLUX.2 klein 4B Q4, for the first estimate
               "flags": ["--fa", "--sampling-method", "euler"], "refs": True, "noncommercial": True,
               "license": "Qwen Research License — non-commercial",
               "about": "Best at readable text in images and detailed scenes; can also make transparent PNGs. "
                        "Slow on 8 GB GPUs (about 2–3 minutes per 1024² image)."},
    "qwenimage": {"label": "Qwen-Image · Q4", "short": "Qwen-Image · text & posters", "api_id": "qwen-image-q4",
                  "family": "qwen-image", "diffusion": "Qwen_Image-Q4_K_M.gguf", "llm": "Qwen2.5-VL-7B-Instruct.Q4_K_M.gguf",
                  "vae": "qwen_image_vae.safetensors", "steps": 20, "cfg": 2.5, "speed": 18,
                  "flags": ["--diffusion-fa", "--flow-shift", "3"], "refs": False,
                  "license": "Apache 2.0 — commercial use OK",
                  "about": "20B model with excellent text in images (posters, signs, infographics) and detailed scenes. "
                           "Best on 16 GB+ GPUs; slow on 8 GB."},
    "qwenedit": {"label": "Qwen-Image-Edit 2511 · Q4", "short": "Qwen-Image-Edit · edits & characters", "api_id": "qwen-image-edit-2511-q4",
                 "family": "qwen-image", "diffusion": "qwen-image-edit-2511-Q4_K_M.gguf", "llm": "Qwen2.5-VL-7B-Instruct.Q4_K_M.gguf",
                 "vision": "Qwen2.5-VL-7B-Instruct.mmproj-f16.gguf", "vae": "qwen_image_vae.safetensors",
                 "steps": 20, "cfg": 2.5, "speed": 20, "flags": ["--diffusion-fa", "--flow-shift", "3"], "refs": True,
                 "license": "Apache 2.0 — commercial use OK",
                 "about": "Edits by instruction and combines up to several reference images: keep a character consistent "
                          "across scenes. Best on 16 GB+ GPUs; slow on 8 GB."},
    "zimagebase": {"label": "Z-Image · Q4", "short": "Z-Image (base) · flexible", "api_id": "z-image-q4", "family": "z-image",
                   "diffusion": "z-image-Q4_K_M.gguf", "llm": "Qwen3-4B-Q4_K_M.gguf", "vae": "ae.safetensors",
                   "steps": 28, "cfg": 5.0, "speed": 9, "flags": ["--diffusion-fa"], "refs": False,
                   "license": "Apache 2.0 — commercial use OK",
                   "about": "The full Z-Image that Turbo is distilled from: slower, more varied and better at following "
                            "long prompts. Shares its text encoder with Z-Image Turbo."},
    "chroma": {"label": "Chroma1-HD · Q4", "short": "Chroma1-HD · any style", "api_id": "chroma1-hd-q4", "family": "chroma",
               "diffusion": "Chroma1-HD-Q4_0.gguf", "t5xxl": "t5xxl_fp8_e4m3fn.safetensors", "vae": "ae.safetensors",
               "steps": 26, "cfg": 4.0, "speed": 10,
               "flags": ["--diffusion-fa", "--model-args", "chroma_use_dit_mask=false"], "refs": False,
               "lora_families": ["flux1"],  # Chroma is built on FLUX.1, so most FLUX.1 LoRAs apply too
               "license": "Apache 2.0 — commercial use OK",
               "about": "Community model built on FLUX.1 schnell: wide range of styles, photo and art, with few content "
                        "restrictions. Uses CFG, so it's slower per image."},
    "schnell": {"label": "FLUX.1 schnell · Q4", "short": "FLUX.1 schnell · fast, small GPUs", "api_id": "flux1-schnell-q4",
                "family": "flux1", "diffusion": "flux1-schnell-q4_0.gguf", "clip_l": "clip_l.safetensors",
                "t5xxl": "t5xxl_fp8_e4m3fn.safetensors", "vae": "ae.safetensors", "steps": 4, "speed": 2,
                "flags": ["--diffusion-fa"], "refs": False, "license": "Apache 2.0 — commercial use OK",
                "about": "The original fast FLUX (4 steps). Runs on 6 GB GPUs; a solid all-rounder."},
    "kontext": {"label": "FLUX.1 Kontext dev · Q4", "short": "FLUX.1 Kontext · image editing", "api_id": "flux1-kontext-dev-q4",
                "family": "flux1", "diffusion": "flux1-kontext-dev-Q4_K_M.gguf", "clip_l": "clip_l.safetensors",
                "t5xxl": "t5xxl_fp8_e4m3fn.safetensors", "vae": "ae.safetensors", "steps": 20, "speed": 8,
                "flags": ["--diffusion-fa"], "refs": True, "noncommercial": True,
                "license": "FLUX Non-Commercial — not for monetized or client work",
                "about": "Edits an image by instruction (\"make it night\", \"change the text\") on 6–8 GB GPUs."},
    "sdxl": {"label": "SDXL 1.0", "short": "SDXL · huge LoRA ecosystem", "api_id": "sdxl-base-1.0", "family": "sdxl",
             "checkpoint": "sd_xl_base_1.0.safetensors", "vae": "sdxl_vae.safetensors", "steps": 25, "cfg": 7.0,
             "sampler": "euler_a", "speed": 3, "flags": [], "refs": False, "license": "CreativeML OpenRAIL++ — commercial use OK",
             "about": "The classic open model with thousands of community LoRAs. Works on 6 GB GPUs; best at 1024 px."},
    "sd15": {"label": "Stable Diffusion 1.5", "short": "SD 1.5 · low-end PCs", "api_id": "sd-1.5", "family": "sd15",
             "checkpoint": "v1-5-pruned-emaonly.safetensors", "steps": 25, "cfg": 7.0, "sampler": "euler_a", "speed": 0.8,
             "flags": [], "refs": False, "license": "CreativeML OpenRAIL-M — commercial use OK",
             "about": "Small and old, but runs almost anywhere, even on 4 GB GPUs or the CPU. Use 512 × 512 or 512 × 768."},
    "flux2dev": {"label": "FLUX.2 dev · Q4", "short": "FLUX.2 dev · top quality, big GPUs", "api_id": "flux2-dev-q4",
                 "family": "flux2-dev", "diffusion": "flux2-dev-Q4_K_S.gguf", "llm": "Mistral-Small-3.2-24B-Instruct-2506-Q4_K_M.gguf",
                 "vae": "flux2-vae.safetensors", "steps": 28, "speed": 40, "flags": _FLUX2, "refs": True, "noncommercial": True,
                 "license": "FLUX Non-Commercial — not for monetized or client work",
                 "about": "The 32B FLUX.2 with a 24B text encoder: top quality and reference images. Needs a 24 GB GPU "
                          "and 64 GB of RAM; far too big for 8–16 GB."},
    "sd35m": {"label": "SD 3.5 Medium", "short": "SD 3.5 Medium · text & composition", "api_id": "sd3.5-medium",
              "family": "sd3", "checkpoint": "sd3.5_medium.safetensors", "clip_l": "clip_l.safetensors",
              "clip_g": "clip_g.safetensors", "t5xxl": "t5xxl_fp8_e4m3fn.safetensors", "steps": 28, "cfg": 4.5,
              "speed": 6, "flags": [], "refs": False, "gated": HF + "stabilityai/stable-diffusion-3.5-medium",
              "license": "Stability AI Community License — commercial use OK under $1M yearly revenue",
              "about": "Stability's 2.5B model: good prompt following and text, runs on 8 GB GPUs. Needs a Hugging Face "
                       "token and accepting its terms on Hugging Face."},
    "sd35l": {"label": "SD 3.5 Large", "short": "SD 3.5 Large · best Stability quality", "api_id": "sd3.5-large",
              "family": "sd3", "checkpoint": "sd3.5_large.safetensors", "clip_l": "clip_l.safetensors",
              "clip_g": "clip_g.safetensors", "t5xxl": "t5xxl_fp8_e4m3fn.safetensors", "steps": 28, "cfg": 4.5,
              "speed": 18, "flags": [], "refs": False, "gated": HF + "stabilityai/stable-diffusion-3.5-large",
              "license": "Stability AI Community License — commercial use OK under $1M yearly revenue",
              "about": "The 8B SD 3.5 at full precision: top Stability quality, for 16–24 GB GPUs. Needs a Hugging Face "
                       "token and accepting its terms."},
    "sd35lt": {"label": "SD 3.5 Large Turbo", "short": "SD 3.5 Large Turbo · 4 steps", "api_id": "sd3.5-large-turbo",
               "family": "sd3", "checkpoint": "sd3.5_large_turbo.safetensors", "clip_l": "clip_l.safetensors",
               "clip_g": "clip_g.safetensors", "t5xxl": "t5xxl_fp8_e4m3fn.safetensors", "steps": 4, "cfg": 1.0,
               "speed": 4, "flags": [], "refs": False, "gated": HF + "stabilityai/stable-diffusion-3.5-large-turbo",
               "license": "Stability AI Community License — commercial use OK under $1M yearly revenue",
               "about": "SD 3.5 Large distilled to 4 steps: much faster, for 16–24 GB GPUs. Needs a Hugging Face token "
                        "and accepting its terms."},
    "flux1dev": {"label": "FLUX.1 dev · Q4", "short": "FLUX.1 dev · huge LoRA ecosystem", "api_id": "flux1-dev-q4",
                 "family": "flux1", "diffusion": "flux1-dev-q4_0.gguf", "clip_l": "clip_l.safetensors",
                 "t5xxl": "t5xxl_fp8_e4m3fn.safetensors", "vae": "ae.safetensors", "steps": 20, "speed": 8,
                 "flags": ["--diffusion-fa"], "refs": False, "noncommercial": True,
                 "license": "FLUX Non-Commercial — not for monetized or client work",
                 "about": "The most popular FLUX for community LoRAs (styles, characters). Runs on 8 GB GPUs."},
    "ideogram4": {"label": "Ideogram 4 · FP8", "short": "Ideogram 4 · typography & design", "api_id": "ideogram-4-fp8",
                  "family": "ideogram4", "diffusion": "ideogram4_fp8.safetensors", "uncond": "ideogram4_uncond_fp8.safetensors",
                  "llm": "Qwen3VL-8B-Instruct-Q4_K_M.gguf", "vae": "flux2-vae.safetensors", "steps": 20, "cfg": 7.0,
                  "speed": 30, "flags": ["--diffusion-fa"], "refs": False, "noncommercial": True,
                  "gated": HF + "ideogram-ai/ideogram-4-fp8", "license": "Ideogram 4 Non-Commercial License",
                  "about": "Strong at typography, posters and layouts; prompts can be detailed JSON briefs. Two 9 GB "
                           "models: for 16–24 GB GPUs. Needs a Hugging Face token and accepting its terms."},
}


# LoRAs only work on the base model they were trained for; models sharing a family can share LoRAs.
FAMILIES = {"flux2-klein-4b": "FLUX.2 klein 4B", "flux2-klein-9b": "FLUX.2 klein 9B", "z-image": "Z-Image",
            "qwen-image-2.1": "Qwen-Image 2.1", "qwen-image": "Qwen-Image", "chroma": "Chroma", "flux1": "FLUX.1",
            "sdxl": "SDXL", "sd15": "SD 1.5", "flux2-dev": "FLUX.2 dev", "sd3": "SD 3.5", "ideogram4": "Ideogram 4"}
# Each model's files and the engine argument that loads them. "checkpoint" is a single all-in-one file
# (SDXL, SD 1.5); the others are separate parts. Only the keys a model has are used.
FILE_ARGS = {"checkpoint": "--model", "diffusion": "--diffusion-model", "uncond": "--uncond-diffusion-model",
             "llm": "--llm", "vision": "--llm_vision",
             "clip_l": "--clip_l", "clip_g": "--clip_g", "t5xxl": "--t5xxl", "vae": "--vae"}
TEXT_PARTS = ("llm", "clip_l", "clip_g", "t5xxl")
MAX_LORAS = 3  # per batch


def lora_families(key: str) -> list[str]:
    """The LoRA families a model can use: its own, plus any it's compatible with."""
    m = MODELS[key]
    return [m["family"], *m.get("lora_families", [])]


def lora_dir(cfg: dict) -> Path:
    return Path(cfg["models_dir"]) / "loras"


def steps_for(cfg: dict, model: str) -> int:
    """The Settings override if set, else the model's own tuned step count."""
    return int(cfg.get("steps") or 0) or MODELS[model]["steps"]


def model_page(key: str) -> str:
    """The model's own page: its gated page, else the Hugging Face repo its main file comes from."""
    m = MODELS[key]
    if m.get("gated"):
        return m["gated"]
    url = FILES[m.get("diffusion") or m["checkpoint"]][0]
    return url.split("/resolve/")[0] if "/resolve/" in url else url


def model_files(key: str) -> list[str]:
    m = MODELS[key]
    return [m[k] for k in FILE_ARGS if m.get(k)]


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
