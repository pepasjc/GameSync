"""Match a ROM filename to a RetroAchievements game by *title*.

A weaker claim than :mod:`shared.ra_hash`, and deliberately so.  Hashing
answers "RA will recognise this exact file".  This only answers "RA has a
set for this game" — useful for disc systems, where reading the boot
executable out of a CHD is a different order of work, but it says nothing
about whether the particular dump in hand is the one RA registered.

Callers must keep the two apart (``ra_match`` is ``hash`` or ``title``) so
a title match is never presented as a guarantee.

Matching is strict equality on a normalised form, never fuzzy: a near-miss
badge on the wrong game is worse than no badge.  Anything that normalises
to the same string as two different games is dropped from the index rather
than guessed at.
"""

from __future__ import annotations

import re
from typing import Iterable

#: RA prefixes its non-retail entries: "~Hack~ Kaizo Mario World",
#: "~Demo~ ...", "~Prototype~ ...".  The tag is not part of the game name.
_RA_TAG_RE = re.compile(r"^\s*~[^~]*~\s*")

#: "(USA)", "(Disc 1)", "[T-En by ...]" and friends.
_BRACKETED_RE = re.compile(r"\([^)]*\)|\[[^\]]*\]")

#: Articles are dropped entirely: No-Intro writes "Legend of Zelda, The"
#: where RA writes "The Legend of Zelda", and neither order should matter.
_ARTICLES = {"the", "a", "an"}

_WORD_RE = re.compile(r"[a-z0-9]+")

#: Roman numerals RA and No-Intro disagree about often enough to matter.
_ROMAN = {
    "i": "1", "ii": "2", "iii": "3", "iv": "4", "v": "5",
    "vi": "6", "vii": "7", "viii": "8", "ix": "9", "x": "10",
}


def _strip_extension(name: str) -> str:
    base = name.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    if "." in base:
        stem, _, ext = base.rpartition(".")
        # Only strip things that look like an extension, not "Vol. 2".
        if stem and len(ext) <= 5 and ext.isalnum():
            return stem
    return base


def normalize(name: str) -> str:
    """Comparable form of a game name: no tags, articles, or punctuation.

    ``"Castlevania - Symphony of the Night (USA).chd"`` and
    ``"Castlevania: Symphony of the Night"`` both give
    ``"castlevaniasymphonyofnight"``.
    """
    text = _strip_extension(name)
    text = _RA_TAG_RE.sub("", text)
    text = _BRACKETED_RE.sub(" ", text)
    words = _WORD_RE.findall(text.lower())
    out = []
    for word in words:
        if word in _ARTICLES:
            continue
        out.append(_ROMAN.get(word, word))
    return "".join(out)


def title_variants(title: str) -> list[str]:
    """Every normalised form an RA title should be findable under.

    RA writes alternates with a pipe (``"Jerry Boy | Smart Ball"``); each
    side is a name the game is genuinely known by.  A subtitle is also
    indexed on its own main title, so ``"Wipeout XL"`` still finds
    ``"Wipeout XL: 2097"``-style entries where one side carries extra.
    """
    out: list[str] = []
    for part in title.split("|"):
        part = _RA_TAG_RE.sub("", part).strip()
        if not part:
            continue
        full = normalize(part)
        if full:
            out.append(full)
    return out


class TitleIndex:
    """Normalised title → ``(game_id, achievements)`` for one RA console.

    Ambiguous keys — two different games normalising alike — are removed,
    so a lookup either finds exactly one game or nothing.
    """

    __slots__ = ("_by_title",)

    def __init__(self, games: Iterable[tuple[int, str, int]]):
        seen: dict[str, tuple[int, int]] = {}
        tagged: dict[str, bool] = {}      # key -> the entry holding it is ~Tagged~
        clashed: set[str] = set()
        for game_id, title, achievements in games:
            # A subset ("Super Mario Sunshine [Subset - Bonus]") is an extra
            # set for the same disc, never the disc itself; indexing it
            # would clash with - and so erase - the base game's key.
            if "[subset" in title.lower():
                continue
            is_tagged = bool(_RA_TAG_RE.match(title))
            for key in title_variants(title):
                existing = seen.get(key)
                if existing is None:
                    seen[key] = (game_id, achievements)
                    tagged[key] = is_tagged
                elif existing[0] != game_id:
                    # The plain retail entry outranks a ~Hack~/~Demo~ that
                    # shares its name; two of a kind are truly ambiguous.
                    if tagged[key] and not is_tagged:
                        seen[key] = (game_id, achievements)
                        tagged[key] = False
                    elif tagged[key] == is_tagged:
                        clashed.add(key)
        for key in clashed:
            seen.pop(key, None)
        self._by_title = seen

    def __len__(self) -> int:
        return len(self._by_title)

    def lookup(self, rom_name: str) -> tuple[int, int] | None:
        """``(game_id, achievements)`` for a ROM filename, else None."""
        key = normalize(rom_name)
        if not key:
            return None
        return self._by_title.get(key)


def build_index(library) -> TitleIndex:
    """TitleIndex over an :class:`shared.ra_api.RaLibrary`.

    Games with no achievement count (the public endpoints do not report
    one) are indexed with -1, matching ``ra_index.ACHIEVEMENTS_UNKNOWN``.
    """
    games = []
    for game_id, title in library.titles.items():
        count = library.achievement_count(game_id)
        games.append((game_id, title, -1 if count is None else count))
    return TitleIndex(games)
