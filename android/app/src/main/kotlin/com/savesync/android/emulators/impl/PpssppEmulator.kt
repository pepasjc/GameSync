package com.savesync.android.emulators.impl

import com.savesync.android.emulators.EmulatorBase
import com.savesync.android.emulators.SaveEntry
import java.io.File


class PpssppEmulator : EmulatorBase() {

    override val name: String = "PPSSPP"
    override val systemPrefix: String = "PPSSPP"

    // PSP product code: 4 uppercase letters + 5 digits (e.g. ULUS10272)
    private val productCodeRegex = Regex("^[A-Z]{4}[0-9]{5}")

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
    private fun makeEntry(slotDir: File, code: String): SaveEntry =
        SaveEntry(
            // Full directory name as titleId so it matches whatever the server stored.
            titleId = slotDir.name,
            // Product code as placeholder; game-name lookup in ViewModel will enrich this.
            displayName = code,
            systemName = systemPrefix,
            saveFile = null,       // no single target file — all slot files are synced
            saveDir = slotDir,     // slot dir — used for hash, upload, download, mkdirs
            isMultiFile = false,   // PSP path in SyncEngine/SaveEntry handles this
        )

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
