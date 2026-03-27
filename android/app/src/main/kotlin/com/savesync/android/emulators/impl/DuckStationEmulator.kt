package com.savesync.android.emulators.impl

import com.savesync.android.emulators.EmulatorBase
import com.savesync.android.emulators.SaveEntry
import java.io.File

/**
 * DuckStation PS1 emulator — handles per-game memory card files (.mcd / .mcr).
 *
 * In practice DuckStation Android often names cards after the game title, e.g.
 * `Breath of Fire IV (USA)_1.mcd`, not after the disc serial. We still normalize
 * the title ID to the PS1 product code when we can, but the on-disk filename must
 * match DuckStation's title-based convention so server-only downloads land in the
 * place the emulator actually reads.
 */
class DuckStationEmulator(
    private val romScanDir: String = ""
) : EmulatorBase() {

    override val name: String = "DuckStation"
    override val systemPrefix: String = "PS1"

    private val mcdExtensions = setOf("mcd", "mcr")

    // Shared / global memory card names to skip
    private val sharedCardNames = setOf(
        "shared_card_1", "shared_card_2", "shared_card_3", "shared_card_4",
        "mcd001", "mcd002", "epsxe000", "epsxe001"
    )

    // Slot suffix pattern: "_1" or "_2" immediately before the extension
    private val slotSuffixRegex = Regex("_\\d+$")

    // PS1 product code: 4 uppercase letters + 5+ digits (e.g. SLUS01234)
    private val serialRegex = Regex("^[A-Z]{4}\\d{5,}$")

    /**
     * Normalizes a memory-card filename stem to a bare PS1 product code.
     * e.g. "SLUS-01234" → "SLUS01234", "SCUS_94163" → "SCUS94163"
     * Returns null if the result doesn't match the expected format.
     */
    private fun normalizeSerial(stem: String): String? {
        val code = stem.uppercase().replace(Regex("[^A-Z0-9]"), "")
        return if (serialRegex.matches(code)) code else null
    }

    private fun findMemcardsDir(allowNonExistent: Boolean = false): File? {
        val candidates = listOf(
            File(baseDir, "Android/data/com.github.stenzek.duckstation/files/memcards"),
            File(baseDir, "Android/data/org.duckstation.duckstation/files/memcards"),
            File(baseDir, "DuckStation/memcards"),
            File(baseDir, "duckstation/memcards"),
        )
        return candidates.firstOrNull { it.exists() && it.isDirectory }
            ?: if (allowNonExistent) candidates.firstOrNull() else null
    }

    private fun ps1RomDirs(scanRoot: File): List<File> {
        return listOf(
            "PS1", "ps1", "PSX", "psx", "PlayStation", "playstation",
            "PlayStation 1", "PlayStation1"
        ).map { File(scanRoot, it) }.filter { it.exists() && it.isDirectory }
    }

    private fun buildRomEntry(memcardsDir: File, label: String, romFile: File): Pair<String, SaveEntry>? {
        val titleId = readPs1Serial(romFile) ?: toPs1TitleId(label)
        val saveFile = File(memcardsDir, "${label}_1.mcd")
        return titleId to SaveEntry(
            titleId = titleId,
            displayName = label,
            systemName = systemPrefix,
            saveFile = saveFile,
            saveDir = null
        )
    }

    override fun discoverSaves(): List<SaveEntry> {
        val memcardsDir = findMemcardsDir() ?: return emptyList()
        val best = mutableMapOf<String, File>()
        val displayNames = mutableMapOf<String, String>()

        memcardsDir.listFiles()?.forEach { file ->
            if (!file.isFile || file.extension.lowercase() !in mcdExtensions) return@forEach

            // Strip slot suffix then check against known shared card names
            val stemNoSlot = slotSuffixRegex.replace(file.nameWithoutExtension, "")
            if (stemNoSlot.lowercase() in sharedCardNames) return@forEach

            val titleId = normalizeSerial(stemNoSlot) ?: toPs1TitleId(stemNoSlot)
            val current = best[titleId]
            val shouldReplace = when {
                current == null -> true
                current.nameWithoutExtension.endsWith("_2") && !file.nameWithoutExtension.endsWith("_2") -> true
                current.nameWithoutExtension.endsWith("_1") && file.nameWithoutExtension.endsWith("_2") -> false
                else -> file.lastModified() > current.lastModified()
            }
            if (shouldReplace) best[titleId] = file
            displayNames[titleId] = stemNoSlot
        }

        return best.map { (titleId, primary) ->
            SaveEntry(
                titleId = titleId,
                displayName = displayNames[titleId] ?: primary.nameWithoutExtension,
                systemName = systemPrefix,
                saveFile = primary,
                saveDir = null
            )
        }
    }

    override fun discoverRomEntries(): Map<String, SaveEntry> {
        val memcardsDir = findMemcardsDir(allowNonExistent = true) ?: return emptyMap()
        if (romScanDir.isBlank()) return emptyMap()

        val scanRoot = File(romScanDir)
        if (!scanRoot.exists() || !scanRoot.isDirectory) return emptyMap()

        val result = mutableMapOf<String, SaveEntry>()
        ps1RomDirs(scanRoot).forEach { systemDir ->
            systemDir.listFiles()?.forEach { entry ->
                when {
                    entry.isFile && entry.extension.lowercase() in setOf("iso", "bin", "cue", "img", "mdf") -> {
                        val romEntry = buildRomEntry(memcardsDir, entry.nameWithoutExtension, entry) ?: return@forEach
                        result.putIfAbsent(romEntry.first, romEntry.second)
                    }
                    entry.isDirectory -> {
                        val disc = entry.listFiles()
                            ?.firstOrNull { it.isFile && it.extension.lowercase() in setOf("iso", "bin", "cue", "img", "mdf") }
                            ?: return@forEach
                        val romEntry = buildRomEntry(memcardsDir, entry.name, disc) ?: return@forEach
                        result.putIfAbsent(romEntry.first, romEntry.second)
                    }
                }
            }
        }
        return result
    }
}
