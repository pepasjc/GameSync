package com.savesync.android.emulators.impl

import com.savesync.android.emulators.EmulatorBase
import com.savesync.android.emulators.SaveEntry
import java.io.File


class PpssppEmulator : EmulatorBase() {

    override val name: String = "PPSSPP"
    override val systemPrefix: String = "PPSSPP"

    // PSP product code: 4 uppercase letters + 5 digits (e.g. ULUS10272)
    private val productCodeRegex = Regex("^[A-Z]{4}[0-9]{5}")

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

    private fun detectSystem(productCode: String): String =
        if (productCode.length >= 4 && productCode.take(4).uppercase() in psxRetailPrefixes) "PS1"
        else "PPSSPP"

    private fun findSaveDataDir(): File? =
        firstExisting("PSP/SAVEDATA", "psp/SAVEDATA", "PSP/savedata")

    /** Extracts the 9-char product code from a PSP slot directory name. e.g. "ULJS000800000" → "ULJS00080". */
    private fun productCode(dirName: String): String? =
        productCodeRegex.find(dirName)?.value

    /**
     * Creates a SaveEntry for a PSP slot directory.
     *
     * The entry uses [saveDir] = slot directory and [saveFile] = null, delegating all
     * hash/upload/download logic to the PSP bundle path in SyncEngine (triggered by
     * [SaveEntry.systemName] == "PPSSPP"):
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
     * Returns one entry per existing PSP slot directory.
     * Used so that server-only PSP saves can be matched and downloaded to the correct path.
     * Uses DATA.BIN as the default download target when no save data file exists yet.
     */
    override fun discoverRomEntries(): Map<String, SaveEntry> {
        val saveDataDir = findSaveDataDir() ?: return emptyMap()
        val result = mutableMapOf<String, SaveEntry>()

        saveDataDir.listFiles()?.forEach { slotDir ->
            if (!slotDir.isDirectory) return@forEach
            val code = productCode(slotDir.name) ?: return@forEach
            result[slotDir.name] = makeEntry(slotDir, code)
        }

        return result
    }
}
