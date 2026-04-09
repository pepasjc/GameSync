package com.savesync.android.ui.screens

import androidx.compose.foundation.clickable
import androidx.compose.foundation.focusable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.filled.CloudDownload
import androidx.compose.material.icons.filled.CloudUpload
import androidx.compose.material.icons.filled.Edit
import androidx.compose.material.icons.filled.Sync
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.ExposedDropdownMenuBox
import androidx.compose.material3.ExposedDropdownMenuDefaults
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.RadioButton
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
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.input.key.Key
import androidx.compose.ui.input.key.KeyEventType
import androidx.compose.ui.input.key.key
import androidx.compose.ui.input.key.onPreviewKeyEvent
import androidx.compose.ui.input.key.type
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.savesync.android.emulators.SaveEntry
import com.savesync.android.storage.SyncStateEntity
import com.savesync.android.ui.MainViewModel
import com.savesync.android.ui.NormalizePickerState
import com.savesync.android.ui.SaveDetailState
import com.savesync.android.ui.SaveSyncStatus
import com.savesync.android.ui.ServerMetaState
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun SaveDetailScreen(
    titleId: String,
    viewModel: MainViewModel,
    syncStateEntities: List<SyncStateEntity>,
    onNavigateBack: () -> Unit
) {
    val saves by viewModel.saves.collectAsState()
    val detailState by viewModel.saveDetailState.collectAsState()
    val serverMeta by viewModel.serverMeta.collectAsState()
    val snackbarHostState = remember { SnackbarHostState() }
    val normalizePickerState by viewModel.normalizePicker.collectAsState()

    val entry = saves.find { it.titleId == titleId }
    val syncState = syncStateEntities.find { it.titleId == titleId }

    // Auto-fetch server metadata when screen opens
    LaunchedEffect(titleId, entry?.systemName) {
        viewModel.fetchServerMeta(titleId, entry?.systemName)
    }

    // Show result messages
    LaunchedEffect(detailState) {
        when (val state = detailState) {
            is SaveDetailState.Success -> {
                snackbarHostState.showSnackbar(state.message)
                viewModel.resetDetailState()
                if (state.navigateBack) onNavigateBack()
            }
            is SaveDetailState.Error -> {
                snackbarHostState.showSnackbar("Error: ${state.message}")
                viewModel.resetDetailState()
            }
            else -> Unit
        }
    }

    // Normalize name picker dialog
    val pickerState = normalizePickerState
    if (pickerState is NormalizePickerState.Visible) {
        var selectedIndex by remember(pickerState) { mutableStateOf(0) }
        AlertDialog(
            onDismissRequest = { viewModel.dismissNormalizePicker() },
            title = { Text("Choose canonical name") },
            text = {
                Column {
                    Text(
                        text = "Select the correct version. " +
                               "USA releases are listed first; demos and protos last.",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                        modifier = Modifier.padding(bottom = 8.dp)
                    )
                    // Scrollable list so long regional variant lists don't overflow
                    LazyColumn(modifier = Modifier.heightIn(max = 320.dp)) {
                        itemsIndexed(pickerState.options) { index, name ->
                            Row(
                                modifier = Modifier
                                    .fillMaxWidth()
                                    .clickable { selectedIndex = index }
                                    .padding(vertical = 2.dp),
                                verticalAlignment = Alignment.CenterVertically
                            ) {
                                RadioButton(
                                    selected = selectedIndex == index,
                                    onClick = { selectedIndex = index }
                                )
                                Spacer(Modifier.width(8.dp))
                                Column {
                                    Text(name, style = MaterialTheme.typography.bodyMedium)
                                    when {
                                        index == 0 ->
                                            Text(
                                                "★ Recommended",
                                                style = MaterialTheme.typography.labelSmall,
                                                color = MaterialTheme.colorScheme.primary
                                            )
                                        name == pickerState.entry.displayName ->
                                            Text(
                                                "current name",
                                                style = MaterialTheme.typography.labelSmall,
                                                color = MaterialTheme.colorScheme.onSurfaceVariant
                                            )
                                    }
                                }
                            }
                        }
                    }
                }
            },
            confirmButton = {
                TextButton(onClick = {
                    viewModel.applyNormalizationChoice(
                        pickerState.entry,
                        pickerState.options[selectedIndex]
                    )
                }) { Text("Apply") }
            },
            dismissButton = {
                TextButton(onClick = { viewModel.dismissNormalizePicker() }) { Text("Cancel") }
            }
        )
    }

    Scaffold(
        modifier = Modifier
            .onPreviewKeyEvent { event ->
                if (event.type != KeyEventType.KeyDown) return@onPreviewKeyEvent false
                when (event.key) {
                    // Gamepad B / Escape → navigate back
                    Key.ButtonB, Key.Escape, Key.Back -> {
                        onNavigateBack()
                        true
                    }
                    else -> false
                }
            }
            .focusable(),
        topBar = {
            TopAppBar(
                title = { Text(entry?.displayName ?: titleId, maxLines = 1) },
                navigationIcon = {
                    IconButton(onClick = onNavigateBack) {
                        Icon(Icons.AutoMirrored.Filled.ArrowBack, contentDescription = "Back")
                    }
                },
                colors = TopAppBarDefaults.topAppBarColors(
                    containerColor = MaterialTheme.colorScheme.primary,
                    titleContentColor = MaterialTheme.colorScheme.onPrimary,
                    navigationIconContentColor = MaterialTheme.colorScheme.onPrimary
                )
            )
        },
        snackbarHost = { SnackbarHost(snackbarHostState) }
    ) { paddingValues ->
        if (entry == null) {
            Box(
                Modifier.fillMaxSize().padding(paddingValues),
                contentAlignment = Alignment.Center
            ) {
                Text("Save not found")
            }
            return@Scaffold
        }

        val isBusy = detailState is SaveDetailState.Working

        // Compute local hash — recompute when the underlying file is modified (e.g. after download)
        val fileModTime = when {
            entry.saveFile != null && entry.extraFiles.isNotEmpty() ->
                (listOf(entry.saveFile) + entry.extraFiles).filter { it.exists() }.maxOfOrNull { it.lastModified() } ?: 0L
            else -> entry.saveFile?.lastModified() ?: entry.saveDir?.lastModified() ?: 0L
        }
        val localHash = remember(entry.titleId, fileModTime) {
            try { entry.computeHash() } catch (_: Exception) { null }
        }
        val localSize = remember(entry.titleId, fileModTime) {
            when {
                entry.saveFile != null && entry.extraFiles.isNotEmpty() ->
                    (listOf(entry.saveFile) + entry.extraFiles).filter { it.exists() }.sumOf { it.length() }
                entry.isMultiFile && entry.saveDir != null ->
                    entry.saveDir.walkTopDown().filter { it.isFile }.sumOf { it.length() }
                entry.saveFile != null ->
                    entry.saveFile.length()
                // PSP: saveFile=null, saveDir=slot dir (all files)
                entry.saveDir != null ->
                    entry.saveDir.walkTopDown().filter { it.isFile }.sumOf { it.length() }
                else -> 0L
            }
        }

        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(paddingValues)
                .padding(horizontal = 20.dp, vertical = 12.dp)
                .verticalScroll(rememberScrollState()),
            verticalArrangement = Arrangement.spacedBy(10.dp)
        ) {
            // System badge + sync status badge + picker for RETRO
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                SystemChip(entry.systemName)
                val detailSyncStatus = viewModel.computeSyncStatus(entry, syncState, cheapOnly = true)
                SyncStatusBadge(detailSyncStatus)
                if (entry.systemName == "RETRO") {
                    Text("Unknown — set below:", style = MaterialTheme.typography.labelSmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
            }
            if (entry.systemName == "RETRO") {
                SystemPicker(onSystemSelected = { viewModel.setSaveSystem(entry, it) })
            }

            HorizontalDivider()
            InfoRow("Title ID", entry.titleId)
            InfoRow("File", entry.saveDir?.name ?: entry.saveFile?.name ?: "—")
            entry.canonicalName?.let { canonical ->
                InfoRow("Canonical name", canonical)
            }

            HorizontalDivider()

            // ── Local vs Server comparison ──────────────────────────────
            Text("Status", style = MaterialTheme.typography.titleSmall, fontWeight = FontWeight.Bold)

            // LOCAL
            Text("📱 Local", style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
            InfoRow("  Size", formatSize(localSize))
            InfoRow("  Hash", localHash?.take(16)?.let { "$it…" } ?: "—")
            syncState?.lastSyncedAt?.takeIf { it > 0 }?.let {
                InfoRow("  Last synced", formatTimestampFull(it))
            } ?: InfoRow("  Last synced", "Never")

            Spacer(Modifier.height(4.dp))

            // SERVER
            Text("☁ Server", style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
            when (val meta = serverMeta) {
                is ServerMetaState.Loading ->
                    Text("  Fetching…", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                is ServerMetaState.NotFound ->
                    Text("  No save on server yet", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                is ServerMetaState.Found -> {
                    InfoRow("  Size", formatSize(meta.sizeBytes))
                    InfoRow("  Hash", meta.hash.take(16).let { "$it…" })
                    if (meta.timestamp > 0) InfoRow("  Saved at", formatTimestampFull(meta.timestamp))
                    meta.source?.let { InfoRow("  Source", it) }
                    // Match indicator
                    val matches = localHash != null && localHash == meta.hash
                    Text(
                        text = if (matches) "  ✓ In sync" else "  ≠ Out of sync",
                        style = MaterialTheme.typography.bodySmall,
                        fontWeight = FontWeight.Bold,
                        color = if (matches) Color(0xFF4CAF50) else MaterialTheme.colorScheme.error
                    )
                }
                is ServerMetaState.Error ->
                    Text("  Error: ${meta.message}", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.error)
                is ServerMetaState.Idle -> {}
            }

            HorizontalDivider()

            // ── Action buttons ────────────────────────────────────────────
            ActionButton(
                label = if (isBusy && detailState.isSync) "Syncing…" else "Smart Sync",
                icon = Icons.Default.Sync,
                enabled = !isBusy,
                containerColor = MaterialTheme.colorScheme.primary,
                isBusy = isBusy && detailState.isSync,
                onClick = { viewModel.syncSave(entry) }
            )
            ActionButton(
                label = if (isBusy && detailState.isUpload) "Uploading…" else "Force Upload ↑",
                icon = Icons.Default.CloudUpload,
                enabled = !isBusy && entry.exists(),
                containerColor = Color(0xFF1565C0),
                isBusy = isBusy && detailState.isUpload,
                onClick = { viewModel.uploadSave(entry) }
            )
            ActionButton(
                label = if (isBusy && detailState.isDownload) "Downloading…" else "Force Download ↓",
                icon = Icons.Default.CloudDownload,
                enabled = !isBusy,
                containerColor = Color(0xFF2E7D32),
                isBusy = isBusy && detailState.isDownload,
                onClick = { viewModel.downloadSave(entry) }
            )
            if (!entry.isServerOnly && entry.systemName != "PPSSPP") {
                HorizontalDivider()
                ActionButton(
                    label = if (isBusy && detailState.isNormalize) "Normalizing…" else
                        entry.canonicalName?.let { "Normalize (→ $it)" } ?: "Normalize Name",
                    icon = Icons.Default.Edit,
                    enabled = !isBusy,
                    containerColor = MaterialTheme.colorScheme.surfaceVariant,
                    contentColor = MaterialTheme.colorScheme.onSurfaceVariant,
                    isBusy = isBusy && detailState.isNormalize,
                    onClick = { viewModel.normalizeRomAndSave(entry) }
                )
            }
        }
    }
}

private val knownSystems = listOf(
    "GBA", "SNES", "NES", "GB", "GBC", "N64",
    "PS1", "PS2", "PSP", "SAT", "DC",
    "MD", "SEGACD", "GC", "WII",
    "PCE", "NGP", "WSWAN", "WSWANC", "ARCADE", "NEOCD",
    "NDS", "A2600", "LYNX", "MAME"
)

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun SystemPicker(onSystemSelected: (String) -> Unit) {
    var expanded by remember { mutableStateOf(false) }
    var selected by remember { mutableStateOf("Select system…") }

    ExposedDropdownMenuBox(
        expanded = expanded,
        onExpandedChange = { expanded = it }
    ) {
        OutlinedTextField(
            value = selected,
            onValueChange = {},
            readOnly = true,
            label = { Text("Set System") },
            trailingIcon = { ExposedDropdownMenuDefaults.TrailingIcon(expanded) },
            modifier = Modifier.fillMaxWidth().menuAnchor()
        )
        ExposedDropdownMenu(
            expanded = expanded,
            onDismissRequest = { expanded = false }
        ) {
            knownSystems.forEach { system ->
                DropdownMenuItem(
                    text = { Text(system) },
                    onClick = {
                        selected = system
                        expanded = false
                        onSystemSelected(system)
                    }
                )
            }
        }
    }
}

@Composable
private fun InfoRow(label: String, value: String) {
    Row(
        modifier = Modifier.fillMaxWidth(),
        horizontalArrangement = Arrangement.SpaceBetween
    ) {
        Text(
            text = label,
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            fontWeight = FontWeight.Medium
        )
        Text(
            text = value,
            style = MaterialTheme.typography.bodySmall
        )
    }
}

@Composable
private fun ActionButton(
    label: String,
    icon: ImageVector,
    enabled: Boolean,
    containerColor: Color,
    isBusy: Boolean,
    onClick: () -> Unit,
    contentColor: Color = Color.White
) {
    Button(
        onClick = onClick,
        enabled = enabled,
        modifier = Modifier.fillMaxWidth(),
        colors = ButtonDefaults.buttonColors(
            containerColor = containerColor,
            contentColor = contentColor
        )
    ) {
        if (isBusy) {
            CircularProgressIndicator(
                modifier = Modifier.size(18.dp),
                strokeWidth = 2.dp,
                color = contentColor
            )
        } else {
            Icon(icon, contentDescription = null, modifier = Modifier.size(18.dp))
        }
        Spacer(Modifier.width(8.dp))
        Text(label)
    }
}

private val SaveDetailState.isSync get() = this is SaveDetailState.Working && action == "sync"
private val SaveDetailState.isUpload get() = this is SaveDetailState.Working && action == "upload"
private val SaveDetailState.isDownload get() = this is SaveDetailState.Working && action == "download"
private val SaveDetailState.isNormalize get() = this is SaveDetailState.Working && action == "normalize"

private fun formatSize(bytes: Long): String = when {
    bytes < 1024 -> "$bytes B"
    bytes < 1024 * 1024 -> "${"%.1f".format(bytes / 1024.0)} KB"
    else -> "${"%.2f".format(bytes / (1024.0 * 1024))} MB"
}

private fun formatTimestampFull(millis: Long): String {
    val sdf = SimpleDateFormat("MMM d yyyy, HH:mm:ss", Locale.getDefault())
    return sdf.format(Date(millis))
}
