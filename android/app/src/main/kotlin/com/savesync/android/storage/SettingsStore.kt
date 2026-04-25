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
import com.savesync.android.sync.SaturnSyncFormat
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
    val romScanDir: String = "",
    /** Path to the Dolphin GC memory card root (e.g. /sdcard/dolphin-mmjr/GC).
     *  Leave empty to use the default dolphin-mmjr path on internal storage. */
    val dolphinMemCardDir: String = "",
    /** Emudeck root folder; supported emulator saves live under <root>/storage/<Emulator>. */
    val emudeckDir: String = "",
    /**
     * Per-system ROM folder overrides: system code → absolute directory path.
     * Overrides the auto-detected subfolder of [romScanDir] for a given system.
     * e.g. "SAT" → "/sdcard/ROMs/Saturn"
     */
    val romDirOverrides: Map<String, String> = emptyMap(),
    /**
     * Per-emulator save folder overrides: emulator key → absolute directory path.
     * Each emulator's path resolver checks this map first before falling back
     * to its built-in auto-detection logic.  Keys match the
     * ``EMULATOR_KEY`` constant on each emulator's companion object
     * (``RetroArch``, ``PPSSPP``, ``DuckStation``, ``DraStic``, ``melonDS``,
     * ``mGBA``, ``Dolphin``, ``AetherSX2``, ``Azahar``).
     *
     * The legacy [dolphinMemCardDir] field is migrated into this map under
     * key ``Dolphin`` on first read so existing installs keep working.
     */
    val saveDirOverrides: Map<String, String> = emptyMap(),
    val saturnSyncFormat: SaturnSyncFormat = SaturnSyncFormat.MEDNAFEN,
    /**
     * Mirrors RetroArch's "Sort Saves into Folders by Core Name" toggle for the
     * Beetle Saturn (Mednafen) core specifically. When true (default), new
     * Saturn .bkr downloads land under saves/Beetle Saturn/<rom>.bkr. When
     * false, they land directly in saves/<rom>.bkr.  Existing saves on disk
     * are still discovered in either location regardless of this toggle.
     *
     * TODO(per-core refactor): generalise to Map<CoreName, Boolean> once the
     * settings UI grows a per-core list.
     */
    val beetleSaturnPerCoreFolder: Boolean = true,
    /**
     * Mirrors RetroArch's "Sort Saves into Folders by Content Directory"
     * toggle, scoped to CD-based systems (PS1, PS2, Saturn, Sega CD, Dreamcast,
     * PC Engine, Neo Geo CD).  When true, predicted save downloads for these
     * systems land in a per-game subfolder (e.g.
     * saves/Grandia/Grandia (Disc 1).bkr) so multi-disc titles stay grouped.
     * When false (default), they land at the saves root like before.
     * Existing saves on disk are still discovered in either layout.
     *
     * Saturn YabaSanshiro is exempt because it uses a single shared
     * backup.bin container, not a per-game file.
     */
    val cdGamesPerContentFolder: Boolean = false
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
        val DOLPHIN_MEM_CARD_DIR = stringPreferencesKey("dolphin_mem_card_dir")
        val EMUDECK_DIR = stringPreferencesKey("emudeck_dir")
        val ROM_DIR_OVERRIDES = stringPreferencesKey("rom_dir_overrides")
        val SAVE_DIR_OVERRIDES = stringPreferencesKey("save_dir_overrides")
        val SATURN_SYNC_FORMAT = stringPreferencesKey("saturn_sync_format")
        val BEETLE_SATURN_PER_CORE_FOLDER = booleanPreferencesKey("beetle_saturn_per_core_folder")
        val CD_GAMES_PER_CONTENT_FOLDER = booleanPreferencesKey("cd_games_per_content_folder")
        /** Tracks whether we've already attempted to restore from external backup */
        val BACKUP_RESTORED = booleanPreferencesKey("backup_restored")
        /** Remembered UI filter state */
        val LAST_SYSTEM_FILTER = stringPreferencesKey("last_system_filter")
        val LAST_STATUS_FILTER = stringPreferencesKey("last_status_filter")
    }

    /**
     * Backup file stored on external storage — survives full app uninstall/reinstall.
     * New location: /sdcard/GameSync/config.json
     * Legacy location: /sdcard/SaveSync/config.json
     */
    private val backupFile: File
        get() = File(Environment.getExternalStorageDirectory(), "GameSync/config.json")

    private val legacyBackupFile: File
        get() = File(Environment.getExternalStorageDirectory(), "SaveSync/config.json")

    val settingsFlow: Flow<Settings> = context.dataStore.data.map { prefs ->
        val consoleId = prefs[Keys.CONSOLE_ID] ?: run {
            UUID.randomUUID().toString().replace("-", "").uppercase()
        }
        val rawSaveDirOverrides = parseOverrides(prefs[Keys.SAVE_DIR_OVERRIDES] ?: "")
        val legacyDolphinMemCardDir = prefs[Keys.DOLPHIN_MEM_CARD_DIR] ?: ""
        // Migrate the legacy single-emulator dolphinMemCardDir into the
        // per-emulator overrides map so the new EmulatorsScreen and the
        // refactored DolphinEmulator both see it.  We only fold it in
        // here (in the read flow) — the on-disk migration happens the
        // next time the user saves a save-dir override from the UI.
        val effectiveSaveDirOverrides = if (
            legacyDolphinMemCardDir.isNotBlank() &&
            rawSaveDirOverrides["Dolphin"].isNullOrBlank()
        ) {
            rawSaveDirOverrides + ("Dolphin" to legacyDolphinMemCardDir)
        } else {
            rawSaveDirOverrides
        }
        Settings(
            serverUrl = prefs[Keys.SERVER_URL] ?: "",
            apiKey = prefs[Keys.API_KEY] ?: "",
            autoSyncEnabled = prefs[Keys.AUTO_SYNC_ENABLED] ?: false,
            autoSyncIntervalMinutes = prefs[Keys.AUTO_SYNC_INTERVAL] ?: 15,
            consoleId = consoleId,
            romScanDir = prefs[Keys.ROM_SCAN_DIR] ?: "",
            dolphinMemCardDir = legacyDolphinMemCardDir,
            emudeckDir = prefs[Keys.EMUDECK_DIR] ?: "",
            romDirOverrides = parseOverrides(prefs[Keys.ROM_DIR_OVERRIDES] ?: ""),
            saveDirOverrides = effectiveSaveDirOverrides,
            saturnSyncFormat = SaturnSyncFormat.fromWireValue(prefs[Keys.SATURN_SYNC_FORMAT]),
            beetleSaturnPerCoreFolder = prefs[Keys.BEETLE_SATURN_PER_CORE_FOLDER] ?: true,
            cdGamesPerContentFolder = prefs[Keys.CD_GAMES_PER_CONTENT_FOLDER] ?: false
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
            backup.dolphinMemCardDir.takeIf { it.isNotBlank() }?.let { p[Keys.DOLPHIN_MEM_CARD_DIR] = it }
            backup.emudeckDir.takeIf { it.isNotBlank() }?.let { p[Keys.EMUDECK_DIR] = it }
            if (backup.saveDirOverrides.isNotEmpty()) {
                p[Keys.SAVE_DIR_OVERRIDES] = encodeOverrides(backup.saveDirOverrides)
            }
            p[Keys.SATURN_SYNC_FORMAT] = backup.saturnSyncFormat.wireValue
            p[Keys.BEETLE_SATURN_PER_CORE_FOLDER] = backup.beetleSaturnPerCoreFolder
            p[Keys.CD_GAMES_PER_CONTENT_FOLDER] = backup.cdGamesPerContentFolder
        }
    }

    suspend fun updateSettings(
        serverUrl: String? = null,
        apiKey: String? = null,
        autoSyncEnabled: Boolean? = null,
        autoSyncIntervalMinutes: Int? = null,
        romScanDir: String? = null,
        dolphinMemCardDir: String? = null,
        emudeckDir: String? = null,
        saturnSyncFormat: SaturnSyncFormat? = null,
        beetleSaturnPerCoreFolder: Boolean? = null,
        cdGamesPerContentFolder: Boolean? = null
    ) {
        context.dataStore.edit { prefs ->
            serverUrl?.let { prefs[Keys.SERVER_URL] = it }
            apiKey?.let { prefs[Keys.API_KEY] = it }
            autoSyncEnabled?.let { prefs[Keys.AUTO_SYNC_ENABLED] = it }
            autoSyncIntervalMinutes?.let { prefs[Keys.AUTO_SYNC_INTERVAL] = it }
            romScanDir?.let { prefs[Keys.ROM_SCAN_DIR] = it }
            dolphinMemCardDir?.let { prefs[Keys.DOLPHIN_MEM_CARD_DIR] = it }
            emudeckDir?.let { prefs[Keys.EMUDECK_DIR] = it }
            saturnSyncFormat?.let { prefs[Keys.SATURN_SYNC_FORMAT] = it.wireValue }
            beetleSaturnPerCoreFolder?.let { prefs[Keys.BEETLE_SATURN_PER_CORE_FOLDER] = it }
            cdGamesPerContentFolder?.let { prefs[Keys.CD_GAMES_PER_CONTENT_FOLDER] = it }
        }
        // Mirror to the external backup file every time settings are saved
        writeBackupFile()
    }

    /** Sets or replaces the override directory for a single system. */
    suspend fun setRomDirOverride(system: String, path: String) {
        context.dataStore.edit { prefs ->
            val current = parseOverrides(prefs[Keys.ROM_DIR_OVERRIDES] ?: "")
            prefs[Keys.ROM_DIR_OVERRIDES] = encodeOverrides(current + (system to path))
        }
        writeBackupFile()
    }

    /** Removes the override for a single system (falls back to auto-detected folder). */
    suspend fun clearRomDirOverride(system: String) {
        context.dataStore.edit { prefs ->
            val current = parseOverrides(prefs[Keys.ROM_DIR_OVERRIDES] ?: "")
            prefs[Keys.ROM_DIR_OVERRIDES] = encodeOverrides(current - system)
        }
        writeBackupFile()
    }

    /**
     * Sets or replaces the save-folder override for a single emulator.  Also
     * folds in the legacy [Settings.dolphinMemCardDir] value (now retired)
     * by clearing it when the user explicitly sets ``Dolphin`` here.
     */
    suspend fun setSaveDirOverride(emulatorKey: String, path: String) {
        context.dataStore.edit { prefs ->
            val current = parseOverrides(prefs[Keys.SAVE_DIR_OVERRIDES] ?: "")
            prefs[Keys.SAVE_DIR_OVERRIDES] = encodeOverrides(current + (emulatorKey to path))
            // One-shot migration: once the user has configured Dolphin
            // through the new screen, drop the legacy single-emulator
            // setting so it can't drift out of sync with the map.
            if (emulatorKey == "Dolphin") {
                prefs.remove(Keys.DOLPHIN_MEM_CARD_DIR)
            }
        }
        writeBackupFile()
    }

    /** Removes the override for a single emulator (falls back to auto-detected). */
    suspend fun clearSaveDirOverride(emulatorKey: String) {
        context.dataStore.edit { prefs ->
            val current = parseOverrides(prefs[Keys.SAVE_DIR_OVERRIDES] ?: "")
            prefs[Keys.SAVE_DIR_OVERRIDES] = encodeOverrides(current - emulatorKey)
            if (emulatorKey == "Dolphin") {
                prefs.remove(Keys.DOLPHIN_MEM_CARD_DIR)
            }
        }
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

    // ── UI filter preferences ───────────────────────────────────────────────

    /** Returns the last saved system filter (e.g. "GBA") or "All" if none saved. */
    suspend fun getLastSystemFilter(): String {
        val prefs = context.dataStore.data.first()
        return prefs[Keys.LAST_SYSTEM_FILTER] ?: "All"
    }

    /** Returns the last saved status filter name (e.g. "SYNCED") or null if none. */
    suspend fun getLastStatusFilter(): String? {
        val prefs = context.dataStore.data.first()
        return prefs[Keys.LAST_STATUS_FILTER]
    }

    suspend fun saveFilterPreferences(systemFilter: String, statusFilter: String?) {
        context.dataStore.edit { prefs ->
            prefs[Keys.LAST_SYSTEM_FILTER] = systemFilter
            if (statusFilter != null) {
                prefs[Keys.LAST_STATUS_FILTER] = statusFilter
            } else {
                prefs.remove(Keys.LAST_STATUS_FILTER)
            }
        }
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
                put("dolphin_mem_card_dir", current.dolphinMemCardDir)
                put("emudeck_dir", current.emudeckDir)
                put("rom_dir_overrides", JSONObject(current.romDirOverrides as Map<*, *>))
                put("save_dir_overrides", JSONObject(current.saveDirOverrides as Map<*, *>))
                put("saturn_sync_format", current.saturnSyncFormat.wireValue)
                put("beetle_saturn_per_core_folder", current.beetleSaturnPerCoreFolder)
                put("cd_games_per_content_folder", current.cdGamesPerContentFolder)
            }
            val file = backupFile
            file.parentFile?.mkdirs()
            file.writeText(json.toString(2))
        } catch (_: Exception) {
            // Backup is best-effort; never crash the app
        }
    }

    private fun parseOverrides(json: String): Map<String, String> = try {
        val obj = JSONObject(json.ifBlank { "{}" })
        obj.keys().asSequence().associateWith { obj.getString(it) }
    } catch (_: Exception) { emptyMap() }

    private fun encodeOverrides(map: Map<String, String>): String =
        JSONObject(map as Map<*, *>).toString()

    private fun readBackupFile(): Settings? {
        return try {
            val file = if (backupFile.exists()) backupFile else legacyBackupFile
            if (!file.exists()) return null
            val json = JSONObject(file.readText())
            Settings(
                serverUrl = json.optString("server_url", ""),
                apiKey = json.optString("api_key", ""),
                autoSyncEnabled = json.optBoolean("auto_sync_enabled", false),
                autoSyncIntervalMinutes = json.optInt("auto_sync_interval_minutes", 15),
                consoleId = json.optString("console_id", ""),
                romScanDir = json.optString("rom_scan_dir", ""),
                dolphinMemCardDir = json.optString("dolphin_mem_card_dir", ""),
                emudeckDir = json.optString("emudeck_dir", ""),
                romDirOverrides = parseOverrides(json.optString("rom_dir_overrides", "")),
                saveDirOverrides = parseOverrides(json.optString("save_dir_overrides", "")),
                saturnSyncFormat = SaturnSyncFormat.fromWireValue(
                    json.optString("saturn_sync_format", SaturnSyncFormat.MEDNAFEN.wireValue)
                ),
                beetleSaturnPerCoreFolder = json.optBoolean("beetle_saturn_per_core_folder", true),
                cdGamesPerContentFolder = json.optBoolean("cd_games_per_content_folder", false)
            )
        } catch (_: Exception) {
            null
        }
    }
}
