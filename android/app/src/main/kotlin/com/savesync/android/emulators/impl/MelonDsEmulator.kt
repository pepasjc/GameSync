package com.savesync.android.emulators.impl

import com.savesync.android.emulators.EmulatorBase
import com.savesync.android.emulators.SaveEntry

class MelonDsEmulator : EmulatorBase() {

    override val name: String = "melonDS"
    override val systemPrefix: String = "NDS"

    override fun discoverSaves(): List<SaveEntry> {
        val savesDir = firstExisting("melonDS", "melonDS Android", "melonds")
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
