# Third-party notices

Fatima Image Studio's own code is MIT licensed (see `LICENSE`). It ships with, or downloads on request,
the following third-party software and models, each under its own licence.

## Shipped in the installer

| Component | Licence | Source |
|---|---|---|
| Python 3.13 (embeddable distribution) | PSF License 2.0 | https://www.python.org |
| FastAPI | MIT | https://github.com/fastapi/fastapi |
| Starlette | BSD-3-Clause | https://github.com/encode/starlette |
| Uvicorn | BSD-3-Clause | https://github.com/encode/uvicorn |
| Pydantic, pydantic-core, pydantic-settings | MIT | https://github.com/pydantic |
| HTTPX, HTTPCore | BSD-3-Clause | https://github.com/encode/httpx |
| AnyIO | MIT | https://github.com/agronholm/anyio |
| Pillow | MIT-CMU (HPND) | https://github.com/python-pillow/Pillow |
| pystray | LGPL-3.0 | https://github.com/moses-palmer/pystray |
| MCP Python SDK | MIT | https://github.com/modelcontextprotocol/python-sdk |
| pywin32 | PSF | https://github.com/mhammond/pywin32 |
| python-multipart | Apache-2.0 | https://github.com/Kludex/python-multipart |
| sse-starlette | BSD-3-Clause | https://github.com/sysid/sse-starlette |
| jsonschema, jsonschema-specifications, referencing, rpds-py | MIT | https://github.com/python-jsonschema |
| attrs | MIT | https://github.com/python-attrs/attrs |
| certifi | MPL-2.0 | https://github.com/certifi/python-certifi |
| click | BSD-3-Clause | https://github.com/pallets/click |
| colorama | BSD-3-Clause | https://github.com/tartley/colorama |
| h11 | MIT | https://github.com/python-hyper/h11 |
| idna | BSD-3-Clause | https://github.com/kjd/idna |
| PyJWT | MIT | https://github.com/jpadilla/pyjwt |
| python-dotenv | BSD-3-Clause | https://github.com/theskumar/python-dotenv |
| six | MIT | https://github.com/benjaminp/six |
| typing_extensions | PSF-2.0 | https://github.com/python/typing_extensions |
| typing-inspection, annotated-types, annotated-doc | MIT | https://github.com/pydantic |
| httpx-sse | MIT | https://github.com/florimondmanca/httpx-sse |
| Geist, Geist Mono fonts | SIL Open Font License 1.1 | https://github.com/vercel/geist-font |

Exact versions are in `packaging/requirements-lock.txt`; each package's licence text is in its
`*.dist-info` folder inside the installed `python\Lib\site-packages`.

pystray is LGPL-3.0. It is shipped unmodified as separate Python source files, which you may replace
with your own build of pystray.

## Downloaded by the app when you choose to

These are not part of the installer. The app downloads them from their publishers when you click Download.

| Component | Licence | Source |
|---|---|---|
| stable-diffusion.cpp engine (`sd-server`), incl. ggml | MIT | https://github.com/leejet/stable-diffusion.cpp |
| NVIDIA CUDA runtime (in the CUDA engine download) | NVIDIA CUDA EULA | redistributed by stable-diffusion.cpp |
| Real-ESRGAN upscalers | BSD-3-Clause | https://github.com/xinntao/Real-ESRGAN |
| FLUX.2 klein 4B (GGUF) | Apache 2.0 | https://huggingface.co/black-forest-labs, GGUF by leejet |
| FLUX.2 klein 9B (GGUF) | FLUX Non-Commercial License | https://huggingface.co/black-forest-labs, GGUF by leejet |
| Z-Image Turbo (GGUF) | Apache 2.0 | https://huggingface.co/Tongyi-MAI, GGUF by leejet |
| Qwen3 4B / 8B text encoders (GGUF) | Apache 2.0 | https://huggingface.co/Qwen, GGUF by unsloth |
| Qwen-Image 2.1 (GGUF) | Qwen Research License (non-commercial) | https://huggingface.co/Qwen/Qwen-Image-2.1, GGUF by abenzerps |
| Qwen3-VL 8B text encoder + vision (GGUF) | Apache 2.0 | https://huggingface.co/Qwen/Qwen3-VL-8B-Instruct-GGUF |
| Qwen-Image 2.1 VAE | Qwen Research License | https://huggingface.co/Comfy-Org/Qwen-Image-2.1 |
| FLUX.2 VAE, Z-Image VAE | per the model's licence | https://huggingface.co/Comfy-Org |
| LoRAs you import | each LoRA's own licence | shown on its Hugging Face page |

The app shows each model's licence before you download it. Images from non-commercial models must not be
used commercially.
