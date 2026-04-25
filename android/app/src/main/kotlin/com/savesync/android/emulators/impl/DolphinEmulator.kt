package com.savesync.android.emulators.impl

import android.os.Environment
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
class DolphinEmulator(
    private val dolphinMemCardDir: String = "",
    private val dolphinRootDir: File? = null
) : EmulatorBase() {

    override val name: String = "Dolphin"
    override val systemPrefix: String = "GC"

    // GCI filename format: "<hex_slot>-<GAMECODE>-<description>.gci"
    // e.g. "01-GM4E-MarioKart Double Dash!!.gci", "8P-GFZE-f_zero.dat.gci"
    private val gciNameRegex = Regex("""^[0-9A-Fa-f]+-([A-Z0-9]{4})-""")

    private val gcRegions = listOf("USA", "EUR", "JAP", "PAL", "NTSC")

    companion object {
        /** Key used in [com.savesync.android.storage.Settings.saveDirOverrides]. */
        const val EMULATOR_KEY = "Dolphin"

        /** Title-id format produced by [discoverSaves]: ``GC_<lowercase 4-char code>``. */
        private val gcTitleIdRegex = Regex("""^GC_([A-Za-z0-9]{4})$""")

        /**
         * Pick a sensible default region folder from the GameCube product
         * code's region byte (the 4th character).  Mirrors Nintendo's official
         * region scheme; unknown codes default to USA so downloads still land
         * somewhere predictable.
         */
        private fun regionFromCode(code: String): String {
            return when (code.uppercase().getOrNull(3)) {
                'E' -> "USA"            // North America
                'P', 'X', 'Y', 'D', 'F', 'I', 'S', 'H', 'U' -> "EUR"  // PAL variants
                'J' -> "JAP"            // Japan
                'K' -> "JAP"            // Korea (no separate folder by convention)
                else -> "USA"
            }
        }

        /** Strip filesystem-unsafe chars so the predicted .gci filename stays writable. */
        private fun sanitizeGciDescription(label: String): String {
            return label
                .replace(Regex("""[\\/:*?"<>|]"""), "")
                .replace(Regex("""\s+"""), " ")
                .trim()
                .ifBlank { "save" }
        }

        /**
         * Locate the GC memory-card root for path prediction without needing a
         * full [DolphinEmulator] instance.  Tries (in order): explicit user
         * override, the Emudeck Dolphin/GC folder, then the common
         * fork-specific paths under external storage.  When no existing
         * folder is found and [allowNonExistent] is true, returns the most
         * likely future path (``dolphin-mmjr/GC``).
         */
        fun findGcBaseDir(
            dolphinMemCardDir: String = "",
            dolphinRootDir: File? = null,
            allowNonExistent: Boolean = false
        ): File? {
            if (dolphinMemCardDir.isNotBlank()) {
                val custom = File(dolphinMemCardDir)
                if (custom.exists() && custom.isDirectory) return custom
                if (allowNonExistent) return custom
            }
            dolphinRootDir?.let { root ->
                val gcDir = File(root, "GC")
                if (gcDir.exists() && gcDir.isDirectory) return gcDir
                if (root.exists() && root.isDirectory) return root
                if (allowNonExistent) return gcDir
            }
            val external = Environment.getExternalStorageDirectory()
            val candidates = listOf(
                "dolphin-mmjr/GC",
                "dolphin-emu/GC",
                "dolphin/GC",
                "Dolphin/GC",
                "Dolphin MMJR/GC",
            )
            candidates.map { File(external, it) }
                .firstOrNull { it.exists() && it.isDirectory }
                ?.let { return it }
            return if (allowNonExistent) File(external, candidates.first()) else null
        }

        /**
         * Predicted ``<gcRoot>/<REGION>/Card A/01-<CODE>-<displayName>.gci``
         * for a GameCube [titleId] handed back by the server.  Used by the
         * server-only fallback in MainViewModel so users can see (and
         * download) GC saves before they've ever opened the corresponding
         * game in Dolphin.
         *
         * Returns null when [titleId] doesn't match the ``GC_<4-char>``
         * format we produce in [discoverSaves].
         */
        fun defaultSaveFile(
            titleId: String,
            displayName: String,
            dolphinMemCardDir: String = "",
            dolphinRootDir: File? = null,
        ): File? {
            val code = gcTitleIdRegex.matchEntire(titleId)?.groupValues?.get(1)?.uppercase()
                ?: return null
            val gcBase = findGcBaseDir(dolphinMemCardDir, dolphinRootDir, allowNonExistent = true)
                ?: return null
            val region = regionFromCode(code)
            val description = sanitizeGciDescription(displayName)
            val fileName = "01-$code-$description.gci"
            return File(File(File(gcBase, region), "Card A"), fileName)
        }
    }

    /** Returns the GC memory card root, trying user override then common paths. */
    private fun findGcBaseDir(): File? {
        if (dolphinMemCardDir.isNotBlank()) {
            val custom = File(dolphinMemCardDir)
            if (custom.exists() && custom.isDirectory) return custom
        }
        dolphinRootDir?.let { root ->
            val gcDir = File(root, "GC")
            if (gcDir.exists() && gcDir.isDirectory) return gcDir
            if (root.exists() && root.isDirectory) return root
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
