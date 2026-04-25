package com.savesync.android

import android.app.Application
import android.app.NotificationChannel
import android.app.NotificationManager
import android.os.Build
import com.savesync.android.api.ApiClient
import com.savesync.android.storage.AppDatabase
import com.savesync.android.storage.SettingsStore
import com.savesync.android.sync.DownloadManager
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.launch

class SaveSyncApp : Application() {

    lateinit var database: AppDatabase
        private set

    /** Process-wide coroutine scope.  Used by [DownloadManager] so big
     *  ROM downloads outlive ViewModel teardown / activity recreation. */
    val appScope: CoroutineScope = CoroutineScope(SupervisorJob() + Dispatchers.IO)

    /** Singleton ROM download manager. */
    lateinit var downloadManager: DownloadManager
        private set

    override fun onCreate() {
        super.onCreate()
        instance = this
        database = AppDatabase.getInstance(this)
        // Notification channels must exist BEFORE the DownloadManager
        // can ever try to start its foreground service, otherwise the
        // service throws on Android 8+ when looking up the channel id.
        createNotificationChannels()
        // Pass the application context so the manager can hand it to
        // DownloadForegroundService.start() — required to keep the
        // process alive during multi-GB ROM transfers.
        downloadManager = DownloadManager(
            appContext = applicationContext,
            dao = database.downloadDao(),
            appScope = appScope,
        )

        // Restore settings from external backup if DataStore was wiped (e.g. after reinstall)
        appScope.launch {
            SettingsStore(this@SaveSyncApp).restoreFromBackupIfNeeded()
        }

        // Re-attach to any download that was running when the process died.
        // We reach into SettingsStore for the current API config because a
        // resumed download needs the same Retrofit client the user
        // originally enqueued with.
        appScope.launch {
            try {
                val settings = SettingsStore(this@SaveSyncApp).settingsFlow.first()
                if (settings.serverUrl.isNotBlank()) {
                    val api = ApiClient.create(settings.serverUrl, settings.apiKey)
                    downloadManager.resumeInterrupted(api)
                }
            } catch (_: Exception) {
                // Best-effort — the Downloads tab will surface paused rows
                // and let the user resume manually.
            }
        }
    }

    private fun createNotificationChannels() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val notificationManager = getSystemService(NotificationManager::class.java)

            // Sync background notifications
            val syncChannel = NotificationChannel(
                CHANNEL_SYNC_ID,
                getString(R.string.channel_sync_name),
                NotificationManager.IMPORTANCE_DEFAULT,
            ).apply {
                description = getString(R.string.channel_sync_desc)
            }
            notificationManager.createNotificationChannel(syncChannel)

            // Foreground download notifications — LOW importance so the
            // persistent banner doesn't make noise on every progress update.
            val downloadChannel = NotificationChannel(
                CHANNEL_DOWNLOADS_ID,
                getString(R.string.channel_downloads_name),
                NotificationManager.IMPORTANCE_LOW,
            ).apply {
                description = getString(R.string.channel_downloads_desc)
                setShowBadge(false)
            }
            notificationManager.createNotificationChannel(downloadChannel)
        }
    }

    companion object {
        lateinit var instance: SaveSyncApp
            private set

        const val CHANNEL_SYNC_ID = "save_sync_channel"
        const val CHANNEL_DOWNLOADS_ID = "save_sync_downloads"
    }
}
