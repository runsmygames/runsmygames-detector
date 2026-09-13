"""Building and uploading the hardware payload.

The tests that matter here are the ones about what the payload does *not*
contain. The detector does not read Steam at all, and that claim is made on
the download page, in the privacy policy and in the window itself.
"""
from __future__ import annotations

import dataclasses
import getpass
import json
from pathlib import PurePosixPath
from types import SimpleNamespace

import pytest
import requests

from detector import hardware, report


class _Resp:
    def __init__(self, status, payload=None):
        self.status_code = status
        self._payload = payload

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


@pytest.fixture
def payload(modern_pc):
    return report.build_payload(modern_pc)


def test_the_compiled_in_server_is_the_real_domain():
    """`build.yml` greps this file for exactly this literal before a release.

    The guard fails closed: anything that stops the string appearing — this
    default moved to another module, a debugging localhost left behind — blocks
    the tag rather than shipping a binary that uploads nowhere. A published
    binary cannot be corrected, only replaced, so the check is worth having on
    both sides: here it fails in seconds, there it fails at release time.

    Read from the source rather than from the imported constant, because the
    constant honours the environment and the guard does not.
    """
    from pathlib import Path

    source = Path(report.__file__).read_text(encoding="utf-8")
    assert '"https://runsmygames.com"' in source


# ------------------------------------------- where a binary decides to post
#
# The address is frozen when the binary is built rather than read on the
# machine that runs it. That is what lets a detector downloaded from a test
# server talk back to that server: the machine that builds it knows its own
# address, and the person downloading it should not have to know anything.


@pytest.fixture
def rebuilt(monkeypatch):
    """Re-resolve `report.SERVER_URL` under a chosen build and environment."""
    import importlib
    import sys
    import types

    def load(*, env=None, built_in=None):
        if env is None:
            monkeypatch.delenv("RUNSMYGAMES_SERVER", raising=False)
        else:
            monkeypatch.setenv("RUNSMYGAMES_SERVER", env)
        if built_in is None:
            # `None` in sys.modules makes the import raise ImportError, which
            # is the plain-checkout case: no file was generated.
            monkeypatch.setitem(sys.modules, "detector._build_config", None)
        else:
            module = types.ModuleType("detector._build_config")
            module.SERVER_URL = built_in
            monkeypatch.setitem(sys.modules, "detector._build_config", module)
        return importlib.reload(report)

    yield load
    monkeypatch.undo()             # before the reload, so it sees reality again
    importlib.reload(report)


def test_a_build_can_freeze_in_its_own_address(rebuilt):
    assert rebuilt(built_in="https://box.ts.net").SERVER_URL == \
        "https://box.ts.net"


def test_the_environment_still_overrides_what_was_built_in(rebuilt):
    """The environment wins over the built-in address, for a checkout."""
    module = rebuilt(env="http://127.0.0.1:8000", built_in="https://box.ts.net")
    assert module.SERVER_URL == "http://127.0.0.1:8000"


def test_with_neither_it_is_production(rebuilt):
    assert rebuilt().SERVER_URL == "https://runsmygames.com"


@pytest.mark.parametrize("url, shown", [
    ("https://tester:hunter2@box.ts.net", "https://box.ts.net"),
    ("https://tester:p@ss@box.ts.net", "https://box.ts.net"),
    ("https://box.ts.net", "https://box.ts.net"),
    ("http://127.0.0.1:8000", "http://127.0.0.1:8000"),
    ("not-a-url", "not-a-url"),
])
def test_a_shown_address_never_carries_the_password(url, shown):
    """A test server is behind basic auth, so the built-in address may hold one.

    It is printed in the window beside the hardware and repeated in every
    failure message. A password with an `@` in it is the case worth pinning:
    splitting on the first `@` would leave half of it on screen.
    """
    assert report.display_url(url) == shown


def test_failures_do_not_echo_the_password_back(monkeypatch, payload):
    def boom(*_a, **_k):
        raise requests.ConnectionError("no route to host")

    monkeypatch.setattr(report.requests, "post", boom)
    with pytest.raises(report.UploadError) as caught:
        report.upload(payload, "https://tester:hunter2@box.ts.net")
    assert "hunter2" not in str(caught.value)
    assert "box.ts.net" in str(caught.value)


def test_the_credentials_are_still_sent_even_though_they_are_not_shown(
        monkeypatch, payload):
    """Redaction is for display only — the request itself must keep them."""
    seen = {}

    def capture(url, **kwargs):
        seen["url"] = url
        return _Resp(200, {"url": "https://box.ts.net/claim/abc"})

    monkeypatch.setattr(report.requests, "post", capture)
    report.upload(payload, "https://tester:hunter2@box.ts.net")
    assert seen["url"] == "https://tester:hunter2@box.ts.net/api/hardware"


def test_the_generated_address_is_never_committable():
    """A test server's address in the repository would redirect every download.

    It would also be invisible: the file is imported, not read, so nothing on
    any page would look wrong until reports stopped arriving. Ignoring it is
    the whole defence, so the defence gets a test.
    """
    from pathlib import Path

    ignored = (Path(__file__).resolve().parents[2] /
               ".gitignore").read_text(encoding="utf-8")
    assert "detector/_build_config.py" in ignored


# ------------------------------------------------------------- contents

def test_payload_is_versioned(payload):
    """Binaries never update themselves, so the server must know the shape."""
    assert payload["report_version"] == report.REPORT_VERSION
    assert payload["detector_version"]


def test_payload_carries_only_hardware(payload):
    assert payload["specs"]["cpu_name"]
    assert "games" not in payload
    assert "wishlist" not in payload


def test_payload_contains_nothing_about_the_account(payload):
    """No SteamID, no token, no cookie — checked against the whole blob."""
    blob = json.dumps(payload).lower()
    for forbidden in ("steamid", "access_token", "token", "cookie",
                      "password", "session", "appid"):
        assert forbidden not in blob


def test_payload_is_json_serialisable(payload):
    assert json.loads(json.dumps(payload)) == payload


def test_description_matches_what_is_actually_sent(payload, modern_pc):
    """The window shows this text; it has to describe the real payload."""
    text = report.describe_payload(payload)
    assert modern_pc.cpu_name in text
    assert modern_pc.gpus[0] in text
    assert "32 GB" in text


# --------------------------------------------------------------- upload

def test_upload_posts_hardware_and_returns_the_link(monkeypatch, payload):
    seen = {}

    def fake_post(url, **kwargs):
        seen["url"] = url
        seen["json"] = kwargs.get("json")
        return _Resp(200, {"token": "abc",
                           "url": "http://localhost:8000/claim/abc"})

    monkeypatch.setattr(report.requests, "post", fake_post)
    result = report.upload(payload, "http://localhost:8000/")
    assert seen["url"] == "http://localhost:8000/api/hardware"
    assert seen["json"] == payload
    assert result["url"] == "http://localhost:8000/claim/abc"


def test_offline_gives_a_human_message(monkeypatch, payload):
    def boom(*_a, **_k):
        raise requests.ConnectionError("no route to host")

    monkeypatch.setattr(report.requests, "post", boom)
    with pytest.raises(report.UploadError, match="internet connection"):
        report.upload(payload)


def test_being_unable_to_reach_it_says_which_address(monkeypatch, payload):
    """The one failure that is about the address must not hide the address.

    A binary pointed at a domain that does not resolve fails in this branch,
    and "Could not reach the server" alone gives nothing to act on; naming the
    address makes the cause self-evident, as the 401 and 403 branches do.
    """
    def boom(*_a, **_k):
        raise requests.ConnectionError("no route to host")

    monkeypatch.setattr(report.requests, "post", boom)
    with pytest.raises(report.UploadError, match=r"https://example\.test"):
        report.upload(payload, "https://example.test")


@pytest.mark.parametrize("status", [400, 413, 500, 503])
def test_a_refused_upload_raises_rather_than_pretending(monkeypatch, payload,
                                                        status):
    monkeypatch.setattr(report.requests, "post", lambda *a, **k: _Resp(status))
    with pytest.raises(report.UploadError):
        report.upload(payload)


@pytest.mark.parametrize("status", [401, 407])
def test_basic_auth_says_so_instead_of_a_bare_status_code(monkeypatch, payload,
                                                          status):
    """Pointing the detector at a password-protected preproduction server.

    The 401 comes from the proxy, not the API, and a bare "HTTP 401" would
    cost somebody an hour working out why.
    """
    monkeypatch.setattr(report.requests, "post", lambda *a, **k: _Resp(status))
    with pytest.raises(report.UploadError, match="username and password"):
        report.upload(payload, "https://prepro.example")


def test_an_answer_without_a_link_is_an_error(monkeypatch, payload):
    monkeypatch.setattr(report.requests, "post",
                        lambda *a, **k: _Resp(200, {"token": "abc"}))
    with pytest.raises(report.UploadError, match="link"):
        report.upload(payload)


def test_unreadable_answer_is_an_error(monkeypatch, payload):
    monkeypatch.setattr(report.requests, "post", lambda *a, **k: _Resp(200))
    with pytest.raises(report.UploadError, match="unreadable"):
        report.upload(payload)


# ------------------------------------------------- the link is the only value
#                                                    a response gets to choose
#
# The contract: **`upload` returns a link that leads back to the address it
# posted to, or it raises.** Everything else a response carries is display
# text; this one string is handed to `webbrowser.open`, which on Windows is
# `os.startfile` — ShellExecute — and will take a local path, a UNC path or
# any registered URI scheme. Python's `webbrowser._check_url` rejects a leading
# dash and nothing else.
#
# The assertions are on what `report.upload` returns or raises, deliberately,
# and not on `webbrowser.open` being patched: `app.py` opens the link in two
# places today and the guarantee has to survive a third.
#
# Not a remote attack on the official binary, which talks to
# `https://runsmygames.com` with certificate verification — but live for anyone
# who sets `RUNSMYGAMES_SERVER`, which the published README invites.

POSTED_TO = "https://runsmygames.com"

REFUSED_LINKS = [
    pytest.param("C:\\Users\\Public\\payload.exe", id="absolute-windows-path"),
    pytest.param("\\\\attacker.example\\share\\payload.exe", id="unc-path"),
    pytest.param("file:///C:/Users/Public/payload.exe", id="file-scheme"),
    pytest.param("javascript:alert(1)", id="javascript-scheme"),
    pytest.param("ms-msdt:/id PCWDiagnostic", id="registered-uri-scheme"),
    # The one that makes scheme-checking insufficient: well-formed, https,
    # and still not us.
    pytest.param("https://evil.example/claim/abc", id="well-formed-other-host"),
    pytest.param("https://runsmygames.com.evil.example/claim/abc",
                 id="our-host-as-a-prefix-of-theirs"),
    pytest.param("https://evil.example/?x=https://runsmygames.com/",
                 id="our-host-in-their-query"),
    pytest.param("http://runsmygames.com/claim/abc", id="scheme-downgraded"),
    pytest.param("https://runsmygames.com:8443/claim/abc", id="other-port"),
    pytest.param("-vim", id="leading-dash-the-only-thing-webbrowser-checks"),
    pytest.param("", id="empty"),
    pytest.param(None, id="missing"),
    pytest.param(12, id="not-even-a-string"),
]


@pytest.mark.parametrize("url", REFUSED_LINKS)
def test_a_link_that_does_not_lead_back_to_us_is_refused(monkeypatch, payload,
                                                         url):
    monkeypatch.setattr(report.requests, "post",
                        lambda *a, **k: _Resp(200, {"token": "abc",
                                                    "url": url}))
    with pytest.raises(report.UploadError):
        report.upload(payload, POSTED_TO)


ACCEPTED_LINKS = [
    pytest.param(POSTED_TO, "https://runsmygames.com/claim/abc",
                 "https://runsmygames.com/claim/abc", id="the-normal-case"),
    pytest.param("https://runsmygames.com/", "https://runsmygames.com/claim/abc",
                 "https://runsmygames.com/claim/abc", id="posted-with-a-slash"),
    pytest.param("http://127.0.0.1:8000", "http://127.0.0.1:8000/claim/abc",
                 "http://127.0.0.1:8000/claim/abc", id="local-development"),
    pytest.param(POSTED_TO, "https://RunsMyGames.com/claim/abc",
                 "https://RunsMyGames.com/claim/abc", id="host-case-differs"),
    pytest.param(POSTED_TO, "https://runsmygames.com:443/claim/abc",
                 "https://runsmygames.com:443/claim/abc", id="explicit-default-port"),
    # A preproduction server is configured as `https://user:password@host` and
    # answers as itself. Refusing this would break the one deployment shape
    # `display_url` exists for.
    pytest.param("https://user:pw@prepro.example",
                 "https://prepro.example/claim/abc",
                 "https://prepro.example/claim/abc", id="basic-auth-in-our-address"),
]


@pytest.mark.parametrize("base, answered, opened", ACCEPTED_LINKS)
def test_a_link_back_to_the_address_we_posted_to_is_opened(monkeypatch, payload,
                                                           base, answered,
                                                           opened):
    monkeypatch.setattr(report.requests, "post",
                        lambda *a, **k: _Resp(200, {"token": "abc",
                                                    "url": answered}))
    assert report.upload(payload, base)["url"] == opened


def test_the_refusal_names_our_address_and_never_the_password(monkeypatch,
                                                              payload):
    monkeypatch.setattr(report.requests, "post",
                        lambda *a, **k: _Resp(200,
                                              {"url": "https://evil.example/"}))
    with pytest.raises(report.UploadError) as raised:
        report.upload(payload, "https://user:hunter2@prepro.example")
    assert "prepro.example" in str(raised.value)
    assert "hunter2" not in str(raised.value)


def test_whitespace_cannot_ride_along_into_shellexecute(monkeypatch, payload):
    """`urlparse` ignores tabs and newlines; `ShellExecute` would not.

    So the value handed back is reassembled from the parse rather than being
    the raw string that was validated.
    """
    monkeypatch.setattr(
        report.requests, "post",
        lambda *a, **k: _Resp(200,
                              {"url": "https://runsmygames.com/cl\taim/abc\n"}))
    opened = report.upload(payload, POSTED_TO)["url"]
    assert opened == "https://runsmygames.com/claim/abc"


def test_an_answer_that_is_not_an_object_is_an_error(monkeypatch, payload):
    """`r.json()` can legally return a list, and `.get` would have raised."""
    monkeypatch.setattr(report.requests, "post",
                        lambda *a, **k: _Resp(200, ["https://evil.example/"]))
    with pytest.raises(report.UploadError, match="unreadable"):
        report.upload(payload, POSTED_TO)


# ------------------------------------------- hardware and nothing else, by value
#
# `test_payload_contains_nothing_about_the_account` above checks **key names**:
# it fails if a field is called `steamid` or `cookie`. It never looks at a
# single value, so a field with an entirely innocent name such as `disk_label`
# could carry `/home/<account name>/.local/share/Steam` and still pass. The
# payload carries hardware and nothing else, and that holds only when it is
# asserted against the serialised values, on every platform's detection path
# rather than only the one the suite happens to run on.
#
# So: build the payload the way each platform's detection builds it, and assert
# against the values.

_HOME_SEGMENTS = ("/home/", "/users/", "/root/", "\\users\\",
                  "documents and settings")

# The mount points a system defines for itself. Any other label has to be a
# volume's own name, and a volume's name has no separators in it.
_MOUNT_LABELS = frozenset(hardware._SYSTEM_MOUNTS)


def _string_values(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _string_values(item)
    elif isinstance(value, list):
        for item in value:
            yield from _string_values(item)


def _detected(monkeypatch, system, library, mounts):
    """The specs a real run on that platform would produce.

    The free-space branch runs for real — `_detect_free_disk` into
    `_drive_label` — because that is where a path could enter the payload.
    Everything else is
    the shared fixture, which is what makes a failure here point at the field
    that changed rather than at the mocking.
    """
    monkeypatch.setattr(hardware.platform, "system", lambda: system)
    monkeypatch.setattr(hardware, "steam_library_paths",
                        lambda root=None: [PurePosixPath(library)])
    monkeypatch.setattr(hardware.shutil, "disk_usage",
                        lambda p: SimpleNamespace(free=500 * 1024 ** 3))
    monkeypatch.setattr(hardware.os.path, "ismount",
                        lambda p: str(p) in set(mounts))
    free_disk_gb, disk_label = hardware._detect_free_disk()
    return free_disk_gb, disk_label


@pytest.mark.parametrize("system, library, mounts", [
    ("Linux", "/home/jparsons/.local/share/Steam", ("/",)),
    ("Linux", "/home/jparsons/.local/share/Steam", ("/", "/home")),
    ("Linux", "/home/jparsons/.steam/steam", ("/home/jparsons",)),
    ("Linux", "/media/jparsons/GamesSSD/SteamLibrary",
     ("/media/jparsons/GamesSSD",)),
    ("Darwin", "/Users/jparsons/Library/Application Support/Steam", ("/",)),
    ("Darwin", "/Users/jparsons/Library/Application Support/Steam",
     ("/System/Volumes/Data",)),
])
def test_no_uploaded_value_names_the_person_off_windows(monkeypatch, modern_pc,
                                                        system, library,
                                                        mounts):
    """No uploaded value names the person, on the platforms without drives.

    Windows answers with a drive letter. Everywhere else the free-space figure
    is read from a library folder that usually sits under the account's home,
    so the label derived from it is where the account name would travel.
    """
    free_disk_gb, disk_label = _detected(monkeypatch, system, library, mounts)
    specs = dataclasses.replace(modern_pc, free_disk_gb=free_disk_gb,
                                disk_label=disk_label,
                                os_name=f"{system} test", os_family="linux")

    blob = json.dumps(report.build_payload(specs))

    assert "jparsons" not in blob
    for value in _string_values(json.loads(blob)):
        lowered = value.lower()
        for segment in _HOME_SEGMENTS:
            assert segment not in lowered, value


@pytest.mark.parametrize("system, library, mounts", [
    ("Linux", "/home/jparsons/.local/share/Steam", ("/",)),
    ("Linux", "/media/jparsons/GamesSSD/SteamLibrary",
     ("/media/jparsons/GamesSSD",)),
    ("Darwin", "/Users/jparsons/Library/Application Support/Steam", ("/",)),
])
def test_the_disk_label_is_a_volume_rather_than_a_place_on_one(
        monkeypatch, system, library, mounts):
    """Stated as what the field may hold, so a new path shape cannot slip in.

    A label is one of the mount points a platform defines, or a volume's own
    name — and a volume's name contains no separator. Nothing else is a label.
    """
    _free, label = _detected(monkeypatch, system, library, mounts)

    assert label in _MOUNT_LABELS or "/" not in label
    assert "\\" not in label


def test_the_running_account_name_never_reaches_a_payload(modern_pc):
    """Against this machine, so it means something wherever it runs.

    A real detection here, not a fixture: the fields come from the hardware
    this suite is running on, which is the only sample of a real machine any
    test has.
    """
    user = getpass.getuser()
    blob = json.dumps(report.build_payload(hardware.detect_system())).lower()

    if len(user) >= 3:
        assert user.lower() not in blob


# --------------------------------------------------------------- the board
#
# The board is load-bearing: `server/compatibility.read_board` turns it into
# the socket, which decides whether an upgrade is a €200 processor or a €450
# platform change. It is in the payload from REPORT_VERSION 3, and without an
# assertion that it arrives, a change to `_detect_platform` could drop it in
# silence and the only symptom would be worse advice.

def test_the_board_reaches_the_payload_as_the_machine_reported_it(modern_pc):
    """Against the serialised blob, not the dataclass, and against the value.

    The vendor prefix stays on: "ASUSTeK COMPUTER INC. PRIME B550M-A" is what
    `Win32_BaseBoard` actually returns, and the server is written to take it
    whole. Trimming it here would make this test pass on a string no machine
    sends.
    """
    board = "ASUSTeK COMPUTER INC. PRIME B550M-A"
    specs = dataclasses.replace(modern_pc, motherboard=board)

    blob = json.loads(json.dumps(report.build_payload(specs)))

    assert blob["specs"]["motherboard"] == board


def test_the_window_says_the_board_is_among_what_it_sends(modern_pc):
    """The window's promise is that everything sent is on screen first, so a
    field that is sent and not shown breaks it whichever way round it is."""
    board = "ASUSTeK COMPUTER INC. PRIME B550M-A"
    specs = dataclasses.replace(modern_pc, motherboard=board)

    text = report.describe_payload(report.build_payload(specs))

    assert board in text
    assert "Board:" in text


def test_a_machine_that_would_not_say_shows_no_board_row(modern_pc):
    """The mirror of the rule above, and the reason the row is conditional: a
    line reading "Board: unknown" describes a value we are not sending, on a
    screen whose whole claim is that it lists the ones we are."""
    specs = dataclasses.replace(modern_pc, motherboard="")

    text = report.describe_payload(report.build_payload(specs))

    assert "Board:" not in text
