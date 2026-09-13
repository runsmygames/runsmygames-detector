"""What ships inside the binary, pinned rather than inherited.

This repository is the detector and nothing else: the desktop program people
are asked to run on their own machine, published so that they can read it. The
site that receives its report is a separate codebase and is not published. That
bargain is only honest while the binary's source actually stands alone, so the
publishable set is pinned here rather than left to whatever happens to be on
disk:

- the modules that go into the executable are written down, so a file
  appearing under `detector/` or `shared/` is a decision somebody made on
  purpose rather than a file that arrived;
- nothing in it may import the site's code — which is what makes the set above
  buildable on its own;
- its third-party dependencies must be the ones `requirements-detector.txt`
  declares, because a clone builds from that file and from nothing else.

One file is in the binary and not in the repository: `_build_config.py`, which
`build.py` writes with the address that build will post to, and `.gitignore`
keeps out. It is listed in `GENERATED` rather than read off the disk, because
"whatever is under `detector/` right now" means different things on a machine
that has built the binary and on a fresh clone.

**This file is deliberately not identical to its counterpart in the private
repository**, and it is the only one that is not. It describes the boundary
between the two, and a boundary reads differently from each side of it.
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

# The packages the binary is made of. `shared/` is in here because the detector
# imports SystemSpecs from it, and the site imports the very same file — which
# is exactly why it is listed rather than assumed.
PUBLISHABLE = ("detector", "shared")

# Generated at build time and git-ignored, so it goes into the *binary* and
# never into the repository. Named here rather than discovered, because a test
# that quietly changes shape depending on whether somebody has built the
# detector is a test that guards one machine: compared against whatever is on
# disk, it would pass wherever a build has run and fail on a fresh clone, which
# has no such file. The address is stamped separately from the source, on both
# sides of the copy, and this is that distinction.
GENERATED = frozenset({"detector/_build_config.py"})

# Everything a clone actually contains. A file appearing under `detector/` or
# `shared/` has to be added here on purpose.
PUBLISHED = frozenset({
    "detector/__init__.py",
    "detector/app.py",
    "detector/diagnostics.py",
    "detector/hardware.py",
    "detector/report.py",
    "shared/__init__.py",
    "shared/specs.py",
})

# Modules the standard library provides, so they need no requirements line.
# Kept as a set rather than probed, because `sys.stdlib_module_names` includes
# things a frozen binary does not necessarily ship.
STDLIB = set(sys.stdlib_module_names)


def _relative(path: Path) -> str:
    return str(path.relative_to(ROOT)).replace("\\", "/")


def _modules() -> list[Path]:
    """Source files on disk, minus anything the build wrote.

    Everything the binary is made of, except the generated module — so this
    returns the same thing on a machine that has built the detector and on one
    that has not.
    """
    out = []
    for package in PUBLISHABLE:
        out.extend(sorted(p for p in (ROOT / package).rglob("*.py")
                          if "__pycache__" not in p.parts
                          and _relative(p) not in GENERATED))
    return out


def _imported_roots(path: Path) -> set[str]:
    """Top-level package names this module imports."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            # A relative import stays inside the package, so it is fine and
            # `node.module` may be None.
            if node.level == 0 and node.module:
                roots.add(node.module.split(".")[0])
    return roots


def _declared_dependencies() -> set[str]:
    text = (ROOT / "requirements-detector.txt").read_text(encoding="utf-8")
    out = set()
    for line in text.splitlines():
        line = line.split("#")[0].strip()
        if not line:
            continue
        out.add(re.split(r"[<>=!\[ ]", line)[0].lower())
    return out


def test_the_publishable_half_is_the_set_we_think_it_is():
    """A new file in the binary should be noticed, not inherited."""
    assert {_relative(p) for p in _modules()} == set(PUBLISHED)


def test_the_generated_module_is_excluded_because_it_is_never_committed():
    """The exclusion above has to be earned, not asserted.

    A name in `GENERATED` is a hole in the previous test, so each one must be
    genuinely absent from the repository — which means git-ignored. If an
    ignore rule is ever dropped, the file becomes committable and belongs in
    `PUBLISHED` instead; this is what makes that a failure rather than a
    quietly wider hole.
    """
    ignored = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    ignored = {line.strip() for line in ignored}
    for name in GENERATED:
        assert name in ignored, f"{name} is excluded here but is committable"
        assert name not in PUBLISHED


@pytest.mark.parametrize("path", [ROOT / p for p in sorted(PUBLISHED)],
                         ids=lambda p: p.name)
def test_nothing_publishable_reaches_into_the_server(path):
    """The one import that would turn the promise into a lie.

    From inside this repository it can only pass: there is no site code here to
    import. It is kept because the same seven files live in the private
    repository too, where `server/` *is* importable — so an import added there
    would build and run perfectly well, and only every clone would be broken.
    This is where that shows up as a failure instead.
    """
    assert "server" not in _imported_roots(path), (
        f"{path.name} imports server/, which cannot be published with it")


def test_the_dependencies_are_the_ones_the_public_build_would_install():
    """A clone builds from this repository's requirements file, not from ours."""
    declared = _declared_dependencies() | {"pyinstaller"}
    local = {Path(p).stem for p in PUBLISHED} | set(PUBLISHABLE) | \
        {Path(p).stem for p in GENERATED}

    third_party = set()
    for path in (ROOT / p for p in sorted(PUBLISHED)):
        for root in _imported_roots(path):
            if root in STDLIB or root in local:
                continue
            third_party.add(root.lower())

    missing = third_party - declared
    assert not missing, (
        f"{sorted(missing)} is imported by the binary but not in "
        "requirements-detector.txt, so a clone would not build")
