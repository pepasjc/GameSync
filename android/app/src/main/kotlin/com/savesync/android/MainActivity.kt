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
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.SideEffect
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.lifecycle.lifecycleScope
import androidx.lifecycle.viewmodel.compose.viewModel
import androidx.navigation.NavGraph.Companion.findStartDestination
import androidx.navigation.NavHostController
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable
import androidx.navigation.compose.currentBackStackEntryAsState
import androidx.navigation.compose.rememberNavController
import com.savesync.android.ui.MainViewModel
import com.savesync.android.ui.screens.DownloadsScreen
import com.savesync.android.ui.screens.EmulatorsScreen
import com.savesync.android.ui.screens.InstalledGamesScreen
import com.savesync.android.ui.screens.RomCatalogScreen
import com.savesync.android.ui.screens.SaveDetailScreen
import com.savesync.android.ui.screens.SavesScreen
import com.savesync.android.ui.screens.SettingsScreen
import com.savesync.android.ui.components.GsConfirmDialog
import com.savesync.android.ui.components.LocalServerOnline
import com.savesync.android.ui.components.LocalShowButtonHints
import com.savesync.android.ui.components.rememberShowButtonHints
import com.savesync.android.ui.theme.SaveSyncTheme
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch

class MainActivity : ComponentActivity() {

    private val _hasStoragePermission = MutableStateFlow(false)
    val hasStoragePermission: StateFlow<Boolean> = _hasStoragePermission

    // ── Gamepad plumbing ─────────────────────────────────────────────────────
    // The unified GameSync controls (same on every console client):
    //   L1 / R1   → previous / next top-level tab (wraps); MainApp drives
    //               the navController from tabCycleEvents.
    //   SELECT    → next sub-tab (the system filter chip on Saves / Catalog /
    //               Installed); each screen subscribes to systemCycleEvents.
    //   L2 / R2   → step the sync-status filter on Saves (statusCycleEvents);
    //               digital buttons or analog triggers.  No longer tabs.
    //   START     → exit, after a confirmation dialog (exitRequests).
    //   B         → cancel / back only: a B nothing on screen claimed is
    //               swallowed on a top-level tab so it never exits the app.
    //   A         → screens handle it; where nothing does (Settings rows) it
    //               activates the focused control like D-pad centre.
    private val _tabCycleEvents = MutableSharedFlow<Int>(extraBufferCapacity = 8)
    val tabCycleEvents: SharedFlow<Int> = _tabCycleEvents

    private val _systemCycleEvents = MutableSharedFlow<Int>(extraBufferCapacity = 8)
    val systemCycleEvents: SharedFlow<Int> = _systemCycleEvents

    private val _statusCycleEvents = MutableSharedFlow<Int>(extraBufferCapacity = 8)
    val statusCycleEvents: SharedFlow<Int> = _statusCycleEvents

    private val _exitRequests = MutableSharedFlow<Unit>(extraBufferCapacity = 1)
    val exitRequests: SharedFlow<Unit> = _exitRequests

    /** Kept current by MainApp: true while a top-level tab is showing. */
    @Volatile
    var onTopLevelTab: Boolean = true

    // Edge-detected state for analog triggers so we fire once per press,
    // not once per polled sample.
    private var lastLeftTrigger = false
    private var lastRightTrigger = false

    // Analog stick + HAT → DPAD key synthesis.  Steam Deck uses deadzone 0.4
    // + 150 ms repeat; we mirror that so sticks feel identical across both
    // apps.  HAT axes (physical D-pad on most pads) only fire ACTION_MOVE on
    // edge transitions — not while the pad is held — so we drive the repeat
    // from a coroutine timer keyed off the last known direction rather than
    // off MotionEvent arrival.
    private var heldDirX = 0
    private var heldDirY = 0
    private var axisRepeatJob: Job? = null

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
     * Intercept the global buttons (L1/R1, L2/R2, SELECT, START) BEFORE any
     * Compose handler sees them, and post-process A/B that nothing claimed.
     * Analog-only triggers are handled in [dispatchGenericMotionEvent] below.
     * Only this window's events arrive here — an open dialog gets its own
     * keys, which is how its A/B/START work.
     */
    override fun dispatchKeyEvent(event: KeyEvent): Boolean {
        val globalDelta: Pair<MutableSharedFlow<Int>, Int>? = when (event.keyCode) {
            KeyEvent.KEYCODE_BUTTON_L1 -> _tabCycleEvents to -1
            KeyEvent.KEYCODE_BUTTON_R1 -> _tabCycleEvents to 1
            KeyEvent.KEYCODE_BUTTON_L2 -> _statusCycleEvents to -1
            KeyEvent.KEYCODE_BUTTON_R2 -> _statusCycleEvents to 1
            KeyEvent.KEYCODE_BUTTON_SELECT -> _systemCycleEvents to 1
            else -> null
        }
        if (globalDelta != null) {
            if (event.action == KeyEvent.ACTION_DOWN && event.repeatCount == 0) {
                globalDelta.first.tryEmit(globalDelta.second)
            }
            return true   // consume DOWN and UP alike
        }
        if (event.keyCode == KeyEvent.KEYCODE_BUTTON_START) {
            if (event.action == KeyEvent.ACTION_DOWN && event.repeatCount == 0) {
                _exitRequests.tryEmit(Unit)
            }
            return true
        }

        if (super.dispatchKeyEvent(event)) return true

        return when (event.keyCode) {
            // B is cancel / back only.  Unclaimed, Android would turn it
            // into BACK and leave the app from a top-level tab; on a pushed
            // screen (save detail, emulator config) that BACK is exactly
            // "back", so let it through there.
            KeyEvent.KEYCODE_BUTTON_B -> onTopLevelTab
            // A that no screen handler claimed (Settings, the emulator
            // config screen) activates the focused control, the way D-pad
            // centre does.  Explicit so it doesn't depend on the device's
            // key-character-map fallback.
            KeyEvent.KEYCODE_BUTTON_A -> super.dispatchKeyEvent(
                KeyEvent(
                    event.downTime,
                    event.eventTime,
                    event.action,
                    KeyEvent.KEYCODE_DPAD_CENTER,
                    event.repeatCount,
                    event.metaState,
                    event.deviceId,
                    event.scanCode,
                    event.flags,
                    event.source,
                )
            )
            else -> false
        }
    }

    /**
     * Handle three things from joystick MotionEvents:
     *  1. Analog L2/R2 triggers (AXIS_LTRIGGER/RTRIGGER or AXIS_BRAKE/GAS) →
     *     edge-detected status-filter stepping (Saves), as a fallback for
     *     controllers that don't emit KEYCODE_BUTTON_L2/R2.
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

        // ── L2/R2 analog trigger fallback → status filter step ───────────
        // Some controllers (PS4/PS5 over Bluetooth, certain Xbox pads) only
        // emit analog AXIS_LTRIGGER/RTRIGGER values, never the digital
        // KEYCODE_BUTTON_L2/R2. Mirror the digital path here so the filter
        // steps either way.
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
        if (ltDown && !lastLeftTrigger) _statusCycleEvents.tryEmit(-1)
        if (rtDown && !lastRightTrigger) _statusCycleEvents.tryEmit(1)
        lastLeftTrigger = ltDown
        lastRightTrigger = rtDown

        // ── Stick + HAT → DPAD key synthesis ─────────────────────────────
        // HAT values are exactly -1/0/+1; the 0.5 threshold is a non-zero
        // check.  Crucially, HAT axes do NOT emit ACTION_MOVE events while
        // the pad is held — only on edge transitions — so we can't drive
        // the repeat off MotionEvent arrival.  Instead, MotionEvent only
        // updates [heldDirX]/[heldDirY], and a single coroutine fires the
        // synthesised ACTION_DOWN+ACTION_UP pairs every STICK_REPEAT_MS
        // until both axes return to zero.
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

        updateHeldDpad(dirX, dirY)
        return true
    }

    private fun emitDpadPulse(dirX: Int, dirY: Int) {
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
    }

    private fun updateHeldDpad(dirX: Int, dirY: Int) {
        if (dirX == heldDirX && dirY == heldDirY) return
        heldDirX = dirX
        heldDirY = dirY
        axisRepeatJob?.cancel()
        axisRepeatJob = null
        if (dirX == 0 && dirY == 0) return
        // Fire the edge press immediately, then keep pulsing while held.
        emitDpadPulse(dirX, dirY)
        axisRepeatJob = lifecycleScope.launch {
            delay(STICK_REPEAT_MS)
            while (isActive && (heldDirX != 0 || heldDirY != 0)) {
                emitDpadPulse(heldDirX, heldDirY)
                delay(STICK_REPEAT_MS)
            }
        }
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
internal val TAB_ROUTES: List<String> = listOf("saves", "catalog", "installed", "downloads", "settings")

/** The top-level tab a pushed route belongs to, for L1/R1 from inside it. */
private fun parentTabIndex(route: String?): Int = when {
    route == null -> 0
    route in TAB_ROUTES -> TAB_ROUTES.indexOf(route)
    route.startsWith("detail") -> 0
    route == "emulators" -> TAB_ROUTES.indexOf("settings")
    else -> -1
}

/** Unwrap a [Context] (possibly wrapped by a ContextWrapper chain) to the
 *  owning [ComponentActivity]. Returns null in previews. */
internal fun Context.findComponentActivity(): ComponentActivity? {
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
    val serverOnline by viewModel.serverOnline.collectAsState()

    val backStackEntry by navController.currentBackStackEntryAsState()
    val currentRoute = backStackEntry?.destination?.route

    val activity = LocalContext.current.findComponentActivity() as? MainActivity

    // Tell the Activity whether an unclaimed B must be swallowed (top-level
    // tab) or may become BACK (pushed screen).
    SideEffect {
        activity?.onTopLevelTab = currentRoute == null || currentRoute in TAB_ROUTES
    }

    // L1/R1 → previous / next top-level tab, wrapping.  The Activity emits
    // on a SharedFlow and we translate it into navController.navigate() so
    // the NavHost stays the single source of truth for the visible tab.
    LaunchedEffect(activity, navController) {
        activity?.tabCycleEvents?.collect { delta ->
            val idx = parentTabIndex(navController.currentDestination?.route)
            if (idx < 0) return@collect
            val next = (idx + delta + TAB_ROUTES.size) % TAB_ROUTES.size
            navigateToTab(navController, TAB_ROUTES[next])
        }
    }

    // START → exit, after asking (A / START again exits, B stays).
    var showExitDialog by remember { mutableStateOf(false) }
    LaunchedEffect(activity) {
        activity?.exitRequests?.collect { showExitDialog = true }
    }

    // (Removed: previously we auto-jumped to the Downloads tab on
    // enqueue, but that yanked the user out of the catalog mid-browse.
    // The catalog's own snackbar — "Queued <name> — see Downloads tab
    // for progress" — is now the only confirmation.  Users navigate
    // manually when they want to watch.)

    val onNavigateToTab: (Int) -> Unit = { idx ->
        navigateToTab(navController, TAB_ROUTES[idx])
    }

    val showHints = rememberShowButtonHints()

    CompositionLocalProvider(
        LocalServerOnline provides serverOnline,
        LocalShowButtonHints provides showHints,
    ) {
        // No Scaffold bars here — each screen draws its own GsTopBar (header
        // + tab strip + sub-tab chips) and GsFooterHints, so the hints can
        // follow the screen's state.
        Scaffold(containerColor = MaterialTheme.colorScheme.background) { padding ->
            NavHost(
                navController = navController,
                startDestination = TAB_ROUTES[0],
                modifier = Modifier.padding(padding),
            ) {
                composable(TAB_ROUTES[0]) {
                    SavesScreen(
                        viewModel = viewModel,
                        syncStateEntities = syncStateEntities,
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
                composable(TAB_ROUTES[4]) {
                    SettingsScreen(
                        viewModel = viewModel,
                        onNavigateToTab = onNavigateToTab,
                        onNavigateToEmulators = { navController.navigate("emulators") }
                    )
                }
                composable("emulators") {
                    EmulatorsScreen(
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

        if (showExitDialog) {
            GsConfirmDialog(
                title = "Exit GameSync?",
                message = "Running downloads keep going in the background.",
                confirmLabel = "Exit",
                dismissLabel = "Stay",
                startConfirms = true,
                onConfirm = {
                    showExitDialog = false
                    activity?.finish()
                },
                onDismiss = { showExitDialog = false },
            )
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
