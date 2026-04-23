"""Smart search + filter for the server ROM catalog.

Kept PyQt-free so it can be unit tested without Qt: the catalog tab calls
``filter_catalog`` with the user's search text / system filter and gets
back a ranked list of ROM dicts to paint.
"""

from __future__ import annotations

import re

from .base import normalize_rom_name
from .rom_match import core_name_slug, _slug_roman_variants

_TOKEN_SPLIT_RE = re.compile(r"[^a-z0-9]+")


def _index_rom(rom: dict) -> tuple[str, str, set[str]]:
    """Return (normalized_haystack, display_slug, token_set) for *rom*.

    ``normalized_haystack`` is a concatenation of every searchable field
    lowercased into a single run of alphanumerics separated by
    underscores — substring matches against it catch things like
    ``"finalfantasy7"`` and ``"ff_vii"`` even when the query doesn't use
    spaces the same way the catalog does.

    The token set adds roman-numeral variants of the display name so a
    user typing ``"ff 7"`` matches ``"Final Fantasy VII"`` without
    having to know which form the catalog uses.
    """
    name = rom.get("name") or ""
    filename = rom.get("filename") or ""
    system = (rom.get("system") or "").lower()
    title_id = (rom.get("title_id") or "").lower()

    display_slug = core_name_slug(name) or normalize_rom_name(filename)
    full_slug = normalize_rom_name(filename) or display_slug

    tokens: set[str] = set()
    for label in (name, filename, title_id):
        for part in _TOKEN_SPLIT_RE.split(label.lower()):
            if part:
                tokens.add(part)
    tokens.update(display_slug.split("_") if display_slug else [])
    tokens.update(full_slug.split("_") if full_slug else [])
    for variant in _slug_roman_variants(display_slug):
        tokens.update(variant.split("_"))

    haystack = "_".join(
        part for part in (system, title_id, full_slug, display_slug) if part
    )
    return haystack, display_slug, tokens


def _query_tokens(query: str) -> list[str]:
    return [t for t in _TOKEN_SPLIT_RE.split(query.lower()) if t]


def _roman_expand(token: str) -> set[str]:
    """Expand roman<->arabic forms for a single token."""
    from .rom_match import _ROMAN_TO_ARABIC, _ARABIC_TO_ROMAN

    out = {token}
    if token in _ROMAN_TO_ARABIC:
        out.add(_ROMAN_TO_ARABIC[token])
    if token in _ARABIC_TO_ROMAN:
        out.add(_ARABIC_TO_ROMAN[token])
    return out


def matches(rom: dict, query: str, system: str | None = None) -> bool:
    """Return True when *rom* matches *query* (all tokens) and *system*."""
    if system and (rom.get("system") or "").upper() != system.upper():
        return False
    query = (query or "").strip()
    if not query:
        return True

    haystack, _display_slug, tokens = _index_rom(rom)
    for raw_token in _query_tokens(query):
        variants = _roman_expand(raw_token)
        # A token matches if any of its variants either appears as a
        # substring of the joined haystack or lands in the token set
        # (which already includes the roman-numeral variants of the
        # display slug).
        if not any(v in haystack or v in tokens for v in variants):
            return False
    return True


def filter_catalog(
    catalog: list[dict],
    query: str = "",
    system: str | None = None,
) -> list[dict]:
    """Return catalog rows matching *query* + *system*, sorted name-first."""
    if not catalog:
        return []

    filtered = [rom for rom in catalog if matches(rom, query, system)]
    filtered.sort(
        key=lambda r: (
            (r.get("system") or "").upper(),
            (r.get("name") or r.get("filename") or "").lower(),
        )
    )
    return filtered


def unique_systems(catalog: list[dict]) -> list[str]:
    """Return the sorted set of system codes present in *catalog*."""
    systems = {
        (rom.get("system") or "").upper()
        for rom in catalog
        if rom.get("system")
    }
    return sorted(s for s in systems if s)
