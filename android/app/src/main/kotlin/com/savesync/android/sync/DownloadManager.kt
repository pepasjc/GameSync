package com.savesync.android.sync

import android.content.Context
import android.util.Log
import com.savesync.android.api.SaveSyncApi
import com.savesync.android.installed.InstalledRomsScanner
import com.savesync.android.storage.DownloadDao
import com.savesync.android.storage.DownloadEntity
import com.savesync.android.systems.SystemAliases
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Deferred
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.NonCancellable
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.async
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.delay
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeoutOrNull
import okhttp3.ResponseBody
import retrofit2.Response
import java.io.File
import java.io.IOException
import java.io.RandomAccessFile
import java.util.UUID

private const val TAG = "DownloadManager"
private const val IO_BUFFER_SIZE = 256 * 1024  // 256 KB — fewer syscalls and better throughput on multi-GB
private const val INITIAL_PERSIST_INTERVAL_MS = 250L
private const val STEADY_PERSIST_INTERVAL_MS = 1500L
private const val INITIAL_PHASE_MS = 5_000L  // first 5 s = aggressive persist
private const val FLUSH_INTERVAL_MS = 5_000L  // fsync every 5 s of streaming
private const val PROGRESS_EVENT_INTERVAL_MS = 200L

/**
 * Auto-retry policy for transient IOException during streaming.
 *
 * Multi-GB downloads on cellular / weak Wi-Fi routinely lose the
 * connection at 80%+ — a tower handover, a Wi-Fi roam, even a proxy
 * idle-cull will surface as a single read() throwing IOException.  The
 * old policy (3 immediate retries, no backoff) burned all attempts during
 * one network blip, so the user got "Failed" on a download that was 4 GB
 * into a 4.2 GB transfer.
 *
 * New policy: more attempts with exponential backoff so a 60-second
 * outage doesn't kill the download.  Each retry uses ``Range:`` so the
 * already-on-disk bytes are preserved — total wasted work is at most one
 * IO_BUFFER_SIZE chunk per failure.
 */
private val RETRY_BACKOFF_MS = longArrayOf(
    2_000L,    // attempt 2: ~2 s after first failure
    8_000L,    // attempt 3: ~8 s
    20_000L,   // attempt 4: ~20 s
    45_000L,   // attempt 5: ~45 s
    90_000L,   // attempt 6: 1.5 min
    180_000L,  // attempt 7: 3 min — covers a typical mobile-data lapse
)
private const val MAX_AUTO_RETRIES = 7  // first attempt + 6 retries
private const val CANCEL_JOIN_TIMEOUT_MS = 5_000L

/**
 * Application-scoped service that owns ROM downloads.
 *
 * Why this lives in app scope (not viewModelScope):
 *   * Long downloads that outlive the Activity (rotation, low-memory kill,
 *     user backgrounding) used to be cancelled when ``viewModelScope``
 *     died.  Surfaced as "the app crashed during my big download" — really
 *     the app was killed and the download with it.  This manager attaches
 *     to ``SaveSyncApp.appScope`` so it keeps streaming bytes through
 *     ViewModel teardown.
 *
 *   * On Android the OS will also reclaim a "background" process under
 *     memory pressure regardless of coroutine scope.  To prevent that we
 *     promote the process via [DownloadForegroundService] whenever the
 *     active queue is non-empty.  See [refreshForegroundService].
 *
 * Pause / resume protocol:
 *   * Active download writes to ``<finalFilePath>.part``.
 *   * Pause: cancel the worker job.  The .part file stays.  We persist
 *     ``downloadedBytes`` to Room so a process death still resumes cleanly.
 *   * Resume: re-launch with ``Range: bytes=<downloadedBytes>-`` and
 *     ``RandomAccessFile.seek(downloadedBytes)`` so the new bytes append.
 *   * Server fallback: if the server replies 200 instead of 206 we restart
 *     the file from byte 0 (truncate the .part).
 *
 * Auto-retry: transient ``IOException`` during streaming triggers up to
 * [MAX_AUTO_RETRIES] retries, each using ``Range:`` so we don't redownload
 * what we already have.
 *
 * Reliability tuning:
 *   * Progress events are throttled to ~5 Hz but the **first** event after
 *     bytes start arriving is emitted unconditionally so the UI doesn't sit
 *     at 0 % for the throttle window.
 *   * The progress SharedFlow uses ``replay = 1`` so a screen that
 *     subscribes mid-download immediately sees the latest sample.
 *   * Room persistence is aggressive (~250 ms) for the first 5 s of each
 *     download so an OS kill in the warm-up phase still lets us resume
 *     accurately, then relaxes to 1.5 s to avoid hammering the disk.
 *   * Join on cancel is bounded ([CANCEL_JOIN_TIMEOUT_MS]) so a hung
 *     network read can't block subsequent user actions.
 */
class DownloadManager(
    private val appContext: Context,
    private val dao: DownloadDao,
    private val appScope: CoroutineScope = CoroutineScope(SupervisorJob() + Dispatchers.IO),
    private val nowMillis: () -> Long = { System.currentTimeMillis() },
) {

    private val jobs = mutableMapOf<String, Job>()
    private val jobsLock = Mutex()
    private var foregroundActive = false

    /**
     * Lightweight per-download progress events.  Compose screens can
     * collect this for sub-second progress bars without thrashing the
     * Room write queue.
     *
     * ``replay = 1`` so a late-subscribing screen immediately gets the
     * most recent event for any active download — without it the progress
     * bar would sit empty until the next 200 ms tick.
     */
    private val _progressEvents = MutableSharedFlow<ProgressEvent>(
        replay = 1,
        extraBufferCapacity = 64,
    )
    val progressEvents: SharedFlow<ProgressEvent> = _progressEvents

    data class ProgressEvent(
        val id: String,
        val downloadedBytes: Long,
        val totalBytes: Long,
        val bytesPerSecond: Long,
    )

    /** Reactive stream of every persisted download row. */
    fun observeAll(): Flow<List<DownloadEntity>> = dao.observeAll()

    // ──────────────────────────────────────────────────────────────────
    // Public API — fire-and-forget wrappers (preferred for UI handlers)
    // ──────────────────────────────────────────────────────────────────

    /**
     * Non-suspending enqueue used by ViewModel button handlers.
     *
     * Runs on [appScope] so the request can never be dropped by
     * ViewModel teardown, and the work survives even if the user
     * navigates away or the Activity recreates between the tap and the
     * actual DB insert.
     */
    fun enqueueAsync(
        api: SaveSyncApi,
        romId: String,
        system: String,
        displayName: String,
        filename: String,
        romScanDir: String,
        romDirOverrides: Map<String, String> = emptyMap(),
        extractFormat: String? = null,
        cdGamesPerContentFolder: Boolean = false,
    ): Deferred<String> = appScope.async {
        enqueue(
            api = api,
            romId = romId,
            system = system,
            displayName = displayName,
            filename = filename,
            romScanDir = romScanDir,
            romDirOverrides = romDirOverrides,
            extractFormat = extractFormat,
            cdGamesPerContentFolder = cdGamesPerContentFolder,
        )
    }

    /** Fire-and-forget pause — owned by [appScope] so it can't be cancelled
     *  by the user navigating away from the Downloads tab mid-action. */
    fun pauseAsync(id: String): Job = appScope.launch { pause(id) }

    fun resumeAsync(api: SaveSyncApi, id: String): Job = appScope.launch { resume(api, id) }

    fun cancelAsync(id: String): Job = appScope.launch { cancel(id) }

    fun removeAsync(id: String): Job = appScope.launch { remove(id) }

    fun clearFinishedAsync(): Job = appScope.launch { clearFinished() }

    // ──────────────────────────────────────────────────────────────────
    // Suspending API (kept for tests + advanced callers)
    // ──────────────────────────────────────────────────────────────────

    /**
     * Add a new download to the queue and start it immediately.
     *
     * Returns the freshly-minted download id so callers (e.g. the catalog
     * snackbar) can deep-link to the Downloads tab on the right row.
     */
    suspend fun enqueue(
        api: SaveSyncApi,
        romId: String,
        system: String,
        displayName: String,
        filename: String,
        romScanDir: String,
        romDirOverrides: Map<String, String> = emptyMap(),
        extractFormat: String? = null,
        cdGamesPerContentFolder: Boolean = false,
    ): String {
        val (finalFile, partFile) = resolveTargetFiles(
            romScanDir = romScanDir,
            system = system,
            filename = filename,
            romDirOverrides = romDirOverrides,
            cdGamesPerContentFolder = cdGamesPerContentFolder,
        )
        val now = nowMillis()
        val entity = DownloadEntity(
            id = UUID.randomUUID().toString(),
            romId = romId,
            system = system,
            displayName = displayName,
            filename = filename,
            partFilePath = partFile.absolutePath,
            finalFilePath = finalFile.absolutePath,
            totalBytes = -1,
            // Important — we mark as DOWNLOADING straight away rather
            // than waiting for the worker to flip the state.  The worker
            // can take a moment to be picked up off the IO dispatcher,
            // and a row that spends that time stuck in QUEUED looks
            // broken in the UI.
            downloadedBytes = if (partFile.exists()) partFile.length() else 0L,
            status = DownloadEntity.Status.DOWNLOADING,
            errorMessage = null,
            extractFormat = extractFormat,
            createdAt = now,
            updatedAt = now,
        )
        dao.upsert(entity)
        // Pre-seed a progress event so the UI shows the row immediately
        // with the resume offset (if any) instead of an empty bar.
        _progressEvents.tryEmit(
            ProgressEvent(
                id = entity.id,
                downloadedBytes = entity.downloadedBytes,
                totalBytes = entity.totalBytes,
                bytesPerSecond = 0L,
            )
        )
        refreshForegroundService()
        startWorker(api, entity)
        return entity.id
    }

    /** Pause an in-flight download.  No-op if it's not running. */
    suspend fun pause(id: String) {
        cancelJob(id)
        val current = dao.getById(id) ?: return
        if (current.status == DownloadEntity.Status.DOWNLOADING ||
            current.status == DownloadEntity.Status.QUEUED
        ) {
            dao.upsert(
                current.copy(
                    status = DownloadEntity.Status.PAUSED,
                    updatedAt = nowMillis(),
                )
            )
        }
        refreshForegroundService()
    }

    /** Resume a paused / failed / cancelled download. */
    suspend fun resume(api: SaveSyncApi, id: String) {
        val current = dao.getById(id) ?: return
        if (current.status == DownloadEntity.Status.COMPLETED) return
        // Bring the on-disk progress in sync with what the .part file
        // actually contains — protects us against a process kill that
        // happened between a write and the next persist.
        val onDisk = File(current.partFilePath).takeIf { it.exists() }?.length() ?: 0L
        val refreshed = current.copy(
            downloadedBytes = onDisk,
            status = DownloadEntity.Status.DOWNLOADING,
            errorMessage = null,
            updatedAt = nowMillis(),
        )
        dao.upsert(refreshed)
        _progressEvents.tryEmit(
            ProgressEvent(
                id = refreshed.id,
                downloadedBytes = refreshed.downloadedBytes,
                totalBytes = refreshed.totalBytes,
                bytesPerSecond = 0L,
            )
        )
        refreshForegroundService()
        startWorker(api, refreshed)
    }

    /** Cancel a download.  Deletes the .part file. */
    suspend fun cancel(id: String) {
        cancelJob(id)
        val current = dao.getById(id) ?: return
        if (current.status == DownloadEntity.Status.COMPLETED) return
        runCatching { File(current.partFilePath).delete() }
        dao.upsert(
            current.copy(
                status = DownloadEntity.Status.CANCELLED,
                downloadedBytes = 0,
                updatedAt = nowMillis(),
            )
        )
        refreshForegroundService()
    }

    /** Remove the row entirely.  Also deletes the .part file. */
    suspend fun remove(id: String) {
        cancelJob(id)
        val current = dao.getById(id)
        if (current != null) {
            runCatching { File(current.partFilePath).delete() }
        }
        dao.deleteById(id)
        refreshForegroundService()
    }

    /** Bulk-clear finished rows (completed / failed / cancelled). */
    suspend fun clearFinished() {
        dao.deleteFinished()
    }

    /**
     * Re-attach workers to any download rows that were ``downloading`` /
     * ``queued`` when the process died.  Call once on app start so a
     * background-killed download silently picks up where it left off.
     */
    suspend fun resumeInterrupted(api: SaveSyncApi) {
        val active = dao.getActive()
        for (entity in active) {
            if (entity.status == DownloadEntity.Status.PAUSED) continue
            // Treat "was downloading when killed" as "should resume now".
            resume(api, entity.id)
        }
    }

    // ──────────────────────────────────────────────────────────────────
    // Job lifecycle
    // ──────────────────────────────────────────────────────────────────

    private suspend fun startWorker(api: SaveSyncApi, entity: DownloadEntity) {
        // Replace any existing job for this id so resume after a failure
        // doesn't double up.
        cancelJob(entity.id)
        val job = appScope.launch { runDownload(api, entity.id) }
        jobsLock.withLock { jobs[entity.id] = job }
        // Clean up our jobs map when the coroutine finishes (any reason).
        job.invokeOnCompletion {
            appScope.launch {
                jobsLock.withLock { jobs.remove(entity.id) }
                refreshForegroundService()
            }
        }
    }

    private suspend fun cancelJob(id: String) {
        val job = jobsLock.withLock { jobs.remove(id) }
        job?.cancel()
        // Bounded wait — if the worker is wedged in NonCancellable code
        // (a slow Room write, say) we don't want subsequent user actions
        // to hang the UI forever.  5 s is plenty for any bookkeeping.
        if (job != null) {
            withTimeoutOrNull(CANCEL_JOIN_TIMEOUT_MS) { job.join() }
        }
    }

    /**
     * Body of the worker coroutine.  Re-reads the entity at every retry
     * boundary so user-driven pause/cancel updates take effect immediately.
     *
     * Retry strategy: ``MAX_AUTO_RETRIES`` total attempts.  Between
     * attempts we sleep for a backoff drawn from ``RETRY_BACKOFF_MS`` so a
     * transient outage (Wi-Fi roam, cellular handover, server restart)
     * doesn't burn all attempts in a few hundred milliseconds.  The wait
     * is interruptible — a user pause/cancel during the backoff exits
     * cleanly because Kotlin's ``delay`` is cooperatively cancellable.
     */
    private suspend fun runDownload(api: SaveSyncApi, id: String) {
        var attempt = 0
        var lastError: IOException? = null
        while (attempt < MAX_AUTO_RETRIES) {
            attempt++
            val entity = dao.getById(id) ?: return
            // External pause / cancel flips the status; bail out quietly.
            if (entity.status == DownloadEntity.Status.PAUSED ||
                entity.status == DownloadEntity.Status.CANCELLED ||
                entity.status == DownloadEntity.Status.COMPLETED
            ) return

            try {
                streamToDisk(api, entity)
                markCompleted(entity.id)
                refreshForegroundService()
                return
            } catch (ce: CancellationException) {
                Log.d(TAG, "Download $id cancelled during attempt $attempt")
                throw ce
            } catch (io: IOException) {
                lastError = io
                Log.w(TAG, "Download $id attempt $attempt failed: ${io.message}", io)
                // On the last attempt, fall through to the failure marker
                // below.  Otherwise sleep with exponential backoff and
                // loop — the next attempt sees the persisted .part file
                // and resumes via Range:.
                if (attempt >= MAX_AUTO_RETRIES) break
                val backoff = RETRY_BACKOFF_MS.getOrElse(attempt - 1) { RETRY_BACKOFF_MS.last() }
                Log.d(TAG, "Download $id sleeping ${backoff}ms before attempt ${attempt + 1}")
                delay(backoff)
            } catch (t: Throwable) {
                Log.e(TAG, "Download $id terminal error", t)
                lastError = IOException(t.message ?: "Unknown error", t)
                break
            }
        }
        val msg = lastError?.message ?: "Download failed"
        markFailed(id, "$msg (after $attempt attempts)")
        refreshForegroundService()
    }

    /**
     * Stream the response body to ``.part``.  Uses ``Range:`` to skip
     * already-downloaded bytes, falls back to a clean restart if the server
     * ignores the header (returns 200 instead of 206).
     */
    private suspend fun streamToDisk(api: SaveSyncApi, entity: DownloadEntity) {
        val partFile = File(entity.partFilePath).also {
            it.parentFile?.mkdirs()
        }
        val resumeFrom = if (partFile.exists()) partFile.length() else 0L
        val rangeHeader = if (resumeFrom > 0L) "bytes=$resumeFrom-" else null

        val response: Response<ResponseBody> = api.downloadRom(
            romId = entity.romId,
            extract = entity.extractFormat,
            range = rangeHeader,
        )
        if (!response.isSuccessful) {
            val code = response.code()
            val detail = runCatching { response.errorBody()?.string() }
                .getOrNull()
                ?.trim()
                ?.takeIf { it.isNotEmpty() }
            throw IOException(
                detail?.let { "HTTP $code: $it" } ?: "HTTP $code"
            )
        }
        val body = response.body()
            ?: throw IOException("Server returned empty response for ROM ${entity.romId}")

        // Determine starting offset.  If the server honoured the range we
        // append; if not (status 200) we truncate and start over.
        val honoredRange = response.code() == 206
        val startOffset = if (honoredRange) resumeFrom else 0L
        val totalBytes = computeTotalBytes(response, body, startOffset)

        if (!honoredRange && resumeFrom > 0L) {
            Log.w(TAG, "Server ignored Range header for ${entity.romId}; restarting from 0")
        }

        // Persist the freshly known total + offset so the UI shows a real
        // progress bar even on the very first chunk.
        persistProgress(entity.id, startOffset, totalBytes)
        _progressEvents.tryEmit(
            ProgressEvent(
                id = entity.id,
                downloadedBytes = startOffset,
                totalBytes = totalBytes,
                bytesPerSecond = 0L,
            )
        )

        var written = startOffset
        val streamStart = nowMillis()
        var lastPersistAt = streamStart
        var lastFlushAt = streamStart
        var lastSampleAt = streamStart
        var lastSampleBytes = startOffset
        var firstChunkSeen = false

        try {
            body.byteStream().use { input ->
                RandomAccessFile(partFile, "rw").use { raf ->
                    if (honoredRange) {
                        raf.seek(startOffset)
                    } else {
                        raf.setLength(0)
                        raf.seek(0)
                    }
                    val buffer = ByteArray(IO_BUFFER_SIZE)
                    while (true) {
                        // Cooperative cancellation — don't write another
                        // chunk if the user paused while the previous read
                        // was in flight.
                        currentCoroutineContext().ensureActive()
                        val read = input.read(buffer)
                        if (read == -1) break
                        raf.write(buffer, 0, read)
                        written += read

                        val now = nowMillis()

                        // First chunk → emit progress unconditionally so
                        // the UI shows movement immediately rather than
                        // waiting up to 200 ms for the throttle.
                        if (!firstChunkSeen) {
                            firstChunkSeen = true
                            _progressEvents.tryEmit(
                                ProgressEvent(
                                    id = entity.id,
                                    downloadedBytes = written,
                                    totalBytes = totalBytes,
                                    bytesPerSecond = 0L,
                                )
                            )
                            persistProgress(entity.id, written, totalBytes)
                            lastSampleAt = now
                            lastSampleBytes = written
                            lastPersistAt = now
                            continue
                        }

                        // Throttled progress event — sub-second updates so
                        // the bar is smooth, but cheap enough to skip
                        // recomposition storms on multi-GB files.
                        val sampleDelta = now - lastSampleAt
                        if (sampleDelta >= PROGRESS_EVENT_INTERVAL_MS) {
                            val bps = if (sampleDelta > 0) {
                                ((written - lastSampleBytes) * 1000L) / sampleDelta
                            } else 0L
                            _progressEvents.tryEmit(
                                ProgressEvent(
                                    id = entity.id,
                                    downloadedBytes = written,
                                    totalBytes = totalBytes,
                                    bytesPerSecond = bps,
                                )
                            )
                            lastSampleAt = now
                            lastSampleBytes = written
                        }

                        // Persist progress to Room — aggressive in the
                        // first few seconds (so a quick OS kill still
                        // remembers where we were), then back off.
                        val persistInterval = if (now - streamStart < INITIAL_PHASE_MS) {
                            INITIAL_PERSIST_INTERVAL_MS
                        } else {
                            STEADY_PERSIST_INTERVAL_MS
                        }
                        if (now - lastPersistAt >= persistInterval) {
                            persistProgress(entity.id, written, totalBytes)
                            lastPersistAt = now
                        }

                        // fsync periodically — keeps real bytes on disk
                        // so a process kill loses at most ~5 s of work.
                        if (now - lastFlushAt >= FLUSH_INTERVAL_MS) {
                            runCatching { raf.fd.sync() }
                            lastFlushAt = now
                        }
                    }
                    // Final flush before close so the rename moves a
                    // fully-on-disk file.
                    runCatching { raf.fd.sync() }
                }
            }
        } catch (ce: CancellationException) {
            // Always persist the final byte-count before we let
            // cancellation propagate so resume picks up exactly where
            // we stopped.
            withContext(NonCancellable) {
                persistProgress(entity.id, written, totalBytes)
            }
            throw ce
        }

        // Final persist + rename .part → final.
        persistProgress(entity.id, written, totalBytes)
        promotePartFile(entity)
    }

    private fun computeTotalBytes(
        response: Response<ResponseBody>,
        body: ResponseBody,
        startOffset: Long,
    ): Long {
        // Prefer Content-Range total ("bytes 1234-5678/9000") since
        // OkHttp's contentLength() reports just the partial length.
        response.headers()["Content-Range"]?.let { range ->
            val total = range.substringAfter('/', "").toLongOrNull()
            if (total != null && total > 0L) return total
        }
        val len = body.contentLength()
        if (len <= 0L) return -1L
        // For non-range responses contentLength == total file size.
        // For a 206 response without Content-Range (rare) it's just
        // the remaining length, so we add the offset.
        return if (response.code() == 206) startOffset + len else len
    }

    private suspend fun persistProgress(id: String, written: Long, totalBytes: Long) {
        val current = dao.getById(id) ?: return
        if (current.status == DownloadEntity.Status.COMPLETED) return
        dao.upsert(
            current.copy(
                downloadedBytes = written,
                totalBytes = if (totalBytes > 0) totalBytes else current.totalBytes,
                updatedAt = nowMillis(),
            )
        )
    }

    private suspend fun promotePartFile(entity: DownloadEntity) {
        val partFile = File(entity.partFilePath)
        val finalFile = File(entity.finalFilePath)
        finalFile.parentFile?.mkdirs()
        if (finalFile.exists()) {
            // Pre-existing file from a previous failed half-rename;
            // remove so the rename can succeed.
            runCatching { finalFile.delete() }
        }
        if (!partFile.renameTo(finalFile)) {
            // Cross-fs (rare on internal storage) — fall back to a copy.
            partFile.inputStream().use { input ->
                finalFile.outputStream().use { output ->
                    input.copyTo(output)
                }
            }
            partFile.delete()
        }
    }

    private suspend fun markCompleted(id: String) {
        val current = dao.getById(id) ?: return
        dao.upsert(
            current.copy(
                status = DownloadEntity.Status.COMPLETED,
                downloadedBytes = if (current.totalBytes > 0) {
                    current.totalBytes
                } else current.downloadedBytes,
                errorMessage = null,
                updatedAt = nowMillis(),
            )
        )
    }

    private suspend fun markFailed(id: String, message: String) {
        val current = dao.getById(id) ?: return
        if (current.status == DownloadEntity.Status.PAUSED ||
            current.status == DownloadEntity.Status.CANCELLED
        ) return
        dao.upsert(
            current.copy(
                status = DownloadEntity.Status.FAILED,
                errorMessage = message,
                updatedAt = nowMillis(),
            )
        )
    }

    /**
     * Resolve `<scanDir>/<system>/<filename>` and the matching `.part`
     * sibling.  When [cdGamesPerContentFolder] is on (and the system is
     * a CD-based one in [CD_FOLDER_SYSTEMS]) the file is wrapped in a
     * per-game subfolder named after the filename stem, so multi-track
     * layouts (PS1 .cue+.bin pairs, Saturn, Sega CD, Dreamcast) stay
     * grouped.  When off, the file lands flat under the system folder.
     *
     * The resolved paths are persisted into the DownloadEntity at
     * enqueue time, so changing the toggle mid-download has no effect on
     * an in-flight download — that one keeps using its original path so
     * resume / retry remain consistent.
     */
    private fun resolveTargetFiles(
        romScanDir: String,
        system: String,
        filename: String,
        romDirOverrides: Map<String, String> = emptyMap(),
        cdGamesPerContentFolder: Boolean = false,
    ): Pair<File, File> {
        val outDir = InstalledRomsScanner.resolveRomTargetDir(
            scanRoot = File(romScanDir),
            system = system,
            romDirOverrides = romDirOverrides,
        )
        val canonicalSystem = SystemAliases.normalizeSystemCode(system)
        val finalDir = if (cdGamesPerContentFolder && canonicalSystem in CD_FOLDER_SYSTEMS) {
            val stem = filename.substringBeforeLast('.', filename)
            File(outDir, stem)
        } else {
            outDir
        }
        val finalFile = File(finalDir, filename)
        val partFile = File(finalDir, "$filename.part")
        return finalFile to partFile
    }

    /**
     * Start the foreground service if any download needs to keep running,
     * stop it once the queue drains.  Idempotent — safe to call from any
     * status transition.
     */
    private suspend fun refreshForegroundService() {
        val anyActive = dao.getActive().any {
            it.status == DownloadEntity.Status.DOWNLOADING ||
                it.status == DownloadEntity.Status.QUEUED
        }
        if (anyActive && !foregroundActive) {
            foregroundActive = true
            try {
                DownloadForegroundService.start(appContext)
            } catch (e: Exception) {
                // Foreground service start can fail if the app is in the
                // background on Android 12+ without the right exemption —
                // the download still runs on appScope, just without OS
                // protection.  Log and move on.
                Log.w(TAG, "Could not start DownloadForegroundService", e)
                foregroundActive = false
            }
        } else if (!anyActive && foregroundActive) {
            foregroundActive = false
            // The service watches the DAO too, so it stops itself; this
            // call is a belt-and-braces request.
            try {
                DownloadForegroundService.stop(appContext)
            } catch (_: Exception) {
                // Ignore — the self-stop on service side will catch up.
            }
        }
    }

    companion object {
        // CD systems share the layout convention with SyncEngine — duplicated
        // here so DownloadManager stays a leaf module (no SyncEngine dep).
        private val CD_FOLDER_SYSTEMS: Set<String> = setOf("SEGACD", "SAT", "DC", "PS1")
    }
}
