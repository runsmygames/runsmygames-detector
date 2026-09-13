#!/usr/bin/env python3
"""Builds the detector executable, on any platform, for a server you name.

    python build.py --server https://runsmygames.com

**This file is the same in the private repository and in the public one**, and
that is the whole reason it exists at the root rather than under `detector/`.
Two build scripts describing one build drift the first time either is touched,
and the drift is invisible until somebody's binary posts to the wrong place.
One file, copied verbatim, cannot disagree with itself.

**`--server` is required and has no default.** A build with a default would
quietly produce a binary that looks like ours and posts to our server, from a
checkout nobody here has seen and with no provenance attached to it. Saying
where a binary points is one flag, and it is the flag that makes the resulting
`.exe` yours rather than an unsigned copy of ours.

What it does, in order: writes `detector/_build_config.py` with that address,
then runs PyInstaller over `detector/app.py`. That address is *frozen into the
executable* — the machine that builds it knows its own address, and whoever
downloads it should not have to know anything. `RUNSMYGAMES_SERVER` in the
environment still overrides it at run time, for a developer with a checkout.

**Nothing built here carries a provenance attestation.** Only the project's own
CI can produce one, and only for the repository it runs in. A binary from this
script is exactly as trustworthy as the checkout it came from, which is the
point of being able to build it yourself.
"""
from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent

# `--name RunsMyGames` produces `RunsMyGames.exe` on Windows and `RunsMyGames`
# elsewhere. It is the name people end up with on disk, so it is a flag rather
# than a constant only in the sense that a fork may want its own.
DEFAULT_NAME = "RunsMyGames"

REQUIREMENTS = "requirements-detector.txt"

# A *shape* check on something a person typed, and deliberately not the trust
# check `server/detector_build._usable_origin` performs. That one refuses any
# address it was not configured with, because there the origin arrives in a
# request header the caller controls and gets compiled into an executable other
# people download. Here the address comes from whoever is running the build, on
# their own machine, for their own binary — so the only question worth asking
# is whether it is an address at all. A typo caught now beats "could not reach
# the server" on somebody else's PC.
#
# Credentials are allowed because `detector/report.py` supports them: a test
# server behind HTTP basic auth is legitimately `https://user:password@host`,
# and `report.display_url` keeps that out of the window and out of the errors.
_SERVER_RE = re.compile(
    r"^https?://"                         # scheme, so nothing has to be guessed
    r"([^/@\s]+@)?"                       # optional user:password@
    r"[A-Za-z0-9.\-]+"                    # host
    r"(:\d+)?$")                          # optional port


class BuildError(Exception):
    pass


def check_server(url: str) -> str:
    url = url.rstrip("/")
    if not _SERVER_RE.match(url):
        raise BuildError(
            f"{url!r} does not look like a server address.\n"
            "Expected something like https://example.com, "
            "http://127.0.0.1:8000, or https://user:password@host for a "
            "server behind basic auth. No path, no trailing slash.")
    return url


def write_build_config(server: str) -> Path:
    """The generated module `detector/report.py` reads its address from.

    Deleted first and written whole, never appended to: a file left over from
    an earlier build would send this binary somewhere else with nobody
    noticing, and that is the failure the whole frozen-address design exists to
    prevent. Plain UTF-8 with no byte-order mark, because it is imported as
    Python.

    It is git-ignored in both repositories, so it can never be committed and
    quietly redirect anybody's download.
    """
    path = ROOT / "detector" / "_build_config.py"
    path.unlink(missing_ok=True)
    path.write_text(f'SERVER_URL = "{server}"\n', encoding="utf-8")
    return path


def install_dependencies() -> None:
    requirements = ROOT / REQUIREMENTS
    if not requirements.is_file():
        raise BuildError(f"{REQUIREMENTS} is missing; is this the repository root?")
    run([sys.executable, "-m", "pip", "install", "-r", str(requirements),
         "pyinstaller"], "Installing dependencies")


def run(command: list[str], what: str) -> None:
    print(f"==> {what}")
    result = subprocess.run(command, cwd=str(ROOT))
    if result.returncode != 0:
        raise BuildError(f"{what.lower()} failed (exit {result.returncode}).")


def build(name: str) -> list[Path]:
    """Runs PyInstaller and returns whatever it actually produced.

    `--paths .` is not optional: PyInstaller puts the *script's* directory on
    the path, not the repository root, so without it `from detector import ...`
    and `from shared.specs import ...` do not resolve and the binary fails at
    start-up rather than at build time.

    The output is *found* rather than assumed, because `--windowed` means
    different things per platform — macOS gets an `.app` bundle beside the
    plain executable — and a script that prints a path nothing is at is worse
    than one that prints two.
    """
    dist = ROOT / "dist"
    work = ROOT / "build"
    before = set(dist.glob(f"{name}*")) if dist.is_dir() else set()

    run([sys.executable, "-m", "PyInstaller", "--onefile", "--windowed",
         "--noconfirm", "--name", name,
         # Everything PyInstaller scribbles stays in `build/` and `dist/`, both
         # git-ignored, so a build never leaves a spec file at the root for
         # somebody to wonder about later.
         "--distpath", str(dist), "--workpath", str(work), "--specpath",
         str(work), "--paths", str(ROOT),
         str(ROOT / "detector" / "app.py")], "Building the executable")

    produced = sorted(p for p in dist.glob(f"{name}*")
                      if p.suffix != ".sha256")
    if not produced:
        raise BuildError(
            f"PyInstaller reported success but nothing named {name}* is in "
            f"{dist}. Nothing was built.")
    if before and set(produced) == before:
        print("    (note: dist/ already held these; check the timestamps)")
    return produced


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build the RunsMyGames detector for a server you name.",
        epilog="The address is frozen into the executable. Nothing built here "
               "carries a provenance attestation.")
    parser.add_argument(
        "--server", required=True, metavar="URL",
        help="where the finished binary will post its hardware report, "
             "e.g. https://runsmygames.com or http://127.0.0.1:8000. "
             "Required on purpose: there is no default.")
    parser.add_argument("--name", default=DEFAULT_NAME,
                        help=f"executable name (default: {DEFAULT_NAME})")
    parser.add_argument(
        "--no-install", action="store_true",
        help=f"skip `pip install -r {REQUIREMENTS} pyinstaller` — for a "
             "virtualenv that already has them")
    args = parser.parse_args(argv)

    try:
        server = check_server(args.server)
        if not (ROOT / "detector" / "app.py").is_file():
            raise BuildError("detector/app.py is missing; run this from the "
                             "repository it came with.")
        if not args.no_install:
            install_dependencies()
        elif shutil.which("pyinstaller") is None and \
                subprocess.run([sys.executable, "-c", "import PyInstaller"],
                               capture_output=True).returncode != 0:
            raise BuildError("PyInstaller is not installed and --no-install "
                             "was given.")

        config = write_build_config(server)
        print(f"==> The executable will post to: {server}")
        produced = build(args.name)
    except BuildError as exc:
        print(f"\nBuild failed: {exc}", file=sys.stderr)
        return 1

    print("\nBuilt:")
    for path in produced:
        print(f"  {path}")
    print(f"\nIt posts to {server}, and says so in its own window before it "
          "sends anything.")
    print(f"{config.relative_to(ROOT)} now holds that address. It is "
          "git-ignored and the next build rewrites it.")
    print("This binary carries no provenance attestation; only the project's "
          "own CI can produce one.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
