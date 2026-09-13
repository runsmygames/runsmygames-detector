"""The machine description that travels from the detector to the server.

This dataclass is the payload's core: the detector fills it in by probing the
real machine (detector/hardware.py) and the server scores games against it
(server/scoring.py). Because both sides depend on it, it lives here on its own
with no third-party imports.

It is also what the upgrade simulator manipulates: `dataclasses.replace(specs,
gpus=["RTX 4060"])` produces a hypothetical machine that `scoring.evaluate`
scores exactly like a real one.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field, fields
from functools import cached_property

# Fields the client is allowed to set. Anything else in an uploaded payload is
# dropped rather than trusted — this is the trust boundary between the binary
# on someone's PC and the server.
#
# 2: the platform fields — motherboard, socket, memory generation and slots,
#    power supply, cooling. Old binaries keep sending version 1 payloads
#    forever, so every one of them defaults to "unknown" and every consumer has
#    to cope with that. See `SystemSpecs` below for which are readable at all.
SPEC_VERSION = 2


def _bounded(value: int, low: int, high: int) -> int:
    """Clamp to a plausible range, or 0 when it is outside one entirely."""
    return value if low <= value <= high else 0


def _text(value: object, limit: int = 120) -> str:
    """A string that can still be encoded after it has been stored.

    JSON can carry a lone surrogate — `"AB\\ud800CD"` decodes to a perfectly
    ordinary Python string — and nothing here or in the scorer minds. It fails
    much later and somewhere else: encoding the finished claim page to UTF-8
    raises, and the visitor gets a 500 for a field they never see. Dropped at
    the trust boundary, with the truncation, for the same reason everything
    else in `from_dict` is coerced here rather than defended against
    downstream.

    Cleaned before truncating, so the cap counts characters that survive.
    """
    return str(value).encode("utf-8", "ignore").decode("utf-8")[:limit]


@dataclass
class SystemSpecs:
    os_name: str = ""
    os_family: str = ""        # "windows" | "linux" | "mac" | ""
    os_version: float = 0.0    # 10.0 / 11.0 on Windows, 14.4 on macOS, 0 elsewhere
    arch_bits: int = 64
    cpu_name: str = ""
    cpu_cores: int = 0
    cpu_threads: int = 0
    cpu_freq_ghz: float = 0.0
    ram_gb: float = 0.0
    gpus: list[str] = field(default_factory=list)
    free_disk_gb: float = 0.0
    disk_label: str = ""       # drive/mount the free space refers to

    # ------------------------------------------------- the platform (v2)
    #
    # What an upgrade has to physically fit into, so that the simulator does
    # not recommend parts that do not go in the machine it is looking at — an
    # AM5 processor for an AM4 board, priced as if the board and the memory
    # came free.
    #
    # **Every one of these can be unknown, and two of them nearly always are.**
    # That is not pessimism, it is what the machine exposes:
    #
    #   motherboard, cpu_socket, ram_type, ram_slots, ram_modules
    #       read from firmware tables (SMBIOS/DMI). Reliable on a desktop.
    #   psu_watts
    #       a power supply is a dumb box with no data line to the system. No
    #       operating system can read its rating. 0 means unknown and will
    #       stay 0 unless a user ever tells us.
    #   cooling
    #       the cooler is equally mute. At best the firmware reports that some
    #       fans exist. "" means unknown.
    #
    # So consumers must treat unknown as "say so", never as "assume the worst"
    # or "assume it is fine" — a recommendation resting on an invented power
    # supply is worse than one that admits the gap and tells you what to check.
    motherboard: str = ""      # vendor and product, e.g. "MSI MAG B550 TOMAHAWK"
    cpu_socket: str = ""       # normalised: "AM4", "AM5", "LGA1700", ""
    ram_type: str = ""         # "DDR3" | "DDR4" | "DDR5" | ""
    ram_slots: int = 0         # memory slots on the board, 0 = unknown
    ram_modules: int = 0       # slots actually populated, 0 = unknown
    psu_watts: int = 0         # 0 = unknown, which is the usual answer
    cooling: str = ""          # "" = unknown; else a short descriptor

    # Virtual adapters that are not real GPUs (VR, streaming, remote...)
    _VIRTUAL = (r"virtual|meta\b|parsec|displaylink|spacedesk|idd\b|remote|"
                r"teamviewer|vnc|basic (display|render)|microsoft (hyper|remote)|"
                r"citrix|vmware|virtualbox|qxl|dummy")
    _INTEGRATED = (r"intel.*(uhd|hd|iris)|amd.*graphics$|"
                   r"radeon\s*\(tm\)\s*graphics")

    @cached_property
    def gpu_name(self) -> str:
        """Main GPU: discards virtual adapters and prefers dedicated ones.

        Cached per instance, and that matters more than it looks: the scorer
        reads it once per game per candidate, so uncached, a large library
        runs three regexes a hundred thousand times and costs seconds on every
        page view. Caching is safe because specs are built once and never
        mutated — the simulator makes hypothetical machines with
        `dataclasses.replace`, which returns a fresh object with a fresh cache.
        Mutating `gpus` in place after reading this would go stale; don't.
        """
        real = [g for g in self.gpus if not re.search(self._VIRTUAL, g, re.I)]
        if not real:
            return self.gpus[0] if self.gpus else ""
        dedicated = [g for g in real if not re.search(self._INTEGRATED, g, re.I)]
        # When in doubt, prefer known dedicated GPU vendors
        branded = [g for g in dedicated
                   if re.search(r"nvidia|geforce|rtx|gtx|radeon rx|arc", g, re.I)]
        return (branded or dedicated or real)[0]

    # ------------------------------------------------------ serialization

    def to_dict(self) -> dict:
        """Plain dict for the JSON payload."""
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: object) -> "SystemSpecs":
        """Rebuilds specs from an uploaded payload, coercing every field.

        Deliberately forgiving and deliberately strict: unknown keys are
        ignored and every known one is cast to its declared type, so a
        malformed or hostile upload yields harmless defaults instead of
        breaking the scoring code far away from here.
        """
        if not isinstance(raw, dict):
            return cls()
        out = cls()
        for f in fields(cls):
            if f.name not in raw:
                continue
            value = raw[f.name]
            try:
                if f.name == "gpus":
                    if not isinstance(value, list):
                        continue
                    # Cap both count and length: this string is rendered later.
                    out.gpus = [_text(g) for g in value[:8]]
                elif f.type in ("str", str):
                    setattr(out, f.name, _text(value))
                elif f.type in ("int", int):
                    setattr(out, f.name, int(value))
                elif f.type in ("float", float):
                    setattr(out, f.name, float(value))
            except (TypeError, ValueError):
                continue          # keep the default for this field

        # The platform numbers get a second pass. Coercing them to int is not
        # enough: they are used in arithmetic and repeated back in sentences,
        # so a negative slot count or a ten-gigawatt power supply has to become
        # "unknown" here rather than something a page prints. Same reasoning as
        # capping the strings, applied to the values instead of the lengths.
        out.ram_slots = _bounded(out.ram_slots, 0, 32)
        out.ram_modules = _bounded(out.ram_modules, 0, out.ram_slots or 32)
        out.psu_watts = _bounded(out.psu_watts, 0, 3000)
        # Normalised on the way in, so that everything downstream can compare
        # sockets and memory generations without each caller remembering to.
        out.cpu_socket = out.cpu_socket.strip().upper()[:16]
        out.ram_type = out.ram_type.strip().upper()[:8]
        return out

    @property
    def ram_slots_free(self) -> int | None:
        """Empty memory slots, or None when the machine did not say.

        A number rather than a boolean because "add a stick" and "replace both
        sticks" are different amounts of money. None rather than 0 because the
        two mean opposite things and a 0 that meant "we don't know" would make
        the page tell someone their board is full when it may be half empty.
        """
        if not self.ram_slots:
            return None
        return max(0, self.ram_slots - self.ram_modules)

    def summary(self) -> str:
        """One-line description, for the report header and the OG image."""
        parts = []
        if self.cpu_name:
            parts.append(self.cpu_name)
        if self.gpu_name:
            parts.append(self.gpu_name)
        if self.ram_gb:
            parts.append(f"{self.ram_gb:.0f} GB RAM")
        return "  ·  ".join(parts)
