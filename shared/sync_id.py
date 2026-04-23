"""Shared sync_id strategies — the canonical "which identifier do we use?"
logic for every client and the server.

Background
----------
Different systems want different identifiers:

  * NDS  →  ``00048000`` + hex(4-byte gamecode)  — matches NDS/3DS homebrew
  * 3DS  →  native 16-char hex title_id
  * PS1/PS2/PSP/Vita/Saturn  →  the disc/cart serial (``SLUS-01234`` etc.)
  * Everything else (SNES, GBA, NES, ...)  →  ``SYSTEM_slug_region``

The rule per system lives in ``shared.systems.SYNC_ID_RULES``.  This module
turns that rule table into concrete functions that **any** Python client can
call to produce the same sync_id the server would produce.

There are four strategies:

  title_id           — client already has a native hex ID; return as-is.
  prefix_hex_serial  — NDS-style: hex prefix + hex(ascii_serial).
  serial             — return the serial verbatim (uppercased, punctuation
                       stripped to match stored form).
  slug               — ``SYSTEM_slug`` fallback for systems with no native ID.

All strategies fall back to ``slug`` when the primary input is missing, so
callers never have to care about "what if the ROM isn't available" — they
just pass whatever they have and get back the best available ID.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from shared.rom_id import make_title_id, normalize_rom_name, parse_title_id
from shared.systems import SYNC_ID_RULES, normalize_system_code


# Serial characters that survive canonicalisation.  Everything else is
# stripped so that ``SCUS-94163`` and ``SCUS 94163`` normalise to the same
# key as ``SCUS94163``.
_SERIAL_STRIP_RE = re.compile(r"[^A-Z0-9]")

# Hex title_id format for the NDS prefix_hex_serial strategy output.
_HEX_TITLE_ID_RE = re.compile(r"^[0-9A-F]{16}$")


@dataclass
class ResolveInput:
    """Inputs a client can pass to the resolver.  All are optional — the
    resolver uses whichever are present and degrades gracefully.

    Fields
    ------
    system : canonical system code (e.g. ``NDS``, ``PS1``).  Free-form input
        is accepted; it will be normalised via
        :func:`shared.systems.normalize_system_code`.
    rom_filename : original ROM filename (``"Super Mario 64 DS (USA).nds"``).
        Used for slug generation and DAT lookup.
    title_id : a native 16-char hex ID already known to the client (3DS).
    gamecode : 4-char NDS ROM header gamecode (``"AMCE"``), when the client
        has read the ROM header directly.
    serial : a disc/cart serial (``"SLUS-94163"``) when the client can read
        it from the disc.
    """

    system: str
    rom_filename: Optional[str] = None
    title_id: Optional[str] = None
    gamecode: Optional[str] = None
    serial: Optional[str] = None


@dataclass
class ResolveResult:
    """What the resolver produces.

    sync_id      — the canonical identifier to use in URLs / storage.
    strategy     — the rule that produced the sync_id.
    fallback     — True when the primary strategy failed and we fell back
                   to slug form; clients can use this to decide whether to
                   prompt the user to place a ROM next to the save.
    """

    sync_id: str
    strategy: str
    fallback: bool = False


def canonicalize_serial(serial: str) -> str:
    """Normalise a serial for storage/comparison.

    ``"SCUS-94163"`` → ``"SCUS94163"``; ``"scus 94163"`` → ``"SCUS94163"``.
    DATs and emulators disagree on punctuation; we pick the no-punctuation
    uppercase form as the canonical representation.
    """
    if not serial:
        return ""
    return _SERIAL_STRIP_RE.sub("", serial.upper())


def is_hex_title_id(value: str) -> bool:
    """True when ``value`` looks like a 16-char hex title_id."""
    return bool(value) and bool(_HEX_TITLE_ID_RE.match(value.upper()))


def nds_gamecode_to_sync_id(gamecode: str, prefix: str = "00048000") -> Optional[str]:
    """Convert a 4-char NDS gamecode to the canonical hex sync_id.

    ``"AMCE"`` → ``"00048000414D4345"``.  Matches the ID the NDS/3DS homebrew
    clients compute locally and the Android client emits when it can read
    the ROM header.  Returns None for invalid input.
    """
    if not gamecode or len(gamecode) != 4:
        return None
    if not all(0x20 <= ord(c) < 0x7F for c in gamecode):
        return None
    if len(prefix) != 8 or not all(c in "0123456789ABCDEFabcdef" for c in prefix):
        return None
    return prefix.upper() + "".join(f"{ord(c):02X}" for c in gamecode)


def slug_sync_id(system: str, rom_filename: str) -> str:
    """Build the slug-form sync_id: ``SYSTEM_slug_region``."""
    canonical_sys = normalize_system_code(system) or system.upper()
    try:
        return make_title_id(canonical_sys, rom_filename)
    except ValueError:
        # Fall back to a free-form composition if the system isn't in the
        # shared registry (shouldn't happen in normal use).
        slug = normalize_rom_name(rom_filename) or "unknown"
        return f"{canonical_sys}_{slug}"


def resolve(
    data: ResolveInput,
    *,
    serial_lookup: Optional[callable] = None,
) -> ResolveResult:
    """Compute the canonical sync_id for the given inputs.

    Parameters
    ----------
    data
        Whatever the caller has: a filename, a native title_id, a gamecode,
        a serial.  Missing fields are OK — the resolver picks the best
        strategy that works with what's provided.
    serial_lookup
        Optional callable ``(system, rom_filename) -> Optional[str]`` for
        looking up a serial from a DAT when the caller doesn't know it.
        The server wires this to ``DatNormalizer.lookup_serial`` so a
        Steam Deck client that only sees ``Mario Kart DS (USA).sav`` still
        gets the canonical hex/serial form.

    Returns
    -------
    ResolveResult
        ``sync_id`` is always populated (slug fallback if nothing better).
        ``fallback`` is True when the primary strategy couldn't be applied.
    """
    system = normalize_system_code(data.system) or data.system.upper().strip()
    rule = SYNC_ID_RULES.get(system, {"strategy": "slug"})
    strategy = rule.get("strategy", "slug")

    # --- title_id strategy ---------------------------------------------
    if strategy == "title_id":
        if data.title_id and is_hex_title_id(data.title_id):
            return ResolveResult(
                sync_id=data.title_id.upper(), strategy="title_id"
            )
        # Nothing we can do without the native ID; fall through to slug.
        if data.rom_filename:
            return ResolveResult(
                sync_id=slug_sync_id(system, data.rom_filename),
                strategy="slug",
                fallback=True,
            )
        return ResolveResult(sync_id=f"{system}_unknown", strategy="slug", fallback=True)

    # --- prefix_hex_serial strategy (NDS) ------------------------------
    if strategy == "prefix_hex_serial":
        prefix = rule.get("prefix", "00048000")
        # Direct gamecode first — cheapest, most reliable.
        if data.gamecode:
            result = nds_gamecode_to_sync_id(data.gamecode, prefix=prefix)
            if result:
                return ResolveResult(sync_id=result, strategy="prefix_hex_serial")
        # Serial input — for NDS the serial IS the gamecode.
        if data.serial and len(data.serial.strip()) == 4:
            result = nds_gamecode_to_sync_id(data.serial.strip(), prefix=prefix)
            if result:
                return ResolveResult(sync_id=result, strategy="prefix_hex_serial")
        # Fall back to DAT lookup if we have a filename and a lookup callable.
        if data.rom_filename and serial_lookup is not None:
            try:
                looked_up = serial_lookup(system, data.rom_filename)
            except Exception:
                looked_up = None
            if looked_up and len(looked_up.strip()) == 4:
                result = nds_gamecode_to_sync_id(looked_up.strip(), prefix=prefix)
                if result:
                    return ResolveResult(
                        sync_id=result, strategy="prefix_hex_serial"
                    )
        # Slug fallback so the save is still addressable.
        if data.rom_filename:
            return ResolveResult(
                sync_id=slug_sync_id(system, data.rom_filename),
                strategy="slug",
                fallback=True,
            )
        return ResolveResult(
            sync_id=f"{system}_unknown", strategy="slug", fallback=True
        )

    # --- serial strategy (PS1/PS2/PSP/Vita/Saturn) ---------------------
    if strategy == "serial":
        if data.serial:
            canonical = canonicalize_serial(data.serial)
            if canonical:
                return ResolveResult(sync_id=canonical, strategy="serial")
        if data.rom_filename and serial_lookup is not None:
            try:
                looked_up = serial_lookup(system, data.rom_filename)
            except Exception:
                looked_up = None
            if looked_up:
                canonical = canonicalize_serial(looked_up)
                if canonical:
                    return ResolveResult(sync_id=canonical, strategy="serial")
        if data.rom_filename:
            return ResolveResult(
                sync_id=slug_sync_id(system, data.rom_filename),
                strategy="slug",
                fallback=True,
            )
        return ResolveResult(
            sync_id=f"{system}_unknown", strategy="slug", fallback=True
        )

    # --- slug strategy (default / fallback) ----------------------------
    if data.rom_filename:
        return ResolveResult(
            sync_id=slug_sync_id(system, data.rom_filename), strategy="slug"
        )
    # Caller passed nothing useful; give them a deterministic placeholder.
    return ResolveResult(
        sync_id=f"{system}_unknown", strategy="slug", fallback=True
    )


def canonicalize_slug_title_id(
    title_id: str,
    *,
    serial_lookup: Optional[callable] = None,
    rom_filename: Optional[str] = None,
) -> str:
    """Try to upgrade a slug-form title_id to its canonical sync_id.

    Used on the server's upload/download paths so a Steam Deck client that
    only has ``NDS_mario_kart_ds_usa`` still converges on the same hex ID
    (``000480004154434A``) the 3DS homebrew uses for the same game.

    Parameters
    ----------
    title_id
        The title_id as received.  If it isn't a slug form
        (``SYSTEM_xxx_yyy``), or if the system's strategy is ``slug`` anyway,
        the input is returned unchanged.
    serial_lookup
        ``(system, rom_filename) -> Optional[str]`` — typically
        ``DatNormalizer.lookup_serial``.  Without this the function can't
        reach DAT data and will return the input unchanged for non-slug
        strategies.
    rom_filename
        Optional real filename to use for DAT lookup when the caller knows
        it.  If omitted, the slug itself is passed in as a pseudo-filename
        (works because ``normalize_rom_name`` is idempotent on slugs).

    Returns
    -------
    str
        The canonical sync_id when a better form could be resolved,
        otherwise the original title_id.  Never raises — any failure in
        DAT lookup falls back to the input.
    """
    parsed = parse_title_id(title_id)
    if parsed is None:
        return title_id  # not a slug form

    system, slug = parsed
    rule = SYNC_ID_RULES.get(system, {"strategy": "slug"})
    if rule.get("strategy") == "slug":
        return title_id  # system uses slugs as canonical — nothing to upgrade

    # Resolve via the normal resolver path.  A pseudo-filename from the
    # slug lets the DAT slug index hit.  The extension is cosmetic — the
    # DAT lookup strips it.  We lowercase the slug because
    # ``validate_any_title_id`` upper-cases whole IDs for consistency, but
    # the DAT's slug index is case-sensitive and stores lowercase keys.
    lookup_filename = rom_filename or f"{slug.lower()}.bin"
    try:
        result = resolve(
            ResolveInput(system=system, rom_filename=lookup_filename),
            serial_lookup=serial_lookup,
        )
    except Exception:
        return title_id

    # Only substitute if the resolver actually applied the primary strategy.
    # A fallback result means DAT lookup failed — the caller's slug is still
    # the best we have.
    if result.fallback:
        return title_id
    if result.strategy == rule.get("strategy"):
        return result.sync_id
    return title_id


__all__ = [
    "ResolveInput",
    "ResolveResult",
    "canonicalize_serial",
    "canonicalize_slug_title_id",
    "is_hex_title_id",
    "nds_gamecode_to_sync_id",
    "resolve",
    "slug_sync_id",
]
