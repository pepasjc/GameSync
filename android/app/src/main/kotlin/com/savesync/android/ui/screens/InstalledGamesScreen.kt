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
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material.icons.filled.Search
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.AssistChip
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
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
import com.savesync.android.installed.InstalledRom
import com.savesync.android.installed.InstalledRomsScanner
import com.savesync.android.ui.MainViewModel
import kotlinx.coroutines.launch

/**
 * Manage locally-installed ROMs: browse, search, and delete (with
 * whole-subfolder collapse when the game lives in a dedicated
 * per-title directory, matching the Steam Deck Installed Games tab).
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun InstalledGamesScreen(
    viewModel: MainViewModel,
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

    LaunchedEffect(Unit) {
        if (!loaded && !loading) viewModel.scanInstalledRoms()
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

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text("Installed Games") },
                actions = {
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
        ) {
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
                    modifier = Modifier.weight(1f)
                )
            }

            SystemFilterRow(
                systems = systems,
                selected = systemFilter,
                onSelect = { systemFilter = it }
            )

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
                            contentPadding = PaddingValues(horizontal = 16.dp, vertical = 8.dp),
                            verticalArrangement = Arrangement.spacedBy(8.dp)
                        ) {
                            items(filtered, key = { rom -> rom.path.absolutePath }) { rom ->
                                InstalledRomCard(rom = rom, onClick = { confirmTarget = rom })
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
private fun SystemFilterRow(
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
                    onClick = { onSelect(null); expanded = false }
                )
                systems.forEach { system ->
                    DropdownMenuItem(
                        text = { Text(system) },
                        onClick = { onSelect(system); expanded = false }
                    )
                }
            }
        }
    }
}

@Composable
private fun InstalledRomCard(
    rom: InstalledRom,
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
