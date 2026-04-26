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
import androidx.compose.material.icons.filled.Download
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material.icons.filled.Search
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.CircularProgressIndicator
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
import androidx.compose.runtime.saveable.listSaver
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.snapshotFlow
import kotlinx.coroutines.flow.drop
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
import com.savesync.android.api.RomEntry
import com.savesync.android.api.preferredDownloadExtractFormat
import com.savesync.android.api.preferredDownloadFilename
import com.savesync.android.catalog.RomCatalogFilter
import com.savesync.android.ui.MainViewModel
import com.savesync.android.ui.components.SystemFilterChip
import com.savesync.android.ui.components.TabSwitchBar
import kotlinx.coroutines.launch

/**
 * Label the [SystemFilterChip] shows when no specific system is
 * selected. The screen stores `null` internally (matching the API's
 * "show everything" semantics); this sentinel is only used at the
 * UI boundary.
 */
private const val ALL_SYSTEMS_LABEL = "All Systems"

/**
 * Browse the server's entire ROM catalog with a smart tokenised search
 * (roman numerals, region-tag stripping) and a per-system filter.
 *
 * Mirrors the Steam Deck ROM Catalog tab UX: tap a row → confirm →
 * stream the ROM into the right ``<romScanDir>/<System>/`` folder via
 * the existing [MainViewModel.downloadRom] pipeline.
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun RomCatalogScreen(
    viewModel: MainViewModel,
    onNavigateToTab: (Int) -> Unit = {},
) {
    val catalog by viewModel.romCatalog.collectAsState()
    val loading by viewModel.romCatalogLoading.collectAsState()
    val loaded by viewModel.romCatalogLoaded.collectAsState()
    val error by viewModel.romCatalogError.collectAsState()
    val downloadState by viewModel.romDownloadState.collectAsState()

    val snackbarHostState = remember { SnackbarHostState() }
    val scope = rememberCoroutineScope()

    // Filter state uses rememberSaveable so the user's place in the catalog
    // (selected system + search query + whether the search bar is open)
    // survives tab switching.  NavHost preserves the destination's
    // SavedState bundle across tab pops with saveState=true /
    // restoreState=true, and rememberSaveable rides on top of that — so
    // queueing a download then bouncing between Catalog ↔ Downloads
    // doesn't reset the filter back to the default system.
    var query by rememberSaveable { mutableStateOf("") }
    var systemFilter by rememberSaveable { mutableStateOf<String?>(null) }
    var searchVisible by rememberSaveable { mutableStateOf(false) }
    // confirmTarget intentionally stays as plain remember — re-showing the
    // download confirmation dialog after the user navigated away is creepy
    // (they'd come back to a modal they didn't open).
    var confirmTarget by remember { mutableStateOf<RomEntry?>(null) }

    // ── Gamepad navigation state ────────────────────────────────────────
    // We drive selection ourselves so D-pad / analog stick scrolls the
    // rom grid without focus leaking up to the search field.
    var selectedIndex by remember { mutableIntStateOf(0) }
    val listState = rememberLazyListState()
    val listFocusRequester = remember { FocusRequester() }
    val searchFocusRequester = remember { FocusRequester() }

    // ── Per-system scroll memory ────────────────────────────────────────
    // The same LazyListState is reused as the user cycles through systems
    // (because the LazyColumn always renders the actively-filtered list),
    // so without this map a system-filter change would either keep the
    // user at the previous system's scroll offset (now pointing at a
    // totally unrelated game) or — depending on filtered.size — clamp them
    // back to the top.  Map: system code (or "ALL" for no filter) →
    // firstVisibleItemIndex when the user last left that system.  We
    // store just the index (no offset) — close enough that the user
    // recognises their place; saving offset too would mostly add noise.
    //
    // listSaver flattens the map to a [k, v, k, v, ...] list which the
    // SavedState bundle handles natively, so this state survives both
    // tab switching (via NavHost saveState=true) and configuration
    // changes (rotation, theme switch).
    val scrollPositions: MutableMap<String, Int> = rememberSaveable(
        saver = listSaver(
            save = { map -> map.flatMap { listOf(it.key, it.value) } },
            restore = { entries ->
                mutableMapOf<String, Int>().apply {
                    var i = 0
                    while (i + 1 < entries.size) {
                        val k = entries[i] as? String ?: ""
                        val v = entries[i + 1] as? Int ?: 0
                        if (k.isNotEmpty()) put(k, v)
                        i += 2
                    }
                }
            }
        )
    ) { mutableMapOf() }
    // Stable map key for the active filter — null filter == "ALL".
    val systemKey = systemFilter ?: "ALL"

    // Lazy first-load when the tab is opened.
    LaunchedEffect(Unit) {
        if (!loaded && !loading) viewModel.fetchRomCatalog()
    }

    // Claim focus for the rom list on entry so the search OutlinedTextField
    // doesn't auto-focus and pop the keyboard. Search is only focused when
    // the user explicitly presses Y / taps the icon.
    LaunchedEffect(Unit) {
        runCatching { listFocusRequester.requestFocus() }
    }

    // Focus the search field whenever it becomes visible.
    LaunchedEffect(searchVisible) {
        if (searchVisible) runCatching { searchFocusRequester.requestFocus() }
    }

    // Surface enqueue outcomes as snackbars.  The Downloads tab now owns
    // progress + final-status display; these snackbars only confirm that
    // the request was accepted (or rejected before it left the device).
    LaunchedEffect(downloadState) {
        when (val s = downloadState) {
            is MainViewModel.RomDownloadState.Downloading -> {
                scope.launch {
                    snackbarHostState.showSnackbar(
                        "Queued ${s.name} — see Downloads tab for progress"
                    )
                    viewModel.resetRomDownloadState()
                }
            }
            is MainViewModel.RomDownloadState.Success -> {
                // Legacy state — preserved for any caller that still uses
                // the synchronous path (e.g. unit tests).  The DownloadManager
                // never emits this directly.
                scope.launch {
                    snackbarHostState.showSnackbar("Downloaded ${s.file.name}")
                    viewModel.resetRomDownloadState()
                }
            }
            is MainViewModel.RomDownloadState.Error -> {
                scope.launch {
                    snackbarHostState.showSnackbar("Download failed: ${s.message}")
                    viewModel.resetRomDownloadState()
                }
            }
            else -> Unit
        }
    }

    val systems = remember(catalog) { RomCatalogFilter.uniqueSystems(catalog) }
    val filtered = remember(catalog, query, systemFilter) {
        RomCatalogFilter.filter(catalog, query, systemFilter)
    }

    // Keep the cursor in range when filters/search change the list size.
    LaunchedEffect(filtered.size) {
        if (filtered.isNotEmpty()) {
            selectedIndex = selectedIndex.coerceIn(0, filtered.size - 1)
        } else {
            selectedIndex = 0
        }
    }

    // Continuously snapshot the active system's scroll position into the
    // per-system map, so flipping back to a previously-visited system
    // restores where you were instead of dumping you at the top.
    //
    // ``drop(1)`` is critical: snapshotFlow always emits the current value
    // immediately on subscription, but when ``systemKey`` changes the
    // restore effect below fires concurrently and we don't want this saver
    // to overwrite the new system's saved position with the previous
    // (stale) listState value before the restore lands.  Skipping the
    // initial emission means we only persist values that came from real
    // scroll events, not from coroutine restart.
    LaunchedEffect(listState, systemKey) {
        snapshotFlow { listState.firstVisibleItemIndex }
            .drop(1)
            .collect { idx -> scrollPositions[systemKey] = idx }
    }

    // Restore saved scroll position when the user switches systems.  We
    // gate on ``lastRestoredKey`` so subsequent filter updates within
    // the same system (e.g. typing in the search box) don't re-scroll
    // the list — only an actual system change triggers a restore.
    var lastRestoredKey by remember { mutableStateOf<String?>(null) }
    LaunchedEffect(systemKey, filtered.size) {
        if (lastRestoredKey == systemKey) return@LaunchedEffect
        if (filtered.isEmpty()) return@LaunchedEffect  // wait for items
        val saved = scrollPositions[systemKey] ?: 0
        val target = saved.coerceIn(0, filtered.size - 1)
        listState.scrollToItem(target)
        // Place the gamepad cursor on the same item so D-pad navigation
        // resumes from the visible row instead of jumping back to item 0.
        selectedIndex = target
        lastRestoredKey = systemKey
    }

    // Helper that cycles the system filter (null = all systems), used by
    // both L1/R1 and D-pad left/right.
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
                            activeTabIndex = 1,
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
                    IconButton(onClick = { viewModel.fetchRomCatalog(force = true) }) {
                        Icon(Icons.Filled.Refresh, contentDescription = "Refresh catalog")
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
                        // A / Enter → open the download-confirm dialog
                        Key.ButtonA, Key.Enter -> {
                            filtered.getOrNull(selectedIndex)?.let { confirmTarget = it }
                            true
                        }
                        // Y → toggle search
                        Key.ButtonY -> {
                            searchVisible = !searchVisible
                            if (!searchVisible) query = ""
                            true
                        }
                        // B / Escape / Back → dismiss dialog or close search
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
                        // Start → refresh catalog
                        Key.ButtonStart -> {
                            viewModel.fetchRomCatalog(force = true)
                            true
                        }
                        // L2 / R2 are Activity-level tab switches — let them bubble up.
                        else -> false
                    }
                }
        ) {
            // Search bar is only rendered when explicitly opened so there's
            // nothing for Android's default focus system to grab on entry.
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
                        placeholder = { Text("Search name, filename, title id…") },
                        modifier = Modifier
                            .weight(1f)
                            .focusRequester(searchFocusRequester)
                    )
                }
            }

            Spacer(Modifier.height(4.dp))

            // List / loading / empty
            Box(modifier = Modifier.weight(1f).fillMaxWidth()) {
                when {
                    loading && catalog.isEmpty() -> {
                        CenterLoader(text = "Loading catalog…")
                    }
                    error != null && catalog.isEmpty() -> {
                        CenterMessage(
                            title = "Couldn't load catalog",
                            detail = error ?: "",
                        )
                    }
                    filtered.isEmpty() && catalog.isEmpty() -> {
                        CenterMessage(
                            title = "The server's ROM catalog is empty.",
                            detail = "Upload ROMs via the server and tap refresh.",
                        )
                    }
                    filtered.isEmpty() -> {
                        CenterMessage(
                            title = "No ROMs match this search.",
                            detail = "Try a different term or clear the system filter.",
                        )
                    }
                    else -> {
                        LazyColumn(
                            state = listState,
                            contentPadding = PaddingValues(horizontal = 16.dp, vertical = 8.dp),
                            verticalArrangement = Arrangement.spacedBy(8.dp)
                        ) {
                            itemsIndexed(
                                filtered,
                                key = { _, rom -> "${rom.system}:${rom.rom_id ?: rom.filename}" }
                            ) { index, rom ->
                                CatalogRomCard(
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

                // Active download overlay (the existing download
                // pipeline is fire-and-forget on the saves tab; show a
                // small indicator so the user knows something is
                // happening without blocking the list).
                if (downloadState is MainViewModel.RomDownloadState.Downloading) {
                    val name = (downloadState as MainViewModel.RomDownloadState.Downloading).name
                    DownloadBanner(name)
                }
            }

            CatalogFooter(total = catalog.size, shown = filtered.size)
        }
    }

    confirmTarget?.let { rom ->
        AlertDialog(
            onDismissRequest = { confirmTarget = null },
            title = { Text("Download ROM?") },
            text = {
                Column {
                    Text(rom.name.ifEmpty { rom.filename }, fontWeight = FontWeight.Bold)
                    Spacer(Modifier.height(4.dp))
                    Text("System: ${rom.system}")
                    Text("File: ${rom.filename}")
                    if (rom.size > 0) Text("Size: ${formatBytes(rom.size)}")
                    Spacer(Modifier.height(8.dp))
                    Text(
                        "Saves into the ROM folder configured in Settings " +
                            "(or the system-specific override, if set).",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            },
            confirmButton = {
                TextButton(onClick = {
                    val id = rom.rom_id ?: rom.title_id
                    val extract = rom.preferredDownloadExtractFormat()
                    viewModel.downloadRom(
                        romId = id,
                        system = rom.system,
                        filename = rom.preferredDownloadFilename(extract),
                        extractFormat = extract,
                    )
                    confirmTarget = null
                }) { Text("Download") }
            },
            dismissButton = {
                TextButton(onClick = { confirmTarget = null }) { Text("Cancel") }
            }
        )
    }
}

@Composable
private fun CatalogRomCard(
    rom: RomEntry,
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
                    rom.name.ifEmpty { rom.filename },
                    fontWeight = FontWeight.SemiBold,
                    maxLines = 1,
                )
                val subtitle = buildString {
                    append(rom.filename)
                    if (rom.size > 0) append("  ·  ${formatBytes(rom.size)}")
                    append("  ·  ${rom.title_id}")
                }
                Text(
                    subtitle,
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    maxLines = 1,
                )
            }
            Icon(
                Icons.Filled.Download,
                contentDescription = "Download",
                modifier = Modifier.size(22.dp),
                tint = MaterialTheme.colorScheme.primary,
            )
        }
    }
}

@Composable
private fun DownloadBanner(name: String) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .padding(16.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        CircularProgressIndicator(modifier = Modifier.size(18.dp))
        // Now an enqueue confirmation — the actual transfer is running on
        // the Downloads tab via DownloadManager.
        Text("Queued $name — open Downloads tab", style = MaterialTheme.typography.bodyMedium)
    }
}

@Composable
private fun CatalogFooter(total: Int, shown: Int) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .padding(horizontal = 16.dp, vertical = 6.dp),
        horizontalArrangement = Arrangement.End,
    ) {
        Text(
            text = if (shown == total) "$total ROMs" else "$shown / $total ROMs",
            style = MaterialTheme.typography.labelSmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
    }
}
