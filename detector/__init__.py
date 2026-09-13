"""The detector: the small binary that runs on the player's PC.

It probes the hardware, shows the user exactly what it found, and — only once
they click — uploads that and nothing else, then opens the browser where they
sign in to Steam and the report is built. It reads no account and never talks
to Steam — the library comes from the browser's sign-in. All the analysis
happens server-side, so the scoring can improve without anyone downloading a
new binary.
"""
