package com.savesync.android.emulators.impl

import com.savesync.android.emulators.EmulatorBase
import com.savesync.android.emulators.SaveEntry
import java.io.File

/**
 * Dolphin GC emulator save scanner.
 *
 * Supports all major Dolphin Android forks (dolphin-mmjr, dolphin-emu, etc.).
 * Default lookup path is `dolphin-mmjr/GC` on external storage; override with
 * [dolphinMemCardDir] to point at any dolphin variant's GC folder.
 *
 * Expected folder structure:
 *
 *     <gcRoot>/<REGION>/<Card Name>/<XX>-<GAMECODE>-<description>.gci
 *
 * For example:
 *
 *     dolphin-mmjr/GC/USA/Card A/01-GM4E-MarioKart Double Dash!!.gci
 *     dolphin-mmjr/GC/EUR/Card A/01-GALP-Super Smash Bros. Melee.gci
 *
 * Each unique 4-char GAMECODE maps to one SaveEntry with titleId `GC_<code>`.
 * If the same game code has multiple .gci files in a card (multiple save slots),
 * the most-recently-modified file is used as the primary; the rest go into extraFiles.
 */
class DolphinEmulator(private val dolphinMemCardDir: String = "") : EmulatorBase() {

    override val name: String = "Dolphin"
    override val systemPrefix: String = "GC"

    // GCI filename format: "<hex_slot>-<GAMECODE>-<description>.gci"
    // e.g. "01-GM4E-MarioKart Double Dash!!.gci", "8P-GFZE-f_zero.dat.gci"
    private val gciNameRegex = Regex("""^[0-9A-Fa-f]+-([A-Z0-9]{4})-""")

    private val gcRegions = listOf("USA", "EUR", "JAP", "PAL", "NTSC")

    /** Returns the GC memory card root, trying user override then common paths. */
    private fun findGcBaseDir(): File? {
        if (dolphinMemCardDir.isNotBlank()) {
            val custom = File(dolphinMemCardDir)
            if (custom.exists() && custom.isDirectory) return custom
        }
        return firstExisting(
            "dolphin-mmjr/GC",
            "dolphin-emu/GC",
            "dolphin/GC",
            "Dolphin/GC",
            "Dolphin MMJR/GC",
        )
    }

    /** Extract the 4-char GC game code from a .gci filename, or null if not parseable. */
    private fun gciGameCode(filename: String): String? {
        val stem = filename.substringBeforeLast(".")
        return gciNameRegex.find(stem)?.groupValues?.get(1)?.uppercase()
    }

    /** Extract a human-readable description from a .gci filename stem.
     *
     *  "01-GM4E-MarioKart Double Dash!!" → "MarioKart Double Dash!!"
     */
    private fun gciDescription(nameWithoutExt: String): String {
        val parts = nameWithoutExt.split("-", limit = 3)
        return parts.getOrNull(2)?.ifBlank { nameWithoutExt } ?: nameWithoutExt
    }

    override fun discoverSaves(): List<SaveEntry> {
        val result = mutableListOf<SaveEntry>()

        val gcBaseDir = findGcBaseDir() ?: return result

        for (region in gcRegions) {
            val regionDir = File(gcBaseDir, region)
            if (!regionDir.exists() || !regionDir.isDirectory) continue

            // Each subdirectory is a memory card slot (e.g. "Card A", "Card B")
            val cardDirs = regionDir.listFiles()?.filter { it.isDirectory } ?: continue

            for (cardDir in cardDirs) {
                // Group .gci files by 4-char game code
                val byCode = mutableMapOf<String, MutableList<File>>()
                cardDir.listFiles()
                    ?.filter { it.isFile && it.extension.lowercase() == "gci" }
                    ?.forEach { file ->
                        val code = gciGameCode(file.name) ?: return@forEach
                        byCode.getOrPut(code) { mutableListOf() }.add(file)
                    }

                for ((code, files) in byCode) {
                    val sorted = files.sortedByDescending { it.lastModified() }
                    val primary = sorted.first()
                    val extras = sorted.drop(1)

                    val displayName = gciDescription(primary.nameWithoutExtension)
                    val titleId = "GC_${code.lowercase()}"

                    result.add(
                        SaveEntry(
                            titleId = titleId,
                            displayName = displayName,
                            systemName = "GC",
                            saveFile = primary,
                            extraFiles = extras,
                            saveDir = null
                        )
                    )
                }
            }
        }

        return result
    }
}
