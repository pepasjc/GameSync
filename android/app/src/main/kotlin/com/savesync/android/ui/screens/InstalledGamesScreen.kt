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
import androidx.compose.material.icons.filled.CloudSync
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material.icons.filled.Search
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.CircularProgressIndicator
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
import androidx.compose.ui.platform.LocalContext
import com.savesync.android.MainActivity
import com.savesync.android.findComponentActivity
import com.savesync.android.installed.InstalledRom
import com.savesync.android.installed.InstalledRomsScanner
import com.savesync.android.ui.MainViewModel
import com.savesync.android.ui.SaveDetailState
import com.savesync.android.ui.components.GsButton
import com.savesync.android.ui.components.GsConfirmDialog
import com.savesync.android.ui.components.GsDialog
import com.savesync.android.ui.components.GsDialogChoice
import com.savesync.android.ui.components.GsFooterHints
import com.savesync.android.ui.components.GsHint
import com.savesync.android.ui.components.GsListRow
import com.savesync.android.ui.components.GsTopBar
import com.savesync.android.ui.components.SystemFilterChip
import com.savesync.android.ui.theme.GsColors
import com.savesync.android.ui.components.firstLetter
import com.savesync.android.ui.components.handleHorizontalHoldKeyEvent
import com.savesync.android.ui.components.rememberHoldNavState
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
    // Shared with SaveDetailScreen — emits Working/Success/Error during a
    // save sync triggered from the per-rom dialog below.
    val saveDetailState by viewModel.saveDetailState.collectAsState()

    val snackbarHostState = remember { SnackbarHostState() }
    val scope = rememberCoroutineScope()

    var query by remember { mutableStateOf("") }
    var systemFilter by remember { mutableStateOf<String?>(null) }
    var confirmTarget by remember { mutableStateOf<InstalledRom?>(null) }
    // Second step of Delete: "really delete?" (can't be undone).
    var deleteTarget by remember { mutableStateOf<InstalledRom?>(null) }
    var searchVisible by remember { mutableStateOf(false) }

    // ── Gamepad navigation state ────────────────────────────────────────
    var selectedIndex by remember { mutableIntStateOf(0) }
    val listState = rememberLazyListState()
    // Hold-to-accelerate state for d-pad left/right (page → alphabet jump).
    val holdNav = rememberHoldNavState()
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

    // Surface the result of a sync triggered from this tab as a snackbar
    // and clear the shared SaveDetailState afterwards so navigating to
    // SaveDetailScreen later doesn't re-show a stale message.  We don't
    // observe the Working state here — the dialog already closed; a
    // snackbar that says "Syncing…" then disappears would be noisy.
    LaunchedEffect(saveDetailState) {
        when (val s = saveDetailState) {
            is SaveDetailState.Success -> {
                scope.launch { snackbarHostState.showSnackbar(s.message) }
                viewModel.resetDetailState()
            }
            is SaveDetailState.Error -> {
                scope.launch { snackbarHostState.showSnackbar(s.message) }
                viewModel.resetDetailState()
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

    // SELECT cycles the system filter (the sub-tab chip) — the Activity
    // emits on systemCycleEvents and we apply it here so the chip stays in
    // sync.
    val activity = LocalContext.current.findComponentActivity() as? MainActivity
    LaunchedEffect(activity, systems, systemFilter) {
        activity?.systemCycleEvents?.collect { delta -> cycleSystem(delta) }
    }

    Scaffold(
        topBar = {
            GsTopBar(
                activeTabIndex = 2,
                onTabClick = onNavigateToTab,
                actions = {
                    if (loading) {
                        CircularProgressIndicator(
                            modifier = Modifier.size(18.dp),
                            strokeWidth = 2.dp,
                            color = GsColors.Accent,
                        )
                    }
                    IconButton(onClick = {
                        searchVisible = !searchVisible
                        if (!searchVisible) query = ""
                    }) {
                        Icon(Icons.Filled.Search, contentDescription = "Search (Y)", tint = GsColors.Text)
                    }
                    IconButton(onClick = { viewModel.scanInstalledRoms(force = true) }) {
                        Icon(Icons.Filled.Refresh, contentDescription = "Rescan (X)", tint = GsColors.Text)
                    }
                },
                subTabs = {
                    SystemFilterChip(
                        label = systemFilter ?: ALL_SYSTEMS_LABEL,
                        options = listOf(ALL_SYSTEMS_LABEL) + systems,
                        onSelect = { choice ->
                            systemFilter = choice.takeIf { it != ALL_SYSTEMS_LABEL }
                        },
                    )
                },
            )
        },
        bottomBar = {
            GsFooterHints(
                listOf(
                    GsHint(GsButton.A, "Manage"),
                    GsHint(GsButton.X, "Rescan"),
                    GsHint(GsButton.Y, "Search"),
                ) + (if (searchVisible || query.isNotEmpty()) listOf(GsHint(GsButton.B, "Clear search")) else emptyList()) +
                    listOf(
                        GsHint(GsButton.SELECT, "System"),
                        GsHint(GsButton.L1, "Tabs"),
                        GsHint(GsButton.START, "Exit"),
                    )
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
                    if (holdNav.handleHorizontalHoldKeyEvent(
                            event,
                            scope,
                            onPage = { d ->
                                if (filtered.isNotEmpty()) {
                                    val page = listState.layoutInfo.visibleItemsInfo.size
                                        .coerceAtLeast(1)
                                    selectedIndex = (selectedIndex + d * page)
                                        .coerceIn(0, filtered.size - 1)
                                    scope.launch { listState.animateScrollToItem(selectedIndex) }
                                }
                            },
                            onAlphabet = { d ->
                                if (filtered.isNotEmpty()) {
                                    val cur = selectedIndex.coerceIn(0, filtered.size - 1)
                                    val curLetter = firstLetter(filtered[cur].displayName)
                                    val step = if (d > 0) 1 else -1
                                    var row = cur + step
                                    var found = -1
                                    while (row in filtered.indices) {
                                        val ltr = firstLetter(filtered[row].displayName)
                                        if (ltr.isNotEmpty() && ltr != curLetter) {
                                            found = row; break
                                        }
                                        row += step
                                    }
                                    selectedIndex = if (found >= 0) found
                                        else if (d > 0) filtered.size - 1 else 0
                                    scope.launch { listState.animateScrollToItem(selectedIndex) }
                                }
                            },
                        )
                    ) return@onPreviewKeyEvent true
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
                        Key.ButtonA, Key.Enter -> {
                            filtered.getOrNull(selectedIndex)?.let { confirmTarget = it }
                            true
                        }
                        Key.ButtonY -> {
                            searchVisible = !searchVisible
                            if (!searchVisible) query = ""
                            true
                        }
                        // X → rescan the ROM folders
                        Key.ButtonX -> {
                            viewModel.scanInstalledRoms(force = true)
                            true
                        }
                        // B → clear + close the search; never starts
                        // anything (unclaimed it's swallowed by the Activity).
                        Key.ButtonB, Key.Escape, Key.Back -> {
                            when {
                                confirmTarget != null -> { confirmTarget = null; true }
                                searchVisible || query.isNotEmpty() -> {
                                    searchVisible = false
                                    query = ""
                                    runCatching { listFocusRequester.requestFocus() }
                                    true
                                }
                                else -> false
                            }
                        }
                        // L1/R1 (tabs), SELECT (system), START (exit) are
                        // intercepted at the Activity level — never reach here.
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
                            contentPadding = PaddingValues(horizontal = 12.dp, vertical = 8.dp),
                            verticalArrangement = Arrangement.spacedBy(4.dp)
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
        // Per-rom action sheet: A syncs this game's save, X deletes the ROM
        // (after a second confirmation), B cancels.
        GsDialog(
            title = rom.displayName,
            onDismiss = { confirmTarget = null },
            choices = listOf(
                GsDialogChoice(GsButton.B, "Cancel", { confirmTarget = null }),
                GsDialogChoice(
                    GsButton.X,
                    "Delete…",
                    {
                        confirmTarget = null
                        deleteTarget = rom
                    },
                    textColor = GsColors.Err,
                ),
                GsDialogChoice(
                    GsButton.A,
                    "Sync Saves",
                    {
                        viewModel.syncInstalledRomSaves(rom)
                        confirmTarget = null
                    },
                    color = GsColors.Accent,
                    textColor = GsColors.Ink,
                ),
            ),
        ) {
            Text("System: ${rom.system}", color = GsColors.Dim)
            Spacer(Modifier.height(6.dp))
            if (wholeFolder) {
                Text("Folder: ${rom.path.parentFile?.name ?: "?"}", color = GsColors.Dim)
                Text(
                    rom.path.parentFile?.absolutePath ?: rom.path.absolutePath,
                    style = MaterialTheme.typography.bodySmall,
                    color = GsColors.Muted,
                )
            } else {
                val extra = if (rom.companionFiles.isNotEmpty()) {
                    " + ${rom.companionFiles.size} companion file(s)"
                } else ""
                Text("File: ${rom.filename}$extra", color = GsColors.Dim)
                Text(
                    "Location: ${rom.path.parentFile?.absolutePath ?: "?"}",
                    style = MaterialTheme.typography.bodySmall,
                    color = GsColors.Muted,
                )
            }
            Spacer(Modifier.height(6.dp))
            Text("Size: ${formatBytes(rom.size)}", color = GsColors.Dim)
            Spacer(Modifier.height(12.dp))
            Text(
                "Sync Saves uploads / downloads this game's save against the server. " +
                    "Delete removes the ROM data permanently — this can't be undone.",
                style = MaterialTheme.typography.bodySmall,
                color = GsColors.Muted,
            )
        }
    }

    deleteTarget?.let { rom ->
        val wholeFolder = InstalledRomsScanner.wouldRemoveWholeFolder(rom)
        GsConfirmDialog(
            title = "Delete ${rom.displayName}?",
            message = if (wholeFolder) {
                "Removes the folder ${rom.path.parentFile?.name ?: "?"} and everything in it. This can't be undone."
            } else {
                "Removes ${rom.filename}" +
                    (if (rom.companionFiles.isNotEmpty()) " and ${rom.companionFiles.size} companion file(s)" else "") +
                    ". This can't be undone."
            },
            confirmLabel = "Delete",
            destructive = true,
            onConfirm = {
                viewModel.deleteInstalledRom(rom)
                deleteTarget = null
            },
            onDismiss = { deleteTarget = null },
        )
    }
}

@Composable
private fun InstalledRomCard(
    rom: InstalledRom,
    isSelected: Boolean,
    onClick: () -> Unit,
) {
    GsListRow(isSelected = isSelected, onClick = onClick) {
        SystemBadge(rom.system)
        Column(modifier = Modifier.weight(1f)) {
            Text(
                rom.displayName,
                fontWeight = FontWeight.SemiBold,
                color = GsColors.Text,
                maxLines = 1,
            )
            val companionNote = if (rom.companionFiles.isNotEmpty()) {
                "  ·  +${rom.companionFiles.size} file(s)"
            } else ""
            val sizeNote = if (rom.size > 0) "  ·  ${formatBytes(rom.size)}" else ""
            Text(
                "${rom.filename}$companionNote$sizeNote",
                style = MaterialTheme.typography.bodySmall,
                color = GsColors.Dim,
                maxLines = 1,
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
                color = GsColors.Muted,
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
