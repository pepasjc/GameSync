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
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.FolderOpen
import androidx.compose.material.icons.filled.Visibility
import androidx.compose.material.icons.filled.VisibilityOff
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.ExposedDropdownMenuBox
import androidx.compose.material3.ExposedDropdownMenuDefaults
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import kotlinx.coroutines.delay
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.input.VisualTransformation
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import com.savesync.android.BuildConfig
import com.savesync.android.api.ApiClient
import com.savesync.android.emulators.EmudeckPaths
import com.savesync.android.sync.SaturnSyncFormat
import com.savesync.android.ui.MainViewModel
import com.savesync.android.ui.components.FolderPickerDialog
import kotlinx.coroutines.launch

private val intervalOptions = listOf(5, 15, 30, 60)

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun SettingsScreen(
    viewModel: MainViewModel,
    onNavigateBack: () -> Unit,
    onNavigateToEmulators: () -> Unit = {}
) {
    val settings by viewModel.settings.collectAsState()
    val scope = rememberCoroutineScope()

    // Initialize local state from DataStore exactly once (when first non-default value arrives).
    // Using remember(key) would reset the field every time DataStore emits, wiping the user's input.
    var serverUrl by remember { mutableStateOf("") }
    var apiKey by remember { mutableStateOf("") }
    var apiKeyVisible by remember { mutableStateOf(false) }
    var autoSync by remember { mutableStateOf(false) }
    var intervalMinutes by remember { mutableStateOf(15) }
    var romScanDir by remember { mutableStateOf("") }
    var emudeckDir by remember { mutableStateOf("") }
    var saturnSyncFormat by remember { mutableStateOf(SaturnSyncFormat.MEDNAFEN) }
    var showFolderPicker by remember { mutableStateOf(false) }
    var showEmudeckFolderPicker by remember { mutableStateOf(false) }
    var settingsLoaded by remember { mutableStateOf(false) }

    // System folder overrides
    val detectedFolders by viewModel.detectedSystemFolders.collectAsState()
    var folderPickerForSystem by remember { mutableStateOf<String?>(null) }
    var addSystemExpanded by remember { mutableStateOf(false) }
    var addSystemSelected by remember { mutableStateOf("") }

    LaunchedEffect(settings) {
        if (!settingsLoaded) {
            serverUrl = settings.serverUrl
            apiKey = settings.apiKey
            autoSync = settings.autoSyncEnabled
            intervalMinutes = settings.autoSyncIntervalMinutes
            romScanDir = settings.romScanDir
            emudeckDir = settings.emudeckDir
            saturnSyncFormat = settings.saturnSyncFormat
            settingsLoaded = true
            // Auto-detect system folders once settings are loaded
            if (settings.romScanDir.isNotBlank() || settings.emudeckDir.isNotBlank()) {
                viewModel.detectSystemFolders()
            }
        }
    }
    var connectionStatus by remember { mutableStateOf<String?>(null) }
    var connectionOk by remember { mutableStateOf<Boolean?>(null) }
    var intervalDropdownExpanded by remember { mutableStateOf(false) }
    var savedConfirmation by remember { mutableStateOf(false) }
    val effectiveRomScanDir = EmudeckPaths.romsDir(emudeckDir)?.absolutePath ?: romScanDir

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text("Settings") },
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
                    navigationIconContentColor = MaterialTheme.colorScheme.onPrimary
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
            verticalArrangement = Arrangement.spacedBy(12.dp)
        ) {

            // --- Server settings ---
            Text("Server", style = MaterialTheme.typography.titleMedium)

            OutlinedTextField(
                value = serverUrl,
                onValueChange = { serverUrl = it },
                label = { Text("Server URL") },
                placeholder = { Text("http://192.168.1.100:8000") },
                modifier = Modifier.fillMaxWidth(),
                singleLine = true,
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Uri)
            )

            OutlinedTextField(
                value = apiKey,
                onValueChange = { apiKey = it },
                label = { Text("API Key") },
                modifier = Modifier.fillMaxWidth(),
                singleLine = true,
                visualTransformation = if (apiKeyVisible) VisualTransformation.None
                    else PasswordVisualTransformation(),
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Password),
                trailingIcon = {
                    IconButton(onClick = { apiKeyVisible = !apiKeyVisible }) {
                        Icon(
                            if (apiKeyVisible) Icons.Default.VisibilityOff else Icons.Default.Visibility,
                            contentDescription = if (apiKeyVisible) "Hide" else "Show"
                        )
                    }
                }
            )

            Row(
                horizontalArrangement = Arrangement.spacedBy(8.dp),
                verticalAlignment = Alignment.CenterVertically
            ) {
                Button(
                    onClick = {
                        scope.launch {
                            connectionStatus = "Testing…"
                            connectionOk = null
                            try {
                                val api = ApiClient.create(serverUrl, apiKey)
                                val resp = api.getStatus()
                                connectionStatus = "Connected — v${resp.version}"
                                connectionOk = true
                            } catch (e: Exception) {
                                connectionStatus = "Failed: ${e.message}"
                                connectionOk = false
                            }
                        }
                    },
                    modifier = Modifier.weight(1f)
                ) {
                    Text("Test Connection")
                }

                Button(
                    onClick = {
                        viewModel.saveSettings(
                            serverUrl = serverUrl,
                            apiKey = apiKey,
                            autoSync = autoSync,
                            intervalMinutes = intervalMinutes,
                            romScanDir = romScanDir,
                            emudeckDir = emudeckDir,
                            saturnSyncFormat = saturnSyncFormat,
                            beetleSaturnPerCoreFolder = settings.beetleSaturnPerCoreFolder,
                            cdGamesPerContentFolder = settings.cdGamesPerContentFolder
                        )
                        savedConfirmation = true
                    },
                    modifier = Modifier.weight(1f),
                    colors = ButtonDefaults.buttonColors(
                        containerColor = MaterialTheme.colorScheme.primaryContainer,
                        contentColor = MaterialTheme.colorScheme.onPrimaryContainer
                    )
                ) {
                    Text("Save")
                }
            }

            if (savedConfirmation) {
                Text(
                    text = "✓ Settings saved",
                    style = MaterialTheme.typography.bodySmall,
                    color = Color(0xFF4CAF50)
                )
                LaunchedEffect(Unit) {
                    kotlinx.coroutines.delay(3000)
                    savedConfirmation = false
                }
            }

            connectionStatus?.let { status ->
                Text(
                    text = status,
                    color = when (connectionOk) {
                        true -> Color(0xFF4CAF50)
                        false -> MaterialTheme.colorScheme.error
                        null -> MaterialTheme.colorScheme.onSurfaceVariant
                    },
                    style = MaterialTheme.typography.bodySmall
                )
            }

            HorizontalDivider()

            // --- Emudeck ---
            Text("Emudeck", style = MaterialTheme.typography.titleMedium)

            Text(
                text = "Optional Emudeck folder. When set, 3DS uses storage/Azahar, " +
                       "GC/Wii uses storage/Dolphin, PS2 uses storage/NetherSX2, " +
                       "and PSP uses storage/PPSSPP. Other emulators keep their normal paths.",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant
            )

            Row(
                modifier = Modifier.fillMaxWidth(),
                verticalAlignment = Alignment.CenterVertically
            ) {
                Column(modifier = Modifier.weight(1f)) {
                    Text(
                        text = "Emudeck Folder",
                        style = MaterialTheme.typography.labelMedium,
                        color = MaterialTheme.colorScheme.onSurfaceVariant
                    )
                    Text(
                        text = if (emudeckDir.isNotBlank()) emudeckDir else "Not set",
                        style = MaterialTheme.typography.bodySmall,
                        color = if (emudeckDir.isNotBlank())
                            MaterialTheme.colorScheme.onSurface
                        else
                            MaterialTheme.colorScheme.onSurfaceVariant
                    )
                }
                Spacer(Modifier.width(8.dp))
                Button(onClick = { showEmudeckFolderPicker = true }) {
                    Icon(
                        Icons.Default.FolderOpen,
                        contentDescription = null,
                        modifier = Modifier.size(18.dp)
                    )
                    Spacer(Modifier.width(6.dp))
                    Text("Browse")
                }
            }

            if (emudeckDir.isNotBlank()) {
                OutlinedButton(
                    onClick = { emudeckDir = "" },
                    modifier = Modifier.fillMaxWidth()
                ) {
                    Text("Clear Emudeck Folder")
                }
            }

            HorizontalDivider()

            // --- Auto sync ---
            Text("Auto Sync", style = MaterialTheme.typography.titleMedium)

            Row(
                modifier = Modifier.fillMaxWidth(),
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.SpaceBetween
            ) {
                Text("Enable auto sync")
                Switch(checked = autoSync, onCheckedChange = { autoSync = it })
            }

            if (autoSync) {
                ExposedDropdownMenuBox(
                    expanded = intervalDropdownExpanded,
                    onExpandedChange = { intervalDropdownExpanded = it }
                ) {
                    OutlinedTextField(
                        value = "$intervalMinutes minutes",
                        onValueChange = {},
                        readOnly = true,
                        label = { Text("Sync Interval") },
                        trailingIcon = { ExposedDropdownMenuDefaults.TrailingIcon(intervalDropdownExpanded) },
                        modifier = Modifier
                            .fillMaxWidth()
                            .menuAnchor()
                    )
                    ExposedDropdownMenu(
                        expanded = intervalDropdownExpanded,
                        onDismissRequest = { intervalDropdownExpanded = false }
                    ) {
                        intervalOptions.forEach { minutes ->
                            DropdownMenuItem(
                                text = { Text("$minutes minutes") },
                                onClick = {
                                    intervalMinutes = minutes
                                    intervalDropdownExpanded = false
                                }
                            )
                        }
                    }
                }
            }

            HorizontalDivider()

            // --- Scan ---
            Text("Saves", style = MaterialTheme.typography.titleMedium)

            Button(
                onClick = { viewModel.scanSaves() },
                modifier = Modifier.fillMaxWidth()
            ) {
                Text("Scan for Save Files")
            }

            var saturnFormatExpanded by remember { mutableStateOf(false) }
            ExposedDropdownMenuBox(
                expanded = saturnFormatExpanded,
                onExpandedChange = { saturnFormatExpanded = it }
            ) {
                OutlinedTextField(
                    value = saturnSyncFormat.label,
                    onValueChange = {},
                    readOnly = true,
                    label = { Text("Saturn Download Format") },
                    supportingText = {
                        Text("Server stays on Beetle/Mednafen format; this controls how Saturn saves are written locally.")
                    },
                    trailingIcon = {
                        ExposedDropdownMenuDefaults.TrailingIcon(saturnFormatExpanded)
                    },
                    modifier = Modifier
                        .fillMaxWidth()
                        .menuAnchor()
                )
                ExposedDropdownMenu(
                    expanded = saturnFormatExpanded,
                    onDismissRequest = { saturnFormatExpanded = false }
                ) {
                    SaturnSyncFormat.values().forEach { format ->
                        DropdownMenuItem(
                            text = { Text(format.label) },
                            onClick = {
                                saturnSyncFormat = format
                                saturnFormatExpanded = false
                            }
                        )
                    }
                }
            }

            // (Beetle Saturn per-core + CD per-content toggles moved to the
            // Emulator Configuration screen — they live with the rest of the
            // RetroArch knobs there.)

            HorizontalDivider()

            // --- Emulator Configuration link ---
            OutlinedButton(
                onClick = onNavigateToEmulators,
                modifier = Modifier.fillMaxWidth()
            ) {
                Text("Emulator Configuration →")
            }
            Text(
                text = "Per-emulator save folder overrides, RetroArch toggles, " +
                       "and other emulator-specific options.",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant
            )

            HorizontalDivider()

            // --- ROM Scan Directory ---
            Text("ROMs", style = MaterialTheme.typography.titleMedium)

            Text(
                text = "Select the folder where your ROMs are organized by system subfolder " +
                       "(e.g. GBA/, MegaDrive/, PS1/, SNES/, …). GameSync uses this to detect " +
                       "which games you have installed so it can show server saves for them. " +
                       "When Emudeck is set, GameSync uses its roms folder.",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant
            )

            // Current path display + browse button
            Row(
                modifier = Modifier.fillMaxWidth(),
                verticalAlignment = Alignment.CenterVertically
            ) {
                Column(modifier = Modifier.weight(1f)) {
                    Text(
                        text = "ROM Directory",
                        style = MaterialTheme.typography.labelMedium,
                        color = MaterialTheme.colorScheme.onSurfaceVariant
                    )
                    Text(
                        text = if (effectiveRomScanDir.isNotBlank()) effectiveRomScanDir else "Not set",
                        style = MaterialTheme.typography.bodySmall,
                        color = if (effectiveRomScanDir.isNotBlank())
                            MaterialTheme.colorScheme.onSurface
                        else
                            MaterialTheme.colorScheme.onSurfaceVariant
                    )
                }
                Spacer(Modifier.width(8.dp))
                Button(onClick = { showFolderPicker = true }) {
                    Icon(
                        Icons.Default.FolderOpen,
                        contentDescription = null,
                        modifier = Modifier.size(18.dp)
                    )
                    Spacer(Modifier.width(6.dp))
                    Text("Browse")
                }
            }

            if (romScanDir.isNotBlank() && emudeckDir.isBlank()) {
                OutlinedButton(
                    onClick = { romScanDir = "" },
                    modifier = Modifier.fillMaxWidth()
                ) {
                    Text("Clear ROM Directory")
                }
            }

            // ROM scan diagnostic
            val romScanResults by viewModel.romScanResults.collectAsState()
            OutlinedButton(
                onClick = { viewModel.runRomScanDiagnostic(romScanDir) },
                modifier = Modifier.fillMaxWidth()
            ) {
                Text("Test ROM Scan")
            }

            // Prepare canonical per-system folders so catalog downloads
            // land in predictable places instead of inventing stray
            // folder names.  Existing files/folders are never touched.
            val prepareMessage by viewModel.prepareFoldersMessage.collectAsState()
            OutlinedButton(
                onClick = { viewModel.prepareRomFolders(romScanDir) },
                modifier = Modifier.fillMaxWidth(),
                enabled = effectiveRomScanDir.isNotBlank(),
            ) {
                Text("Prepare ROM Folders")
            }
            Text(
                text = "Creates the standard per-system folders " +
                    "(PS1, GBA, SEGACD, …) under your ROM directory. " +
                    "Existing folders are left alone.",
                style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            prepareMessage?.let { msg ->
                Text(
                    text = msg,
                    style = MaterialTheme.typography.labelSmall,
                    color = Color(0xFF4CAF50),
                )
            }
            if (romScanResults.isNotEmpty()) {
                Spacer(Modifier.height(4.dp))
                Text(
                    "Systems found in ROM directory:",
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant
                )
                romScanResults.forEach { (system, count) ->
                    val ok = count > 0
                    Text(
                        text = "${if (ok) "✓" else "✗"}  $system  —  $count ROM${if (count != 1) "s" else ""}",
                        style = MaterialTheme.typography.labelSmall,
                        color = if (ok) Color(0xFF4CAF50) else MaterialTheme.colorScheme.onSurfaceVariant
                    )
                }
            }

            // --- Per-system folder overrides ---
            HorizontalDivider()
            Text("System Folders", style = MaterialTheme.typography.titleMedium)
            Text(
                text = "GameSync auto-detects which subfolder of your ROM directory belongs " +
                       "to each system. Override any folder here — useful when a folder has " +
                       "a non-standard name (e.g. \"Saturn\" instead of \"SAT\").",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant
            )

            Row(
                modifier = Modifier.fillMaxWidth(),
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.SpaceBetween
            ) {
                val overrides = settings.romDirOverrides
                val allSystems = (detectedFolders.keys + overrides.keys).toSortedSet()
                Text(
                    text = "${allSystems.size} system${if (allSystems.size != 1) "s" else ""} detected",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant
                )
                OutlinedButton(onClick = { viewModel.detectSystemFolders() }) {
                    Text("Detect", style = MaterialTheme.typography.labelMedium)
                }
            }

            val overrides = settings.romDirOverrides
            val allSystems = (detectedFolders.keys + overrides.keys).toSortedSet()

            if (allSystems.isEmpty() && effectiveRomScanDir.isNotBlank()) {
                Text(
                    text = "No system folders detected yet — press Detect.",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant
                )
            }

            allSystems.forEach { system ->
                val effectivePath = overrides[system] ?: detectedFolders[system] ?: return@forEach
                val isOverridden = system in overrides
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    verticalAlignment = Alignment.CenterVertically
                ) {
                    // System badge
                    SystemChip(system)
                    Spacer(Modifier.width(8.dp))
                    // Folder path (shrinks to fill remaining space)
                    Column(modifier = Modifier.weight(1f)) {
                        Text(
                            text = effectivePath.substringAfterLast('/'),
                            style = MaterialTheme.typography.bodySmall,
                            fontWeight = if (isOverridden) FontWeight.Bold else FontWeight.Normal,
                            maxLines = 1,
                            overflow = TextOverflow.Ellipsis
                        )
                        if (isOverridden) {
                            Text(
                                text = effectivePath,
                                style = MaterialTheme.typography.labelSmall,
                                color = MaterialTheme.colorScheme.onSurfaceVariant,
                                maxLines = 1,
                                overflow = TextOverflow.Ellipsis
                            )
                        }
                    }
                    // Browse button
                    IconButton(onClick = { folderPickerForSystem = system }) {
                        Icon(
                            Icons.Default.FolderOpen,
                            contentDescription = "Change folder",
                            modifier = Modifier.size(20.dp),
                            tint = MaterialTheme.colorScheme.primary
                        )
                    }
                    // Clear override button (only shown when user has set an override)
                    if (isOverridden) {
                        IconButton(onClick = { viewModel.clearRomDirOverride(system) }) {
                            Icon(
                                Icons.Default.Close,
                                contentDescription = "Reset to auto-detected",
                                modifier = Modifier.size(20.dp),
                                tint = MaterialTheme.colorScheme.error
                            )
                        }
                    }
                }
            }

            // Add a custom system folder not yet auto-detected
            val knownAddSystems = listOf(
                "SAT", "DC", "PS1", "PS2", "PSP", "GBA", "GBC", "GB", "SNES", "NES",
                "N64", "NDS", "3DS", "GC", "WII", "MD", "SMS", "GG", "SEGACD", "PCE", "NEOCD",
                "NGP", "WSWAN", "WSWANC", "A2600", "A7800", "LYNX", "MAME", "ARCADE"
            ).filter { it !in allSystems }

            if (knownAddSystems.isNotEmpty()) {
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(8.dp)
                ) {
                    ExposedDropdownMenuBox(
                        expanded = addSystemExpanded,
                        onExpandedChange = { addSystemExpanded = it },
                        modifier = Modifier.weight(1f)
                    ) {
                        OutlinedTextField(
                            value = addSystemSelected.ifBlank { "Add system…" },
                            onValueChange = {},
                            readOnly = true,
                            trailingIcon = { ExposedDropdownMenuDefaults.TrailingIcon(addSystemExpanded) },
                            modifier = Modifier
                                .fillMaxWidth()
                                .menuAnchor(),
                            textStyle = MaterialTheme.typography.bodySmall
                        )
                        ExposedDropdownMenu(
                            expanded = addSystemExpanded,
                            onDismissRequest = { addSystemExpanded = false }
                        ) {
                            knownAddSystems.forEach { sys ->
                                DropdownMenuItem(
                                    text = { Text(sys, style = MaterialTheme.typography.bodySmall) },
                                    onClick = {
                                        addSystemSelected = sys
                                        addSystemExpanded = false
                                    }
                                )
                            }
                        }
                    }
                    Button(
                        onClick = { folderPickerForSystem = addSystemSelected },
                        enabled = addSystemSelected.isNotBlank()
                    ) {
                        Icon(Icons.Default.Add, contentDescription = null, modifier = Modifier.size(18.dp))
                        Spacer(Modifier.width(4.dp))
                        Text("Browse")
                    }
                }
            }

            // RetroArch path diagnostics
            val retroPaths by viewModel.retroArchPaths.collectAsState()
            if (retroPaths.isNotEmpty()) {
                Spacer(Modifier.height(4.dp))
                Text(
                    "RetroArch detected paths:",
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant
                )
                for ((path, found) in retroPaths) {
                    Text(
                        text = "${if (found) "✓" else "✗"}  $path",
                        style = MaterialTheme.typography.labelSmall,
                        color = if (found) Color(0xFF4CAF50) else MaterialTheme.colorScheme.onSurfaceVariant
                    )
                }
            }
            OutlinedButton(
                onClick = { viewModel.checkRetroArchPaths() },
                modifier = Modifier.fillMaxWidth()
            ) {
                Text("Diagnose RetroArch Paths")
            }

            HorizontalDivider()

            // (Dolphin GC memory card folder moved to the Emulator
            // Configuration screen alongside the other emulator save-folder
            // overrides.  The legacy `dolphinMemCardDir` value is auto-merged
            // into `saveDirOverrides["Dolphin"]` on first read so existing
            // installs keep working.)

            // --- App info ---
            Spacer(Modifier.height(4.dp))
            Text(
                text = "GameSync v${BuildConfig.VERSION_NAME}",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant
            )
            Text(
                text = "Console ID: ${settings.consoleId}",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant
            )
        }
    }

    // Folder picker dialog — shown outside the Scaffold so it overlays everything
    if (showFolderPicker) {
        FolderPickerDialog(
            initialPath = romScanDir,
            onDismiss = { showFolderPicker = false },
            onFolderSelected = { path ->
                romScanDir = path
                showFolderPicker = false
                // Auto-save immediately so the setting persists even without
                // pressing "Save", and so the ROM scan diagnostic picks it up.
                viewModel.saveSettings(
                    serverUrl = serverUrl,
                    apiKey = apiKey,
                    autoSync = autoSync,
                    intervalMinutes = intervalMinutes,
                    romScanDir = path,
                    emudeckDir = emudeckDir,
                    saturnSyncFormat = saturnSyncFormat,
                    beetleSaturnPerCoreFolder = settings.beetleSaturnPerCoreFolder,
                    cdGamesPerContentFolder = settings.cdGamesPerContentFolder
                )
            }
        )
    }

    if (showEmudeckFolderPicker) {
        FolderPickerDialog(
            initialPath = emudeckDir,
            onDismiss = { showEmudeckFolderPicker = false },
            onFolderSelected = { path ->
                emudeckDir = path
                showEmudeckFolderPicker = false
                viewModel.saveSettings(
                    serverUrl = serverUrl,
                    apiKey = apiKey,
                    autoSync = autoSync,
                    intervalMinutes = intervalMinutes,
                    romScanDir = romScanDir,
                    emudeckDir = path,
                    saturnSyncFormat = saturnSyncFormat,
                    beetleSaturnPerCoreFolder = settings.beetleSaturnPerCoreFolder,
                    cdGamesPerContentFolder = settings.cdGamesPerContentFolder
                )
            }
        )
    }

    // Per-system folder picker
    val pickerSystem = folderPickerForSystem
    if (pickerSystem != null) {
        val currentOverride = settings.romDirOverrides[pickerSystem]
        val detectedPath = detectedFolders[pickerSystem]
        FolderPickerDialog(
            initialPath = currentOverride ?: detectedPath ?: romScanDir,
            onDismiss = { folderPickerForSystem = null },
            onFolderSelected = { path ->
                viewModel.setRomDirOverride(pickerSystem, path)
                folderPickerForSystem = null
                addSystemSelected = ""
            }
        )
    }
}
