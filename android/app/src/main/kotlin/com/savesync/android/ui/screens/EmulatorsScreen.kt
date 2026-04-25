package com.savesync.android.ui.screens

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.FolderOpen
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import com.savesync.android.emulators.EmulatorCatalog
import com.savesync.android.emulators.EmulatorDescriptor
import com.savesync.android.emulators.impl.RetroArchEmulator
import com.savesync.android.ui.MainViewModel
import com.savesync.android.ui.components.FolderPickerDialog

/**
 * Per-emulator save folder configuration screen, reachable from
 * [SettingsScreen] via the "Emulator Configuration →" button.
 *
 * One card per emulator showing:
 *   • The current effective save folder (override OR auto-detected hint)
 *   • Browse / Clear buttons that write to ``Settings.saveDirOverrides``
 *   • Emulator-specific extras (RetroArch hosts the Beetle Saturn per-core
 *     and CD per-content toggles here so all of one emulator's knobs stay
 *     together)
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun EmulatorsScreen(
    viewModel: MainViewModel,
    onNavigateBack: () -> Unit,
) {
    val settings by viewModel.settings.collectAsState()

    var folderPickerForEmulator by remember { mutableStateOf<String?>(null) }

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text("Emulator Configuration") },
                navigationIcon = {
                    IconButton(onClick = onNavigateBack) {
                        Icon(
                            Icons.AutoMirrored.Filled.ArrowBack,
                            contentDescription = "Back"
                        )
                    }
                },
                colors = TopAppBarDefaults.topAppBarColors(
                    containerColor = MaterialTheme.colorScheme.primary,
                    titleContentColor = MaterialTheme.colorScheme.onPrimary,
                    navigationIconContentColor = MaterialTheme.colorScheme.onPrimary,
                )
            )
        }
    ) { paddingValues ->
        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(paddingValues)
                .verticalScroll(rememberScrollState())
                .padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            Text(
                text = "Set a custom save folder for each emulator. Existing " +
                       "auto-detection (Emudeck root, retroarch.cfg, etc.) " +
                       "is used when no override is configured. Existing " +
                       "saves on disk are still discovered regardless.",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )

            EmulatorCatalog.ALL.forEach { descriptor ->
                EmulatorCard(
                    descriptor = descriptor,
                    overridePath = settings.saveDirOverrides[descriptor.key],
                    onBrowse = { folderPickerForEmulator = descriptor.key },
                    onClear = { viewModel.clearSaveDirOverride(descriptor.key) },
                    extras = if (descriptor.key == RetroArchEmulator.EMULATOR_KEY) {
                        {
                            RetroArchExtras(
                                beetleSaturnPerCoreFolder = settings.beetleSaturnPerCoreFolder,
                                cdGamesPerContentFolder = settings.cdGamesPerContentFolder,
                                onBeetleChange = { newBeetle ->
                                    viewModel.saveRetroArchToggles(
                                        beetleSaturnPerCoreFolder = newBeetle,
                                        cdGamesPerContentFolder = settings.cdGamesPerContentFolder
                                    )
                                },
                                onPerContentChange = { newPerContent ->
                                    viewModel.saveRetroArchToggles(
                                        beetleSaturnPerCoreFolder = settings.beetleSaturnPerCoreFolder,
                                        cdGamesPerContentFolder = newPerContent
                                    )
                                },
                            )
                        }
                    } else null,
                )
            }
        }
    }

    val pickerKey = folderPickerForEmulator
    if (pickerKey != null) {
        FolderPickerDialog(
            initialPath = settings.saveDirOverrides[pickerKey] ?: "",
            onDismiss = { folderPickerForEmulator = null },
            onFolderSelected = { path ->
                viewModel.setSaveDirOverride(pickerKey, path)
                folderPickerForEmulator = null
            }
        )
    }
}

@Composable
private fun EmulatorCard(
    descriptor: EmulatorDescriptor,
    overridePath: String?,
    onBrowse: () -> Unit,
    onClear: () -> Unit,
    extras: (@Composable () -> Unit)? = null,
) {
    val effectivePath = overridePath?.takeIf { it.isNotBlank() }
    val isOverridden = effectivePath != null

    Card(
        modifier = Modifier.fillMaxWidth(),
        colors = CardDefaults.cardColors(
            containerColor = MaterialTheme.colorScheme.surfaceVariant
        ),
    ) {
        Column(
            modifier = Modifier
                .fillMaxWidth()
                .padding(12.dp),
            verticalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            // Title + subtitle
            Row(verticalAlignment = Alignment.CenterVertically) {
                Column(modifier = Modifier.weight(1f)) {
                    Text(
                        text = descriptor.displayName,
                        style = MaterialTheme.typography.titleMedium,
                        fontWeight = FontWeight.SemiBold,
                    )
                    Text(
                        text = descriptor.systemHint,
                        style = MaterialTheme.typography.labelSmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            }

            // Effective save path
            Column(modifier = Modifier.fillMaxWidth()) {
                Text(
                    text = if (isOverridden) "Override" else "Auto-detected",
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
                Text(
                    text = effectivePath ?: descriptor.defaultPathHint,
                    style = MaterialTheme.typography.bodySmall,
                    fontWeight = if (isOverridden) FontWeight.Bold else FontWeight.Normal,
                    maxLines = 2,
                    overflow = TextOverflow.Ellipsis,
                )
            }

            // Browse / Clear buttons
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.spacedBy(8.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                OutlinedButton(
                    onClick = onBrowse,
                    modifier = Modifier.weight(1f),
                ) {
                    Icon(
                        Icons.Default.FolderOpen,
                        contentDescription = null,
                        modifier = Modifier.size(18.dp),
                    )
                    Spacer(Modifier.width(6.dp))
                    Text(if (isOverridden) "Change" else "Set Override")
                }
                if (isOverridden) {
                    OutlinedButton(onClick = onClear) {
                        Icon(
                            Icons.Default.Close,
                            contentDescription = "Clear override",
                            modifier = Modifier.size(18.dp),
                        )
                        Spacer(Modifier.width(6.dp))
                        Text("Clear")
                    }
                }
            }

            // Emulator-specific extras (e.g. RetroArch toggles)
            if (extras != null) {
                HorizontalDivider()
                extras()
            }
        }
    }

    Spacer(Modifier.height(4.dp))
}

@Composable
private fun RetroArchExtras(
    beetleSaturnPerCoreFolder: Boolean,
    cdGamesPerContentFolder: Boolean,
    onBeetleChange: (Boolean) -> Unit,
    onPerContentChange: (Boolean) -> Unit,
) {
    Row(
        modifier = Modifier.fillMaxWidth(),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.SpaceBetween,
    ) {
        Column(modifier = Modifier.weight(1f)) {
            Text("Beetle Saturn: per-core save folder")
            Text(
                text = if (beetleSaturnPerCoreFolder)
                    "saves/Beetle Saturn/<rom>.bkr (matches RetroArch's Sort Saves by Core)."
                else
                    "saves/<rom>.bkr at the root.",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
        Switch(
            checked = beetleSaturnPerCoreFolder,
            onCheckedChange = onBeetleChange,
        )
    }

    Row(
        modifier = Modifier.fillMaxWidth(),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.SpaceBetween,
    ) {
        Column(modifier = Modifier.weight(1f)) {
            Text("CD games: per-content folder")
            Text(
                text = if (cdGamesPerContentFolder)
                    "CD-game saves go in saves/<game>/<rom>.<ext> (PS1, PS2, Saturn, Sega CD, Dreamcast, PCE, NeoCD)."
                else
                    "CD-game saves land at saves/<rom>.<ext> (root).",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
        Switch(
            checked = cdGamesPerContentFolder,
            onCheckedChange = onPerContentChange,
        )
    }
}
