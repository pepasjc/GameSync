package com.savesync.android.ui.screens

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
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
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import com.google.accompanist.swiperefresh.SwipeRefresh
import com.google.accompanist.swiperefresh.rememberSwipeRefreshState
import com.savesync.android.emulators.SaveEntry
import com.savesync.android.storage.SyncStateEntity
import com.savesync.android.ui.MainViewModel
import com.savesync.android.ui.SaveSyncStatus
import com.savesync.android.ui.SyncState
import kotlinx.coroutines.launch
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun SavesScreen(
    viewModel: MainViewModel,
    syncStateEntities: List<SyncStateEntity>,
    onNavigateToSettings: () -> Unit,
    onNavigateToDetail: (String) -> Unit = {}
) {
    val saves by viewModel.saves.collectAsState()
    val syncState by viewModel.syncState.collectAsState()
    val selectedFilter by viewModel.selectedFilter.collectAsState()
    val availableFilters by viewModel.availableFilters.collectAsState()
    val searchQuery by viewModel.searchQuery.collectAsState()
    val statusFilter by viewModel.statusFilter.collectAsState()
    val availableStatusFilters by viewModel.availableStatusFilters.collectAsState()
    val snackbarHostState = remember { SnackbarHostState() }

    var searchVisible by remember { mutableStateOf(false) }
    var filterMenuExpanded by remember { mutableStateOf(false) }
    var showSyncConfirmDialog by remember { mutableStateOf(false) }

    // ── Manual D-pad selection state ─────────────────────────────────────
    // Instead of relying on Compose's focus system (which leaks to toolbar),
    // we track the selected index ourselves and scroll the list manually.
    var selectedIndex by remember { mutableIntStateOf(0) }
    val listState = rememberLazyListState()
    val coroutineScope = rememberCoroutineScope()
    val searchFocusRequester = remember { FocusRequester() }

    // ── Toolbar focus mode ───────────────────────────────────────────────
    // Y button cycles between game list and toolbar. In toolbar mode,
    // D-pad left/right selects toolbar items, A activates, Y/B returns.
    // 0 = Search, 1 = Filter, 2 = Settings
    var toolbarMode by remember { mutableStateOf(false) }
    var toolbarSelectedIndex by remember { mutableIntStateOf(0) }
    val toolbarItemCount = 4 // Sync, Search, Filter, Settings

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

    val syncCountLabel = when {
        selectedFilter == "All" -> "Sync all ${saves.size} saves?"
        else -> "Sync ${saves.size} $selectedFilter saves?"
    }

    val filterLabel = buildString {
        if (selectedFilter != "All") append(selectedFilter)
        if (statusFilter != null) {
            if (isNotEmpty()) append(" · ")
            append(statusFilter!!.label)
        }
        if (searchQuery.isNotBlank()) {
            if (isNotEmpty()) append(" · ")
            append("\"$searchQuery\"")
        }
    }

    Scaffold(
        topBar = {
            TopAppBar(
                title = {
                    Column {
                        Text("Save Sync", style = MaterialTheme.typography.titleMedium)
                        if (filterLabel.isNotEmpty()) {
                            Text(
                                text = filterLabel,
                                style = MaterialTheme.typography.labelSmall,
                                color = MaterialTheme.colorScheme.onPrimary.copy(alpha = 0.8f),
                                maxLines = 1,
                                overflow = TextOverflow.Ellipsis
                            )
                        }
                    }
                },
                colors = TopAppBarDefaults.topAppBarColors(
                    containerColor = MaterialTheme.colorScheme.primary,
                    titleContentColor = MaterialTheme.colorScheme.onPrimary,
                    actionIconContentColor = MaterialTheme.colorScheme.onPrimary
                ),
                actions = {
                    if (saves.isNotEmpty()) {
                        Text(
                            text = "${saves.size}",
                            style = MaterialTheme.typography.labelMedium,
                            color = MaterialTheme.colorScheme.onPrimary.copy(alpha = 0.7f),
                            modifier = Modifier.padding(end = 4.dp)
                        )
                    }
                    // Toolbar item 0: Sync
                    val syncHighlight = toolbarMode && toolbarSelectedIndex == 0
                    Box(
                        modifier = if (syncHighlight) Modifier
                            .clip(RoundedCornerShape(8.dp))
                            .background(MaterialTheme.colorScheme.onPrimary.copy(alpha = 0.25f))
                        else Modifier
                    ) {
                        if (isSyncing) {
                            Box(modifier = Modifier.size(48.dp), contentAlignment = Alignment.Center) {
                                CircularProgressIndicator(
                                    modifier = Modifier.size(20.dp),
                                    strokeWidth = 2.dp,
                                    color = MaterialTheme.colorScheme.onPrimary
                                )
                            }
                        } else {
                            IconButton(onClick = { showSyncConfirmDialog = true }) {
                                Icon(Icons.Default.Sync, contentDescription = "Sync (X)")
                            }
                        }
                    }
                    // Toolbar item 1: Search
                    val searchHighlight = toolbarMode && toolbarSelectedIndex == 1
                    Box(
                        modifier = if (searchHighlight) Modifier
                            .clip(RoundedCornerShape(8.dp))
                            .background(MaterialTheme.colorScheme.onPrimary.copy(alpha = 0.25f))
                        else Modifier
                    ) {
                        IconButton(onClick = {
                            searchVisible = !searchVisible
                            if (!searchVisible) viewModel.setSearchQuery("")
                            toolbarMode = false
                        }) {
                            Icon(Icons.Default.Search, contentDescription = "Search (Y)")
                        }
                    }
                    // Toolbar item 2: Filter
                    val filterHighlight = toolbarMode && toolbarSelectedIndex == 2
                    Box(
                        modifier = if (filterHighlight) Modifier
                            .clip(RoundedCornerShape(8.dp))
                            .background(MaterialTheme.colorScheme.onPrimary.copy(alpha = 0.25f))
                        else Modifier
                    ) {
                        IconButton(onClick = {
                            filterMenuExpanded = true
                            toolbarMode = false
                        }) {
                            Icon(Icons.Default.FilterList, contentDescription = "Filters (L1/R1)")
                        }
                        DropdownMenu(
                            expanded = filterMenuExpanded,
                            onDismissRequest = { filterMenuExpanded = false }
                        ) {
                            Text(
                                "System",
                                style = MaterialTheme.typography.labelSmall,
                                color = MaterialTheme.colorScheme.onSurfaceVariant,
                                modifier = Modifier.padding(horizontal = 12.dp, vertical = 4.dp)
                            )
                            availableFilters.forEach { filter ->
                                DropdownMenuItem(
                                    text = {
                                        Text(
                                            text = filter,
                                            fontWeight = if (filter == selectedFilter) FontWeight.Bold else FontWeight.Normal
                                        )
                                    },
                                    onClick = {
                                        viewModel.setFilter(filter)
                                        filterMenuExpanded = false
                                    },
                                    leadingIcon = if (filter == selectedFilter) {
                                        { Text("✓", fontWeight = FontWeight.Bold) }
                                    } else null
                                )
                            }
                            if (availableStatusFilters.isNotEmpty()) {
                                Text(
                                    "Status",
                                    style = MaterialTheme.typography.labelSmall,
                                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                                    modifier = Modifier.padding(horizontal = 12.dp, vertical = 4.dp)
                                )
                                DropdownMenuItem(
                                    text = {
                                        Text(
                                            text = "All Status",
                                            fontWeight = if (statusFilter == null) FontWeight.Bold else FontWeight.Normal
                                        )
                                    },
                                    onClick = {
                                        viewModel.setStatusFilter(null)
                                        filterMenuExpanded = false
                                    },
                                    leadingIcon = if (statusFilter == null) {
                                        { Text("✓", fontWeight = FontWeight.Bold) }
                                    } else null
                                )
                                availableStatusFilters.forEach { status ->
                                    DropdownMenuItem(
                                        text = {
                                            Text(
                                                text = "${statusIcon(status)} ${status.label}",
                                                fontWeight = if (status == statusFilter) FontWeight.Bold else FontWeight.Normal,
                                                color = statusChipColor(status)
                                            )
                                        },
                                        onClick = {
                                            viewModel.setStatusFilter(if (status == statusFilter) null else status)
                                            filterMenuExpanded = false
                                        },
                                        leadingIcon = if (status == statusFilter) {
                                            { Text("✓", fontWeight = FontWeight.Bold) }
                                        } else null
                                    )
                                }
                            }
                        }
                    }
                    // Toolbar item 3: Settings
                    val settingsHighlight = toolbarMode && toolbarSelectedIndex == 3
                    Box(
                        modifier = if (settingsHighlight) Modifier
                            .clip(RoundedCornerShape(8.dp))
                            .background(MaterialTheme.colorScheme.onPrimary.copy(alpha = 0.25f))
                        else Modifier
                    ) {
                        IconButton(onClick = {
                            onNavigateToSettings()
                            toolbarMode = false
                        }) {
                            Icon(Icons.Default.Settings, contentDescription = "Settings (Start)")
                        }
                    }
                }
            )
        },
        snackbarHost = { SnackbarHost(snackbarHostState) }
    ) { paddingValues ->
        // ── Master key handler ───────────────────────────────────────────
        // Intercept ALL D-pad / gamepad keys at the top level.
        // D-pad up/down changes selectedIndex, A confirms, shoulder buttons
        // cycle filters. This completely bypasses Compose's focus traversal.
        SwipeRefresh(
            state = rememberSwipeRefreshState(isRefreshing = isSyncing),
            onRefresh = { viewModel.scanSaves() },
            modifier = Modifier
                .fillMaxSize()
                .padding(paddingValues)
                .onPreviewKeyEvent { event ->
                    if (event.type != KeyEventType.KeyDown) return@onPreviewKeyEvent false

                    // ── Toolbar mode: D-pad navigates toolbar items ──────
                    if (toolbarMode) {
                        when (event.key) {
                            Key.DirectionLeft -> {
                                toolbarSelectedIndex = (toolbarSelectedIndex - 1 + toolbarItemCount) % toolbarItemCount
                                true
                            }
                            Key.DirectionRight -> {
                                toolbarSelectedIndex = (toolbarSelectedIndex + 1) % toolbarItemCount
                                true
                            }
                            // A / Enter → activate selected toolbar item
                            Key.ButtonA, Key.Enter -> {
                                when (toolbarSelectedIndex) {
                                    0 -> { // Sync
                                        if (!isSyncing) showSyncConfirmDialog = true
                                    }
                                    1 -> { // Search
                                        searchVisible = !searchVisible
                                        if (!searchVisible) viewModel.setSearchQuery("")
                                    }
                                    2 -> filterMenuExpanded = true  // Filter
                                    3 -> onNavigateToSettings()     // Settings
                                }
                                toolbarMode = false
                                true
                            }
                            // Y or B → return to list mode
                            Key.ButtonY, Key.ButtonB, Key.Escape -> {
                                toolbarMode = false
                                true
                            }
                            // Consume everything else in toolbar mode
                            Key.DirectionUp, Key.DirectionDown -> true
                            else -> false
                        }
                    } else {
                        // ── List mode: normal D-pad + gamepad shortcuts ──
                        when (event.key) {
                            // D-pad / analog stick navigation
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
                            // A / Enter → open selected game
                            Key.ButtonA, Key.Enter -> {
                                if (saves.isNotEmpty()) {
                                    onNavigateToDetail(saves[selectedIndex].titleId)
                                }
                                true
                            }
                            // Y → cycle to toolbar mode
                            Key.ButtonY -> {
                                toolbarMode = true
                                toolbarSelectedIndex = 0
                                true
                            }
                            // X → sync (with confirmation)
                            Key.ButtonX -> {
                                if (!isSyncing) showSyncConfirmDialog = true
                                true
                            }
                            // Start → settings
                            Key.ButtonStart -> {
                                onNavigateToSettings()
                                true
                            }
                            // L1 → previous system filter
                            Key.ButtonL1 -> {
                                val idx = availableFilters.indexOf(selectedFilter)
                                viewModel.setFilter(availableFilters.getOrElse(idx - 1) { availableFilters.last() })
                                true
                            }
                            // R1 → next system filter
                            Key.ButtonR1 -> {
                                val idx = availableFilters.indexOf(selectedFilter)
                                viewModel.setFilter(availableFilters.getOrElse(idx + 1) { availableFilters.first() })
                                true
                            }
                            // L2 → previous status filter
                            Key.ButtonL2 -> {
                                val all = listOf(null) + availableStatusFilters
                                val idx = all.indexOf(statusFilter)
                                viewModel.setStatusFilter(all.getOrElse(idx - 1) { all.last() })
                                true
                            }
                            // R2 → next status filter
                            Key.ButtonR2 -> {
                                val all = listOf(null) + availableStatusFilters
                                val idx = all.indexOf(statusFilter)
                                viewModel.setStatusFilter(all.getOrElse(idx + 1) { all.first() })
                                true
                            }
                            // Consume left/right so they don't escape to system navigation
                            Key.DirectionLeft, Key.DirectionRight -> true
                            else -> false
                        }
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

                // Toolbar mode indicator at bottom of list area
                if (toolbarMode) {
                    val toolbarItems = listOf("Sync", "Search", "Filter", "Settings")
                    Row(
                        modifier = Modifier
                            .fillMaxWidth()
                            .background(MaterialTheme.colorScheme.surfaceVariant)
                            .padding(horizontal = 12.dp, vertical = 6.dp),
                        horizontalArrangement = Arrangement.Center,
                        verticalAlignment = Alignment.CenterVertically
                    ) {
                        toolbarItems.forEachIndexed { idx, label ->
                            val isActive = idx == toolbarSelectedIndex
                            Box(
                                modifier = Modifier
                                    .clip(RoundedCornerShape(6.dp))
                                    .background(
                                        if (isActive) MaterialTheme.colorScheme.primary
                                        else Color.Transparent
                                    )
                                    .padding(horizontal = 12.dp, vertical = 4.dp)
                            ) {
                                Text(
                                    text = label,
                                    style = MaterialTheme.typography.labelMedium,
                                    fontWeight = if (isActive) FontWeight.Bold else FontWeight.Normal,
                                    color = if (isActive) MaterialTheme.colorScheme.onPrimary
                                            else MaterialTheme.colorScheme.onSurfaceVariant
                                )
                            }
                            if (idx < toolbarItems.size - 1) Spacer(Modifier.width(8.dp))
                        }
                    }
                }
            }
        }
    }

    // ── Sync confirmation dialog ─────────────────────────────────────────
    if (showSyncConfirmDialog) {
        AlertDialog(
            onDismissRequest = { showSyncConfirmDialog = false },
            title = { Text("Confirm Sync") },
            text = { Text(syncCountLabel) },
            confirmButton = {
                TextButton(onClick = {
                    showSyncConfirmDialog = false
                    viewModel.syncNow()
                }) {
                    Text("Sync")
                }
            },
            dismissButton = {
                TextButton(onClick = { showSyncConfirmDialog = false }) {
                    Text("Cancel")
                }
            }
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
        verticalArrangement = Arrangement.spacedBy(6.dp),
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
    val cardColors = when (syncStatus) {
        SaveSyncStatus.CONFLICT -> CardDefaults.cardColors(
            containerColor = Color(0x1AFF5252)
        )
        SaveSyncStatus.SERVER_ONLY -> CardDefaults.cardColors(
            containerColor = MaterialTheme.colorScheme.surfaceVariant
        )
        else -> CardDefaults.cardColors()
    }

    val border = if (isSelected) {
        BorderStroke(2.dp, MaterialTheme.colorScheme.primary)
    } else null

    Card(
        modifier = modifier
            .fillMaxWidth()
            .clickable(onClick = onClick),
        elevation = CardDefaults.cardElevation(
            defaultElevation = if (isSelected) 6.dp else 2.dp
        ),
        colors = cardColors,
        border = border
    ) {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(horizontal = 12.dp, vertical = 8.dp),
            verticalAlignment = Alignment.CenterVertically
        ) {
            // Left: game name + optional canonical subtitle
            Column(modifier = Modifier.weight(1f)) {
                Text(
                    text = entry.displayName,
                    style = MaterialTheme.typography.bodyMedium,
                    fontWeight = FontWeight.SemiBold,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis
                )
                entry.canonicalName?.let { canonical ->
                    Text(
                        text = canonical,
                        style = MaterialTheme.typography.labelSmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis
                    )
                }
            }

            Spacer(Modifier.width(8.dp))

            // Right: system badge, sync badge, timestamp, status icon
            Row(
                horizontalArrangement = Arrangement.spacedBy(4.dp),
                verticalAlignment = Alignment.CenterVertically
            ) {
                SystemBadge(entry.systemName)
                SyncStatusBadge(syncStatus)
                if (!entry.isServerOnly) {
                    syncState?.lastSyncedAt?.let { ts ->
                        Text(
                            text = formatTimestamp(ts),
                            style = MaterialTheme.typography.labelSmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant
                        )
                    }
                }
                SyncStatusIcon(syncStatus)
            }
        }
    }
}

// ── Non-focusable badge composables ──────────────────────────────────────────

@Composable
fun SyncStatusBadge(status: SaveSyncStatus) {
    val bgColor = statusChipColor(status)
    Box(
        modifier = Modifier
            .clip(RoundedCornerShape(8.dp))
            .background(bgColor)
            .padding(horizontal = 8.dp, vertical = 4.dp)
    ) {
        Text(
            text = "${statusIcon(status)} ${status.label}",
            style = MaterialTheme.typography.labelSmall,
            fontWeight = FontWeight.Medium,
            color = Color.White
        )
    }
}

@Composable
fun SystemBadge(systemName: String) {
    val bgColor = systemChipColor(systemName)
    Box(
        modifier = Modifier
            .clip(RoundedCornerShape(8.dp))
            .background(bgColor)
            .padding(horizontal = 8.dp, vertical = 4.dp)
    ) {
        Text(
            text = systemName,
            style = MaterialTheme.typography.labelSmall,
            fontWeight = FontWeight.Bold,
            color = Color.White
        )
    }
}

@Composable
fun SystemChip(systemName: String) = SystemBadge(systemName)

@Composable
private fun SyncStatusIcon(syncStatus: SaveSyncStatus) {
    val (icon, tint) = when (syncStatus) {
        SaveSyncStatus.SYNCED -> "✓" to Color(0xFF4CAF50)
        SaveSyncStatus.LOCAL_ONLY -> "●" to MaterialTheme.colorScheme.primary
        SaveSyncStatus.SERVER_ONLY -> "↓" to Color(0xFF1976D2)
        SaveSyncStatus.LOCAL_NEWER -> "↑" to Color(0xFFF57C00)
        SaveSyncStatus.SERVER_NEWER -> "↓" to Color(0xFF1976D2)
        SaveSyncStatus.CONFLICT -> "⚠" to Color(0xFFFF5252)
        SaveSyncStatus.UNKNOWN -> "?" to MaterialTheme.colorScheme.onSurfaceVariant
    }
    Text(
        text = icon,
        style = MaterialTheme.typography.titleMedium,
        color = tint
    )
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

private fun statusChipColor(status: SaveSyncStatus): Color = when (status) {
    SaveSyncStatus.SYNCED -> Color(0xFF4CAF50)
    SaveSyncStatus.LOCAL_ONLY -> Color(0xFF1565C0)
    SaveSyncStatus.SERVER_ONLY -> Color(0xFF546E7A)
    SaveSyncStatus.LOCAL_NEWER -> Color(0xFFF57C00)
    SaveSyncStatus.SERVER_NEWER -> Color(0xFF1976D2)
    SaveSyncStatus.CONFLICT -> Color(0xFFFF5252)
    SaveSyncStatus.UNKNOWN -> Color(0xFF78909C)
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
        "PS1", "PSX"      -> Color(0xFF1A237E)
        "PS2"             -> Color(0xFF0D47A1)
        "PSP"             -> Color(0xFF01579B)
        "PPSSPP"          -> Color(0xFF0277BD)
        "GEN", "MD"       -> Color(0xFF37474F)
        "SMS"             -> Color(0xFF455A64)
        "GG"              -> Color(0xFF4CAF50)
        "SEGACD"          -> Color(0xFF263238)
        "SAT"             -> Color(0xFF4E342E)
        "DC"              -> Color(0xFFF57C00)
        "ARCADE", "FBA",
        "MAME"            -> Color(0xFFC62828)
        "NEOCD", "NGP"    -> Color(0xFFAD1457)
        "PCE", "TG16"     -> Color(0xFF00695C)
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
