"""Offer the RetroAchievements-supported version of an installed game.

RA builds a set against particular dumps.  For systems matched by *title*
(disc systems, see :mod:`shared.ra_titles`) every region of a game badges in
the catalogue, but only some of them will actually load the set - RA's
Dreamcast Soulcalibur was made against the Europe disc, so a USA copy shows
the badge and never unlocks anything.  The server marks the dumps RA really
recognises (``ra_match == "hash"``, or ``ra_dump`` when the file name is one
RA registered for the set), and this module answers "is there a verified
catalogue version of this installed game, when the installed one is not?".

Shared by the MiSTer and Steam Deck clients; the Android client carries a
Kotlin port (``android/.../catalog/RaVariants.kt``) - change one, change the
other, and keep ``mister/tests/test_ra_variant.py`` and
``shared/tests/test_ra_variants.py`` passing.

A *game* here is anything with ``system``, ``name`` and ``rows`` (the
catalogue rows it is made of - one per disc), e.g.
:class:`shared.mister_install.DiscGroup`.
"""

from __future__ import annotations

import re
from typing import Callable, Iterable, Optional


def game_ra_verified(rows) -> bool:
    """RA will recognise this very dump, not merely a game of its name.

    An exact hash says so; for systems matched by name, so does the file
    name being one RA registered for the set (``ra_dump``).
    """
    for row in rows or ():
        try:
            if int(row.get("ra_achievements") or 0) <= 0:
                continue
        except (TypeError, ValueError):
            continue
        if (str(row.get("ra_match") or "hash").lower() == "hash"
                or row.get("ra_dump")):
            return True
    return False


#: Tags that make a release something other than the game itself: a bonus
#: disc shares the game's name once tags are gone, and is not a version of
#: it.
_NOT_A_RELEASE_RE = re.compile(
    r"\((?:[^)]*\b(?:bonus|demo|beta|proto|prototype|sample|kiosk|"
    r"trial|preview|msu-?1?|msu-md|md\+)\b[^)]*)\)", re.IGNORECASE)


def is_plain_release(name: str) -> bool:
    """A retail release: no tags at all, and not a bonus disc or demo."""
    return "[" not in name and not _NOT_A_RELEASE_RE.search(name)


#: A fan translation's tag: ``[T-En by Aeon Genesis v1.00]``, ``[T-En v1.0
#: tettsui77]`` (RA's own naming), ``[T-Fr ...]``.
_TRANSLATION_RE = re.compile(r"\[T-([A-Za-z]{2,3})\b")

#: Regions whose releases are in English, and the language list form.
_ENGLISH_RE = re.compile(
    r"\((?:[^)]*\b(?:USA|World|Europe|Australia|UK|Canada)\b[^)]*)\)"
    r"|\((?:[A-Za-z]{2},)*En(?:,[A-Za-z]{2})*\)")


def translation_language(name: str) -> str:
    """``"en"`` for a ``[T-En ...]`` translation, ``""`` for anything else."""
    found = _TRANSLATION_RE.search(name)
    return found.group(1).lower() if found else ""


def is_swappable(name: str) -> bool:
    """A release that may be offered or replaced: a retail release, or a fan
    translation (whatever extra patches ride along with it). A plain hack
    is neither - trading it for the badge would lose the hack."""
    if _NOT_A_RELEASE_RE.search(name):
        return False
    return "[" not in name or bool(translation_language(name))


def same_language(installed: str, candidate: str) -> bool:
    """Whether ``candidate`` keeps the language the player chose.

    A translation is only ever replaced by a translation into the same
    language, or - for English - by a release that is English already: a
    ``[T-En]`` Japanese game is never offered its Japanese original. A
    retail release may be offered either - :meth:`VariantIndex.variants`
    only keeps the translations when RA supports no retail version (RA's
    Ganbare Goemon 3 set exists for the DDSTranslation patch alone).
    """
    language = translation_language(installed)
    if not language:
        return True
    if translation_language(candidate) == language:
        return True
    return (language == "en" and is_plain_release(candidate)
            and bool(_ENGLISH_RE.search(candidate)))


def variant_key(system, name):
    """What makes two catalogue games versions of one another: the name
    with every tag, article and space gone, so "Soul Calibur (USA)" and
    "Soulcalibur (Europe) (En,Fr,De,Es)" agree."""
    from shared.ra_titles import normalize as ra_normalize

    base = ra_normalize(name)
    return (system, base) if base else None


def ra_ids(rows) -> set:
    ids = set()
    for row in rows or ():
        try:
            game_id = int(row.get("ra_game_id") or 0)
        except (TypeError, ValueError):
            continue
        if game_id > 0:
            ids.add(game_id)
    return ids


#: Which version to offer first when RA supports several.
_REGION_ORDER = ("world", "usa", "europe")


def region_rank(name: str) -> int:
    lowered = name.lower()
    for rank, region in enumerate(_REGION_ORDER):
        if "(" + region in lowered or ", " + region in lowered:
            return rank
    return len(_REGION_ORDER)


def _no_pack(_game) -> str:
    return ""


class VariantIndex:
    """The catalogue, indexed once for :meth:`variants`.

    ``name_key`` is the client's "same game name" key (the one it uses to
    tell whether a catalogue game is installed); ``pack_kind`` returns a
    non-empty string for an MSU pack, which is never offered.
    """

    def __init__(
        self,
        games: Iterable,
        name_key: Callable[[str], str],
        pack_kind: Callable[[object], str] = _no_pack,
    ):
        self._name_key = name_key
        self._by_name: dict = {}
        self._verified: dict = {}
        self._verified_by_id: dict = {}
        #: ``(system, md5)`` of every dump RA recognises exactly - what an
        #: installed cartridge is checked against when its name says nothing.
        self._hashes: set = set()
        for game in games:
            if pack_kind(game):
                # Never offered, and never what an installed plain ROM is:
                # the name key folds "(MSU1)" away, so a pack listed first
                # would otherwise stand in for the ROM of the same name.
                continue
            self._by_name.setdefault((game.system, name_key(game.name)), game)
            if not game_ra_verified(game.rows):
                continue
            for row in game.rows or ():
                md5 = str(row.get("ra_hash") or "").lower()
                if md5:
                    self._hashes.add((game.system, md5))
            if not is_swappable(game.name):
                continue
            key = variant_key(game.system, game.name)
            if key is not None:
                self._verified.setdefault(key, []).append(game)
            for game_id in ra_ids(game.rows):
                self._verified_by_id.setdefault((game.system, game_id), []).append(game)

    def variants(
        self,
        system: str,
        name: str,
        is_pack: bool = False,
        installed: Optional[Callable[[object], bool]] = None,
        content_hash: Optional[Callable[[], Optional[str]]] = None,
    ) -> list:
        """Catalogue versions of an installed game RA verifiably supports,
        when the installed one is not among them.  Best first.

        "Verifiably" is an exact hash or a file name RA registered for the
        set - not a title match, which is just as true of the USA
        Soulcalibur RA will not load as of the Europe one it will.

        Versions are found by name (tags, articles and spacing aside) and
        by the RA game the server linked the installed game's catalogue
        entry to - which is what finds "Front Mission: Gun Hazard" for a
        "Front Mission Series - Gun Hazard" install.

        Whether the installed copy is itself supported is decided by
        ``content_hash`` when given - the RA hash of the file on the device,
        asked for only once there is something to offer - and otherwise by
        its catalogue entry, which must exist: a game the catalogue cannot
        see is never second-guessed by name alone.  Nothing is offered once
        a verified version is already installed (``installed(game)``).
        """
        if is_pack or not self._verified or not is_swappable(name):
            return []
        own_key = self._name_key(name)
        own = self._by_name.get((system, own_key))
        own_ids = ra_ids(own.rows) if own is not None else set()

        pool = list(self._verified.get(variant_key(system, name), ()))
        for game_id in own_ids:
            for game in self._verified_by_id.get((system, game_id), ()):
                if game not in pool:
                    pool.append(game)
        found = []
        same_name = False
        for candidate in pool:
            if not same_language(name, candidate.name):
                continue
            if self._name_key(candidate.name) == own_key:
                # A verified entry under the installed one's own name: it
                # cannot be installed beside it, and by name alone the two
                # cannot be told apart.
                same_name = True
                continue
            if own_ids and not own_ids & ra_ids(candidate.rows):
                # RA filed them as different games ("1Xtreme" is not
                # "ESPN Extreme Games", whatever a title alias says).
                continue
            found.append(candidate)
        if not found:
            return []

        md5 = content_hash() if content_hash is not None else None
        if md5:
            if (system, md5.lower()) in self._hashes:
                return []           # RA knows this very file
        elif own is None or game_ra_verified(own.rows) or same_name:
            return []

        if installed is not None and any(installed(c) for c in found):
            return []
        if not translation_language(name):
            retail = [c for c in found if is_plain_release(c.name)]
            if retail:
                found = retail
        found.sort(key=lambda g: (region_rank(g.name), g.name.lower()))
        return found


#: Region words a release name carries in its parenthetical tags.
_REGION_WORDS = (
    "usa", "europe", "japan", "world", "asia", "korea", "china", "taiwan",
    "brazil", "australia", "canada", "uk", "germany", "france", "spain",
    "italy", "netherlands", "sweden", "russia", "hong kong",
)


def regions(name: str) -> set:
    """The regions in a release name's ``(...)`` tags; ``[...]`` patch tags
    are ignored, so "(Japan) [T-En ...]" is Japan."""
    found = set()
    depth = 0
    group = []
    for ch in name:
        if ch == "[":
            depth += 1
        elif ch == "]":
            depth = max(0, depth - 1)
        elif depth == 0 and ch == "(":
            group = []
        elif depth == 0 and ch == ")":
            for part in "".join(group).split(","):
                word = part.strip().lower()
                if word in _REGION_WORDS:
                    found.add(word)
            group = []
        elif depth == 0:
            group.append(ch)
    return found


def same_region(installed: str, replacement: str) -> bool:
    """Whether a save should follow the game to ``replacement``.

    True when both name a region and they share one: a Japanese translation
    swapped for another patch of the same Japanese game, or "(USA)" for
    "(USA, Europe)". A save from another region can load as corrupt, so it
    stays where it is.
    """
    a, b = regions(installed), regions(replacement)
    return bool(a & b)
