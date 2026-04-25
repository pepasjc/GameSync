package com.savesync.android.emulators.impl

import com.savesync.android.emulators.EmulatorBase
import com.savesync.android.emulators.SaveEntry
import java.io.File

class MgbaEmulator(
    /**
     * Optional explicit save folder override, configured in the Emulator
     * Configuration screen.  Wins over the built-in ``mGBA/saves`` lookup.
     */
    private val saveDirOverride: String? = null
) : EmulatorBase() {

    override val name: String = "mGBA"
    override val systemPrefix: String = "GBA"

    companion object {
        /** Key used in [com.savesync.android.storage.Settings.saveDirOverrides]. */
        const val EMULATOR_KEY = "mGBA"
    }

    override fun discoverSaves(): List<SaveEntry> {
        val savesDir = saveDirOverride
            ?.takeIf { it.isNotBlank() }
            ?.let(::File)
            ?.takeIf { it.exists() && it.isDirectory }
            ?: firstExisting("mGBA/saves", "mGBA")
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
