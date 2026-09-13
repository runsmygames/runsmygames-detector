"""The local-only diagnostic dump that `--diagnose` writes. See "The diagnostic
dump" in the README.
"""
from __future__ import annotations

import dataclasses
import getpass
import json
import socket

import pytest

from detector import diagnostics, hardware, report


@pytest.fixture
def fake_raw():
    return [{"label": "cpu_name (Win32_Processor)",
            "cmd": ["powershell", "-NoProfile", "-Command", "..."],
            "stdout": "AMD Ryzen 7 5800X 8-Core Processor\n", "error": None}]


def test_collect_shapes_the_dump_around_specs_and_raw(monkeypatch, modern_pc,
                                                       fake_raw):
    monkeypatch.setattr(hardware, "detect_system_with_raw",
                        lambda steam_root=None: (modern_pc, fake_raw))

    data = diagnostics.collect()

    assert data["specs"] == modern_pc.to_dict()
    assert data["raw"] == fake_raw
    assert data["detector_version"] == report.DETECTOR_VERSION
    assert data["report_version"] == report.REPORT_VERSION
    assert "captured_at" in data
    assert data["platform"]["system"]


def test_collect_never_uploads_anything(monkeypatch, modern_pc):
    """The whole point of a *local* diagnostic: nothing here may call
    `report.upload`, not even by accident."""
    def explode(*_a, **_kw):
        raise AssertionError("diagnostics tried to upload")
    monkeypatch.setattr(report, "upload", explode)
    monkeypatch.setattr(hardware, "detect_system_with_raw",
                        lambda steam_root=None: (modern_pc, []))

    diagnostics.collect()  # would have raised above if it tried


def test_dump_writes_readable_json_next_to_the_given_directory(
        tmp_path, monkeypatch, modern_pc, fake_raw):
    monkeypatch.setattr(hardware, "detect_system_with_raw",
                        lambda steam_root=None: (modern_pc, fake_raw))

    path = diagnostics.dump(out_dir=tmp_path)

    assert path == tmp_path / diagnostics.FILENAME
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["specs"]["cpu_name"] == modern_pc.cpu_name
    assert data["raw"] == fake_raw


def test_dump_dir_uses_the_current_directory_for_a_plain_checkout(
        monkeypatch, tmp_path):
    monkeypatch.setattr(diagnostics.sys, "frozen", False, raising=False)
    monkeypatch.chdir(tmp_path)
    assert diagnostics._dump_dir() == tmp_path


def test_dump_dir_sits_beside_the_frozen_executable(monkeypatch, tmp_path):
    exe = tmp_path / "RunsMyGames.exe"
    exe.write_bytes(b"")
    monkeypatch.setattr(diagnostics.sys, "frozen", True, raising=False)
    monkeypatch.setattr(diagnostics.sys, "executable", str(exe))
    assert diagnostics._dump_dir() == tmp_path


# ------------------------------------------------- hardware and nothing else
#
# This file is the one artefact in the project designed to leave a stranger's
# machine and come back to us, so the rule the upload payload is held to —
# hardware and nothing else — has to hold here or it becomes the first
# exception to it. `test_publishable.py` pins the payload against what it
# serialises; these pin the dump against the file it actually writes.
#
# Two layers, in order. The Steam-root read is not captured at all, because
# the answer to "the dump carries something that is not hardware" is to stop
# carrying it. `redact` is the second layer, over output the hardware reads
# produce and nobody here controls.


def _written(tmp_path, monkeypatch, specs, raw) -> tuple[dict, str]:
    """Runs the real `dump` over a machine we describe, and reads it back."""
    monkeypatch.setattr(hardware, "detect_system_with_raw",
                        lambda steam_root=None: (specs, raw))
    path = diagnostics.dump(out_dir=tmp_path)
    text = path.read_text(encoding="utf-8")
    return json.loads(text), text


def test_where_steam_is_installed_never_reaches_the_dump():
    """A real detection on this machine: no line of it says where Steam is.

    The read still happens — free disk space is worked out from the library
    folder, and a disk figure is hardware. What is not hardware is the folder,
    which on a PC where Steam sits under the profile is the Windows account
    name. So the read is made with `capture=False` and the dump never sees it.
    """
    _specs, raw = hardware.detect_system_with_raw()

    for entry in raw:
        blob = json.dumps(entry).lower()
        assert "steam" not in blob, entry["label"]
        assert "valve" not in blob, entry["label"]


def test_the_dump_holds_the_hardware_reads_and_only_those():
    """Stated as what is in it, so a read added later has to be looked at."""
    _specs, raw = hardware.detect_system_with_raw()

    assert raw, "a real machine reports something"
    for entry in raw:
        assert entry["label"].split()[0] in {"cpu_name", "gpus", "platform"}, \
            entry["label"]


def test_a_profile_path_that_did_get_through_is_still_redacted(
        tmp_path, monkeypatch, modern_pc):
    """The second layer, on the shape the first one keeps out.

    `specs.disk_label` cannot be a path — `_drive_label` builds a
    volume name from a fixed vocabulary — and a read is only captured when its
    answer is a component. This asserts what happens anyway if either of those
    is ever loosened, which is the point of having two.
    """
    specs = dataclasses.replace(modern_pc,
                                disk_label="/home/jparsons/.local/share/Steam")

    data, text = _written(tmp_path, monkeypatch, specs, [])

    assert "jparsons" not in text
    assert data["specs"]["disk_label"] == "/home/<user>/.local/share/Steam"


def test_a_failed_read_names_the_path_it_failed_on(tmp_path, monkeypatch,
                                                   modern_pc):
    """The error string is where the next leak arrives.

    Nobody writing a new platform read thinks of it, because the path is not
    something the code chose to record — the exception carries it.
    """
    raw = [{"label": "platform (dmi)", "cmd": ["read:/sys/.../board_vendor"],
            "stdout": "",
            "error": "FileNotFoundError: [Errno 2] No such file or "
                     "directory: '/home/jparsons/.steam/steam'"}]

    data, text = _written(tmp_path, monkeypatch, modern_pc, raw)

    assert "jparsons" not in text
    assert "/home/<user>/.steam/steam" in data["raw"][0]["error"]


def test_serials_uuids_and_macs_are_taken_out(tmp_path, monkeypatch,
                                              modern_pc):
    """Nothing read today reports any of these, and that is the point.

    macOS `system_profiler` is one field away from doing so, and a read added
    later should arrive already covered rather than waiting for somebody to
    notice.
    """
    raw = [{"label": "gpus (system_profiler SPDisplaysDataType)",
            "cmd": ["system_profiler", "SPDisplaysDataType"],
            "stdout": "Chipset Model: Apple M2\n"
                      "      Serial Number: C02XY1234ZZ\n"
                      "      Hardware UUID: 4b0b2b1e-9c1a-4f2b-8a3d-1122aabbccdd\n"
                      "      MAC Address: a4:83:e7:1f:2c:9d\n",
            "error": None}]

    data, text = _written(tmp_path, monkeypatch, modern_pc, raw)

    assert "C02XY1234ZZ" not in text
    assert "4b0b2b1e" not in text
    assert "a4:83:e7" not in text
    # The reading itself is what the dump exists for and survives all of it.
    assert "Apple M2" in data["raw"][0]["stdout"]


def test_this_machines_own_names_never_reach_the_file(tmp_path, monkeypatch,
                                                      modern_pc):
    """Not a shape test: the real account and host names of whoever runs it.

    A path is the way the name normally arrives, so most of it is covered
    above. This is the backstop for the way it arrives that nobody predicted —
    an environment dump, a window title, a share name — and it is written
    against this machine so it means something on every machine it runs on.
    """
    user = getpass.getuser()
    host = socket.gethostname()
    raw = [{"label": "hypothetical", "cmd": ["whoami"],
            "stdout": f"logged in as {user} on {host}\n", "error": None}]

    _data, text = _written(tmp_path, monkeypatch, modern_pc, raw)

    if len(user) >= diagnostics._MIN_TOKEN:
        assert user.lower() not in text.lower()
    if len(host) >= diagnostics._MIN_TOKEN:
        assert host.lower() not in text.lower()


def test_the_hardware_readings_come_through_untouched(tmp_path, monkeypatch,
                                                      modern_pc, fake_raw):
    """Over-redaction would be quieter than the leak and nearly as bad.

    Every field here is what the dump is *for*; a pass that eats a model
    number leaves a file that looks fine and answers nothing.
    """
    data, _text = _written(tmp_path, monkeypatch, modern_pc, fake_raw)

    assert data["specs"] == modern_pc.to_dict()
    assert data["raw"] == fake_raw
