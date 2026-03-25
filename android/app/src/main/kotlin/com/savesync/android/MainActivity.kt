package com.savesync.android

import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.Environment
import android.provider.Settings
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.core.content.ContextCompat
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.lifecycle.viewmodel.compose.viewModel
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable
import androidx.navigation.compose.rememberNavController
import com.savesync.android.storage.AppDatabase
import com.savesync.android.ui.MainViewModel
import com.savesync.android.ui.screens.SaveDetailScreen
import com.savesync.android.ui.screens.SavesScreen
import com.savesync.android.ui.screens.SettingsScreen
import com.savesync.android.ui.theme.SaveSyncTheme
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow

class MainActivity : ComponentActivity() {

    private val _hasStoragePermission = MutableStateFlow(false)
    val hasStoragePermission: StateFlow<Boolean> = _hasStoragePermission

    private val storagePermissionLauncher =
        registerForActivityResult(ActivityResultContracts.RequestMultiplePermissions()) { results ->
            // Granted if at least READ was granted (or on Android 13+ where it's auto-granted)
            val granted = results.values.any { it } || results.isEmpty()
            _hasStoragePermission.value = granted
        }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        // Check current permission state
        _hasStoragePermission.value = checkStoragePermission()

        setContent {
            SaveSyncTheme {
                Surface(
                    modifier = Modifier.fillMaxSize(),
                    color = MaterialTheme.colorScheme.background
                ) {
                    val hasPermission by hasStoragePermission.collectAsState()
                    if (hasPermission) {
                        MainApp()
                    } else {
                        PermissionRationale(
                            onGrantClick = { requestStoragePermission() },
                            onSkipClick = { _hasStoragePermission.value = true }
                        )
                    }
                }
            }
        }
    }

    override fun onResume() {
        super.onResume()
        // Re-check permission when returning from system settings
        _hasStoragePermission.value = checkStoragePermission()
    }

    private fun checkStoragePermission(): Boolean {
        return if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            Environment.isExternalStorageManager()
        } else {
            ContextCompat.checkSelfPermission(
                this, android.Manifest.permission.READ_EXTERNAL_STORAGE
            ) == PackageManager.PERMISSION_GRANTED
        }
    }

    private fun requestStoragePermission() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            // Open the per-app All Files Access page; fall back to the general list
            val perApp = Intent(Settings.ACTION_MANAGE_APP_ALL_FILES_ACCESS_PERMISSION).apply {
                data = Uri.parse("package:$packageName")
            }
            val general = Intent(Settings.ACTION_MANAGE_ALL_FILES_ACCESS_PERMISSION)
            try {
                storagePermissionLauncher.launch(arrayOf()) // no-op, just to satisfy launcher
                startActivity(perApp)
            } catch (_: Exception) {
                try { startActivity(general) } catch (_: Exception) { /* user must navigate manually */ }
            }
        } else {
            storagePermissionLauncher.launch(
                arrayOf(
                    android.Manifest.permission.READ_EXTERNAL_STORAGE,
                    android.Manifest.permission.WRITE_EXTERNAL_STORAGE
                )
            )
        }
    }
}

@Composable
private fun MainApp() {
    val navController = rememberNavController()
    val viewModel: MainViewModel = viewModel()
    val db = AppDatabase.getInstance(androidx.compose.ui.platform.LocalContext.current)
    val syncStateEntities by db.syncStateDao().getAll().collectAsState(initial = emptyList())

    NavHost(navController = navController, startDestination = "saves") {
        composable("saves") {
            SavesScreen(
                viewModel = viewModel,
                syncStateEntities = syncStateEntities,
                onNavigateToSettings = { navController.navigate("settings") },
                onNavigateToDetail = { titleId -> navController.navigate("detail/$titleId") }
            )
        }
        composable("settings") {
            SettingsScreen(
                viewModel = viewModel,
                onNavigateBack = { navController.popBackStack() }
            )
        }
        composable("detail/{titleId}") { backStackEntry ->
            val titleId = backStackEntry.arguments?.getString("titleId") ?: return@composable
            SaveDetailScreen(
                titleId = titleId,
                viewModel = viewModel,
                syncStateEntities = syncStateEntities,
                onNavigateBack = { navController.popBackStack() }
            )
        }
    }
}

@Composable
private fun PermissionRationale(onGrantClick: () -> Unit, onSkipClick: () -> Unit) {
    Column(
        modifier = Modifier
            .fillMaxSize()
            .padding(24.dp),
        verticalArrangement = Arrangement.Center,
        horizontalAlignment = Alignment.CenterHorizontally
    ) {
        Text(
            text = "Storage Permission Required",
            style = MaterialTheme.typography.headlineSmall
        )
        Spacer(Modifier.height(16.dp))
        Text(
            text = "Save Sync needs \"All files access\" to read and write emulator save files (RetroArch, PPSSPP, DraStic, etc.).",
            style = MaterialTheme.typography.bodyMedium
        )
        Spacer(Modifier.height(8.dp))
        Text(
            text = "If the button doesn't open Settings, grant it manually:\n\nSettings → Apps → Special app access → All files access → Save Sync → Allow\n\n(Note: this does NOT appear under the regular Permissions screen)",
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant
        )
        Spacer(Modifier.height(24.dp))
        Button(
            onClick = onGrantClick,
            modifier = Modifier.fillMaxWidth()
        ) {
            Text("Open Settings to Grant Permission")
        }
        Spacer(Modifier.height(8.dp))
        OutlinedButton(
            onClick = onSkipClick,
            modifier = Modifier.fillMaxWidth()
        ) {
            Text("Skip (limited functionality)")
        }
    }
}
