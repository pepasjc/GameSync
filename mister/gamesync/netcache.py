"""A short-lived on-disk cache for the server lookups a scan needs.

Profiling a scan on real hardware:

    walk saves dir        14 ms
    read + hash 16 saves  37 ms
    scan, no server       22 ms
    scan, with server   6861 ms

Reading and hashing every save is nothing. The cost is the catalogue and title
lists fetched to resolve names for serial-keyed systems - ``/titles`` alone was
1198 ms, and a per-system ROM list 500-850 ms more.

Those lists change only when the library does, so they are cached for a few
minutes. This is deliberately *not* used for anything that decides what to
transfer: the sync plan is always fetched live. The worst a stale entry can do
is fail to resolve a save that was added to the server minutes ago, which the
next rescan fixes - and Y forces a refresh.
"""

from __future__ import annotations

import json
import os
import time

from shared.mister import MISTER_CONFIG_DIR

CACHE_PATH = os.path.join(MISTER_CONFIG_DIR, "server_cache.json")

VERSION = 1

#: Long enough to make a rescan feel instant, short enough that a save added
#: from another console shows up without anyone thinking about caches.
DEFAULT_TTL = 600.0


class NetCache:
    def __init__(self, path: str = CACHE_PATH, ttl: float = DEFAULT_TTL):
        self.path = path
        self.ttl = ttl
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

    def get(self, key: str, now=None):
        """The cached value for *key*, or None when absent or stale."""
        entry = self._entries.get(key)
        if not entry:
            self.misses += 1
            return None
        now = time.time() if now is None else now
        try:
            age = now - float(entry.get("stored", 0))
        except (TypeError, ValueError):
            self.misses += 1
            return None
        if age < 0 or age > self.ttl:
            self.misses += 1
            return None
        self.hits += 1
        return entry.get("value")

    def put(self, key: str, value, now=None) -> None:
        self._entries[key] = {
            "stored": time.time() if now is None else now,
            "value": value,
        }
        self._dirty = True

    def invalidate(self, prefix: str = "") -> None:
        """Drop everything, or everything under a prefix."""
        if prefix:
            stale = [k for k in self._entries if k.startswith(prefix)]
        else:
            stale = list(self._entries)
        for key in stale:
            del self._entries[key]
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
            # Same rule as the hash cache: an unwritable cache is a slow scan,
            # never a failure.
            pass

    def __len__(self) -> int:
        return len(self._entries)
