package com.savesync.android.storage

import android.content.Context
import android.os.Environment
import androidx.datastore.core.DataStore
import androidx.datastore.preferences.core.Preferences
import androidx.datastore.preferences.core.booleanPreferencesKey
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.intPreferencesKey
import androidx.datastore.preferences.core.stringPreferencesKey
import androidx.datastore.preferences.preferencesDataStore
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.map
import org.json.JSONObject
import java.io.File
import java.util.UUID

data class Settings(
    val serverUrl: String = "",
    val apiKey: String = "",
    val autoSyncEnabled: Boolean = false,
    val autoSyncIntervalMinutes: Int = 15,
    val consoleId: String = "",
    /** Directory whose subfolders are scanned for ROMs by system name (e.g. /sdcard/Isos) */
    val romScanDir: String = ""
)

val Context.dataStore: DataStore<Preferences> by preferencesDataStore(name = "save_sync_settings")

class SettingsStore(private val context: Context) {

    private object Keys {
        val SERVER_URL = stringPreferencesKey("server_url")
        val API_KEY = stringPreferencesKey("api_key")
        val AUTO_SYNC_ENABLED = booleanPreferencesKey("auto_sync_enabled")
        val AUTO_SYNC_INTERVAL = intPreferencesKey("auto_sync_interval_minutes")
        val CONSOLE_ID = stringPreferencesKey("console_id")
        val ROM_SCAN_DIR = stringPreferencesKey("rom_scan_dir")
        /** Tracks whether we've already attempted to restore from external backup */
        val BACKUP_RESTORED = booleanPreferencesKey("backup_restored")
    }

    /**
     * Backup file stored on external storage — survives full app uninstall/reinstall.
     * Location: /sdcard/SaveSync/config.json
     */
    private val backupFile: File
        get() = File(Environment.getExternalStorageDirectory(), "SaveSync/config.json")

    val settingsFlow: Flow<Settings> = context.dataStore.data.map { prefs ->
        val consoleId = prefs[Keys.CONSOLE_ID] ?: run {
            UUID.randomUUID().toString().replace("-", "").uppercase()
        }
        Settings(
            serverUrl = prefs[Keys.SERVER_URL] ?: "",
            apiKey = prefs[Keys.API_KEY] ?: "",
            autoSyncEnabled = prefs[Keys.AUTO_SYNC_ENABLED] ?: false,
            autoSyncIntervalMinutes = prefs[Keys.AUTO_SYNC_INTERVAL] ?: 15,
            consoleId = consoleId,
            romScanDir = prefs[Keys.ROM_SCAN_DIR] ?: ""
        )
    }

    /**
     * Called once on app startup. If DataStore has no server URL yet, tries to restore
     * settings from the external backup file (survives uninstall).
     */
    suspend fun restoreFromBackupIfNeeded() {
        val prefs = context.dataStore.data.first()
        // Already has settings — nothing to do
        if (!prefs[Keys.SERVER_URL].isNullOrBlank()) return
        // Already attempted a restore this install — don't loop
        if (prefs[Keys.BACKUP_RESTORED] == true) return

        val backup = readBackupFile() ?: return

        // Mark that we've attempted a restore so we don't loop on genuine first-launch
        context.dataStore.edit { it[Keys.BACKUP_RESTORED] = true }

        // Write all fields from the backup file into DataStore
        context.dataStore.edit { p ->
            backup.serverUrl.takeIf { it.isNotBlank() }?.let { p[Keys.SERVER_URL] = it }
            backup.apiKey.takeIf { it.isNotBlank() }?.let { p[Keys.API_KEY] = it }
            p[Keys.AUTO_SYNC_ENABLED] = backup.autoSyncEnabled
            p[Keys.AUTO_SYNC_INTERVAL] = backup.autoSyncIntervalMinutes
            backup.consoleId.takeIf { it.isNotBlank() }?.let { p[Keys.CONSOLE_ID] = it }
            backup.romScanDir.takeIf { it.isNotBlank() }?.let { p[Keys.ROM_SCAN_DIR] = it }
        }
    }

    suspend fun updateSettings(
        serverUrl: String? = null,
        apiKey: String? = null,
        autoSyncEnabled: Boolean? = null,
        autoSyncIntervalMinutes: Int? = null,
        romScanDir: String? = null
    ) {
        context.dataStore.edit { prefs ->
            serverUrl?.let { prefs[Keys.SERVER_URL] = it }
            apiKey?.let { prefs[Keys.API_KEY] = it }
            autoSyncEnabled?.let { prefs[Keys.AUTO_SYNC_ENABLED] = it }
            autoSyncIntervalMinutes?.let { prefs[Keys.AUTO_SYNC_INTERVAL] = it }
            romScanDir?.let { prefs[Keys.ROM_SCAN_DIR] = it }
        }
        // Mirror to the external backup file every time settings are saved
        writeBackupFile()
    }

    suspend fun ensureConsoleId(): String {
        var id = ""
        context.dataStore.edit { prefs ->
            val existing = prefs[Keys.CONSOLE_ID]
            if (existing.isNullOrEmpty()) {
                id = UUID.randomUUID().toString().replace("-", "").uppercase()
                prefs[Keys.CONSOLE_ID] = id
            } else {
                id = existing
            }
        }
        return id
    }

    // ── Backup helpers ────────────────────────────────────────────────────────

    private suspend fun writeBackupFile() {
        try {
            val current = settingsFlow.first()
            val json = JSONObject().apply {
                put("server_url", current.serverUrl)
                put("api_key", current.apiKey)
                put("auto_sync_enabled", current.autoSyncEnabled)
                put("auto_sync_interval_minutes", current.autoSyncIntervalMinutes)
                put("console_id", current.consoleId)
                put("rom_scan_dir", current.romScanDir)
            }
            val file = backupFile
            file.parentFile?.mkdirs()
            file.writeText(json.toString(2))
        } catch (_: Exception) {
            // Backup is best-effort; never crash the app
        }
    }

    private fun readBackupFile(): Settings? {
        return try {
            val file = backupFile
            if (!file.exists()) return null
            val json = JSONObject(file.readText())
            Settings(
                serverUrl = json.optString("server_url", ""),
                apiKey = json.optString("api_key", ""),
                autoSyncEnabled = json.optBoolean("auto_sync_enabled", false),
                autoSyncIntervalMinutes = json.optInt("auto_sync_interval_minutes", 15),
                consoleId = json.optString("console_id", ""),
                romScanDir = json.optString("rom_scan_dir", "")
            )
        } catch (_: Exception) {
            null
        }
    }
}
