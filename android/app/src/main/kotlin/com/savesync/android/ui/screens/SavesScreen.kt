package com.savesync.android.ui.screens

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
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.CloudDownload
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material.icons.filled.Sync
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.ExtendedFloatingActionButton
import androidx.compose.material3.FilterChip
import androidx.compose.material3.FilterChipDefaults
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.SuggestionChip
import androidx.compose.material3.SuggestionChipDefaults
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import com.google.accompanist.swiperefresh.SwipeRefresh
import com.google.accompanist.swiperefresh.rememberSwipeRefreshState
import com.savesync.android.emulators.SaveEntry
import com.savesync.android.storage.SyncStateEntity
import com.savesync.android.ui.MainViewModel
import com.savesync.android.ui.SyncState
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
    val snackbarHostState = remember { SnackbarHostState() }

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

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text("Save Sync") },
                colors = TopAppBarDefaults.topAppBarColors(
                    containerColor = MaterialTheme.colorScheme.primary,
                    titleContentColor = MaterialTheme.colorScheme.onPrimary,
                    actionIconContentColor = MaterialTheme.colorScheme.onPrimary
                ),
                actions = {
                    IconButton(onClick = onNavigateToSettings) {
                        Icon(Icons.Default.Settings, contentDescription = "Settings")
                    }
                }
            )
        },
        floatingActionButton = {
            ExtendedFloatingActionButton(
                text = { Text(if (isSyncing) "Syncing…" else "Sync All") },
                icon = {
                    if (isSyncing) {
                        CircularProgressIndicator(
                            modifier = Modifier.size(20.dp),
                            strokeWidth = 2.dp,
                            color = MaterialTheme.colorScheme.onPrimaryContainer
                        )
                    } else {
                        Icon(Icons.Default.Sync, contentDescription = null)
                    }
                },
                onClick = { if (!isSyncing) viewModel.syncNow() },
                containerColor = MaterialTheme.colorScheme.primaryContainer
            )
        },
        snackbarHost = { SnackbarHost(snackbarHostState) }
    ) { paddingValues ->
        SwipeRefresh(
            state = rememberSwipeRefreshState(isRefreshing = isSyncing),
            onRefresh = { viewModel.scanSaves() },
            modifier = Modifier
                .fillMaxSize()
                .padding(paddingValues)
        ) {
            Column(modifier = Modifier.fillMaxSize()) {
                // Filter bar — only show when there are multiple systems
                if (availableFilters.size > 2) {
                    FilterBar(
                        filters = availableFilters,
                        selected = selectedFilter,
                        onSelect = { viewModel.setFilter(it) }
                    )
                }

                // Save count header
                if (saves.isNotEmpty()) {
                    Text(
                        text = "${saves.size} save${if (saves.size == 1) "" else "s"}",
                        style = MaterialTheme.typography.labelMedium,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                        modifier = Modifier.padding(horizontal = 12.dp, vertical = 4.dp)
                    )
                }

                if (saves.isEmpty()) {
                    EmptyState(activeFilter = selectedFilter)
                } else {
                    SavesList(
                        saves = saves,
                        syncStateEntities = syncStateEntities,
                        onSaveClick = onNavigateToDetail
                    )
                }
            }
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun FilterBar(
    filters: List<String>,
    selected: String,
    onSelect: (String) -> Unit
) {
    LazyRow(
        modifier = Modifier
            .fillMaxWidth()
            .padding(horizontal = 12.dp, vertical = 6.dp),
        horizontalArrangement = Arrangement.spacedBy(8.dp),
        verticalAlignment = Alignment.CenterVertically
    ) {
        items(filters) { filter ->
            val isSelected = filter == selected
            FilterChip(
                selected = isSelected,
                onClick = { onSelect(filter) },
                label = {
                    Text(
                        text = filter,
                        style = MaterialTheme.typography.labelMedium,
                        fontWeight = if (isSelected) FontWeight.Bold else FontWeight.Normal,
                        color = if (isSelected) Color.White
                                else MaterialTheme.colorScheme.onSurface
                    )
                },
                colors = FilterChipDefaults.filterChipColors(
                    selectedContainerColor = MaterialTheme.colorScheme.primary,
                    selectedLabelColor = Color.White
                ),
                border = if (isSelected) null
                         else FilterChipDefaults.filterChipBorder(
                             enabled = true,
                             selected = false
                         )
            )
        }
    }
}

@Composable
private fun EmptyState(activeFilter: String) {
    Box(
        modifier = Modifier.fillMaxSize(),
        contentAlignment = Alignment.Center
    ) {
        Column(horizontalAlignment = Alignment.CenterHorizontally) {
            if (activeFilter != "All") {
                Text(
                    text = "No $activeFilter saves found",
                    style = MaterialTheme.typography.titleMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant
                )
                Spacer(Modifier.height(8.dp))
                Text(
                    text = "Try selecting a different filter.",
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
    onSaveClick: (String) -> Unit
) {
    val syncMap = syncStateEntities.associateBy { it.titleId }

    LazyColumn(
        modifier = Modifier.fillMaxSize(),
        verticalArrangement = Arrangement.spacedBy(8.dp),
        contentPadding = PaddingValues(12.dp)
    ) {
        items(saves, key = { it.titleId }) { entry ->
            SaveCard(
                entry = entry,
                syncState = syncMap[entry.titleId],
                onClick = { onSaveClick(entry.titleId) }
            )
        }
    }
}

@Composable
private fun SaveCard(
    entry: SaveEntry,
    syncState: SyncStateEntity?,
    onClick: () -> Unit
) {
    val cardColors = if (entry.isServerOnly) {
        CardDefaults.cardColors(
            containerColor = MaterialTheme.colorScheme.surfaceVariant
        )
    } else {
        CardDefaults.cardColors()
    }

    Card(
        modifier = Modifier.fillMaxWidth(),
        onClick = onClick,
        elevation = CardDefaults.cardElevation(defaultElevation = 2.dp),
        colors = cardColors
    ) {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(12.dp),
            verticalAlignment = Alignment.CenterVertically
        ) {
            Column(modifier = Modifier.weight(1f)) {
                Text(
                    text = entry.displayName,
                    style = MaterialTheme.typography.titleSmall,
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
                Spacer(Modifier.height(4.dp))
                Row(
                    horizontalArrangement = Arrangement.spacedBy(6.dp),
                    verticalAlignment = Alignment.CenterVertically
                ) {
                    SystemChip(entry.systemName)
                    if (!entry.isServerOnly) {
                        syncState?.lastSyncedAt?.let { ts ->
                            Text(
                                text = formatTimestamp(ts),
                                style = MaterialTheme.typography.labelSmall,
                                color = MaterialTheme.colorScheme.onSurfaceVariant
                            )
                        }
                    } else {
                        Text(
                            text = "Server only",
                            style = MaterialTheme.typography.labelSmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant
                        )
                    }
                }
            }
            Spacer(Modifier.width(8.dp))
            if (entry.isServerOnly) {
                Icon(
                    imageVector = Icons.Default.CloudDownload,
                    contentDescription = "Server only — tap to download",
                    tint = Color(0xFF1976D2),
                    modifier = Modifier.size(24.dp)
                )
            } else {
                SyncStatusIcon(syncState = syncState)
            }
        }
    }
}

@Composable
fun SystemChip(systemName: String) {
    val bgColor = systemChipColor(systemName)
    SuggestionChip(
        onClick = {},
        label = {
            Text(
                text = systemName,
                style = MaterialTheme.typography.labelSmall,
                fontWeight = FontWeight.Bold,
                color = Color.White
            )
        },
        colors = SuggestionChipDefaults.suggestionChipColors(
            containerColor = bgColor,
            labelColor = Color.White
        ),
        border = null
    )
}

@Composable
private fun SyncStatusIcon(syncState: SyncStateEntity?) {
    val (icon, tint) = when {
        syncState?.lastSyncedHash != null ->
            "✓" to Color(0xFF4CAF50)   // synced
        else ->
            "↑" to MaterialTheme.colorScheme.primary  // local, never synced → needs first upload
    }
    Text(
        text = icon,
        style = MaterialTheme.typography.titleLarge,
        color = tint
    )
}

fun systemChipColor(systemName: String): Color {
    return when (systemName.uppercase()) {
        // Nintendo handhelds
        "GBA"             -> Color(0xFF6A1B9A)  // purple
        "GBC"             -> Color(0xFF8E24AA)  // lighter purple
        "GB"              -> Color(0xFF546E7A)  // slate
        "NDS", "DS"       -> Color(0xFF1565C0)  // blue
        "3DS"             -> Color(0xFF0277BD)  // light blue
        // Nintendo home
        "NES", "FC"       -> Color(0xFFB71C1C)  // dark red
        "SNES", "SFC"     -> Color(0xFFE65100)  // deep orange
        "N64"             -> Color(0xFF558B2F)  // green
        "GC"              -> Color(0xFF7B1FA2)  // purple
        "WII"             -> Color(0xFF00838F)  // teal
        // Sony
        "PS1", "PSX"      -> Color(0xFF1A237E)  // dark navy
        "PS2"             -> Color(0xFF0D47A1)  // navy
        "PSP"             -> Color(0xFF01579B)  // light navy
        "PPSSPP"          -> Color(0xFF0277BD)  // sky blue
        // Sega
        "GEN", "MD"       -> Color(0xFF37474F)  // blue-grey
        "SMS"             -> Color(0xFF455A64)  // lighter blue-grey
        "GG"              -> Color(0xFF4CAF50)  // green (Game Gear)
        "SCD"             -> Color(0xFF263238)  // almost black
        "SAT"             -> Color(0xFF4E342E)  // brown
        "DC"              -> Color(0xFFF57C00)  // orange (Dreamcast)
        // Arcade
        "ARCADE", "FBA",
        "MAME"            -> Color(0xFFC62828)  // arcade red
        "NEOCD", "NGP"    -> Color(0xFFAD1457)  // pink
        // Other
        "PCE", "TG16"     -> Color(0xFF00695C)  // dark teal (PC Engine)
        "WS"              -> Color(0xFF2E7D32)  // dark green
        "LYNX"            -> Color(0xFF4527A0)  // deep purple
        "A2600", "A7800"  -> Color(0xFF6D4C41)  // brown (Atari)
        "RETRO"           -> Color(0xFF37474F)  // blue-grey fallback
        else              -> Color(0xFF546E7A)
    }
}

private fun formatTimestamp(millis: Long): String {
    val sdf = SimpleDateFormat("MMM d, HH:mm", Locale.getDefault())
    return sdf.format(Date(millis))
}
