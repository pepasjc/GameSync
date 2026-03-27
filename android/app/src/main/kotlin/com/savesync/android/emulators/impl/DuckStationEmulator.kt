package com.savesync.android.emulators.impl

import com.savesync.android.emulators.EmulatorBase
import com.savesync.android.emulators.SaveEntry
import java.io.File

/**
 * DuckStation PS1 emulator — handles per-game memory card files (.mcd / .mcr).
 *
 * DuckStation saves one memory card per game using the game's serial number as the
 * filename, e.g. `SLUS-01234.mcd`.  The serial is normalized to a bare product code
 * for the title ID: `SLUS01234` — matching PSone Classics on PSP/Vita.
 *
 * Candidate directories (checked in order):
 *   - DuckStation/memcards/         (standard external storage layout)
 *   - duckstation/memcards/
 *   - Android/data/com.github.stenzek.duckstation/files/memcards/
 *   - Android/data/org.duckstation.duckstation/files/memcards/
 *
 * Shared memory cards (`shared_card_1.mcd`, `shared_card_2.mcd`, `Mcd001.mcd`) are
 * skipped — they contain saves for multiple games and can't be synced per-title.
 *
 * Memory card slot suffixes (`_1`, `_2` before the extension) are stripped so that
 * `SLUS-01234_1.mcd` and `SLUS-01234_2.mcd` collapse to the same title ID.  When
 * multiple slot files exist for the same serial, the most recently modified one is used.
 */
class DuckStationEmulator : EmulatorBase() {

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

    private fun findMemcardsDir(): File? = firstExistingAbsolute(
        File(baseDir, "DuckStation/memcards"),
        File(baseDir, "duckstation/memcards"),
        File(baseDir, "Android/data/com.github.stenzek.duckstation/files/memcards"),
        File(baseDir, "Android/data/org.duckstation.duckstation/files/memcards"),
    )

    override fun discoverSaves(): List<SaveEntry> {
        val memcardsDir = findMemcardsDir() ?: return emptyList()

        // Collect best (most recently modified) mcd per slug, to handle slot variants
        val best = mutableMapOf<String, SaveEntry>()

        memcardsDir.listFiles()?.forEach { file ->
            if (!file.isFile || file.extension.lowercase() !in mcdExtensions) return@forEach

            // Strip slot suffix then check against known shared card names
            val stemNoSlot = slotSuffixRegex.replace(file.nameWithoutExtension, "")
            if (stemNoSlot.lowercase() in sharedCardNames) return@forEach

            val titleId = normalizeSerial(stemNoSlot) ?: toPs1TitleId(stemNoSlot)
            val existing = best[titleId]
            if (existing == null || file.lastModified() > (existing.saveFile?.lastModified() ?: 0L)) {
                best[titleId] = SaveEntry(
                    titleId = titleId,
                    displayName = stemNoSlot,
                    systemName = systemPrefix,
                    saveFile = file,
                    saveDir = null
                )
            }
        }

        return best.values.toList()
    }
}
