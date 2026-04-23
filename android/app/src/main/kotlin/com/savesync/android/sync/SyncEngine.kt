package com.savesync.android.sync

import android.util.Log
import com.savesync.android.api.NormalizeRequest
import com.savesync.android.api.NormalizeRomEntry
import com.savesync.android.api.SaveSyncApi
import com.savesync.android.api.SaturnArchiveLookupRequest
import com.savesync.android.api.SaturnArchiveLookupResult
import com.savesync.android.api.SyncRequest
import com.savesync.android.api.SyncTitle
import com.savesync.android.emulators.SaveEntry
import com.savesync.android.installed.InstalledRomsScanner
import com.savesync.android.storage.AppDatabase
import com.savesync.android.storage.SyncStateEntity
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.RequestBody.Companion.toRequestBody
import java.io.File
import java.io.IOException

private const val TAG = "SyncEngine"

private data class SaturnCanonicalSnapshot(
    val bytes: ByteArray,
    val archiveNames: List<String>
)

data class SyncResult(
    val uploaded: Int,
    val downloaded: Int,
    val conflicts: List<String>,
    val errors: List<String>
)

class SyncEngine(
    private val api: SaveSyncApi,
    private val db: AppDatabase,
    private val consoleId: String,
    private val saturnSyncFormat: SaturnSyncFormat = SaturnSyncFormat.MEDNAFEN
) {
    private val dao = db.syncStateDao()

    private suspend fun autoResolveSharedSaturnArchives(entry: SaveEntry): List<String> {
        if (!isSharedYabaSanshiroContainer(entry)) return emptyList()

        val stored = SaturnArchiveStateStore.get(entry.titleId)
        if (stored.isNotEmpty()) return stored

        val saveFile = entry.saveFile ?: return emptyList()
        if (!saveFile.exists()) return emptyList()

        val archiveNames = try {
            SaturnSaveFormatConverter.archiveNames(saveFile.readBytes())
        } catch (e: Exception) {
            Log.w(TAG, "Could not parse YabaSanshiro backup for ${entry.titleId}", e)
            return emptyList()
        }
        if (archiveNames.isEmpty()) return emptyList()

        val lookupResults = try {
            api.lookupSaturnArchives(
                SaturnArchiveLookupRequest(
                    title_id = entry.titleId,
                    archive_names = archiveNames
                )
            ).results
        } catch (e: Exception) {
            Log.w(TAG, "Saturn archive lookup failed for ${entry.titleId}", e)
            return emptyList()
        }

        val selected = lookupResults
            .filter { it.matches_current_title || it.status == "exact_current" || it.status == "includes_current" }
            .flatMap { it.archive_names }
            .map { it.trim() }
            .filter { it.isNotEmpty() }
            .distinct()

        if (selected.isNotEmpty()) {
            SaturnArchiveStateStore.put(entry.titleId, selected)
        }
        return selected
    }

    suspend fun sync(saves: List<SaveEntry>): SyncResult {
        val resolvedSaves = normalizeCanonicalSlugEntries(saves)
        val errors = mutableListOf<String>()
        var uploaded = 0
        var downloaded = 0
        val conflicts = mutableListOf<String>()

        // Build a map for quick lookup
        val saveMap = resolvedSaves.associateBy { it.titleId }

        val (rawCardEntries, otherEntries) = resolvedSaves.partition {
            isPs1RawEntry(it) || isPs2RawEntry(it) || isGcRawEntry(it)
        }
        for (entry in rawCardEntries) {
            try {
                when (syncRawCardEntry(entry)) {
                    RawCardSyncOutcome.UPLOADED -> uploaded++
                    RawCardSyncOutcome.DOWNLOADED -> downloaded++
                    RawCardSyncOutcome.CONFLICT -> conflicts.add(entry.displayName)
                    RawCardSyncOutcome.UP_TO_DATE, RawCardSyncOutcome.SKIPPED -> Unit
                }
            } catch (e: Exception) {
                Log.e(TAG, "Raw-card sync failed for ${entry.titleId}", e)
                errors.add("Sync error: ${entry.displayName}: ${e.message}")
            }
        }

        resolvedSaves.forEach { entry ->
            if (isSharedYabaSanshiroContainer(entry)) {
                autoResolveSharedSaturnArchives(entry)
            }
        }

        val syncStateByTitle = resolvedSaves.associate { entry ->
            entry.titleId to dao.getById(entry.titleId)
        }

        // Server-only entries normally have no local file yet, but shared Saturn
        // containers like YabaSanshiro can still have a usable local sync source.
        val (serverOnlyEntries, localEntries) = otherEntries.partition { entry ->
            entry.isServerOnly && !hasLocalSyncSource(entry, syncStateByTitle[entry.titleId])
        }
        for (entry in serverOnlyEntries) {
            if (!hasLocalSaveTarget(entry)) {
                Log.i(TAG, "Skipping server-only entry without local target: ${entry.titleId}")
                continue
            }
            try {
                val success = downloadSave(entry, entry.titleId)
                if (success) {
                    downloaded++
                    // Record sync state using the freshly-written file, not the entry
                    // (which still has isServerOnly=true and would return hash="")
                    updateSyncStateFromFile(entry)
                } else {
                    errors.add("Download failed: ${entry.displayName}")
                }
            } catch (e: Exception) {
                Log.e(TAG, "Server-only download failed for ${entry.titleId}", e)
                errors.add("Download error: ${entry.displayName}: ${e.message}")
            }
        }

        // Build sync request titles for entries that have a local save file
        val syncTitles = localEntries.mapNotNull { entry ->
            val lastSyncState = syncStateByTitle[entry.titleId]
            if (!hasLocalSyncSource(entry, lastSyncState)) return@mapNotNull null
            try {
                val hash = canonicalHash(entry, lastSyncState)
                val timestamp = localTimestamp(entry) / 1000L  // convert ms to seconds
                val size = canonicalSize(entry, lastSyncState)
                SyncTitle(
                    title_id = entry.titleId,
                    save_hash = hash,
                    timestamp = timestamp,
                    size = size,
                    last_synced_hash = lastSyncState?.lastSyncedHash,
                    console_id = consoleId
                )
            } catch (e: Exception) {
                Log.e(TAG, "Error computing hash for ${entry.titleId}", e)
                errors.add("Hash error: ${entry.displayName}")
                null
            }
        }

        if (syncTitles.isEmpty()) {
            return SyncResult(0, 0, emptyList(), errors)
        }

        val syncResponse = try {
            api.sync(SyncRequest(titles = syncTitles, console_id = consoleId))
        } catch (e: Exception) {
            Log.e(TAG, "Sync API call failed", e)
            return SyncResult(0, 0, emptyList(), listOf("Sync failed: ${e.message}"))
        }

        // Process uploads
        for (titleId in syncResponse.upload) {
            val entry = saveMap[titleId] ?: continue
            try {
                val success = uploadSave(entry)
                if (success) {
                    uploaded++
                    updateSyncState(entry)
                } else {
                    errors.add("Upload failed: ${entry.displayName}")
                }
            } catch (e: Exception) {
                Log.e(TAG, "Upload failed for $titleId", e)
                errors.add("Upload error: ${entry.displayName}: ${e.message}")
            }
        }

        // Process downloads
        for (titleId in syncResponse.download) {
            val entry = saveMap[titleId] ?: continue
            try {
                val success = downloadSave(entry, titleId)
                if (success) {
                    downloaded++
                    updateSyncState(entry)
                } else {
                    errors.add("Download failed: ${entry.displayName}")
                }
            } catch (e: Exception) {
                Log.e(TAG, "Download failed for $titleId", e)
                errors.add("Download error: ${entry.displayName}: ${e.message}")
            }
        }

        // Process server_only (download saves that only exist on server)
        for (titleId in syncResponse.server_only) {
            val entry = saveMap[titleId]
            if (entry != null) {
                try {
                    val success = downloadSave(entry, titleId)
                    if (success) {
                        downloaded++
                        updateSyncState(entry)
                    }
                } catch (e: Exception) {
                    Log.e(TAG, "Server-only download failed for $titleId", e)
                    errors.add("Download error: $titleId: ${e.message}")
                }
            }
        }

        // Record conflicts
        for (titleId in syncResponse.conflict) {
            val entry = saveMap[titleId]
            conflicts.add(entry?.displayName ?: titleId)
        }

        return SyncResult(
            uploaded = uploaded,
            downloaded = downloaded,
            conflicts = conflicts,
            errors = errors
        )
    }

    private suspend fun normalizeCanonicalSlugEntries(saves: List<SaveEntry>): List<SaveEntry> {
        val canonicalSlugSaves = saves.filter {
            it.titleId.contains('_') && (it.systemName == "PS1" || it.systemName == "3DS")
        }
        if (canonicalSlugSaves.isEmpty()) return saves

        return try {
            val response = api.normalizeRoms(
                NormalizeRequest(
                    roms = canonicalSlugSaves.map {
                        NormalizeRomEntry(system = it.systemName, filename = it.displayName)
                    }
                )
            )
            val serialMap = canonicalSlugSaves.indices.associate { i ->
                val oldId = canonicalSlugSaves[i].titleId
                val newId = response.results.getOrNull(i)?.title_id ?: oldId
                oldId to newId
            }.filter { (old, new) -> old != new && !new.contains('_') }

            if (serialMap.isEmpty()) saves
            else saves.map { entry ->
                val resolved = serialMap[entry.titleId]
                if (resolved != null) entry.copy(titleId = resolved) else entry
            }
        } catch (e: Exception) {
            Log.w(TAG, "normalizeCanonicalSlugEntries failed", e)
            saves
        }
    }

    private fun isSaturnEntry(entry: SaveEntry): Boolean {
        return entry.systemName == "SAT" && entry.saveFile != null && !entry.isMultiFile && !entry.isPspSlot
    }

    private fun isSharedYabaSanshiroContainer(entry: SaveEntry): Boolean {
        return isSaturnEntry(entry) &&
            saturnSyncFormat == SaturnSyncFormat.YABASANSHIRO &&
            entry.saveFile?.name.equals("backup.bin", ignoreCase = true)
    }

    private fun saturnCanonicalSnapshot(
        entry: SaveEntry,
        @Suppress("UNUSED_PARAMETER") syncState: SyncStateEntity? = null
    ): SaturnCanonicalSnapshot? {
        if (!isSaturnEntry(entry)) return null

        val saveFile = entry.saveFile ?: return null
        val canonicalBytes = if (isSharedYabaSanshiroContainer(entry)) {
            val archiveNames = SaturnArchiveStateStore.get(entry.titleId)
            if (archiveNames.isEmpty()) {
                throw UnsupportedOperationException(
                    "This YabaSanshiro save still needs Saturn archive selection metadata."
                )
            }
            SaturnSaveFormatConverter.extractCanonical(saveFile.readBytes(), archiveNames)
        } else {
            SaturnSaveFormatConverter.toCanonical(saveFile.readBytes())
        }

        return SaturnCanonicalSnapshot(
            bytes = canonicalBytes,
            archiveNames = SaturnSaveFormatConverter.archiveNames(canonicalBytes)
        )
    }

    private fun hasLocalSyncSource(
        entry: SaveEntry,
        @Suppress("UNUSED_PARAMETER") syncState: SyncStateEntity? = null
    ): Boolean {
        if (entry.exists()) return true
        return isSharedYabaSanshiroContainer(entry) &&
            entry.saveFile?.exists() == true &&
            SaturnArchiveStateStore.get(entry.titleId).isNotEmpty()
    }

    private fun canonicalBytes(
        entry: SaveEntry,
        syncState: SyncStateEntity? = null
    ): ByteArray {
        return saturnCanonicalSnapshot(entry, syncState)?.bytes ?: entry.readBytes()
    }

    private fun canonicalHash(
        entry: SaveEntry,
        syncState: SyncStateEntity? = null
    ): String {
        return saturnCanonicalSnapshot(entry, syncState)?.let { HashUtils.sha256Bytes(it.bytes) }
            ?: entry.computeHash()
    }

    private fun canonicalSize(
        entry: SaveEntry,
        syncState: SyncStateEntity? = null
    ): Long {
        return when {
            isSaturnEntry(entry) -> canonicalBytes(entry, syncState).size.toLong()
            entry.isPspSlot ->
                entry.saveDir!!.walkTopDown().filter { it.isFile }.sumOf { it.length() }
            entry.isMultiFile && entry.saveDir != null ->
                entry.saveDir.walkTopDown().filter { it.isFile }.sumOf { it.length() }
            entry.saveFile != null -> entry.saveFile.length()
            else -> 0L
        }
    }

    private fun localTimestamp(entry: SaveEntry): Long {
        return when {
            isSharedYabaSanshiroContainer(entry) && entry.saveFile?.exists() == true ->
                entry.saveFile.lastModified()
            else -> entry.getTimestamp()
        }
    }

    private fun writeDownloadedBytes(entry: SaveEntry, canonicalBytes: ByteArray): Boolean {
        return when {
            entry.isMultiFile && entry.saveDir != null -> {
                entry.saveDir.mkdirs()
                unzipBytesToDirectory(canonicalBytes, entry.saveDir)
                true
            }
            entry.saveFile != null -> {
                entry.saveFile.parentFile?.mkdirs()
                val bytesToWrite = if (isSharedYabaSanshiroContainer(entry)) {
                    val existing = entry.saveFile.takeIf { it.exists() }?.readBytes()
                    SaturnSaveFormatConverter.mergeCanonicalIntoYabaSanshiro(existing, canonicalBytes)
                } else if (isSaturnEntry(entry)) {
                    SaturnSaveFormatConverter.fromCanonical(canonicalBytes, saturnSyncFormat)
                } else {
                    canonicalBytes
                }
                entry.saveFile.writeBytes(bytesToWrite)
                true
            }
            else -> false
        }
    }

    suspend fun uploadSave(entry: SaveEntry): Boolean {
        if (isSharedYabaSanshiroContainer(entry)) {
            autoResolveSharedSaturnArchives(entry)
        }
        return if (entry.isPspSlot) {
            uploadPspBundle(entry)
        } else if (isPs1RawEntry(entry)) {
            uploadPs1Card(entry)
        } else if (isPs2RawEntry(entry)) {
            uploadPs2Card(entry)
        } else if (isGcRawEntry(entry)) {
            uploadGcCard(entry)
        } else {
            uploadSaveRaw(entry)
        }
    }

    private suspend fun uploadPs1Card(entry: SaveEntry): Boolean {
        return try {
            val saveFile = entry.saveFile ?: return false
            val response = api.uploadPs1Card(
                titleId = entry.titleId,
                slot = 0,
                consoleId = consoleId,
                body = saveFile.readBytes().toRequestBody("application/octet-stream".toMediaType())
            )
            response.status == "ok"
        } catch (e: Exception) {
            Log.e(TAG, "uploadPs1Card failed for ${entry.titleId}", e)
            false
        }
    }

    private suspend fun uploadPs2Card(entry: SaveEntry): Boolean {
        return try {
            val saveFile = entry.saveFile ?: return false
            val response = api.uploadPs2Card(
                titleId = entry.titleId,
                format = "ps2",
                consoleId = consoleId,
                body = saveFile.readBytes().toRequestBody("application/octet-stream".toMediaType())
            )
            response.status == "ok"
        } catch (e: Exception) {
            Log.e(TAG, "uploadPs2Card failed for ${entry.titleId}", e)
            false
        }
    }

    private suspend fun uploadSaveRaw(entry: SaveEntry): Boolean {
        return try {
            val syncState = dao.getById(entry.titleId)
            val bytes = canonicalBytes(entry, syncState)
            val requestBody = bytes.toRequestBody("application/octet-stream".toMediaType())
            val response = api.uploadSaveRaw(
                titleId = entry.titleId,
                source = "android",
                consoleId = consoleId,
                body = requestBody
            )
            response.status == "ok"
        } catch (e: Exception) {
            Log.e(TAG, "uploadSaveRaw failed for ${entry.titleId}", e)
            false
        }
    }

    /**
     * Uploads a PSP/PSX slot directory as a bundle v4.
     * Includes ALL files in the slot dir (DATA.BIN, ICON0.PNG, PARAM.SFO, etc.)
     * so the server hash matches the PSP homebrew client's algorithm.
     * Used for both PSP games and PS1 PSone Classics running under PPSSPP.
     */
    private suspend fun uploadPspBundle(entry: SaveEntry): Boolean {
        val slotDir = entry.saveDir ?: return false
        return try {
            val bundleBytes = BundleUtils.createBundle(entry.titleId, slotDir)
            val requestBody = bundleBytes.toRequestBody("application/octet-stream".toMediaType())
            val response = api.uploadSaveBundle(
                titleId = entry.titleId,
                source = "psp_emu",
                force = true,
                consoleId = consoleId,
                body = requestBody
            )
            response.status == "ok"
        } catch (e: Exception) {
            Log.e(TAG, "uploadPspBundle failed for ${entry.titleId}", e)
            false
        }
    }

    suspend fun downloadSave(entry: SaveEntry, titleId: String): Boolean {
        return if (entry.isPspSlot) {
            downloadPspBundle(entry, titleId)
        } else if (entry.systemName == "PS1" && entry.saveFile != null) {
            downloadPs1Card(entry, titleId)
        } else if (entry.systemName == "PS2" && entry.saveFile != null) {
            downloadPs2Card(entry, titleId)
        } else if (entry.systemName == "GC") {
            downloadGcCard(entry, titleId)
        } else {
            downloadSaveRaw(entry, titleId)
        }
    }

    private fun hasLocalSaveTarget(entry: SaveEntry): Boolean {
        return entry.saveFile != null || entry.saveDir != null
    }

    private suspend fun upsertKnownSyncState(
        entry: SaveEntry,
        hash: String,
        saturnArchiveNames: List<String>? = null
    ) {
        val now = System.currentTimeMillis()
        saturnArchiveNames?.let { SaturnArchiveStateStore.put(entry.titleId, it) }
        dao.upsert(
            SyncStateEntity(
                titleId = entry.titleId,
                lastSyncedHash = hash,
                lastSyncedAt = now,
                displayName = entry.displayName,
                systemName = entry.systemName
            )
        )
    }

    /**
     * Download a ROM from the server's ROM catalog.
     * Saves to the ROM scan directory under the appropriate system subfolder.
     * Returns the downloaded File on success, null on failure.
     */
    suspend fun downloadRom(
        romId: String,
        system: String,
        romScanDir: String,
        expectedFilename: String? = null,
        romDirOverrides: Map<String, String> = emptyMap(),
        extractFormat: String? = null,
    ): File? {
        return try {
            val response = api.downloadRom(romId, extract = extractFormat)
            if (!response.isSuccessful) {
                Log.e(TAG, "ROM download HTTP error for $romId: ${response.code()}")
                return null
            }
            val body = response.body() ?: return null

            val filename = expectedFilename
                ?: response.headers()["Content-Disposition"]
                    ?.let { cd ->
                        val match = Regex("filename=\"?([^\"]+)\"?").find(cd)
                        match?.groupValues?.get(1)
                    }
                ?: "$romId.rom"

            // Delegate folder selection to the shared helper so the download
            // path, the installed-ROMs scanner, and the Steam Deck client all
            // agree on where an incoming ROM lands.  The helper canonicalises
            // alias codes (``SCD`` → ``SEGACD``, ``GEN`` → ``MD``, …) before
            // picking a candidate, which fixes the regression where catalog
            // downloads for Sega CD landed in ``roms/SCD/`` instead of the
            // user's existing ``roms/segacd/``.
            val outDir = InstalledRomsScanner.resolveRomTargetDir(
                scanRoot = File(romScanDir),
                system = system,
                romDirOverrides = romDirOverrides,
            )
            outDir.mkdirs()
            val outFile = File(outDir, filename)

            body.byteStream().use { input ->
                outFile.outputStream().use { output ->
                    val buffer = ByteArray(ROM_BUFFER_SIZE)
                    var bytesRead: Int
                    while (input.read(buffer).also { bytesRead = it } != -1) {
                        output.write(buffer, 0, bytesRead)
                    }
                }
            }

            Log.i(TAG, "ROM downloaded: ${outFile.absolutePath} (${outFile.length()} bytes)")
            outFile
        } catch (e: Exception) {
            Log.e(TAG, "downloadRom failed for $romId", e)
            null
        }
    }

    private suspend fun downloadPs1Card(entry: SaveEntry, titleId: String): Boolean {
        return try {
            val saveFile = entry.saveFile ?: return false
            val response = api.downloadPs1Card(titleId, slot = 0)
            if (!response.isSuccessful) {
                val message = response.errorBody()?.string()?.let(::extractErrorDetail)
                    ?: "Download failed (${response.code()})"
                Log.e(TAG, "PS1 card download HTTP error for $titleId slot0: $message")
                throw IOException(message)
            }
            val body = response.body() ?: return false
            val bytes = body.bytes()
            saveFile.parentFile?.mkdirs()
            saveFile.writeBytes(bytes)
            true
        } catch (e: Exception) {
            Log.e(TAG, "downloadPs1Card failed for $titleId", e)
            false
        }
    }

    private suspend fun downloadPs2Card(entry: SaveEntry, titleId: String): Boolean {
        return try {
            val saveFile = entry.saveFile ?: return false
            val response = api.downloadPs2Card(titleId, format = "ps2")
            if (!response.isSuccessful) {
                val message = response.errorBody()?.string()?.let(::extractErrorDetail)
                    ?: "Download failed (${response.code()})"
                Log.e(TAG, "PS2 card download HTTP error for $titleId: $message")
                throw IOException(message)
            }
            val body = response.body() ?: return false
            val bytes = body.bytes()
            saveFile.parentFile?.mkdirs()
            saveFile.writeBytes(bytes)
            true
        } catch (e: Exception) {
            Log.e(TAG, "downloadPs2Card failed for $titleId", e)
            false
        }
    }

    private suspend fun downloadSaveRaw(entry: SaveEntry, titleId: String): Boolean {
        val response = api.downloadSaveRaw(titleId)
        if (!response.isSuccessful) {
            val message = response.errorBody()?.string()?.let(::extractErrorDetail)
                ?: "Download failed (${response.code()})"
            Log.e(TAG, "Download HTTP error for $titleId: $message")
            throw IOException(message)
        }
        return try {
            val body = response.body() ?: return false
            val bytes = body.bytes()
            val wrote = writeDownloadedBytes(entry, bytes)
            if (wrote && isSaturnEntry(entry)) {
                val serverHash = response.headers()["X-Save-Hash"]
                    ?: HashUtils.sha256Bytes(bytes)
                val archiveNames = SaturnSaveFormatConverter.archiveNames(bytes)
                upsertKnownSyncState(entry, serverHash, archiveNames)
            }
            wrote
        } catch (e: Exception) {
            Log.e(TAG, "downloadSaveRaw failed for $titleId", e)
            false
        }
    }

    /**
     * Downloads a PPSSPP save as a bundle v4 and extracts all files to the slot directory.
     * The slot directory is created if it doesn't exist (server-only saves).
     */
    private suspend fun downloadPspBundle(entry: SaveEntry, titleId: String): Boolean {
        val slotDir = entry.saveDir ?: return false
        val response = api.downloadSaveBundle(titleId)
        if (!response.isSuccessful) {
            val message = response.errorBody()?.string()?.let(::extractErrorDetail)
                ?: "Download failed (${response.code()})"
            Log.e(TAG, "Download bundle HTTP error for $titleId: $message")
            throw IOException(message)
        }
        return try {
            val bytes = response.body()?.bytes() ?: return false
            val files = BundleUtils.parseBundle(bytes)
            if (files.isEmpty()) return false
            slotDir.mkdirs()
            for ((name, data) in files) {
                File(slotDir, name).writeBytes(data)
            }
            true
        } catch (e: Exception) {
            Log.e(TAG, "downloadPspBundle failed for $titleId", e)
            false
        }
    }

    suspend fun recordSyncedState(entry: SaveEntry) {
        updateSyncState(entry)
    }

    suspend fun recordSyncedStateFromFile(entry: SaveEntry) {
        updateSyncStateFromFile(entry)
    }

    private suspend fun updateSyncState(entry: SaveEntry) {
        try {
            val saturnSnapshot = saturnCanonicalSnapshot(entry)
            val hash = saturnSnapshot?.let { HashUtils.sha256Bytes(it.bytes) } ?: entry.computeHash()
            saturnSnapshot?.archiveNames?.let { SaturnArchiveStateStore.put(entry.titleId, it) }
            val now = System.currentTimeMillis()
            dao.upsert(
                SyncStateEntity(
                    titleId = entry.titleId,
                    lastSyncedHash = hash,
                    lastSyncedAt = now,
                    displayName = entry.displayName,
                    systemName = entry.systemName
                )
            )
        } catch (e: Exception) {
            Log.e(TAG, "Failed to update sync state for ${entry.titleId}", e)
        }
    }

    /**
     * Like [updateSyncState] but reads the hash from disk rather than from the
     * entry object. Used after downloading a server-only save, because
     * [SaveEntry.computeHash] returns "" for entries marked [SaveEntry.isServerOnly].
     */
    private suspend fun updateSyncStateFromFile(entry: SaveEntry) {
        try {
            val saturnSnapshot = saturnCanonicalSnapshot(entry)
            val hash = when {
                // PSP/PSX slot: hash = sha256(all files sorted by name, data only, no paths)
                entry.isPspSlot && entry.saveDir?.exists() == true ->
                    HashUtils.sha256DirFiles(entry.saveDir)
                saturnSnapshot != null ->
                    HashUtils.sha256Bytes(saturnSnapshot.bytes)
                entry.saveFile?.exists() == true -> HashUtils.sha256File(entry.saveFile)
                entry.saveDir?.exists() == true  -> HashUtils.sha256Dir(entry.saveDir)
                else -> return  // nothing to record
            }
            saturnSnapshot?.archiveNames?.let { SaturnArchiveStateStore.put(entry.titleId, it) }
            val now = System.currentTimeMillis()
            dao.upsert(
                SyncStateEntity(
                    titleId = entry.titleId,
                    lastSyncedHash = hash,
                    lastSyncedAt = now,
                    displayName = entry.displayName,
                    systemName = entry.systemName
                )
            )
        } catch (e: Exception) {
            Log.e(TAG, "Failed to update sync state (from file) for ${entry.titleId}", e)
        }
    }

    private fun unzipBytesToDirectory(bytes: ByteArray, targetDir: java.io.File) {
        val bais = java.io.ByteArrayInputStream(bytes)
        val zis = java.util.zip.ZipInputStream(bais)
        var entry = zis.nextEntry
        while (entry != null) {
            val outFile = java.io.File(targetDir, entry.name)
            if (entry.isDirectory) {
                outFile.mkdirs()
            } else {
                outFile.parentFile?.mkdirs()
                outFile.outputStream().use { os ->
                    zis.copyTo(os)
                }
            }
            zis.closeEntry()
            entry = zis.nextEntry
        }
        zis.close()
    }

    private fun extractErrorDetail(body: String): String {
        return Regex("\"detail\"\\s*:\\s*\"([^\"]+)\"")
            .find(body)
            ?.groupValues
            ?.getOrNull(1)
            ?.replace("\\n", "\n")
            ?: body
    }

    private fun isPs1RawEntry(entry: SaveEntry): Boolean {
        return entry.systemName == "PS1" && entry.saveFile != null && !entry.isPspSlot
    }

    private fun isPs2RawEntry(entry: SaveEntry): Boolean {
        return entry.systemName == "PS2" && entry.saveFile != null
    }

    private fun isGcRawEntry(entry: SaveEntry): Boolean {
        return entry.systemName == "GC"
    }

    private suspend fun uploadGcCard(entry: SaveEntry): Boolean {
        return try {
            val saveFile = entry.saveFile ?: return false
            val bytes = saveFile.readBytes()
            val requestBody = bytes.toRequestBody("application/octet-stream".toMediaType())
            val response = api.uploadGcCard(
                titleId = entry.titleId,
                format = "gci",
                consoleId = consoleId,
                body = requestBody
            )
            response.status == "ok"
        } catch (e: Exception) {
            Log.e(TAG, "uploadGcCard failed for ${entry.titleId}", e)
            false
        }
    }

    private suspend fun downloadGcCard(entry: SaveEntry, titleId: String): Boolean {
        return try {
            val saveFile = entry.saveFile ?: return false
            val response = api.downloadGcCard(titleId, format = "gci")
            if (!response.isSuccessful) {
                val message = response.errorBody()?.string()?.let(::extractErrorDetail)
                    ?: "Download failed (${response.code()})"
                Log.e(TAG, "downloadGcCard error for $titleId: $message")
                return false
            }
            val bytes = response.body()?.bytes() ?: return false
            saveFile.parentFile?.mkdirs()
            saveFile.writeBytes(bytes)
            true
        } catch (e: Exception) {
            Log.e(TAG, "downloadGcCard failed for $titleId", e)
            false
        }
    }

    private suspend fun syncRawCardEntry(entry: SaveEntry): RawCardSyncOutcome {
        val localExists = entry.exists()
        val localHash = if (localExists) canonicalHash(entry) else ""
        val lastSyncHash = dao.getById(entry.titleId)?.lastSyncedHash

        val serverMeta = try {
            when (entry.systemName) {
                "PS1" -> api.getPs1CardMeta(entry.titleId, slot = 0)
                "PS2" -> api.getPs2CardMeta(entry.titleId, format = "ps2")
                "GC"  -> api.getGcCardMeta(entry.titleId)
                else -> null
            }
        } catch (e: retrofit2.HttpException) {
            if (e.code() == 404) null else throw e
        }

        if (serverMeta == null) {
            if (!localExists) return RawCardSyncOutcome.SKIPPED
            val ok = when (entry.systemName) {
                "PS1" -> uploadPs1Card(entry)
                "PS2" -> uploadPs2Card(entry)
                "GC"  -> uploadGcCard(entry)
                else -> false
            }
            if (ok) {
                updateSyncState(entry)
                return RawCardSyncOutcome.UPLOADED
            }
            throw IOException("Upload failed")
        }

        val serverHash = serverMeta.save_hash ?: ""
        if (localExists && localHash == serverHash) {
            return RawCardSyncOutcome.UP_TO_DATE
        }

        if (!localExists) {
            val ok = when (entry.systemName) {
                "PS1" -> downloadPs1Card(entry, entry.titleId)
                "PS2" -> downloadPs2Card(entry, entry.titleId)
                "GC"  -> downloadGcCard(entry, entry.titleId)
                else -> false
            }
            if (ok) {
                updateSyncStateFromFile(entry)
                return RawCardSyncOutcome.DOWNLOADED
            }
            throw IOException("Download failed")
        }

        return when {
            lastSyncHash != null && lastSyncHash == serverHash -> {
                val ok = when (entry.systemName) {
                    "PS1" -> uploadPs1Card(entry)
                    "PS2" -> uploadPs2Card(entry)
                    "GC"  -> uploadGcCard(entry)
                    else -> false
                }
                if (ok) {
                    updateSyncState(entry)
                    RawCardSyncOutcome.UPLOADED
                } else {
                    throw IOException("Upload failed")
                }
            }
            lastSyncHash != null && lastSyncHash == localHash -> {
                val ok = when (entry.systemName) {
                    "PS1" -> downloadPs1Card(entry, entry.titleId)
                    "PS2" -> downloadPs2Card(entry, entry.titleId)
                    "GC"  -> downloadGcCard(entry, entry.titleId)
                    else -> false
                }
                if (ok) {
                    updateSyncStateFromFile(entry)
                    RawCardSyncOutcome.DOWNLOADED
                } else {
                    throw IOException("Download failed")
                }
            }
            else -> RawCardSyncOutcome.CONFLICT
        }
    }
}

private enum class RawCardSyncOutcome {
    UPLOADED,
    DOWNLOADED,
    CONFLICT,
    UP_TO_DATE,
    SKIPPED,
}

private const val ROM_BUFFER_SIZE = 8192
