package com.savesync.android.emulators.impl

import com.savesync.android.emulators.EmulatorBase
import com.savesync.android.emulators.SaveEntry

class AetherSX2Emulator : EmulatorBase() {

    override val name: String = "AetherSX2 / NetherSX2"
    override val systemPrefix: String = "PS2"

    override fun discoverSaves(): List<SaveEntry> {
        val memcardsDir = firstExisting(
            "AetherSX2/memcards",
            "NetherSX2/memcards",
            "aethersx2/memcards",
            "nethersx2/memcards"
        ) ?: return emptyList()

        val result = mutableListOf<SaveEntry>()

        memcardsDir.listFiles()?.forEach { file ->
            if (file.isFile && file.extension.lowercase() == "ps2") {
                val romName = file.nameWithoutExtension
                val titleId = toTitleId(romName)
                result.add(
                    SaveEntry(
                        titleId = titleId,
                        displayName = romName,
                        systemName = systemPrefix,
                        saveFile = file,
                        saveDir = null
                    )
                )
            }
        }

        return result
    }
}
