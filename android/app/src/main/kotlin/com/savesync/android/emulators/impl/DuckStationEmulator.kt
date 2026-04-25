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
    private val romScanDir: String = "",
    /**
     * Optional explicit memcards folder override, configured in the Emulator
     * Configuration screen.  Wins over the hardcoded
     * ``Android/data/com.github.stenzek.duckstation/files/memcards`` path
     * when set — useful for users who route DuckStation saves to an SD card.
     */
    private val saveDirOverride: String? = null
) : EmulatorBase() {
    companion object {
        /** Key used in [com.savesync.android.storage.Settings.saveDirOverrides]. */
        const val EMULATOR_KEY = "DuckStation"

        fun findMemcardsDir(baseDir: File, allowNonExistent: Boolean = false): File? {
            val primary = File(baseDir, "Android/data/com.github.stenzek.duckstation/files/memcards")
            return if (primary.exists() && primary.isDirectory) {
                primary
            } else if (allowNonExistent) {
                primary
            } else {
                null
            }
        }
    }

    override val name: String = "DuckStation"
    override val systemPrefix: String = "PS1"

    private val mcdExtensions = setOf("mcd", "mcr")
    private val romExtensions = setOf("iso", "bin", "cue", "img", "mdf", "chd", "pbp")

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
     * Serial-backed entries are stronger than slug-backed ones because they match the
     * server title ID directly. This matters for BIN/CUE folders where the BIN may be
     * seen first and fall back to a slug, but the paired CUE can still resolve the
     * correct product code a moment later.
     */
    private fun isSerialTitleId(titleId: String): Boolean = serialRegex.matches(titleId)

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
        if (!saveDirOverride.isNullOrBlank()) {
            val overrideDir = File(saveDirOverride)
            if (overrideDir.exists() && overrideDir.isDirectory) return overrideDir
            if (allowNonExistent) return overrideDir
        }
        return findMemcardsDir(baseDir, allowNonExistent)
    }

    private fun ps1RomDirs(scanRoot: File): List<File> {
        return listOf(
            "PS1", "ps1", "PSX", "psx", "PlayStation", "playstation",
            "PlayStation 1", "PlayStation1"
        ).map { File(scanRoot, it) }.filter { it.exists() && it.isDirectory }
    }

    /**
     * Keeps the generated DuckStation card name close to what the emulator already uses.
     *
     * If the ROM lives inside a dedicated game folder that contains one or more disc
     * images, we use that leaf folder name. Otherwise we fall back to the ROM filename
     * itself so category folders like `Racing/` do not become bogus card names.
     */
    private fun romLabelFor(systemDir: File, romFile: File): String {
        val parent = romFile.parentFile ?: return romFile.nameWithoutExtension
        if (parent == systemDir) return romFile.nameWithoutExtension

        val imageCount = parent.listFiles()
            ?.count { it.isFile && it.extension.lowercase() in romExtensions }
            ?: 0

        return if (imageCount >= 1) parent.name else romFile.nameWithoutExtension
    }

    /**
     * Clean local ROM/folder labels by stripping dump junk and disc markers, but do not
     * invent region strings here. Server-only downloads should prefer the server title
     * for their final filename; this helper only prevents obvious `[U] [SLUS-12345]`
     * noise from polluting local ROM anchors.
     */
    private fun normalizeDuckStationCardLabel(label: String): String {
        return label
            .replace(
                Regex("""\s*[\(\[]\s*(disc|cd)\s*[0-9]+(?:\s*of\s*[0-9]+)?\s*[\)\]]""", RegexOption.IGNORE_CASE),
                ""
            )
            .replace(
                Regex("""\s*[\(\[][A-Z]{4}[-_ ]?\d{5}.*?[\)\]]""", RegexOption.IGNORE_CASE),
                ""
            )
            .replace(
                Regex("""\s*[\(\[]\s*(U|E|J|USA|EUROPE|JAPAN)\s*[\)\]]""", RegexOption.IGNORE_CASE),
                ""
            )
            .replace(Regex("""\s+"""), " ")
            .trim()
            .ifBlank { label }
    }

    private fun buildRomEntry(memcardsDir: File, label: String, romFile: File): Pair<String, SaveEntry>? {
        val titleId = readPs1Serial(romFile)
            ?: normalizeSerial(romFile.nameWithoutExtension)
            ?: normalizeSerial(label)
            ?: toPs1TitleId(label)
        val cardLabel = normalizeDuckStationCardLabel(label)
        val saveFile = File(memcardsDir, "${cardLabel}_1.mcd")
        return titleId to SaveEntry(
            titleId = titleId,
            displayName = cardLabel,
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
            systemDir.walkTopDown()
                .filter { it.isFile && it.extension.lowercase() in romExtensions }
                .forEach { romFile ->
                    // CHD/PBP cannot currently provide a serial through our lightweight parser,
                    // but they still need to participate in ROM anchoring so server-only saves
                    // can land in DuckStation's expected per-game filename.
                    val label = romLabelFor(systemDir, romFile)
                    val romEntry = buildRomEntry(memcardsDir, label, romFile) ?: return@forEach
                    val (titleId, saveEntry) = romEntry

                    // If a later pass finds the real serial for a title we previously saw
                    // only as a slug, replace the weaker slug mapping with the serial one.
                    if (isSerialTitleId(titleId)) {
                        result.entries
                            .firstOrNull { (_, existing) ->
                                existing.systemName == systemPrefix &&
                                !isSerialTitleId(existing.titleId) &&
                                existing.displayName.equals(saveEntry.displayName, ignoreCase = true)
                            }
                            ?.let { stale -> result.remove(stale.key) }
                    }

                    result.putIfAbsent(titleId, saveEntry)
                }
        }
        return result
    }
}
