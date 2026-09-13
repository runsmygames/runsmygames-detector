# RunsMyGames — the hardware detector

This is the small desktop program that [RunsMyGames](https://runsmygames.com)
asks people to run on their own PC. It works out what the machine is, shows
that on screen, and — only when the person presses the button — sends it to the
site, which opens in their browser and builds the report.

It is published because asking somebody to run a program is asking for trust,
and the only honest answer to *"what does it actually do?"* is the source. You
can read it, build it yourself, and point your build at whatever server you
like.

**MIT licensed.** The site that receives the report is a separate, private
codebase; this repository is the program that runs on your machine and nothing
else.

## What it reads, and what it sends

It reads hardware:

- processor — name, cores, threads, clock;
- graphics adapters;
- memory — how much, what generation, how many slots and how many are filled;
- motherboard and processor socket;
- power supply wattage and cooling, on the rare machine whose firmware admits
  to them;
- operating system, architecture, and the free space on one disk.

That is the whole of it. `shared/specs.py` is the exact structure that travels,
`detector/report.py:build_payload` is the exact object that is uploaded, and
`detector/report.py:describe_payload` turns that same object into the text the
window shows you *before* anything is sent. The claim on screen is checkable
against the code, and the code against the payload — that is the point of the
arrangement, and there is deliberately no second path out.

There is no account in it, no list of games, and no path from your disk. The
program never contacts Steam; you can leave Steam running. Your library reaches
the site because *you* sign in to Steam in your own browser, afterwards, on the
site's side of the fence.

Nothing leaves the machine until you click. The upload is one `POST` to
`/api/hardware`, and `detector/report.py` is the whole of the network surface,
kept in one file so that it can be read in one sitting.

## Why this repository looks brand new

Because its history was not copied.

The project began on **14 August 2026** and lives in a private repository — the
site, the scoring, the requirements catalogue and this program together. When
the detector was split out to be published, the choice was between rewriting
several hundred commits of authorship metadata and starting a clean history.
A clean history won, for the privacy of the people in the old commits, so this
repository's first commit is the day it was extracted rather than the day the
code was written.

For scale: 385 commits in the private repository at the time of extraction, 17
of them touching these files. The code is not a first draft — it has already
been through one substantial rewrite. An earlier version located the Steam
client's cookie store, decrypted it and downloaded the account's library from
the machine itself. That worked, and it was the wrong design: behaviourally it
is indistinguishable from credential-stealing malware, antivirus treated it
exactly that way, and it forced people to close Steam. It was removed and
replaced by the browser sign-in described above. **It is not coming back**, and
several comments in this code exist to say so to whoever reads it next.

## Build it yourself

You need Python 3.10 or newer. On Linux you also need Tk
(`sudo apt install python3-tk`).

```bash
git clone https://github.com/runsmygames/runsmygames-detector.git
cd runsmygames-detector
python build.py --server https://runsmygames.com
```

That writes `detector/_build_config.py`, installs the dependencies, runs
PyInstaller, and leaves the executable in `dist/` — `RunsMyGames.exe` on
Windows, `RunsMyGames` elsewhere. `python build.py --help` lists the rest.

**`--server` is required and has no default**, which is deliberate. The address
is frozen into the executable at build time: whatever you name is where that
binary will post, for the whole of its life, and nothing on the machine that
runs it can be expected to know better. A default would let a build quietly
point at our server, which is not a thing you should be able to do by accident
— name the address and the binary is unambiguously yours.

Point it at your own server, or at `http://127.0.0.1:8000` if you are running
one locally. `RUNSMYGAMES_SERVER` in the environment still overrides the
compiled-in address when you run from a checkout.

### The binary on runsmygames.com is not built from this repository

Say it plainly, because it would be easy to imply otherwise: **the `.exe` the
site hands out is built from the private repository, not from this one.** These
are the same source files, but they are copied here rather than served from
here, and nothing in this repository is a link in the chain that produces the
published download. A binary you build from this clone is your own build, it
carries no provenance attestation, and it is not the file anybody else
downloaded.

If you want to check a download from the site, check it against the site. If
you want to know what the program does, read this.

### How the two are kept from drifting apart

Not by intention — by a check, run against this repository before every
release. It computes a content fingerprint over exactly the files published
here and compares it against the source the next binary is about to be built
from; if the two disagree, the release does not happen until they are brought
back in step, and the file that differs is named rather than guessed at. The
same fingerprint decides, on the private side, whether the binary needs
rebuilding at all — so "the source changed" means the same thing to both
questions instead of two schemes that could quietly disagree with each other.

That check is what makes this page's claim checkable rather than assumed: at
the moment of any given release, what shipped and what is public here are
either provably the same files, or the release was stopped.

## Run it from source

```bash
pip install -r requirements-detector.txt      # Linux also needs python3-tk
RUNSMYGAMES_SERVER=http://127.0.0.1:8000 python -m detector.app
```

On Windows PowerShell, set the variable first:
`$env:RUNSMYGAMES_SERVER = "http://127.0.0.1:8000"`

## The diagnostic dump

`python -m detector.app --diagnose` (or `RunsMyGames.exe --diagnose`) runs
detection once, writes `runsmygames_diagnostic.json` beside where it ran, and
exits. **It uploads nothing** — `report.upload` is never reached from that
path. It holds the parsed result and the raw text each hardware read produced
it from, which is what makes a wrong reading diagnosable at all, and it is put
through `diagnostics.redact` first: account name, hostname, MAC addresses,
UUIDs and anything labelled as a serial are replaced before the file is
written. It is a flag rather than a button because a file that leaves somebody
else's machine should be a thing they chose deliberately.

## Tests

```bash
pip install -r requirements-dev.txt
python -m pytest
```

They need no network, no server and no configuration. The window tests skip
themselves where there is no display.

Two of them are worth knowing about, because they are the ones holding up the
promises this page makes: `tests/detector/test_report.py` asserts against the
**serialised payload** that no uploaded value names the person or the machine —
the earlier version checked field *names* only, so a disk label reading
`/home/<account name>/…` went out off Windows and passed every run, because the
field's name is innocent and nothing looked inside it. And
`tests/detector/test_publishable.py`
pins the set of modules that go into the executable and refuses any import of
the site's code, so "this is all of it" stays a fact rather than a claim.

## Reading the source

A few comments point at files that are not here — `server/scoring.py`,
`docs/detector.md` and the like. Those live in the private repository, and the
references are left in place rather than stripped: the seven modules that make
up the program are the same files the site is built from, unedited, and an
unedited copy is worth more than tidy cross-references. Nothing here *imports*
any of them, and a test enforces that.

---

Not affiliated with Valve or Steam.
