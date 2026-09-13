"""Sends the detected hardware to the server and gets back a link to open.

This is the whole network surface of the detector. The binary posts a hardware
description, receives a one-time token, and opens that link; the library comes
from the user signing in to Steam in their own browser. It never sees a
credential and never touches the Steam client, because a program that reads a
Steam client's cookie store is, behaviourally, indistinguishable from an
infostealer, and antivirus heuristics treat it accordingly.

The payload is versioned because binaries do not update themselves: once a
release is out it keeps sending its own shape forever.
"""
from __future__ import annotations

import os
from urllib.parse import urlparse

import requests

from shared.specs import SystemSpecs

# 3: carries the platform — board, socket, memory generation and slots, and
# whatever the firmware will admit about power and cooling. The server still
# accepts 2, because a released binary keeps sending its own shape forever and
# the platform fields simply arrive as unknown from those.
REPORT_VERSION = 3
DETECTOR_VERSION = "3.1.0"

# Where a finished binary posts to, resolved from three places, most specific
# wins:
#
#   1. `RUNSMYGAMES_SERVER` in the environment at run time — a developer
#      pointing a checkout at a server on their own machine.
#   2. `detector/_build_config.py`, written by the build from the same variable
#      and frozen into the executable.
#   3. The production domain, compiled in as the default.
#
# (2) is the one that matters and it is why this is not just an environment
# read. A binary carries whatever it was built with, so without it one
# downloaded from a test server would post to production and fail, unless the
# person who downloaded it knew about an environment variable. **The machine
# that builds it already knows its own address.** Building it there and freezing
# that in means a download is a download: press the button, run it, come back.
#
# (3) stays a literal in this file deliberately: `build.yml` greps for it before
# allowing a release, so no published binary can point anywhere else. And (2) is
# generated and git-ignored, so a test server's address can never be committed
# and quietly redirect everybody's downloads — the failure that shape of trick
# invites is far worse than the typing it saves.
try:
    from detector._build_config import SERVER_URL as _BUILT_IN   # generated
except ImportError:                        # a plain checkout, or a CI build
    _BUILT_IN = "https://runsmygames.com"

SERVER_URL = os.environ.get("RUNSMYGAMES_SERVER", _BUILT_IN).rstrip("/")

UPLOAD_TIMEOUT_S = 60


class UploadError(Exception):
    pass


def display_url(url: str) -> str:
    """The address with any credentials stripped, for showing to a person.

    A test server sits behind HTTP basic auth, so the address a binary is built
    with can legitimately be `https://user:password@host`. That string is shown
    in the window beside the hardware and repeated in every failure message —
    neither is a place a password belongs, least of all in a window whose whole
    job is to be looked at while somebody decides whether to trust it.

    The host is the last `@`-separated part, so a password containing `@`
    cannot smuggle any of itself into the result.
    """
    scheme, sep, rest = url.partition("://")
    if not sep:
        return url
    return f"{scheme}://{rest.rsplit('@', 1)[-1]}"


_DEFAULT_PORTS = {"http": 80, "https": 443}


def _origin(url: str) -> tuple[str, str, int] | None:
    """(scheme, host, port) for an http(s) address, or None if it is not one.

    Credentials are deliberately dropped: a test server behind basic auth is
    configured as `https://user:password@host` and answers with plain
    `https://host/claim/...`, which is the same place. Host is lowercased and
    the port is filled in from the scheme, so `https://Host` and
    `https://host:443` compare equal.
    """
    try:
        parsed = urlparse(url)
        port = parsed.port
    except ValueError:                       # a malformed port, e.g. `:notanumber`
        return None
    if parsed.scheme not in _DEFAULT_PORTS or not parsed.hostname:
        return None
    return (parsed.scheme, parsed.hostname.lower(),
            port or _DEFAULT_PORTS[parsed.scheme])


def claim_url(base: str, url: object) -> str:
    """The one value in a server response that this program acts on.

    Everything else the detector receives is display text. This string is
    handed to `webbrowser.open`, which on Windows is `os.startfile` — that is
    ShellExecute, and it will take `C:\\...\\payload.exe`, a UNC path like
    `\\\\host\\share\\payload.exe`, or any registered URI scheme. Python's own
    `webbrowser._check_url` rejects a leading dash and nothing else, so it is
    not a check.

    It is checked **here**, where the response is parsed and stops being
    somebody else's data, rather than at the two places in `app.py` that open
    it. A check duplicated at two call sites is a check the third call site
    will not have.

    The rule is the strong one: the link must lead back to the address this
    binary posted to. Not merely "is it http" — a well-formed
    `https://evil.example/` passes that and is still somewhere else. The
    returned value is reassembled from the parse, so tabs and newlines that
    `urlparse` ignores cannot ride along into `ShellExecute`.

    This matters because `RUNSMYGAMES_SERVER` exists and the README that ships
    with the published source documents pointing a build at another server.
    """
    if not isinstance(url, str) or not url.strip():
        raise UploadError("The server did not return a link to continue at.")
    parsed = urlparse(url.strip())
    ours = _origin(base)
    if ours is None or _origin(parsed.geturl()) != ours:
        raise UploadError(
            f"{display_url(base)} answered with a link that leads somewhere "
            "else, so it was not opened. Your report went to that address and "
            "nowhere else. Please report this — it is a bug on our side.")
    return parsed.geturl()


def build_payload(specs: SystemSpecs) -> dict:
    """The exact object that gets uploaded. Nothing is added later."""
    return {
        "report_version": REPORT_VERSION,
        "detector_version": DETECTOR_VERSION,
        "specs": specs.to_dict(),
    }


def describe_payload(payload: dict) -> str:
    """Plain-language summary of what is about to be sent.

    Shown in the window before uploading. The point is that the claim on screen
    is checkable against this function, and this function against the payload.

    A line appears only when there is something in it. That is not tidiness: a
    row reading "Board: unknown" would be describing a value we are not
    sending, on a screen whose whole promise is that it lists what we are.
    """
    specs = payload.get("specs", {})
    gpus = ", ".join(specs.get("gpus") or []) or "unknown"

    memory = f"{specs.get('ram_gb', 0):.0f} GB"
    if specs.get("ram_type"):
        memory += f" {specs['ram_type']}"
    slots = specs.get("ram_slots") or 0
    if slots:
        memory += f", {specs.get('ram_modules') or '?'} of {slots} slots used"

    disk = f"{specs.get('free_disk_gb', 0):.0f} GB"
    if specs.get("disk_label"):
        disk += f" on {specs['disk_label']}"

    lines = [f"Processor: {specs.get('cpu_name') or 'unknown'}",
             f"Graphics:  {gpus}",
             f"Memory:    {memory}"]
    if specs.get("motherboard"):
        lines.append(f"Board:     {specs['motherboard']}")
    if specs.get("psu_watts"):
        lines.append(f"Power:     {specs['psu_watts']} W")
    if specs.get("cooling"):
        lines.append(f"Cooling:   {specs['cooling']}")
    lines += [f"Free disk: {disk}",
              f"System:    {specs.get('os_name') or 'unknown'}"]
    return "\n".join(lines)


def upload(payload: dict, server_url: str | None = None) -> dict:
    """POSTs the hardware and returns {'token', 'url'} to open in a browser."""
    base = (server_url or SERVER_URL).rstrip("/")
    try:
        r = requests.post(f"{base}/api/hardware", json=payload,
                          timeout=UPLOAD_TIMEOUT_S)
    except requests.RequestException as exc:
        # Name the address, as the 401 and 403 branches below do. This is the
        # only branch reachable when the address itself is wrong, so it is the
        # one failure that is *about* the destination, and hiding it would
        # leave nothing on screen to act on.
        raise UploadError(
            f"Could not reach {display_url(base)}.\n\n"
            "Check your internet connection and try again. If that is not "
            "where this report should go, start the detector with "
            "RUNSMYGAMES_SERVER set to the right address.") from exc

    if r.status_code == 413:
        raise UploadError("That report is too large to upload. Please report "
                          "this — it is a bug on our side.")
    if r.status_code in (401, 407):
        # Almost always a preproduction server behind HTTP basic auth rather
        # than anything the API did. Saying so turns a confusing hour into a
        # one-line fix, and the real site never returns this.
        raise UploadError(
            f"{display_url(base)} asked for a username and password. If that's "
            "a test server behind basic auth, put the credentials in the URL:\n"
            "  RUNSMYGAMES_SERVER=https://user:password@host")
    if r.status_code == 403:
        raise UploadError(f"{display_url(base)} refused the upload (403). If "
                          "that's a test server, check it allows this machine.")
    if r.status_code != 200:
        raise UploadError(f"The server refused the report (HTTP {r.status_code}).")
    try:
        data = r.json()
    except ValueError as exc:
        raise UploadError("The server sent back an unreadable answer.") from exc

    if not isinstance(data, dict):
        raise UploadError("The server sent back an unreadable answer.")

    # The trust boundary. Past this line `data["url"]` is a link to our own
    # server and callers may open it; before it, it is a string a server chose.
    data["url"] = claim_url(base, data.get("url"))
    return data
