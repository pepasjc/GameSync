package com.savesync.android.workers

import android.app.NotificationManager
import android.content.Context
import androidx.core.app.NotificationCompat
import androidx.work.CoroutineWorker
import androidx.work.WorkerParameters
import com.savesync.android.R
import com.savesync.android.SaveSyncApp
import com.savesync.android.api.ApiClient
import com.savesync.android.emulators.EmulatorRegistry
import com.savesync.android.storage.SettingsStore
import com.savesync.android.sync.SyncEngine
import kotlinx.coroutines.flow.first

class SyncWorker(
    private val context: Context,
    params: WorkerParameters
) : CoroutineWorker(context, params) {

    override suspend fun doWork(): Result {
        return try {
            // Read settings
            val settingsStore = SettingsStore(context)
            val settings = settingsStore.settingsFlow.first()

            if (settings.serverUrl.isBlank()) {
                postNotification("Sync skipped", "Server URL not configured.")
                return Result.failure()
            }

            // Ensure console ID is persisted
            val consoleId = settingsStore.ensureConsoleId()

            // Create API client
            val api = ApiClient.create(settings.serverUrl, settings.apiKey)

            // Discover saves
            val saves = EmulatorRegistry.discoverAllSaves(
                romScanDir = settings.romScanDir,
                dolphinMemCardDir = settings.dolphinMemCardDir,
                romDirOverrides = settings.romDirOverrides
            )
            if (saves.isEmpty()) {
                postNotification("Sync complete", "No save files found.")
                return Result.success()
            }

            // Run sync
            val db = SaveSyncApp.instance.database
            val engine = SyncEngine(api, db, consoleId)
            val result = engine.sync(saves)

            // Post notification
            val summary = buildString {
                append("Uploaded: ${result.uploaded}, Downloaded: ${result.downloaded}")
                if (result.conflicts.isNotEmpty()) {
                    append(", Conflicts: ${result.conflicts.size}")
                }
                if (result.errors.isNotEmpty()) {
                    append(", Errors: ${result.errors.size}")
                }
            }
            postNotification("Sync complete", summary)

            if (result.errors.isNotEmpty()) Result.failure() else Result.success()
        } catch (e: Exception) {
            postNotification("Sync failed", e.message ?: "Unknown error")
            Result.failure()
        }
    }

    private fun postNotification(title: String, message: String) {
        val notificationManager =
            context.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager

        val notification = NotificationCompat.Builder(context, SaveSyncApp.CHANNEL_SYNC_ID)
            .setContentTitle(title)
            .setContentText(message)
            .setSmallIcon(android.R.drawable.ic_popup_sync)
            .setAutoCancel(true)
            .setPriority(NotificationCompat.PRIORITY_DEFAULT)
            .build()

        notificationManager.notify(NOTIFICATION_ID, notification)
    }

    companion object {
        const val WORK_NAME = "SaveSyncWorker"
        private const val NOTIFICATION_ID = 1001
    }
}
