package com.savesync.android.emulators.impl

import com.savesync.android.emulators.EmulatorBase
import com.savesync.android.emulators.SaveEntry

class DraSticEmulator(private val romScanDir: String = "") : EmulatorBase() {

    override val name: String = "DraStic"
    override val systemPrefix: String = "NDS"

    override fun discoverSaves(): List<SaveEntry> {
        val backupDir = firstExisting("drastic/backup", "DraStic/backup", "Drastic/backup")
            ?: return emptyList()

        // Build NDS ROM search dirs for gamecode lookup
        val romDirs = ndsRomSearchDirs(romScanDir) + listOfNotNull(
            firstExisting("drastic/roms", "DraStic/roms", "Drastic/roms")
        )

        val result = mutableListOf<SaveEntry>()

        backupDir.listFiles()?.forEach { file ->
            if (file.isFile && file.extension.lowercase() == "dsv") {
                val romName = file.nameWithoutExtension
                // Try to read the gamecode from the matching NDS ROM file.
                // Fall back to slug-based title ID if the ROM can't be found.
                val romFile = findNdsRom(romName, romDirs)
                val titleId = romFile?.let { readNdsGamecode(it) } ?: toTitleId(romName)
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
