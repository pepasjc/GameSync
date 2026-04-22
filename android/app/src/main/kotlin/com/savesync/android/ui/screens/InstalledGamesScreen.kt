package com.savesync.android.ui.screens

import androidx.compose.foundation.BorderStroke
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
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material.icons.filled.Search
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
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
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.input.key.Key
import androidx.compose.ui.input.key.KeyEventType
import androidx.compose.ui.input.key.key
import androidx.compose.ui.input.key.onPreviewKeyEvent
import androidx.compose.ui.input.key.type
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.savesync.android.installed.InstalledRom
import com.savesync.android.installed.InstalledRomsScanner
import com.savesync.android.ui.MainViewModel
import com.savesync.android.ui.components.SystemFilterChip
import com.savesync.android.ui.components.TabSwitchBar
import kotlinx.coroutines.launch

/**
 * Label the [SystemFilterChip] shows when no specific system is
 * selected. The screen stores `null` internally; this sentinel only
 * exists at the UI boundary.
 */
private const val ALL_SYSTEMS_LABEL = "All Systems"

/**
 * Manage locally-installed ROMs: browse, search, and delete (with
 * whole-subfolder collapse when the game lives in a dedicated
 * per-title directory, matching the Steam Deck Installed Games tab).
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun InstalledGamesScreen(
    viewModel: MainViewModel,
    onNavigateToTab: (Int) -> Unit = {},
) {
    val roms by viewModel.installedRoms.collectAsState()
    val loading by viewModel.installedRomsLoading.collectAsState()
    val loaded by viewModel.installedRomsLoaded.collectAsState()
    val deleteState by viewModel.deleteInstalledState.collectAsState()

    val snackbarHostState = remember { SnackbarHostState() }
    val scope = rememberCoroutineScope()

    var query by remember { mutableStateOf("") }
    var systemFilter by remember { mutableStateOf<String?>(null) }
    var confirmTarget by remember { mutableStateOf<InstalledRom?>(null) }
    var searchVisible by remember { mutableStateOf(false) }

    // ── Gamepad navigation state ────────────────────────────────────────
    var selectedIndex by remember { mutableIntStateOf(0) }
    val listState = rememberLazyListState()
    val listFocusRequester = remember { FocusRequester() }
    val searchFocusRequester = remember { FocusRequester() }

    LaunchedEffect(Unit) {
        if (!loaded && !loading) viewModel.scanInstalledRoms()
    }

    // Claim focus for the rom list on entry so the search field doesn't
    // auto-grab focus. Search is only focused on explicit Y / tap.
    LaunchedEffect(Unit) {
        runCatching { listFocusRequester.requestFocus() }
    }

    LaunchedEffect(searchVisible) {
        if (searchVisible) runCatching { searchFocusRequester.requestFocus() }
    }

    LaunchedEffect(deleteState) {
        when (val s = deleteState) {
            is MainViewModel.DeleteInstalledState.Success -> {
                val msg = if (s.result.removedDir != null) {
                    "Removed folder ${s.result.removedDir.name} (${s.result.deletedCount} files)"
                } else {
                    "Deleted ${s.rom.displayName} (${s.result.deletedCount} files)"
                }
                scope.launch { snackbarHostState.showSnackbar(msg) }
                viewModel.resetDeleteInstalledState()
            }
            is MainViewModel.DeleteInstalledState.Error -> {
                scope.launch {
                    snackbarHostState.showSnackbar(
                        "Delete had errors: ${s.result.errors.joinToString()}"
                    )
                }
                viewModel.resetDeleteInstalledState()
            }
            else -> Unit
        }
    }

    val filtered = remember(roms, query, systemFilter) {
        filterInstalled(roms, query, systemFilter)
    }
    val systems = remember(roms) { roms.map { it.system }.distinct().sorted() }

    LaunchedEffect(filtered.size) {
        if (filtered.isNotEmpty()) {
            selectedIndex = selectedIndex.coerceIn(0, filtered.size - 1)
        } else {
            selectedIndex = 0
        }
    }

    fun cycleSystem(delta: Int) {
        if (systems.isEmpty()) return
        val all = listOf<String?>(null) + systems
        val idx = all.indexOf(systemFilter).let { if (it < 0) 0 else it }
        val next = (idx + delta + all.size) % all.size
        systemFilter = all[next]
    }

    Scaffold(
        topBar = {
            TopAppBar(
                title = {
                    Row(
                        verticalAlignment = Alignment.CenterVertically,
                        horizontalArrangement = Arrangement.spacedBy(10.dp),
                    ) {
                        TabSwitchBar(
                            activeTabIndex = 2,
                            onTabClick = onNavigateToTab,
                        )
                        SystemFilterChip(
                            label = systemFilter ?: ALL_SYSTEMS_LABEL,
                            options = listOf(ALL_SYSTEMS_LABEL) + systems,
                            onSelect = { choice ->
                                systemFilter = choice.takeIf { it != ALL_SYSTEMS_LABEL }
                            },
                        )
                    }
                },
                actions = {
                    IconButton(onClick = {
                        searchVisible = !searchVisible
                        if (!searchVisible) query = ""
                    }) {
                        Icon(Icons.Filled.Search, contentDescription = "Search (Y)")
                    }
                    IconButton(onClick = { viewModel.scanInstalledRoms(force = true) }) {
                        Icon(Icons.Filled.Refresh, contentDescription = "Rescan")
                    }
                }
            )
        },
        snackbarHost = { SnackbarHost(snackbarHostState) }
    ) { padding ->
        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(padding)
                .focusRequester(listFocusRequester)
                .focusable()
                .onPreviewKeyEvent { event ->
                    if (event.type != KeyEventType.KeyDown) return@onPreviewKeyEvent false
                    when (event.key) {
                        Key.DirectionDown -> {
                            if (filtered.isNotEmpty()) {
                                selectedIndex = (selectedIndex + 1).coerceAtMost(filtered.size - 1)
                                scope.launch { listState.animateScrollToItem(selectedIndex) }
                            }
                            true
                        }
                        Key.DirectionUp -> {
                            if (filtered.isNotEmpty()) {
                                selectedIndex = (selectedIndex - 1).coerceAtLeast(0)
                                scope.launch { listState.animateScrollToItem(selectedIndex) }
                            }
                            true
                        }
                        // D-pad / stick left/right → cycle system filter
                        Key.DirectionLeft -> { cycleSystem(-1); true }
                        Key.DirectionRight -> { cycleSystem(1); true }
                        // L1 / R1 → page-scroll the list (Steam Deck parity).
                        Key.ButtonL1 -> {
                            if (filtered.isNotEmpty()) {
                                val page = listState.layoutInfo.visibleItemsInfo.size
                                    .coerceAtLeast(1)
                                selectedIndex = (selectedIndex - page).coerceAtLeast(0)
                                scope.launch { listState.animateScrollToItem(selectedIndex) }
                            }
                            true
                        }
                        Key.ButtonR1 -> {
                            if (filtered.isNotEmpty()) {
                                val page = listState.layoutInfo.visibleItemsInfo.size
                                    .coerceAtLeast(1)
                                selectedIndex = (selectedIndex + page)
                                    .coerceAtMost(filtered.size - 1)
                                scope.launch { listState.animateScrollToItem(selectedIndex) }
                            }
                            true
                        }
                        Key.ButtonA, Key.Enter -> {
                            filtered.getOrNull(selectedIndex)?.let { confirmTarget = it }
                            true
                        }
                        Key.ButtonY -> {
                            searchVisible = !searchVisible
                            if (!searchVisible) query = ""
                            true
                        }
                        Key.ButtonB, Key.Escape, Key.Back -> {
                            when {
                                confirmTarget != null -> { confirmTarget = null; true }
                                searchVisible -> {
                                    searchVisible = false
                                    query = ""
                                    runCatching { listFocusRequester.requestFocus() }
                                    true
                                }
                                else -> false
                            }
                        }
                        Key.ButtonStart -> {
                            viewModel.scanInstalledRoms(force = true)
                            true
                        }
                        // L2 / R2 are Activity-level tab switches — let them bubble up.
                        else -> false
                    }
                }
        ) {
            if (searchVisible) {
                Row(
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(horizontal = 16.dp, vertical = 8.dp),
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(8.dp)
                ) {
                    OutlinedTextField(
                        value = query,
                        onValueChange = { query = it },
                        singleLine = true,
                        leadingIcon = { Icon(Icons.Filled.Search, contentDescription = null) },
                        placeholder = { Text("Search installed ROMs…") },
                        modifier = Modifier
                            .weight(1f)
                            .focusRequester(searchFocusRequester)
                    )
                }
            }

            Spacer(Modifier.height(4.dp))

            Box(modifier = Modifier.weight(1f).fillMaxWidth()) {
                when {
                    loading && roms.isEmpty() -> {
                        CenterLoader(text = "Scanning installed ROMs…")
                    }
                    filtered.isEmpty() && roms.isEmpty() -> {
                        CenterMessage(
                            title = "No installed ROMs found.",
                            detail = "Download ROMs from the Catalog tab or point the " +
                                "ROM scan dir at your library in Settings.",
                        )
                    }
                    filtered.isEmpty() -> {
                        CenterMessage(title = "No ROMs match this search.")
                    }
                    else -> {
                        LazyColumn(
                            state = listState,
                            contentPadding = PaddingValues(horizontal = 16.dp, vertical = 8.dp),
                            verticalArrangement = Arrangement.spacedBy(8.dp)
                        ) {
                            itemsIndexed(
                                filtered,
                                key = { _, rom -> rom.path.absolutePath }
                            ) { index, rom ->
                                InstalledRomCard(
                                    rom = rom,
                                    isSelected = index == selectedIndex,
                                    onClick = {
                                        selectedIndex = index
                                        confirmTarget = rom
                                    }
                                )
                            }
                        }
                    }
                }
            }

            InstalledFooter(total = roms.size, shown = filtered.size, totalBytes = roms.sumOf { it.size })
        }
    }

    confirmTarget?.let { rom ->
        val wholeFolder = InstalledRomsScanner.wouldRemoveWholeFolder(rom)
        AlertDialog(
            onDismissRequest = { confirmTarget = null },
            title = { Text("Delete ROM?") },
            text = {
                Column {
                    Text(rom.displayName, fontWeight = FontWeight.Bold)
                    Spacer(Modifier.height(6.dp))
                    Text("System: ${rom.system}")
                    if (wholeFolder) {
                        Text(
                            "Removes the whole folder (and every file inside it):",
                        )
                        Text(
                            rom.path.parentFile?.absolutePath ?: rom.path.absolutePath,
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    } else {
                        val extra = if (rom.companionFiles.isNotEmpty()) {
                            " + ${rom.companionFiles.size} companion file(s)"
                        } else ""
                        Text("File: ${rom.filename}$extra")
                        Text(
                            "Location: ${rom.path.parentFile?.absolutePath ?: "?"}",
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }
                    Spacer(Modifier.height(6.dp))
                    Text("Frees: ${formatBytes(rom.size)}")
                    Spacer(Modifier.height(8.dp))
                    Text(
                        "This removes the data permanently and cannot be undone.",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.error,
                    )
                }
            },
            confirmButton = {
                TextButton(onClick = {
                    viewModel.deleteInstalledRom(rom)
                    confirmTarget = null
                }) { Text("Delete") }
            },
            dismissButton = {
                TextButton(onClick = { confirmTarget = null }) { Text("Cancel") }
            }
        )
    }
}

@Composable
private fun InstalledRomCard(
    rom: InstalledRom,
    isSelected: Boolean,
    onClick: () -> Unit,
) {
    val border = if (isSelected) {
        BorderStroke(2.dp, MaterialTheme.colorScheme.primary)
    } else null
    Card(
        modifier = Modifier
            .fillMaxWidth()
            .clickable(onClick = onClick),
        elevation = CardDefaults.cardElevation(
            defaultElevation = if (isSelected) 6.dp else 2.dp
        ),
        border = border,
    ) {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(12.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            SystemBadge(rom.system)
            Column(modifier = Modifier.weight(1f)) {
                Text(
                    rom.displayName,
                    fontWeight = FontWeight.SemiBold,
                    maxLines = 1,
                )
                val companionNote = if (rom.companionFiles.isNotEmpty()) {
                    "  ·  +${rom.companionFiles.size} file(s)"
                } else ""
                val sizeNote = if (rom.size > 0) "  ·  ${formatBytes(rom.size)}" else ""
                Text(
                    "${rom.filename}$companionNote$sizeNote",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    maxLines = 1,
                )
            }
            Icon(
                Icons.Filled.Delete,
                contentDescription = "Delete",
                modifier = Modifier.size(22.dp),
                tint = MaterialTheme.colorScheme.error,
            )
        }
    }
}

@Composable
private fun InstalledFooter(total: Int, shown: Int, totalBytes: Long) {
    val sizeTxt = formatBytes(totalBytes)
    val summary = when {
        total == 0 -> ""
        shown == total && sizeTxt.isNotBlank() -> "$total ROMs  ·  $sizeTxt"
        shown == total -> "$total ROMs"
        else -> "$shown / $total ROMs"
    }
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .padding(horizontal = 16.dp, vertical = 6.dp),
        horizontalArrangement = Arrangement.End,
    ) {
        if (summary.isNotBlank()) {
            Text(
                summary,
                style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
    }
}

private fun filterInstalled(
    roms: List<InstalledRom>,
    query: String,
    system: String?,
): List<InstalledRom> {
    val q = query.trim().lowercase()
    return roms.filter { rom ->
        if (!system.isNullOrBlank() && !rom.system.equals(system, ignoreCase = true)) {
            return@filter false
        }
        if (q.isEmpty()) return@filter true
        val haystack = "${rom.displayName} ${rom.filename} ${rom.system}".lowercase()
        q in haystack
    }
}
