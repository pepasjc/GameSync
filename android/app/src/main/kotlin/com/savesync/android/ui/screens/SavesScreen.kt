package com.savesync.android.ui.screens

import android.content.Intent
import android.net.Uri
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.focusable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Clear
import androidx.compose.material.icons.filled.FilterList
import androidx.compose.material.icons.filled.Language
import androidx.compose.material.icons.filled.Search
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material.icons.filled.Sync
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.OutlinedTextFieldDefaults
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.input.key.Key
import androidx.compose.ui.input.key.KeyEventType
import androidx.compose.ui.input.key.key
import androidx.compose.ui.input.key.onPreviewKeyEvent
import androidx.compose.ui.input.key.type
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import com.google.accompanist.swiperefresh.SwipeRefresh
import com.google.accompanist.swiperefresh.rememberSwipeRefreshState
import com.savesync.android.MainActivity
import com.savesync.android.emulators.SaveEntry
import com.savesync.android.findComponentActivity
import com.savesync.android.storage.SyncStateEntity
import com.savesync.android.ui.MainViewModel
import com.savesync.android.ui.SaveSyncStatus
import com.savesync.android.ui.SyncState
import com.savesync.android.ui.SaveDetailState
import com.savesync.android.ui.components.ButtonGlyph
import com.savesync.android.ui.components.GsButton
import com.savesync.android.ui.components.GsConfirmDialog
import com.savesync.android.ui.components.GsFooterHints
import com.savesync.android.ui.components.GsHint
import com.savesync.android.ui.components.GsListRow
import com.savesync.android.ui.components.GsPill
import com.savesync.android.ui.components.GsTopBar
import com.savesync.android.ui.components.LocalShowButtonHints
import com.savesync.android.ui.components.SystemFilterChip
import com.savesync.android.ui.theme.GsColors
import com.savesync.android.ui.components.firstLetter
import com.savesync.android.ui.components.handleHorizontalHoldKeyEvent
import com.savesync.android.ui.components.rememberHoldNavState
import kotlinx.coroutines.launch
import java.text.SimpleDateFormat
import java.net.URI
import java.util.Date
import java.util.Locale

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun SavesScreen(
    viewModel: MainViewModel,
    syncStateEntities: List<SyncStateEntity>,
    onNavigateToDetail: (String) -> Unit = {},
    onNavigateToTab: (Int) -> Unit = {},
) {
    val context = LocalContext.current
    val saves by viewModel.saves.collectAsState()
    val settings by viewModel.settings.collectAsState()
    val syncState by viewModel.syncState.collectAsState()
    val selectedFilter by viewModel.selectedFilter.collectAsState()
    val availableFilters by viewModel.availableFilters.collectAsState()
    val searchQuery by viewModel.searchQuery.collectAsState()
    val statusFilter by viewModel.statusFilter.collectAsState()
    val availableStatusFilters by viewModel.availableStatusFilters.collectAsState()
    // Shared with SaveDetailScreen: the outcome of an A-button smart sync.
    val saveDetailState by viewModel.saveDetailState.collectAsState()
    val snackbarHostState = remember { SnackbarHostState() }
    // Title id of the save A just smart-synced, so a conflict it reports
    // can open that save's detail screen (where Force Upload / Download live).
    var pendingSyncTitle by remember { mutableStateOf<String?>(null) }

    var searchVisible by remember { mutableStateOf(false) }
    var showSyncConfirmDialog by remember { mutableStateOf(false) }

    // ── Manual D-pad selection state ─────────────────────────────────────
    // We track the selected index ourselves and scroll the list manually so
    // the gamepad cursor doesn't leak to the toolbar actions above it.
    var selectedIndex by remember { mutableIntStateOf(0) }
    val listState = rememberLazyListState()
    val coroutineScope = rememberCoroutineScope()
    // Hold-to-accelerate state for d-pad left/right: page scroll → faster
    // page scroll → alphabet jump.  See HoldNav.kt.
    val holdNav = rememberHoldNavState()
    val searchFocusRequester = remember { FocusRequester() }
    // Parent container steals focus on entry so the search TextField below
    // doesn't auto-focus and pop the keyboard when this tab opens.
    val listFocusRequester = remember { FocusRequester() }

    val webLibraryUrl = remember(settings.serverUrl) { buildWebLibraryUrl(settings.serverUrl) }

    // Clamp selection when list size changes (e.g. filter applied)
    LaunchedEffect(saves.size) {
        if (saves.isNotEmpty()) {
            selectedIndex = selectedIndex.coerceIn(0, saves.size - 1)
        }
    }

    // When search becomes visible, focus the text field
    LaunchedEffect(searchVisible) {
        if (searchVisible) {
            searchFocusRequester.requestFocus()
        }
    }

    // On entry, claim focus for the list container so the search TextField
    // doesn't auto-focus. Matches the Steam Deck "land on the game list, not
    // the search" behaviour.
    LaunchedEffect(Unit) {
        runCatching { listFocusRequester.requestFocus() }
    }

    // SELECT steps the system filter (the sub-tab chip) — the Activity emits
    // on systemCycleEvents and we apply it here so the chip stays in sync.
    val activity = context.findComponentActivity() as? MainActivity
    LaunchedEffect(activity, availableFilters, selectedFilter) {
        activity?.systemCycleEvents?.collect { delta ->
            if (availableFilters.isEmpty()) return@collect
            val idx = availableFilters.indexOf(selectedFilter).let { if (it < 0) 0 else it }
            val next = (idx + delta + availableFilters.size) % availableFilters.size
            viewModel.setFilter(availableFilters[next])
        }
    }
    // L2 / R2 step the sync-status filter (All → each status present → All).
    LaunchedEffect(activity, availableStatusFilters, statusFilter) {
        activity?.statusCycleEvents?.collect { delta ->
            val all = listOf<SaveSyncStatus?>(null) + availableStatusFilters
            val idx = all.indexOf(statusFilter).let { if (it < 0) 0 else it }
            val next = (idx + delta + all.size) % all.size
            viewModel.setStatusFilter(all[next])
        }
    }

    // Outcome of an A-button smart sync.  A conflict can only be settled on
    // the detail screen (Force Upload / Force Download), so go there.
    LaunchedEffect(saveDetailState) {
        when (val s = saveDetailState) {
            is SaveDetailState.Success -> {
                val conflictTitle = pendingSyncTitle
                pendingSyncTitle = null
                viewModel.resetDetailState()
                if (conflictTitle != null && s.message.contains("Conflict")) {
                    onNavigateToDetail(conflictTitle)
                } else {
                    snackbarHostState.showSnackbar(s.message)
                }
            }
            is SaveDetailState.Error -> {
                pendingSyncTitle = null
                viewModel.resetDetailState()
                snackbarHostState.showSnackbar("Error: ${s.message}")
            }
            else -> Unit
        }
    }

    // Show sync result in snackbar
    LaunchedEffect(syncState) {
        when (val state = syncState) {
            is SyncState.Success -> {
                val r = state.result
                val msg = "Sync done — ↑${r.uploaded} ↓${r.downloaded}" +
                        (if (r.conflicts.isNotEmpty()) " ⚠${r.conflicts.size} conflicts" else "") +
                        (if (r.errors.isNotEmpty()) " ✗${r.errors.size} errors" else "")
                snackbarHostState.showSnackbar(msg)
                viewModel.resetSyncState()
            }
            is SyncState.Error -> {
                snackbarHostState.showSnackbar("Error: ${state.message}")
                viewModel.resetSyncState()
            }
            else -> Unit
        }
    }

    val isSyncing = syncState is SyncState.Syncing
    val isSyncingOne = saveDetailState is SaveDetailState.Working

    /**
     * A: smart sync the highlighted save — upload or download per its
     * status.  Saturn saves need the archive picker on the detail screen,
     * so they open it instead; a conflict found by the sync does too.
     */
    fun smartSyncSelected() {
        val entry = saves.getOrNull(selectedIndex) ?: return
        if (isSyncing || isSyncingOne) return
        if (entry.systemName == "SAT") {
            onNavigateToDetail(entry.titleId)
            return
        }
        pendingSyncTitle = entry.titleId
        viewModel.syncSave(entry)
    }

    val syncCountLabel = when {
        selectedFilter == "All" -> "Sync all ${saves.size} saves?"
        else -> "Sync ${saves.size} $selectedFilter saves?"
    }

    Scaffold(
        topBar = {
            GsTopBar(
                activeTabIndex = 0,
                onTabClick = onNavigateToTab,
                actions = {
                    if (saves.isNotEmpty()) {
                        Text(
                            text = "${saves.size}",
                            style = MaterialTheme.typography.labelMedium,
                            color = GsColors.Dim,
                            modifier = Modifier.padding(end = 4.dp)
                        )
                    }
                    // Sync all (X button); spins for a single A sync too
                    if (isSyncing || isSyncingOne) {
                        Box(modifier = Modifier.size(48.dp), contentAlignment = Alignment.Center) {
                            CircularProgressIndicator(
                                modifier = Modifier.size(20.dp),
                                strokeWidth = 2.dp,
                                color = GsColors.Accent
                            )
                        }
                    } else {
                        IconButton(onClick = { showSyncConfirmDialog = true }) {
                            Icon(Icons.Default.Sync, contentDescription = "Sync all (X)", tint = GsColors.Text)
                        }
                    }
                    // Search (touch; Y opens details on a controller)
                    IconButton(onClick = {
                        searchVisible = !searchVisible
                        if (!searchVisible) viewModel.setSearchQuery("")
                    }) {
                        Icon(Icons.Default.Search, contentDescription = "Search", tint = GsColors.Text)
                    }
                    // Web ROM library (touch only)
                    IconButton(
                        onClick = {
                            webLibraryUrl?.let { url ->
                                context.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(url)))
                            }
                        },
                        enabled = webLibraryUrl != null
                    ) {
                        Icon(Icons.Default.Language, contentDescription = "Web ROM Library", tint = GsColors.Text)
                    }
                },
                subTabs = {
                    SystemFilterChip(
                        label = selectedFilter,
                        options = availableFilters,
                        onSelect = { viewModel.setFilter(it) },
                    )
                    if (availableStatusFilters.isNotEmpty() || statusFilter != null) {
                        StatusFilterChip(
                            statusFilter = statusFilter,
                            options = availableStatusFilters,
                            onSelect = { viewModel.setStatusFilter(it) },
                        )
                    }
                },
            )
        },
        bottomBar = {
            GsFooterHints(
                if (searchVisible) listOf(
                    GsHint(GsButton.A, "Sync"),
                    GsHint(GsButton.B, "Close search"),
                    GsHint(GsButton.X, "Sync all"),
                    GsHint(GsButton.Y, "Details"),
                ) else listOf(
                    GsHint(GsButton.A, "Sync"),
                    GsHint(GsButton.X, "Sync all"),
                    GsHint(GsButton.Y, "Details"),
                    GsHint(GsButton.SELECT, "System"),
                    GsHint(GsButton.L2R2, "Status"),
                    GsHint(GsButton.L1, "Tabs"),
                    GsHint(GsButton.START, "Exit"),
                )
            )
        },
        snackbarHost = { SnackbarHost(snackbarHostState) }
    ) { paddingValues ->
        // ── Master gamepad handler ───────────────────────────────────────
        // D-pad up/down steps the list, left/right page-scrolls with hold
        // acceleration (then letter jumps).  A smart-syncs the row, X syncs
        // everything, Y opens details, B only closes search.  L1/R1 (tabs),
        // L2/R2 (status filter), SELECT (system) and START (exit) are
        // handled at the Activity level and never reach here.
        SwipeRefresh(
            state = rememberSwipeRefreshState(isRefreshing = isSyncing),
            onRefresh = { viewModel.scanSaves() },
            modifier = Modifier
                .fillMaxSize()
                .padding(paddingValues)
                .focusRequester(listFocusRequester)
                .focusable()
                .onPreviewKeyEvent { event ->
                    if (holdNav.handleHorizontalHoldKeyEvent(
                            event,
                            coroutineScope,
                            onPage = { d ->
                                if (saves.isNotEmpty()) {
                                    val page = listState.layoutInfo.visibleItemsInfo.size
                                        .coerceAtLeast(1)
                                    selectedIndex = (selectedIndex + d * page)
                                        .coerceIn(0, saves.size - 1)
                                    coroutineScope.launch {
                                        listState.animateScrollToItem(selectedIndex)
                                    }
                                }
                            },
                            onAlphabet = { d ->
                                if (saves.isNotEmpty()) {
                                    val cur = selectedIndex.coerceIn(0, saves.size - 1)
                                    val curLetter = firstLetter(saves[cur].displayName)
                                    val step = if (d > 0) 1 else -1
                                    var row = cur + step
                                    var found = -1
                                    while (row in saves.indices) {
                                        val ltr = firstLetter(saves[row].displayName)
                                        if (ltr.isNotEmpty() && ltr != curLetter) {
                                            found = row; break
                                        }
                                        row += step
                                    }
                                    selectedIndex = if (found >= 0) found
                                        else if (d > 0) saves.size - 1 else 0
                                    coroutineScope.launch {
                                        listState.animateScrollToItem(selectedIndex)
                                    }
                                }
                            },
                        )
                    ) return@onPreviewKeyEvent true
                    if (event.type != KeyEventType.KeyDown) return@onPreviewKeyEvent false
                    when (event.key) {
                        // D-pad / analog stick — vertical: scroll list
                        Key.DirectionDown -> {
                            if (saves.isNotEmpty()) {
                                selectedIndex = (selectedIndex + 1).coerceAtMost(saves.size - 1)
                                coroutineScope.launch {
                                    listState.animateScrollToItem(selectedIndex)
                                }
                            }
                            true
                        }
                        Key.DirectionUp -> {
                            if (saves.isNotEmpty()) {
                                selectedIndex = (selectedIndex - 1).coerceAtLeast(0)
                                coroutineScope.launch {
                                    listState.animateScrollToItem(selectedIndex)
                                }
                            }
                            true
                        }
                        // A → smart sync the selected save
                        Key.ButtonA -> {
                            smartSyncSelected()
                            true
                        }
                        // Enter (keyboard) keeps opening the detail screen
                        Key.Enter -> {
                            saves.getOrNull(selectedIndex)?.let { onNavigateToDetail(it.titleId) }
                            true
                        }
                        // B / Escape → close search if open; otherwise
                        // unclaimed (the Activity swallows it on a tab).
                        Key.ButtonB, Key.Escape, Key.Back -> {
                            if (searchVisible) {
                                searchVisible = false
                                viewModel.setSearchQuery("")
                                runCatching { listFocusRequester.requestFocus() }
                                true
                            } else false
                        }
                        // Y → details of the selected save
                        Key.ButtonY -> {
                            saves.getOrNull(selectedIndex)?.let { onNavigateToDetail(it.titleId) }
                            true
                        }
                        // X → sync all (with confirmation)
                        Key.ButtonX -> {
                            if (!isSyncing) showSyncConfirmDialog = true
                            true
                        }
                        else -> false
                    }
                }
        ) {
            Column(modifier = Modifier.fillMaxSize()) {
                if (searchVisible) {
                    SearchBar(
                        query = searchQuery,
                        onQueryChange = { viewModel.setSearchQuery(it) },
                        onDismiss = {
                            searchVisible = false
                            viewModel.setSearchQuery("")
                        },
                        modifier = Modifier.focusRequester(searchFocusRequester)
                    )
                }

                if (saves.isEmpty()) {
                    EmptyState(
                        activeFilter = selectedFilter,
                        statusFilter = statusFilter,
                        searchQuery = searchQuery
                    )
                } else {
                    SavesList(
                        saves = saves,
                        syncStateEntities = syncStateEntities,
                        viewModel = viewModel,
                        listState = listState,
                        selectedIndex = selectedIndex,
                        onSaveClick = onNavigateToDetail,
                        onSelectIndex = { selectedIndex = it },
                        modifier = Modifier.weight(1f)
                    )
                }
            }
        }
    }

    // ── Sync confirmation dialog ─────────────────────────────────────────
    if (showSyncConfirmDialog) {
        GsConfirmDialog(
            title = "Sync all",
            message = syncCountLabel,
            confirmLabel = "Sync",
            onConfirm = {
                showSyncConfirmDialog = false
                viewModel.syncNow()
            },
            onDismiss = { showSyncConfirmDialog = false },
        )
    }
}

@Composable
private fun SearchBar(
    query: String,
    onQueryChange: (String) -> Unit,
    onDismiss: () -> Unit,
    modifier: Modifier = Modifier
) {
    OutlinedTextField(
        value = query,
        onValueChange = onQueryChange,
        modifier = modifier
            .fillMaxWidth()
            .padding(horizontal = 12.dp, vertical = 4.dp)
            .onPreviewKeyEvent { event ->
                if (event.type != KeyEventType.KeyDown) return@onPreviewKeyEvent false
                when (event.key) {
                    Key.Escape, Key.ButtonB, Key.Back -> {
                        onDismiss()
                        true
                    }
                    else -> false
                }
            },
        placeholder = { Text("Search games… (Esc to close)") },
        leadingIcon = {
            Icon(
                Icons.Default.Search,
                contentDescription = "Search",
                tint = MaterialTheme.colorScheme.onSurfaceVariant
            )
        },
        trailingIcon = {
            IconButton(onClick = {
                if (query.isNotEmpty()) onQueryChange("") else onDismiss()
            }) {
                Icon(
                    Icons.Default.Clear,
                    contentDescription = if (query.isNotEmpty()) "Clear search" else "Close search",
                    tint = MaterialTheme.colorScheme.onSurfaceVariant
                )
            }
        },
        singleLine = true,
        colors = OutlinedTextFieldDefaults.colors(
            focusedContainerColor = MaterialTheme.colorScheme.surface,
            unfocusedContainerColor = MaterialTheme.colorScheme.surface
        ),
        shape = MaterialTheme.shapes.medium
    )
}

@Composable
private fun EmptyState(
    activeFilter: String,
    statusFilter: SaveSyncStatus?,
    searchQuery: String
) {
    Box(
        modifier = Modifier.fillMaxSize(),
        contentAlignment = Alignment.Center
    ) {
        Column(horizontalAlignment = Alignment.CenterHorizontally) {
            val hasAnyFilter = activeFilter != "All" || statusFilter != null || searchQuery.isNotBlank()
            if (hasAnyFilter) {
                Text(
                    text = "No matching saves",
                    style = MaterialTheme.typography.titleMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant
                )
                Spacer(Modifier.height(8.dp))
                Text(
                    text = "Try adjusting your filters or search query.",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant
                )
            } else {
                Text(
                    text = "No saves found",
                    style = MaterialTheme.typography.titleMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant
                )
                Spacer(Modifier.height(8.dp))
                Text(
                    text = "Install an emulator and create some save files,\nthen pull down to refresh.",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant
                )
            }
        }
    }
}

@Composable
private fun SavesList(
    saves: List<SaveEntry>,
    syncStateEntities: List<SyncStateEntity>,
    viewModel: MainViewModel,
    listState: androidx.compose.foundation.lazy.LazyListState,
    selectedIndex: Int,
    onSaveClick: (String) -> Unit,
    onSelectIndex: (Int) -> Unit,
    modifier: Modifier = Modifier
) {
    val syncMap = syncStateEntities.associateBy { it.titleId }

    LazyColumn(
        modifier = modifier.fillMaxWidth(),
        state = listState,
        verticalArrangement = Arrangement.spacedBy(4.dp),
        contentPadding = PaddingValues(horizontal = 12.dp, vertical = 8.dp)
    ) {
        itemsIndexed(saves, key = { _, entry -> entry.titleId }) { index, entry ->
            val syncStatus = viewModel.computeSyncStatus(entry, syncMap[entry.titleId], cheapOnly = true)
            val isSelected = index == selectedIndex
            SaveCard(
                entry = entry,
                syncState = syncMap[entry.titleId],
                syncStatus = syncStatus,
                isSelected = isSelected,
                onClick = {
                    onSelectIndex(index)
                    onSaveClick(entry.titleId)
                }
            )
        }
    }
}

/**
 * Compact single-row card: game name on the left, system + sync status on the right.
 * [isSelected] draws a highlight border for D-pad/gamepad cursor visibility.
 */
@Composable
private fun SaveCard(
    entry: SaveEntry,
    syncState: SyncStateEntity?,
    syncStatus: SaveSyncStatus,
    isSelected: Boolean,
    onClick: () -> Unit,
    modifier: Modifier = Modifier
) {
    GsListRow(isSelected = isSelected, onClick = onClick, modifier = modifier) {
        // Left: game name + optional canonical subtitle
        Column(modifier = Modifier.weight(1f)) {
            Text(
                text = entry.displayName,
                style = MaterialTheme.typography.bodyMedium,
                fontWeight = FontWeight.SemiBold,
                color = GsColors.Text,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis
            )
            entry.canonicalName?.let { canonical ->
                Text(
                    text = canonical,
                    style = MaterialTheme.typography.labelSmall,
                    color = GsColors.Dim,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis
                )
            }
        }

        // Right: system, last-synced time, status pill
        Row(
            horizontalArrangement = Arrangement.spacedBy(6.dp),
            verticalAlignment = Alignment.CenterVertically
        ) {
            SystemBadge(entry.systemName)
            if (!entry.isServerOnly) {
                syncState?.lastSyncedAt?.let { ts ->
                    Text(
                        text = formatTimestamp(ts),
                        style = MaterialTheme.typography.labelSmall,
                        color = GsColors.Muted
                    )
                }
            }
            SyncStatusBadge(syncStatus)
        }
    }
}

/**
 * Sub-tab chip for the sync-status filter (L2/R2 step it; tap for the list).
 */
@Composable
private fun StatusFilterChip(
    statusFilter: SaveSyncStatus?,
    options: List<SaveSyncStatus>,
    onSelect: (SaveSyncStatus?) -> Unit,
) {
    var expanded by remember { mutableStateOf(false) }
    Box {
        Row(
            modifier = Modifier
                .clip(RoundedCornerShape(16.dp))
                .background(GsColors.Panel)
                .clickable { expanded = true }
                .padding(horizontal = 10.dp, vertical = 4.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(6.dp),
        ) {
            if (LocalShowButtonHints.current) ButtonGlyph(GsButton.L2R2)
            Icon(
                Icons.Default.FilterList,
                contentDescription = "Status filter",
                tint = if (statusFilter != null) statusChipColor(statusFilter) else GsColors.Dim,
                modifier = Modifier.size(16.dp),
            )
            Text(
                text = statusFilter?.label ?: "All status",
                style = MaterialTheme.typography.labelLarge,
                color = if (statusFilter != null) statusChipColor(statusFilter) else GsColors.Dim,
                maxLines = 1,
            )
        }
        DropdownMenu(
            expanded = expanded,
            onDismissRequest = { expanded = false }
        ) {
            DropdownMenuItem(
                text = {
                    Text(
                        text = "All status",
                        fontWeight = if (statusFilter == null) FontWeight.Bold else FontWeight.Normal
                    )
                },
                onClick = {
                    onSelect(null)
                    expanded = false
                },
                leadingIcon = if (statusFilter == null) {
                    { Text("✓", fontWeight = FontWeight.Bold) }
                } else null
            )
            options.forEach { status ->
                DropdownMenuItem(
                    text = {
                        Text(
                            text = "${statusIcon(status)} ${status.label}",
                            fontWeight = if (status == statusFilter) FontWeight.Bold else FontWeight.Normal,
                            color = statusChipColor(status)
                        )
                    },
                    onClick = {
                        onSelect(if (status == statusFilter) null else status)
                        expanded = false
                    },
                    leadingIcon = if (status == statusFilter) {
                        { Text("✓", fontWeight = FontWeight.Bold) }
                    } else null
                )
            }
        }
    }
}

// ── Non-focusable badge composables ──────────────────────────────────────────

@Composable
fun SyncStatusBadge(status: SaveSyncStatus) {
    GsPill(text = "${statusIcon(status)} ${status.label}", color = statusChipColor(status))
}

/** System code tag: a quiet outlined chip, so the status pill stays the loud one. */
@Composable
fun SystemBadge(systemName: String) {
    Box(
        modifier = Modifier
            .clip(RoundedCornerShape(6.dp))
            .background(GsColors.Bg2)
            .border(1.dp, GsColors.Line, RoundedCornerShape(6.dp))
            .padding(horizontal = 6.dp, vertical = 2.dp)
    ) {
        Text(
            text = systemName,
            style = MaterialTheme.typography.labelSmall,
            fontWeight = FontWeight.Bold,
            color = GsColors.Accent2
        )
    }
}

@Composable
fun SystemChip(systemName: String) = SystemBadge(systemName)

/**
 * RetroAchievements badge in RA gold, so it reads as a reward rather than
 * as another sync status.  A title-only match is outlined ("RA?").
 */
@Composable
fun RaBadge(titleOnly: Boolean = false) {
    if (titleOnly) {
        Box(
            modifier = Modifier
                .clip(RoundedCornerShape(10.dp))
                .border(1.dp, GsColors.Ra, RoundedCornerShape(10.dp))
                .padding(horizontal = 7.dp, vertical = 2.dp)
        ) {
            Text("RA?", style = MaterialTheme.typography.labelSmall,
                fontWeight = FontWeight.Bold, color = GsColors.Ra)
        }
    } else {
        GsPill(text = "RA", color = GsColors.Ra, filled = true)
    }
}

// ── Helper functions ─────────────────────────────────────────────────────────

private fun statusIcon(status: SaveSyncStatus): String = when (status) {
    SaveSyncStatus.SYNCED -> "✓"
    SaveSyncStatus.LOCAL_ONLY -> "●"
    SaveSyncStatus.SERVER_ONLY -> "☁"
    SaveSyncStatus.LOCAL_NEWER -> "↑"
    SaveSyncStatus.SERVER_NEWER -> "↓"
    SaveSyncStatus.CONFLICT -> "⚠"
    SaveSyncStatus.UNKNOWN -> "?"
}

/** Status pill colours, shared with every console client. */
fun statusChipColor(status: SaveSyncStatus): Color = when (status) {
    SaveSyncStatus.SYNCED -> GsColors.Ok
    SaveSyncStatus.LOCAL_ONLY -> GsColors.Info
    SaveSyncStatus.SERVER_ONLY -> GsColors.Warn
    SaveSyncStatus.LOCAL_NEWER -> GsColors.Info
    SaveSyncStatus.SERVER_NEWER -> GsColors.Info
    SaveSyncStatus.CONFLICT -> GsColors.Err
    SaveSyncStatus.UNKNOWN -> GsColors.Dim
}

fun systemChipColor(systemName: String): Color {
    return when (systemName.uppercase()) {
        "GBA"             -> Color(0xFF6A1B9A)
        "GBC"             -> Color(0xFF8E24AA)
        "GB"              -> Color(0xFF546E7A)
        "NDS", "DS"       -> Color(0xFF1565C0)
        "3DS"             -> Color(0xFF0277BD)
        "NES", "FC"       -> Color(0xFFB71C1C)
        "SNES", "SFC"     -> Color(0xFFE65100)
        "N64"             -> Color(0xFF558B2F)
        "GC"              -> Color(0xFF7B1FA2)
        "WII"             -> Color(0xFF00838F)
        "WIIU"            -> Color(0xFF006064)
        "PS1", "PSX"      -> Color(0xFF1A237E)
        "PS2"             -> Color(0xFF0D47A1)
        "PSP", "PPSSPP"   -> Color(0xFF01579B)
        "GEN", "MD"       -> Color(0xFF37474F)
        "SMS"             -> Color(0xFF455A64)
        "SG1000"          -> Color(0xFF1E88E5)
        "GG"              -> Color(0xFF4CAF50)
        "SEGACD"          -> Color(0xFF263238)
        "SAT"             -> Color(0xFF4E342E)
        "DC"              -> Color(0xFFF57C00)
        "ARCADE", "FBA",
        "MAME"            -> Color(0xFFC62828)
        "NEOCD", "NGP"    -> Color(0xFFAD1457)
        "PCE", "TG16"     -> Color(0xFF00695C)
        "PCECD", "TGCD"   -> Color(0xFF00838F)
        "PCSG"            -> Color(0xFF689F38)
        "PCFX"            -> Color(0xFF827717)
        "WSWAN", "WSWANC" -> Color(0xFF2E7D32)
        "LYNX"            -> Color(0xFF4527A0)
        "A2600", "A7800"  -> Color(0xFF6D4C41)
        "RETRO"           -> Color(0xFF37474F)
        else              -> Color(0xFF546E7A)
    }
}

private fun formatTimestamp(millis: Long): String {
    val sdf = SimpleDateFormat("MMM d, HH:mm", Locale.getDefault())
    return sdf.format(Date(millis))
}

private fun buildWebLibraryUrl(serverUrl: String): String? {
    val trimmed = serverUrl.trim()
    if (trimmed.isBlank()) return null

    return runCatching {
        val apiUri = URI(trimmed)
        val scheme = apiUri.scheme ?: return null
        val host = apiUri.host ?: return null
        val port = when (apiUri.port) {
            8000 -> 80
            -1 -> -1
            else -> apiUri.port
        }
        URI(scheme, null, host, port, "/", null, null).toString()
    }.getOrNull()
}
