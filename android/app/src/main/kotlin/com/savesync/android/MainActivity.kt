package com.savesync.android

import android.content.Context
import android.content.ContextWrapper
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.Environment
import android.os.SystemClock
import android.provider.Settings
import android.view.InputDevice
import android.view.KeyEvent
import android.view.MotionEvent
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
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.lifecycle.viewmodel.compose.viewModel
import androidx.navigation.NavGraph.Companion.findStartDestination
import androidx.navigation.NavHostController
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable
import androidx.navigation.compose.currentBackStackEntryAsState
import androidx.navigation.compose.rememberNavController
import com.savesync.android.ui.MainViewModel
import com.savesync.android.ui.screens.DownloadsScreen
import com.savesync.android.ui.screens.InstalledGamesScreen
import com.savesync.android.ui.screens.RomCatalogScreen
import com.savesync.android.ui.screens.SaveDetailScreen
import com.savesync.android.ui.screens.SavesScreen
import com.savesync.android.ui.screens.SettingsScreen
import com.savesync.android.ui.theme.SaveSyncTheme
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.StateFlow

class MainActivity : ComponentActivity() {

    private val _hasStoragePermission = MutableStateFlow(false)
    val hasStoragePermission: StateFlow<Boolean> = _hasStoragePermission

    // ── Gamepad plumbing ─────────────────────────────────────────────────────
    // L2/R2 (digital OR analog trigger) cycle the top-level tabs globally;
    // MainApp observes this flow to drive navController.
    private val _tabCycleEvents = MutableSharedFlow<Int>(extraBufferCapacity = 8)
    val tabCycleEvents: SharedFlow<Int> = _tabCycleEvents

    // Edge-detected state for analog triggers so we fire once per press,
    // not once per polled sample.
    private var lastLeftTrigger = false
    private var lastRightTrigger = false

    // Analog stick → DPAD key synthesis. Steam Deck uses deadzone 0.4 + 150 ms
    // repeat; we mirror that so sticks feel identical across both apps.
    private var lastAxisDirY = 0
    private var lastAxisDirX = 0
    private var lastAxisRepeat = 0L

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

    /**
     * Intercept L2/R2 globally to cycle tabs BEFORE any Compose handler sees
     * them. We match both the standard L2/R2 keycodes AND a couple of common
     * alternates some PS4/Xbox controllers send. Analog-only triggers are
     * handled in [dispatchGenericMotionEvent] below.
     */
    override fun dispatchKeyEvent(event: KeyEvent): Boolean {
        if (event.action == KeyEvent.ACTION_DOWN && event.repeatCount == 0) {
            when (event.keyCode) {
                KeyEvent.KEYCODE_BUTTON_L2 -> { _tabCycleEvents.tryEmit(-1); return true }
                KeyEvent.KEYCODE_BUTTON_R2 -> { _tabCycleEvents.tryEmit(1); return true }
            }
        }
        return super.dispatchKeyEvent(event)
    }

    /**
     * Handle three things from joystick MotionEvents:
     *  1. Analog L2/R2 triggers (AXIS_LTRIGGER/RTRIGGER or AXIS_BRAKE/GAS) →
     *     edge-detected tab cycling, as a fallback for controllers that don't
     *     emit KEYCODE_BUTTON_L2/R2.
     *  2. Left-stick AXIS_X/AXIS_Y → synthesized DPAD_* key events with 0.4
     *     deadzone and 150 ms repeat (matching the Steam Deck app).
     *  3. HAT axes AXIS_HAT_X/AXIS_HAT_Y (physical D-pad on most pads) →
     *     the SAME synthesized DPAD_* key path. Important: Android's built-in
     *     HAT→DPAD synthesizer only fires when this handler *doesn't* consume
     *     the MotionEvent, and we must return true below to hide the raw
     *     stick noise from children. So if we don't read HAT ourselves,
     *     physical DPAD presses get silently swallowed.
     */
    override fun dispatchGenericMotionEvent(event: MotionEvent): Boolean {
        val isJoystick = (event.source and InputDevice.SOURCE_JOYSTICK) == InputDevice.SOURCE_JOYSTICK
        if (!isJoystick || event.action != MotionEvent.ACTION_MOVE) {
            return super.dispatchGenericMotionEvent(event)
        }

        // ── L2/R2 analog trigger fallback ────────────────────────────────
        val lt = maxOf(
            event.getAxisValue(MotionEvent.AXIS_LTRIGGER),
            event.getAxisValue(MotionEvent.AXIS_BRAKE),
        )
        val rt = maxOf(
            event.getAxisValue(MotionEvent.AXIS_RTRIGGER),
            event.getAxisValue(MotionEvent.AXIS_GAS),
        )
        val ltDown = lt > TRIGGER_THRESHOLD
        val rtDown = rt > TRIGGER_THRESHOLD
        if (ltDown && !lastLeftTrigger) _tabCycleEvents.tryEmit(-1)
        if (rtDown && !lastRightTrigger) _tabCycleEvents.tryEmit(1)
        lastLeftTrigger = ltDown
        lastRightTrigger = rtDown

        // ── Stick + HAT → DPAD key synthesis ─────────────────────────────
        // Combine analog stick and HAT axes so both go through one
        // edge-detect/repeat pipeline. HAT values are always exactly
        // -1/0/+1 (digital DPAD); the 0.5 threshold is just a non-zero
        // check. If the DPAD is held, we get continuous MOVE events at
        // the driver's poll rate, so the repeat logic kicks in the same
        // as with the stick.
        val y = event.getAxisValue(MotionEvent.AXIS_Y)
        val hatY = event.getAxisValue(MotionEvent.AXIS_HAT_Y)
        val x = event.getAxisValue(MotionEvent.AXIS_X)
        val hatX = event.getAxisValue(MotionEvent.AXIS_HAT_X)

        val dirY = when {
            y < -STICK_DEADZONE || hatY < -0.5f -> -1
            y > STICK_DEADZONE || hatY > 0.5f -> 1
            else -> 0
        }
        val dirX = when {
            x < -STICK_DEADZONE || hatX < -0.5f -> -1
            x > STICK_DEADZONE || hatX > 0.5f -> 1
            else -> 0
        }

        if (dirY == 0 && dirX == 0) {
            // Stick returned to centre — reset so the next press fires immediately.
            lastAxisDirY = 0
            lastAxisDirX = 0
            lastAxisRepeat = 0L
            return true
        }

        val now = SystemClock.uptimeMillis()
        val directionChanged = dirY != lastAxisDirY || dirX != lastAxisDirX
        val repeatDue = now - lastAxisRepeat >= STICK_REPEAT_MS
        if (directionChanged || repeatDue) {
            if (dirY != 0) {
                val code = if (dirY < 0) KeyEvent.KEYCODE_DPAD_UP else KeyEvent.KEYCODE_DPAD_DOWN
                dispatchKeyEvent(KeyEvent(KeyEvent.ACTION_DOWN, code))
                dispatchKeyEvent(KeyEvent(KeyEvent.ACTION_UP, code))
            }
            if (dirX != 0) {
                val code = if (dirX < 0) KeyEvent.KEYCODE_DPAD_LEFT else KeyEvent.KEYCODE_DPAD_RIGHT
                dispatchKeyEvent(KeyEvent(KeyEvent.ACTION_DOWN, code))
                dispatchKeyEvent(KeyEvent(KeyEvent.ACTION_UP, code))
            }
            lastAxisDirY = dirY
            lastAxisDirX = dirX
            lastAxisRepeat = now
        }
        return true
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

    companion object {
        private const val STICK_DEADZONE = 0.4f
        private const val STICK_REPEAT_MS = 150L
        private const val TRIGGER_THRESHOLD = 0.5f
    }
}

/**
 * Ordered list of top-level tab routes. The index is what [TabSwitchBar]
 * binds to, so any reorder here automatically shifts the selected indicator.
 */
internal val TAB_ROUTES: List<String> = listOf("saves", "catalog", "installed", "downloads")

/** Unwrap a [Context] (possibly wrapped by a ContextWrapper chain) to the
 *  owning [ComponentActivity]. Returns null in previews. */
private fun Context.findComponentActivity(): ComponentActivity? {
    var current: Context? = this
    while (current is ContextWrapper) {
        if (current is ComponentActivity) return current
        current = current.baseContext
    }
    return null
}

@Composable
private fun MainApp() {
    val navController = rememberNavController()
    val viewModel: MainViewModel = viewModel()
    // Collected from ViewModel (SharingStarted.Eagerly) so it's already populated
    // by the time the first frame renders — no "?" flash on startup.
    val syncStateEntities by viewModel.syncStateEntities.collectAsState()

    val backStackEntry by navController.currentBackStackEntryAsState()
    val currentRoute = backStackEntry?.destination?.route

    // Wire L2/R2 → cycle tabs. The Activity emits on a SharedFlow and we
    // translate it into a navController.navigate() here so the NavHost is
    // the single source of truth for which tab is visible.
    val activity = LocalContext.current.findComponentActivity() as? MainActivity
    LaunchedEffect(activity, navController) {
        activity?.tabCycleEvents?.collect { delta ->
            val route = navController.currentDestination?.route
            val idx = TAB_ROUTES.indexOf(route)
            if (idx < 0) return@collect   // on detail/settings — don't cycle
            val next = (idx + delta + TAB_ROUTES.size) % TAB_ROUTES.size
            navigateToTab(navController, TAB_ROUTES[next])
        }
    }

    // Auto-jump to Downloads when the user enqueues a new ROM so they see
    // the live progress immediately instead of guessing if it started.
    LaunchedEffect(navController, viewModel) {
        viewModel.navigateToDownloadsTab.collect {
            val current = navController.currentDestination?.route
            // Only auto-jump from the catalog — leaves saves / installed
            // alone for users who triggered a download from elsewhere.
            if (current == TAB_ROUTES[1]) {
                navigateToTab(navController, TAB_ROUTES[3])
            }
        }
    }

    val onNavigateToTab: (Int) -> Unit = { idx ->
        navigateToTab(navController, TAB_ROUTES[idx])
    }

    // NOTE: no Scaffold topBar/bottomBar here — the tab strip is now
    // folded into each screen's own TopAppBar via TabSwitchBar, so the
    // user sees a single merged toolbar instead of two stacked bars.
    Scaffold { padding ->
        NavHost(
            navController = navController,
            startDestination = TAB_ROUTES[0],
            modifier = Modifier.padding(padding),
        ) {
            composable(TAB_ROUTES[0]) {
                SavesScreen(
                    viewModel = viewModel,
                    syncStateEntities = syncStateEntities,
                    onNavigateToSettings = { navController.navigate("settings") },
                    onNavigateToDetail = { titleId -> navController.navigate("detail/$titleId") },
                    onNavigateToTab = onNavigateToTab,
                )
            }
            composable(TAB_ROUTES[1]) {
                RomCatalogScreen(
                    viewModel = viewModel,
                    onNavigateToTab = onNavigateToTab,
                )
            }
            composable(TAB_ROUTES[2]) {
                InstalledGamesScreen(
                    viewModel = viewModel,
                    onNavigateToTab = onNavigateToTab,
                )
            }
            composable(TAB_ROUTES[3]) {
                DownloadsScreen(
                    viewModel = viewModel,
                    onNavigateToTab = onNavigateToTab,
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
}

private fun navigateToTab(navController: NavHostController, route: String) {
    if (navController.currentDestination?.route == route) return
    navController.navigate(route) {
        // Pop back to the start destination so we don't stack
        // a History Every Tap Ever.
        popUpTo(navController.graph.findStartDestination().id) {
            saveState = true
        }
        launchSingleTop = true
        restoreState = true
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
            text = "GameSync needs \"All files access\" to read and write emulator save files (RetroArch, PPSSPP, DraStic, etc.).",
            style = MaterialTheme.typography.bodyMedium
        )
        Spacer(Modifier.height(8.dp))
        Text(
            text = "If the button doesn't open Settings, grant it manually:\n\nSettings → Apps → Special app access → All files access → GameSync → Allow\n\n(Note: this does NOT appear under the regular Permissions screen)",
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
