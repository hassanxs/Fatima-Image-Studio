"""Download one Hugging Face file with Xet; run by studio.hf as a separate process.

  python -m studio.xet_worker <repo> <revision> <path in repo> <folder>

Prints "PROGRESS <bytes> <total>" about once a second and "DONE <local path>" at the end. The token
comes from HF_TOKEN. Being a separate process, the app can stop it at any moment.
"""
import os
import sys
import time

from huggingface_hub import hf_hub_download
from tqdm import tqdm


class Reporter(tqdm):
    """A progress bar that prints machine-readable lines instead of drawing."""

    def __init__(self, *args, **kwargs):
        kwargs["disable"] = False
        kwargs["file"] = open(os.devnull, "w")  # draw nowhere
        super().__init__(*args, **kwargs)
        self._last = 0.0

    def update(self, n=1):
        out = super().update(n)
        now = time.monotonic()
        if now - self._last >= 0.5:
            self._last = now
            print(f"PROGRESS {int(self.n)} {int(self.total or 0)}", flush=True)
        return out


def main() -> None:
    repo, revision, path, folder = sys.argv[1:5]
    local = hf_hub_download(repo, path, revision=revision, local_dir=folder, tqdm_class=Reporter)
    print(f"DONE {local}", flush=True)


if __name__ == "__main__":
    main()
