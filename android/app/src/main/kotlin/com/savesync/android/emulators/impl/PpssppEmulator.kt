package com.savesync.android.emulators.impl

import com.savesync.android.emulators.EmulatorBase
import com.savesync.android.emulators.SaveEntry
import java.io.File


class PpssppEmulator(
    private val romScanDir: String = "",
    private val romDirOverrides: Map<String, String> = emptyMap()
) : EmulatorBase() {

    override val name: String = "PPSSPP"
    override val systemPrefix: String = "PSP"

    companion object {
        fun findSaveDataDir(baseDir: File, allowNonExistent: Boolean = false): File? {
            val candidates = listOf("PSP/SAVEDATA", "psp/SAVEDATA", "PSP/savedata")
            val existing = candidates
                .map { File(baseDir, it) }
                .firstOrNull { it.exists() && it.isDirectory }
            if (existing != null) return existing
            return if (allowNonExistent) File(baseDir, "PSP/SAVEDATA") else null
        }

        /**
         * Predicted SAVEDATA slot directory for a PSP title_id.  The server hands
         * back the full slot name (e.g. "ULUS10567DATA"), so we use it verbatim
         * as the directory name — matches what the PPSSPP scanner yields once a
         * save actually exists on disk.
         */
        fun defaultSlotDir(baseDir: File, titleId: String): File? {
            val root = findSaveDataDir(baseDir, allowNonExistent = true) ?: return null
            return File(root, titleId)
        }
    }

    // PSP product code: 4 uppercase letters + 5 digits (e.g. ULUS10272)
    private val productCodeRegex = Regex("^[A-Z]{4}[0-9]{5}")
    private val productCodeSearchRegex = Regex("([A-Z]{4}[0-9]{5})", RegexOption.IGNORE_CASE)
    private val romExtensions = setOf("iso", "cso", "pbp", "chd")

    // Full slot directory name must be alphanumeric only (matches server's _PRODUCT_CODE_RE)
    // e.g. "ULJS000800000" OK, "SLES00001-SAVE0" or "ULJS_DATA00" would be rejected
    private val validSlotDirRegex = Regex("^[A-Za-z0-9]{4,31}$")

    /**
     * PS1 retail disc product code prefixes.  These appear in PSP/PPSSPP SAVEDATA when
     * a PSone Classic was downloaded from PSN and is running under PSP emulation.
     * The 4-letter prefix uniquely identifies PS1 retail/PSN discs vs native PSP games.
     */
    private val psxRetailPrefixes = setOf(
        // North America
        "SLUS", "SCUS", "PAPX",
        // Europe
        "SLES", "SCES", "SCED",
        // Japan
        "SLPS", "SLPM", "SCPS", "SCPM",
        // Other regions
        "SLAJ", "SLEJ", "SCAJ"
    )

    private fun detectSystem(productCode: String): String {
        val upper = productCode.uppercase()
        return when {
            upper.length >= 4 && upper.take(4) in psxRetailPrefixes -> "PS1"
            // PSN PSone Classic codes start with "NP" (e.g. NPUJ00662, NPUF00001).
            // Classify as PS1 so they get the PSN→retail serial remapping in MainViewModel.
            upper.length >= 2 && upper.take(2) == "NP" -> "PS1"
            else -> "PSP"
        }
    }

    private fun findSaveDataDir(allowNonExistent: Boolean = false): File? {
        val primary = File(baseDir, "PSP/SAVEDATA")
        val existing = firstExisting("PSP/SAVEDATA", "psp/SAVEDATA", "PSP/savedata")
        return when {
            existing != null -> existing
            allowNonExistent -> primary
            else -> null
        }
    }

    private fun pspRomDirs(): List<File> {
        val dirs = mutableListOf<File>()

        listOf(
            "PSP/ISO",
            "psp/ISO",
            "PSP/iso",
            "psp/iso",
            "ISO",
            "iso",
            "PSP/GAME",
            "psp/GAME",
            "PSP/game",
            "psp/game"
        ).forEach { rel ->
            val dir = File(baseDir, rel)
            if (dir.exists() && dir.isDirectory) dirs.add(dir)
        }

        if (romScanDir.isNotBlank()) {
            val scanRoot = File(romScanDir)
            listOf(
                "PSP",
                "psp",
                "PlayStation Portable",
                "PlayStationPortable",
                "Sony - PlayStation Portable"
            ).forEach { sub ->
                val dir = File(scanRoot, sub)
                if (dir.exists() && dir.isDirectory) dirs.add(dir)
            }
        }

        // User-specified override for PSP ROMs
        romDirOverrides["PSP"]?.let { overridePath ->
            val dir = File(overridePath)
            if (dir.exists() && dir.isDirectory) dirs.add(dir)
        }

        return dirs.distinctBy { it.absolutePath }
    }

    /** Extracts the 9-char product code from a PSP slot directory name. e.g. "ULJS000800000" → "ULJS00080". */
    private fun productCode(dirName: String): String? =
        productCodeRegex.find(dirName)?.value

    private fun productCodeFromText(text: String): String? =
        productCodeSearchRegex.find(text)?.value?.uppercase()

    private fun romLabelFor(romRoot: File, romFile: File): String {
        val stem = romFile.nameWithoutExtension
        return if (romFile.extension.equals("pbp", ignoreCase = true) &&
            stem.equals("EBOOT", ignoreCase = true) &&
            romFile.parentFile != null &&
            romFile.parentFile != romRoot
        ) {
            romFile.parentFile!!.name
        } else {
            stem
        }
    }

    private fun productCodeFromRom(romRoot: File, romFile: File): String? {
        val candidates = mutableListOf(romFile.nameWithoutExtension, romFile.name)
        if (romFile.extension.equals("pbp", ignoreCase = true) && romFile.parentFile != null) {
            candidates.add(romFile.parentFile!!.name)
        }
        if (romFile.parentFile != null && romFile.parentFile != romRoot) {
            candidates.add(romFile.parentFile!!.name)
        }

        return candidates.firstNotNullOfOrNull { productCodeFromText(it) }
    }

    private fun fallbackSlotName(label: String): String {
        val sanitized = label
            .replace(Regex("[^A-Za-z0-9]+"), "")
            .take(31)
        return sanitized.ifBlank { "PSPSAVE" }
    }

    /**
     * Creates a SaveEntry for a PSP slot directory.
     *
     * The entry uses [saveDir] = slot directory and [saveFile] = null, delegating all
     * hash/upload/download logic to the PSP bundle path in SyncEngine (triggered by
     * [SaveEntry.isPspSlot]):
     *
     *  • Hash  = sha256(concat(all files sorted by name)) — no paths, just data.
     *            Matches the server's bundle hash and the PSP homebrew client.
     *  • Upload = bundle v4 with all slot files via POST /api/v1/saves/{title_id}.
     *  • Download = parse bundle v4, extract all files to slot directory.
     *
     * [isMultiFile] is set to **false** to avoid the generic zip-directory path;
     * PSP-specific overrides in [SaveEntry] and [SyncEngine] handle the slot dir.
     *
     * @param slotDir  The PSP SAVEDATA slot directory (e.g. PSP/SAVEDATA/ULJS000800000)
     * @param code     9-char product code used as placeholder displayName
     */
    private fun makeEntry(slotDir: File, code: String): SaveEntry {
        val system = detectSystem(code)
        return SaveEntry(
            // PSone Classics ALWAYS use the bare 9-char PS1 serial as the title ID
            // so they match saves synced from real PSP/Vita hardware and DuckStation.
            // e.g. SLUS00975DATA00 → titleId = "SLUS00975", not "SLUS00975DATA00".
            //
            // PSP games use the full slot-dir name when it is server-safe (alphanumeric)
            // to distinguish multiple save slots (DATA00, DATA01, …) per product code.
            titleId = if (system == "PS1") code
                      else if (validSlotDirRegex.matches(slotDir.name)) slotDir.name
                      else code,
            // Product code as placeholder; game-name lookup in ViewModel will enrich this.
            displayName = code,
            systemName = system,
            saveFile = null,       // no single target file — all slot files are synced
            saveDir = slotDir,     // slot dir — used for hash, upload, download, mkdirs
            isMultiFile = false,   // isPspSlot=true drives the PSP bundle path
        )
    }

    private fun makeRomEntry(saveDataDir: File, romRoot: File, romFile: File): Pair<String, SaveEntry> {
        val label = romLabelFor(romRoot, romFile)
        val code = productCodeFromRom(romRoot, romFile)
        val system = code?.let(::detectSystem) ?: "PSP"
        val slotName = code ?: fallbackSlotName(label)
        val titleId = code ?: when (system) {
            "PS1" -> toPs1TitleId(label)
            else -> toTitleId(label, "PSP")
        }

        return titleId to SaveEntry(
            titleId = titleId,
            displayName = label,
            systemName = system,
            saveFile = null,
            saveDir = File(saveDataDir, slotName),
            isMultiFile = false,
        )
    }

    override fun discoverSaves(): List<SaveEntry> {
        val saveDataDir = findSaveDataDir() ?: return emptyList()
        val result = mutableListOf<SaveEntry>()

        saveDataDir.listFiles()?.forEach { slotDir ->
            if (!slotDir.isDirectory) return@forEach
            val code = productCode(slotDir.name) ?: return@forEach
            result.add(makeEntry(slotDir, code))
        }

        return result
    }

    /**
     * Returns existing PPSSPP slot directories plus ROM-derived placeholder entries.
     *
     * ROM-derived entries let the Android client anchor server-only PSP saves before a
     * local save exists yet. They point at the predicted PPSSPP save-slot directory and
     * include `.iso`, `.cso`, `.pbp`, and `.chd` ROMs.
     */
    override fun discoverRomEntries(): Map<String, SaveEntry> {
        val saveDataDir = findSaveDataDir(allowNonExistent = true) ?: return emptyMap()
        val result = mutableMapOf<String, SaveEntry>()

        saveDataDir.listFiles()?.forEach { slotDir ->
            if (!slotDir.isDirectory) return@forEach
            val code = productCode(slotDir.name) ?: return@forEach
            result[slotDir.name] = makeEntry(slotDir, code)
        }

        pspRomDirs().forEach { romRoot ->
            romRoot.walkTopDown()
                .filter { it.isFile && it.extension.lowercase() in romExtensions }
                .forEach { romFile ->
                    val (titleId, entry) = makeRomEntry(saveDataDir, romRoot, romFile)
                    result.putIfAbsent(titleId, entry)
                }
        }

        return result
    }
}
