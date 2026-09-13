"""Local-only diagnostic dump of what the detector reads on this machine.

This writes, next to wherever the detector is running from, both the parsed
`SystemSpecs` and the raw text each platform-specific read produced them from (the actual `Win32_Processor` / `Win32_VideoController` /
`Win32_BaseBoard` / `Win32_PhysicalMemory` / `Win32_PowerSupply` output on
Windows, and the equivalents on Linux/macOS). The raw half is the point: a
parsed field that comes back wrong says nothing about *why*, and the raw text
beside it says whether the read failed or the parse did.

**Nothing here is uploaded.** It is not the payload, not a new field, not a
new request — `report.upload` is never called from this module. Invoked with
`python -m detector.app --diagnose` from a checkout, or `RunsMyGames.exe
--diagnose` for a build. Deliberately not a button: see `docs/detector.md`
for why the window's copy stays exactly as it is.

**It is also the one artefact designed to leave a stranger's machine**, which
is why `redact` runs over everything before it is written — see below.
"""
from __future__ import annotations

import getpass
import json
import os
import platform
import re
import socket
import sys
from datetime import datetime, timezone
from pathlib import Path

from detector import hardware, report

FILENAME = "runsmygames_diagnostic.json"

# ------------------------------------------------------------------ redaction
#
# The detector carries hardware and nothing else. This file is not the upload
# payload, but it is the one thing in the project *designed* to be sent back to
# us from a machine nobody here owns, so the same rule has to hold on it or it
# becomes the first exception to it.
#
# **This is the second line, not the first.** The first is not capturing a
# read whose answer is not hardware: the Steam-root lookup answers with
# wherever Steam is installed, and therefore with the Windows account name on
# any PC where that is under the profile, so it is made with
# `hardware._run(capture=False)` and never reaches the dump. Something that is
# not hardware is kept out, not carried in a tidied form.
#
# What survives is a pass over whatever the hardware reads produced, because
# their output is not ours to predict: `system_profiler` is one field away
# from reporting a display's serial, and a read that fails carries the path it
# failed on inside the exception text. Neither would be noticed by whoever
# adds the next read.
#
# Deliberately a pass over the finished dump rather than more logic in
# `hardware.py`: detection is the normal run path, shared with the upload, and
# nothing here may alter what a real run reads or sends.

USER_PLACEHOLDER = "<user>"
HOST_PLACEHOLDER = "<host>"

# The account name's hiding place is the segment after the profile root. The
# root itself is kept — "under a user profile" is exactly the kind of thing
# the dump is for.
_HOME_DIR_RE = re.compile(
    r"(?P<head>(?:[A-Za-z]:)?[\\/](?:Users|home|Documents and Settings)[\\/])"
    r"(?P<name>[^\\/\r\n\"']+)",
    re.IGNORECASE)

_MAC_RE = re.compile(r"\b(?:[0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}\b")
_UUID_RE = re.compile(r"\b[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-"
                      r"[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}\b")

# Nothing read today reports a serial, and this is here so that the next read
# added cannot quietly start doing so — `system_profiler` on macOS and the DMI
# tables both label them this way, and a future field would arrive already
# covered rather than needing somebody to notice.
_SERIAL_LINE_RE = re.compile(
    r"(?im)^(?P<key>[^\r\n]{0,60}?\b(?:serial\s*number|serial|uuid|udid|"
    r"asset\s*tag)\b[ \t]*(?::|=|\bis\b)[ \t]*)(?P<value>\S[^\r\n]*)")

# Short enough and it is not a name any more, it is a substring of something
# else: a two-letter account would rewrite half the board's model number.
_MIN_TOKEN = 3


def _identity_patterns() -> list[tuple[re.Pattern, str]]:
    """This machine's own names, longest first, as whole-word patterns.

    Longest first because a hostname often contains the account name — an
    account `alice` on a machine called `alice-pc` — and replacing the short
    one first would leave `<user>-pc` behind: still a name, just a mangled one.
    """
    found: list[tuple[str, str]] = []
    for value in (os.environ.get("USERNAME"), os.environ.get("USER"),
                  _quietly(getpass.getuser)):
        if value and len(value) >= _MIN_TOKEN:
            found.append((value, USER_PLACEHOLDER))
    for value in (os.environ.get("COMPUTERNAME"), _quietly(platform.node),
                  _quietly(socket.gethostname)):
        if value and len(value) >= _MIN_TOKEN:
            found.append((value, HOST_PLACEHOLDER))
    found.sort(key=lambda pair: len(pair[0]), reverse=True)
    return [(re.compile(rf"\b{re.escape(name)}\b", re.IGNORECASE), placeholder)
            for name, placeholder in found]


def _quietly(fn):
    """`getpass.getuser` raises where there is no account to name."""
    try:
        return fn()
    except Exception:                                 # noqa: BLE001
        return None


def _redact_text(text: str, patterns: list[tuple[re.Pattern, str]]) -> str:
    text = _HOME_DIR_RE.sub(lambda m: m.group("head") + USER_PLACEHOLDER, text)
    for pattern, placeholder in patterns:
        text = pattern.sub(placeholder, text)
    text = _MAC_RE.sub("<mac>", text)
    text = _UUID_RE.sub("<uuid>", text)
    return _SERIAL_LINE_RE.sub(lambda m: m.group("key") + "<redacted>", text)


def redact(data, patterns=None):
    """Every string in the dump, with this machine's identity taken out.

    Applied to `specs` and `raw` alike and to the whole structure rather than
    to the fields known to carry a path today, because the leak arrives in
    whichever read somebody adds next — an error string carrying the path it
    failed on is the obvious one, and nobody would think to redact it.
    """
    if patterns is None:
        patterns = _identity_patterns()
    if isinstance(data, str):
        return _redact_text(data, patterns)
    if isinstance(data, dict):
        return {key: redact(value, patterns) for key, value in data.items()}
    if isinstance(data, list):
        return [redact(value, patterns) for value in data]
    return data


def _dump_dir() -> Path:
    """Next to the running executable.

    For a PyInstaller build, that is the folder holding the `.exe` — the only
    thing somebody has on a machine nobody here owns. For a plain checkout
    there is no executable to sit beside, so it is the current directory.
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path.cwd()


def collect(steam_root: str | None = None) -> dict:
    """Runs detection once; returns the parsed specs and the raw reads.

    Shaped to load straight back as a test fixture: `specs` is
    `SystemSpecs.to_dict()` and `raw` is a flat list of
    `{label, cmd, stdout, error}`, one per read, in the order `detect_system`
    performed them.

    `raw` holds the hardware reads and only those — the Steam-root lookup is
    not captured, so no line of this file says where Steam is or who owns the
    machine. Everything is then put through `redact`, which is the second
    line rather than the first: see the block above.
    """
    specs, raw = hardware.detect_system_with_raw(steam_root)
    return redact({
        "detector_version": report.DETECTOR_VERSION,
        "report_version": report.REPORT_VERSION,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "version": platform.version(),
            "machine": platform.machine(),
        },
        "specs": specs.to_dict(),
        "raw": raw,
    })


def dump(steam_root: str | None = None, out_dir: Path | None = None) -> Path:
    """Writes the diagnostic JSON next to the executable; returns the path."""
    data = collect(steam_root)
    path = (out_dir or _dump_dir()) / FILENAME
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False),
                    encoding="utf-8")
    return path


if __name__ == "__main__":
    written = dump()
    print(f"Wrote {written}")
