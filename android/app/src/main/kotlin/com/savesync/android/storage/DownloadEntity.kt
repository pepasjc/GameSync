package com.savesync.android.storage

import androidx.room.Entity
import androidx.room.PrimaryKey

/**
 * One queued / running / finished ROM download.
 *
 * The DownloadManager owns the lifecycle and persists this row as the user
 * pauses, resumes, retries, or cancels.  Holding the state in Room (rather
 * than only in-memory ViewModel state) lets long-running downloads survive
 * Activity recreation, low-memory kills, and explicit user pause / app
 * restart cycles — which is the root cause behind the "app crashed during
 * a long download" reports.
 *
 * `partFilePath` points at the ``<finalFilePath>.part`` file that holds
 * the bytes received so far.  When `bytesDownloaded == totalBytes` the
 * manager renames `.part` to `finalFilePath` and flips the status to
 * [Status.COMPLETED].
 */
@Entity(tableName = "downloads")
data class DownloadEntity(
    @PrimaryKey val id: String,
    val romId: String,
    val system: String,
    val displayName: String,
    val filename: String,
    val partFilePath: String,
    val finalFilePath: String,
    val totalBytes: Long,
    val downloadedBytes: Long,
    val status: String,
    val errorMessage: String?,
    val extractFormat: String?,
    val createdAt: Long,
    val updatedAt: Long,
) {
    /** Stable string constants stored in [status]. */
    object Status {
        const val QUEUED = "queued"
        const val DOWNLOADING = "downloading"
        const val PAUSED = "paused"
        const val COMPLETED = "completed"
        const val FAILED = "failed"
        const val CANCELLED = "cancelled"
    }

    /** True for terminal states — used by the UI to gate retry/cancel buttons. */
    val isTerminal: Boolean
        get() = status == Status.COMPLETED ||
            status == Status.FAILED ||
            status == Status.CANCELLED

    /** Fractional progress in [0f..1f] or null if total size is unknown. */
    val progressFraction: Float?
        get() = if (totalBytes > 0L) {
            (downloadedBytes.toDouble() / totalBytes.toDouble())
                .toFloat()
                .coerceIn(0f, 1f)
        } else null
}
