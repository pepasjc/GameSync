package com.savesync.android.ui

import android.app.Application
import android.os.Environment
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
import com.savesync.android.api.RomEntry
import com.savesync.android.catalog.RomCatalogFilter
import com.savesync.android.installed.DeleteResult
import com.savesync.android.installed.InstalledRom
import com.savesync.android.installed.InstalledRomsScanner
import com.savesync.android.api.SaturnArchiveLookupRequest
import com.savesync.android.api.SaturnArchiveLookupResult
import com.savesync.android.api.SaveSyncApi
import com.savesync.android.emulators.EmulatorRegistry
import com.savesync.android.emulators.EmudeckPaths
import com.savesync.android.emulators.SaveEntry
import com.savesync.android.emulators.impl.AzaharEmulator
import com.savesync.android.emulators.impl.DolphinEmulator
import com.savesync.android.emulators.impl.MelonDsEmulator
import com.savesync.android.emulators.impl.PpssppEmulator
import com.savesync.android.emulators.impl.RetroArchEmulator
import com.savesync.android.emulators.impl.DuckStationEmulator
import com.savesync.android.storage.DownloadEntity
import com.savesync.android.storage.Settings
import com.savesync.android.storage.SettingsStore
import com.savesync.android.storage.SyncStateEntity
import com.savesync.android.sync.DownloadManager
import com.savesync.android.sync.HashUtils
import com.savesync.android.sync.SaturnArchiveStateStore
import com.savesync.android.sync.SaturnSyncFormat
import com.savesync.android.sync.SaturnSaveFormatConverter
import com.savesync.android.sync.SyncEngine
import com.savesync.android.sync.SyncResult
import com.savesync.android.workers.SyncWorker
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.io.File
import java.time.OffsetDateTime
import java.time.format.DateTimeParseException
import java.util.concurrent.TimeUnit
import com.savesync.android.emulators.impl.AetherSX2Emulator

sealed class SyncState {
    object Idle : SyncState()
    object Syncing : SyncState()
    data class Success(val result: SyncResult) : SyncState()
    data class Error(val message: String) : SyncState()
}

/**
 * Describes the sync relationship between a local save and the server.
 * Used for filtering and status display in the saves list.
 */
enum class SaveSyncStatus(val label: String) {
    /** Local hash matches server hash — fully synced. */
    SYNCED("Synced"),
    /** Exists locally but never synced / no server copy. */
    LOCAL_ONLY("Local Only"),
    /** Exists on server but not locally. */
    SERVER_ONLY("Server Only"),
    /** Local changed since last sync, server unchanged → needs upload. */
    LOCAL_NEWER("Local Newer"),
    /** Server changed since last sync, local unchanged → needs download. */
    SERVER_NEWER("Server Newer"),
    /** Both sides changed since last sync → conflict. */
    CONFLICT("Conflict"),
    /** No sync state recorded and hashes differ — unknown relationship. */
    UNKNOWN("Not Synced")
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

enum class SaturnArchiveAction {
    SYNC,
    UPLOAD,
}

data class SaturnArchivePickerOption(
    val archiveFamily: String,
    val archiveNames: List<String>,
    val detail: String,
    val preselected: Boolean,
)

sealed class SaturnArchivePickerState {
    object Hidden : SaturnArchivePickerState()
    data class Visible(
        val entry: SaveEntry,
        val action: SaturnArchiveAction,
        val hiddenSelectedArchives: List<String>,
        val options: List<SaturnArchivePickerOption>,
    ) : SaturnArchivePickerState()
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
    private val compactPsCodeRegex = Regex("""^[A-Z]{4}\d{5}$""")
    private val hex16TitleIdRegex = Regex("""^[0-9A-Fa-f]{16}$""")

    private val settingsStore = SettingsStore(application)
    private val db = SaveSyncApp.instance.database
    /** Application-scoped download manager — survives ViewModel teardown.
     *  See [com.savesync.android.sync.DownloadManager] for the rationale. */
    private val downloadManager = SaveSyncApp.instance.downloadManager

    // ── Downloads tab plumbing ─────────────────────────────────────────
    /** Live list of every persisted download row, ordered most-recent-first. */
    val downloads: StateFlow<List<DownloadEntity>> =
        downloadManager.observeAll()
            .stateIn(viewModelScope, SharingStarted.Eagerly, emptyList())

    /** Per-download throttled progress events.  Re-exposed verbatim so the
     *  Downloads screen can collect velocity / sub-second progress without
     *  thrashing Room. */
    val downloadProgressEvents: SharedFlow<DownloadManager.ProgressEvent> =
        downloadManager.progressEvents

    // (Removed _navigateToDownloadsTab one-shot signal: enqueueing a ROM
    // used to auto-jump the user to the Downloads tab, which broke the
    // "queue several games while browsing the catalog" flow.  The
    // snackbar at "Queued <name> — see Downloads tab for progress" is the
    // confirmation feedback now; the user navigates manually if they
    // want to watch progress.)

    // Unfiltered combined list of local + server-only saves
    private val _allSaves = MutableStateFlow<List<SaveEntry>>(emptyList())

    // Currently selected system filter ("All" means no filter)
    private val _selectedFilter = MutableStateFlow<String>("All")
    val selectedFilter: StateFlow<String> = _selectedFilter

    // Search query (filters by display name, canonical name, or title ID)
    private val _searchQuery = MutableStateFlow("")
    val searchQuery: StateFlow<String> = _searchQuery

    // Status filter ("All" means no status filter)
    private val _statusFilter = MutableStateFlow<SaveSyncStatus?>(null)
    val statusFilter: StateFlow<SaveSyncStatus?> = _statusFilter

    private val _saturnArchiveSelectionVersion = MutableStateFlow(0)
    val saturnArchiveSelectionVersion: StateFlow<Int> = _saturnArchiveSelectionVersion

    /** Room-backed sync state for all titles. Eagerly collected so it's populated
     *  before the first compose frame, eliminating the "?" flash on startup. */
    val syncStateEntities: StateFlow<List<SyncStateEntity>> =
        db.syncStateDao().getAll()
            .stateIn(viewModelScope, SharingStarted.Eagerly, emptyList())

    /**
     * Computes the sync status for a save entry by comparing local hash,
     * server hash, and last-synced hash from the Room database.
     *
     * When [cheapOnly] is true, skip the expensive file-hash computation and
     * only return statuses derivable from metadata (isServerOnly, presence of
     * lastSyncedHash). Used during composition to avoid disk I/O on the main thread.
     */
    fun computeSyncStatus(
        entry: SaveEntry,
        syncState: SyncStateEntity?,
        cheapOnly: Boolean = false
    ): SaveSyncStatus {
        val isSharedYabaSanshiroEntry =
            entry.systemName == "SAT" &&
                entry.isServerOnly &&
                entry.saveFile?.name.equals("backup.bin", ignoreCase = true)

        if (isSharedYabaSanshiroEntry && entry.saveFile?.exists() == true) {
            if (syncState?.lastSyncedHash == null) {
                return SaveSyncStatus.LOCAL_ONLY
            }
            if (cheapOnly) return SaveSyncStatus.SYNCED

            val localHash = try {
                val archiveNames = SaturnArchiveStateStore.get(entry.titleId)
                if (archiveNames.isEmpty() || entry.saveFile?.exists() != true) {
                    ""
                } else {
                    val canonical = SaturnSaveFormatConverter.extractCanonical(
                        entry.saveFile.readBytes(),
                        archiveNames
                    )
                    HashUtils.sha256Bytes(canonical)
                }
            } catch (_: Exception) {
                ""
            }

            return if (localHash.isNotEmpty() && localHash != syncState.lastSyncedHash) {
                SaveSyncStatus.LOCAL_NEWER
            } else {
                SaveSyncStatus.SYNCED
            }
        }
        if (entry.isServerOnly) return SaveSyncStatus.SERVER_ONLY

        val lastSyncedHash = syncState?.lastSyncedHash
        if (lastSyncedHash == null) {
            // Never synced
            return SaveSyncStatus.LOCAL_ONLY
        }

        if (cheapOnly) {
            // Cheap mode: we know it was synced at least once; assume SYNCED
            // unless background computation says otherwise.
            return SaveSyncStatus.SYNCED
        }

        // Full mode (called from background coroutine in combine transform)
        val localHash = try { entry.computeHash() } catch (_: Exception) { "" }
        val localChanged = localHash.isNotEmpty() && localHash != lastSyncedHash

        // Without eagerly fetching server meta for every entry, we can only
        // tell "local changed since last sync" vs "matches last sync".
        // The full three-way comparison happens during actual sync.
        return when {
            localChanged -> SaveSyncStatus.LOCAL_NEWER
            else -> SaveSyncStatus.SYNCED
        }
    }

    // Filtered view of saves, derived from _allSaves + _selectedFilter + _searchQuery + _statusFilter + syncStateEntities
    val saves: StateFlow<List<SaveEntry>> = combine(
        _allSaves,
        _selectedFilter,
        _searchQuery,
        _statusFilter,
        syncStateEntities,
        _saturnArchiveSelectionVersion
    ) { args: Array<*> ->
        @Suppress("UNCHECKED_CAST")
        val all = args[0] as List<SaveEntry>
        val systemFilter = args[1] as String
        val query = args[2] as String
        val statusFilter = args[3] as SaveSyncStatus?
        @Suppress("UNCHECKED_CAST")
        val syncEntities = args[4] as List<SyncStateEntity>
        args[5] as Int

        var result = all

        // System filter
        if (systemFilter != "All") {
            result = result.filter { it.systemName == systemFilter }
        }

        // Text search
        if (query.isNotBlank()) {
            val q = query.lowercase().trim()
            result = result.filter { entry ->
                entry.displayName.lowercase().contains(q) ||
                entry.titleId.lowercase().contains(q) ||
                (entry.canonicalName?.lowercase()?.contains(q) == true)
            }
        }

        // Status filter
        if (statusFilter != null) {
            val syncMap = syncEntities.associateBy { it.titleId }
            result = result.filter { entry ->
                computeSyncStatus(entry, syncMap[entry.titleId]) == statusFilter
            }
        }

        result
    }.stateIn(viewModelScope, SharingStarted.WhileSubscribed(5_000), emptyList())

    // "All" + distinct sorted system names present in _allSaves
    val availableFilters: StateFlow<List<String>> = _allSaves.map { saves ->
        listOf("All") + saves.map { it.systemName }.distinct().sorted()
    }.stateIn(viewModelScope, SharingStarted.WhileSubscribed(5_000), listOf("All"))

    // Status filter options that have at least one matching entry
    val availableStatusFilters: StateFlow<List<SaveSyncStatus>> = combine(
        _allSaves, syncStateEntities, _saturnArchiveSelectionVersion
    ) { allSaves, syncEntities, _ ->
        val syncMap = syncEntities.associateBy { it.titleId }
        allSaves.map { computeSyncStatus(it, syncMap[it.titleId]) }
            .distinct()
            .sortedBy { it.ordinal }
    }.stateIn(viewModelScope, SharingStarted.WhileSubscribed(5_000), emptyList())

    private val _syncState = MutableStateFlow<SyncState>(SyncState.Idle)
    val syncState: StateFlow<SyncState> = _syncState

    private val _saveDetailState = MutableStateFlow<SaveDetailState>(SaveDetailState.Idle)
    val saveDetailState: StateFlow<SaveDetailState> = _saveDetailState

    private val _normalizePicker = MutableStateFlow<NormalizePickerState>(NormalizePickerState.Hidden)
    val normalizePicker: StateFlow<NormalizePickerState> = _normalizePicker

    private val _saturnArchivePicker =
        MutableStateFlow<SaturnArchivePickerState>(SaturnArchivePickerState.Hidden)
    val saturnArchivePicker: StateFlow<SaturnArchivePickerState> = _saturnArchivePicker

    private val _retroArchPaths = MutableStateFlow<List<Pair<String, Boolean>>>(emptyList())
    val retroArchPaths: StateFlow<List<Pair<String, Boolean>>> = _retroArchPaths

    /** Map of system name → ROM count found in the ROM scan directory */
    private val _romScanResults = MutableStateFlow<Map<String, Int>>(emptyMap())
    val romScanResults: StateFlow<Map<String, Int>> = _romScanResults

    private val _romAvailable = MutableStateFlow<Set<String>>(emptySet())
    val romAvailable: StateFlow<Set<String>> = _romAvailable

    private val _romsByTitle = MutableStateFlow<Map<String, List<RomEntry>>>(emptyMap())
    val romsByTitle: StateFlow<Map<String, List<RomEntry>>> = _romsByTitle

    sealed class RomDownloadState {
        object Idle : RomDownloadState()
        data class Downloading(val name: String) : RomDownloadState()
        data class Success(val file: java.io.File) : RomDownloadState()
        data class Error(val message: String) : RomDownloadState()
    }

    private val _romDownloadState = MutableStateFlow<RomDownloadState>(RomDownloadState.Idle)
    val romDownloadState: StateFlow<RomDownloadState> = _romDownloadState

    // ── ROM Catalog tab ─────────────────────────────────────────────
    /** Full server ROM catalog, fetched lazily on first tab entry and
     *  refreshable on demand.  Null while loading the first time. */
    private val _romCatalog = MutableStateFlow<List<RomEntry>>(emptyList())
    val romCatalog: StateFlow<List<RomEntry>> = _romCatalog

    private val _romCatalogLoading = MutableStateFlow(false)
    val romCatalogLoading: StateFlow<Boolean> = _romCatalogLoading

    private val _romCatalogLoaded = MutableStateFlow(false)
    val romCatalogLoaded: StateFlow<Boolean> = _romCatalogLoaded

    private val _romCatalogError = MutableStateFlow<String?>(null)
    val romCatalogError: StateFlow<String?> = _romCatalogError

    // ── Installed Games tab ─────────────────────────────────────────
    private val _installedRoms = MutableStateFlow<List<InstalledRom>>(emptyList())
    val installedRoms: StateFlow<List<InstalledRom>> = _installedRoms

    private val _installedRomsLoading = MutableStateFlow(false)
    val installedRomsLoading: StateFlow<Boolean> = _installedRomsLoading

    private val _installedRomsLoaded = MutableStateFlow(false)
    val installedRomsLoaded: StateFlow<Boolean> = _installedRomsLoaded

    sealed class DeleteInstalledState {
        object Idle : DeleteInstalledState()
        data class Success(val rom: InstalledRom, val result: DeleteResult) : DeleteInstalledState()
        data class Error(val rom: InstalledRom, val result: DeleteResult) : DeleteInstalledState()
    }

    private val _deleteInstalledState =
        MutableStateFlow<DeleteInstalledState>(DeleteInstalledState.Idle)
    val deleteInstalledState: StateFlow<DeleteInstalledState> = _deleteInstalledState

    // Server metadata for the currently open detail screen
    private val _serverMeta = MutableStateFlow<ServerMetaState>(ServerMetaState.Idle)
    val serverMeta: StateFlow<ServerMetaState> = _serverMeta

    val settings: StateFlow<Settings> = settingsStore.settingsFlow
        .stateIn(
            scope = viewModelScope,
            started = SharingStarted.WhileSubscribed(5_000),
            initialValue = Settings()
        )

    /**
     * Auto-detected system → folder path, derived by scanning the romScanDir subfolders.
     * Not persisted — refreshed on demand via [detectSystemFolders].
     */
    private val _detectedSystemFolders = MutableStateFlow<Map<String, String>>(emptyMap())
    val detectedSystemFolders: StateFlow<Map<String, String>> = _detectedSystemFolders

    init {
        // Restore persisted filter preferences before scanning
        viewModelScope.launch {
            val lastSystem = settingsStore.getLastSystemFilter()
            val lastStatusName = settingsStore.getLastStatusFilter()
            _selectedFilter.value = lastSystem
            _statusFilter.value = lastStatusName?.let { name ->
                SaveSyncStatus.entries.find { it.name == name }
            }
        }
        scanSaves()
    }

    fun setFilter(system: String) {
        _selectedFilter.value = system
        viewModelScope.launch {
            settingsStore.saveFilterPreferences(system, _statusFilter.value?.name)
        }
    }

    fun setSearchQuery(query: String) {
        _searchQuery.value = query
    }

    fun setStatusFilter(status: SaveSyncStatus?) {
        _statusFilter.value = status
        viewModelScope.launch {
            settingsStore.saveFilterPreferences(_selectedFilter.value, status?.name)
        }
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
                val romScanDir = effectiveRomScanDir(currentSettings)
                val emudeckDir = currentSettings.emudeckDir
                val romDirOverrides = currentSettings.romDirOverrides
                val saveDirOverrides = currentSettings.saveDirOverrides
                val rawLocalSaves = EmulatorRegistry.discoverAllSaves(
                    overrides = overrides,
                    romScanDir = romScanDir,
                    emudeckDir = emudeckDir,
                    romDirOverrides = romDirOverrides,
                    saveDirOverrides = saveDirOverrides,
                    saturnSyncFormat = currentSettings.saturnSyncFormat,
                    beetleSaturnPerCoreFolder = currentSettings.beetleSaturnPerCoreFolder,
                    cdGamesPerContentFolder = currentSettings.cdGamesPerContentFolder
                )

                // Discover all ROMs the emulators know about (with expected save paths)
                val allRomEntries = EmulatorRegistry.discoverAllRomEntries(
                    romScanDir = romScanDir,
                    emudeckDir = emudeckDir,
                    romDirOverrides = romDirOverrides,
                    saveDirOverrides = saveDirOverrides,
                    saturnSyncFormat = currentSettings.saturnSyncFormat,
                    beetleSaturnPerCoreFolder = currentSettings.beetleSaturnPerCoreFolder,
                    cdGamesPerContentFolder = currentSettings.cdGamesPerContentFolder
                )

                val serverOnlySaves: List<SaveEntry>
                val localSaves: List<SaveEntry>

                if (currentSettings.serverUrl.isNotBlank()) {
                    val api = try {
                        ApiClient.create(currentSettings.serverUrl, currentSettings.apiKey)
                    } catch (e: Exception) {
                        _allSaves.value = sortSaves(rawLocalSaves)
                        return@launch
                    }

                    // Some systems have canonical server IDs that are not the default
                    // SYS_slug format we derive locally:
                    // - PS1 uses retail serials like SCUS94163
                    // - 3DS uses 16-char title IDs like 0004000000030800
                    // Ask the normalize endpoint to bridge those slug entries to the
                    // server's canonical ID before we do any matching or syncing.
                    val canonicalSlugSaves = rawLocalSaves.filter {
                        it.titleId.contains('_') &&
                            (it.systemName == "PS1" || it.systemName == "3DS")
                    }
                    val resolvedRawSaves = if (canonicalSlugSaves.isNotEmpty()) {
                        try {
                            val response = api.normalizeRoms(NormalizeRequest(
                                roms = canonicalSlugSaves.map {
                                    NormalizeRomEntry(system = it.systemName, filename = it.displayName)
                                }
                            ))
                            val serialMap = canonicalSlugSaves.indices.associate { i ->
                                val oldId = canonicalSlugSaves[i].titleId
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

                    // Enrich product-code entries with proper game names from server.
                    // PSP, PS1, PS2, SAT, and GC saves resolved via disc headers all carry
                    // compact product-code title IDs (e.g. SLUS01234, SAT_T-12705H).
                    // Slug-based PS1/PS2 IDs (PS1_slug / PS2_slug) are skipped here.
                    val productCodeEntries = resolvedRawSaves.filter { entry ->
                        entry.systemName == "PSP" ||
                        (entry.systemName == "3DS" && hex16TitleIdRegex.matches(entry.titleId)) ||
                        ((entry.systemName == "PS1" || entry.systemName == "PS2") && !entry.titleId.contains('_')) ||
                        (entry.systemName == "SAT" && entry.titleId.startsWith("SAT_")) ||
                        (entry.systemName == "GC" && entry.titleId.startsWith("GC_"))
                    }
                    val gameNameTriple =
                        if (productCodeEntries.isNotEmpty()) {
                            try {
                                val resp = api.lookupGameNames(GameNameRequest(codes = productCodeEntries.map { it.titleId }))
                                Triple(
                                    resp.names,
                                    resp.types,
                                    resp.retail_serials ?: emptyMap<String, String>()
                                )
                            } catch (_: Exception) {
                                Triple(
                                    emptyMap<String, String>(),
                                    emptyMap<String, String>(),
                                    emptyMap<String, String>()
                                )
                            }
                        } else Triple(
                            emptyMap<String, String>(),
                            emptyMap<String, String>(),
                            emptyMap<String, String>()
                        )
                    val productCodeNames = gameNameTriple.first
                    val productCodeTypes = gameNameTriple.second
                    val retailSerials = gameNameTriple.third

                    // Apply retail serial remapping first (NP* PSone Classic codes → actual disc serials),
                    // then enrich display names. retailSerials maps e.g. "NPUJ00662" → "SLPM86034".
                    val enrichedLocalSaves = resolvedRawSaves.map { entry ->
                        val retailSerial = retailSerials[entry.titleId]
                        val effectiveId = retailSerial ?: entry.titleId
                        val gameName = productCodeNames[effectiveId] ?: productCodeNames[entry.titleId]
                        val lookedUpType = productCodeTypes[effectiveId] ?: productCodeTypes[entry.titleId]
                        when {
                            retailSerial != null -> entry.copy(
                                titleId = retailSerial,
                                systemName = "PS1",
                                displayName = gameName ?: entry.displayName,
                                canonicalName = gameName?.takeIf { it != entry.displayName }
                            )
                            entry.systemName == "PSP" && lookedUpType == "PS1" -> entry.copy(
                                systemName = "PS1",
                                displayName = gameName ?: entry.displayName,
                                canonicalName = gameName?.takeIf { it != entry.displayName }
                            )
                            gameName != null && gameName != entry.titleId ->
                                entry.copy(
                                    displayName = gameName,
                                    canonicalName = gameName.takeIf { it != entry.displayName }
                                )
                            else -> entry
                        }
                    }

                    val titlesResponse = try {
                        api.getTitles()
                    } catch (e: Exception) {
                        _allSaves.value = sortSaves(rawLocalSaves)
                        return@launch
                    }
                    val romCatalogByTitle: Map<String, List<RomEntry>> = try {
                        api.getRoms(hasSave = true).roms.groupBy { it.title_id }
                    } catch (_: Exception) {
                        emptyMap()
                    }

                    val ps1ServerIds: Set<String> = try {
                        api.getTitles(consoleType = "PS1").titles.map { it.title_id }.toSet()
                    } catch (_: Exception) {
                        emptySet()
                    }
                    val ps2ServerIds: Set<String> = try {
                        api.getTitles(consoleType = "PS2").titles.map { it.title_id }.toSet()
                    } catch (_: Exception) {
                        emptySet()
                    }
                    val pspServerIds: Set<String> = try {
                        api.getTitles(consoleType = "PSP").titles.map { it.title_id }.toSet()
                    } catch (_: Exception) {
                        emptySet()
                    }
                    val satServerIds: Set<String> = try {
                        api.getTitles(consoleType = "SAT").titles.map { it.title_id }.toSet()
                    } catch (_: Exception) {
                        emptySet()
                    }

                    val serverTitleIds: Set<String> = titlesResponse.titles.map { it.title_id }.toSet()

                    // Remap local save titleIds to the server's canonical ID when the
                    // systems differ only by alias (e.g. local "GEN_sonic" → server "MD_sonic").
                    // Uses androidToServerSystems (one-to-many) to try ALL possible server
                    // prefixes — e.g. for "SEGACD" it tries "SCD_slug"
                    // and picks the first one the server actually has.
                    localSaves = dedupeLocalPs1Entries(
                        reconcilePs1LocalSavesWithRomEntries(
                            enrichedLocalSaves.map { entry ->
                        val localSys = entry.titleId.substringBefore('_')
                        val slug     = entry.titleId.substringAfter('_')
                        val serverAlias = androidToServerSystems[localSys]
                            ?.map { "${it}_$slug" }
                            ?.firstOrNull { it in serverTitleIds }
                        if (serverAlias != null) entry.copy(titleId = serverAlias) else entry
                            },
                            allRomEntries
                        )
                    )

                    val localTitleIds = localSaves.map { it.titleId }.toSet()

                    serverOnlySaves = try {
                        // Server titles not present locally (after alias remapping above)
                        val unmatchedServerTitles = titlesResponse.titles
                            .filter { it.title_id !in localTitleIds }

                        val unmatchedServerIds = unmatchedServerTitles.map { it.title_id }.toSet()
                        val unmatchedTypeLookup = lookupServerTitleTypes(api, unmatchedServerTitles)
                        val normalizedUnmatchedTitles = unmatchedServerTitles.map { titleInfo ->
                            val lookedUpType = unmatchedTypeLookup[titleInfo.title_id]?.first
                            val lookedUpName = unmatchedTypeLookup[titleInfo.title_id]?.second
                            val forcedType = when (titleInfo.title_id) {
                                in ps1ServerIds -> "PS1"
                                in ps2ServerIds -> "PS2"
                                in pspServerIds -> "PSP"
                                in satServerIds -> "SAT"
                                else -> lookedUpType
                            }
                            titleInfo.copy(
                                platform = forcedType ?: titleInfo.platform,
                                game_name = lookedUpName ?: titleInfo.game_name,
                                name = lookedUpName ?: titleInfo.name
                            )
                        }
                        val canonicalIdMap = buildCanonicalIdMap(
                            romEntries = allRomEntries.values
                                .filter { it.titleId !in unmatchedServerIds },
                            serverTitleIds = unmatchedServerIds,
                            api = api
                        )

                        val effectiveRomEntries = allRomEntries + canonicalIdMap

                        // System alias pass for still-unmatched server titles
                        val aliasMatches = resolveBySystemAliases(
                            normalizedUnmatchedTitles.filter { !effectiveRomEntries.containsKey(it.title_id) },
                            effectiveRomEntries
                        )
                        val ps1Matches = buildPs1ServerOnlyMatches(
                            normalizedUnmatchedTitles.filter {
                                !effectiveRomEntries.containsKey(it.title_id) &&
                                !aliasMatches.containsKey(it.title_id)
                            },
                            effectiveRomEntries + aliasMatches
                        )
                        val pspMatches = buildPspServerOnlyMatches(
                            normalizedUnmatchedTitles.filter {
                                !effectiveRomEntries.containsKey(it.title_id) &&
                                !aliasMatches.containsKey(it.title_id) &&
                                !ps1Matches.containsKey(it.title_id)
                            },
                            effectiveRomEntries + aliasMatches + ps1Matches
                        )
                        val fullyEffectiveRomEntries = effectiveRomEntries + aliasMatches + ps1Matches + pspMatches

                        val matchedServerOnly = normalizedUnmatchedTitles
                            .filter { fullyEffectiveRomEntries.containsKey(it.title_id) }
                            .map { titleInfo ->
                                val romEntry = fullyEffectiveRomEntries[titleInfo.title_id]!!
                                val resolvedSystem = normalizeSystemCode(
                                    titleInfo.platform
                                        ?: titleInfo.system
                                        ?: titleInfo.consoleType
                                        ?: romEntry.systemName
                                )
                                val preferredDisplayName =
                                    if (resolvedSystem == "PS1") {
                                        duckStationPs1CardBaseName(
                                            romEntry.canonicalName
                                                ?: titleInfo.game_name
                                                ?: titleInfo.name
                                                ?: romEntry.displayName
                                        )
                                    } else {
                                        romEntry.displayName
                                    }
                                val preferredSaveFile =
                                    if (resolvedSystem == "PS1" && romEntry.saveFile != null) {
                                        val ext = romEntry.saveFile.extension.ifBlank { "mcd" }
                                        File(romEntry.saveFile.parentFile, "${preferredDisplayName}_1.$ext")
                                    } else {
                                        romEntry.saveFile
                                    }
                                romEntry.copy(
                                    // Normalise legacy system codes to canonical form so
                                    // server aliases (e.g. "SCD", "GEN", "WS") map correctly.
                                    systemName = resolvedSystem,
                                    displayName = preferredDisplayName,
                                    saveFile = preferredSaveFile,
                                    isServerOnly = true,
                                    canonicalName = romEntry.canonicalName
                                        ?: titleInfo.game_name?.takeIf { it != preferredDisplayName }
                                        ?: titleInfo.name?.takeIf { it != preferredDisplayName }
                                )
                            }

                        val stillUnanchoredTitles = normalizedUnmatchedTitles
                            .filter { !fullyEffectiveRomEntries.containsKey(it.title_id) }

                        // Some PS1 titles still cannot be anchored to a scanned ROM entry
                        // (missing serial, odd image format, etc.). In that case we still
                        // surface them using a DuckStation-style predicted card filename so
                        // the user can download them and, in many cases, DuckStation will
                        // already pick them up.
                        val ps1ServerOnly = stillUnanchoredTitles
                            .mapNotNull { titleInfo -> buildPs1ServerOnlyEntry(titleInfo) }

                        // PS2 is a special case: AetherSX2 often uses shared default cards
                        // instead of per-game saves, so there may be no local ROM/save-derived
                        // entry to anchor a server-only title. We still surface those saves so
                        // the user can download a per-game card and configure it manually.
                        val ps2ServerOnly = stillUnanchoredTitles
                            .mapNotNull { titleInfo ->
                                buildPs2ServerOnlyEntry(
                                    titleInfo,
                                    currentSettings.saveDirOverrides,
                                    currentSettings.emudeckDir
                                )
                            }

                        // PSP server-only saves don't need a local ROM to be useful: PPSSPP
                        // stores per-title slots at PSP/SAVEDATA/<title_id>/ and the server
                        // gives us the slot name verbatim, so we can predict the path even
                        // when the ROM isn't installed yet.
                        val pspServerOnly = stillUnanchoredTitles
                            .mapNotNull { titleInfo ->
                                buildPspServerOnlyEntry(
                                    titleInfo,
                                    currentSettings.saveDirOverrides,
                                    currentSettings.emudeckDir
                                )
                            }

                        // GC saves live at <dolphinRoot>/GC/<REGION>/Card A/<slot>-<CODE>-*.gci
                        // and the server's title_id (GC_<code>) gives us everything we need
                        // to predict the path. Without this entry GC server-only saves were
                        // invisible because nothing else maps GC to a fallback.
                        val gcServerOnly = stillUnanchoredTitles
                            .mapNotNull { titleInfo ->
                                buildGcServerOnlyEntry(
                                    titleInfo,
                                    currentSettings.saveDirOverrides,
                                    currentSettings.emudeckDir
                                )
                            }

                        val threedssServerOnly = stillUnanchoredTitles
                            .mapNotNull { titleInfo ->
                                build3dsServerOnlyEntry(
                                    titleInfo,
                                    currentSettings.saveDirOverrides,
                                    currentSettings.emudeckDir
                                )
                            }

                        val specialFallbackIds = (ps1ServerOnly + ps2ServerOnly + pspServerOnly + gcServerOnly + threedssServerOnly)
                            .mapTo(mutableSetOf()) { it.titleId }

                        // If a save has no local ROM/save anchor yet, still surface it when the
                        // server can provide the ROM. That lets the user discover the save,
                        // download the ROM, and then rescan to get a concrete save target path.
                        val romCatalogServerOnly = stillUnanchoredTitles
                            .filter { it.title_id !in specialFallbackIds }
                            .mapNotNull { titleInfo ->
                                buildRomCatalogServerOnlyEntry(
                                    titleInfo = titleInfo,
                                    roms = romCatalogByTitle[titleInfo.title_id].orEmpty(),
                                    emudeckDir = currentSettings.emudeckDir
                                )
                            }

                        matchedServerOnly + ps1ServerOnly + ps2ServerOnly + pspServerOnly + gcServerOnly + threedssServerOnly + romCatalogServerOnly
                    } catch (e: Exception) {
                        emptyList()
                    }
                } else {
                    localSaves = rawLocalSaves
                    serverOnlySaves = emptyList()
                }

                _allSaves.value = sortSaves(dedupeAliasedEntries(localSaves + serverOnlySaves))
            } catch (e: Exception) {
                _allSaves.value = emptyList()
            }
        }
    }

    /**
     * Keep the visible save list deterministic across emulator scans and server merges.
     *
     * Without an explicit sort, systems like AetherSX2 can appear to shuffle rows
     * because local filesystem enumeration and server-only merge order are not stable.
     */
    private fun sortSaves(entries: List<SaveEntry>): List<SaveEntry> {
        return entries.sortedWith(
            compareBy<SaveEntry>(
                { it.systemName },
                { it.displayName.lowercase() },
                { it.titleId },
                { it.isServerOnly }
            )
        )
    }

    /**
     * Collapse alias-equivalent IDs such as GEN/GENESIS/MEGADRIVE -> MD so the same
     * logical save does not appear multiple times after local and server merges.
     */
    private fun dedupeAliasedEntries(entries: List<SaveEntry>): List<SaveEntry> {
        return entries
            .groupBy { aliasDedupKey(it) }
            .values
            .map { group ->
                group.minWithOrNull(
                    compareBy<SaveEntry>(
                        { if (it.isServerOnly) 1 else 0 },
                        { if (it.exists()) 0 else 1 },
                        { aliasPrefixPriority(it) },
                        { it.displayName.length },
                        { it.titleId }
                    )
                )!!
            }
    }

    private fun aliasDedupKey(entry: SaveEntry): String {
        val titleId = entry.titleId
        val idx = titleId.indexOf('_')
        if (idx <= 0) return titleId
        val normalizedSystem = normalizeSystemCode(titleId.substring(0, idx))
        val slug = titleId.substring(idx + 1)
        return "${normalizedSystem}_$slug"
    }

    private fun aliasPrefixPriority(entry: SaveEntry): Int {
        val titleId = entry.titleId
        val idx = titleId.indexOf('_')
        if (idx <= 0) return 0
        val prefix = titleId.substring(0, idx)
        return if (prefix == normalizeSystemCode(prefix)) 0 else 1
    }

    /**
     * When we already have a PS1 ROM anchor with a real disc serial, prefer that over
     * weaker local identifiers derived from filename normalization or PSN title-name
     * lookup. This keeps DuckStation cards aligned with the installed disc's retail ID.
     */
    private fun reconcilePs1LocalSavesWithRomEntries(
        entries: List<SaveEntry>,
        romEntries: Map<String, SaveEntry>
    ): List<SaveEntry> {
        val ps1RomEntries = romEntries.values
            .filter { it.systemName == "PS1" && compactPsCodeRegex.matches(it.titleId) }
            .distinctBy { it.titleId }
        if (ps1RomEntries.isEmpty()) return entries

        return entries.map { entry ->
            if (entry.systemName != "PS1") return@map entry
            if (compactPsCodeRegex.matches(entry.titleId) && !entry.titleId.startsWith("NP")) {
                return@map entry
            }

            val entryAnchor = normalizePs1Anchor(entry.displayName)
            if (entryAnchor.isBlank()) return@map entry

            val candidates = ps1RomEntries.filter { romEntry ->
                ps1AnchorsEquivalent(entryAnchor, normalizePs1Anchor(romEntry.displayName))
            }
            val uniqueCandidate = candidates
                .distinctBy { it.titleId }
                .singleOrNull()
                ?: return@map entry

            entry.copy(
                titleId = uniqueCandidate.titleId,
                systemName = "PS1",
                canonicalName = uniqueCandidate.canonicalName
                    ?: entry.canonicalName?.takeUnless { compactPsCodeRegex.matches(it.uppercase()) }
                    ?: uniqueCandidate.displayName.takeIf { it != entry.displayName }
            )
        }
    }

    /**
     * A single DuckStation memory card file should surface as one local PS1 row. When
     * multiple candidate IDs point at the same card, prefer the retail serial over NP*
     * aliases or slug IDs.
     */
    private fun dedupeLocalPs1Entries(entries: List<SaveEntry>): List<SaveEntry> {
        val grouped = entries.groupBy { entry ->
            if (entry.systemName == "PS1" && !entry.isServerOnly && entry.saveFile != null) {
                "PS1:${entry.saveFile.absolutePath.lowercase()}"
            } else null
        }

        val keep = mutableMapOf<String, SaveEntry>()
        for ((key, values) in grouped) {
            if (key == null) continue
            keep[key] = values.minWithOrNull(
                compareBy<SaveEntry>(
                    { ps1LocalEntryPriority(it) },
                    { it.displayName.length },
                    { it.titleId }
                )
            ) ?: continue
        }

        return entries.filter { entry ->
            val key =
                if (entry.systemName == "PS1" && !entry.isServerOnly && entry.saveFile != null) {
                    "PS1:${entry.saveFile.absolutePath.lowercase()}"
                } else null
            key == null || keep[key] == entry
        }
    }

    private fun ps1LocalEntryPriority(entry: SaveEntry): Int {
        val titleId = entry.titleId.uppercase()
        return when {
            compactPsCodeRegex.matches(titleId) && !titleId.startsWith("NP") -> 0
            compactPsCodeRegex.matches(titleId) -> 1
            else -> 2
        }
    }

    /**
     * Maps each Android-side canonical code to every server alias that
     * points at it (e.g. ``MD`` ← ``GENESIS`` / ``MEGADRIVE`` / ``GEN``).
     * Used when remapping a local titleId to probe every possible server
     * prefix.  Delegates to [com.savesync.android.systems.SystemAliases] so
     * the alias table lives in exactly one place.
     */
    private val androidToServerSystems: Map<String, List<String>> =
        com.savesync.android.systems.SystemAliases.CANONICAL_TO_SERVER

    /**
     * Normalises a system code to Android's canonical form for display.
     * "SCD" → "SEGACD", "WS" → "WSWAN", etc.  Codes already in Android
     * form are returned unchanged (case preserved) so equality checks
     * like ``prefix == normalizeSystemCode(prefix)`` still work as a
     * "is this already canonical?" probe.
     */
    private fun normalizeSystemCode(system: String) =
        com.savesync.android.systems.SystemAliases.canonicalOrSelf(system)

    /**
     * Surface a server-only PSP title even when the user hasn't installed
     * the corresponding PPSSPP ROM yet.  PSP saves live in
     * ``PSP/SAVEDATA/<title_id>/`` and the server hands back the full slot
     * name as ``title_id``, so we can predict the directory verbatim.
     *
     * Mirrors [buildPs2ServerOnlyEntry] / [build3dsServerOnlyEntry] — without
     * this fallback, [buildPspServerOnlyMatches] only returns entries that
     * anchored to a local ROM, and the post-anchor filter at line ~698 drops
     * everything else.
     *
     * ``isMultiFile = false`` so [SaveEntry.isPspSlot] returns true and the
     * sync engine takes the PSP-bundle path on download.
     */
    private fun buildPspServerOnlyEntry(
        titleInfo: com.savesync.android.api.TitleInfo,
        saveDirOverrides: Map<String, String>,
        emudeckDir: String
    ): SaveEntry? {
        val system = normalizeSystemCode(
            titleInfo.platform
                ?: titleInfo.system
                ?: titleInfo.consoleType
                ?: ""
        )
        if (system != "PSP") return null

        // Per-emulator override wins over Emudeck. The override path is
        // expected to point at the SAVEDATA root (mirrors PpssppEmulator's
        // own override resolution); we append the title_id as the slot dir.
        val override = saveDirOverrides[PpssppEmulator.EMULATOR_KEY]
            ?.takeIf { it.isNotBlank() }
        val slotDir = if (override != null) {
            File(File(override), titleInfo.title_id)
        } else {
            val pspBase = EmudeckPaths.ppssppRoot(emudeckDir)
                ?: Environment.getExternalStorageDirectory()
            PpssppEmulator.defaultSlotDir(pspBase, titleInfo.title_id) ?: return null
        }

        val displayName = titleInfo.name
            ?: titleInfo.game_name
            ?: titleInfo.title_id

        return SaveEntry(
            titleId = titleInfo.title_id,
            displayName = displayName,
            systemName = "PSP",
            saveFile = null,
            saveDir = slotDir,
            isMultiFile = false,
            isServerOnly = true,
            canonicalName = titleInfo.name?.takeIf { it != displayName }
                ?: titleInfo.game_name?.takeIf { it != displayName }
        )
    }

    private fun buildPs2ServerOnlyEntry(
        titleInfo: com.savesync.android.api.TitleInfo,
        saveDirOverrides: Map<String, String>,
        emudeckDir: String
    ): SaveEntry? {
        val system = normalizeSystemCode(
            titleInfo.platform
                ?: titleInfo.system
                ?: titleInfo.consoleType
                ?: ""
        )
        if (system != "PS2") return null

        // Per-emulator override wins over Emudeck and the auto-detected
        // memcards path.  Without this the user's NetherSX2 override
        // configured in Emulator Configuration would be silently ignored
        // for server-only PS2 entries (the Emudeck-based companion call
        // that follows would resolve a different path first).
        val override = saveDirOverrides[AetherSX2Emulator.EMULATOR_KEY]
            ?.takeIf { it.isNotBlank() }
        val memcardsDir = if (override != null) {
            File(override)
        } else {
            val ps2Base = EmudeckPaths.netherSx2Root(emudeckDir)
                ?: Environment.getExternalStorageDirectory()
            AetherSX2Emulator.findMemcardsDir(
                ps2Base,
                allowNonExistent = emudeckDir.isNotBlank()
            ) ?: return null
        }

        val displayName = titleInfo.name
            ?: titleInfo.game_name
            ?: titleInfo.title_id
        val predictedFile = File(
            memcardsDir,
            "${titleInfo.title_id}_${AetherSX2Emulator.sanitizeServerCardName(displayName)}.ps2"
        )

        return SaveEntry(
            titleId = titleInfo.title_id,
            displayName = displayName,
            systemName = "PS2",
            saveFile = predictedFile,
            saveDir = null,
            isServerOnly = true,
            canonicalName = titleInfo.name?.takeIf { it != displayName }
                ?: titleInfo.game_name?.takeIf { it != displayName }
        )
    }

    /**
     * Surface a server-only GameCube title even when the user hasn't yet
     * created any local saves in Dolphin.  GC saves live at
     * ``<dolphinRoot>/GC/<REGION>/Card A/<slot>-<CODE>-<name>.gci`` and the
     * server's title_id encodes the 4-char product code (``GC_<code>``), so
     * we can predict the file path verbatim — region is inferred from the
     * code's region byte (4th char).
     *
     * Mirrors [buildPs2ServerOnlyEntry] / [buildPspServerOnlyEntry].  Without
     * this fallback, GC server-only saves were never visible because no
     * builder existed and the post-anchor filter at line ~698 dropped them.
     */
    private fun buildGcServerOnlyEntry(
        titleInfo: com.savesync.android.api.TitleInfo,
        saveDirOverrides: Map<String, String>,
        emudeckDir: String
    ): SaveEntry? {
        val system = normalizeSystemCode(
            titleInfo.platform
                ?: titleInfo.system
                ?: titleInfo.consoleType
                ?: ""
        )
        if (system != "GC") return null

        val displayName = titleInfo.name
            ?: titleInfo.game_name
            ?: titleInfo.title_id

        val dolphinOverride = saveDirOverrides[DolphinEmulator.EMULATOR_KEY]
            ?.takeIf { it.isNotBlank() }
            ?: ""
        val saveFile = DolphinEmulator.defaultSaveFile(
            titleId = titleInfo.title_id,
            displayName = displayName,
            dolphinMemCardDir = dolphinOverride,
            dolphinRootDir = EmudeckPaths.dolphinRoot(emudeckDir)
        ) ?: return null

        return SaveEntry(
            titleId = titleInfo.title_id,
            displayName = displayName,
            systemName = "GC",
            saveFile = saveFile,
            saveDir = null,
            isServerOnly = true,
            canonicalName = titleInfo.name?.takeIf { it != displayName }
                ?: titleInfo.game_name?.takeIf { it != displayName }
        )
    }

    private fun build3dsServerOnlyEntry(
        titleInfo: com.savesync.android.api.TitleInfo,
        saveDirOverrides: Map<String, String>,
        emudeckDir: String
    ): SaveEntry? {
        val system = normalizeSystemCode(
            titleInfo.platform
                ?: titleInfo.system
                ?: titleInfo.consoleType
                ?: ""
        )
        if (system != "3DS") return null
        if (!hex16TitleIdRegex.matches(titleInfo.title_id)) return null

        // Per-emulator override wins over Emudeck. The override is expected
        // to be the title root (sdmc/Nintendo 3DS/<id>/<id>/title); we feed
        // it via candidateTitleRoots so defaultSaveDir's findTitleRoot picks
        // it as the highest-priority candidate.
        val override = saveDirOverrides[AzaharEmulator.EMULATOR_KEY]
            ?.takeIf { it.isNotBlank() }
        val saveDir = if (override != null) {
            AzaharEmulator.defaultSaveDir(
                storageBaseDir = Environment.getExternalStorageDirectory(),
                titleId = titleInfo.title_id,
                candidateTitleRoots = listOf(File(override))
            )
        } else {
            AzaharEmulator.defaultSaveDir(
                storageBaseDir = EmudeckPaths.azaharRoot(emudeckDir)
                    ?: Environment.getExternalStorageDirectory(),
                titleId = titleInfo.title_id
            )
        } ?: return null

        val displayName = titleInfo.game_name
            ?: titleInfo.name
            ?: titleInfo.title_id

        return SaveEntry(
            titleId = titleInfo.title_id,
            displayName = displayName,
            systemName = "3DS",
            saveFile = null,
            saveDir = saveDir,
            isMultiFile = true,
            isServerOnly = true,
            canonicalName = titleInfo.game_name?.takeIf { it != displayName }
                ?: titleInfo.name?.takeIf { it != displayName }
        )
    }

    private fun buildRomCatalogServerOnlyEntry(
        titleInfo: com.savesync.android.api.TitleInfo,
        roms: List<RomEntry>,
        emudeckDir: String
    ): SaveEntry? {
        if (roms.isEmpty()) return null

        val preferredRom = roms.minWithOrNull(
            compareBy<RomEntry>(
                { it.filename.length },
                { it.filename.lowercase() }
            )
        ) ?: return null

        val resolvedSystem = normalizeSystemCode(
            titleInfo.platform
                ?: titleInfo.system
                ?: titleInfo.consoleType
                ?: preferredRom.system
        )

        val displayName = titleInfo.game_name
            ?: titleInfo.name
            ?: preferredRom.name
            ?: preferredRom.filename.substringBeforeLast('.')
        val canonicalName = sequenceOf(
            titleInfo.game_name,
            titleInfo.name,
            preferredRom.name
        ).firstOrNull { !it.isNullOrBlank() && it != displayName }

        // Predict a save path so the Download button is usable even without
        // a local ROM.  Without this the user would hit "No local save
        // location is known for this title yet" every time.
        val (predictedFile, predictedDir, isMulti) = predictDefaultSaveTarget(
            resolvedSystem, titleInfo.title_id, displayName, emudeckDir
        )

        return SaveEntry(
            titleId = titleInfo.title_id,
            displayName = displayName,
            systemName = resolvedSystem,
            saveFile = predictedFile,
            saveDir = predictedDir,
            isMultiFile = isMulti,
            isServerOnly = true,
            canonicalName = canonicalName
        )
    }

    /**
     * Predicts where on disk a SERVER_ONLY save for [system] should land when
     * the user doesn't have a local ROM / save yet.  Mirrors the Steam Deck
     * client's generic server-only builder so both clients converge on the
     * same paths, which matters when users sync across devices.
     *
     * Returns ``(saveFile, saveDir, isMultiFile)``.  Returns ``(null, null,
     * false)`` for unsupported systems — the Download button stays disabled
     * and the UI shows "Download the ROM first" instead.
     */
    private fun predictDefaultSaveTarget(
        system: String,
        titleId: String,
        displayName: String,
        emudeckDir: String = ""
    ): Triple<File?, File?, Boolean> {
        val base = Environment.getExternalStorageDirectory()
        // Per-emulator save-dir overrides take precedence over auto-detection
        // (Emudeck, fork-specific paths, etc.). Read directly from the current
        // settings StateFlow so callers don't have to thread the map through.
        val saveDirOverrides = settings.value.saveDirOverrides
        fun overrideFor(key: String): File? = saveDirOverrides[key]
            ?.takeIf { it.isNotBlank() }
            ?.let(::File)
            ?.takeIf { it.exists() && it.isDirectory }

        return when (system.uppercase()) {
            "PSP" -> {
                // PPSSPP stores per-title saves in PSP/SAVEDATA/<title_id>/.
                // Server hands us the full slot name as title_id, so use it
                // verbatim.  isMultiFile=false so isPspSlot kicks in and the
                // sync engine takes the PSP-bundle path.
                val pspOverride = overrideFor(PpssppEmulator.EMULATOR_KEY)
                val slotDir = if (pspOverride != null) {
                    File(pspOverride, titleId)
                } else {
                    PpssppEmulator.defaultSlotDir(
                        EmudeckPaths.ppssppRoot(emudeckDir) ?: base,
                        titleId
                    )
                }
                Triple(null, slotDir, false)
            }
            "NDS" -> {
                // melonDS is the common Android NDS target.  Its scanner
                // reads <stem>.sav from the melonDS/ folder — mirror that so
                // a Download lands where the emulator will look.  SyncEngine
                // creates parent dirs on write, so we don't mkdirs() here.
                val melonDir = overrideFor(MelonDsEmulator.EMULATOR_KEY)
                    ?: listOf("melonDS", "melonDS Android", "melonds")
                        .map { File(base, it) }
                        .firstOrNull { it.exists() && it.isDirectory }
                    ?: File(base, "melonDS")
                val file = File(melonDir, "${sanitizeFilesystemStem(displayName)}.sav")
                Triple(file, null, false)
            }
            "VITA" -> {
                // Vita3K: per-title savedata under ux0/user/00/savedata/<id>/.
                val candidates = listOf(
                    "Vita3K/ux0/user/00/savedata",
                    "vita3k/ux0/user/00/savedata",
                    "Android/data/org.vita3k.emulator/files/ux0/user/00/savedata",
                )
                val root = candidates
                    .map { File(base, it) }
                    .firstOrNull { it.exists() && it.isDirectory }
                    ?: File(base, candidates.first())
                Triple(null, File(root, titleId), true)
            }
            in retroarchSystems -> {
                // RetroArch.defaultSaveFile() doesn't yet support an explicit
                // root override — it auto-detects via cfg / external storage.
                // The emulator instance honours saveDirOverride at scan time,
                // so once a save lands locally it'll be found correctly even
                // if the prediction path differs from the override.
                // TODO(per-emulator predict): plumb the RetroArch override
                // into defaultSaveFile() too.
                val saturnFormat = settings.value.saturnSyncFormat
                val perCore = settings.value.beetleSaturnPerCoreFolder
                val perContent = settings.value.cdGamesPerContentFolder
                val file = RetroArchEmulator.defaultSaveFile(
                    externalStorage = base,
                    system = system,
                    label = displayName,
                    saturnSyncFormat = saturnFormat,
                    beetleSaturnPerCoreFolder = perCore,
                    cdGamesPerContentFolder = perContent,
                )
                Triple(file, null, false)
            }
            else -> Triple(null, null, false)
        }
    }

    /** Strip disc/bracket tags and filesystem-unsafe chars from a display name. */
    private fun sanitizeFilesystemStem(label: String): String {
        return label
            .replace(Regex("""\s*[\(\[]\s*(disc|cd|side)\s*\d+(?:\s*of\s*\d+)?\s*[\)\]]""",
                RegexOption.IGNORE_CASE), "")
            .replace(Regex("""[\\/:*?"<>|]"""), "")
            .replace(Regex("""\s+"""), " ")
            .trim()
            .ifBlank { "game" }
    }

    /** Systems whose server-only saves land as a single .srm/.bkr under the
     *  RetroArch saves directory. */
    private val retroarchSystems = setOf(
        "GBA", "GB", "GBC", "NES", "SNES", "N64",
        "MD", "SEGACD", "SMS", "GG", "32X",
        "DC", "PCE", "LYNX", "NGP", "NGPC", "WSWAN", "WSWANC",
        "NEOGEO", "SAT"
    )

    /**
     * Builds a best-effort DuckStation card path for a server-only PS1 title when no
     * ROM-derived anchor was found. DuckStation usually names per-game cards from the
     * title rather than the serial, and often omits disc numbers, so we mimic that.
     */
    private fun buildPs1ServerOnlyEntry(
        titleInfo: com.savesync.android.api.TitleInfo
    ): SaveEntry? {
        val system = normalizeSystemCode(
            titleInfo.platform
                ?: titleInfo.system
                ?: titleInfo.consoleType
                ?: ""
        )
        if (system != "PS1") return null

        val memcardsDir = DuckStationEmulator.findMemcardsDir(Environment.getExternalStorageDirectory())
            ?: return null

        val displayName = titleInfo.game_name
            ?: titleInfo.name
            ?: titleInfo.title_id
        val predictedBase = duckStationPs1CardBaseName(displayName)
        val predictedFile = File(memcardsDir, "${predictedBase}_1.mcd")

        return SaveEntry(
            titleId = titleInfo.title_id,
            displayName = displayName,
            systemName = "PS1",
            saveFile = predictedFile,
            saveDir = null,
            isServerOnly = true,
            canonicalName = titleInfo.game_name?.takeIf { it != displayName }
                ?: titleInfo.name?.takeIf { it != displayName }
        )
    }

    /**
     * Matches unmatched PS1 server titles against installed DuckStation ROM entries.
     *
     * DuckStation derives per-game memory-card filenames from the ROM/game label, so
     * PS1 server-only downloads must reuse the exact ROM-derived save path whenever
     * possible. We therefore prefer an existing ROM anchor over synthesizing a fresh
     * filename from the server title.
     */
    private fun buildPs1ServerOnlyMatches(
        stillUnmatched: List<com.savesync.android.api.TitleInfo>,
        romEntries: Map<String, SaveEntry>
    ): Map<String, SaveEntry> {
        if (stillUnmatched.isEmpty()) return emptyMap()

        val ps1RomEntries = romEntries.values
            .filter { it.systemName == "PS1" }
            .distinctBy { it.saveFile?.absolutePath ?: it.displayName }
        if (ps1RomEntries.isEmpty()) return emptyMap()

        val byAnchor = ps1RomEntries.groupBy { normalizePs1Anchor(it.displayName) }
        val usedAnchors = romEntries.values
            .filter { it.systemName == "PS1" && compactPsCodeRegex.matches(it.titleId) }
            .mapTo(mutableSetOf()) { normalizePs1Anchor(it.displayName) }
        val result = mutableMapOf<String, SaveEntry>()
        val groupedServerTitles = stillUnmatched
            .filter {
                normalizeSystemCode(
                    it.platform
                        ?: it.system
                        ?: it.consoleType
                        ?: ""
                ) == "PS1"
            }
            .groupBy { normalizePs1Anchor(it.name ?: it.game_name ?: it.title_id) }

        for ((anchor, titlesForAnchor) in groupedServerTitles) {
            if (anchor.isBlank() || anchor in usedAnchors) continue

            val candidates = byAnchor[anchor].orEmpty()
            if (candidates.isEmpty()) continue

            val preferredLocal = candidates.minWithOrNull(
                compareBy<SaveEntry>(
                    { ps1DiscPreference(it.displayName) },
                    { it.displayName.length },
                    { it.displayName.lowercase() }
                )
            ) ?: continue

            val preferredTitle = titlesForAnchor.minWithOrNull(
                compareBy<com.savesync.android.api.TitleInfo>(
                    { ps1ServerTitlePreference(it, preferredLocal.displayName) },
                    { (it.name ?: it.game_name ?: it.title_id).length },
                    { (it.name ?: it.game_name ?: it.title_id).lowercase() }
                )
            ) ?: continue

            result[preferredTitle.title_id] = preferredLocal.copy(
                titleId = preferredTitle.title_id,
                canonicalName = preferredLocal.canonicalName
                    ?: preferredTitle.name?.takeIf { it != preferredLocal.displayName }
                    ?: preferredTitle.game_name?.takeIf { it != preferredLocal.displayName }
            )
            usedAnchors.add(anchor)
        }

        return result
    }

    /**
     * Match PSP server-only saves against installed PPSSPP ROMs.
     *
     * ROM-derived PSP entries give us a save root (PSP/SAVEDATA) before any local save
     * exists. When we find a matching server title, rewrite that anchor to the exact
     * server slot directory name so bundle downloads land where PPSSPP expects them.
     */
    private fun buildPspServerOnlyMatches(
        stillUnmatched: List<com.savesync.android.api.TitleInfo>,
        romEntries: Map<String, SaveEntry>
    ): Map<String, SaveEntry> {
        if (stillUnmatched.isEmpty()) return emptyMap()

        val pspRomEntries = romEntries.values
            .filter { it.saveDir != null && it.systemName == "PSP" }
            .distinctBy { it.saveDir?.absolutePath ?: it.titleId }
        if (pspRomEntries.isEmpty()) return emptyMap()

        val byCode = pspRomEntries
            .mapNotNull { entry -> pspProductCodePrefix(entry.titleId)?.let { it to entry } }
            .groupBy({ it.first }, { it.second })
        val byAnchor = pspRomEntries.groupBy { normalizePspAnchor(it.displayName) }

        val result = mutableMapOf<String, SaveEntry>()
        for (titleInfo in stillUnmatched) {
            val system = normalizeSystemCode(
                titleInfo.platform
                    ?: titleInfo.system
                    ?: titleInfo.consoleType
                    ?: ""
            )
            if (system != "PSP") continue

            val serverId = titleInfo.title_id
            val code = pspProductCodePrefix(serverId)
            val serverName = titleInfo.name ?: titleInfo.game_name ?: serverId

            val anchorEntry = when {
                code != null && !byCode[code].isNullOrEmpty() -> {
                    byCode[code]!!.minWithOrNull(
                        compareBy<SaveEntry>(
                            { if (it.titleId == code) 0 else 1 },
                            { it.displayName.length },
                            { it.displayName.lowercase() }
                        )
                    )
                }
                else -> byAnchor[normalizePspAnchor(serverName)]?.singleOrNull()
            } ?: continue

            val saveRoot = anchorEntry.saveDir?.parentFile ?: continue
            result[serverId] = anchorEntry.copy(
                titleId = serverId,
                saveDir = File(saveRoot, serverId),
                canonicalName = anchorEntry.canonicalName
                    ?: titleInfo.name?.takeIf { it != anchorEntry.displayName }
                    ?: titleInfo.game_name?.takeIf { it != anchorEntry.displayName }
            )
        }

        return result
    }

    private fun ps1ServerTitlePreference(
        titleInfo: com.savesync.android.api.TitleInfo,
        localLabel: String
    ): Int {
        val localRegion = ps1RegionRank(localLabel)
        val serverName = titleInfo.name ?: titleInfo.game_name ?: titleInfo.title_id
        val serverRegion = ps1RegionRank(serverName)
        val isPsnCode = titleInfo.title_id.uppercase().startsWith("NP")

        return when {
            localRegion >= 0 && serverRegion == localRegion && !isPsnCode -> 0
            localRegion >= 0 && serverRegion == localRegion -> 1
            !isPsnCode && serverRegion == 0 -> 2  // USA
            !isPsnCode && serverRegion == 1 -> 3  // Europe
            !isPsnCode && serverRegion == 2 -> 4  // Japan
            !isPsnCode -> 5
            serverRegion == 0 -> 6
            serverRegion == 1 -> 7
            serverRegion == 2 -> 8
            else -> 9
        }
    }

    private fun ps1RegionRank(name: String): Int {
        val lower = name.lowercase()
        return when {
            "usa" in lower || "u.s.a" in lower -> 0
            "europe" in lower || "eur" in lower || "pal" in lower -> 1
            "japan" in lower || "jpn" in lower -> 2
            else -> -1
        }
    }

    /**
     * Normalizes PS1 names to a common base so server titles like "Parasite Eve"
     * can still anchor to local disc labels such as "Parasite Eve (USA) (Disc 1)".
     */
    private fun normalizePs1Anchor(name: String): String {
        val withoutTags = name
            .replace(Regex("""\s*[\(\[][^\)\]]*[\)\]]"""), " ")
            .replace(Regex("""\b(disc|cd)\s*[0-9]+\b""", RegexOption.IGNORE_CASE), " ")
            .replace(Regex("""\b[0-9]+\s*of\s*[0-9]+\b""", RegexOption.IGNORE_CASE), " ")

        return withoutTags
            .lowercase()
            .replace(Regex("[^a-z0-9]+"), " ")
            .trim()
    }

    private fun normalizePspAnchor(name: String): String {
        return name
            .replace(Regex("""\s*[\(\[][^\)\]]*[\)\]]"""), " ")
            .replace(Regex("""\s+"""), " ")
            .lowercase()
            .replace(Regex("[^a-z0-9]+"), " ")
            .trim()
    }

    private fun pspProductCodePrefix(titleId: String): String? {
        if (titleId.length < 9) return null
        val prefix = titleId.take(9).uppercase()
        return prefix.takeIf { compactPsCodeRegex.matches(it) }
    }

    private fun ps1AnchorsEquivalent(left: String, right: String): Boolean {
        if (left.isBlank() || right.isBlank()) return false
        if (left == right) return true

        val longer: String
        val shorter: String
        if (left.length >= right.length) {
            longer = left
            shorter = right
        } else {
            longer = right
            shorter = left
        }

        return shorter.length >= 8 && longer.contains(shorter)
    }

    /**
     * Prefer the plain label or disc 1 when several installed ROMs collapse to the same
     * base title. That gives the first server download the filename DuckStation is most
     * likely already using locally.
     */
    private fun ps1DiscPreference(name: String): Int {
        val lower = name.lowercase()
        return when {
            Regex("""\b(disc|cd)\s*1\b""", RegexOption.IGNORE_CASE).containsMatchIn(lower) -> 0
            !Regex("""\b(disc|cd)\s*[0-9]+\b""", RegexOption.IGNORE_CASE).containsMatchIn(lower) -> 1
            else -> 2
        }
    }

    /**
     * DuckStation commonly names per-game cards from a No-Intro-like title, but without
     * disc markers. Keep region text like "(USA)" intact so the generated filename stays
     * close to what the emulator is already likely using.
     */
    private fun duckStationPs1CardBaseName(name: String): String {
        return name
            .replace(Regex("""\s*[\(\[]\s*(disc|cd)\s*[0-9]+(?:\s*of\s*[0-9]+)?\s*[\)\]]""", RegexOption.IGNORE_CASE), "")
            .replace(Regex("""\s*\[[^\]]*]"""), "")
            .replace(Regex("""\s+"""), " ")
            .trim()
    }

    private suspend fun lookupServerTitleTypes(
        api: SaveSyncApi,
        titles: List<com.savesync.android.api.TitleInfo>
    ): Map<String, Pair<String, String>> {
        val codes = titles.mapNotNull { titleInfo ->
            val code = titleInfo.title_id.uppercase()
            code.takeIf { compactPsCodeRegex.matches(it) }
        }.distinct()
        if (codes.isEmpty()) return emptyMap()

        return try {
            val response = api.lookupGameNames(GameNameRequest(codes = codes))
            codes.mapNotNull { code ->
                val type = response.types[code] ?: return@mapNotNull null
                val name = response.names[code] ?: code
                code to (type to name)
            }.toMap()
        } catch (_: Exception) {
            emptyMap()
        }
    }

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
            val androidSys = normalizeSystemCode(serverSys)
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
                val engine = SyncEngine(api, db, consoleId, currentSettings.saturnSyncFormat)
                // Only sync local saves (exclude server-only entries)
                val romScanDir = effectiveRomScanDir(currentSettings)
                val emudeckDir = currentSettings.emudeckDir
                val romDirOverrides = currentSettings.romDirOverrides
                val saveDirOverrides = currentSettings.saveDirOverrides
                val allLocalSaves = _allSaves.value.filter { !it.isServerOnly }.ifEmpty {
                    EmulatorRegistry.discoverAllSaves(
                        romScanDir = romScanDir,
                        emudeckDir = emudeckDir,
                        romDirOverrides = romDirOverrides,
                        saveDirOverrides = saveDirOverrides,
                        saturnSyncFormat = currentSettings.saturnSyncFormat,
                        beetleSaturnPerCoreFolder = currentSettings.beetleSaturnPerCoreFolder,
                        cdGamesPerContentFolder = currentSettings.cdGamesPerContentFolder
                    ).also { found ->
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

    /** Scans romScanDir subfolders and populates [detectedSystemFolders]. */
    fun detectSystemFolders() {
        viewModelScope.launch {
            val romScanDir = effectiveRomScanDir(settingsStore.settingsFlow.first())
            _detectedSystemFolders.value = EmulatorRegistry.detectSystemFolders(romScanDir)
        }
    }

    /** Persists a per-system ROM folder override and triggers a rescan. */
    fun setRomDirOverride(system: String, path: String) {
        viewModelScope.launch {
            settingsStore.setRomDirOverride(system, path)
            scanSaves()
        }
    }

    /** Removes a per-system override (reverts to auto-detected folder) and triggers a rescan. */
    fun clearRomDirOverride(system: String) {
        viewModelScope.launch {
            settingsStore.clearRomDirOverride(system)
            scanSaves()
        }
    }

    fun saveSettings(
        serverUrl: String,
        apiKey: String,
        autoSync: Boolean,
        intervalMinutes: Int,
        romScanDir: String = "",
        emudeckDir: String = "",
        saturnSyncFormat: SaturnSyncFormat = SaturnSyncFormat.MEDNAFEN,
        beetleSaturnPerCoreFolder: Boolean = true,
        cdGamesPerContentFolder: Boolean = false
    ) {
        viewModelScope.launch {
            settingsStore.updateSettings(
                serverUrl = serverUrl,
                apiKey = apiKey,
                autoSyncEnabled = autoSync,
                autoSyncIntervalMinutes = intervalMinutes,
                romScanDir = romScanDir,
                emudeckDir = emudeckDir,
                saturnSyncFormat = saturnSyncFormat,
                beetleSaturnPerCoreFolder = beetleSaturnPerCoreFolder,
                cdGamesPerContentFolder = cdGamesPerContentFolder
            )
            ApiClient.invalidate()
            scheduleOrCancelAutoSync(autoSync, intervalMinutes)
            scanSaves()
        }
    }

    /** Persists a per-emulator save folder override and triggers a rescan. */
    fun setSaveDirOverride(emulatorKey: String, path: String) {
        viewModelScope.launch {
            settingsStore.setSaveDirOverride(emulatorKey, path)
            scanSaves()
        }
    }

    /** Removes a per-emulator save folder override and triggers a rescan. */
    fun clearSaveDirOverride(emulatorKey: String) {
        viewModelScope.launch {
            settingsStore.clearSaveDirOverride(emulatorKey)
            scanSaves()
        }
    }

    /**
     * Persists just the two RetroArch-specific toggles, used by
     * EmulatorsScreen's RetroArch card.  Doesn't disturb the rest of the
     * settings (server URL, etc.) and triggers a rescan so save discovery
     * picks up the new layout immediately.
     */
    fun saveRetroArchToggles(
        beetleSaturnPerCoreFolder: Boolean,
        cdGamesPerContentFolder: Boolean
    ) {
        viewModelScope.launch {
            settingsStore.updateSettings(
                beetleSaturnPerCoreFolder = beetleSaturnPerCoreFolder,
                cdGamesPerContentFolder = cdGamesPerContentFolder
            )
            scanSaves()
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
                val meta = when {
                    systemName == "PS1" && !titleId.contains('_') ->
                        api.getPs1CardMeta(titleId, slot = 0)
                    systemName == "PS2" && !titleId.contains('_') ->
                        api.getPs2CardMeta(titleId, format = "ps2")
                    systemName == "GC" && titleId.startsWith("GC_") ->
                        api.getGcCardMeta(titleId)
                    else -> api.getSaveMeta(titleId)
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
            val currentSettings = settingsStore.settingsFlow.first()
            val effectiveDir = effectiveRomScanDir(currentSettings, dir)
            if (effectiveDir.isBlank()) {
                _romScanResults.value = mapOf("(no ROM directory set)" to 0)
                return@launch
            }
            val allRoms = EmulatorRegistry.discoverAllRomEntries(
                romScanDir = effectiveDir,
                emudeckDir = currentSettings.emudeckDir,
                romDirOverrides = currentSettings.romDirOverrides,
                saveDirOverrides = currentSettings.saveDirOverrides,
                saturnSyncFormat = currentSettings.saturnSyncFormat,
                beetleSaturnPerCoreFolder = currentSettings.beetleSaturnPerCoreFolder,
                cdGamesPerContentFolder = currentSettings.cdGamesPerContentFolder
            )
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

    // ``prepareRomFolders`` surface for the Settings screen — creates the
    // canonical per-system folders under the user's ROM directory so
    // catalog downloads land in predictable places.  Result flows back
    // through ``_prepareFoldersMessage`` as a snackbar-ready summary.
    private val _prepareFoldersMessage = MutableStateFlow<String?>(null)
    val prepareFoldersMessage: StateFlow<String?> = _prepareFoldersMessage

    fun prepareRomFolders(dir: String = "") {
        viewModelScope.launch {
            val currentSettings = settingsStore.settingsFlow.first()
            val effectiveDir = effectiveRomScanDir(currentSettings, dir)
            if (effectiveDir.isBlank()) {
                _prepareFoldersMessage.value =
                    "Set a ROM directory first, then try again."
                return@launch
            }
            val report = withContext(Dispatchers.IO) {
                InstalledRomsScanner.prepareRomFolders(
                    scanRoot = File(effectiveDir),
                    romDirOverrides = currentSettings.romDirOverrides,
                )
            }
            _prepareFoldersMessage.value = when {
                report.errors.isNotEmpty() -> {
                    val first = report.errors.first().let { "${it.first}: ${it.second}" }
                    "Created ${report.createdCount} folder(s); " +
                        "${report.errors.size} failed (e.g. $first)"
                }
                report.createdCount == 0 ->
                    "All ${report.existing.size} system folders already exist."
                else ->
                    "Created ${report.createdCount} folder(s) under $effectiveDir."
            }
        }
    }

    fun consumePrepareFoldersMessage() {
        _prepareFoldersMessage.value = null
    }

    private fun isSharedYabaSanshiroEntry(entry: SaveEntry, settings: Settings): Boolean {
        return settings.saturnSyncFormat == SaturnSyncFormat.YABASANSHIRO &&
            entry.systemName == "SAT" &&
            entry.saveFile?.name.equals("backup.bin", ignoreCase = true)
    }

    private fun rememberSaturnArchiveSelection(titleId: String, archiveNames: List<String>) {
        val normalized = archiveNames
            .map { it.trim() }
            .filter { it.isNotEmpty() }
            .distinct()
        if (normalized.isEmpty()) return
        SaturnArchiveStateStore.put(titleId, normalized)
        _saturnArchiveSelectionVersion.value += 1
    }

    fun prepareSaveDetail(entry: SaveEntry?) {
        if (entry == null) return

        viewModelScope.launch {
            val currentSettings = settingsStore.settingsFlow.first()
            if (!isSharedYabaSanshiroEntry(entry, currentSettings)) return@launch

            if (SaturnArchiveStateStore.get(entry.titleId).isNotEmpty()) {
                _saturnArchiveSelectionVersion.value += 1
                return@launch
            }

            val saveFile = entry.saveFile
            if (saveFile?.exists() != true || currentSettings.serverUrl.isBlank()) return@launch

            val archiveNames = try {
                SaturnSaveFormatConverter.archiveNames(saveFile.readBytes())
            } catch (_: Exception) {
                return@launch
            }
            if (archiveNames.isEmpty()) return@launch

            val lookupResults = try {
                val api = ApiClient.create(currentSettings.serverUrl, currentSettings.apiKey)
                api.lookupSaturnArchives(
                    SaturnArchiveLookupRequest(
                        title_id = entry.titleId,
                        archive_names = archiveNames
                    )
                ).results
            } catch (_: Exception) {
                return@launch
            }

            val (hiddenSelected, visibleOptions) = buildSaturnArchiveOptions(lookupResults)
            when {
                hiddenSelected.isNotEmpty() && visibleOptions.none { it.preselected } ->
                    rememberSaturnArchiveSelection(entry.titleId, hiddenSelected)
                visibleOptions.isEmpty() && hiddenSelected.isNotEmpty() ->
                    rememberSaturnArchiveSelection(entry.titleId, hiddenSelected)
                visibleOptions.all { it.preselected } -> {
                    rememberSaturnArchiveSelection(
                        entry.titleId,
                        hiddenSelected + visibleOptions.flatMap { it.archiveNames }
                    )
                }
            }
        }
    }

    fun canUploadFromDetail(entry: SaveEntry): Boolean {
        if (entry.exists()) return true
        return entry.systemName == "SAT" &&
            entry.saveFile?.name.equals("backup.bin", ignoreCase = true) &&
            entry.saveFile?.exists() == true
    }

    fun detailLocalHash(entry: SaveEntry): String? {
        val isSharedYabaSanshiroEntry =
            entry.systemName == "SAT" &&
                entry.isServerOnly &&
                entry.saveFile?.name.equals("backup.bin", ignoreCase = true)

        if (!isSharedYabaSanshiroEntry) {
            return try { entry.computeHash().ifBlank { null } } catch (_: Exception) { null }
        }

        if (entry.saveFile?.exists() != true) return null
        return try {
            val archiveNames = SaturnArchiveStateStore.get(entry.titleId)
            if (archiveNames.isEmpty()) {
                null
            } else {
                val canonical = SaturnSaveFormatConverter.extractCanonical(
                    entry.saveFile.readBytes(),
                    archiveNames
                )
                HashUtils.sha256Bytes(canonical)
            }
        } catch (_: Exception) {
            null
        }
    }

    fun detailLocalSize(entry: SaveEntry): Long {
        val isSharedYabaSanshiroEntry =
            entry.systemName == "SAT" &&
                entry.isServerOnly &&
                entry.saveFile?.name.equals("backup.bin", ignoreCase = true)

        if (!isSharedYabaSanshiroEntry) {
            return when {
                entry.saveFile != null && entry.extraFiles.isNotEmpty() ->
                    (listOf(entry.saveFile) + entry.extraFiles).filter { it.exists() }.sumOf { it.length() }
                entry.isMultiFile && entry.saveDir != null ->
                    entry.saveDir.walkTopDown().filter { it.isFile }.sumOf { it.length() }
                entry.saveFile != null ->
                    entry.saveFile.length()
                entry.saveDir != null ->
                    entry.saveDir.walkTopDown().filter { it.isFile }.sumOf { it.length() }
                else -> 0L
            }
        }

        if (entry.saveFile?.exists() != true) return 0L
        return try {
            val archiveNames = SaturnArchiveStateStore.get(entry.titleId)
            if (archiveNames.isEmpty()) {
                entry.saveFile.length()
            } else {
                SaturnSaveFormatConverter.extractCanonical(
                    entry.saveFile.readBytes(),
                    archiveNames
                ).size.toLong()
            }
        } catch (_: Exception) {
            entry.saveFile.length()
        }
    }

    private fun buildSaturnArchiveOptions(
        lookupResults: List<SaturnArchiveLookupResult>,
    ): Pair<List<String>, List<SaturnArchivePickerOption>> {
        val hiddenSelected = mutableListOf<String>()
        val visibleOptions = mutableListOf<SaturnArchivePickerOption>()

        for (result in lookupResults) {
            when (result.status) {
                "exact_current" -> hiddenSelected += result.archive_names
                "includes_current" -> {
                    val detail = result.candidates.joinToString(", ") { it.game_name }
                    visibleOptions += SaturnArchivePickerOption(
                        archiveFamily = result.archive_family,
                        archiveNames = result.archive_names,
                        detail = if (detail.isBlank()) "Likely matches this title" else detail,
                        preselected = true
                    )
                }
                "unknown" -> visibleOptions += SaturnArchivePickerOption(
                    archiveFamily = result.archive_family,
                    archiveNames = result.archive_names,
                    detail = "Unknown archive",
                    preselected = false
                )
                else -> Unit
            }
        }

        return hiddenSelected to visibleOptions
    }

    private suspend fun ensureSaturnArchiveSelection(
        entry: SaveEntry,
        settings: Settings,
        api: SaveSyncApi,
        action: SaturnArchiveAction
    ): Boolean {
        if (!isSharedYabaSanshiroEntry(entry, settings)) return true
        if (SaturnArchiveStateStore.get(entry.titleId).isNotEmpty()) return true

        val saveFile = entry.saveFile
        if (saveFile?.exists() != true) return true

        val archiveNames = try {
            SaturnSaveFormatConverter.archiveNames(saveFile.readBytes())
        } catch (e: Exception) {
            _saveDetailState.value = SaveDetailState.Error(
                e.message ?: "Could not parse Saturn backup.bin"
            )
            return false
        }

        if (archiveNames.isEmpty()) {
            _saveDetailState.value = SaveDetailState.Error("No Saturn save archives found in backup.bin")
            return false
        }

        val lookupResults = try {
            api.lookupSaturnArchives(
                SaturnArchiveLookupRequest(
                    title_id = entry.titleId,
                    archive_names = archiveNames
                )
            ).results
        } catch (_: Exception) {
            archiveNames.map { archiveName ->
                SaturnArchiveLookupResult(
                    archive_family = archiveName,
                    archive_names = listOf(archiveName),
                    status = "unknown",
                    matches_current_title = false,
                    candidates = emptyList()
                )
            }
        }

        val (hiddenSelected, visibleOptions) = buildSaturnArchiveOptions(lookupResults)
        if (visibleOptions.isEmpty()) {
            if (hiddenSelected.isEmpty()) {
                _saveDetailState.value = SaveDetailState.Error(
                    "Could not identify which YabaSanshiro archives belong to this game."
                )
                return false
            }
            rememberSaturnArchiveSelection(entry.titleId, hiddenSelected)
            return true
        }

        if (hiddenSelected.isNotEmpty() && visibleOptions.none { it.preselected }) {
            rememberSaturnArchiveSelection(entry.titleId, hiddenSelected)
            return true
        }

        if (visibleOptions.all { it.preselected }) {
            val finalSelection = (
                hiddenSelected + visibleOptions.flatMap { it.archiveNames }
            )
                .map { it.trim() }
                .filter { it.isNotEmpty() }
                .distinct()
            if (finalSelection.isNotEmpty()) {
                rememberSaturnArchiveSelection(entry.titleId, finalSelection)
                return true
            }
        }

        _saturnArchivePicker.value = SaturnArchivePickerState.Visible(
            entry = entry,
            action = action,
            hiddenSelectedArchives = hiddenSelected,
            options = visibleOptions
        )
        return false
    }

    fun dismissSaturnArchivePicker() {
        _saturnArchivePicker.value = SaturnArchivePickerState.Hidden
    }

    fun applySaturnArchiveSelection(selectedArchives: Set<String>) {
        val pickerState = _saturnArchivePicker.value
        if (pickerState !is SaturnArchivePickerState.Visible) return

        _saturnArchivePicker.value = SaturnArchivePickerState.Hidden
        val selectedFamilies = selectedArchives
        val finalSelection = (
            pickerState.hiddenSelectedArchives +
                pickerState.options
                    .filter { it.archiveFamily in selectedFamilies }
                    .flatMap { it.archiveNames }
        )
            .map { it.trim() }
            .filter { it.isNotEmpty() }
            .distinct()

        viewModelScope.launch {
            if (finalSelection.isEmpty()) {
                _saveDetailState.value = SaveDetailState.Error(
                    "Choose at least one Saturn archive for this game."
                )
                return@launch
            }

            rememberSaturnArchiveSelection(pickerState.entry.titleId, finalSelection)
            when (pickerState.action) {
                SaturnArchiveAction.SYNC -> syncSave(pickerState.entry)
                SaturnArchiveAction.UPLOAD -> uploadSave(pickerState.entry)
            }
        }
    }

    fun syncSave(entry: SaveEntry) {
        viewModelScope.launch {
            val currentSettings = settingsStore.settingsFlow.first()
            if (currentSettings.serverUrl.isBlank()) {
                _saveDetailState.value = SaveDetailState.Error("Server URL not configured")
                return@launch
            }
            if (entry.isServerOnly && !hasLocalSaveTarget(entry)) {
                _saveDetailState.value = SaveDetailState.Error(
                    "No local save location is known for this title yet. Download the ROM first, then rescan and download the save."
                )
                return@launch
            }
            _saveDetailState.value = SaveDetailState.Working("sync")
            try {
                val consoleId = settingsStore.ensureConsoleId()
                val api = ApiClient.create(currentSettings.serverUrl, currentSettings.apiKey)
                if (!ensureSaturnArchiveSelection(entry, currentSettings, api, SaturnArchiveAction.SYNC)) {
                    if (_saturnArchivePicker.value is SaturnArchivePickerState.Visible) {
                        _saveDetailState.value = SaveDetailState.Idle
                    }
                    return@launch
                }
                val engine = SyncEngine(api, db, consoleId, currentSettings.saturnSyncFormat)
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

    /**
     * Trigger a save-sync for a ROM listed on the Installed tab.  The
     * Installed tab models rows as ``InstalledRom`` (path-based) rather
     * than ``SaveEntry`` (sync-engine-aware), so we have to look up the
     * matching SaveEntry from the unfiltered combined list before we can
     * hand it off to the existing per-save sync flow.
     *
     * Match key is (system code) + (normalised display name) — lowercase
     * + alphanumeric-only — to absorb the small differences between how
     * each scanner builds its display name (RetroArchEmulator uses the
     * file stem verbatim, InstalledRomsScanner runs the stem through
     * ``prettyName`` which collapses underscores to spaces).
     *
     * Falls through to the existing ``saveDetailState`` flow so the same
     * snackbar / progress UI used by SaveDetailScreen + RomCatalogScreen
     * applies here too — the Installed tab subscribes to that flow.
     */
    fun syncInstalledRomSaves(rom: InstalledRom) {
        val target = normalizeForLookup(rom.displayName)
        val match = _allSaves.value.firstOrNull { entry ->
            entry.systemName.equals(rom.system, ignoreCase = true) &&
                normalizeForLookup(entry.displayName) == target
        }
        if (match == null) {
            _saveDetailState.value = SaveDetailState.Error(
                "No save found for \"${rom.displayName}\" yet. " +
                    "If you've never opened this game, sync once after creating " +
                    "a save in the emulator so the server has something to track."
            )
            return
        }
        // Delegate to the existing per-save sync; it owns API client setup,
        // Saturn-archive picker handshake, status reporting, and the
        // post-sync metadata refresh.
        syncSave(match)
    }

    /** Normalisation used by [syncInstalledRomSaves] to bridge slight
     *  display-name differences between scanners. */
    private fun normalizeForLookup(name: String): String =
        name.lowercase().replace(Regex("[^a-z0-9]+"), "")

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
                if (!ensureSaturnArchiveSelection(entry, currentSettings, api, SaturnArchiveAction.UPLOAD)) {
                    if (_saturnArchivePicker.value is SaturnArchivePickerState.Visible) {
                        _saveDetailState.value = SaveDetailState.Idle
                    }
                    return@launch
                }
                val engine = SyncEngine(api, db, consoleId, currentSettings.saturnSyncFormat)
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
            if (!hasLocalSaveTarget(entry)) {
                _saveDetailState.value = SaveDetailState.Error(
                    "No local save location is known for this title yet. Download the ROM first, then rescan and download the save."
                )
                return@launch
            }
            _saveDetailState.value = SaveDetailState.Working("download")
            try {
                val consoleId = settingsStore.ensureConsoleId()
                val api = ApiClient.create(currentSettings.serverUrl, currentSettings.apiKey)
                val engine = SyncEngine(api, db, consoleId, currentSettings.saturnSyncFormat)
                val ok = engine.downloadSave(entry, entry.titleId)
                if (ok) {
                    engine.recordSyncedStateFromFile(entry)
                    fetchServerMeta(entry.titleId, entry.systemName)
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

    fun fetchRomAvailable() {
        viewModelScope.launch {
            val currentSettings = settingsStore.settingsFlow.first()
            if (currentSettings.serverUrl.isBlank()) return@launch
            try {
                val api = ApiClient.create(currentSettings.serverUrl, currentSettings.apiKey)
                val titles = _allSaves.value.map { it.titleId }.toSet()
                if (titles.isEmpty()) return@launch
                val response = api.getRoms()
                val matchingRoms = response.roms
                    .filter { it.title_id in titles }
                _romsByTitle.value = matchingRoms.groupBy { it.title_id }
                _romAvailable.value = matchingRoms.map { it.title_id }.toSet()
            } catch (_: Exception) {}
        }
    }

    fun downloadRom(
        romId: String,
        system: String,
        filename: String? = null,
        extractFormat: String? = null,
    ) {
        // Run on viewModelScope only long enough to read settings; the
        // actual enqueue hops onto appScope inside enqueueAsync so it
        // can't be cancelled by ViewModel teardown.  Without this hop
        // a fast user (tap → navigate away) could leave a row in DB
        // with no worker attached.
        viewModelScope.launch {
            val currentSettings = settingsStore.settingsFlow.first()
            if (currentSettings.serverUrl.isBlank()) {
                _romDownloadState.value = RomDownloadState.Error("Server URL not configured")
                return@launch
            }
            val romScanDir = effectiveRomScanDir(currentSettings)
            if (romScanDir.isBlank()) {
                _romDownloadState.value = RomDownloadState.Error("ROM directory not configured. Set it in Settings.")
                return@launch
            }
            try {
                val api = ApiClient.create(currentSettings.serverUrl, currentSettings.apiKey)
                val displayName = filename ?: romId
                // Fire-and-forget on appScope — the worker, the row insert,
                // and the foreground-service handoff all happen there.
                downloadManager.enqueueAsync(
                    api = api,
                    romId = romId,
                    system = system,
                    displayName = displayName,
                    filename = filename ?: romId,
                    romScanDir = romScanDir,
                    romDirOverrides = currentSettings.romDirOverrides,
                    extractFormat = extractFormat,
                )
                _romDownloadState.value = RomDownloadState.Downloading(displayName)
            } catch (e: Exception) {
                _romDownloadState.value = RomDownloadState.Error(e.message ?: "Download failed")
            }
        }
    }

    fun resetRomDownloadState() {
        _romDownloadState.value = RomDownloadState.Idle
    }

    // ──────────────────────────────────────────────────────────────────
    // Downloads tab — manager passthroughs.  All of these hop onto
    // appScope so a ViewModel teardown mid-action doesn't leave the
    // UI lying about a status that didn't actually transition.
    // ──────────────────────────────────────────────────────────────────

    fun pauseDownload(id: String) {
        downloadManager.pauseAsync(id)
    }

    fun resumeDownload(id: String) {
        viewModelScope.launch {
            val currentSettings = settingsStore.settingsFlow.first()
            if (currentSettings.serverUrl.isBlank()) return@launch
            val api = ApiClient.create(currentSettings.serverUrl, currentSettings.apiKey)
            downloadManager.resumeAsync(api, id)
        }
    }

    fun cancelDownload(id: String) {
        downloadManager.cancelAsync(id)
    }

    fun removeDownload(id: String) {
        downloadManager.removeAsync(id)
    }

    fun clearFinishedDownloads() {
        downloadManager.clearFinishedAsync()
    }

    /**
     * Trigger a fresh save / installed-rom scan after a download finishes.
     * The Downloads screen calls this from a LaunchedEffect that watches
     * for status transitions to COMPLETED so the rest of the app picks
     * up the new file without an explicit refresh.
     */
    fun onDownloadCompleted() {
        viewModelScope.launch {
            scanSaves()
            scanInstalledRoms(force = true)
        }
    }

    // ──────────────────────────────────────────────────────────────
    // ROM Catalog tab
    // ──────────────────────────────────────────────────────────────

    /** Fetch the full server ROM catalog for the browse tab.  Called
     *  lazily on first tab entry and again when the user hits refresh. */
    fun fetchRomCatalog(force: Boolean = false) {
        if (_romCatalogLoading.value) return
        if (_romCatalogLoaded.value && !force && _romCatalog.value.isNotEmpty()) return
        viewModelScope.launch {
            val current = settingsStore.settingsFlow.first()
            if (current.serverUrl.isBlank()) {
                _romCatalogError.value = "Server URL not configured."
                _romCatalogLoaded.value = true
                return@launch
            }
            _romCatalogLoading.value = true
            _romCatalogError.value = null
            try {
                val api = ApiClient.create(current.serverUrl, current.apiKey)
                val response = api.getRoms()
                _romCatalog.value = response.roms
                _romCatalogLoaded.value = true
            } catch (e: Exception) {
                _romCatalogError.value = e.message ?: e.javaClass.simpleName
                _romCatalogLoaded.value = true
            } finally {
                _romCatalogLoading.value = false
            }
        }
    }

    /** Smart-filtered view over [romCatalog]: every query token must
     *  match, roman↔arabic variants expand automatically. */
    fun filteredCatalog(query: String, system: String? = null): List<RomEntry> =
        RomCatalogFilter.filter(_romCatalog.value, query, system)

    // ──────────────────────────────────────────────────────────────
    // Installed Games tab
    // ──────────────────────────────────────────────────────────────

    fun scanInstalledRoms(force: Boolean = false) {
        if (_installedRomsLoading.value) return
        if (_installedRomsLoaded.value && !force && _installedRoms.value.isNotEmpty()) return
        viewModelScope.launch {
            val current = settingsStore.settingsFlow.first()
            _installedRomsLoading.value = true
            try {
                // The scanner is pure Kotlin + File I/O so it's safe
                // to run straight on the IO dispatcher.  Fine-grained
                // dispatch switch would be nice but this matches the
                // other scanners in the app.
                val roms = InstalledRomsScanner.scanInstalled(
                    effectiveRomScanDir(current),
                    current.romDirOverrides,
                )
                _installedRoms.value = roms
                _installedRomsLoaded.value = true
            } catch (e: Exception) {
                // Best-effort scan — missing permission or broken
                // paths shouldn't crash the tab; show an empty list.
                _installedRoms.value = emptyList()
                _installedRomsLoaded.value = true
            } finally {
                _installedRomsLoading.value = false
            }
        }
    }

    /** Delete an installed ROM (whole-folder where applicable) and
     *  refresh both the installed list and the saves list. */
    fun deleteInstalledRom(rom: InstalledRom) {
        viewModelScope.launch {
            val result = InstalledRomsScanner.deleteInstalled(rom)
            _deleteInstalledState.value = if (result.errors.isEmpty()) {
                DeleteInstalledState.Success(rom, result)
            } else {
                DeleteInstalledState.Error(rom, result)
            }
            // Refresh installed list and saves — a deleted ROM may
            // flip a synced save entry back to "server only".
            scanInstalledRoms(force = true)
            scanSaves()
        }
    }

    fun resetDeleteInstalledState() {
        _deleteInstalledState.value = DeleteInstalledState.Idle
    }

    private fun hasLocalSaveTarget(entry: SaveEntry): Boolean {
        return entry.saveFile != null || entry.saveDir != null
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
                val romScanDir = effectiveRomScanDir(currentSettings)
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

    private fun effectiveRomScanDir(settings: Settings, requestedDir: String = ""): String {
        return EmudeckPaths.romsDir(settings.emudeckDir)?.absolutePath
            ?: requestedDir.ifBlank { settings.romScanDir }
    }
}
