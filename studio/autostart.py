"""Windows shortcuts: Start menu entry, project launcher and the optional Startup-folder entry."""
import os
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw

from . import APP_NAME, config

LEGACY_NAME = "Image Studio"  # before the rename
ICON = config.DATA / "icon.ico"
STARTUP_LINK = Path(os.environ.get("APPDATA", "")) / "Microsoft/Windows/Start Menu/Programs/Startup" / f"{APP_NAME}.lnk"
START_MENU_LINK = Path(os.environ.get("APPDATA", "")) / "Microsoft/Windows/Start Menu/Programs" / f"{APP_NAME}.lnk"
PROJECT_LINK = config.ROOT / f"{APP_NAME}.lnk"


LAUNCHER = config.ROOT / "FatimaImageStudio.exe"  # installed copies start through this


def pythonw() -> str:
    exe = Path(sys.executable)
    candidate = exe.with_name("pythonw.exe")
    return str(candidate if candidate.exists() else exe)


def launch_command(app_args: str) -> tuple[str, str]:
    """What a shortcut runs: the launcher when installed, else pythonw -m studio."""
    if config.INSTALLED and LAUNCHER.exists():
        return str(LAUNCHER), app_args
    return pythonw(), f"-m studio {app_args}"


def python_console() -> str:
    """python.exe (not pythonw): stdio MCP needs a console-mode interpreter."""
    bundled = config.ROOT / "python" / "python.exe"
    if config.INSTALLED and bundled.exists():
        return str(bundled)
    exe = Path(sys.executable)
    candidate = exe.with_name("python.exe")
    return str(candidate if candidate.exists() else exe)


def icon_image(size: int = 256) -> Image.Image:
    """The studio mark: a white aperture on the red square (same drawing as icons/aperture.svg)."""
    img = Image.new("RGBA", (size, size), "#de462b")
    d = ImageDraw.Draw(img)
    s = size / 24 * 0.78  # aperture drawn on a 24-unit grid, inset like the header mark
    o = (size - 24 * s) / 2
    pt = lambda x, y: (o + x * s, o + y * s)
    w = max(2, round(size / 14))
    d.ellipse([pt(2, 2), pt(22, 22)], outline="#fcfaf5", width=w)
    for a, b in [((14.31, 8), (20.05, 17.94)), ((9.69, 8), (21.17, 8)), ((7.38, 12), (13.12, 2.06)),
                 ((9.69, 16), (3.95, 6.06)), ((14.31, 16), (2.83, 16)), ((16.62, 12), (10.88, 21.94))]:
        d.line([pt(*a), pt(*b)], fill="#fcfaf5", width=w)
    return img


def ensure_icon() -> Path:
    if not ICON.exists():
        ICON.parent.mkdir(parents=True, exist_ok=True)
        icon_image().save(ICON, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    return ICON


def make_shortcut(link: Path, app_args: str) -> None:
    target, args = launch_command(app_args)
    link.parent.mkdir(parents=True, exist_ok=True)
    ps = ("$s=(New-Object -ComObject WScript.Shell).CreateShortcut($env:LNK);"
          "$s.TargetPath=$env:TARGET;$s.Arguments=$env:ARGS;$s.WorkingDirectory=$env:WD;"
          "$s.IconLocation=$env:ICO;$s.Description='Bulk image generation on this PC';$s.Save()")
    env = os.environ | {"LNK": str(link), "TARGET": target, "ARGS": args, "WD": str(config.ROOT),
                        "ICO": target if target == str(LAUNCHER) else str(ensure_icon())}
    subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps], env=env, check=True,
                   capture_output=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def migrate_legacy() -> None:
    """Shortcuts made under the old name: recreate them under the new one."""
    folder = STARTUP_LINK.parent
    if (folder / f"{LEGACY_NAME}.lnk").exists():
        (folder / f"{LEGACY_NAME}.lnk").unlink()
        set_enabled(True)
    for old, new in ((START_MENU_LINK.parent / f"{LEGACY_NAME}.lnk", START_MENU_LINK),
                     (config.ROOT / f"{LEGACY_NAME}.lnk", PROJECT_LINK)):
        if old.exists() and not config.INSTALLED:
            old.unlink()
            make_shortcut(new, "--tray")


def install_launchers() -> None:
    """Start menu entry and a launcher in the project folder; both open the tray app."""
    for link in (START_MENU_LINK, PROJECT_LINK):
        make_shortcut(link, "--tray")


def enabled() -> bool:
    return STARTUP_LINK.exists()


def set_enabled(on: bool) -> None:
    if on:
        make_shortcut(STARTUP_LINK, "--tray --no-browser")
    else:
        STARTUP_LINK.unlink(missing_ok=True)
