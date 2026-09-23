package com.savesync.android.emulators

import com.savesync.android.emulators.impl.AetherSX2Emulator

/**
 * Which PS2 emulator GameSync syncs memory cards for.
 *
 * AetherSX2 / NetherSX2 and ARMSX2 are all PCSX2 derivatives and use the same
 * ``<name>.ps2`` card format, so the save logic is shared
 * ([AetherSX2Emulator]); only the folder
 * the cards live in differs.  Only the selected emulator is scanned, so a
 * device with both installed doesn't list every PS2 save twice, and
 * server-only downloads land in the right app's folder.
 */
enum class Ps2EmulatorChoice(
    val wireValue: String,
    val label: String,
    /** Key into [com.savesync.android.storage.Settings.saveDirOverrides]. */
    val emulatorKey: String,
    /** Memcard folders relative to external storage / the EmuDeck root, most likely first. */
    val memcardCandidates: List<String>,
) {
    AETHERSX2(
        "aethersx2",
        "AetherSX2 / NetherSX2",
        AetherSX2Emulator.EMULATOR_KEY,
        listOf(
            "Android/data/xyz.aethersx2.android/files/memcards",
            "Android/data/xyz.aethersx2.android/files/Memcards",
            "Android/data/xyz.aethersx2.android/files/memorycards",
            "Android/data/xyz.aethersx2.android/files/MemoryCards",
            "memcards",
            "Memcards",
            "memorycards",
            "MemoryCards",
            "AetherSX2/memcards",
            "NetherSX2/memcards",
            "aethersx2/memcards",
            "nethersx2/memcards"
        )
    ),
    ARMSX2(
        "armsx2",
        "ARMSX2",
        AetherSX2Emulator.ARMSX2_EMULATOR_KEY,
        listOf(
            "Android/data/come.nanodata.armsx2/files/memcards",
            "Android/data/come.nanodata.armsx2/files/Memcards",
            "ARMSX2/memcards",
            "armsx2/memcards"
        )
    );

    companion object {
        fun fromWireValue(value: String?): Ps2EmulatorChoice =
            values().firstOrNull { it.wireValue.equals(value, ignoreCase = true) }
                ?: AETHERSX2
    }
}
