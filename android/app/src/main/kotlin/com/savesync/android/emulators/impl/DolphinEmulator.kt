package com.savesync.android.emulators.impl

import com.savesync.android.emulators.EmulatorBase
import com.savesync.android.emulators.SaveEntry
import java.io.File

class DolphinEmulator : EmulatorBase() {

    override val name: String = "Dolphin"
    override val systemPrefix: String = "GC"

    private val gcRegions = listOf("USA", "EUR", "JAP", "PAL", "NTSC")

    override fun discoverSaves(): List<SaveEntry> {
        val result = mutableListOf<SaveEntry>()

        // GameCube saves: /dolphin-emu/GC/{region}/*.gci
        val gcBaseDir = File(baseDir, "dolphin-emu/GC")
        if (gcBaseDir.exists()) {
            gcRegions.forEach { region ->
                val regionDir = File(gcBaseDir, region)
                if (regionDir.exists()) {
                    regionDir.listFiles()?.forEach { file ->
                        if (file.isFile && file.extension.lowercase() == "gci") {
                            val romName = file.nameWithoutExtension
                            val slug = romName
                                .lowercase()
                                .replace(Regex("[^a-z0-9]+"), "_")
                                .trim('_')
                            val titleId = "GC_$slug"
                            result.add(
                                SaveEntry(
                                    titleId = titleId,
                                    displayName = romName,
                                    systemName = "GC",
                                    saveFile = file,
                                    saveDir = null
                                )
                            )
                        }
                    }
                }
            }
        }

        // Wii saves: /dolphin-emu/Wii/title/
        val wiiTitleDir = File(baseDir, "dolphin-emu/Wii/title")
        if (wiiTitleDir.exists()) {
            wiiTitleDir.listFiles()?.forEach { highDir ->
                if (highDir.isDirectory) {
                    highDir.listFiles()?.forEach { lowDir ->
                        if (lowDir.isDirectory) {
                            // Title ID = high + low directories combined
                            val wiiTitleId = "WII_${highDir.name}_${lowDir.name}"
                                .lowercase()
                                .replace(Regex("[^a-z0-9]+"), "_")
                                .trim('_')
                            val displayName = "${highDir.name}${lowDir.name}"
                            result.add(
                                SaveEntry(
                                    titleId = "WII_${highDir.name}${lowDir.name}",
                                    displayName = displayName,
                                    systemName = "WII",
                                    saveFile = null,
                                    saveDir = lowDir
                                )
                            )
                        }
                    }
                }
            }
        }

        return result
    }
}
