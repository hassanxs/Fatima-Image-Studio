"""What this PC has (GPU, memory, CPU, disk, power) and what to run on it."""
import ctypes
import os
import shutil
import subprocess
import winreg
from pathlib import Path

from . import config

_DISPLAY_CLASS = r"SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}"
_SKIP = ("microsoft basic", "remote", "virtual", "parsec", "meta virtual", "idd")
_cache: dict | None = None


def _vendor(name: str) -> str:
    n = name.lower()
    return "nvidia" if "nvidia" in n else "amd" if ("amd" in n or "radeon" in n) else "intel" if "intel" in n else "other"


def _nvidia_smi() -> list[dict]:
    exe = shutil.which("nvidia-smi") or r"C:\Windows\System32\nvidia-smi.exe"
    try:
        out = subprocess.run([exe, "--query-gpu=name,memory.total,driver_version", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=10,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
    except (OSError, subprocess.TimeoutExpired):
        return []
    gpus = []
    for line in out.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) == 3:
            gpus.append({"name": parts[0], "vendor": "nvidia", "vram_gb": round(float(parts[1]) / 1024, 1),
                         "driver": parts[2], "integrated": False})
    return gpus


def _registry_gpus() -> list[dict]:
    """Display adapters from the registry. Unlike WMI, it reports VRAM above 4 GB."""
    gpus = []
    try:
        root = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, _DISPLAY_CLASS)
    except OSError:
        return gpus
    for i in range(64):
        try:
            sub = winreg.EnumKey(root, i)
        except OSError:
            break
        if not sub.isdigit():
            continue
        try:
            with winreg.OpenKey(root, sub) as k:
                name = winreg.QueryValueEx(k, "DriverDesc")[0]
                try:
                    mem = int(winreg.QueryValueEx(k, "HardwareInformation.qwMemorySize")[0])
                except OSError:
                    mem = 0
        except OSError:
            continue
        if any(s in name.lower() for s in _SKIP):
            continue
        vram = round(mem / 1024**3, 1)
        # Integrated graphics share system memory and report little or none of their own.
        gpus.append({"name": name, "vendor": _vendor(name), "vram_gb": vram, "driver": None,
                     "integrated": vram < 2 or (_vendor(name) == "intel" and "arc" not in name.lower())})
    return gpus


def _ram_gb() -> float:
    class MEMORYSTATUSEX(ctypes.Structure):
        _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
    m = MEMORYSTATUSEX()
    m.dwLength = ctypes.sizeof(m)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
    return round(m.ullTotalPhys / 1024**3, 1)


def _cpu() -> str:
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0") as k:
            return winreg.QueryValueEx(k, "ProcessorNameString")[0].strip()
    except OSError:
        return "Unknown CPU"


def on_battery() -> bool | None:
    class SYSTEM_POWER_STATUS(ctypes.Structure):
        _fields_ = [("ACLineStatus", ctypes.c_byte), ("BatteryFlag", ctypes.c_byte),
                    ("BatteryLifePercent", ctypes.c_byte), ("SystemStatusFlag", ctypes.c_byte),
                    ("BatteryLifeTime", ctypes.c_ulong), ("BatteryFullLifeTime", ctypes.c_ulong)]
    s = SYSTEM_POWER_STATUS()
    if not ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(s)) or s.BatteryFlag & 128:  # 128 = no battery
        return None
    return s.ACLineStatus == 0


def detect(refresh: bool = False) -> dict:
    """GPUs, RAM and CPU. Cached: hardware doesn't change while the app runs."""
    global _cache
    if _cache is None or refresh:
        nv = _nvidia_smi()
        others = [g for g in _registry_gpus() if not (g["vendor"] == "nvidia" and nv)]
        gpus = sorted(nv + others, key=lambda g: (g["integrated"], -g["vram_gb"]))
        _cache = {"gpus": gpus, "ram_gb": _ram_gb(), "cpu": _cpu(), "cores": os.cpu_count() or 0}
    return _cache


def main_gpu(hw: dict) -> dict | None:
    """The card images will run on: the biggest dedicated one."""
    return next((g for g in hw["gpus"] if not g["integrated"]), None)


def recommended_engine(hw: dict) -> str:
    g = main_gpu(hw)
    if g and g["vendor"] == "nvidia":
        return "cuda"
    if g:
        return "vulkan"
    return "cpu"


def model_fit(key: str, hw: dict, engine: str) -> str:
    """'fits' (runs fully on the GPU), 'tight' (parts move to RAM: slower), 'too_big' or 'cpu'."""
    if engine == "cpu" or not main_gpu(hw):
        return "cpu"
    vram = main_gpu(hw)["vram_gb"]
    # The text encoder runs first and is then swapped out, so the diffusion model and VAE set the peak.
    m = config.MODELS[key]
    gb = lambda f: config.FILES[f][1] / 1024**3
    peak = max(gb(m["llm"]), gb(m["diffusion"]) + gb(m["vae"])) + 1.5  # + working memory at 1024²
    total = gb(m["diffusion"]) + gb(m["llm"]) + gb(m["vae"])
    if peak > vram + 2:
        return "too_big"
    if total + 1.0 > vram:
        return "tight"
    return "fits"


def recommended_model(hw: dict, engine: str) -> str:
    """Prefer the commercial-use models; the best one that runs fully on the GPU, else the small one."""
    for key in ("q8", "q4"):
        if model_fit(key, hw, engine) == "fits":
            return key
    return "q4"


def low_vram(hw: dict, engine: str) -> bool:
    g = main_gpu(hw)
    return engine != "cpu" and bool(g) and g["vram_gb"] < 6


def warnings(hw: dict, cfg: dict, engine: str) -> list[dict]:
    out = []
    g = main_gpu(hw)
    if not g:
        out.append({"level": "error", "code": "no_gpu",
                    "message": "No dedicated graphics card found. Images will be made on the CPU, which takes minutes each instead of seconds."})
    elif g["vendor"] == "nvidia" and g.get("driver"):
        try:
            major = float(g["driver"].split(".")[0])
        except ValueError:
            major = 999
        if major < 528:
            out.append({"level": "error", "code": "old_driver",
                        "message": f"Your NVIDIA driver ({g['driver']}) is too old for the engine. Update it from nvidia.com or the NVIDIA app."})
    if g and g["vram_gb"] < 6:
        out.append({"level": "warn", "code": "low_vram",
                    "message": f"Your GPU has {g['vram_gb']:g} GB. Low-memory mode is on: images take longer, but they work."})
    if hw["ram_gb"] < 16:
        out.append({"level": "warn", "code": "low_ram",
                    "message": f"This PC has {hw['ram_gb']:g} GB of RAM. 16 GB or more is recommended; close other apps while generating."})
    free = disk_free(cfg)
    if free is not None and free < 15 * 1024**3:
        out.append({"level": "warn", "code": "low_disk",
                    "message": f"Only {free / 1e9:.0f} GB free where models are stored. A model needs 5–11 GB."})
    if g and "laptop" in g["name"].lower() and on_battery():
        out.append({"level": "info", "code": "battery",
                    "message": "You're on battery. Plug in the charger: laptop GPUs run much slower on battery."})
    return out


def disk_free(cfg: dict) -> int | None:
    p = Path(cfg["models_dir"])
    while not p.exists() and p.parent != p:
        p = p.parent
    try:
        return shutil.disk_usage(p).free
    except OSError:
        return None
