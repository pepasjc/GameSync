"""Shared ROM-based title ID helpers.

This package is the single source of truth for ROM filename normalization
and emulator-style title IDs (``GBA_advance_wars_usa``) across every
Python component — server, desktop, steamdeck, tools.

Layout
------
  * :mod:`shared.rom_id.normalizer` — filename → slug → title_id rules.
  * :mod:`shared.rom_id.saturn`     — Saturn-specific product code rules
                                      (DAT lookup, header parsing).
  * :mod:`shared.rom_id.dreamcast`  — Dreamcast product code rules
                                      (``DC_<serial>`` sync ids).

Importing from the package root (``from shared.rom_id import
normalize_rom_name``) keeps existing call sites working; the package
itself just re-exports the underlying modules' public names.
"""

from __future__ import annotations

from shared.systems import SYSTEM_CODES

from shared.rom_id.dreamcast import (
    DC_TITLE_ID_PREFIX,
    canonical_dc_serial,
    dc_device_folder_ids,
    make_dc_title_id,
    parse_dc_title_id,
)
from shared.rom_id.normalizer import (
    make_title_id,
    normalize_rom_name,
    parse_title_id,
)

__all__ = [
    "SYSTEM_CODES",
    "DC_TITLE_ID_PREFIX",
    "canonical_dc_serial",
    "dc_device_folder_ids",
    "make_dc_title_id",
    "make_title_id",
    "normalize_rom_name",
    "parse_dc_title_id",
    "parse_title_id",
]
