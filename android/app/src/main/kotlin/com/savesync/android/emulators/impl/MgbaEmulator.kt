package com.savesync.android.emulators.impl

import com.savesync.android.emulators.EmulatorBase
import com.savesync.android.emulators.SaveEntry

class MgbaEmulator : EmulatorBase() {

    override val name: String = "mGBA"
    override val systemPrefix: String = "GBA"

    override fun discoverSaves(): List<SaveEntry> {
        val savesDir = firstExisting("mGBA/saves", "mGBA")
            ?: return emptyList()

        val result = mutableListOf<SaveEntry>()

        savesDir.listFiles()?.forEach { file ->
            if (file.isFile && file.extension.lowercase() == "sav") {
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
