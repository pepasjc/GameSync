package com.savesync.android.sync

import android.app.Notification
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.IBinder
import android.util.Log
import androidx.core.app.NotificationCompat
import com.savesync.android.MainActivity
import com.savesync.android.R
import com.savesync.android.SaveSyncApp
import com.savesync.android.storage.DownloadEntity
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.flow.launchIn
import kotlinx.coroutines.flow.onEach

/**
 * Foreground service that keeps the process alive while ROM downloads
 * are running.
 *
 * Why this exists — Android aggressively kills "background" processes
 * under memory pressure (and on Android 12+ also when the user swipes
 * the app out of recents).  Our long ROM downloads were getting
 * silently killed mid-stream, surfacing to the user as "the app
 * crashed during my big download".  A foreground service tells the OS
 * "we're doing important work" and forces it to show a sticky
 * notification.  In exchange we get strong execution guarantees.
 *
 * Lifecycle:
 *   * [DownloadManager] starts this service the first time a download
 *     transitions into ``downloading`` / ``queued`` and stops it once
 *     every download is in a terminal state.
 *   * The service itself watches [DownloadManager.observeAll] so the
 *     notification text always reflects the current queue without
 *     needing the manager to push updates.
 */
class DownloadForegroundService : Service() {

    private val scope = CoroutineScope(SupervisorJob())
    private var collectorJob: Job? = null

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        // Promote to foreground IMMEDIATELY — Android requires
        // ``startForeground`` within ~5 s of ``startService`` or the
        // process is killed with ForegroundServiceDidNotStartInTimeException.
        startForegroundCompat(buildNotification(active = 0, primary = null))

        val manager = SaveSyncApp.instance.downloadManager
        collectorJob = manager.observeAll()
            .onEach { downloads -> updateNotification(downloads) }
            .launchIn(scope)
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        // START_STICKY → if the OS kills us we want to be restarted.
        // The DownloadManager.resumeInterrupted hook in Application.onCreate
        // then re-attaches to any in-flight downloads.
        return START_STICKY
    }

    override fun onDestroy() {
        super.onDestroy()
        collectorJob?.cancel()
        scope.cancel()
    }

    // ──────────────────────────────────────────────────────────────────
    // Notification helpers
    // ──────────────────────────────────────────────────────────────────

    private fun updateNotification(downloads: List<DownloadEntity>) {
        val active = downloads.filter {
            it.status == DownloadEntity.Status.DOWNLOADING ||
                it.status == DownloadEntity.Status.QUEUED
        }
        if (active.isEmpty()) {
            // Nothing left to do — let Android tear us down.  detach=true
            // removes the sticky notification cleanly.
            stopForegroundCompat()
            stopSelf()
            return
        }
        val primary = active.firstOrNull { it.status == DownloadEntity.Status.DOWNLOADING }
            ?: active.first()
        val notification = buildNotification(active.size, primary)
        try {
            val mgr = getSystemService(NOTIFICATION_SERVICE)
                as android.app.NotificationManager
            mgr.notify(NOTIFICATION_ID, notification)
        } catch (e: Exception) {
            Log.w(TAG, "Failed to update download notification", e)
        }
    }

    private fun buildNotification(active: Int, primary: DownloadEntity?): Notification {
        val title = when {
            active <= 0 -> "Preparing download…"
            active == 1 -> "Downloading ${primary?.displayName ?: "ROM"}"
            else -> "Downloading $active ROMs"
        }
        val text = primary?.let { entity ->
            val total = entity.totalBytes
            val done = entity.downloadedBytes
            if (total > 0L) {
                val pct = (done.toDouble() / total.toDouble() * 100).toInt()
                "${formatBytesCompact(done)} / ${formatBytesCompact(total)}  ($pct%)"
            } else {
                "${formatBytesCompact(done)} downloaded"
            }
        } ?: ""

        // Tap → open the app on the Downloads tab.
        val intent = Intent(this, MainActivity::class.java).apply {
            flags = Intent.FLAG_ACTIVITY_SINGLE_TOP or Intent.FLAG_ACTIVITY_CLEAR_TOP
        }
        val pi = PendingIntent.getActivity(
            this, 0, intent,
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )

        val builder = NotificationCompat.Builder(this, SaveSyncApp.CHANNEL_DOWNLOADS_ID)
            .setSmallIcon(android.R.drawable.stat_sys_download)
            .setContentTitle(title)
            .setContentText(text)
            .setOngoing(true)
            .setOnlyAlertOnce(true)
            .setPriority(NotificationCompat.PRIORITY_LOW)
            .setContentIntent(pi)
            .setSilent(true)

        if (primary != null && primary.totalBytes > 0L) {
            val pct = ((primary.downloadedBytes.toDouble() /
                primary.totalBytes.toDouble()) * 100).toInt().coerceIn(0, 100)
            builder.setProgress(100, pct, false)
        } else {
            // Indeterminate bar for queued / unknown-size downloads.
            builder.setProgress(0, 0, true)
        }
        return builder.build()
    }

    private fun startForegroundCompat(notification: Notification) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            // Q+ requires the foregroundServiceType bitmask.
            startForeground(
                NOTIFICATION_ID,
                notification,
                ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC,
            )
        } else {
            startForeground(NOTIFICATION_ID, notification)
        }
    }

    private fun stopForegroundCompat() {
        // Service.STOP_FOREGROUND_REMOVE = 1, available since API 24.
        // Reference via the Service class to avoid Kotlin's looser
        // static-member resolution for Java parents.
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.N) {
            stopForeground(Service.STOP_FOREGROUND_REMOVE)
        } else {
            @Suppress("DEPRECATION")
            stopForeground(true)
        }
    }

    companion object {
        private const val TAG = "DownloadFG"
        private const val NOTIFICATION_ID = 4242

        /** Start the service.  Idempotent. */
        fun start(context: Context) {
            val intent = Intent(context, DownloadForegroundService::class.java)
            // ``startForegroundService`` requires Android 8+.  We're at
            // minSdk 29 so it's always available, but keep the contract
            // explicit.
            context.startForegroundService(intent)
        }

        /** Best-effort stop request — the service also self-stops when
         *  the active queue empties, so direct calls here are mostly a
         *  belt-and-braces. */
        fun stop(context: Context) {
            val intent = Intent(context, DownloadForegroundService::class.java)
            context.stopService(intent)
        }
    }
}

private fun formatBytesCompact(bytes: Long): String {
    if (bytes < 1024) return "$bytes B"
    val units = arrayOf("KB", "MB", "GB", "TB")
    var size = bytes.toDouble() / 1024.0
    var u = 0
    while (size >= 1024.0 && u < units.size - 1) {
        size /= 1024.0
        u++
    }
    return "%.1f %s".format(size, units[u])
}
