package com.savesync.android.ui

import android.app.Application
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import androidx.work.Constraints
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.NetworkType
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.WorkManager
import com.savesync.android.SaveSyncApp
import com.savesync.android.api.ApiClient
import com.savesync.android.api.GameNameRequest
import com.savesync.android.api.NormalizeRequest
import com.savesync.android.api.NormalizeRomEntry
import com.savesync.android.api.SaveSyncApi
import com.savesync.android.emulators.EmulatorRegistry
import com.savesync.android.emulators.SaveEntry
import com.savesync.android.emulators.impl.RetroArchEmulator
import com.savesync.android.storage.Settings
import com.savesync.android.storage.SettingsStore
import com.savesync.android.storage.SyncStateEntity
import com.savesync.android.sync.SyncEngine
import com.savesync.android.sync.SyncResult
import com.savesync.android.workers.SyncWorker
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.launch
import java.time.OffsetDateTime
import java.time.format.DateTimeParseException
import java.util.concurrent.TimeUnit

sealed class SyncState {
    object Idle : SyncState()
    object Syncing : SyncState()
    data class Success(val result: SyncResult) : SyncState()
    data class Error(val message: String) : SyncState()
}

sealed class SaveDetailState {
    object Idle : SaveDetailState()
    data class Working(val action: String) : SaveDetailState()
    data class Success(val message: String, val navigateBack: Boolean = false) : SaveDetailState()
    data class Error(val message: String) : SaveDetailState()
}

sealed class NormalizePickerState {
    object Hidden : NormalizePickerState()
    /** Server returned multiple possible canonical names — show picker to user. */
    data class Visible(
        val entry: SaveEntry,
        val options: List<String>,   // sorted: first = recommended (USA-first)
    ) : NormalizePickerState()
}

sealed class ServerMetaState {
    object Idle : ServerMetaState()
    object Loading : ServerMetaState()
    data class Found(
        val hash: String,
        val sizeBytes: Long,
        val timestamp: Long,
        val source: String?
    ) : ServerMetaState()
    object NotFound : ServerMetaState()
    data class Error(val message: String) : ServerMetaState()
}

class MainViewModel(application: Application) : AndroidViewModel(application) {

    private val settingsStore = SettingsStore(application)
    private val db = SaveSyncApp.instance.database

    // Unfiltered combined list of local + server-only saves
    private val _allSaves = MutableStateFlow<List<SaveEntry>>(emptyList())

    // Currently selected system filter ("All" means no filter)
    private val _selectedFilter = MutableStateFlow<String>("All")
    val selectedFilter: StateFlow<String> = _selectedFilter

    // Filtered view of saves, derived from _allSaves + _selectedFilter
    val saves: StateFlow<List<SaveEntry>> = combine(_allSaves, _selectedFilter) { all, filter ->
        if (filter == "All") all else all.filter { it.systemName == filter }
    }.stateIn(viewModelScope, SharingStarted.WhileSubscribed(5_000), emptyList())

    // "All" + distinct sorted system names present in _allSaves
    val availableFilters: StateFlow<List<String>> = _allSaves.map { saves ->
        listOf("All") + saves.map { it.systemName }.distinct().sorted()
    }.stateIn(viewModelScope, SharingStarted.WhileSubscribed(5_000), listOf("All"))

    private val _syncState = MutableStateFlow<SyncState>(SyncState.Idle)
    val syncState: StateFlow<SyncState> = _syncState

    private val _saveDetailState = MutableStateFlow<SaveDetailState>(SaveDetailState.Idle)
    val saveDetailState: StateFlow<SaveDetailState> = _saveDetailState

    private val _normalizePicker = MutableStateFlow<NormalizePickerState>(NormalizePickerState.Hidden)
    val normalizePicker: StateFlow<NormalizePickerState> = _normalizePicker

    private val _retroArchPaths = MutableStateFlow<List<Pair<String, Boolean>>>(emptyList())
    val retroArchPaths: StateFlow<List<Pair<String, Boolean>>> = _retroArchPaths

    /** Map of system name → ROM count found in the ROM scan directory */
    private val _romScanResults = MutableStateFlow<Map<String, Int>>(emptyMap())
    val romScanResults: StateFlow<Map<String, Int>> = _romScanResults

    // Server metadata for the currently open detail screen
    private val _serverMeta = MutableStateFlow<ServerMetaState>(ServerMetaState.Idle)
    val serverMeta: StateFlow<ServerMetaState> = _serverMeta

    /** Room-backed sync state for all titles. Eagerly collected so it's populated
     *  before the first compose frame, eliminating the "?" flash on startup. */
    val syncStateEntities: StateFlow<List<SyncStateEntity>> =
        db.syncStateDao().getAll()
            .stateIn(viewModelScope, SharingStarted.Eagerly, emptyList())

    val settings: StateFlow<Settings> = settingsStore.settingsFlow
        .stateIn(
            scope = viewModelScope,
            started = SharingStarted.WhileSubscribed(5_000),
            initialValue = Settings()
        )

    init {
        scanSaves()
    }

    fun setFilter(system: String) {
        _selectedFilter.value = system
    }

    fun scanSaves() {
        viewModelScope.launch {
            try {
                // Use settingsFlow.first() — not settings.value — so we always read the
                // real persisted DataStore value. settings.value returns the StateFlow's
                // initialValue (empty Settings) until a UI subscriber attaches, which means
                // the init{} scan would always run with romScanDir="" otherwise.
                val currentSettings = settingsStore.settingsFlow.first()
                val overrides = db.savePathOverrideDao().getAll()
                    .associate { it.filePath to it.system }
                val romScanDir = currentSettings.romScanDir
                val rawLocalSaves = EmulatorRegistry.discoverAllSaves(overrides, romScanDir)

                // Discover all ROMs the emulators know about (with expected save paths)
                val allRomEntries = EmulatorRegistry.discoverAllRomEntries(romScanDir)

                val serverOnlySaves: List<SaveEntry>
                val localSaves: List<SaveEntry>

                if (currentSettings.serverUrl.isNotBlank()) {
                    val api = try {
                        ApiClient.create(currentSettings.serverUrl, currentSettings.apiKey)
                    } catch (e: Exception) {
                        _allSaves.value = rawLocalSaves
                        return@launch
                    }

                    // For PS1 saves with slug-based title IDs (e.g. from DuckStation with
                    // game-title-named memory cards, or RetroArch without a ROM scan dir),
                    // call the normalize endpoint to resolve the disc serial from psxdb.
                    // The server now returns a bare product code (e.g. "SCUS94163") for PS1
                    // when a matching entry is found in its psxdb reverse index.
                    val ps1SlugSaves = rawLocalSaves.filter { it.systemName == "PS1" && it.titleId.contains('_') }
                    val resolvedRawSaves = if (ps1SlugSaves.isNotEmpty()) {
                        try {
                            val response = api.normalizeRoms(NormalizeRequest(
                                roms = ps1SlugSaves.map { NormalizeRomEntry(system = "PS1", filename = it.displayName) }
                            ))
                            val serialMap = ps1SlugSaves.indices.associate { i ->
                                val oldId = ps1SlugSaves[i].titleId
                                val newId = response.results.getOrNull(i)?.title_id ?: oldId
                                oldId to newId
                            }.filter { (old, new) -> old != new && !new.contains('_') }
                            if (serialMap.isEmpty()) rawLocalSaves
                            else rawLocalSaves.map { entry ->
                                val resolved = serialMap[entry.titleId]
                                if (resolved != null) entry.copy(titleId = resolved) else entry
                            }
                        } catch (_: Exception) { rawLocalSaves }
                    } else rawLocalSaves

                    // Enrich PPSSPP and PS1 product-code entries with proper game names from server.
                    // PS1 saves resolved via SYSTEM.CNF (RetroArch) or PSone Classic directories
                    // (PPSSPP) already carry product-code title IDs (e.g. SLUS01234) — look them up
                    // just like PSP codes. Slug-based PS1 title IDs (PS1_slug) are skipped.
                    val productCodeEntries = resolvedRawSaves.filter { entry ->
                        entry.systemName == "PPSSPP" ||
                        (entry.systemName == "PS1" && !entry.titleId.contains('_'))
                    }
                    val gameNamePair =
                        if (productCodeEntries.isNotEmpty()) {
                            try {
                                val resp = api.lookupGameNames(GameNameRequest(codes = productCodeEntries.map { it.titleId }))
                                resp.names to (resp.retail_serials ?: emptyMap<String, String>())
                            } catch (_: Exception) { emptyMap<String, String>() to emptyMap<String, String>() }
                        } else emptyMap<String, String>() to emptyMap<String, String>()
                    val productCodeNames = gameNamePair.first
                    val retailSerials = gameNamePair.second

                    // Apply retail serial remapping first (NP* PSone Classic codes → actual disc serials),
                    // then enrich display names. retailSerials maps e.g. "NPUJ00662" → "SLPM86034".
                    val enrichedLocalSaves = resolvedRawSaves.map { entry ->
                        val retailSerial = retailSerials[entry.titleId]
                        val effectiveId = retailSerial ?: entry.titleId
                        val gameName = productCodeNames[effectiveId] ?: productCodeNames[entry.titleId]
                        when {
                            retailSerial != null -> entry.copy(
                                titleId = retailSerial,
                                systemName = "PS1",
                                displayName = gameName ?: entry.displayName,
                                canonicalName = entry.titleId  // keep NP* code as canonical reference
                            )
                            gameName != null && gameName != entry.titleId ->
                                entry.copy(displayName = gameName, canonicalName = entry.titleId)
                            else -> entry
                        }
                    }

                    val serverTitleIds: Set<String> = try {
                        api.getTitles().titles.map { it.title_id }.toSet()
                    } catch (e: Exception) {
                        emptySet()
                    }

                    // Remap local save titleIds to the server's canonical ID when the
                    // systems differ only by alias (e.g. local "GEN_sonic" → server "MD_sonic").
                    // Uses androidToServerSystems (one-to-many) to try ALL possible server
                    // prefixes — e.g. for "GEN" it tries "MD_slug", "GENESIS_slug", "MEGADRIVE_slug"
                    // and picks the first one the server actually has.
                    localSaves = enrichedLocalSaves.map { entry ->
                        val localSys = entry.titleId.substringBefore('_')
                        val slug     = entry.titleId.substringAfter('_')
                        val serverAlias = androidToServerSystems[localSys]
                            ?.map { "${it}_$slug" }
                            ?.firstOrNull { it in serverTitleIds }
                        if (serverAlias != null) entry.copy(titleId = serverAlias) else entry
                    }

                    val localTitleIds = localSaves.map { it.titleId }.toSet()

                    serverOnlySaves = try {
                        val titlesResponse = api.getTitles()

                        // Server titles not present locally (after alias remapping above)
                        val unmatchedServerTitles = titlesResponse.titles
                            .filter { it.title_id !in localTitleIds }

                        val unmatchedServerIds = unmatchedServerTitles.map { it.title_id }.toSet()
                        val canonicalIdMap = buildCanonicalIdMap(
                            romEntries = allRomEntries.values
                                .filter { it.titleId !in unmatchedServerIds },
                            serverTitleIds = unmatchedServerIds,
                            api = api
                        )

                        val effectiveRomEntries = allRomEntries + canonicalIdMap

                        // System alias pass for still-unmatched server titles
                        val aliasMatches = resolveBySystemAliases(
                            unmatchedServerTitles.filter { !effectiveRomEntries.containsKey(it.title_id) },
                            effectiveRomEntries
                        )
                        val fullyEffectiveRomEntries = effectiveRomEntries + aliasMatches

                        unmatchedServerTitles
                            .filter { fullyEffectiveRomEntries.containsKey(it.title_id) }
                            .map { titleInfo ->
                                val romEntry = fullyEffectiveRomEntries[titleInfo.title_id]!!
                                romEntry.copy(
                                    // Normalise the system code to Android's conventions so
                                    // "MD" (desktop) and "GEN" (Android) both show the same chip.
                                    systemName = normalizeSystemCode(
                                        titleInfo.platform ?: titleInfo.system ?: romEntry.systemName
                                    ),
                                    isServerOnly = true,
                                    canonicalName = romEntry.canonicalName
                                        ?: titleInfo.name?.takeIf { it != romEntry.displayName }
                                        ?: titleInfo.game_name?.takeIf { it != romEntry.displayName }
                                )
                            }
                    } catch (e: Exception) {
                        emptyList()
                    }
                } else {
                    localSaves = rawLocalSaves
                    serverOnlySaves = emptyList()
                }

                _allSaves.value = localSaves + serverOnlySaves
            } catch (e: Exception) {
                _allSaves.value = emptyList()
            }
        }
    }

    /**
     * Maps legacy/server-side system codes → Android-side canonical codes.
     * Used to normalise system names for display and for deduplication.
     * The desktop client and older server records use different names for some systems.
     */
    private val serverToAndroidSystem = mapOf(
        // Sega — desktop uses MD/SEGACD, Android uses GEN/SCD
        "MD"        to "GEN",
        "GENESIS"   to "GEN",
        "MEGADRIVE" to "GEN",
        "SEGACD"    to "SCD",
        // Bandai
        "WSWAN"     to "WS",
        "WSWANC"    to "WS",
        // Atari
        "ATARI2600" to "A2600",
        "ATARI7800" to "A7800",
    )

    /**
     * Maps each Android-side system code to ALL possible server-side system codes.
     *
     * Multiple server codes can map to the same Android code (e.g. "MD", "GENESIS", and
     * "MEGADRIVE" all → "GEN"), so a simple [associate] reverse would silently drop all but
     * the last entry.  This one-to-many map avoids that by grouping all variants together,
     * so we try every possible server prefix when remapping a local titleId.
     */
    private val androidToServerSystems: Map<String, List<String>> =
        serverToAndroidSystem.entries.groupBy({ it.value }, { it.key })

    /**
     * Normalises a system code to Android's canonical form for display.
     * "MD" → "GEN", "SEGACD" → "SCD", "WSWAN" → "WS", etc.
     * Codes already in Android form are returned unchanged.
     */
    private fun normalizeSystemCode(system: String) =
        serverToAndroidSystem[system.uppercase()] ?: system

    /**
     * For server titles that couldn't be matched directly, tries to find a local ROM
     * entry using system code aliases (e.g. server "MD_sonic_…" ↔ local "GEN_sonic_…").
     * Returns a map of server_title_id → SaveEntry ready to be merged into effectiveRomEntries.
     */
    private fun resolveBySystemAliases(
        stillUnmatched: List<com.savesync.android.api.TitleInfo>,
        romEntries: Map<String, SaveEntry>
    ): Map<String, SaveEntry> {
        if (stillUnmatched.isEmpty()) return emptyMap()
        val result = mutableMapOf<String, SaveEntry>()
        for (serverTitle in stillUnmatched) {
            val serverId  = serverTitle.title_id
            val serverSys = serverId.substringBefore('_')
            val slug      = serverId.substringAfter('_')
            val androidSys = serverToAndroidSystem[serverSys] ?: continue
            val localEntry = romEntries["${androidSys}_$slug"] ?: continue
            // Re-key the entry under the server's titleId so the look-up succeeds
            result[serverId] = localEntry.copy(titleId = serverId)
        }
        return result
    }

    /**
     * Asks the server to normalize ROM filenames so we can match them against
     * server title_ids — WITHOUT renaming anything on disk or in the UI.
     *
     * Only entries whose current titleId is NOT already in [serverTitleIds] are
     * sent (we only care about finding the bridge between a local ROM and an
     * unmatched server entry).
     *
     * Returns a map of  canonical_title_id → SaveEntry  where the entry keeps
     * its original [SaveEntry.displayName] and [SaveEntry.saveFile] path; only
     * the key (and internal titleId) is updated so server matching works.
     *
     * If the server returns source == "filename" it means no DAT was loaded and
     * the result is just the slug — we still accept it because it may still
     * resolve an unmatched server title.
     */
    private suspend fun buildCanonicalIdMap(
        romEntries: Iterable<SaveEntry>,
        serverTitleIds: Set<String>,
        api: SaveSyncApi
    ): Map<String, SaveEntry> {
        val candidates = romEntries.toList()
        if (candidates.isEmpty()) return emptyMap()

        val requestItems = candidates.map { entry ->
            NormalizeRomEntry(system = entry.systemName, filename = entry.displayName)
        }

        val response = try {
            api.normalizeRoms(NormalizeRequest(roms = requestItems))
        } catch (e: Exception) {
            return emptyMap()
        }

        // index results by original filename for quick lookup
        val normalized = response.results.associateBy { it.original_filename }

        val result = mutableMapOf<String, SaveEntry>()
        for (entry in candidates) {
            val norm = normalized[entry.displayName] ?: continue
            val canonicalId = norm.title_id

            // Only add to the map if this canonical ID is one the server has
            // but we didn't already match — that's the whole point of this call
            if (canonicalId in serverTitleIds) {
                // Keep original displayName and file paths — do NOT rename anything.
                // Store the canonical name separately so the UI can show it as a subtitle.
                result[canonicalId] = entry.copy(
                    titleId = canonicalId,
                    canonicalName = norm.canonical_name.takeIf { it != entry.displayName }
                )
            }
        }
        return result
    }

    fun setSaveSystem(entry: SaveEntry, newSystem: String) {
        viewModelScope.launch {
            val filePath = (entry.saveFile ?: entry.saveDir)?.absolutePath ?: return@launch
            db.savePathOverrideDao().upsert(
                com.savesync.android.storage.SavePathOverrideEntity(
                    filePath = filePath,
                    system = newSystem
                )
            )
            // Re-scan so the list updates with the new system
            scanSaves()
        }
    }

    fun syncNow() {
        viewModelScope.launch {
            val currentSettings = settingsStore.settingsFlow.first()
            if (currentSettings.serverUrl.isBlank()) {
                _syncState.value = SyncState.Error("Server URL not configured")
                return@launch
            }

            _syncState.value = SyncState.Syncing
            try {
                val consoleId = settingsStore.ensureConsoleId()
                val api = ApiClient.create(currentSettings.serverUrl, currentSettings.apiKey)
                val engine = SyncEngine(api, db, consoleId)
                // Only sync local saves (exclude server-only entries)
                val romScanDir = currentSettings.romScanDir
                val allLocalSaves = _allSaves.value.filter { !it.isServerOnly }.ifEmpty {
                    EmulatorRegistry.discoverAllSaves(romScanDir = romScanDir).also { found ->
                        _allSaves.value = found
                    }
                }
                // Respect the active system filter — "All" syncs everything,
                // any other filter scopes the sync to that system only.
                val activeFilter = _selectedFilter.value
                val localSaves = if (activeFilter == "All") allLocalSaves
                                 else allLocalSaves.filter { it.systemName == activeFilter }
                val result = engine.sync(localSaves)
                _syncState.value = SyncState.Success(result)
                // Refresh list so downloaded server-only entries drop the isServerOnly flag
                // and status icons reflect the new sync state
                scanSaves()
            } catch (e: Exception) {
                _syncState.value = SyncState.Error(e.message ?: "Sync failed")
            }
        }
    }

    fun saveSettings(
        serverUrl: String,
        apiKey: String,
        autoSync: Boolean,
        intervalMinutes: Int,
        romScanDir: String = ""
    ) {
        viewModelScope.launch {
            settingsStore.updateSettings(
                serverUrl = serverUrl,
                apiKey = apiKey,
                autoSyncEnabled = autoSync,
                autoSyncIntervalMinutes = intervalMinutes,
                romScanDir = romScanDir
            )
            ApiClient.invalidate()
            scheduleOrCancelAutoSync(autoSync, intervalMinutes)
        }
    }

    private fun scheduleOrCancelAutoSync(enabled: Boolean, intervalMinutes: Int) {
        val workManager = WorkManager.getInstance(getApplication())
        if (enabled) {
            val constraints = Constraints.Builder()
                .setRequiredNetworkType(NetworkType.CONNECTED)
                .build()

            val actualInterval = intervalMinutes.toLong().coerceAtLeast(15L)
            val request = PeriodicWorkRequestBuilder<SyncWorker>(actualInterval, TimeUnit.MINUTES)
                .setConstraints(constraints)
                .build()

            workManager.enqueueUniquePeriodicWork(
                SyncWorker.WORK_NAME,
                ExistingPeriodicWorkPolicy.UPDATE,
                request
            )
        } else {
            workManager.cancelUniqueWork(SyncWorker.WORK_NAME)
        }
    }

    fun resetSyncState() {
        _syncState.value = SyncState.Idle
    }

    fun fetchServerMeta(titleId: String, systemName: String? = null) {
        viewModelScope.launch {
            val currentSettings = settingsStore.settingsFlow.first()
            if (currentSettings.serverUrl.isBlank()) return@launch
            _serverMeta.value = ServerMetaState.Loading
            try {
                val api = ApiClient.create(currentSettings.serverUrl, currentSettings.apiKey)
                val meta = if (systemName == "PS1" && !titleId.contains('_')) {
                    api.getPs1CardMeta(titleId, slot = 0)
                } else {
                    api.getSaveMeta(titleId)
                }
                _serverMeta.value = ServerMetaState.Found(
                    hash = meta.save_hash ?: "—",
                    sizeBytes = meta.save_size ?: 0L,
                    timestamp = parseServerDisplayTimestamp(meta),
                    source = meta.platform
                )
            } catch (e: retrofit2.HttpException) {
                _serverMeta.value = if (e.code() == 404) ServerMetaState.NotFound
                                    else ServerMetaState.Error("HTTP ${e.code()}")
            } catch (e: Exception) {
                _serverMeta.value = ServerMetaState.Error(e.message ?: "Failed")
            }
        }
    }

    private fun parseServerDisplayTimestamp(meta: com.savesync.android.api.SaveMeta): Long {
        meta.server_timestamp?.let { iso ->
            try {
                return OffsetDateTime.parse(iso).toInstant().toEpochMilli()
            } catch (_: DateTimeParseException) {
            }
        }

        val raw = meta.client_timestamp ?: return 0L
        return when {
            raw <= 0L -> 0L
            raw in 946684800000L..4102444800000L -> raw
            raw in 946684800L..4102444800L -> raw * 1000L
            else -> 0L
        }
    }

    fun clearServerMeta() {
        _serverMeta.value = ServerMetaState.Idle
    }

    fun checkRetroArchPaths() {
        viewModelScope.launch {
            _retroArchPaths.value = RetroArchEmulator().retroarchDiagnosticPaths()
        }
    }

    /**
     * Scans the configured ROM directory and reports how many ROMs were found per system.
     * Useful for diagnosing why certain systems are not appearing.
     */
    fun runRomScanDiagnostic(dir: String = "") {
        viewModelScope.launch {
            val effectiveDir = dir.ifBlank { settingsStore.settingsFlow.first().romScanDir }
            if (effectiveDir.isBlank()) {
                _romScanResults.value = mapOf("(no ROM directory set)" to 0)
                return@launch
            }
            val allRoms = EmulatorRegistry.discoverAllRomEntries(romScanDir = effectiveDir)
            // Group by system and count
            _romScanResults.value = if (allRoms.isEmpty()) {
                mapOf("(no ROMs found)" to 0)
            } else {
                allRoms.values
                    .groupBy { it.systemName }
                    .mapValues { (_, entries) -> entries.size }
                    .toSortedMap()
            }
        }
    }

    fun resetDetailState() {
        _saveDetailState.value = SaveDetailState.Idle
    }

    fun syncSave(entry: SaveEntry) {
        viewModelScope.launch {
            val currentSettings = settingsStore.settingsFlow.first()
            if (currentSettings.serverUrl.isBlank()) {
                _saveDetailState.value = SaveDetailState.Error("Server URL not configured")
                return@launch
            }
            _saveDetailState.value = SaveDetailState.Working("sync")
            try {
                val consoleId = settingsStore.ensureConsoleId()
                val api = ApiClient.create(currentSettings.serverUrl, currentSettings.apiKey)
                val engine = SyncEngine(api, db, consoleId)
                val result = engine.sync(listOf(entry))
                val msg = buildString {
                    if (result.uploaded > 0) append("↑ Uploaded to server. ")
                    if (result.downloaded > 0) append("↓ Downloaded from server. ")
                    if (result.conflicts.isNotEmpty()) append("⚠ Conflict detected — use Upload or Download to force. ")
                    if (result.errors.isNotEmpty()) append("✗ Error: ${result.errors.first()}. ")
                    if (result.uploaded == 0 && result.downloaded == 0
                        && result.conflicts.isEmpty() && result.errors.isEmpty()) {
                        append("Already in sync with server.")
                    }
                }
                // Refresh server meta after a sync
                fetchServerMeta(entry.titleId, entry.systemName)
                _saveDetailState.value = SaveDetailState.Success(msg.trim())
            } catch (e: Exception) {
                _saveDetailState.value = SaveDetailState.Error(e.message ?: "Sync failed")
            }
        }
    }

    fun uploadSave(entry: SaveEntry) {
        viewModelScope.launch {
            val currentSettings = settingsStore.settingsFlow.first()
            if (currentSettings.serverUrl.isBlank()) {
                _saveDetailState.value = SaveDetailState.Error("Server URL not configured")
                return@launch
            }
            _saveDetailState.value = SaveDetailState.Working("upload")
            try {
                val consoleId = settingsStore.ensureConsoleId()
                val api = ApiClient.create(currentSettings.serverUrl, currentSettings.apiKey)
                val engine = SyncEngine(api, db, consoleId)
                val ok = engine.uploadSave(entry)
                if (ok) {
                    engine.recordSyncedState(entry)
                    fetchServerMeta(entry.titleId, entry.systemName)
                    scanSaves()  // refresh status icons in the list
                }
                _saveDetailState.value = if (ok)
                    SaveDetailState.Success("↑ Uploaded successfully")
                else
                    SaveDetailState.Error("Upload failed")
            } catch (e: Exception) {
                _saveDetailState.value = SaveDetailState.Error(e.message ?: "Upload failed")
            }
        }
    }

    fun downloadSave(entry: SaveEntry) {
        viewModelScope.launch {
            val currentSettings = settingsStore.settingsFlow.first()
            if (currentSettings.serverUrl.isBlank()) {
                _saveDetailState.value = SaveDetailState.Error("Server URL not configured")
                return@launch
            }
            _saveDetailState.value = SaveDetailState.Working("download")
            try {
                val consoleId = settingsStore.ensureConsoleId()
                val api = ApiClient.create(currentSettings.serverUrl, currentSettings.apiKey)
                val engine = SyncEngine(api, db, consoleId)
                val ok = engine.downloadSave(entry, entry.titleId)
                if (ok) {
                    engine.recordSyncedStateFromFile(entry)
                    fetchServerMeta(entry.titleId, entry.systemName)
                    // Re-scan so the entry moves from "server only" to "local save"
                    scanSaves()
                }
                _saveDetailState.value = if (ok)
                    SaveDetailState.Success(
                        "↓ Downloaded successfully.\n\n" +
                        "⚠ Make sure RetroArch is fully closed before opening the game — " +
                        "if RetroArch auto-loaded a save state when you last closed it, " +
                        "load the game once and use RetroArch's Load State menu to discard it, " +
                        "or delete the .state file from the saves folder."
                    )
                else
                    SaveDetailState.Error("No save found on server")
            } catch (e: Exception) {
                _saveDetailState.value = SaveDetailState.Error(e.message ?: "Download failed")
            }
        }
    }

    fun normalizeRomAndSave(entry: SaveEntry) {
        viewModelScope.launch {
            val currentSettings = settingsStore.settingsFlow.first()
            _saveDetailState.value = SaveDetailState.Working("normalize")
            try {
                val api = ApiClient.create(currentSettings.serverUrl, currentSettings.apiKey)
                val response = api.normalizeRoms(NormalizeRequest(listOf(
                    NormalizeRomEntry(system = entry.systemName, filename = entry.displayName)
                )))
                val norm = response.results.firstOrNull()
                    ?: throw Exception("No result from server")

                _saveDetailState.value = SaveDetailState.Idle

                when (norm.source) {
                    "filename" -> {
                        // No DAT entry found — server just returned the stem as-is.
                        // Don't claim "already canonical"; tell the user no match was found.
                        _saveDetailState.value = SaveDetailState.Error(
                            "No match found in database for \"${entry.displayName}\". " +
                            "Ensure a No-Intro DAT for ${entry.systemName} is loaded on the server."
                        )
                    }
                    "dat_crc32" -> {
                        // Exact CRC32 match — single authoritative answer, no picker needed.
                        if (norm.canonical_name == entry.displayName) {
                            _saveDetailState.value = SaveDetailState.Success("✓ Already using canonical name")
                        } else {
                            applyNormalizationChoice(entry, norm.canonical_name)
                        }
                    }
                    else -> {
                        // "dat_filename" — fuzzy slug match. Always show the picker so the user
                        // can confirm and see all regional/version variants sorted by priority.
                        val options = listOf(norm.canonical_name) + norm.alternatives
                        _normalizePicker.value = NormalizePickerState.Visible(entry, options)
                    }
                }
            } catch (e: Exception) {
                _saveDetailState.value = SaveDetailState.Error(e.message ?: "Normalize failed")
            }
        }
    }

    fun dismissNormalizePicker() {
        _normalizePicker.value = NormalizePickerState.Hidden
    }

    fun applyNormalizationChoice(entry: SaveEntry, chosenName: String) {
        _normalizePicker.value = NormalizePickerState.Hidden
        viewModelScope.launch {
            val currentSettings = settingsStore.settingsFlow.first()
            _saveDetailState.value = SaveDetailState.Working("normalize")
            try {
                if (chosenName == entry.displayName) {
                    // User confirmed the name is already correct (picked from the picker)
                    _saveDetailState.value = SaveDetailState.Success("✓ Already using canonical name")
                    return@launch
                }
                val renamed = when {
                    entry.saveFile?.exists() == true -> {
                        val dest = java.io.File(entry.saveFile.parent, "$chosenName.${entry.saveFile.extension}")
                        !dest.exists() && entry.saveFile.renameTo(dest)
                    }
                    entry.saveDir?.exists() == true -> {
                        val dest = java.io.File(entry.saveDir.parent, chosenName)
                        !dest.exists() && entry.saveDir.renameTo(dest)
                    }
                    else -> {
                        _saveDetailState.value = SaveDetailState.Error("No local save file to rename")
                        return@launch
                    }
                }
                if (!renamed) {
                    _saveDetailState.value = SaveDetailState.Error("Could not rename — destination already exists")
                    return@launch
                }
                // Try to rename ROM in romScanDir
                val romScanDir = currentSettings.romScanDir
                val renamedRom = if (romScanDir.isNotBlank()) {
                    val systemDir = java.io.File(romScanDir, entry.systemName)
                    val romFile = systemDir.listFiles()?.firstOrNull { f ->
                        f.isFile && f.nameWithoutExtension == entry.displayName
                    }
                    if (romFile != null) {
                        val dest = java.io.File(romFile.parent, "$chosenName.${romFile.extension}")
                        !dest.exists() && romFile.renameTo(dest)
                    } else false
                } else false
                scanSaves()
                val msg = buildString {
                    append("✓ Renamed to \"$chosenName\"")
                    if (renamedRom) append("\n✓ ROM also renamed")
                    else if (romScanDir.isNotBlank()) append("\n⚠ ROM not found in ROM directory")
                }
                _saveDetailState.value = SaveDetailState.Success(msg, navigateBack = true)
            } catch (e: Exception) {
                _saveDetailState.value = SaveDetailState.Error(e.message ?: "Normalize failed")
            }
        }
    }
}
