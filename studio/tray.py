"""System tray icon. It only talks to the running server over HTTP, so it never touches the event loop."""
import json
import os
import subprocess
import threading
import time
import urllib.request
import webbrowser

import pystray
from PIL import ImageDraw

from . import autostart

STATE_COLORS = {"ready": "#d9f45c", "busy": "#f5a524", "starting": "#f5a524", "error": "#ff3b30"}


class Tray:
    def __init__(self, cfg: dict, on_quit):
        self.cfg, self.on_quit = cfg, on_quit
        self.base = f"http://{cfg['host']}:{cfg['port']}"
        self.state = {}
        self._icons = {}
        self.icon = pystray.Icon("fatima-image-studio", self._image(None), "Fatima Image Studio", menu=pystray.Menu(
            pystray.MenuItem("Open Fatima Image Studio", self.open, default=True),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Copy API URL", lambda: copy(f"{self.base}/v1")),
            pystray.MenuItem("Copy API key", lambda: copy(self.cfg["api_key"])),
            pystray.MenuItem("Open batches folder", lambda: os.startfile(self.cfg["batches_dir"])),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Unload model", self.unload, enabled=lambda _: self.engine_state() == "ready"),
            pystray.MenuItem("Start with Windows", self.toggle_autostart, checked=lambda _: autostart.enabled()),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit", self.quit),
        ))

    def run(self) -> None:
        threading.Thread(target=self._watch, daemon=True).start()
        self.icon.run()

    def engine_state(self) -> str | None:
        return (self.state.get("engine") or {}).get("state")

    def _image(self, state: str | None):
        if state not in self._icons:
            img = autostart.icon_image(64)
            if state in STATE_COLORS:  # status dot, like the header's
                ImageDraw.Draw(img).ellipse([40, 40, 62, 62], fill=STATE_COLORS[state], outline="#20221e", width=3)
            self._icons[state] = img
        return self._icons[state]

    def _watch(self) -> None:
        labels = {"stopped": "standby", "starting": "loading model", "ready": "model loaded",
                  "busy": "generating", "error": "engine error"}
        while True:
            try:
                with urllib.request.urlopen(self.base + "/api/state", timeout=5) as r:
                    self.state = json.load(r)
                queued = len(self.state.get("queue", []))
                state = self.engine_state()
                self.icon.icon = self._image(state)
                self.icon.title = f"Fatima Image Studio — {labels.get(state, state)}" + (f" · {queued} batch(es) queued" if queued else "")
                self.icon.update_menu()
                self._notify_finished()
            except Exception:
                pass
            time.sleep(3)

    def _notify_finished(self) -> None:
        """A Windows notification when a batch stops running (finished, partly failed, or cancelled)."""
        with urllib.request.urlopen(self.base + "/api/batches", timeout=5) as r:
            batches = json.load(r)
        seen, self._seen = getattr(self, "_seen", None), {b["id"]: b["status"] for b in batches}
        if seen is None or not self.cfg.get("notify", True):
            return
        for b in batches:
            if seen.get(b["id"]) not in ("running", "queued") or b["status"] in ("running", "queued", "paused"):
                continue
            mins = (b.get("elapsed_seconds") or 0) / 60
            took = f" in {mins:.0f} min" if mins >= 1 else ""
            if b["failed"]:
                text = f"{b['done']} of {b['total']} images done{took} — {b['failed']} failed. Open Fatima Image Studio to retry."
            elif b["status"] == "cancelled" or b["cancelled"]:
                text = f"Stopped after {b['done']} of {b['total']} images."
            else:
                text = f"All {b['total']} images done{took}" + (f", {b['upscaled']} upscaled." if b.get("upscaled") else ".")
            self.icon.notify(text, f"Batch finished: {b['name']}"[:63])

    def open(self) -> None:
        webbrowser.open(self.base + "/")

    def unload(self) -> None:
        req = urllib.request.Request(self.base + "/api/engine/unload", method="POST", headers={"X-Studio": "1"})
        try:
            urllib.request.urlopen(req, timeout=30)
        except Exception as e:
            self.icon.notify(str(e), "Fatima Image Studio")

    def toggle_autostart(self) -> None:
        autostart.set_enabled(not autostart.enabled())

    def quit(self) -> None:
        self.icon.visible = False
        self.on_quit()
        self.icon.stop()


def copy(text: str) -> None:
    subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", "Set-Clipboard -Value $env:CLIP"],
                   env=os.environ | {"CLIP": text}, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
