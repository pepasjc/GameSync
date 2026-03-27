package com.savesync.android

import android.app.Application
import android.app.NotificationChannel
import android.app.NotificationManager
import android.os.Build
import com.savesync.android.storage.AppDatabase
import com.savesync.android.storage.SettingsStore
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.launch

class SaveSyncApp : Application() {

    lateinit var database: AppDatabase
        private set

    private val appScope = CoroutineScope(SupervisorJob() + Dispatchers.IO)

    override fun onCreate() {
        super.onCreate()
        instance = this
        database = AppDatabase.getInstance(this)
        createNotificationChannels()

        // Restore settings from external backup if DataStore was wiped (e.g. after reinstall)
        appScope.launch {
            SettingsStore(this@SaveSyncApp).restoreFromBackupIfNeeded()
        }
    }

    private fun createNotificationChannels() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val channelId = CHANNEL_SYNC_ID
            val channelName = getString(R.string.channel_sync_name)
            val channelDesc = getString(R.string.channel_sync_desc)
            val importance = NotificationManager.IMPORTANCE_DEFAULT
            val channel = NotificationChannel(channelId, channelName, importance).apply {
                description = channelDesc
            }
            val notificationManager = getSystemService(NotificationManager::class.java)
            notificationManager.createNotificationChannel(channel)
        }
    }

    companion object {
        lateinit var instance: SaveSyncApp
            private set

        const val CHANNEL_SYNC_ID = "save_sync_channel"
    }
}
