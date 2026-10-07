"""Downloads models and engine builds (resumable), reports progress, and removes them again."""
import asyncio
import logging
import shutil
import zipfile
from pathlib import Path

import httpx

from . import config, hf

log = logging.getLogger("studio.downloads")


class Downloads:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.jobs: dict[str, dict] = {}  # model key -> {status, done, total, error}
        self._tasks: dict[str, asyncio.Task] = {}
        self.on_upscaler_installed = None  # callback: the engine reads the upscalers folder when it starts

    @property
    def folder(self) -> Path:
        return Path(self.cfg["models_dir"])

    def missing(self, key: str) -> list[str]:
        return [f for f in config.model_files(key) if not (self.folder / f).exists()]

    def status(self) -> list[dict]:
        installed = config.installed_models(self.cfg)
        out = []
        for key, m in config.MODELS.items():
            files = config.model_files(key)
            out.append({
                "key": key, "label": m["label"], "about": m["about"], "license": m["license"],
                "noncommercial": bool(m.get("noncommercial")), "refs": m["refs"], "steps": m["steps"],
                "installed": key in installed,
                "size": sum(config.FILES[f][1] for f in files),
                "to_download": sum(config.FILES[f][1] for f in self.missing(key)),
                "partial": self.partial_bytes(key),  # from disk, so a paused download survives a restart
                "job": self.jobs.get(key),
            })
        return out

    def partial_bytes(self, key: str) -> int:
        parts = (self.folder / (f + ".part") for f in self.missing(key))
        return sum(p.stat().st_size for p in parts if p.exists())

    def start(self, key: str) -> None:
        if key not in config.MODELS:
            raise ValueError("Unknown model")
        self._launch(key, self._run(key))

    def _launch(self, key: str, coro) -> None:
        if key in self._tasks and not self._tasks[key].done():
            coro.close()
            return
        task = asyncio.create_task(coro)
        self.jobs[key] = {"status": "downloading", "done": 0, "total": 0, "error": None}
        self._tasks[key] = task

    def cancel(self, key: str) -> None:
        task = self._tasks.get(key)
        if task and not task.done():
            task.cancel()

    async def _run(self, key: str) -> None:
        job = self.jobs[key]
        files = self.missing(key)
        job["total"] = sum(config.FILES[f][1] for f in files)
        try:
            self.folder.mkdir(parents=True, exist_ok=True)
            async with httpx.AsyncClient(follow_redirects=True, timeout=httpx.Timeout(30, read=300)) as client:
                for name in files:
                    await self._fetch(client, *config.FILES[name], self.folder / name, job)
            job["status"] = "done"
            log.info("Downloaded model %s", key)
            if self.cfg["default_model"] not in config.installed_models(self.cfg):  # first model: make it the default
                self.cfg["default_model"] = key
                config.save(self.cfg)
        except asyncio.CancelledError:
            job["status"] = "cancelled"  # the .part file stays, so the next download resumes
        except Exception as e:
            log.exception("Download of %s failed", key)
            job.update(status="failed", error=str(e) or e.__class__.__name__)

    async def _fetch(self, client: httpx.AsyncClient, url: str, size: int, final: Path, job: dict) -> None:
        if hf.use_xet(self.cfg, url) and not final.with_name(final.name + ".part").exists():
            base = job["done"]
            job["method"] = "xet"
            try:
                await hf.xet_download(self.cfg, url, final, lambda n: job.__setitem__("done", base + n))
                if final.stat().st_size == size:
                    job["done"] = base + size
                    return
                final.unlink()
                log.warning("Xet gave %s with the wrong size; downloading it the normal way", final.name)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.warning("Xet download of %s failed (%s); downloading it the normal way", final.name, e)
            hf.discard_staging(final)
            job["done"] = base
        job["method"] = "http"
        name, part = final.name, final.with_name(final.name + ".part")
        have = part.stat().st_size if part.exists() else 0
        headers = ({"Range": f"bytes={have}-"} if have else {}) | hf.headers(self.cfg, url)
        async with client.stream("GET", url, headers=headers) as r:
            r.raise_for_status()
            if have and r.status_code != 206:  # server ignored the range; start over
                have = 0
            job["done"] += have
            with open(part, "ab" if have else "wb") as f:
                async for chunk in r.aiter_bytes(1 << 20):
                    f.write(chunk)
                    job["done"] += len(chunk)
        if part.stat().st_size != size:
            raise IOError(f"{name} is incomplete ({part.stat().st_size} of {size} bytes); try again.")
        part.replace(final)

    # ---- upscalers (job key "upscaler:<id>") ----

    def upscalers(self) -> list[dict]:
        installed = config.installed_upscalers(self.cfg)
        out = []
        for key, u in config.UPSCALERS.items():
            part = config.upscaler_path(self.cfg, key).with_suffix(".pth.part")
            out.append({"key": key, "label": u["label"], "hint": u["hint"], "size": u["size"],
                        "installed": key in installed, "partial": part.stat().st_size if part.exists() else 0,
                        "job": self.jobs.get("upscaler:" + key)})
        return out

    def start_upscaler(self, key: str) -> None:
        if key not in config.UPSCALERS:
            raise ValueError("Unknown upscaler")
        self._launch("upscaler:" + key, self._run_upscaler(key))

    async def _run_upscaler(self, key: str) -> None:
        job = self.jobs["upscaler:" + key]
        u, path = config.UPSCALERS[key], config.upscaler_path(self.cfg, key)
        job["total"] = u["size"]
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            async with httpx.AsyncClient(follow_redirects=True, timeout=httpx.Timeout(30, read=300)) as client:
                await self._fetch(client, u["url"], u["size"], path, job)
            job["status"] = "done"
            if self.on_upscaler_installed:
                await self.on_upscaler_installed()
        except asyncio.CancelledError:
            job["status"] = "cancelled"
        except Exception as e:
            log.exception("Upscaler %s download failed", key)
            job.update(status="failed", error=str(e) or e.__class__.__name__)

    def delete_upscaler(self, key: str) -> None:
        task = self._tasks.get("upscaler:" + key)
        if task and not task.done():
            raise RuntimeError("That upscaler is downloading. Cancel it first.")
        path = config.upscaler_path(self.cfg, key)
        for p in (path, path.with_suffix(".pth.part")):
            p.unlink(missing_ok=True)
        self.jobs.pop("upscaler:" + key, None)

    # ---- engine builds (job key "engine:<id>") ----

    def engine_zips(self, key: str) -> list[tuple[str, int, Path]]:
        root = config.engine_dir(self.cfg, key).parent
        return [(url, size, root / url.rsplit("/", 1)[1]) for url, size in config.ENGINES[key]["zips"]]

    def engine_partial(self, key: str) -> int:
        parts = (z.with_name(z.name + ".part") for _, _, z in self.engine_zips(key))
        return sum(p.stat().st_size for p in parts if p.exists())

    def start_engine(self, key: str) -> None:
        if key not in config.ENGINES:
            raise ValueError("Unknown engine")
        self._launch("engine:" + key, self._run_engine(key))

    async def _run_engine(self, key: str) -> None:
        job = self.jobs["engine:" + key]
        zips = self.engine_zips(key)
        job["total"] = sum(size for _, size, _ in zips)
        target = config.engine_dir(self.cfg, key)
        staging = target.with_name(target.name + ".new")
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            async with httpx.AsyncClient(follow_redirects=True, timeout=httpx.Timeout(30, read=300)) as client:
                for url, size, path in zips:
                    if path.exists():
                        job["done"] += size
                    else:
                        await self._fetch(client, url, size, path, job)
            job["status"] = "installing"
            await asyncio.to_thread(self._unpack, [z for _, _, z in zips], staging, target)
            job["status"] = "done"
            log.info("Installed engine %s", key)
            if self.cfg["engine"] not in config.installed_engines(self.cfg):  # first engine: use it
                self.cfg["engine"] = key
                config.save(self.cfg)
        except asyncio.CancelledError:
            job["status"] = "cancelled"
        except Exception as e:
            log.exception("Engine %s download failed", key)
            job.update(status="failed", error=str(e) or e.__class__.__name__)

    @staticmethod
    def _unpack(zips: list[Path], staging: Path, target: Path) -> None:
        shutil.rmtree(staging, ignore_errors=True)
        for z in zips:
            with zipfile.ZipFile(z) as f:
                f.extractall(staging)
        if not (staging / "sd-server.exe").exists():
            raise IOError("The engine download doesn't contain sd-server.exe")
        if target.exists():
            shutil.rmtree(target)
        staging.replace(target)
        for z in zips:
            z.unlink()

    def discard_engine(self, key: str) -> None:
        task = self._tasks.get("engine:" + key)
        if task and not task.done():
            raise RuntimeError("That engine is downloading right now. Cancel it first.")
        for _, _, z in self.engine_zips(key):
            for p in (z, z.with_name(z.name + ".part")):
                p.unlink(missing_ok=True)
        self.jobs.pop("engine:" + key, None)

    def delete_engine(self, key: str) -> None:
        self.discard_engine(key)
        shutil.rmtree(config.engine_dir(self.cfg, key), ignore_errors=True)

    def discard(self, key: str) -> list[str]:
        """Abandon a paused download: delete its unfinished .part files."""
        busy = {f for k, t in self._tasks.items() if not t.done() and k in config.MODELS for f in config.model_files(k)}
        removed = []
        for name in self.missing(key):
            if name in busy:
                raise RuntimeError("Another download is using this file right now. Cancel it first.")
        for name in self.missing(key):
            hf.discard_staging(self.folder / name)
            part = self.folder / (name + ".part")
            if part.exists():
                part.unlink()
                removed.append(part.name)
        self.jobs.pop(key, None)
        return removed

    def delete(self, key: str) -> list[str]:
        """Remove a model's files, keeping any another installed model still uses."""
        others = {f for k in config.installed_models(self.cfg) if k != key for f in config.model_files(k)}
        removed = []
        for name in config.model_files(key):
            if name in others:
                continue
            for path in (self.folder / name, self.folder / (name + ".part")):
                if path.exists():
                    path.unlink()
                    removed.append(path.name)
        self.jobs.pop(key, None)
        return removed
