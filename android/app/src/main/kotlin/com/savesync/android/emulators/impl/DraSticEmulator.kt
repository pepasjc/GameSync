package com.savesync.android.emulators.impl

import com.savesync.android.emulators.EmulatorBase
import com.savesync.android.emulators.SaveEntry

class DraSticEmulator : EmulatorBase() {

    override val name: String = "DraStic"
    override val systemPrefix: String = "NDS"

    override fun discoverSaves(): List<SaveEntry> {
        val backupDir = firstExisting("drastic/backup", "DraStic/backup", "Drastic/backup")
            ?: return emptyList()

        val result = mutableListOf<SaveEntry>()

        backupDir.listFiles()?.forEach { file ->
            if (file.isFile && file.extension.lowercase() == "dsv") {
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
