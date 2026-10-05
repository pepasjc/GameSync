"""The ROM catalogue, kept on disk between runs.

Opening a client used to mean re-downloading the whole catalogue - tens of
thousands of rows, several megabytes of JSON - even though the library changes
maybe once a week. The server publishes a fingerprint per system
(``GET /roms/fingerprints``), so the catalogue is stored per system alongside
the fingerprint it was fetched under, and on the next run only the systems
whose fingerprint moved are fetched again. One new SNES ROM costs one SNES
page, not everything.

There is no TTL: the fingerprint says whether the copy is current, and a server
that cannot be reached leaves the last copy usable.

Shared by the MiSTer and Steam Deck clients (the console clients carry C ports
of the same strategy). Python 3.9, standard library only: MiSTer vendors it.
"""

from __future__ import annotations

import json
import os


class CatalogCache:
    """``system -> {fingerprint, rows}``, stored as one JSON file.

    ``version`` is the caller's row schema: bump it when the meaning of a
    stored row changes (a field every row must carry), so an older cache is
    refetched rather than misread.
    """

    def __init__(self, path: str, version: int = 1):
        self.path = str(path)
        self.version = version
        self._systems: dict = {}
        self._dirty = False
        self.load()

    def load(self) -> None:
        try:
            with open(self.path, "r") as handle:
                data = json.load(handle)
        except (OSError, ValueError):
            return
        if not isinstance(data, dict) or data.get("version") != self.version:
            return
        systems = data.get("systems")
        if isinstance(systems, dict):
            self._systems = systems

    def systems(self) -> list:
        return sorted(self._systems)

    def fingerprint(self, system: str):
        entry = self._systems.get(system)
        return entry.get("fingerprint") if entry else None

    def rows(self, system: str) -> list:
        entry = self._systems.get(system)
        return list(entry.get("rows") or []) if entry else []

    def all_rows(self) -> list:
        rows = []
        for system in self.systems():
            rows.extend(self.rows(system))
        return rows

    def put(self, system: str, fingerprint: str, rows: list) -> None:
        self._systems[system] = {"fingerprint": fingerprint,
                                 "rows": list(rows)}
        self._dirty = True

    def drop(self, system: str) -> None:
        if system in self._systems:
            del self._systems[system]
            self._dirty = True

    def plan(self, server: dict, wanted) -> tuple:
        """Split *wanted* systems into ``(fresh, stale)`` against the
        server's fingerprints; systems the server no longer lists are dropped.
        """
        fresh, stale = [], []
        for system in wanted:
            info = server.get(system)
            if info is None:
                self.drop(system)
                continue
            if self.fingerprint(system) == str(info.get("fingerprint") or ""):
                fresh.append(system)
            else:
                stale.append(system)
        for system in list(self._systems):
            if system not in server:
                self.drop(system)
        return fresh, stale

    def clear(self) -> None:
        self._systems = {}
        self._dirty = True

    def save(self) -> None:
        if not self._dirty:
            return
        payload = {"version": self.version, "systems": self._systems}
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
            # An unwritable cache is a slow start, never a failure.
            pass

    def __len__(self) -> int:
        return sum(len(entry.get("rows") or [])
                   for entry in self._systems.values())
