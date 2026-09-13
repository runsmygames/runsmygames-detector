"""The detector window.

One job, one screen: work out what this computer is, show the user exactly
that, and — only once they click — send it and open the browser, where they
sign in to Steam and the report gets built.

It reads no accounts, no files and no credentials. Everything it can possibly
send is on screen before anything is sent, which is why the window can show the
real payload rather than a promise about it.
"""
from __future__ import annotations

import sys
import threading
import tkinter as tk
import webbrowser
from tkinter import ttk

from detector import hardware, report

# Its own copy rather than the server's: nothing in the detector may import
# `server`, and this is shown in a window that has to work offline.
#
# **This is the detector's own public repository, not the site's.** The window
# offers it as "see exactly what it does", so it has to be a page the person
# clicking can actually open, and the site's repository is private. Only the
# seven modules in `tests/detector/test_publishable.PUBLISHED` are over there,
# which is the whole of what this program is.
SOURCE_URL = "https://github.com/runsmygames/runsmygames-detector"

BODY_WIDTH = 520


class DetectorApp(tk.Tk):
    def __init__(self, server_url: str | None = None):
        super().__init__()
        self.server_url = server_url
        self.title("RunsMyGames")
        self.geometry("600x520")
        # Height only, and it is not a nicety: the detail block has rows for
        # the board, the power supply and the cooling, and how many appear
        # depends on what this particular machine will admit to. A fixed height
        # that fits a desktop clips a machine that reports more.
        self.resizable(False, True)
        self.minsize(600, 460)
        self.payload: dict | None = None
        self._build_ui()
        self.after(200, self._start_detection)

    # ------------------------------------------------------------ UI
    def _build_ui(self) -> None:
        frame = ttk.Frame(self, padding=24)
        frame.pack(fill="both", expand=True)

        ttk.Label(frame, text="RunsMyGames",
                  font=("", 16, "bold")).pack(anchor="w")
        self.headline = tk.StringVar(value="Checking what this computer is…")
        ttk.Label(frame, textvariable=self.headline, wraplength=BODY_WIDTH,
                  font=("", 10)).pack(anchor="w", pady=(6, 12))

        # The bar lives in a strip of its own so that hiding it does not move
        # everything below it up and down. Hiding is the point: a stopped
        # indeterminate bar is drawn *empty*, which is indistinguishable from
        # one that is stuck at the beginning, so a finished scan would read as
        # still running. No bar at all is the only unambiguous way to say that
        # nothing is happening.
        self.progress_strip = ttk.Frame(frame, height=18)
        self.progress_strip.pack(fill="x")
        self.progress_strip.pack_propagate(False)
        self.progress = ttk.Progressbar(self.progress_strip,
                                        mode="indeterminate")
        self.progress.pack(fill="both", expand=True)
        self.progress.start(12)

        # What was found. Monospaced so the labelled rows line up.
        self.detail = tk.StringVar(value="")
        ttk.Label(frame, textvariable=self.detail, wraplength=BODY_WIDTH,
                  justify="left", font=("Consolas", 9),
                  foreground="#444").pack(anchor="w", pady=(14, 0))

        # Where it goes, in the same column as what goes. The window's whole
        # claim is that everything it sends is on screen first, and *where* is
        # part of that — a binary downloaded from a test server still carries
        # the compiled-in address, so the only way to know which one it will
        # use is to be told before pressing the button rather than after.
        ttk.Label(frame, text=f"Sends to:  {report.display_url(self._server())}",
                  wraplength=BODY_WIDTH, justify="left", font=("Consolas", 9),
                  foreground="#444").pack(anchor="w", pady=(6, 0))

        self.disclosure = ttk.Label(
            frame, wraplength=BODY_WIDTH, justify="left", foreground="#555",
            text=("This is everything that gets sent. Your browser opens next, "
                  "where you sign in through Steam so we know which games to "
                  "check against — your password is only ever typed on Steam's "
                  "own site."))
        self.disclosure.pack(anchor="w", pady=(12, 0))
        self.disclosure.pack_forget()

        link = ttk.Label(frame, text="Open source — see exactly what it does",
                         foreground="#0b57d0", cursor="hand2")
        link.pack(anchor="w", pady=(8, 0))
        link.bind("<Button-1>", lambda _e: webbrowser.open(SOURCE_URL))

        buttons = ttk.Frame(frame)
        buttons.pack(side="bottom", fill="x", pady=(16, 0))
        self.action = ttk.Button(buttons, text="Continue in browser",
                                 command=self._upload, state="disabled")
        self.action.pack(side="right")
        self.retry = ttk.Button(buttons, text="Retry",
                                command=self._start_detection)
        self.retry.pack(side="right", padx=(0, 8))
        self.retry.pack_forget()

    # --------------------------------------------------------- helpers
    def _server(self) -> str:
        """The address this run will upload to, whatever chose it."""
        return self.server_url or report.SERVER_URL

    def _set_state(self, headline: str, detail: str = "", *,
                   busy: bool = False, can_upload: bool = False,
                   can_retry: bool = False) -> None:
        """Single place that moves the window between its states."""
        self.headline.set(headline)
        self.detail.set(detail)
        if busy:
            self.progress.pack(fill="both", expand=True)
            self.progress.start(12)
        else:
            self.progress.stop()
            self.progress.pack_forget()
        self.action.configure(state="normal" if can_upload else "disabled")
        if can_upload:
            self.disclosure.pack(anchor="w", pady=(12, 0))
        if can_retry:
            self.retry.pack(side="right", padx=(0, 8))
        else:
            self.retry.pack_forget()

    def _on_ui(self, fn, *args, **kwargs) -> None:
        """Run a callback on the tkinter thread."""
        self.after(0, lambda: fn(*args, **kwargs))

    # ------------------------------------------------------ detection
    def _start_detection(self) -> None:
        self._set_state("Checking what this computer is…", busy=True)
        threading.Thread(target=self._detect, daemon=True).start()

    def _detect(self) -> None:
        try:
            specs = hardware.detect_system()
        except Exception as exc:                      # noqa: BLE001
            self._on_ui(self._failed, f"Could not read your hardware: {exc}")
            return
        self._on_ui(self._ready, report.build_payload(specs))

    def _ready(self, payload: dict) -> None:
        self.payload = payload
        self._set_state("Here's what we found:",
                        report.describe_payload(payload), can_upload=True)

    def _failed(self, message: str) -> None:
        self.payload = None
        self._set_state("Couldn't finish", message, can_retry=True)

    # --------------------------------------------------------- upload
    def _upload(self) -> None:
        if not self.payload:
            return
        self._set_state("Opening your browser…",
                        report.describe_payload(self.payload), busy=True)
        threading.Thread(target=self._do_upload, daemon=True).start()

    def _do_upload(self) -> None:
        try:
            result = report.upload(self.payload, self.server_url)
        except report.UploadError as exc:
            self._on_ui(self._failed, str(exc))
            return
        self._on_ui(self._done, result)

    def _done(self, result: dict) -> None:
        webbrowser.open(result["url"])
        self._set_state(
            "Continue in your browser.",
            "Sign in through Steam there and your report will be ready.\n\n"
            f"{result['url']}\n\n"
            "If nothing opened, copy that link into your browser.")
        self.action.configure(text="Open again", state="normal",
                              command=lambda: webbrowser.open(result["url"]))


def main() -> None:
    # A developer-only entry point, not a button: no window, no upload, just
    # the raw hardware dump described in docs/detector.md. Kept out of the UI
    # entirely so the window's copy never has to mention it.
    if "--diagnose" in sys.argv[1:]:
        from detector import diagnostics
        path = diagnostics.dump()
        print(f"Wrote {path}")
        return
    DetectorApp().mainloop()


if __name__ == "__main__":
    main()
