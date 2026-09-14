"""The detector window.

Tk needs a display, so these skip where there isn't one (headless CI). They
still run on the Windows and desktop-Linux builds, which is where the binary
actually ships. What they guard is the promise the window makes: nothing is
sent before the user clicks, and the text shown is the real payload.
"""
from __future__ import annotations

import pytest

tkinter = pytest.importorskip("tkinter")

from detector import app as detector_app          # noqa: E402
from detector import report                       # noqa: E402


@pytest.fixture(scope="module")
def _root():
    """One Tk root for the whole module.

    Tk does not take kindly to several `tk.Tk()` instances being created and
    destroyed in one process: one per test makes these fail at random.
    """
    original = detector_app.DetectorApp._start_detection
    # Patched on the class, not with monkeypatch, because this outlives a test.
    detector_app.DetectorApp._start_detection = lambda self: None
    try:
        win = detector_app.DetectorApp(server_url="http://localhost:1")
    except tkinter.TclError as exc:               # headless: no display
        detector_app.DetectorApp._start_detection = original
        pytest.skip(f"no Tk display: {exc}")
    yield win
    win.destroy()
    detector_app.DetectorApp._start_detection = original


@pytest.fixture
def window(_root):
    """The shared window, returned to its opening state."""
    _root.payload = None
    _root._set_state("Checking what this computer is…")
    return _root


def test_the_window_opens_with_nothing_ready_to_send(window):
    assert window.payload is None
    assert str(window.action["state"]) == "disabled"


def test_the_bar_is_only_on_screen_while_something_is_happening(window,
                                                                modern_pc):
    """A stopped indeterminate bar is drawn empty, which reads as stuck.

    With the scan finished and the results on screen, an empty bar underneath
    them says the machine is still being read. Asking the geometry manager
    whether the bar is packed is the question
    that holds here — `winfo_ismapped` answers about a toplevel these tests
    never put on screen, so it is false throughout and proves nothing.
    """
    window._set_state("Checking…", busy=True)
    assert window.progress in window.progress_strip.pack_slaves()

    window._ready(report.build_payload(modern_pc))
    assert window.progress not in window.progress_strip.pack_slaves()


def test_the_window_says_where_it_will_send_before_it_sends(window):
    """Where it goes is part of what the window promises to show up front.

    Through `display_url`, so that a test server's basic-auth password cannot
    end up drawn on screen in the window somebody is looking at while they
    decide whether to trust the program.
    """
    shown = [w.cget("text") for w in window.winfo_children()[0].winfo_children()
             if "text" in w.keys()]
    assert any(report.display_url(window._server()) in str(t) for t in shown)


def test_detection_failure_offers_a_retry_and_stays_disabled(window):
    window._failed("Could not read your hardware: nope")
    assert window.payload is None
    assert str(window.action["state"]) == "disabled"
    assert "nope" in window.detail.get()


def test_a_ready_payload_enables_the_button_and_shows_what_will_be_sent(
        window, modern_pc):
    payload = report.build_payload(modern_pc)
    window._ready(payload)

    assert str(window.action["state"]) == "normal"
    shown = window.detail.get()
    # The text on screen has to be derived from the payload, not written by hand.
    assert shown == report.describe_payload(payload)
    assert modern_pc.cpu_name in shown


def test_uploading_without_a_payload_does_nothing(window, monkeypatch):
    """Guards the click path: no payload, no request, ever."""
    def explode(*_a, **_kw):
        raise AssertionError("upload attempted with no payload")

    monkeypatch.setattr(report, "upload", explode)
    window.payload = None
    window._upload()


def test_detection_only_reads_hardware(window, monkeypatch, modern_pc):
    """Detection fills the payload with hardware and nothing about games.

    The library comes from the browser's sign-in, never from the detector: a
    binary that reads a Steam cookie store looks exactly like an infostealer to
    antivirus heuristics.
    """
    monkeypatch.setattr(detector_app.hardware, "detect_system",
                        lambda: modern_pc)
    window._detect()
    window.update()
    assert window.payload["specs"]["cpu_name"] == modern_pc.cpu_name
    assert "games" not in window.payload
    assert "wishlist" not in window.payload


def test_a_failed_upload_is_explained_and_retryable(window, monkeypatch,
                                                    modern_pc):
    window.payload = report.build_payload(modern_pc)
    monkeypatch.setattr(report, "upload", lambda *_a, **_k: (_ for _ in ()).throw(
        report.UploadError("Could not reach the server.")))
    window._do_upload()
    window.update()
    assert "Could not reach the server." in window.detail.get()
    assert str(window.retry.winfo_manager()) != ""


def test_diagnose_flag_writes_a_dump_and_never_opens_the_window(monkeypatch,
                                                                 tmp_path):
    """A hidden flag, not a button. This must not instantiate `DetectorApp`
    at all, so it needs no Tk display and runs everywhere the rest of this
    module gets skipped."""
    from detector import diagnostics

    monkeypatch.setattr(detector_app.sys, "argv",
                        ["RunsMyGames.exe", "--diagnose"])
    calls = []
    monkeypatch.setattr(diagnostics, "dump",
                        lambda: calls.append(True) or tmp_path / "d.json")

    def explode(*_a, **_kw):
        raise AssertionError("the window must not open in --diagnose mode")
    monkeypatch.setattr(detector_app, "DetectorApp", explode)

    detector_app.main()
    assert calls == [True]


def test_success_hands_over_the_link(window, monkeypatch, modern_pc):
    opened = []
    monkeypatch.setattr(detector_app.webbrowser, "open", opened.append)
    window._done({"token": "abc", "url": "https://example/claim/abc"})
    window.update()
    assert opened == ["https://example/claim/abc"]
    # The link is also shown, so a browser that refused to open isn't a dead end.
    assert window.link_entry.get() == "https://example/claim/abc"


def test_the_shown_link_can_be_selected_and_copied(window, monkeypatch):
    """The screen that exists for a browser that did not open.

    Whoever reads this screen has to get the address into a browser by hand,
    so both ways of taking it have to be there: a field they can select from,
    and a control that does it for them. A `ttk.Label` offers neither, which
    is what this replaced.
    """
    monkeypatch.setattr(detector_app.webbrowser, "open", lambda _u: None)
    window._done({"token": "abc", "url": "https://example/claim/abc"})
    window.update()

    assert window.link_entry in window.link_entry.master.pack_slaves()
    assert str(window.link_entry["state"]) == "readonly"    # selectable, not dead
    assert window.copy in window.copy.master.pack_slaves()

    window.clipboard_clear()
    window._copy_link()
    window.update()
    assert window.clipboard_get() == "https://example/claim/abc"


def test_copying_says_it_copied_and_then_offers_the_job_again(window,
                                                               monkeypatch):
    """A control that answers nothing gets pressed again."""
    monkeypatch.setattr(detector_app.webbrowser, "open", lambda _u: None)
    window._done({"token": "abc", "url": "https://example/claim/abc"})
    window._copy_link()
    assert window.copy["text"] == detector_app.COPIED_LABEL

    window._offer_copy_again()
    assert window.copy["text"] == detector_app.COPY_LABEL


def test_the_link_and_its_copy_control_only_exist_once_there_is_a_link(window):
    """Every other state has nothing to copy, so it shows nothing to copy."""
    window._set_state("Checking what this computer is…", busy=True)
    assert window.link_entry not in window.link_entry.master.pack_slaves()
    assert window.copy not in window.copy.master.pack_slaves()

    window._failed("Could not read your hardware: nope")
    assert window.link_entry not in window.link_entry.master.pack_slaves()
    assert window.copy not in window.copy.master.pack_slaves()


def test_copying_with_no_link_touches_nothing(window):
    """The guard that keeps an empty state from emptying the clipboard."""
    window.clipboard_clear()
    window.clipboard_append("something the user already had")
    window._set_link("")
    window._copy_link()
    assert window.clipboard_get() == "something the user already had"


def test_the_copy_control_reads_as_words_as_well_as_a_glyph():
    """Tk draws an uncovered character as an empty box, and which characters a
    machine covers varies. The label has to survive that, so the icon is never
    the only thing on the button."""
    for label in (detector_app.COPY_LABEL, detector_app.COPIED_LABEL):
        words = "".join(c for c in label if c.isascii()).strip()
        assert words and any(c.isalpha() for c in words), label
