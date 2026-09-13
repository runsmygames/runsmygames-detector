"""Automatic detection of the machine's hardware (Windows / Linux / macOS).

Produces the `SystemSpecs` that the detector uploads. The dataclass itself
lives in shared/specs.py because the server scores against the same shape.
"""
from __future__ import annotations

import os
import platform
import re
import shutil
import subprocess
import sys
import threading
from pathlib import Path

import psutil

from shared.specs import SystemSpecs

__all__ = ["SystemSpecs", "detect_system", "detect_system_with_raw",
          "steam_library_paths"]


# Hide the console window each subprocess would otherwise flash in a
# --windowed (GUI) PyInstaller build on Windows.
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# Where `detect_system_with_raw` collects the raw text each read produced its
# answer from, for the local diagnostic dump (`detector/diagnostics.py`,
# `python -m detector.app --diagnose`). `threading.local` rather than a plain
# module list: `app.py` runs detection on a background thread, and a global
# list would mean two runs racing to append to the one thing.
_raw_local = threading.local()


def _raw_sink() -> list[dict] | None:
    return getattr(_raw_local, "sink", None)


def _run(cmd: list[str], *, label: str | None = None,
         capture: bool = True) -> str:
    """Runs a platform read, recording it for the diagnostic dump.

    `capture=False` keeps a read out of the dump entirely. The dump carries
    hardware readings and nothing else, and a read whose *answer* is a place
    on this machine rather than a component has no business in a file that is
    meant to be sent back to us — see `detector/diagnostics.py`.
    """
    error = None
    out = ""
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=15,
                             creationflags=_NO_WINDOW).stdout
    except Exception as exc:                      # noqa: BLE001
        error = f"{type(exc).__name__}: {exc}"
    sink = _raw_sink()
    if sink is not None and capture:
        sink.append({"label": label or " ".join(cmd), "cmd": cmd,
                     "stdout": out, "error": error})
    return out


def _read_raw(label: str, path: Path, **kwargs) -> str:
    """Reads a text file, logging it to the raw sink the way `_run` logs a
    subprocess call. The non-Windows platform reads have no subprocess to
    capture — this is their equivalent.
    """
    error = None
    text = ""
    try:
        text = path.read_text(**kwargs)
    except OSError as exc:
        error = f"{type(exc).__name__}: {exc}"
    sink = _raw_sink()
    if sink is not None:
        sink.append({"label": label, "cmd": [f"read:{path}"], "stdout": text,
                     "error": error})
    return text


def _detect_cpu_name() -> str:
    system = platform.system()
    if system == "Windows":
        out = _run(["powershell", "-NoProfile", "-Command",
                    "(Get-CimInstance Win32_Processor).Name"],
                  label="cpu_name (Win32_Processor)")
        if out.strip():
            return out.strip().splitlines()[0]
    elif system == "Linux":
        text = _read_raw("cpu_name (/proc/cpuinfo)", Path("/proc/cpuinfo"))
        for line in text.splitlines():
            if line.lower().startswith("model name"):
                return line.split(":", 1)[1].strip()
    elif system == "Darwin":
        out = _run(["sysctl", "-n", "machdep.cpu.brand_string"],
                  label="cpu_name (sysctl machdep.cpu.brand_string)")
        if out.strip():
            return out.strip()
    return platform.processor() or "Unknown CPU"


def _detect_gpus() -> list[str]:
    system = platform.system()
    gpus: list[str] = []
    if system == "Windows":
        out = _run(["powershell", "-NoProfile", "-Command",
                    "(Get-CimInstance Win32_VideoController).Name"],
                  label="gpus (Win32_VideoController)")
        gpus = [l.strip() for l in out.splitlines() if l.strip()]
    elif system == "Linux":
        # nvidia-smi if present (gives the exact NVIDIA GPU name)
        if shutil.which("nvidia-smi"):
            out = _run(["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
                      label="gpus (nvidia-smi)")
            gpus += [l.strip() for l in out.splitlines() if l.strip()]
        out = _run(["lspci"], label="gpus (lspci)")
        for line in out.splitlines():
            if re.search(r"VGA compatible controller|3D controller", line):
                name = line.split(":", 2)[-1].strip()
                if not any(name in g or g in name for g in gpus):
                    gpus.append(name)
    elif system == "Darwin":
        out = _run(["system_profiler", "SPDisplaysDataType"],
                  label="gpus (system_profiler SPDisplaysDataType)")
        gpus = re.findall(r"Chipset Model:\s*(.+)", out)
    return gpus or ["Unknown GPU"]


# --------------------------------------------------------------- platform
#
# What an upgrade has to fit into: the board, the socket, the memory
# generation and how many slots are already full. Read from the firmware's own
# tables (SMBIOS on Windows, the DMI files on Linux), which is the only place
# any of it is written down.
#
# Two of the fields cannot be read at all, and it is worth being exact about
# why rather than returning a guess:
#
#   * **The power supply.** It has no data line to the system. Nothing in
#     Windows, Linux or macOS can read its rating, because the rating exists
#     only on a label on the box. `Win32_PowerSupply` is queried below because
#     it costs nothing in a call we are already making, and on a desktop it is
#     empty essentially always. So the server has to reason from the *load* an
#     upgrade would add and tell the user what to check.
#   * **The cooler.** Equally mute. The firmware sometimes admits that some
#     fans exist, which is what is collected here, and that is the whole of it.
#
# Everything returns "unknown" rather than a default, because a wrong value
# here becomes a confident sentence on a page about spending money.

# SMBIOS memory types. 0 and 2 are "unknown" and "other", which many boards
# report; those stay unknown rather than being guessed at from clock speed,
# where DDR4 and DDR5 overlap around 4000 MT/s.
_SMBIOS_MEMORY_TYPES = {20: "DDR", 21: "DDR2", 24: "DDR3", 26: "DDR4",
                        34: "DDR5"}

# `SocketDesignation` is free text and OEM boards fill it with placeholders —
# "U3E1", "SOCKET 0", "To Be Filled By O.E.M.". Only strings shaped like a real
# socket are passed on; the server infers the rest from the processor name,
# where the knowledge can be corrected without republishing the binary.
_REAL_SOCKET = re.compile(r"^(am[45]|fm2\+?|tr4|strx4|str5|slgax?\d+|lga\s?\d{3,4}|"
                          r"socket\s?am[45])$", re.I)

_WINDOWS_PLATFORM_PS = r"""
$ErrorActionPreference = 'SilentlyContinue'
$b = Get-CimInstance Win32_BaseBoard | Select-Object -First 1
"board=$(($b.Manufacturer + ' ' + $b.Product).Trim())"
$p = Get-CimInstance Win32_Processor | Select-Object -First 1
"socket=$($p.SocketDesignation)"
$m = @(Get-CimInstance Win32_PhysicalMemory)
"modules=$($m.Count)"
"memtype=$($m | ForEach-Object { $_.SMBIOSMemoryType } | Select-Object -First 1)"
$a = Get-CimInstance Win32_PhysicalMemoryArray | Select-Object -First 1
"slots=$($a.MemoryDevices)"
"fans=$(@(Get-CimInstance Win32_Fan).Count)"
"psumw=$(Get-CimInstance Win32_PowerSupply | ForEach-Object { $_.TotalOutputPower } | Select-Object -First 1)"
"""


def _parse_keyed(out: str) -> dict[str, str]:
    """`key=value` lines into a dict, ignoring anything that isn't one."""
    parsed = {}
    for line in out.splitlines():
        key, sep, value = line.partition("=")
        if sep and key.strip():
            parsed[key.strip()] = value.strip()
    return parsed


def _as_int(text: str, low: int, high: int) -> int:
    """A bounded integer, or 0 for anything unparseable or implausible."""
    try:
        value = int(float(text))
    except (TypeError, ValueError):
        return 0
    return value if low <= value <= high else 0


def _clean_board(text: str) -> str:
    """Drop the placeholders firmware writes when nobody filled the field in."""
    text = re.sub(r"\s+", " ", text).strip(" -")
    if not text or re.search(r"to be filled|o\.?e\.?m\.?|default string|"
                             r"^system (manufacturer|product)|invalid|none$",
                             text, re.I):
        return ""
    return text[:120]


def _detect_platform() -> dict:
    """Board, socket, memory generation and slots. Unknowns stay unknown."""
    blank = {"motherboard": "", "cpu_socket": "", "ram_type": "",
             "ram_slots": 0, "ram_modules": 0, "psu_watts": 0, "cooling": ""}
    system = platform.system()

    if system == "Windows":
        # One PowerShell process for all of it. Five separate ones cost several
        # seconds with the user watching a progress bar, and this runs on the
        # path between pressing the button and seeing anything.
        found = _parse_keyed(_run(
            ["powershell", "-NoProfile", "-Command", _WINDOWS_PLATFORM_PS],
            label="platform (Win32_BaseBoard/Win32_Processor/"
                  "Win32_PhysicalMemory/Win32_PhysicalMemoryArray/"
                  "Win32_Fan/Win32_PowerSupply)"))
        socket = found.get("socket", "").strip()
        fans = _as_int(found.get("fans", ""), 1, 64)
        # Reported in milliwatts when reported at all, which is close to never.
        psu_mw = _as_int(found.get("psumw", ""), 1, 3_000_000)
        return {
            "motherboard": _clean_board(found.get("board", "")),
            "cpu_socket": socket.upper() if _REAL_SOCKET.match(socket) else "",
            "ram_type": _SMBIOS_MEMORY_TYPES.get(
                _as_int(found.get("memtype", ""), 1, 255), ""),
            "ram_slots": _as_int(found.get("slots", ""), 1, 32),
            "ram_modules": _as_int(found.get("modules", ""), 1, 32),
            "psu_watts": psu_mw // 1000 if psu_mw else 0,
            "cooling": f"{fans} fans reported" if fans else "",
        }

    if system == "Linux":
        # sysfs, so no root and no `dmidecode`. Only the board is here; the
        # memory and socket tables need privileges we will not ask for, and the
        # server infers what it can from the processor name instead.
        dmi = Path("/sys/devices/virtual/dmi/id")
        parts = []
        for name in ("board_vendor", "board_name"):
            text = _read_raw(f"platform ({dmi / name})", dmi / name,
                             errors="replace")
            if text.strip():
                parts.append(text.strip())
        return {**blank, "motherboard": _clean_board(" ".join(parts))}

    # macOS: the board is not a thing anyone upgrades, and Apple exposes none
    # of this. Unknown throughout is the truthful answer rather than a gap.
    return blank


# --------------------------------------------------------------- OS / arch

def _detect_os() -> tuple[str, str, float]:
    """Returns (display name, family, numeric version).

    Windows 11 reports itself as "10" through platform.release(); the build
    number is the only reliable way to tell them apart.
    """
    system = platform.system()
    if system == "Windows":
        version = 0.0
        release = platform.release()
        try:
            wv = sys.getwindowsversion()
            if wv.major >= 10:
                version = 11.0 if wv.build >= 22000 else 10.0
            elif (wv.major, wv.minor) == (6, 3):
                version = 8.1
            elif (wv.major, wv.minor) == (6, 2):
                version = 8.0
            elif (wv.major, wv.minor) == (6, 1):
                version = 7.0
            elif wv.major == 6:
                version = 6.0          # Vista
            elif wv.major == 5:
                version = 5.1          # XP
        except (AttributeError, OSError):
            m = re.match(r"(\d+(?:\.\d+)?)", release)
            version = float(m.group(1)) if m else 0.0
        name = f"Windows {version:g}" if version else f"Windows {release}"
        return name, "windows", version
    if system == "Darwin":
        mac = platform.mac_ver()[0]
        m = re.match(r"(\d+)(?:\.(\d+))?", mac)
        version = float(f"{m.group(1)}.{m.group(2) or 0}") if m else 0.0
        return f"macOS {mac or platform.release()}", "mac", version
    if system == "Linux":
        return f"Linux {platform.release()}", "linux", 0.0
    return f"{system} {platform.release()}", "", 0.0


def _detect_arch_bits() -> int:
    machine = (platform.machine() or "").lower()
    if machine in ("x86", "i386", "i486", "i586", "i686", "armv7l"):
        return 32
    return 64


# --------------------------------------------------------------- disk

def _steam_roots(steam_root: str | Path | None = None) -> list[Path]:
    """Candidate Steam installation directories for this platform."""
    system = platform.system()
    roots: list[Path] = []
    if steam_root:
        roots.append(Path(steam_root))
    if system == "Windows":
        # Never captured: the answer is a folder on this machine, and on a PC
        # where Steam sits under the profile it is the Windows account name.
        # It is read to work out free space, which is hardware; the path
        # itself is not, so it does not reach the diagnostic dump.
        out = _run(["powershell", "-NoProfile", "-Command",
                    "(Get-ItemProperty 'HKCU:\\Software\\Valve\\Steam' -Name "
                    "SteamPath -ErrorAction SilentlyContinue).SteamPath"],
                  capture=False)
        if out.strip():
            roots.append(Path(out.strip().splitlines()[0]))
        roots += [Path(r"C:\Program Files (x86)\Steam"), Path(r"C:\Steam")]
    elif system == "Linux":
        home = Path.home()
        roots += [home / ".steam" / "steam", home / ".local" / "share" / "Steam",
                  home / ".var/app/com.valvesoftware.Steam/data/Steam"]
    elif system == "Darwin":
        roots.append(Path.home() / "Library" / "Application Support" / "Steam")
    return roots


def steam_library_paths(steam_root: str | Path | None = None) -> list[Path]:
    """Steam library folders declared in libraryfolders.vdf (may be empty)."""
    paths: list[Path] = []
    for root in _steam_roots(steam_root):
        vdf = root / "steamapps" / "libraryfolders.vdf"
        try:
            text = vdf.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for raw in re.findall(r'"path"\s*"([^"]+)"', text):
            p = Path(raw.replace("\\\\", "\\"))
            if p.exists() and p not in paths:
                paths.append(p)
        if root.exists() and root not in paths:
            paths.append(root)
    return paths


def _detect_free_disk(steam_root: str | Path | None = None) -> tuple[float, str]:
    """Free space where Steam actually installs games.

    Picks the Steam library folder with the most free space; falls back to the
    system drive when Steam isn't installed or can't be read.
    """
    best_gb, best_label = 0.0, ""
    for path in steam_library_paths(steam_root):
        try:
            free = shutil.disk_usage(path).free / 1024**3
        except OSError:
            continue
        if free > best_gb:
            best_gb, best_label = free, _drive_label(path)
    if best_label:
        return round(best_gb, 1), best_label

    system_root = "C:\\" if platform.system() == "Windows" else "/"
    try:
        free = shutil.disk_usage(system_root).free / 1024**3
    except OSError:
        return 0.0, ""
    return round(free, 1), system_root


# What a free-space label is allowed to be — and there is nothing else this
# can construct.
#
# The label names a **volume**. It is never a place inside one, because a place
# inside one is where the account name lives: a Steam library folder such as
# `/home/<account name>/.local/share/Steam` would carry it into a `SystemSpecs`
# field that is serialised, uploaded, and rendered back to the user as
# "on {disk_label}". That would be the detector sending something that is not
# hardware, which is the one thing it may not do.
#
# So the rule is stated as what a label may be rather than as the shapes it
# must not have: a drive letter, one of the mount points a system defines for
# itself, or the name a removable volume carries. No branch copies a segment
# out of the path it was handed, so a path shape nobody thought of cannot leak
# through a `str(path)` fallback.
_WINDOWS_DRIVE_RE = re.compile(r"^[A-Za-z]:$")

_SYSTEM_MOUNTS = frozenset({"/", "/home", "/data", "/games", "/srv", "/opt",
                            "/var", "/System/Volumes/Data"})

# Media root -> how deep the volume's own name sits under it. Pinned rather
# than "the last segment under a media root", because udisks mounts removable
# disks at `/media/<user>/<volume>` and `/run/media/<user>/<volume>`: the
# account name is the segment in between, and a rule that took the last
# segment of `/media/alice` would hand back `alice`.
_MOUNT_ROOTS = (("/run/media", 2), ("/media", 2), ("/mnt", 1), ("/Volumes", 1))

# When none of the above fits. A figure with a vague label is worth more than
# a figure with a private one, and the number itself is unaffected.
UNNAMED_VOLUME = "this PC's disk"


def _mount_point(path: Path | str) -> str:
    """The volume `path` sits on, as a POSIX-style string.

    Deliberately string-first rather than `Path.resolve()`: this branch only
    ever runs off Windows, and `Path` would apply the *host's* semantics to it
    — which is also what makes the behaviour testable from any machine.
    """
    text = str(path).replace("\\", "/").rstrip("/") or "/"
    while not os.path.ismount(text):
        parent = text.rsplit("/", 1)[0] or "/"
        if parent == text:
            return "/"
        text = parent
    return text


def _drive_label(path: Path) -> str:
    """Which volume the free-space figure refers to — never where on it."""
    if platform.system() == "Windows":
        # The caller's own `.drive` first: `Path()` re-parses with the *host's*
        # rules, so a Windows path handed to this branch from a Linux test box
        # comes back with no drive at all and the branch silently stops being
        # the one under test.
        drive = getattr(path, "drive", "") or Path(path).drive
        return f"{drive}\\" if _WINDOWS_DRIVE_RE.match(drive) else UNNAMED_VOLUME

    mount = _mount_point(path)
    if mount in _SYSTEM_MOUNTS:
        return mount
    for root, depth in _MOUNT_ROOTS:
        if mount.startswith(f"{root}/"):
            parts = mount[len(root) + 1:].split("/")
            return parts[-1] if len(parts) == depth else UNNAMED_VOLUME
    return UNNAMED_VOLUME


def detect_system(steam_root: str | Path | None = None) -> SystemSpecs:
    freq = psutil.cpu_freq()
    os_name, os_family, os_version = _detect_os()
    free_disk_gb, disk_label = _detect_free_disk(steam_root)
    return SystemSpecs(
        os_name=os_name,
        os_family=os_family,
        os_version=os_version,
        arch_bits=_detect_arch_bits(),
        cpu_name=_detect_cpu_name(),
        cpu_cores=psutil.cpu_count(logical=False) or 0,
        cpu_threads=psutil.cpu_count(logical=True) or 0,
        cpu_freq_ghz=round((freq.max or freq.current) / 1000, 2) if freq else 0.0,
        ram_gb=round(psutil.virtual_memory().total / 1024**3, 1),
        gpus=_detect_gpus(),
        free_disk_gb=free_disk_gb,
        disk_label=disk_label,
        **_detect_platform(),
    )


def detect_system_with_raw(
    steam_root: str | Path | None = None,
) -> tuple[SystemSpecs, list[dict]]:
    """`detect_system`, plus the raw text each platform-specific read
    produced its answer from.

    For the local diagnostic dump only (`detector/diagnostics.py`) — the raw
    list never leaves this machine and is not part of the upload payload. A
    parsed field that comes back wrong says nothing about *why*; the raw text
    beside it says whether the read failed or the parse did.
    """
    raw: list[dict] = []
    _raw_local.sink = raw
    try:
        specs = detect_system(steam_root)
    finally:
        _raw_local.sink = None
    return specs, raw


if __name__ == "__main__":
    s = detect_system()
    print(s)
