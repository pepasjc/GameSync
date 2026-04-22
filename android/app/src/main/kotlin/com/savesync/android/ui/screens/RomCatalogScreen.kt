package com.savesync.android.ui.screens

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
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Download
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material.icons.filled.Search
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.AssistChip
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
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.savesync.android.api.RomEntry
import com.savesync.android.catalog.RomCatalogFilter
import com.savesync.android.ui.MainViewModel
import kotlinx.coroutines.launch

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
) {
    val catalog by viewModel.romCatalog.collectAsState()
    val loading by viewModel.romCatalogLoading.collectAsState()
    val loaded by viewModel.romCatalogLoaded.collectAsState()
    val error by viewModel.romCatalogError.collectAsState()
    val downloadState by viewModel.romDownloadState.collectAsState()

    val snackbarHostState = remember { SnackbarHostState() }
    val scope = rememberCoroutineScope()

    var query by remember { mutableStateOf("") }
    var systemFilter by remember { mutableStateOf<String?>(null) }
    var confirmTarget by remember { mutableStateOf<RomEntry?>(null) }

    // Lazy first-load when the tab is opened.
    LaunchedEffect(Unit) {
        if (!loaded && !loading) viewModel.fetchRomCatalog()
    }

    // Surface download outcomes as snackbars.
    LaunchedEffect(downloadState) {
        when (val s = downloadState) {
            is MainViewModel.RomDownloadState.Success -> {
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

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text("ROM Catalog") },
                actions = {
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
        ) {
            // Search + system filter row
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
                    modifier = Modifier.weight(1f)
                )
            }

            SystemFilterChips(
                systems = systems,
                selected = systemFilter,
                onSelect = { systemFilter = it }
            )

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
                            contentPadding = PaddingValues(horizontal = 16.dp, vertical = 8.dp),
                            verticalArrangement = Arrangement.spacedBy(8.dp)
                        ) {
                            items(filtered, key = { rom -> "${rom.system}:${rom.rom_id ?: rom.filename}" }) { rom ->
                                CatalogRomCard(
                                    rom = rom,
                                    onClick = { confirmTarget = rom }
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
                    viewModel.downloadRom(id, rom.system, rom.filename)
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
private fun SystemFilterChips(
    systems: List<String>,
    selected: String?,
    onSelect: (String?) -> Unit,
) {
    if (systems.isEmpty()) return
    var expanded by remember { mutableStateOf(false) }
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .padding(horizontal = 16.dp, vertical = 4.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(8.dp)
    ) {
        Text("System:", style = MaterialTheme.typography.labelLarge)
        Box {
            AssistChip(
                onClick = { expanded = true },
                label = { Text(selected ?: "All Systems") }
            )
            DropdownMenu(expanded = expanded, onDismissRequest = { expanded = false }) {
                DropdownMenuItem(
                    text = { Text("All Systems") },
                    onClick = {
                        onSelect(null)
                        expanded = false
                    }
                )
                systems.forEach { system ->
                    DropdownMenuItem(
                        text = { Text(system) },
                        onClick = {
                            onSelect(system)
                            expanded = false
                        }
                    )
                }
            }
        }
    }
}

@Composable
private fun CatalogRomCard(
    rom: RomEntry,
    onClick: () -> Unit,
) {
    Card(
        modifier = Modifier
            .fillMaxWidth()
            .clickable(onClick = onClick),
        elevation = CardDefaults.cardElevation(defaultElevation = 2.dp),
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
        Text("Downloading $name…", style = MaterialTheme.typography.bodyMedium)
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
