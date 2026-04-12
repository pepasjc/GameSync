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
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.input.VisualTransformation
import androidx.compose.ui.unit.dp
import com.savesync.android.BuildConfig
import com.savesync.android.api.ApiClient
import com.savesync.android.ui.MainViewModel
import com.savesync.android.ui.components.FolderPickerDialog
import kotlinx.coroutines.launch

private val intervalOptions = listOf(5, 15, 30, 60)

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun SettingsScreen(
    viewModel: MainViewModel,
    onNavigateBack: () -> Unit
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
    var dolphinMemCardDir by remember { mutableStateOf("") }
    var showFolderPicker by remember { mutableStateOf(false) }
    var showDolphinFolderPicker by remember { mutableStateOf(false) }
    var settingsLoaded by remember { mutableStateOf(false) }

    LaunchedEffect(settings) {
        if (!settingsLoaded) {
            serverUrl = settings.serverUrl
            apiKey = settings.apiKey
            autoSync = settings.autoSyncEnabled
            intervalMinutes = settings.autoSyncIntervalMinutes
            romScanDir = settings.romScanDir
            dolphinMemCardDir = settings.dolphinMemCardDir
            settingsLoaded = true
        }
    }
    var connectionStatus by remember { mutableStateOf<String?>(null) }
    var connectionOk by remember { mutableStateOf<Boolean?>(null) }
    var intervalDropdownExpanded by remember { mutableStateOf(false) }
    var savedConfirmation by remember { mutableStateOf(false) }

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
                        viewModel.saveSettings(serverUrl, apiKey, autoSync, intervalMinutes, romScanDir, dolphinMemCardDir)
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

            HorizontalDivider()

            // --- ROM Scan Directory ---
            Text("ROMs", style = MaterialTheme.typography.titleMedium)

            Text(
                text = "Select the folder where your ROMs are organized by system subfolder " +
                       "(e.g. GBA/, MegaDrive/, PS1/, SNES/, …). GameSync uses this to detect " +
                       "which games you have installed so it can show server saves for them.",
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
                        text = if (romScanDir.isNotBlank()) romScanDir else "Not set",
                        style = MaterialTheme.typography.bodySmall,
                        color = if (romScanDir.isNotBlank())
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

            if (romScanDir.isNotBlank()) {
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

            // --- Dolphin GC ---
            Text("Dolphin (GameCube)", style = MaterialTheme.typography.titleMedium)

            Text(
                text = "Path to the Dolphin GC memory card folder (e.g. /sdcard/dolphin-mmjr/GC). " +
                       "Required if your saves are on an SD card or a different Dolphin variant. " +
                       "Leave empty to use the default dolphin-mmjr path on internal storage.",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant
            )

            Row(
                modifier = Modifier.fillMaxWidth(),
                verticalAlignment = Alignment.CenterVertically
            ) {
                Column(modifier = Modifier.weight(1f)) {
                    Text(
                        text = "Dolphin GC Folder",
                        style = MaterialTheme.typography.labelMedium,
                        color = MaterialTheme.colorScheme.onSurfaceVariant
                    )
                    Text(
                        text = if (dolphinMemCardDir.isNotBlank()) dolphinMemCardDir else "Default (dolphin-mmjr/GC)",
                        style = MaterialTheme.typography.bodySmall,
                        color = if (dolphinMemCardDir.isNotBlank())
                            MaterialTheme.colorScheme.onSurface
                        else
                            MaterialTheme.colorScheme.onSurfaceVariant
                    )
                }
                Spacer(Modifier.width(8.dp))
                Button(onClick = { showDolphinFolderPicker = true }) {
                    Icon(
                        Icons.Default.FolderOpen,
                        contentDescription = null,
                        modifier = Modifier.size(18.dp)
                    )
                    Spacer(Modifier.width(6.dp))
                    Text("Browse")
                }
            }

            if (dolphinMemCardDir.isNotBlank()) {
                OutlinedButton(
                    onClick = { dolphinMemCardDir = "" },
                    modifier = Modifier.fillMaxWidth()
                ) {
                    Text("Clear Dolphin Path")
                }
            }

            HorizontalDivider()

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
                viewModel.saveSettings(serverUrl, apiKey, autoSync, intervalMinutes, path, dolphinMemCardDir)
            }
        )
    }

    if (showDolphinFolderPicker) {
        FolderPickerDialog(
            initialPath = dolphinMemCardDir,
            onDismiss = { showDolphinFolderPicker = false },
            onFolderSelected = { path ->
                dolphinMemCardDir = path
                showDolphinFolderPicker = false
                viewModel.saveSettings(serverUrl, apiKey, autoSync, intervalMinutes, romScanDir, path)
            }
        )
    }
}
