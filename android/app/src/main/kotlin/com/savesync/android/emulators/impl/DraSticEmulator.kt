package com.savesync.android.emulators.impl

import com.savesync.android.emulators.EmulatorBase
import com.savesync.android.emulators.SaveEntry
import java.io.File

class DraSticEmulator(
    private val romScanDir: String = "",
    /**
     * Optional explicit save folder override, configured in the Emulator
     * Configuration screen.  Wins over the built-in ``drastic/backup``
     * auto-detection when set.
     */
    private val saveDirOverride: String? = null
) : EmulatorBase() {

    override val name: String = "DraStic"
    override val systemPrefix: String = "NDS"

    companion object {
        /** Key used in [com.savesync.android.storage.Settings.saveDirOverrides]. */
        const val EMULATOR_KEY = "DraStic"
    }

    override fun discoverSaves(): List<SaveEntry> {
        val backupDir = saveDirOverride
            ?.takeIf { it.isNotBlank() }
            ?.let(::File)
            ?.takeIf { it.exists() && it.isDirectory }
            ?: firstExisting("drastic/backup", "DraStic/backup", "Drastic/backup")
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
