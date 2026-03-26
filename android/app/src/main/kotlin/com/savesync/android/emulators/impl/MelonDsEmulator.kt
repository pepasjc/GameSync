package com.savesync.android.emulators.impl

import com.savesync.android.emulators.EmulatorBase
import com.savesync.android.emulators.SaveEntry

class MelonDsEmulator(private val romScanDir: String = "") : EmulatorBase() {

    override val name: String = "melonDS"
    override val systemPrefix: String = "NDS"

    override fun discoverSaves(): List<SaveEntry> {
        val savesDir = firstExisting("melonDS", "melonDS Android", "melonds")
            ?: return emptyList()

        // Build NDS ROM search dirs for gamecode lookup
        val romDirs = ndsRomSearchDirs(romScanDir)

        val result = mutableListOf<SaveEntry>()

        savesDir.listFiles()?.forEach { file ->
            if (file.isFile && file.extension.lowercase() == "sav") {
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
