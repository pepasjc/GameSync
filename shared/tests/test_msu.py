"""MSU pack detection: kind by contents, wrapping-folder hoisting, ROM member."""

from __future__ import annotations

from shared import msu

_MSU_MD_CUE = 'FILE "Game (MSU-MD).bin" BINARY\n  TRACK 01 AUDIO\n    INDEX 01 00:00:00\n'
_MD_PLUS_CUE = (
    'FILE "Game - Track 02.wav" WAVE\n  TRACK 02 AUDIO\n    INDEX 01 00:00:00\n'
    'FILE "Game - Track 03.wav" WAVE\n  TRACK 03 AUDIO\n    INDEX 01 00:00:00\n'
)


def _cues(mapping):
    return lambda name: mapping[name]


def test_msu1_needs_msu_sidecar_and_rom():
    names = ["ActRaiser (USA) (MSU1).sfc", "ActRaiser (USA) (MSU1).msu",
             "ActRaiser (USA) (MSU1)-1.pcm", "ActRaiser (USA) (MSU1).bml"]
    assert msu.detect_kind("SNES", names, _cues({})) == msu.MSU1
    assert msu.detect_kind("SNES", names[:1], _cues({})) is None
    # A sidecar with no cart is not a pack either.
    assert msu.detect_kind("SNES", names[1:], _cues({})) is None
    # Only SNES carries MSU-1.
    assert msu.detect_kind("MD", names, _cues({})) is None


def test_msu1_ignores_nested_files():
    names = ["sub/Game.sfc", "sub/Game.msu"]
    assert msu.detect_kind("SNES", names, _cues({})) is None


def test_msu_md_is_binary_audio_beside_md_rom():
    names = ["Game (MSU-MD).md", "Game (MSU-MD).cue", "Game (MSU-MD).bin"]
    kind = msu.detect_kind("MD", names, _cues({"Game (MSU-MD).cue": _MSU_MD_CUE}))
    assert kind == msu.MSU_MD


def test_md_plus_is_wave_audio_beside_rom():
    names = ["Game.md", "Game.cue", "Game - Track 02.wav", "Game - Track 03.wav"]
    kind = msu.detect_kind("MD", names, _cues({"Game.cue": _MD_PLUS_CUE}))
    assert kind == msu.MD_PLUS


def test_bin_rom_not_mistaken_for_audio():
    """A ``.bin`` cart is a ROM only when the cue doesn't claim it as audio."""
    names = ["Game.bin", "Game.cue", "Game - Track 02.wav"]
    kind = msu.detect_kind("MD", names, _cues({"Game.cue": _MD_PLUS_CUE}))
    assert kind == msu.MD_PLUS
    # Misfiled Sega CD disc: cue + data bin, no cart image at all.
    names = ["Disc.cue", "Disc.bin"]
    cue = 'FILE "Disc.bin" BINARY\n  TRACK 01 MODE1/2352\n    INDEX 01 00:00:00\n'
    assert msu.detect_kind("MD", names, _cues({"Disc.cue": cue})) is None


def test_unreadable_cue_is_not_a_pack():
    def boom(_name):
        raise OSError("nope")
    assert msu.detect_kind("MD", ["G.md", "G.cue", "G.bin"], boom) is None


def test_strip_common_root():
    root, names = msu.strip_common_root([
        "Pack/", "Pack/Game.sfc", "Pack/Game.msu", "Pack/Game-1.pcm",
    ])
    assert root == "Pack"
    assert names == ["Game.sfc", "Game.msu", "Game-1.pcm"]

    root, names = msu.strip_common_root(["Game.md", "Game.cue", "Game.bin"])
    assert root == ""
    assert names == ["Game.md", "Game.cue", "Game.bin"]

    # Two roots, or one loose file beside a folder: nothing to hoist.
    assert msu.strip_common_root(["A/x.sfc", "B/x.msu"])[0] == ""
    assert msu.strip_common_root(["A/x.sfc", "x.msu"])[0] == ""
    assert msu.strip_common_root([]) == ("", [])


def test_rom_member_prefers_the_sidecar_stem():
    names = ["Game (MSU-MD) [Invincibility cheat].md", "Game (MSU-MD).md",
             "Game (MSU-MD).cue", "Game (MSU-MD).bin"]
    assert msu.rom_member(msu.MSU_MD, names) == "Game (MSU-MD).md"
    snes = ["bb_msu1.smc", "bb_msu1.msu", "bb_msu1-4.pcm"]
    assert msu.rom_member(msu.MSU1, snes) == "bb_msu1.smc"
    assert msu.rom_member(msu.MSU1, ["only.msu"]) is None
    # No cue reader: a real cart extension beats the same-stem audio .bin.
    shinobi = ["Super Shinobi (MSU-MD) [cheat].md", "Super Shinobi (MSU-MD).bin",
               "Super Shinobi (MSU-MD).cue", "Super Shinobi (MSU-MD).md"]
    assert msu.rom_member(msu.MSU_MD, shinobi) == "Super Shinobi (MSU-MD).md"


def test_rom_member_skips_cue_referenced_bin():
    names = ["Game.bin", "Game.cue", "Audio.bin"]
    cue = 'FILE "Audio.bin" BINARY\n  TRACK 01 AUDIO\n    INDEX 01 00:00:00\n'
    assert msu.rom_member(msu.MSU_MD, names, _cues({"Game.cue": cue})) == "Game.bin"


def test_archive_worth_peeking():
    assert msu.archive_worth_peeking("Game.zip", 500 * 1024 * 1024)
    assert msu.archive_worth_peeking("Game (MSU1).zip", 10)
    assert msu.archive_worth_peeking("Game (MD+).zip", 10)
    assert not msu.archive_worth_peeking("Game (USA).zip", 4 * 1024 * 1024)


def test_junk_and_display_name():
    assert msu.is_junk("Game.srm")
    assert msu.is_junk("bb_msu.asm")
    assert msu.is_junk("Thumbs.db")
    assert not msu.is_junk("Game.bml")
    assert not msu.is_junk("Game-12.pcm")
    assert msu.display_name_for("ActRaiser (USA) (MSU1).zip") == "ActRaiser (USA) (MSU1)"
    assert msu.display_name_for("Folder Name") == "Folder Name"


def test_plan_extraction_hoists_drops_junk_and_renames():
    members = ["Pack/", "Pack/Game.md", "Pack/Game.cue", "Pack/Game.bin",
               "Pack/Game.srm", "Pack/../evil.md"]
    plan = msu.plan_extraction(msu.MSU_MD, members, rom_rename="cart.rom")
    assert plan == [
        ("Pack/Game.md", "cart.rom"),
        ("Pack/Game.cue", "Game.cue"),
        ("Pack/Game.bin", "Game.bin"),
    ]
    # No rename, no wrapping folder: members map onto themselves.
    plan = msu.plan_extraction(msu.MSU1, ["G.sfc", "G.msu", "G-1.pcm", "Thumbs.db"])
    assert plan == [("G.sfc", "G.sfc"), ("G.msu", "G.msu"), ("G-1.pcm", "G-1.pcm")]


def test_identity_candidates_strip_only_hack_tags():
    assert msu.strip_hack_tags("Area 88 (USA) (MSU1) [T-En by Blizzz v1.03] [Hack by Kurrono & Conn v2] [n]") == \
        "Area 88 (USA) (MSU1) [T-En by Blizzz v1.03] [n]"
    cands = msu.identity_candidates(
        "ActRaiser (USA) (MSU1) [Hack by DarkShock v1.0]", "ActRaiser (USA) (MSU1).sfc")
    assert cands == [
        "ActRaiser (USA) (MSU1) [Hack by DarkShock v1.0]",
        "ActRaiser (USA) (MSU1)",
    ]
    # No tag, odd cart name: two distinct candidates, no blanks.
    assert msu.identity_candidates("Bubsy (USA)", "bb_msu1.smc") == ["Bubsy (USA)", "bb_msu1"]
    assert msu.identity_candidates("Game", None) == ["Game"]
