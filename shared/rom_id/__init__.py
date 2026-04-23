"""Shared ROM-based title ID helpers.

This package is the single source of truth for ROM filename normalization
and emulator-style title IDs (``GBA_advance_wars_usa``) across every
Python component — server, desktop, steamdeck, tools.

Layout
------
  * :mod:`shared.rom_id.normalizer` — filename → slug → title_id rules.
  * :mod:`shared.rom_id.saturn`     — Saturn-specific product code rules
                                      (DAT lookup, header parsing).

Importing from the package root (``from shared.rom_id import
normalize_rom_name``) keeps existing call sites working; the package
itself just re-exports the underlying modules' public names.
"""

from __future__ import annotations

from shared.systems import SYSTEM_CODES

from shared.rom_id.normalizer import (
    make_title_id,
    normalize_rom_name,
    parse_title_id,
)

__all__ = [
    "SYSTEM_CODES",
    "make_title_id",
    "normalize_rom_name",
    "parse_title_id",
]
