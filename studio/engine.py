"""Runs sd-server.exe (stable-diffusion.cpp) as a child process and drives its native job API."""
import asyncio
import base64
import io
import logging
import re
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import httpx
from PIL import Image

from . import config, hardware

log = logging.getLogger("studio.engine")

# sd-server draws the sampling progress bar as "|=====>    | 3/4 - 1.4it/s"
SAMPLING_BAR = re.compile(r"\|[=> ]+\|\s*(\d+)/(\d+)")


class EngineError(Exception):
    pass


class Cancelled(Exception):
    pass


def _up16(n: int) -> int:
    return min(2048, -(-int(n) // 16) * 16)


def _pad(png: bytes | None, w: int, h: int) -> bytes | None:
    """Centre an image on a w×h canvas whose edges continue the image (a stretched copy behind it)."""
    if png is None:
        return None
    img = Image.open(io.BytesIO(png))
    canvas = img.resize((w, h), Image.LANCZOS)
    canvas.paste(img, ((w - img.width) // 2, (h - img.height) // 2))
    out = io.BytesIO()
    canvas.save(out, "PNG")
    return out.getvalue()


def _trim(png: bytes, w: int, h: int) -> bytes:
    img = Image.open(io.BytesIO(png))
    left, top = (img.width - w) // 2, (img.height - h) // 2
    out = io.BytesIO()
    img.crop((left, top, left + w, top + h)).save(out, "PNG")
    return out.getvalue()


class Engine:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.proc: subprocess.Popen | None = None
        self.model: str | None = None
        self.port: int | None = None
        self.state = "stopped"  # stopped | starting | ready | busy | error
        self.error: str | None = None
        self.phase: str | None = None  # encoding | sampling | decoding
        self.step = 0
        self.steps = 0
        self.last_used = time.monotonic()
        self.gpu = detect_gpu()
        self.log_path = config.DATA / "logs" / "engine.log"
        self._lock = asyncio.Lock()
        self._cancel = False
        self._job = _KillOnCloseJob() if sys.platform == "win32" else None

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def status(self) -> dict:
        return {"state": self.state, "model": self.model, "error": self.error, "gpu": self.gpu,
                "phase": self.phase, "step": self.step, "steps": self.steps}

    # ---- lifecycle -------------------------------------------------------

    async def _ensure(self, model: str) -> None:
        if self.alive() and self.model == model:
            return
        await self._stop()
        await self._start(model)

    async def _start(self, model: str) -> None:
        m = config.MODELS[model]
        models_dir = Path(self.cfg["models_dir"])
        exe = config.engine_dir(self.cfg) / "sd-server.exe"
        if not exe.exists():
            self._fail("No engine installed. Open the Setup page to download one.")
        self.port = _free_port()
        args = [str(exe), "--listen-ip", "127.0.0.1", "--listen-port", str(self.port),
                "--diffusion-model", str(models_dir / m["diffusion"]),
                "--llm", str(models_dir / m["llm"]),
                "--vae", str(models_dir / m["vae"]),
                "--steps", str(m["steps"]), *self._flags(m)]
        (models_dir / "upscalers").mkdir(parents=True, exist_ok=True)
        args += ["--hires-upscalers-dir", str(models_dir / "upscalers")]
        config.lora_dir(self.cfg).mkdir(parents=True, exist_ok=True)
        args += ["--lora-model-dir", str(config.lora_dir(self.cfg))]
        self.state, self.model, self.error = "starting", model, None
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        log.info("Starting engine (%s) on port %s", model, self.port)
        self.proc = subprocess.Popen(args, cwd=exe.parent, stdin=subprocess.DEVNULL,
                                     stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if self._job:
            self._job.add(self.proc)
        threading.Thread(target=self._pump_output, args=(self.proc,), daemon=True).start()

        started = time.monotonic()
        async with httpx.AsyncClient(timeout=2) as client:
            while True:
                if self.proc.poll() is not None:
                    self._fail(f"Engine exited with code {self.proc.returncode} (see data/logs/engine.log)")
                if time.monotonic() - started > 300:
                    await self._stop()
                    self._fail("Engine did not become ready within 5 minutes")
                try:
                    if (await client.get(self.base + "/sdcpp/v1/capabilities")).status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                await asyncio.sleep(0.5)
        self.state = "ready"
        self.last_used = time.monotonic()
        log.info("Engine ready in %.1fs", time.monotonic() - started)

    def _flags(self, m: dict) -> list[str]:
        flags = list(m["flags"])
        if self.cfg["engine"] != "cuda":
            flags = [f for f in flags if f != "--sage-attn"]  # SageAttention is CUDA-only
        mode = self.cfg.get("low_vram", "auto")
        if mode == "on" or (mode == "auto" and hardware.low_vram(hardware.detect(), self.cfg["engine"])):
            flags += ["--offload-to-cpu", "--vae-tiling"]
        return flags

    def _fail(self, message: str):
        self.state, self.error = "error", message
        raise EngineError(message)

    async def _stop(self) -> None:
        proc, self.proc = self.proc, None
        if proc and proc.poll() is None:
            log.info("Stopping engine")
            proc.terminate()
            try:
                await asyncio.to_thread(proc.wait, 10)
            except subprocess.TimeoutExpired:
                proc.kill()
        if self.state != "error":
            self.state = "stopped"
        self.model = None if self.state == "stopped" else self.model

    async def load(self, model: str) -> None:
        """Load a model now instead of on the first image."""
        async with self._lock:
            await self._ensure(model)
            self.last_used = time.monotonic()

    async def stop(self) -> None:
        """Stop between images (waits for the current one to finish)."""
        async with self._lock:
            await self._stop()

    async def shutdown(self) -> None:
        await self._stop()

    async def unload_if_idle(self) -> None:
        minutes = float(self.cfg.get("idle_unload_minutes") or 0)
        if minutes <= 0 or self.state != "ready" or self._lock.locked():
            return
        if time.monotonic() - self.last_used >= minutes * 60:
            log.info("Idle for %s min; unloading model to free GPU memory", minutes)
            await self.stop()

    # ---- generation ------------------------------------------------------

    def cancel_current(self) -> None:
        self._cancel = True

    async def generate(self, model: str, *, prompt: str, width: int, height: int, seed: int,
                       steps: int, refs: list[bytes] = (), init: bytes | None = None,
                       mask: bytes | None = None, strength: float = 0.75, loras: list[dict] = ()) -> bytes:
        """Generate one image and return its PNG bytes. `init` (+ optional `mask`) does
        image-to-image / inpainting; `refs` are reference images.

        The engine only makes sizes in multiples of 16, so any other size (e.g. 1920x1080) is made a
        little larger (1920x1088) and trimmed evenly back to the exact size asked for."""
        gw, gh = _up16(width), _up16(height)
        if (gw, gh) != (width, height):
            init, mask = _pad(init, gw, gh), _pad(mask, gw, gh)
            png = await self.generate(model, prompt=prompt, width=gw, height=gh, seed=seed, steps=steps, refs=refs,
                                      init=init, mask=mask, strength=strength, loras=loras)
            return _trim(png, width, height)
        async with self._lock:
            self._cancel = False
            await self._ensure(model)
            body = {
                "prompt": prompt, "width": width, "height": height, "seed": seed, "batch_count": 1,
                "ref_images": [base64.b64encode(r).decode() for r in refs],
                "init_image": base64.b64encode(init).decode() if init else None,
                "mask_image": base64.b64encode(mask).decode() if mask else None,
                "strength": strength,
                "lora": [{"path": l["file"], "multiplier": l["strength"]} for l in loras],
                "sample_params": {"sample_method": "euler", "sample_steps": steps,
                                  "guidance": {"txt_cfg": 1.0}},
                "output_format": "png",
            }
            if init and width * height > 1_200_000:
                # Encoding a large source image (vary / inpaint) doesn't fit in 8 GB at once; tile the VAE.
                body["vae_tiling_params"] = {"enabled": True}
            self.state, self.phase, self.step, self.steps = "busy", "encoding", 0, steps
            try:
                async with httpx.AsyncClient(timeout=30) as client:
                    r = await client.post(self.base + "/sdcpp/v1/img_gen", json=body)
                    if r.status_code >= 400:
                        raise EngineError(f"Engine rejected the request: {r.text[:300]}")
                    job_url = f"{self.base}/sdcpp/v1/jobs/{r.json()['id']}"
                    deadline = time.monotonic() + float(self.cfg["image_timeout_s"])
                    cancel_sent = False
                    while True:
                        await asyncio.sleep(0.15)
                        if not self.alive():
                            raise EngineError("Engine stopped unexpectedly (see data/logs/engine.log)")
                        if (self._cancel or time.monotonic() > deadline) and not cancel_sent:
                            await client.post(job_url + "/cancel")
                            cancel_sent = True
                        job = (await client.get(job_url)).json()
                        if job["status"] == "completed":
                            return base64.b64decode(job["result"]["images"][0]["b64_json"])
                        if job["status"] == "cancelled":
                            if self._cancel:
                                raise Cancelled()
                            raise EngineError("Timed out")
                        if job["status"] == "failed":
                            message = (job.get("error") or {}).get("message") or "Generation failed"
                            if "no results" in message and width * height > 2_500_000:  # how running out of VRAM shows up
                                message = ("Not enough GPU memory for this size. Turn on Low-memory mode "
                                           "(Settings → Setup) or choose a smaller size.")
                            raise EngineError(message)
            except httpx.HTTPError as e:
                raise EngineError(f"Could not reach the engine: {e}") from e
            finally:
                self.phase = None
                self.last_used = time.monotonic()
                if self.alive():
                    self.state = "ready"
                elif self.state != "error":
                    self.state = "stopped"

    async def upscale(self, model: str, png: bytes, upscaler: str, factor: int) -> bytes:
        """Upscale with an ESRGAN model (always 4x); 2x is the 4x result scaled down, which looks
        better than running the model on a smaller target."""
        async with self._lock:
            await self._ensure(self.model or model)  # any loaded model can upscale; don't swap for it
            self.state, self.phase = "busy", "upscaling"
            try:
                async with httpx.AsyncClient(timeout=float(self.cfg["image_timeout_s"])) as client:
                    r = await client.post(self.base + "/sdcpp/v1/upscale", json={
                        "image": base64.b64encode(png).decode(), "upscaler": upscaler, "repeats": 1, "output_format": "png"})
                if r.status_code >= 400:
                    raise EngineError(f"Upscale failed: {r.text[:300]}")
                out = base64.b64decode(r.json()["images"][0]["b64_json"])
            except httpx.HTTPError as e:
                raise EngineError(f"Could not reach the engine: {e}") from e
            finally:
                self.phase = None
                self.last_used = time.monotonic()
                self.state = "ready" if self.alive() else ("stopped" if self.state != "error" else "error")
        if factor == 4:
            return out
        return await asyncio.to_thread(_downscale, out, 4 // factor)

    # ---- engine output -----------------------------------------------------

    def _pump_output(self, proc: subprocess.Popen) -> None:
        """Copy engine output to the log and track sampling progress."""
        pending = b""
        with open(self.log_path, "a", encoding="utf-8", errors="replace") as out:
            while chunk := proc.stdout.read1(4096):
                pending += chunk
                *lines, pending = re.split(rb"[\r\n]", pending)
                for raw in lines:
                    line = raw.decode("utf-8", "replace").replace("\x1b[K", "").rstrip()
                    if not line.strip():
                        continue
                    bar = SAMPLING_BAR.search(line)
                    if bar:
                        self.phase, self.step, self.steps = "sampling", int(bar[1]), int(bar[2])
                        continue
                    if "|" in line and "/s" in line:  # model-loading progress bars
                        continue
                    if "decoding" in line and self.phase:
                        self.phase = "decoding"
                    out.write(line + "\n")
                out.flush()


def _downscale(png: bytes, by: int) -> bytes:
    img = Image.open(io.BytesIO(png))
    img = img.resize((img.width // by, img.height // by), Image.LANCZOS)
    out = io.BytesIO()
    img.save(out, "PNG")
    return out.getvalue()


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def detect_gpu() -> str:
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
                             capture_output=True, text=True, timeout=10,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        name = out.stdout.strip().splitlines()[0]
        return name.replace("NVIDIA GeForce ", "")
    except Exception:
        return "GPU"


class _KillOnCloseJob:
    """Windows job object: the engine dies with this process, so it never keeps VRAM after a crash."""

    def __init__(self):
        import ctypes
        from ctypes import wintypes

        class Basic(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                        ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                        ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                        ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                        ("SchedulingClass", wintypes.DWORD)]

        class Extended(ctypes.Structure):
            _fields_ = [("Basic", Basic), ("Io", ctypes.c_ulonglong * 6),
                        ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                        ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]

        self._k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._k32.CreateJobObjectW.restype = wintypes.HANDLE
        self._k32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        self._k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        self._handle = self._k32.CreateJobObjectW(None, None)
        info = Extended()
        info.Basic.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        self._k32.SetInformationJobObject(self._handle, 9, ctypes.byref(info), ctypes.sizeof(info))

    def add(self, proc: subprocess.Popen) -> None:
        self._k32.AssignProcessToJobObject(self._handle, int(proc._handle))
