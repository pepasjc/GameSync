package com.savesync.android.sync

import android.util.Log
import com.savesync.android.api.SaveSyncApi
import com.savesync.android.api.SyncRequest
import com.savesync.android.api.SyncTitle
import com.savesync.android.emulators.SaveEntry
import com.savesync.android.storage.AppDatabase
import com.savesync.android.storage.SyncStateEntity
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.RequestBody.Companion.toRequestBody
import java.io.File
import java.io.IOException

private const val TAG = "SyncEngine"

data class SyncResult(
    val uploaded: Int,
    val downloaded: Int,
    val conflicts: List<String>,
    val errors: List<String>
)

class SyncEngine(
    private val api: SaveSyncApi,
    private val db: AppDatabase,
    private val consoleId: String
) {
    private val dao = db.syncStateDao()

    suspend fun sync(saves: List<SaveEntry>): SyncResult {
        val errors = mutableListOf<String>()
        var uploaded = 0
        var downloaded = 0
        val conflicts = mutableListOf<String>()

        // Build a map for quick lookup
        val saveMap = saves.associateBy { it.titleId }

        val (rawCardEntries, otherEntries) = saves.partition {
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

        // Server-only entries have no local file yet — download them directly without
        // going through the sync negotiation (the server doesn't know our state for these).
        val (serverOnlyEntries, localEntries) = otherEntries.partition { it.isServerOnly }
        for (entry in serverOnlyEntries) {
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
            if (!entry.exists()) return@mapNotNull null
            try {
                val hash = entry.computeHash()
                val timestamp = entry.getTimestamp() / 1000L  // convert ms to seconds
                val size = when {
                    entry.isPspSlot ->
                        entry.saveDir!!.walkTopDown().filter { it.isFile }.sumOf { it.length() }
                    entry.isMultiFile && entry.saveDir != null ->
                        entry.saveDir.walkTopDown().filter { it.isFile }.sumOf { it.length() }
                    entry.saveFile != null -> entry.saveFile.length()
                    else -> 0L
                }
                val lastSyncState = dao.getById(entry.titleId)
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

    suspend fun uploadSave(entry: SaveEntry): Boolean {
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
            val bytes = entry.readBytes()
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
     * Used for both PPSSPP (PSP games) and PSX (PSone Classics under PPSSPP).
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

    /**
     * Download a ROM from the server's ROM catalog.
     * Saves to the ROM scan directory under the appropriate system subfolder.
     * Returns the downloaded File on success, null on failure.
     */
    suspend fun downloadRom(
        titleId: String,
        romScanDir: String,
        expectedFilename: String? = null
    ): File? {
        return try {
            val response = api.downloadRom(titleId)
            if (!response.isSuccessful) {
                Log.e(TAG, "ROM download HTTP error for $titleId: ${response.code()}")
                return null
            }
            val body = response.body() ?: return null

            val filename = expectedFilename
                ?: response.headers()["Content-Disposition"]
                    ?.let { cd ->
                        val match = Regex("filename=\"?([^\"]+)\"?").find(cd)
                        match?.groupValues?.get(1)
                    }
                ?: "$titleId.rom"

            val system = titleId.substringBefore('_').let { code ->
                mapOf(
                    "SEGACD" to "segacd", "WSWAN" to "wonderswan",
                    "NEOCD" to "neogeocd", "PCECD" to "pcenginecd"
                ).getOrDefault(code, code.lowercase())
            }

            val outDir = File(romScanDir, system)
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
            Log.e(TAG, "downloadRom failed for $titleId", e)
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

            when {
                entry.isMultiFile && entry.saveDir != null -> {
                    entry.saveDir.mkdirs()
                    unzipBytesToDirectory(bytes, entry.saveDir)
                }
                entry.saveFile != null -> {
                    entry.saveFile.parentFile?.mkdirs()
                    entry.saveFile.writeBytes(bytes)
                }
                else -> return false
            }
            true
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
            val hash = entry.computeHash()
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
            val hash = when {
                // PSP/PSX slot: hash = sha256(all files sorted by name, data only, no paths)
                entry.isPspSlot && entry.saveDir?.exists() == true ->
                    HashUtils.sha256DirFiles(entry.saveDir)
                entry.saveFile?.exists() == true -> HashUtils.sha256File(entry.saveFile)
                entry.saveDir?.exists() == true  -> HashUtils.sha256Dir(entry.saveDir)
                else -> return  // nothing to record
            }
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
        val localHash = if (localExists) entry.computeHash() else ""
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
