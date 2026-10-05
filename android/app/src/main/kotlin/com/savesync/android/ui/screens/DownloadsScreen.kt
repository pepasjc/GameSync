package com.savesync.android.ui.screens

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
import androidx.compose.material.icons.filled.Cancel
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material.icons.filled.Download
import androidx.compose.material.icons.filled.Pause
import androidx.compose.material.icons.filled.PlayArrow
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateMapOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.input.key.Key
import androidx.compose.ui.input.key.KeyEventType
import androidx.compose.ui.input.key.key
import androidx.compose.ui.input.key.onPreviewKeyEvent
import androidx.compose.ui.input.key.type
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.savesync.android.storage.DownloadEntity
import com.savesync.android.sync.DownloadManager
import com.savesync.android.ui.MainViewModel
import com.savesync.android.ui.components.GsButton
import com.savesync.android.ui.components.GsConfirmDialog
import com.savesync.android.ui.components.GsFooterHints
import com.savesync.android.ui.components.GsHint
import com.savesync.android.ui.components.GsListRow
import com.savesync.android.ui.components.GsPill
import com.savesync.android.ui.components.GsTopBar
import com.savesync.android.ui.components.handleHorizontalHoldKeyEvent
import com.savesync.android.ui.components.rememberHoldNavState
import com.savesync.android.ui.theme.GsColors
import kotlinx.coroutines.launch

/**
 * Downloads tab — single source of truth for in-flight + finished ROM
 * downloads.  Built around the application-scoped
 * [com.savesync.android.sync.DownloadManager] so a download survives
 * Activity recreation, low-memory kills, and tab switches.
 *
 * Each row supports:
 *   * Pause / Resume (HTTP Range based — picks up at the saved byte offset)
 *   * Cancel (deletes the .part file)
 *   * Remove (purges the row from the database)
 *
 * Controller: a row cursor (D-pad), A = pause / resume / retry the
 * selected row, B = pause the running download, X = clear finished,
 * Y = cancel (active) or remove (finished) the selected row, after asking.
 *
 * The progress bar is driven by the live ProgressEvents flow (≈4 Hz)
 * with a fallback to the persisted Room row when no event is current —
 * so a paused download still shows the right percentage.
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun DownloadsScreen(
    viewModel: MainViewModel,
    onNavigateToTab: (Int) -> Unit = {},
) {
    val downloads by viewModel.downloads.collectAsState()

    // Live { id → most-recent ProgressEvent } map.  Compose's
    // [mutableStateMapOf] only invalidates the entries that actually
    // changed, so a 5 Hz progress stream doesn't recompose the whole
    // list — just the row whose key was updated.
    val liveProgress = remember { mutableStateMapOf<String, DownloadManager.ProgressEvent>() }
    LaunchedEffect(Unit) {
        viewModel.downloadProgressEvents.collect { event ->
            liveProgress[event.id] = event
        }
    }

    // When a download flips to COMPLETED, ask the rest of the app to
    // refresh so the new file shows up in Saves / Installed Games.
    val previousStatuses = remember { mutableStateOf<Map<String, String>>(emptyMap()) }
    LaunchedEffect(downloads) {
        val newMap = downloads.associate { it.id to it.status }
        val prev = previousStatuses.value
        var anyCompleted = false
        for (download in downloads) {
            val previous = prev[download.id]
            if (previous != DownloadEntity.Status.COMPLETED &&
                download.status == DownloadEntity.Status.COMPLETED
            ) anyCompleted = true
        }
        if (anyCompleted) viewModel.onDownloadCompleted()
        previousStatuses.value = newMap
    }

    val activeCount = downloads.count {
        it.status == DownloadEntity.Status.DOWNLOADING ||
            it.status == DownloadEntity.Status.QUEUED
    }
    val finishedCount = downloads.count { it.isTerminal }

    // ── Gamepad cursor ──────────────────────────────────────────────────
    var selectedIndex by remember { mutableIntStateOf(0) }
    val listState = rememberLazyListState()
    val scope = rememberCoroutineScope()
    val holdNav = rememberHoldNavState()
    val listFocusRequester = remember { FocusRequester() }
    // Y asks before cancelling / removing the selected row.
    var removeTarget by remember { mutableStateOf<DownloadEntity?>(null) }

    LaunchedEffect(downloads.size) {
        selectedIndex = if (downloads.isEmpty()) 0
            else selectedIndex.coerceIn(0, downloads.size - 1)
    }
    LaunchedEffect(Unit) { runCatching { listFocusRequester.requestFocus() } }

    // The summary line sits above the rows as item 0 while anything runs.
    val headerItems = if (activeCount > 0) 1 else 0
    fun moveTo(index: Int) {
        if (downloads.isEmpty()) return
        selectedIndex = index.coerceIn(0, downloads.size - 1)
        scope.launch { listState.animateScrollToItem(selectedIndex + headerItems) }
    }

    /** A: the selected row's main action, by status. */
    fun primaryAction(download: DownloadEntity) {
        when (download.status) {
            DownloadEntity.Status.DOWNLOADING,
            DownloadEntity.Status.QUEUED -> viewModel.pauseDownload(download.id)
            DownloadEntity.Status.PAUSED,
            DownloadEntity.Status.FAILED,
            DownloadEntity.Status.CANCELLED -> viewModel.resumeDownload(download.id)
            else -> Unit   // completed: nothing to do
        }
    }

    val selected = downloads.getOrNull(selectedIndex)
    val primaryLabel = when (selected?.status) {
        DownloadEntity.Status.DOWNLOADING, DownloadEntity.Status.QUEUED -> "Pause"
        DownloadEntity.Status.PAUSED -> "Resume"
        DownloadEntity.Status.FAILED, DownloadEntity.Status.CANCELLED -> "Retry"
        else -> null
    }

    Scaffold(
        topBar = {
            GsTopBar(
                activeTabIndex = 3,
                onTabClick = onNavigateToTab,
                actions = {
                    if (finishedCount > 0) {
                        TextButton(onClick = { viewModel.clearFinishedDownloads() }) {
                            Text("Clear finished", color = GsColors.Accent)
                        }
                    }
                },
            )
        },
        bottomBar = {
            GsFooterHints(
                buildList {
                    if (primaryLabel != null) add(GsHint(GsButton.A, primaryLabel))
                    if (activeCount > 0) add(GsHint(GsButton.B, "Pause running"))
                    if (finishedCount > 0) add(GsHint(GsButton.X, "Clear finished"))
                    if (selected != null) {
                        add(GsHint(GsButton.Y, if (selected.isTerminal) "Remove" else "Cancel"))
                    }
                    add(GsHint(GsButton.L1, "Tabs"))
                    add(GsHint(GsButton.START, "Exit"))
                }
            )
        },
    ) { padding ->
        Box(
            modifier = Modifier
                .fillMaxSize()
                .padding(padding)
                .focusRequester(listFocusRequester)
                .focusable()
                .onPreviewKeyEvent { event ->
                    if (holdNav.handleHorizontalHoldKeyEvent(
                            event,
                            scope,
                            onPage = { d ->
                                val page = listState.layoutInfo.visibleItemsInfo.size.coerceAtLeast(1)
                                moveTo(selectedIndex + d * page)
                            },
                            // No names to letter-jump through: keep paging.
                            onAlphabet = { d ->
                                val page = listState.layoutInfo.visibleItemsInfo.size.coerceAtLeast(1)
                                moveTo(selectedIndex + d * page)
                            },
                        )
                    ) return@onPreviewKeyEvent true
                    if (event.type != KeyEventType.KeyDown) return@onPreviewKeyEvent false
                    when (event.key) {
                        Key.DirectionDown -> { moveTo(selectedIndex + 1); true }
                        Key.DirectionUp -> { moveTo(selectedIndex - 1); true }
                        Key.ButtonA, Key.Enter -> {
                            downloads.getOrNull(selectedIndex)?.let { primaryAction(it) }
                            true
                        }
                        // B: pause the running download (the selected one
                        // if it is running, else the first that is).  With
                        // nothing running it is unclaimed — and the
                        // Activity swallows it, so B never exits.
                        Key.ButtonB -> {
                            val running = downloads.getOrNull(selectedIndex)
                                ?.takeIf { it.status == DownloadEntity.Status.DOWNLOADING }
                                ?: downloads.firstOrNull { it.status == DownloadEntity.Status.DOWNLOADING }
                            if (running != null) {
                                viewModel.pauseDownload(running.id)
                                true
                            } else false
                        }
                        Key.ButtonX -> {
                            if (finishedCount > 0) viewModel.clearFinishedDownloads()
                            true
                        }
                        Key.ButtonY -> {
                            removeTarget = downloads.getOrNull(selectedIndex)
                            true
                        }
                        else -> false
                    }
                }
        ) {
            if (downloads.isEmpty()) {
                EmptyDownloads()
            } else {
                LazyColumn(
                    state = listState,
                    modifier = Modifier.fillMaxSize(),
                    contentPadding = PaddingValues(horizontal = 12.dp, vertical = 8.dp),
                    verticalArrangement = Arrangement.spacedBy(4.dp),
                ) {
                    if (activeCount > 0) {
                        item {
                            Text(
                                "$activeCount active · $finishedCount finished",
                                style = MaterialTheme.typography.labelSmall,
                                color = GsColors.Dim,
                                modifier = Modifier.padding(horizontal = 4.dp, vertical = 4.dp),
                            )
                        }
                    }
                    itemsIndexed(downloads, key = { _, d -> d.id }) { index, download ->
                        DownloadRow(
                            download = download,
                            liveProgress = liveProgress[download.id],
                            isSelected = index == selectedIndex,
                            onSelect = { selectedIndex = index },
                            onPause = { viewModel.pauseDownload(download.id) },
                            onResume = { viewModel.resumeDownload(download.id) },
                            onCancel = { viewModel.cancelDownload(download.id) },
                            onRemove = { viewModel.removeDownload(download.id) },
                            onRetry = { viewModel.resumeDownload(download.id) },
                        )
                    }
                }
            }
        }
    }

    removeTarget?.let { target ->
        val terminal = target.isTerminal
        GsConfirmDialog(
            title = if (terminal) "Remove from list?" else "Cancel download?",
            message = if (terminal) {
                "${target.displayName} — the row goes; a downloaded file stays where it is."
            } else {
                "${target.displayName} — the partial file is deleted."
            },
            confirmLabel = if (terminal) "Remove" else "Cancel download",
            dismissLabel = "Keep",
            destructive = !terminal,
            onConfirm = {
                if (terminal) viewModel.removeDownload(target.id) else viewModel.cancelDownload(target.id)
                removeTarget = null
            },
            onDismiss = { removeTarget = null },
        )
    }
}

@Composable
private fun EmptyDownloads() {
    Box(
        modifier = Modifier
            .fillMaxSize()
            .padding(32.dp),
        contentAlignment = Alignment.Center,
    ) {
        Column(horizontalAlignment = Alignment.CenterHorizontally) {
            Icon(
                Icons.Filled.Download,
                contentDescription = null,
                modifier = Modifier.size(48.dp),
                tint = GsColors.Muted,
            )
            Spacer(Modifier.height(12.dp))
            Text(
                "No downloads yet",
                style = MaterialTheme.typography.titleMedium,
                color = GsColors.Text,
            )
            Spacer(Modifier.height(4.dp))
            Text(
                "Pick a ROM from the Catalog tab — its progress will show up here.",
                style = MaterialTheme.typography.bodyMedium,
                color = GsColors.Dim,
            )
        }
    }
}

/** Progress card: name, status pill, bar, then bytes · speed · ETA. */
@Composable
private fun DownloadRow(
    download: DownloadEntity,
    liveProgress: DownloadManager.ProgressEvent?,
    isSelected: Boolean,
    onSelect: () -> Unit,
    onPause: () -> Unit,
    onResume: () -> Unit,
    onCancel: () -> Unit,
    onRemove: () -> Unit,
    onRetry: () -> Unit,
) {
    // Prefer the live event for active rows; otherwise fall back to what's
    // persisted in the DB (still correct for paused / completed / failed).
    val downloaded = liveProgress?.downloadedBytes ?: download.downloadedBytes
    val total = liveProgress?.totalBytes?.takeIf { it > 0 } ?: download.totalBytes
    val fraction: Float? = if (total > 0L) {
        (downloaded.toDouble() / total.toDouble()).toFloat().coerceIn(0f, 1f)
    } else null

    GsListRow(isSelected = isSelected, onClick = onSelect) {
        Column(modifier = Modifier.weight(1f)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Column(modifier = Modifier.weight(1f)) {
                    Text(
                        download.displayName,
                        fontWeight = FontWeight.SemiBold,
                        color = GsColors.Text,
                        maxLines = 1,
                    )
                    Text(
                        text = "${download.system} · ${download.filename}",
                        style = MaterialTheme.typography.bodySmall,
                        color = GsColors.Dim,
                        maxLines = 1,
                    )
                }
                Spacer(Modifier.size(8.dp))
                StatusPill(download.status)
                StatusActions(
                    status = download.status,
                    onPause = onPause,
                    onResume = onResume,
                    onCancel = onCancel,
                    onRemove = onRemove,
                    onRetry = onRetry,
                )
            }

            Spacer(Modifier.height(6.dp))

            if (fraction != null) {
                // Lambda form is the supported overload in Material 3 1.2+
                // — the older Float form is deprecated and gone in 1.3.
                LinearProgressIndicator(
                    progress = { fraction },
                    modifier = Modifier.fillMaxWidth(),
                    color = statusColor(download.status),
                    trackColor = GsColors.Line,
                )
            } else if (download.status == DownloadEntity.Status.DOWNLOADING ||
                download.status == DownloadEntity.Status.QUEUED
            ) {
                LinearProgressIndicator(
                    modifier = Modifier.fillMaxWidth(),
                    color = GsColors.Accent,
                    trackColor = GsColors.Line,
                )
            }

            Spacer(Modifier.height(4.dp))
            Text(
                text = buildSubtitle(download, downloaded, total, liveProgress?.bytesPerSecond),
                style = MaterialTheme.typography.bodySmall,
                color = GsColors.Dim,
            )

            val error = download.errorMessage
            if (!error.isNullOrBlank() && download.status == DownloadEntity.Status.FAILED) {
                Spacer(Modifier.height(4.dp))
                Text(
                    text = error,
                    style = MaterialTheme.typography.bodySmall,
                    color = GsColors.Err,
                )
            }
        }
    }
}

private fun statusColor(status: String): Color = when (status) {
    DownloadEntity.Status.COMPLETED -> GsColors.Ok
    DownloadEntity.Status.FAILED -> GsColors.Err
    DownloadEntity.Status.PAUSED -> GsColors.Warn
    DownloadEntity.Status.CANCELLED -> GsColors.Muted
    DownloadEntity.Status.QUEUED -> GsColors.Dim
    else -> GsColors.Info
}

@Composable
private fun StatusPill(status: String) {
    val label = when (status) {
        DownloadEntity.Status.COMPLETED -> "Done"
        DownloadEntity.Status.FAILED -> "Failed"
        DownloadEntity.Status.PAUSED -> "Paused"
        DownloadEntity.Status.CANCELLED -> "Cancelled"
        DownloadEntity.Status.QUEUED -> "Queued"
        else -> "Downloading"
    }
    GsPill(text = label, color = statusColor(status))
}

@Composable
private fun StatusActions(
    status: String,
    onPause: () -> Unit,
    onResume: () -> Unit,
    onCancel: () -> Unit,
    onRemove: () -> Unit,
    onRetry: () -> Unit,
) {
    Row(
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(0.dp),
    ) {
        when (status) {
            DownloadEntity.Status.DOWNLOADING,
            DownloadEntity.Status.QUEUED,
            -> {
                IconButton(onClick = onPause) {
                    Icon(Icons.Filled.Pause, contentDescription = "Pause", tint = GsColors.Text)
                }
                IconButton(onClick = onCancel) {
                    Icon(Icons.Filled.Cancel, contentDescription = "Cancel", tint = GsColors.Dim)
                }
            }
            DownloadEntity.Status.PAUSED -> {
                IconButton(onClick = onResume) {
                    Icon(Icons.Filled.PlayArrow, contentDescription = "Resume", tint = GsColors.Accent)
                }
                IconButton(onClick = onCancel) {
                    Icon(Icons.Filled.Cancel, contentDescription = "Cancel", tint = GsColors.Dim)
                }
            }
            DownloadEntity.Status.FAILED,
            DownloadEntity.Status.CANCELLED,
            -> {
                IconButton(onClick = onRetry) {
                    Icon(Icons.Filled.Refresh, contentDescription = "Retry", tint = GsColors.Accent)
                }
                IconButton(onClick = onRemove) {
                    Icon(Icons.Filled.Delete, contentDescription = "Remove", tint = GsColors.Dim)
                }
            }
            DownloadEntity.Status.COMPLETED -> {
                IconButton(onClick = onRemove) {
                    Icon(Icons.Filled.Delete, contentDescription = "Remove from list", tint = GsColors.Dim)
                }
            }
        }
    }
}

private fun buildSubtitle(
    download: DownloadEntity,
    downloaded: Long,
    total: Long,
    bytesPerSecond: Long?,
): String {
    return when (download.status) {
        DownloadEntity.Status.COMPLETED -> "Done · ${formatBytes(downloaded)}"
        DownloadEntity.Status.PAUSED -> {
            if (total > 0L) {
                val pct = (downloaded.toDouble() / total.toDouble() * 100).toInt()
                "Paused · ${formatBytes(downloaded)} / ${formatBytes(total)} ($pct%)"
            } else {
                "Paused · ${formatBytes(downloaded)}"
            }
        }
        DownloadEntity.Status.FAILED -> "Failed · ${formatBytes(downloaded)} downloaded"
        DownloadEntity.Status.CANCELLED -> "Cancelled"
        DownloadEntity.Status.QUEUED -> "Queued"
        else -> {
            // Active download.  Three sub-states:
            //   1. Truly waiting for the server  →  "Connecting to server…"
            //      Only when neither the total nor any downloaded bytes
            //      are known yet. Server conversions (3DS / RVZ → ISO)
            //      can take 10+ seconds before the first chunk arrives.
            //   2. Bytes flowing but server didn't send Content-Length
            //      (shouldn't happen with our nginx config, but some
            //      proxies / chunked-encoding paths drop it)  →
            //      show "Downloading · X MB" so the user can see motion.
            //   3. Normal case with known total → percent + ETA.
            if (downloaded == 0L && total <= 0L) {
                return "Connecting to server…"
            }
            val parts = mutableListOf<String>()
            if (total > 0L) {
                val pct = (downloaded.toDouble() / total.toDouble() * 100).toInt()
                parts += "${formatBytes(downloaded)} / ${formatBytes(total)} ($pct%)"
            } else {
                // total unknown but bytes are flowing — keep the user informed
                parts += "Downloading · ${formatBytes(downloaded)}"
            }
            if (bytesPerSecond != null && bytesPerSecond > 0) {
                parts += "${formatBytes(bytesPerSecond)}/s"
                if (total > 0L && bytesPerSecond > 0L) {
                    val remaining = total - downloaded
                    if (remaining > 0L) {
                        val etaSec = remaining / bytesPerSecond
                        parts += "ETA ${formatDuration(etaSec)}"
                    }
                }
            }
            parts.joinToString(" · ")
        }
    }
}

private fun formatDuration(seconds: Long): String {
    if (seconds < 60) return "${seconds}s"
    val minutes = seconds / 60
    val secs = seconds % 60
    if (minutes < 60) return "${minutes}m ${secs}s"
    val hours = minutes / 60
    val mins = minutes % 60
    return "${hours}h ${mins}m"
}
