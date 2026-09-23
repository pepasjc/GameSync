"""desktop/dreamcast_ipbin.py — thin shim that re-exports from shared/.

The Dreamcast IP.BIN reader lives in ``shared/rom_id/dreamcast.py`` so the
desktop client, the Steam Deck scanner and the server all read the same disc
header the same way.  Do not add definitions here; edit that module instead.
"""

from __future__ import annotations

import systems as _systems  # noqa: F401 — puts the repo root on sys.path

from shared.rom_id.dreamcast import (  # noqa: E402
    IMAGE_EXTENSIONS,
    IP_BIN_MAGIC,
    IP_BIN_SIZE,
    IpBin,
    find_disc_image,
    parse_gdi_track_files,
    parse_ip_bin,
    read_folder_ip_bin,
    read_ip_bin,
)

__all__ = [
    "IMAGE_EXTENSIONS",
    "IP_BIN_MAGIC",
    "IP_BIN_SIZE",
    "IpBin",
    "find_disc_image",
    "parse_gdi_track_files",
    "parse_ip_bin",
    "read_folder_ip_bin",
    "read_ip_bin",
]
