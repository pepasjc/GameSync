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
import com.savesync.android.api.SaveSyncApi
import com.savesync.android.emulators.EmulatorRegistry
import com.savesync.android.emulators.SaveEntry
import com.savesync.android.emulators.impl.RetroArchEmulator
import com.savesync.android.emulators.impl.DuckStationEmulator
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

    private val settingsStore = SettingsStore(application)
    private val db = SaveSyncApp.instance.database

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
        syncStateEntities
    ) { args: Array<*> ->
        @Suppress("UNCHECKED_CAST")
        val all = args[0] as List<SaveEntry>
        val systemFilter = args[1] as String
        val query = args[2] as String
        val statusFilter = args[3] as SaveSyncStatus?
        @Suppress("UNCHECKED_CAST")
        val syncEntities = args[4] as List<SyncStateEntity>

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
        _allSaves, syncStateEntities
    ) { allSaves, syncEntities ->
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
                val romScanDir = currentSettings.romScanDir
                val dolphinMemCardDir = currentSettings.dolphinMemCardDir
                val romDirOverrides = currentSettings.romDirOverrides
                val rawLocalSaves = EmulatorRegistry.discoverAllSaves(overrides, romScanDir, dolphinMemCardDir, romDirOverrides)

                // Discover all ROMs the emulators know about (with expected save paths)
                val allRomEntries = EmulatorRegistry.discoverAllRomEntries(romScanDir, dolphinMemCardDir, romDirOverrides)

                val serverOnlySaves: List<SaveEntry>
                val localSaves: List<SaveEntry>

                if (currentSettings.serverUrl.isNotBlank()) {
                    val api = try {
                        ApiClient.create(currentSettings.serverUrl, currentSettings.apiKey)
                    } catch (e: Exception) {
                        _allSaves.value = sortSaves(rawLocalSaves)
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

                    // Enrich product-code entries with proper game names from server.
                    // PSP, PS1, PS2, SAT, and GC saves resolved via disc headers all carry
                    // compact product-code title IDs (e.g. SLUS01234, SAT_T-12705H).
                    // Slug-based PS1/PS2 IDs (PS1_slug / PS2_slug) are skipped here.
                    val productCodeEntries = resolvedRawSaves.filter { entry ->
                        entry.systemName == "PSP" ||
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
                            .mapNotNull { titleInfo -> buildPs2ServerOnlyEntry(titleInfo) }

                        val specialFallbackIds = (ps1ServerOnly + ps2ServerOnly)
                            .mapTo(mutableSetOf()) { it.titleId }

                        // If a save has no local ROM/save anchor yet, still surface it when the
                        // server can provide the ROM. That lets the user discover the save,
                        // download the ROM, and then rescan to get a concrete save target path.
                        val romCatalogServerOnly = stillUnanchoredTitles
                            .filter { it.title_id !in specialFallbackIds }
                            .mapNotNull { titleInfo ->
                                buildRomCatalogServerOnlyEntry(
                                    titleInfo = titleInfo,
                                    roms = romCatalogByTitle[titleInfo.title_id].orEmpty()
                                )
                            }

                        matchedServerOnly + ps1ServerOnly + ps2ServerOnly + romCatalogServerOnly
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
     * Maps legacy/server-side system codes → canonical codes.
     * Used to normalise system names for display and for deduplication.
     * Legacy saves on the server may use older codes from previous client versions.
     */
    private val serverToAndroidSystem = mapOf(
        // Sega — GENESIS, MEGADRIVE, and GEN are all aliases for MD
        "GENESIS"   to "MD",
        "MEGADRIVE" to "MD",
        "GEN"       to "MD",
        // SCD is a legacy alias for SEGACD (older Android uploads used SCD)
        "SCD"       to "SEGACD",
        // WS is a legacy alias for WSWAN (older Android uploads used WS)
        "WS"        to "WSWAN",
        // ATARI2600/ATARI7800 are legacy aliases (older desktop uploads used these)
        "ATARI2600" to "A2600",
        "ATARI7800" to "A7800",
        // PPSSPP was the old Android system name for PSP; normalise to PSP
        "PPSSPP"    to "PSP",
    )

    /**
     * Maps each Android-side system code to ALL possible server-side system codes.
     *
     * Multiple server codes can map to the same Android code (e.g. "GENESIS" and
     * "MEGADRIVE" both → "MD"), so a simple [associate] reverse would silently drop all but
     * the last entry.  This one-to-many map avoids that by grouping all variants together,
     * so we try every possible server prefix when remapping a local titleId.
     */
    private val androidToServerSystems: Map<String, List<String>> =
        serverToAndroidSystem.entries.groupBy({ it.value }, { it.key })

    /**
     * Normalises a system code to Android's canonical form for display.
     * "SCD" → "SEGACD", "WS" → "WSWAN", etc.
     * Codes already in Android form are returned unchanged.
     */
    private fun normalizeSystemCode(system: String) =
        serverToAndroidSystem[system.uppercase()] ?: system

    private fun buildPs2ServerOnlyEntry(
        titleInfo: com.savesync.android.api.TitleInfo
    ): SaveEntry? {
        val system = normalizeSystemCode(
            titleInfo.platform
                ?: titleInfo.system
                ?: titleInfo.consoleType
                ?: ""
        )
        if (system != "PS2") return null

        val memcardsDir = AetherSX2Emulator.findMemcardsDir(Environment.getExternalStorageDirectory())
            ?: return null

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

    private fun buildRomCatalogServerOnlyEntry(
        titleInfo: com.savesync.android.api.TitleInfo,
        roms: List<RomEntry>
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

        return SaveEntry(
            titleId = titleInfo.title_id,
            displayName = displayName,
            systemName = resolvedSystem,
            saveFile = null,
            saveDir = null,
            isServerOnly = true,
            canonicalName = canonicalName
        )
    }

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
                val dolphinMemCardDir = currentSettings.dolphinMemCardDir
                val romDirOverrides = currentSettings.romDirOverrides
                val allLocalSaves = _allSaves.value.filter { !it.isServerOnly }.ifEmpty {
                    EmulatorRegistry.discoverAllSaves(romScanDir = romScanDir, dolphinMemCardDir = dolphinMemCardDir, romDirOverrides = romDirOverrides).also { found ->
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
            val romScanDir = settingsStore.settingsFlow.first().romScanDir
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
        dolphinMemCardDir: String = ""
    ) {
        viewModelScope.launch {
            settingsStore.updateSettings(
                serverUrl = serverUrl,
                apiKey = apiKey,
                autoSyncEnabled = autoSync,
                autoSyncIntervalMinutes = intervalMinutes,
                romScanDir = romScanDir,
                dolphinMemCardDir = dolphinMemCardDir
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
            val effectiveDir = dir.ifBlank { settingsStore.settingsFlow.first().romScanDir }
            if (effectiveDir.isBlank()) {
                _romScanResults.value = mapOf("(no ROM directory set)" to 0)
                return@launch
            }
            val romDirOverrides = settingsStore.settingsFlow.first().romDirOverrides
            val allRoms = EmulatorRegistry.discoverAllRomEntries(romScanDir = effectiveDir, romDirOverrides = romDirOverrides)
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
                val engine = SyncEngine(api, db, consoleId)
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

    fun downloadRom(romId: String, system: String, filename: String? = null) {
        viewModelScope.launch {
            val currentSettings = settingsStore.settingsFlow.first()
            if (currentSettings.serverUrl.isBlank()) {
                _romDownloadState.value = RomDownloadState.Error("Server URL not configured")
                return@launch
            }
            if (currentSettings.romScanDir.isBlank()) {
                _romDownloadState.value = RomDownloadState.Error("ROM directory not configured. Set it in Settings.")
                return@launch
            }
            _romDownloadState.value = RomDownloadState.Downloading(filename ?: romId)
            try {
                val api = ApiClient.create(currentSettings.serverUrl, currentSettings.apiKey)
                val engine = SyncEngine(api, db, settingsStore.ensureConsoleId())
                val file = engine.downloadRom(romId, system, currentSettings.romScanDir, filename, currentSettings.romDirOverrides)
                if (file != null) {
                    _romDownloadState.value = RomDownloadState.Success(file)
                    // Rescan so the newly downloaded ROM is picked up: the SaveEntry for this
                    // title will get a real saveFile/saveDir and isServerOnly becomes false,
                    // which re-enables the Sync / Upload / Download buttons immediately.
                    scanSaves()
                } else {
                    _romDownloadState.value = RomDownloadState.Error("ROM not found on server")
                }
            } catch (e: Exception) {
                _romDownloadState.value = RomDownloadState.Error(e.message ?: "Download failed")
            }
        }
    }

    fun resetRomDownloadState() {
        _romDownloadState.value = RomDownloadState.Idle
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
