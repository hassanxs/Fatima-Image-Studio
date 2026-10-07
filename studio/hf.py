"""Hugging Face access: the optional token, and fast Xet downloads.

With a token (Settings), files on huggingface.co are fetched through Xet, Hugging Face's chunked storage
backend, by a small separate process (xet_worker) so a download can really be cancelled. The token also
goes with ordinary downloads (higher rate limits; gated repos whose terms the user accepted). Anything
that fails falls back to the plain HTTP download, and without a token nothing changes.
"""
import asyncio
import importlib.util
import logging
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import httpx

from . import config

log = logging.getLogger("studio.hf")

HF_FILE = re.compile(r"^https://huggingface\.co/(?P<repo>[^/]+/[^/]+)/resolve/(?P<rev>[^/]+)/(?P<path>.+)$")


def token(cfg: dict) -> str | None:
    return (cfg.get("hf_token") or "").strip() or None


def headers(cfg: dict, url: str) -> dict:
    """Authorization for huggingface.co only (httpx drops it on redirects to other hosts, like the CDN)."""
    t = token(cfg)
    return {"Authorization": f"Bearer {t}"} if t and url.startswith("https://huggingface.co/") else {}


def xet_available() -> bool:
    return bool(importlib.util.find_spec("huggingface_hub") and importlib.util.find_spec("hf_xet"))


def use_xet(cfg: dict, url: str) -> bool:
    return bool(token(cfg)) and bool(HF_FILE.match(url)) and xet_available()


async def whoami(t: str) -> str:
    """The account a token belongs to; raises ValueError if Hugging Face doesn't accept it."""
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.get("https://huggingface.co/api/whoami-v2", headers={"Authorization": f"Bearer {t}"})
    if r.status_code == 401:
        raise ValueError("Hugging Face didn't accept that token. Copy it again from huggingface.co/settings/tokens.")
    r.raise_for_status()
    return r.json().get("name") or "your account"


def _python() -> str:
    """A console Python that can run `-m studio.xet_worker` (the bundled one when installed)."""
    bundled = config.ROOT / "python" / "python.exe"
    if config.INSTALLED and bundled.exists():
        return str(bundled)
    exe = Path(sys.executable)
    return str(exe.with_name("python.exe") if exe.with_name("python.exe").exists() else exe)


async def xet_download(cfg: dict, url: str, final: Path, on_progress) -> None:
    """Download one file via Xet into `final`. on_progress(done_bytes) is called as it goes."""
    m = HF_FILE.match(url)
    work = final.parent / ".xet" / final.name  # staging folder, removed afterwards
    work.mkdir(parents=True, exist_ok=True)
    env = os.environ | {"HF_XET_CACHE": str(work / "cache"), "HF_HUB_DISABLE_TELEMETRY": "1",
                        "PYTHONIOENCODING": "utf-8"} | ({"HF_TOKEN": token(cfg)} if token(cfg) else {})
    proc = await asyncio.create_subprocess_exec(
        _python(), "-m", "studio.xet_worker", m["repo"], m["rev"], m["path"], str(work / "files"),
        cwd=str(config.ROOT), env=env, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    result, tail = None, []
    try:
        async for raw in proc.stdout:
            line = raw.decode("utf-8", "replace").strip()
            if line.startswith("PROGRESS "):
                on_progress(int(line.split()[1]))
            elif line.startswith("DONE "):
                result = Path(line[5:])
            elif line:
                tail = (tail + [line])[-5:]
        code = await proc.wait()
    except asyncio.CancelledError:
        proc.kill()
        await proc.wait()
        raise
    if code != 0 or not result or not result.exists():
        raise IOError(f"Xet download failed ({code}): {' / '.join(tail) or 'no output'}")
    result.replace(final)
    shutil.rmtree(work, ignore_errors=True)


def discard_staging(final: Path) -> None:
    shutil.rmtree(final.parent / ".xet" / final.name, ignore_errors=True)
