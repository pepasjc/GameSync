"""Remember what each save file hashed to, so a rescan only reads what changed.

Same approach as the desktop client: a file is unchanged if its size and mtime
are unchanged, and its derived facts can be reused. On a MiSTer this matters
more than on a PC - the SD card is exfat mounted ``sync``, and a scan otherwise
reads every memory card in full on every run.

What is cached is deliberately limited to facts that are a **pure function of
the file's bytes**: the hash, the in-card serial, and whether the card is
blank. The title id is not cached, because resolving it can involve the ROM
catalogue, which changes when the server's library does - a cached title id
would keep a save pinned to a slot it no longer belongs in.
"""

from __future__ import annotations

import json
import os

from shared.mister import MISTER_CONFIG_DIR

CACHE_PATH = os.path.join(MISTER_CONFIG_DIR, "hash_cache.json")

#: Format marker. Bumped when the meaning of an entry changes, so a stale cache
#: is discarded rather than misread. 2: ``serials`` (every product code on a
#: shared PS1 card) alongside the first-only ``serial``.
VERSION = 2

#: exfat stores mtime with two-second granularity, so compare with a tolerance
#: rather than for equality.
MTIME_TOLERANCE = 2.0


class HashCache:
    """``path -> {size, mtime, hash, serial, blank}``."""

    def __init__(self, path: str = CACHE_PATH):
        self.path = path
        self._entries = {}
        self._dirty = False
        self.hits = 0
        self.misses = 0
        self.load()

    def load(self) -> None:
        try:
            with open(self.path, "r") as handle:
                data = json.load(handle)
        except (OSError, ValueError):
            return
        if not isinstance(data, dict) or data.get("version") != VERSION:
            return
        entries = data.get("entries")
        if isinstance(entries, dict):
            self._entries = entries

    def get(self, path: str, size: int, mtime: float):
        """The cached facts for an unchanged file, else None."""
        entry = self._entries.get(path)
        if not entry:
            self.misses += 1
            return None
        if int(entry.get("size", -1)) != int(size):
            self.misses += 1
            return None
        try:
            if abs(float(entry.get("mtime", -1)) - float(mtime)) > MTIME_TOLERANCE:
                self.misses += 1
                return None
        except (TypeError, ValueError):
            self.misses += 1
            return None
        self.hits += 1
        return entry

    def put(self, path: str, size: int, mtime: float, save_hash: str,
            serial=None, blank: bool = False, serials=None) -> None:
        if serials is None:
            serials = [serial] if serial else []
        serials = list(serials)
        self._entries[path] = {
            "size": int(size),
            "mtime": float(mtime),
            "hash": save_hash,
            "serial": serial or (serials[0] if serials else None),
            "serials": serials,
            "blank": bool(blank),
        }
        self._dirty = True

    def prune(self, live_paths) -> None:
        """Drop entries for saves that are no longer on the device."""
        live = set(live_paths)
        stale = [path for path in self._entries if path not in live]
        for path in stale:
            del self._entries[path]
        if stale:
            self._dirty = True

    def save(self) -> None:
        if not self._dirty:
            return
        payload = {"version": VERSION, "entries": self._entries}
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
        except Exception:
            pass
        temp = self.path + ".part"
        try:
            with open(temp, "w") as handle:
                json.dump(payload, handle)
            os.replace(temp, self.path)
            self._dirty = False
        except Exception:
            # A cache that cannot be written is a slow scan, not a failure, so
            # nothing here may propagate - not even a bad path or a full disk.
            pass

    def clear(self) -> None:
        self._entries = {}
        self._dirty = True

    def __len__(self) -> int:
        return len(self._entries)
