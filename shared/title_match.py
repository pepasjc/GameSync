"""Matching a local file name to a server title id.

Some systems key saves by a disc **serial**, not by the game's name - PS1 and
Saturn among them (see ``SYNC_ID_RULES``). When the save itself carries no
serial (a MiSTer memory card the game has not written to yet, or Saturn backup
RAM, which has no disc id at all), the only bridge from the file on disk to the
server's key is the game's name. That bridge has to survive the two sides
naming the same game differently:

    on the device   Final Fantasy IX (USA).sav        -> final_fantasy_ix_usa
    on the server   Final Fantasy IX (USA, Canada)    -> final_fantasy_ix_usa_canada

An exact slug comparison misses that, and the save then looks local-only and
can never be synced.

So matching runs in three passes, each stricter about what it will accept than
the last is lenient:

1. Exact slug - unchanged behaviour, always preferred.
2. Same base name (region ignored) **and compatible regions** - the region sets
   overlap, or one side names no region at all.
3. Same base name, and only one candidate has it.

Region compatibility is the important guard. Regional releases have different
serials, and a USA save filed under a Japanese serial is a corrupted sync, not
a convenience. ``(USA)`` matches ``(USA, Canada)`` because the sets intersect;
``(USA)`` never matches ``(Europe)``.
"""

from __future__ import annotations

from typing import Dict, Iterable, List, Optional, Tuple

import re

from shared.rom_id.normalizer import _REGION_RE, _strip_extension, normalize_rom_name

__all__ = ["TitleMatcher", "base_slug", "regions_of", "regions_compatible"]

#: Server save names carry bracketed tags the ROM normaliser leaves alone -
#: "Final Fantasy IX [Disc1of4]" against "Final Fantasy IX (USA) (Disc 1)".
#: Stripped for the loose pass only; the exact pass keeps its existing rules.
_BRACKET_TAG_RE = re.compile(r"\s*\[[^\]]*\]")


def regions_of(name: str) -> frozenset:
    """Region tokens in a ROM/save name, lower-cased. Empty when unmarked."""
    match = _REGION_RE.search(_strip_extension(str(name or "")))
    if not match:
        return frozenset()
    text = match.group(0).strip(" ()")
    return frozenset(part for part in text.lower().replace(",", " ").split())


def base_slug(name: str) -> str:
    """The slug with any region suffix and bracketed tags removed."""
    name = _BRACKET_TAG_RE.sub(" ", str(name or ""))
    slug = normalize_rom_name(name)
    suffix = "_".join(sorted(regions_of(name)))
    if not suffix:
        return slug
    # The normaliser appends regions in their written order, so strip by
    # length rather than by the sorted form.
    tail = "_" + "_".join(regions_of_ordered(name))
    if slug.endswith(tail):
        return slug[: -len(tail)]
    return slug


def regions_of_ordered(name: str) -> List[str]:
    """Region tokens in the order the normaliser appends them."""
    match = _REGION_RE.search(_strip_extension(str(name or "")))
    if not match:
        return []
    text = match.group(0).strip(" ()")
    return text.lower().replace(",", " ").split()


def regions_compatible(left: Iterable, right: Iterable) -> bool:
    """True when two region sets could describe the same release.

    An unmarked name is treated as "could be anything" - homebrew and
    no-intro-less dumps routinely carry no region at all.
    """
    left = frozenset(left)
    right = frozenset(right)
    if not left or not right:
        return True
    return bool(left & right)


class TitleMatcher:
    """Resolves a name to a title id, tolerating regional naming differences."""

    def __init__(self):
        self._exact: Dict[str, str] = {}
        self._by_base: Dict[str, List[Tuple[frozenset, str]]] = {}
        self._authoritative: set = set()
        self._authoritative_bases: Dict[str, Tuple[frozenset, str]] = {}

    def add(self, title_id: str, *names: str, **options) -> None:
        """Index one server entry under every name it is known by.

        ``authoritative=True`` marks an entry that already holds a save on the
        server. Those win over the ROM catalogue, because landing in the slot
        that actually has data is the whole point - and a catalogue serial can
        simply be wrong. One live example: the ROM catalogue files
        "Final Fantasy IX (USA) (Disc 1).chd" under the *Europe* serial
        SLES02965, while the real save sits under SLUS01251.
        """
        title_id = str(title_id or "").strip()
        if not title_id:
            return
        authoritative = bool(options.get("authoritative"))
        for name in names:
            if not name:
                continue
            slug = normalize_rom_name(name)
            if slug and slug != "unknown":
                if authoritative:
                    self._exact[slug] = title_id
                    self._authoritative.add(slug)
                elif slug not in self._authoritative:
                    self._exact.setdefault(slug, title_id)

            base = base_slug(name)
            if not base or base == "unknown":
                continue
            bucket = self._by_base.setdefault(base, [])
            entry = (regions_of(name), title_id)
            if authoritative:
                self._authoritative_bases[base] = entry
            elif entry not in bucket:
                bucket.append(entry)

    def lookup_exact(self, name: str) -> Optional[str]:
        """The title id of an entry named *exactly* like ``name``, or None.

        For slug-keyed systems, where the name is the identity and a regional
        near-miss must never be bridged - but the server may still file a ROM
        under a title id that is not the slug of its file name (a translation
        patch resolved through a DAT alias), and a save named after that
        exact file belongs in that slot.
        """
        slug = normalize_rom_name(name)
        if not slug or slug == "unknown":
            return None
        return self._exact.get(slug)

    def lookup(self, name: str) -> Optional[str]:
        """The server title id for a local file name, or None."""
        slug = normalize_rom_name(name)
        if not slug or slug == "unknown":
            return None

        found = self._exact.get(slug)
        if found and slug in self._authoritative:
            return found

        base = base_slug(name)
        wanted = regions_of(name)

        # A slot that already holds a save outranks the ROM catalogue, provided
        # the regions do not contradict each other. The catalogue's serial can
        # be wrong - one live server files "Final Fantasy IX (USA) (Disc 1)"
        # under the Europe serial SLES02965 - and following it would strand the
        # save in a slot with nothing in it while the real save sits elsewhere.
        owner = self._authoritative_bases.get(base)
        if owner and regions_compatible(wanted, owner[0]):
            return owner[1]

        if found:
            return found

        candidates = self._by_base.get(base)
        if not candidates:
            return None
        compatible = [title_id for regions, title_id in candidates
                      if regions_compatible(wanted, regions)]
        # All discs of a game share one title id, so several rows agreeing is
        # the normal case, not an ambiguity.
        unique = set(compatible)
        if len(unique) == 1:
            return compatible[0]

        # Either nothing was region-compatible, or several distinct releases
        # were. Both mean refuse: a (USA) save filed under the Europe serial is
        # a corrupted sync, not a near miss, and there is deliberately no
        # "only one candidate so it must be right" fallback here - an explicit
        # region mismatch is a mismatch.
        return None

    def to_dict(self) -> dict:
        """A JSON-safe snapshot, so a client can cache the built index.

        Rebuilding from raw catalogue rows means re-running the slug rules over
        every name, which on a MiSTer costs seconds for a real library.
        """
        return {
            "exact": self._exact,
            "authoritative": sorted(self._authoritative),
            "by_base": {
                base: [[sorted(regions), title_id]
                       for regions, title_id in bucket]
                for base, bucket in self._by_base.items()
            },
            "authoritative_bases": {
                base: [sorted(regions), title_id]
                for base, (regions, title_id) in self._authoritative_bases.items()
            },
        }

    @classmethod
    def from_dict(cls, data: dict) -> "TitleMatcher":
        matcher = cls()
        if not isinstance(data, dict):
            return matcher
        matcher._exact = dict(data.get("exact") or {})
        matcher._authoritative = set(data.get("authoritative") or ())
        matcher._by_base = {
            base: [(frozenset(regions), title_id)
                   for regions, title_id in bucket]
            for base, bucket in (data.get("by_base") or {}).items()
        }
        matcher._authoritative_bases = {
            base: (frozenset(value[0]), value[1])
            for base, value in (data.get("authoritative_bases") or {}).items()
            if isinstance(value, (list, tuple)) and len(value) == 2
        }
        return matcher

    def __len__(self) -> int:
        return len(self._exact)
