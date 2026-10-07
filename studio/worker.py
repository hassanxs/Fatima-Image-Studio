"""The single GPU queue: API requests first, then batch images in batch order, then upscales."""
import asyncio
import collections
import datetime as dt
import logging
import random
import time

from . import config
from .engine import Cancelled, Engine, EngineError
from .store import Store, compose_prompt, pinned_refs, prompt_of, prompt_refs, settings_for

log = logging.getLogger("studio.worker")


def with_triggers(text: str, s: dict) -> str:
    """LoRAs often need their trigger words in the prompt to kick in."""
    triggers = [l["triggers"] for l in s.get("loras") or [] if l.get("triggers")]
    return ", ".join([*triggers, text])


def _now() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


class Worker:
    def __init__(self, store: Store, engine: Engine, cfg: dict):
        self.store, self.engine, self.cfg = store, engine, cfg
        self.current: tuple[dict, dict] | None = None  # (batch, item) being generated
        self.api_busy = False
        self._api_jobs: collections.deque = collections.deque()
        self._wake = asyncio.Event()

    def notify(self) -> None:
        self._wake.set()

    async def submit(self, **params) -> bytes:
        """Generate one image for an API caller, ahead of any batch work."""
        fut = asyncio.get_running_loop().create_future()
        self._api_jobs.append((params, fut))
        self.notify()
        return await fut

    def next_item(self) -> tuple[str, dict, dict] | None:
        batches = [b for b in sorted(self.store.batches.values(), key=lambda b: b["order"]) if not b["paused"]]
        # Single images jump the queue: they're made right after the image in progress.
        batches.sort(key=lambda b: b.get("kind") != "singles")
        for b in batches:
            for it in b["items"]:
                if it["status"] == "queued":
                    return "generate", b, it
        for b in batches:  # upscales wait until every queued image has been generated
            for it in b["items"]:
                if (it.get("upscale") or {}).get("status") == "queued":
                    return "upscale", b, it
        return None

    async def run(self) -> None:
        while True:
            self._wake.clear()
            if self._api_jobs:
                await self._run_api_job(*self._api_jobs.popleft())
            elif nxt := self.next_item():
                kind, b, it = nxt
                await (self._run_item(b, it) if kind == "generate" else self._run_upscale(b, it))
            else:
                await self._wake.wait()

    async def _run_api_job(self, params: dict, fut: asyncio.Future) -> None:
        if fut.cancelled():
            return
        self.api_busy = True
        try:
            png = await self.engine.generate(**params)
            if not fut.cancelled():
                fut.set_result(png)
        except Exception as e:
            if not fut.cancelled():
                fut.set_exception(e)
        finally:
            self.api_busy = False

    async def _run_item(self, b: dict, it: dict) -> None:
        prompt = next(p for p in b["prompts"] if p["n"] == it["prompt"])
        s = settings_for(b, prompt)
        if it["seed"] is None:
            it["seed"] = random.randint(0, 2**31 - 1)
        it.update(status="running", error=None, started=_now())
        self.current = (b, it)
        self.store.save(b)
        started = time.monotonic()
        try:
            params = self._params(b, it, prompt)
            try:
                png = await self.engine.generate(**params)
            except EngineError as e:
                if str(e) == "Timed out":
                    raise
                # One retry covers a crashed engine or a transient out-of-memory.
                log.warning("Retrying %s/%s after: %s", b["name"], it["file"], e)
                png = await self.engine.generate(**params)
            (self.store.folder(b) / it["file"]).write_bytes(png)
            it["status"] = "done"
            if s.get("upscale"):  # batch asked for every image to be upscaled
                self._queue_upscale(b, it, s["upscale"]["factor"], s["upscale"]["model"])
        except Cancelled:
            it["status"] = "cancelled"
        except Exception as e:
            log.exception("Failed %s/%s", b["name"], it["file"])
            it.update(status="failed", error=str(e) or e.__class__.__name__)
        finally:
            it.update(duration=round(time.monotonic() - started, 2), finished=_now())
            self.current = None
            if b["id"] in self.store.batches:
                self.store.save(b)

    def _params(self, b: dict, it: dict, prompt: dict) -> dict:
        s, folder = settings_for(b, prompt), self.store.folder(b)
        steps = s.get("steps") or config.steps_for(self.cfg, s["model"])
        params = dict(model=s["model"], seed=it["seed"], steps=steps)
        kind = it.get("kind")
        params["loras"] = s.get("loras") or []
        if not kind:  # a normal batch image: pinned references + the prompt's own
            refs = pinned_refs(b) + prompt_refs(prompt)
            return params | dict(prompt=with_triggers(compose_prompt(prompt["text"], s.get("style")), s),
                                 width=prompt.get("width", s["width"]), height=prompt.get("height", s["height"]),
                                 refs=[(folder / r).read_bytes() for r in refs])
        source = next(x for x in b["items"] if x["id"] == it["source"])
        src = (folder / source["file"]).read_bytes()
        params |= dict(width=it["width"], height=it["height"])
        if kind == "edit":  # instruction edit: the image goes in as a reference and keeps its layout
            return params | dict(prompt=it["edit_prompt"], refs=[src])
        text = with_triggers(compose_prompt(it["edit_prompt"] or prompt["text"], s.get("style")), s)
        if kind == "inpaint":
            return params | dict(prompt=text, init=src, mask=(folder / it["mask"]).read_bytes(), strength=1.0)
        # vary: image-to-image. The engine runs strength × steps, so raise steps to keep ~the model's count.
        strength = it["strength"]
        return params | dict(prompt=text, init=src, strength=strength,
                             steps=max(steps, min(30, round(steps / max(strength, 0.05)))))

    def add_edit(self, b: dict, source: dict, *, kind: str, prompt: str, strength: float,
                 mask: bytes | None) -> dict:
        seed = source["seed"] if kind != "vary" else random.randint(0, 2**31 - 1)
        it = self.store.add_edit(b, source, kind=kind, prompt=prompt, strength=strength, mask=mask, seed=seed)
        b["paused"] = False
        self.store.save(b)
        self.notify()
        return it

    # ---- batch controls ----------------------------------------------------

    def cancel(self, b: dict) -> None:
        for it in b["items"]:
            if it["status"] == "queued":
                it["status"] = "cancelled"
            if (it.get("upscale") or {}).get("status") == "queued":
                self.store.drop_upscale(b, it)
        if self.current and self.current[0] is b:
            self.engine.cancel_current()
        self.store.save(b)

    def retry(self, b: dict, item_id: str | None = None) -> None:
        for it in b["items"]:
            if it["status"] in ("failed", "cancelled") and item_id in (None, it["id"]):
                it.update(status="queued", error=None)
        b["paused"] = False
        self.store.save(b)
        self.notify()

    def regenerate(self, b: dict, it: dict, new_seed: bool) -> None:
        """Queue one image again; it replaces the existing file when it finishes."""
        if self.current and self.current[1] is it:
            raise ValueError("That image is being generated right now.")
        if new_seed:
            it["seed"] = random.randint(0, 2**31 - 1)
        self.store.drop_upscale(b, it)  # the old upscale no longer matches; redone after if the batch upscales
        it.update(status="queued", error=None)
        b["paused"] = False
        self.store.save(b)
        self.notify()

    def is_busy_with(self, b: dict, it: dict | None = None) -> bool:
        return bool(self.current) and self.current[0] is b and (it is None or self.current[1] is it)

    # ---- upscaling -----------------------------------------------------------

    def _queue_upscale(self, b: dict, it: dict, factor: int, model: str) -> None:
        stem = it["file"].rsplit(".", 1)[0]
        it["upscale"] = {"factor": factor, "model": model, "status": "queued", "error": None,
                         "file": f"upscaled/{stem}_x{factor}.png"}

    def upscale(self, b: dict, items: list[dict], factor: int, model: str) -> int:
        """Queue upscales for finished images; ones already upscaled the same way are skipped."""
        count = 0
        for it in items:
            up = it.get("upscale") or {}
            if it["status"] != "done" or up.get("status") in ("queued", "running"):
                continue
            if up.get("status") == "done" and up.get("factor") == factor and up.get("model") == model:
                continue
            self.store.drop_upscale(b, it)
            self._queue_upscale(b, it, factor, model)
            count += 1
        b["paused"] = False
        self.store.save(b)
        self.notify()
        return count

    async def _run_upscale(self, b: dict, it: dict) -> None:
        up = it["upscale"]
        up.update(status="running", error=None)
        self.current = (b, it)
        self.store.save(b)
        try:
            folder = self.store.folder(b)
            png = await self.engine.upscale(settings_for(b, prompt_of(b, it))["model"], (folder / it["file"]).read_bytes(),
                                            config.UPSCALERS[up["model"]]["name"], up["factor"])
            (folder / "upscaled").mkdir(exist_ok=True)
            (folder / up["file"]).write_bytes(png)
            up["status"] = "done"
        except Exception as e:
            log.exception("Upscale failed %s/%s", b["name"], it["file"])
            up.update(status="failed", error=str(e) or e.__class__.__name__)
        finally:
            self.current = None
            if b["id"] in self.store.batches:
                self.store.save(b)
