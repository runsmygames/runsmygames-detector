"""GPU selection and OS/architecture detection helpers."""
from __future__ import annotations

from pathlib import Path, PurePosixPath, PureWindowsPath
from unittest import mock

import pytest

from detector import hardware
from shared.specs import SystemSpecs


def test_virtual_adapters_are_ignored():
    specs = SystemSpecs(gpus=["Meta Virtual Monitor", "Parsec Virtual Display",
                              "NVIDIA GeForce RTX 3070"])
    assert specs.gpu_name == "NVIDIA GeForce RTX 3070"


def test_dedicated_gpu_wins_over_integrated():
    specs = SystemSpecs(gpus=["Intel(R) UHD Graphics 770",
                              "NVIDIA GeForce RTX 4060 Laptop GPU"])
    assert "RTX 4060" in specs.gpu_name


def test_integrated_gpu_is_used_when_it_is_the_only_one():
    specs = SystemSpecs(gpus=["Intel(R) Iris(R) Xe Graphics"])
    assert specs.gpu_name == "Intel(R) Iris(R) Xe Graphics"


def test_only_virtual_adapters_still_returns_something():
    specs = SystemSpecs(gpus=["Parsec Virtual Display Adapter"])
    assert specs.gpu_name == "Parsec Virtual Display Adapter"


def test_no_gpus_returns_empty_string():
    assert SystemSpecs().gpu_name == ""


def test_detect_arch_bits(monkeypatch):
    monkeypatch.setattr(hardware.platform, "machine", lambda: "AMD64")
    assert hardware._detect_arch_bits() == 64
    monkeypatch.setattr(hardware.platform, "machine", lambda: "i686")
    assert hardware._detect_arch_bits() == 32


def test_detect_os_reports_family(monkeypatch):
    monkeypatch.setattr(hardware.platform, "system", lambda: "Linux")
    monkeypatch.setattr(hardware.platform, "release", lambda: "6.8.0")
    name, family, version = hardware._detect_os()
    assert family == "linux" and name.startswith("Linux") and version == 0.0


def test_steam_root_override_is_searched_first(tmp_path):
    lib = tmp_path / "steamapps" / "libraryfolders.vdf"
    lib.parent.mkdir(parents=True)
    lib.write_text('"libraryfolders"{"0"{"path"\t"' + str(tmp_path) + '"}}',
                   encoding="utf-8")
    paths = hardware.steam_library_paths(tmp_path)
    assert tmp_path in paths


# ------------------------------------------------- the raw diagnostic sink
#
# `_run` and `_read_raw` are the two places a read's raw text can be
# captured, for `detect_system_with_raw` / `detector/diagnostics.py`. Nothing
# above changes when no sink is active (the default, and what every other
# test in this file exercises) — these check the capture itself.

def test_run_is_silent_with_no_sink_active(monkeypatch):
    monkeypatch.setattr(hardware.subprocess, "run",
                        lambda *_a, **_k: type("R", (), {"stdout": "hi"})())
    assert hardware._raw_sink() is None
    assert hardware._run(["echo", "hi"], label="x") == "hi"


def test_run_records_label_cmd_stdout_and_no_error_on_success():
    hardware._raw_local.sink = (raw := [])
    try:
        monkeypatch_stdout = type("R", (), {"stdout": "AMD Ryzen 7\n"})()
        with mock.patch.object(hardware.subprocess, "run",
                               return_value=monkeypatch_stdout):
            out = hardware._run(["powershell", "-Command", "x"], label="cpu")
    finally:
        hardware._raw_local.sink = None

    assert out == "AMD Ryzen 7\n"
    assert raw == [{"label": "cpu", "cmd": ["powershell", "-Command", "x"],
                    "stdout": "AMD Ryzen 7\n", "error": None}]


def test_run_records_the_error_when_the_command_cannot_run():
    hardware._raw_local.sink = (raw := [])
    try:
        with mock.patch.object(hardware.subprocess, "run",
                               side_effect=FileNotFoundError("no such program")):
            out = hardware._run(["nope"], label="x")
    finally:
        hardware._raw_local.sink = None

    assert out == ""
    assert raw[0]["stdout"] == ""
    assert raw[0]["error"] == "FileNotFoundError: no such program"


def test_read_raw_captures_a_file_read(tmp_path):
    target = tmp_path / "board_vendor"
    target.write_text("ASUSTeK COMPUTER INC.", encoding="utf-8")
    hardware._raw_local.sink = (raw := [])
    try:
        text = hardware._read_raw("platform (board_vendor)", target)
    finally:
        hardware._raw_local.sink = None

    assert text == "ASUSTeK COMPUTER INC."
    assert raw == [{"label": "platform (board_vendor)",
                    "cmd": [f"read:{target}"],
                    "stdout": "ASUSTeK COMPUTER INC.", "error": None}]


def test_read_raw_records_the_error_when_the_file_is_missing(tmp_path):
    missing = tmp_path / "does_not_exist"
    hardware._raw_local.sink = (raw := [])
    try:
        text = hardware._read_raw("platform (missing)", missing)
    finally:
        hardware._raw_local.sink = None

    assert text == ""
    assert raw[0]["error"] is not None
    assert "does_not_exist" in raw[0]["error"] or "No such file" in raw[0]["error"]


def test_detect_system_with_raw_matches_detect_system_and_resets_the_sink():
    """Same specs `detect_system` would produce, plus a non-empty raw log,
    and the sink is not left armed for whatever calls `detect_system` next."""
    specs, raw = hardware.detect_system_with_raw()

    assert specs.cpu_name  # detect_system always returns *something*
    assert isinstance(raw, list) and raw
    for entry in raw:
        assert set(entry) == {"label", "cmd", "stdout", "error"}
        assert entry["label"]

    assert hardware._raw_sink() is None  # not left active


# --------------------------------------------------- the free-space label
#
# `disk_label` is uploaded, so it can never be the Steam library folder itself:
# off Windows that is a path like `/home/<account name>/.local/share/Steam`, in
# a payload that carries hardware and nothing else. These pin the rule: a
# label is a volume, and a volume is not a place inside itself.


def _mounted(monkeypatch, *mounts):
    """A machine whose mount table is exactly `mounts`, off Windows."""
    monkeypatch.setattr(hardware.platform, "system", lambda: "Linux")
    monkeypatch.setattr(hardware.os.path, "ismount",
                        lambda p: str(p) in set(mounts))


@pytest.mark.parametrize("library, mounts, expected", [
    # A home directory, in both of its shapes.
    ("/home/jparsons/.local/share/Steam", ("/",), "/"),
    ("/home/jparsons/.local/share/Steam", ("/", "/home"), "/home"),
    # A removable disk: udisks puts the account name *between* the media root
    # and the volume's own name, so "the last segment" is right here only
    # because the depth is pinned.
    ("/media/jparsons/GamesSSD/SteamLibrary",
     ("/media/jparsons/GamesSSD",), "GamesSSD"),
    ("/run/media/jparsons/T7/Steam", ("/run/media/jparsons/T7",), "T7"),
    ("/mnt/games/Steam", ("/mnt/games",), "games"),
    ("/Volumes/Macintosh HD/Steam", ("/Volumes/Macintosh HD",), "Macintosh HD"),
])
def test_a_label_names_the_volume_and_not_the_person(monkeypatch, library,
                                                     mounts, expected):
    _mounted(monkeypatch, *mounts)
    assert hardware._drive_label(PurePosixPath(library)) == expected


@pytest.mark.parametrize("library, mounts", [
    # The trap: `/media/<user>` is one segment short of a volume, so the last
    # segment is the account name. It must fall through, not be handed back.
    ("/media/jparsons/Steam", ("/media/jparsons",)),
    ("/run/media/jparsons/Steam", ("/run/media/jparsons",)),
    # Anything the vocabulary does not cover falls through too, which is what
    # makes this a rule about what a label may be rather than a blocklist.
    ("/export/homes/jparsons/Steam", ("/export/homes/jparsons",)),
    ("/home/jparsons/Steam", ("/home/jparsons",)),
])
def test_anything_not_in_the_vocabulary_is_not_a_label(monkeypatch, library,
                                                       mounts):
    _mounted(monkeypatch, *mounts)
    label = hardware._drive_label(PurePosixPath(library))

    assert label == hardware.UNNAMED_VOLUME
    assert "jparsons" not in label


def test_no_label_off_windows_can_ever_be_a_path(monkeypatch):
    """The property, rather than another example.

    Whatever the mount table says, the answer is a volume name or a mount
    point this platform defines — never a directory somebody owns. A separator
    anywhere but the front is what a path looks like.
    """
    for mounts in (("/",), ("/home",), ("/home/jparsons",),
                   ("/media/jparsons",), ("/media/jparsons/Disk",),
                   ("/tmp/mnt/jparsons/x",)):
        _mounted(monkeypatch, *mounts)
        label = hardware._drive_label(
            PurePosixPath("/home/jparsons/.local/share/Steam"))
        assert "jparsons" not in label
        assert "/" not in label.lstrip("/")


def test_windows_still_answers_with_the_drive(monkeypatch):
    """`PureWindowsPath`, not `Path`, and that is the whole trick.

    `Path` re-parses with the rules of whatever box the suite runs on, so a
    Windows path on a Linux runner has no drive and this stops testing the
    branch it names.
    """
    monkeypatch.setattr(hardware.platform, "system", lambda: "Windows")
    assert hardware._drive_label(PureWindowsPath(r"D:\SteamLibrary")) == "D:\\"
    # A UNC library folder has no drive letter, and `\server\share` is not a
    # label — it names somebody's network, which is not hardware either.
    assert hardware._drive_label(
        PureWindowsPath(r"\fileserver\games\Steam")) == hardware.UNNAMED_VOLUME


# ------------------------------------------------------------ the board
#
# `_clean_board` is the only thing standing between an unfilled firmware field
# and a server that reads the board as a fact about the socket. A placeholder
# that got through would not look like a bug: it would look like a board we
# have no row for, which is a sentence the site is willing to say.

@pytest.mark.parametrize("reported", [
    "To Be Filled By O.E.M. To Be Filled By O.E.M.",
    "Default string Default string",
    "System manufacturer System Product Name",
    "OEM",
    "INVALID",
    "None",
    "",
    "   ",
    " - ",
])
def test_a_placeholder_board_is_dropped_rather_than_reported(reported):
    assert hardware._clean_board(reported) == ""


@pytest.mark.parametrize("reported, cleaned", [
    # A string a real machine returns, unedited.
    ("ASUSTeK COMPUTER INC. PRIME B550M-A",
     "ASUSTeK COMPUTER INC. PRIME B550M-A"),
    ("  Micro-Star International Co., Ltd.   MAG B550 TOMAHAWK  ",
     "Micro-Star International Co., Ltd. MAG B550 TOMAHAWK"),
    ("Gigabyte Technology Co., Ltd.\tB450M DS3H",
     "Gigabyte Technology Co., Ltd. B450M DS3H"),
])
def test_a_real_board_survives_with_its_model_intact(reported, cleaned):
    """Whitespace is normalised and nothing else is.

    The model number is what `compatibility.read_board` matches a chipset in,
    so anything that edits the middle of this string edits the socket.
    """
    assert hardware._clean_board(reported) == cleaned


def test_a_board_name_is_bounded():
    """Firmware is free text from a stranger's machine and the field is stored
    and shown; 120 characters is more than any real board needs."""
    assert len(hardware._clean_board("ASUS " + "X" * 500)) == 120
