"""Enhanced-audio ROM packs: SNES MSU-1, Mega Drive MSU-MD and MD+.

A pack is a patched cartridge ROM plus the streamed audio it expects to
find next to it, so unlike a plain ROM it has to travel as a *folder*.
The catalog stores one as a bundle and stamps it with a ``bundle_kind``
so a client knows what it is unpacking and where the target device wants
it:

``msu1``
    SNES.  ``<stem>.sfc`` + ``<stem>.msu`` (may be empty) + ``<stem>-N.pcm``
    tracks.  Every consumer (MiSTer SNES core, snes9x/bsnes, FXPak Pro)
    finds the audio by the ROM's own stem, so the files only have to agree
    with each other, not with the pack's folder name.

``msu-md``
    Mega Drive, the original community format: ``<stem>.md`` + ``<stem>.cue``
    + one raw ``BINARY`` audio image.  Emulators load the ``.md``; MiSTer
    plays it through the *MegaCD* core with the ROM renamed ``cart.rom``.

``mdplus``
    Mega Drive, the MegaSD-derived format now native to emulators and the
    MiSTer MegaDrive core: same ``.md`` + ``.cue`` shape, but the cue points
    at separate ``WAVE`` tracks.  Loaded as a plain ROM from its folder.

The two Mega Drive kinds carry differently patched ROMs, so one is never a
substitute for the other — the audio container in the cue sheet is what
tells them apart.  Detection is by contents only: a pack may be a loose
per-game folder or a ``.zip`` dropped anywhere under the system folder.
"""

from __future__ import annotations

import posixpath
import re
from typing import Callable, Iterable, Optional

MSU1 = "msu1"
MSU_MD = "msu-md"
MD_PLUS = "mdplus"

#: Canonical system code -> the pack kinds that system can carry.
MSU_SYSTEMS: dict[str, tuple[str, ...]] = {
    "SNES": (MSU1,),
    "MD": (MSU_MD, MD_PLUS),
}

_SNES_ROM_EXTS = frozenset({".sfc", ".smc"})
_MD_ROM_EXTS = frozenset({".md", ".gen", ".smd", ".bin"})

#: Files a pack ships that no consumer needs: emulator manifests from the
#: pack author's own setup, stray saves, patch sources.  Dropped from the
#: manifest so a client's folder holds only what plays.
_JUNK_EXTS = frozenset({".srm", ".asm", ".txt", ".nfo", ".ips", ".bps", ".url"})
_JUNK_NAMES = frozenset({"thumbs.db", ".ds_store", "desktop.ini"})

#: Archives at least this big are worth opening to look for a pack: the
#: smallest real pack is tens of MB of PCM, and no plain SNES or Mega Drive
#: ROM zip comes anywhere near it.
ARCHIVE_PEEK_MIN_SIZE = 16 * 1024 * 1024

#: ...unless the name already says what it is.
_NAME_HINT_RE = re.compile(r"msu|md\s*\+|mdplus", re.IGNORECASE)

_CUE_FILE_RE = re.compile(
    r'^\s*FILE\s+(?:"([^"]*)"|(\S+))\s+(\w+)\s*$', re.IGNORECASE | re.MULTILINE
)


def archive_worth_peeking(name: str, size: int) -> bool:
    """Whether a ``.zip`` in a SNES / Mega Drive folder might hold a pack."""
    return size >= ARCHIVE_PEEK_MIN_SIZE or bool(_NAME_HINT_RE.search(name))


def strip_common_root(names: Iterable[str]) -> tuple[str, list[str]]:
    """Drop the single top-level folder a zip's members all share.

    ``("ActRaiser (USA) (MSU1)", ["ActRaiser (USA) (MSU1).sfc", ...])`` for a
    pack zipped with its folder; ``("", names)`` when the members already sit
    at the root or don't share one.  Directory entries are discarded either
    way.
    """
    files = [n.replace("\\", "/") for n in names if n and not n.endswith("/")]
    files = [n.lstrip("/") for n in files]
    if not files:
        return "", []
    heads = {n.split("/", 1)[0] for n in files}
    if len(heads) != 1 or any("/" not in n for n in files):
        return "", files
    root = heads.pop()
    return root, [n.split("/", 1)[1] for n in files]


def is_junk(name: str) -> bool:
    base = posixpath.basename(name)
    lower = base.lower()
    if lower in _JUNK_NAMES or lower.startswith("."):
        return True
    return posixpath.splitext(lower)[1] in _JUNK_EXTS


def _ext(name: str) -> str:
    return posixpath.splitext(name)[1].lower()


def cue_audio_container(cue_text: str) -> Optional[str]:
    """``"WAVE"`` / ``"BINARY"`` / ... from a cue sheet's FILE lines, else None."""
    kinds = {m.group(3).upper() for m in _CUE_FILE_RE.finditer(cue_text)}
    if not kinds:
        return None
    if "WAVE" in kinds:
        return "WAVE"
    if "BINARY" in kinds:
        return "BINARY"
    return kinds.pop()


def cue_referenced_files(cue_text: str) -> set[str]:
    """Lower-cased basenames every FILE line of a cue sheet points at."""
    out: set[str] = set()
    for m in _CUE_FILE_RE.finditer(cue_text):
        ref = m.group(1) if m.group(1) is not None else m.group(2)
        out.add(posixpath.basename(ref.replace("\\", "/")).lower())
    return out


def detect_kind(
    system: str,
    names: Iterable[str],
    read_text: Callable[[str], str],
) -> Optional[str]:
    """Classify a file set as a pack kind, or None when it is not one.

    ``names`` are member paths relative to the pack root (see
    :func:`strip_common_root`); ``read_text`` returns a member's text and is
    only called for cue sheets.  Only root-level members count — a pack is
    flat, and anything nested is somebody else's folder.
    """
    kinds = MSU_SYSTEMS.get(system.upper())
    if not kinds:
        return None
    flat = [n for n in names if "/" not in n]
    exts = {_ext(n) for n in flat}

    if MSU1 in kinds:
        if ".msu" in exts and exts & _SNES_ROM_EXTS:
            return MSU1
        return None

    cues = [n for n in flat if _ext(n) == ".cue"]
    if not cues:
        return None
    referenced: set[str] = set()
    container = None
    for cue in cues:
        try:
            text = read_text(cue)
        except Exception:  # noqa: BLE001 - unreadable cue is "not a pack"
            return None
        referenced |= cue_referenced_files(text)
        container = container or cue_audio_container(text)
    # The ROM is whatever cart image the cue does *not* claim as audio.
    rom = [
        n for n in flat
        if _ext(n) in _MD_ROM_EXTS and n.lower() not in referenced
    ]
    if not rom or container is None:
        return None
    return MD_PLUS if container == "WAVE" else MSU_MD


def rom_member(kind: str, names: Iterable[str], read_text=None) -> Optional[str]:
    """The cartridge image inside a pack — what a launcher opens.

    Prefers a ROM whose stem matches the ``.msu`` / ``.cue`` sidecar, since
    a pack may ship a second ROM variant (``[Invincibility cheat].md``) next
    to the real one.
    """
    flat = [n for n in names if "/" not in n]
    if kind == MSU1:
        rom_exts, sidecar_ext = _SNES_ROM_EXTS, ".msu"
    else:
        rom_exts, sidecar_ext = _MD_ROM_EXTS, ".cue"
    sidecars = {posixpath.splitext(n)[0].lower() for n in flat if _ext(n) == sidecar_ext}
    referenced: set[str] = set()
    if kind != MSU1 and read_text is not None:
        for n in flat:
            if _ext(n) == ".cue":
                try:
                    referenced |= cue_referenced_files(read_text(n))
                except Exception:  # noqa: BLE001
                    pass
    roms = [
        n for n in flat
        if _ext(n) in rom_exts and n.lower() not in referenced
    ]
    if not roms:
        return None

    # ``.bin`` is also what MSU-MD audio ships as, so without a cue to rule
    # it out a proper cart extension outranks it; a sidecar-matching stem
    # outranks everything.
    def rank(n: str) -> tuple[int, int, int]:
        return (
            0 if posixpath.splitext(n)[0].lower() in sidecars else 1,
            1 if _ext(n) == ".bin" else 0,
            len(n),
        )

    return sorted(roms, key=rank)[0]


def display_name_for(raw: str) -> str:
    """Folder / zip name with archive suffixes gone, otherwise untouched.

    The ``(MSU1)`` / ``(MSU-MD)`` tag stays in: it is how a user tells the
    pack from the plain ROM in a list, even though the *title id* drops it
    so both share one save slot.
    """
    name = raw
    while _ext(name) in {".zip", ".7z", ".rar"}:
        name = posixpath.splitext(name)[0]
    return name.strip()


def plan_extraction(
    kind: str,
    members: Iterable[str],
    rom_rename: Optional[str] = None,
    read_text=None,
) -> list[tuple[str, str]]:
    """``[(zip member, relative destination)]`` for unpacking a pack.

    One rule for every client: the single wrapping folder is hoisted away,
    junk is dropped, and when the target wants the cart under a fixed name
    (MiSTer's MegaCD core: ``cart.rom``) the ROM member is renamed on the way
    out.  Members that would escape the target are refused by omission.
    """
    names = [n for n in members if n and not n.endswith("/")]
    _root, stripped = strip_common_root(names)
    rom = rom_member(kind, stripped, read_text) if rom_rename else None
    out: list[tuple[str, str]] = []
    for member, rel in zip(names, stripped):
        if rel.startswith("/") or ".." in rel.split("/"):
            continue
        if is_junk(rel):
            continue
        dest = rom_rename if (rom is not None and rel == rom) else rel
        out.append((member, dest))
    return out


#: The bracket that names the MSU hack's author in No-Intro-style pack names
#: (``[Hack by Conn & Kurrono v3]``).  Not part of the game's identity: the
#: pack plays the same save as the ROM it patched.  Translation, FastROM and
#: other tags stay, because a translated baseline ROM carries them too.
_HACK_TAG_RE = re.compile(r"\s*\[Hack by [^\]]*\]", re.IGNORECASE)


def strip_hack_tags(name: str) -> str:
    return re.sub(r"\s{2,}", " ", _HACK_TAG_RE.sub("", name)).strip()


def identity_candidates(display_name: str, rom_name: Optional[str]) -> list[str]:
    """Names to try, in order, when deciding which ROM a pack is a pack *of*.

    The pack's own name first, then that name without its ``[Hack by …]``
    tag(s), then the cart member's name and its stripped form.  The caller
    resolves each through the same identity rules as a plain ROM and takes
    the first that matches a ROM it knows; failing that, the earliest that
    the DAT recognises, and as a last resort the stripped pack name — a
    plain ROM being the likeliest partner.  Duplicates are dropped, order
    kept.
    """
    out: list[str] = []
    for raw in (display_name, strip_hack_tags(display_name),
                rom_name, strip_hack_tags(rom_name) if rom_name else None):
        if not raw:
            continue
        name = raw
        while _ext(name) in {".zip", ".7z", ".rar", ".sfc", ".smc", ".md", ".gen", ".smd", ".bin"}:
            name = posixpath.splitext(name)[0]
        name = name.strip()
        if name and name not in out:
            out.append(name)
    return out
