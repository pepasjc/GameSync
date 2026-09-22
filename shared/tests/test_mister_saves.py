"""Tests for the shared MiSTer save rules.

These cover the rules themselves - no device, no SFTP - so both the desktop
client and the on-device client are exercised by one suite. The desktop-side
integration is covered separately by desktop/tests/test_mister_ssh_sync.py.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shared.mister_saves import (  # noqa: E402
    MISTER_MD_SAVE_SIZE,
    is_ps1_card_blank,
    is_segacd_bram_blank,
    md_from_mister,
    md_to_mister,
    needs_payload_read,
    ps1_card_serial,
    ps1_serial_from_filename,
    resolve_save_identity,
    resolve_title_id,
)


# ── PS1 cards ───────────────────────────────────────────────────────────────

def _ps1_card(entries=()):
    """A formatted 128 KB card; each entry is an in-card filename."""
    card = bytearray(b"\x00" * (128 * 1024))
    card[0:2] = b"MC"
    for index, name in enumerate(entries):
        offset = (index + 1) * 128
        card[offset] = 0x51
        encoded = name.encode("ascii")
        card[offset + 0x0A:offset + 0x0A + len(encoded)] = encoded
    return bytes(card)


def test_ps1_serial_read_from_inside_the_card():
    card = _ps1_card(["BASLUS-01324DRACULA"])
    assert ps1_card_serial(card) == "SLUS01324"
    assert not is_ps1_card_blank(card)


def test_ps1_blank_card_is_detected_but_has_no_serial():
    card = _ps1_card()
    assert ps1_card_serial(card) is None
    assert is_ps1_card_blank(card) is True


def test_ps1_unformatted_card_is_not_blank():
    """Only a *formatted* card counts as blank; junk is left alone."""
    assert is_ps1_card_blank(b"\x00" * (128 * 1024)) is False
    assert ps1_card_serial(b"\x00" * (128 * 1024)) is None


def test_ps1_filename_serial_accepts_the_written_forms():
    for stem in ("SLPM-86219", "SLPM_86219", "SLPM86219", "slus_012.34"):
        assert ps1_serial_from_filename(stem) is not None


def test_ps1_filename_serial_rejects_ordinary_game_names():
    """An unrecognised prefix must never be mistaken for a serial."""
    assert ps1_serial_from_filename("Final Fantasy IX (USA)") is None
    assert ps1_serial_from_filename("ABCD-12345") is None   # not a retail prefix


def test_in_card_serial_beats_a_serial_filename():
    """Variant discs boot under one serial and save under another."""
    identity = resolve_save_identity("PS1", _ps1_card(["BASLUS-01324DRACULA"]))
    assert resolve_title_id("PS1", "SLPM-86219", identity, "PS1_slug") \
        == "SLUS01324"


def test_serial_filename_used_when_the_card_has_no_save_yet():
    identity = resolve_save_identity("PS1", _ps1_card())
    assert identity.is_blank
    assert resolve_title_id("PS1", "SLPM-86219", identity, "PS1_slug") \
        == "SLPM86219"


# Seen live (2026-09-07): three MiSTer cards named for three different games
# all opened with the same Parasite Eve II save, so all three keyed themselves
# SLUS00594 and fought over one server slot.
SHARED_CARD = ["BASLUS-00594PE2", "BASLUS-00421RE2", "BISLPM-86023DRAX00"]


def test_shared_card_lists_every_serial_in_directory_order():
    from shared.mister_saves import ps1_card_serials

    identity = resolve_save_identity("PS1", _ps1_card(SHARED_CARD))
    assert identity.serials == ("SLUS00594", "SLUS00421", "SLPM86023")
    assert identity.serial == "SLUS00594"
    assert ps1_card_serials(_ps1_card(SHARED_CARD + ["BASLUS-00594PE2B"])) \
        == ["SLUS00594", "SLUS00421", "SLPM86023"]


def test_shared_card_is_keyed_by_the_game_it_is_named_for():
    identity = resolve_save_identity("PS1", _ps1_card(SHARED_CARD))

    # Disc-serial file name.
    assert resolve_title_id("PS1", "SLPM-86023", identity, "PS1_slug") \
        == "SLPM86023"

    # Catalogue hit on the game name (a translation, so the name alone
    # would never have matched the serial).
    def lookup(system, stem):
        assert system == "PS1"
        return "SLPM86023" if "Dracula" in stem else None

    assert resolve_title_id("PS1", "Akumajou Dracula X [T-En]", identity,
                            "PS1_akumajou", catalog_lookup=lookup) \
        == "SLPM86023"


def test_shared_card_without_a_save_for_its_game_keeps_the_first_serial():
    """The variant-disc rule: the card is all we have to go on."""
    identity = resolve_save_identity("PS1", _ps1_card(SHARED_CARD))
    assert resolve_title_id("PS1", "SLUS-01324", identity, "PS1_slug") \
        == "SLUS00594"
    assert resolve_title_id("PS1", "Breath of Fire IV (USA)", identity,
                            "PS1_bof4", catalog_lookup=lambda s, n: "SLUS01324") \
        == "SLUS00594"


def test_single_game_card_never_consults_the_name():
    identity = resolve_save_identity("PS1", _ps1_card(["BASLUS-01324BOF4"]))

    def lookup(system, stem):
        raise AssertionError("should not be called for a one-game card")

    assert resolve_title_id("PS1", "SLPM-86219", identity, "PS1_slug",
                            catalog_lookup=lookup) == "SLUS01324"


def test_identity_from_cached_serials_round_trips():
    """Both clients rebuild the identity from their hash caches."""
    from shared.mister_saves import SaveIdentity

    identity = SaveIdentity(b"", serials=("SLUS00594", "SLPM86023"))
    assert identity.serial == "SLUS00594"
    legacy = SaveIdentity(b"", serial="SLUS00594")
    assert legacy.serials == ("SLUS00594",)
    assert resolve_title_id("PS1", "SLPM-86023", identity, "x") == "SLPM86023"
    assert resolve_title_id("PS1", "SLPM-86023", legacy, "x") == "SLUS00594"


# ── Sega CD ─────────────────────────────────────────────────────────────────

def _segacd_bram(occupied=False):
    data = bytearray(b"\x00" * 8192)
    data[-0x40:-0x40 + len(b"SEGA_CD_ROM")] = b"SEGA_CD_ROM"
    if occupied:
        data[-0x60:-0x40] = b"A" * 0x20
    return bytes(data)


def test_segacd_blank_bram_detected():
    assert is_segacd_bram_blank(_segacd_bram()) is True
    assert is_segacd_bram_blank(_segacd_bram(occupied=True)) is False


def test_segacd_unformatted_bram_is_not_blank():
    assert is_segacd_bram_blank(b"\x00" * 8192) is False


def test_segacd_payload_is_not_converted():
    """Raw 8 KB BRAM is byte-for-byte what other clients keep."""
    data = _segacd_bram(occupied=True)
    assert resolve_save_identity("SEGACD", data).hash_payload == data


# ── Mega Drive ──────────────────────────────────────────────────────────────

def _md_expanded(payload, sram=8192):
    packed = bytearray(b"\x00" * sram)
    packed[:len(payload)] = payload
    out = bytearray(sram * 2)
    out[1::2] = packed
    return bytes(out)


def test_md_round_trips_between_layouts():
    expanded = _md_expanded(b"BOWIE")
    core = md_to_mister(expanded)
    assert len(core) == MISTER_MD_SAVE_SIZE
    assert core[8192:] == b"\xff" * (MISTER_MD_SAVE_SIZE - 8192)
    assert md_from_mister(core, target_size=len(expanded)) == expanded


def test_md_core_image_hashes_as_the_expanded_layout():
    """Or a core-written save would never match its own upload."""
    core = md_to_mister(_md_expanded(b"SARAH"))
    identity = resolve_save_identity("MD", core)
    assert identity.hash_payload == md_from_mister(core)
    assert identity.hash_payload != core


def test_md_empty_core_image_is_blank():
    assert resolve_save_identity("MD", b"\xff" * MISTER_MD_SAVE_SIZE).is_blank


def test_md_non_core_sized_save_passes_through():
    small = b"\x01\x02\x03"
    assert md_from_mister(small) == small
    assert resolve_save_identity("MD", small).hash_payload == small


# ── Dispatch ────────────────────────────────────────────────────────────────

def test_only_the_special_systems_need_a_payload_read():
    assert needs_payload_read("PS1") is True
    assert needs_payload_read("SAT") is True
    assert needs_payload_read("SEGACD") is True
    assert needs_payload_read("MD", MISTER_MD_SAVE_SIZE) is True
    # A Mega Drive save that is not a core image needs no conversion.
    assert needs_payload_read("MD", 8192) is False
    assert needs_payload_read("SNES") is False
    assert needs_payload_read("GBA", 32768) is False


def test_plain_cartridge_save_is_untouched():
    data = b"\x42" * 8192
    identity = resolve_save_identity("SNES", data)
    assert identity.hash_payload == data
    assert identity.serial is None
    assert identity.is_blank is False


def test_catalog_lookup_bridges_a_renamed_saturn_save():
    """A translation patch renames the file beyond recognition, so the
    catalogue is the only bridge to the server's key."""
    identity = resolve_save_identity("SNES", b"\x00" * 16)  # no serial

    def catalog(system, stem):
        assert system == "SAT"
        return "SAT_T-9527G" if "Symphony" in stem else None

    assert resolve_title_id(
        "SAT", "Castlevania - Symphony of the Night (Japan) [T-En]",
        identity, "SAT_slug", catalog_lookup=catalog) == "SAT_T-9527G"
    assert resolve_title_id(
        "SAT", "Some Other Game", identity, "SAT_slug",
        catalog_lookup=catalog) == "SAT_slug"


# ── Housekeeping bytes ──────────────────────────────────────────────────────

def test_ps1_write_test_frame_is_not_save_data():
    """Observed live: the MiSTer core writes "MC" plus a checksum into block 0
    frame 63, where a card that came from a PSP had zeros. Three bytes, and the
    save is otherwise identical - it must not read as a conflict."""
    from shared.mister_saves import content_key, same_content

    card = bytearray(_ps1_card(["BASLUS-01324DRACULA"]))
    other = bytearray(card)
    other[8064] = 0x4D          # 'M'
    other[8065] = 0x43          # 'C'
    other[8191] = 0x0E          # checksum

    assert bytes(card) != bytes(other)
    assert same_content("PS1", bytes(card), bytes(other)) is True
    assert content_key("PS1", bytes(card)) == content_key("PS1", bytes(other))


def test_ps1_real_save_data_still_differs():
    """The exemption must not hide an actual change."""
    from shared.mister_saves import same_content

    card = bytearray(_ps1_card(["BASLUS-01324DRACULA"]))
    other = bytearray(card)
    other[9000] = 0x99          # inside a save block

    assert same_content("PS1", bytes(card), bytes(other)) is False


def test_ps1_directory_change_still_differs():
    from shared.mister_saves import same_content

    card = bytearray(_ps1_card(["BASLUS-01324DRACULA"]))
    other = bytearray(card)
    other[128 + 0x0A] = ord("X")   # a directory entry's in-card name

    assert same_content("PS1", bytes(card), bytes(other)) is False


def test_an_unformatted_card_is_compared_verbatim():
    from shared.mister_saves import same_content

    junk = b"\x01" * (128 * 1024)
    other = b"\x02" + junk[1:]
    assert same_content("PS1", junk, other) is False


def test_systems_without_housekeeping_compare_byte_for_byte():
    from shared.mister_saves import same_content

    assert same_content("SNES", b"abc", b"abc") is True
    assert same_content("SNES", b"abc", b"abd") is False


def test_describe_difference_names_ps1_regions():
    """The user sees "upload" on a card they never saved to; the description
    is what tells the core's write-test frame apart from a real save."""
    from shared.mister_saves import describe_difference

    card = _ps1_card(["BASLUS-01324DRACULA", "BASLUS-01251FF7-S01"])
    assert describe_difference("PS1", card, card) == ""

    other = bytearray(card)
    other[8064:8066] = b"MC"
    assert describe_difference("PS1", card, bytes(other)) == \
        "block 0 write-test frame"

    other = bytearray(card)
    other[2 * 8192 + 100] = 0x99          # inside the second save's block
    other[2 * 8192 + 4000] = 0x98         # same block, another frame: one entry
    assert describe_difference("PS1", card, bytes(other)) == \
        "block 2 (BASLUS-01251FF7-S01)"

    other = bytearray(card)
    other[128 + 0x0A] = ord("X")
    other[8064] = 0x4D
    assert describe_difference("PS1", card, bytes(other)) == \
        "block 0 directory, block 0 write-test frame"


def test_describe_difference_size_and_generic():
    from shared.mister_saves import describe_difference

    assert describe_difference("SNES", b"a" * 10, b"a" * 12) == \
        "size 10 vs 12 bytes"
    left = bytes(4096)
    right = bytearray(left)
    right[3000] = 1
    assert describe_difference("SNES", left, bytes(right)) == "offset 0x800"


def test_md_filler_bytes_are_not_save_data():
    """Observed live: every odd byte matched, only the even-offset bus filler
    differed, and the save read as a permanent conflict."""
    from shared.mister_saves import same_content

    packed = bytes(range(256)) * 32
    ours = bytearray(len(packed) * 2)
    ours[1::2] = packed                      # we write 0x00 filler
    theirs = bytearray(b"\xff" * (len(packed) * 2))
    theirs[1::2] = packed                    # another client wrote 0xFF

    assert bytes(ours) != bytes(theirs)
    assert same_content("MD", bytes(ours), bytes(theirs)) is True


def test_md_a_real_sram_change_still_differs():
    from shared.mister_saves import same_content

    packed = bytearray(bytes(range(256)) * 32)
    ours = bytearray(len(packed) * 2)
    ours[1::2] = packed
    packed[10] ^= 0xFF                       # a byte the game actually wrote
    theirs = bytearray(len(packed) * 2)
    theirs[1::2] = packed

    assert same_content("MD", bytes(ours), bytes(theirs)) is False


def test_a_card_lists_every_game_it_holds():
    """A PlayStation card is shared: one on a real MiSTer held nine saves
    across eight games while the server's copy of it held one."""
    from shared.mister_saves import ps1_card_save_names

    card = _ps1_card(["BASLUS-00594G020", "BASLUS-0042100",
                      "BISLPM-86023DRAX00"])
    assert ps1_card_save_names(card) == ["BASLUS-00594G020", "BASLUS-0042100",
                                         "BISLPM-86023DRAX00"]


def test_an_empty_or_unformatted_card_lists_nothing():
    from shared.mister_saves import ps1_card_save_names

    assert ps1_card_save_names(_ps1_card()) == []
    assert ps1_card_save_names(b"\x00" * (128 * 1024)) == []
    assert ps1_card_save_names(b"") == []


def test_the_identity_serial_is_only_the_first_save():
    """The card is keyed by whichever save comes first in the directory, even
    though it holds several games."""
    card = _ps1_card(["BASLUS-01324DRACULA", "BASLUS-00421SOMETHING"])
    assert ps1_card_serial(card) == "SLUS01324"


def test_slug_system_takes_only_an_exact_catalogue_file_name():
    """The server keyed a translation patch under the original title's slug.
    A save named after that exact file goes to that slot; a regional
    near-miss on a slug system is still refused."""
    from shared.title_match import TitleMatcher

    matcher = TitleMatcher()
    patched = ("Famicom Detective Club Part II (Japan) (NP) "
               "[T-En by Demiforce v1.00] [n].sfc")
    matcher.add("SNES_famicom_tantei_club_part_ii_ushiro_ni_tatsu_shoujo_japan",
                patched, "Famicom Tantei Club Part II (Japan) (NP)")
    matcher.add("SNES_chrono_trigger_usa", "Chrono Trigger (USA).sfc")

    assert matcher.lookup_exact(patched[:-4]) == \
        "SNES_famicom_tantei_club_part_ii_ushiro_ni_tatsu_shoujo_japan"
    # Loose matching bridges an unmarked name; exact does not.
    assert matcher.lookup("Chrono Trigger") == "SNES_chrono_trigger_usa"
    assert matcher.lookup_exact("Chrono Trigger") is None

    identity = resolve_save_identity("SNES", b"\x00" * 16)
    assert resolve_title_id(
        "SNES", patched[:-4], identity, "SNES_famicom_detective_club_x",
        catalog_lookup=lambda s, n: matcher.lookup_exact(n)) == \
        "SNES_famicom_tantei_club_part_ii_ushiro_ni_tatsu_shoujo_japan"
