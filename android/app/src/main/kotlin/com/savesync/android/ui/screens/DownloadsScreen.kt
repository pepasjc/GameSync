package com.savesync.android.ui.screens

import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.tween
import androidx.compose.foundation.ExperimentalFoundationApi
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
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.RoundedCornerShape
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
import androidx.compose.material3.Surface
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
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import com.savesync.android.storage.DownloadEntity
import com.savesync.android.sync.DownloadManager
import com.savesync.android.sync.MAX_PARALLEL_DOWNLOADS
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
 * Layout: an overall summary card while anything is in progress, then an
 * "In progress" section in queue order (downloading, waiting for one of
 * the [MAX_PARALLEL_DOWNLOADS] slots, or paused) and a "Finished" section,
 * newest first.  Rows never reorder on progress — only a status change
 * moves a row, and it animates to its new place.
 *
 * Controller: a row cursor (D-pad) that follows its download as rows
 * move, A = pause / resume / retry the selected row, B = pause the running
 * download, X = clear finished, Y = cancel (active) or remove (finished)
 * the selected row, after asking.
 *
 * The progress bar is driven by the live ProgressEvents flow (≈5 Hz)
 * with a fallback to the persisted Room row when no event is current —
 * so a paused download still shows the right percentage.
 */
@OptIn(ExperimentalMaterial3Api::class, ExperimentalFoundationApi::class)
@Composable
fun DownloadsScreen(
    viewModel: MainViewModel,
    onNavigateToTab: (Int) -> Unit = {},
) {
    val downloads by viewModel.downloads.collectAsState()

    // Live { id → most-recent ProgressEvent } map, plus a smoothed speed
    // per download: raw 200 ms samples swing wildly, which made the ETA
    // jump around.  mutableStateMapOf only invalidates the changed entry.
    val liveProgress = remember { mutableStateMapOf<String, DownloadManager.ProgressEvent>() }
    val smoothedSpeed = remember { mutableStateMapOf<String, Long>() }
    LaunchedEffect(Unit) {
        viewModel.downloadProgressEvents.collect { event ->
            liveProgress[event.id] = event
            if (event.bytesPerSecond > 0) {
                val previous = smoothedSpeed[event.id]
                smoothedSpeed[event.id] = if (previous == null || previous <= 0) {
                    event.bytesPerSecond
                } else {
                    (previous * 0.8 + event.bytesPerSecond * 0.2).toLong()
                }
            }
        }
    }

    // When a download flips to COMPLETED, ask the rest of the app to
    // refresh so the new file shows up in Saves / Installed Games.
    val previousStatuses = remember { mutableStateOf<Map<String, String>>(emptyMap()) }
    LaunchedEffect(downloads) {
        val prev = previousStatuses.value
        val anyCompleted = downloads.any {
            it.status == DownloadEntity.Status.COMPLETED &&
                prev[it.id] != DownloadEntity.Status.COMPLETED
        }
        if (anyCompleted) viewModel.onDownloadCompleted()
        previousStatuses.value = downloads.associate { it.id to it.status }
        // Speed only means something while bytes flow.
        downloads.filter { it.status != DownloadEntity.Status.DOWNLOADING }
            .forEach { smoothedSpeed.remove(it.id) }
    }

    // ── Grouping: stable order, never by last-update time ───────────────
    val inProgress = downloads.filter { !it.isTerminal }.sortedBy { it.createdAt }
    val finished = downloads.filter { it.isTerminal }.sortedByDescending { it.updatedAt }
    val ordered = inProgress + finished
    val runningCount = inProgress.count { it.status == DownloadEntity.Status.DOWNLOADING }
    val waitingCount = inProgress.count { it.status == DownloadEntity.Status.QUEUED }
    val pausedCount = inProgress.count { it.status == DownloadEntity.Status.PAUSED }
    val queuePositions: Map<String, Int> = inProgress
        .filter { it.status == DownloadEntity.Status.QUEUED }
        .mapIndexed { i, d -> d.id to i + 1 }
        .toMap()

    // LazyColumn layout: summary, section headers and rows, each keyed.
    val listKeys: List<String> = buildList {
        if (inProgress.isNotEmpty()) {
            add(KEY_SUMMARY)
            add(KEY_ACTIVE_HEADER)
            inProgress.forEach { add(it.id) }
        }
        if (finished.isNotEmpty()) {
            add(KEY_FINISHED_HEADER)
            finished.forEach { add(it.id) }
        }
    }

    // ── Gamepad cursor: tracks a download id, not a position ────────────
    var selectedId by remember { mutableStateOf<String?>(null) }
    var lastSelectedIndex by remember { mutableIntStateOf(0) }
    val listState = rememberLazyListState()
    val scope = rememberCoroutineScope()
    val holdNav = rememberHoldNavState()
    val listFocusRequester = remember { FocusRequester() }
    // Y asks before cancelling / removing the selected row.
    var removeTarget by remember { mutableStateOf<DownloadEntity?>(null) }

    // If the selected row went away (removed / cleared), stay at the same
    // position rather than jumping to the top.
    val selectedIndex = ordered.indexOfFirst { it.id == selectedId }
        .takeIf { it >= 0 }
        ?: lastSelectedIndex.coerceIn(0, (ordered.size - 1).coerceAtLeast(0))
    val selected = ordered.getOrNull(selectedIndex)
    LaunchedEffect(selected?.id) {
        selectedId = selected?.id
        lastSelectedIndex = selectedIndex
    }
    LaunchedEffect(Unit) { runCatching { listFocusRequester.requestFocus() } }

    fun moveTo(index: Int) {
        if (ordered.isEmpty()) return
        val target = ordered[index.coerceIn(0, ordered.size - 1)]
        selectedId = target.id
        lastSelectedIndex = ordered.indexOf(target)
        val listIndex = listKeys.indexOf(target.id)
        if (listIndex >= 0) scope.launch { listState.animateScrollToItem(listIndex) }
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
                    if (runningCount + waitingCount > 0) {
                        IconButton(onClick = { viewModel.pauseAllDownloads() }) {
                            Icon(Icons.Filled.Pause, contentDescription = "Pause all", tint = GsColors.Text)
                        }
                    }
                    if (pausedCount > 0) {
                        IconButton(onClick = { viewModel.resumeAllDownloads() }) {
                            Icon(Icons.Filled.PlayArrow, contentDescription = "Resume all", tint = GsColors.Accent)
                        }
                    }
                    if (finished.isNotEmpty()) {
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
                    if (runningCount > 0) add(GsHint(GsButton.B, "Pause running"))
                    if (finished.isNotEmpty()) add(GsHint(GsButton.X, "Clear finished"))
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
                            selected?.let { primaryAction(it) }
                            true
                        }
                        // B: pause the running download (the selected one
                        // if it is running, else the first that is).  With
                        // nothing running it is unclaimed — and the
                        // Activity swallows it, so B never exits.
                        Key.ButtonB -> {
                            val running = selected
                                ?.takeIf { it.status == DownloadEntity.Status.DOWNLOADING }
                                ?: inProgress.firstOrNull { it.status == DownloadEntity.Status.DOWNLOADING }
                            if (running != null) {
                                viewModel.pauseDownload(running.id)
                                true
                            } else false
                        }
                        Key.ButtonX -> {
                            if (finished.isNotEmpty()) viewModel.clearFinishedDownloads()
                            true
                        }
                        Key.ButtonY -> {
                            removeTarget = selected
                            true
                        }
                        else -> false
                    }
                }
        ) {
            if (ordered.isEmpty()) {
                EmptyDownloads()
            } else {
                LazyColumn(
                    state = listState,
                    modifier = Modifier.fillMaxSize(),
                    contentPadding = PaddingValues(horizontal = 12.dp, vertical = 8.dp),
                    verticalArrangement = Arrangement.spacedBy(4.dp),
                ) {
                    if (inProgress.isNotEmpty()) {
                        item(key = KEY_SUMMARY) {
                            SummaryCard(
                                inProgress = inProgress,
                                liveProgress = liveProgress,
                                smoothedSpeed = smoothedSpeed,
                                runningCount = runningCount,
                                waitingCount = waitingCount,
                                pausedCount = pausedCount,
                            )
                        }
                        item(key = KEY_ACTIVE_HEADER) {
                            SectionHeader("In progress", inProgress.size)
                        }
                        items(inProgress, key = { it.id }) { download ->
                            DownloadRow(
                                download = download,
                                liveProgress = liveProgress[download.id],
                                speed = smoothedSpeed[download.id],
                                queuePosition = queuePositions[download.id],
                                isSelected = download.id == selected?.id,
                                onSelect = { selectedId = download.id },
                                onPause = { viewModel.pauseDownload(download.id) },
                                onResume = { viewModel.resumeDownload(download.id) },
                                onCancel = { removeTarget = download },
                                onRemove = { viewModel.removeDownload(download.id) },
                                modifier = Modifier.animateItemPlacement(),
                            )
                        }
                    }
                    if (finished.isNotEmpty()) {
                        item(key = KEY_FINISHED_HEADER) {
                            SectionHeader("Finished", finished.size)
                        }
                        items(finished, key = { it.id }) { download ->
                            DownloadRow(
                                download = download,
                                liveProgress = liveProgress[download.id],
                                speed = null,
                                queuePosition = null,
                                isSelected = download.id == selected?.id,
                                onSelect = { selectedId = download.id },
                                onPause = {},
                                onResume = { viewModel.resumeDownload(download.id) },
                                onCancel = {},
                                onRemove = { viewModel.removeDownload(download.id) },
                                modifier = Modifier.animateItemPlacement(),
                            )
                        }
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

private const val KEY_SUMMARY = "__summary"
private const val KEY_ACTIVE_HEADER = "__header_active"
private const val KEY_FINISHED_HEADER = "__header_finished"

/** Overall progress of everything not yet finished: counts, bytes, speed, ETA. */
@Composable
private fun SummaryCard(
    inProgress: List<DownloadEntity>,
    liveProgress: Map<String, DownloadManager.ProgressEvent>,
    smoothedSpeed: Map<String, Long>,
    runningCount: Int,
    waitingCount: Int,
    pausedCount: Int,
) {
    var done = 0L
    var total = 0L
    var unknownSize = false
    for (d in inProgress) {
        val live = liveProgress[d.id]
        val bytes = live?.downloadedBytes ?: d.downloadedBytes
        val size = live?.totalBytes?.takeIf { it > 0 } ?: d.totalBytes
        if (size > 0) {
            done += bytes.coerceAtMost(size)
            total += size
        } else {
            unknownSize = true
        }
    }
    val speed = inProgress
        .filter { it.status == DownloadEntity.Status.DOWNLOADING }
        .sumOf { smoothedSpeed[it.id] ?: 0L }
    val fraction = if (total > 0) (done.toDouble() / total).toFloat().coerceIn(0f, 1f) else null

    Surface(
        color = GsColors.Panel,
        shape = RoundedCornerShape(10.dp),
        modifier = Modifier
            .fillMaxWidth()
            .padding(bottom = 4.dp),
    ) {
        Column(Modifier.padding(horizontal = 14.dp, vertical = 12.dp)) {
            Text(
                buildList {
                    if (runningCount > 0) add("$runningCount downloading")
                    if (waitingCount > 0) add("$waitingCount waiting")
                    if (pausedCount > 0) add("$pausedCount paused")
                }.joinToString(" · "),
                color = GsColors.Text,
                fontWeight = FontWeight.SemiBold,
            )
            Spacer(Modifier.height(8.dp))
            SmoothProgressBar(fraction, GsColors.Accent, indeterminate = runningCount > 0)
            Spacer(Modifier.height(6.dp))
            Row(Modifier.fillMaxWidth()) {
                Text(
                    text = if (total > 0) {
                        val pct = (done * 100 / total).toInt()
                        "${sizeText(done)} of ${sizeText(total)}" +
                            (if (unknownSize) "+" else "") + " · $pct%"
                    } else "Waiting for sizes…",
                    style = MaterialTheme.typography.bodySmall,
                    color = GsColors.Dim,
                    modifier = Modifier.weight(1f),
                )
                if (speed > 0) {
                    val remaining = (total - done).coerceAtLeast(0)
                    Text(
                        text = "${sizeText(speed)}/s" +
                            if (remaining > 0 && !unknownSize) " · ${formatDuration(remaining / speed)} left" else "",
                        style = MaterialTheme.typography.bodySmall,
                        color = GsColors.Dim,
                    )
                }
            }
            if (waitingCount > 0) {
                Spacer(Modifier.height(4.dp))
                Text(
                    "$MAX_PARALLEL_DOWNLOADS download at a time; the rest start as slots free up.",
                    style = MaterialTheme.typography.labelSmall,
                    color = GsColors.Muted,
                )
            }
        }
    }
}

@Composable
private fun SectionHeader(title: String, count: Int) {
    Row(
        verticalAlignment = Alignment.CenterVertically,
        modifier = Modifier.padding(start = 4.dp, top = 8.dp, bottom = 2.dp),
    ) {
        Text(
            title.uppercase(),
            style = MaterialTheme.typography.labelMedium,
            color = GsColors.Dim,
            fontWeight = FontWeight.Bold,
        )
        Spacer(Modifier.size(6.dp))
        Text("$count", style = MaterialTheme.typography.labelMedium, color = GsColors.Muted)
    }
}

/** Progress bar that glides between the ~5 Hz samples instead of stepping. */
@Composable
private fun SmoothProgressBar(fraction: Float?, color: Color, indeterminate: Boolean) {
    if (fraction != null) {
        val animated by animateFloatAsState(
            targetValue = fraction,
            animationSpec = tween(durationMillis = 250),
            label = "download-progress",
        )
        LinearProgressIndicator(
            progress = { animated },
            modifier = Modifier.fillMaxWidth(),
            color = color,
            trackColor = GsColors.Line,
        )
    } else if (indeterminate) {
        LinearProgressIndicator(
            modifier = Modifier.fillMaxWidth(),
            color = color,
            trackColor = GsColors.Line,
        )
    } else {
        LinearProgressIndicator(
            progress = { 0f },
            modifier = Modifier.fillMaxWidth(),
            color = color,
            trackColor = GsColors.Line,
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

/** One download: name + status, system · file, bar, then size on the left and speed · ETA on the right. */
@Composable
private fun DownloadRow(
    download: DownloadEntity,
    liveProgress: DownloadManager.ProgressEvent?,
    speed: Long?,
    queuePosition: Int?,
    isSelected: Boolean,
    onSelect: () -> Unit,
    onPause: () -> Unit,
    onResume: () -> Unit,
    onCancel: () -> Unit,
    onRemove: () -> Unit,
    modifier: Modifier = Modifier,
) {
    // Prefer the live event for active rows; otherwise fall back to what's
    // persisted in the DB (still correct for paused / completed / failed).
    val downloaded = liveProgress?.downloadedBytes ?: download.downloadedBytes
    val total = liveProgress?.totalBytes?.takeIf { it > 0 } ?: download.totalBytes
    val fraction: Float? = when {
        download.status == DownloadEntity.Status.COMPLETED -> 1f
        total > 0L -> (downloaded.toDouble() / total.toDouble()).toFloat().coerceIn(0f, 1f)
        else -> null
    }
    val running = download.status == DownloadEntity.Status.DOWNLOADING

    GsListRow(isSelected = isSelected, onClick = onSelect, modifier = modifier) {
        Column(modifier = Modifier.weight(1f)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Column(modifier = Modifier.weight(1f)) {
                    Text(
                        download.displayName,
                        fontWeight = FontWeight.SemiBold,
                        color = GsColors.Text,
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis,
                    )
                    Text(
                        text = "${download.system} · ${download.filename}",
                        style = MaterialTheme.typography.bodySmall,
                        color = GsColors.Dim,
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis,
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
                )
            }

            // Finished rows stay compact: no bar, just the outcome line.
            if (!download.isTerminal) {
                Spacer(Modifier.height(6.dp))
                SmoothProgressBar(fraction, statusColor(download.status), indeterminate = running)
            }

            Spacer(Modifier.height(4.dp))
            Row(Modifier.fillMaxWidth()) {
                Text(
                    text = leftDetail(download, downloaded, total, queuePosition),
                    style = MaterialTheme.typography.bodySmall,
                    color = GsColors.Dim,
                    modifier = Modifier.weight(1f),
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                )
                if (running && speed != null && speed > 0) {
                    val remaining = if (total > 0) (total - downloaded).coerceAtLeast(0) else 0L
                    Text(
                        text = "${sizeText(speed)}/s" +
                            if (remaining > 0) " · ${formatDuration(remaining / speed)} left" else "",
                        style = MaterialTheme.typography.bodySmall,
                        color = GsColors.Dim,
                    )
                }
            }

            val error = download.errorMessage
            if (!error.isNullOrBlank() && download.status == DownloadEntity.Status.FAILED) {
                Spacer(Modifier.height(4.dp))
                Text(
                    text = error,
                    style = MaterialTheme.typography.bodySmall,
                    color = GsColors.Err,
                    maxLines = 2,
                    overflow = TextOverflow.Ellipsis,
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
        DownloadEntity.Status.QUEUED -> "Waiting"
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
                IconButton(onClick = onResume) {
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

/** Size / state text under the bar; speed and ETA sit on the right. */
private fun leftDetail(
    download: DownloadEntity,
    downloaded: Long,
    total: Long,
    queuePosition: Int?,
): String {
    fun progress(): String = if (total > 0L) {
        val pct = (downloaded.toDouble() / total.toDouble() * 100).toInt()
        "${sizeText(downloaded)} / ${sizeText(total)} · $pct%"
    } else sizeText(downloaded)

    return when (download.status) {
        DownloadEntity.Status.COMPLETED ->
            "Done · ${sizeText(if (total > 0) total else downloaded)}"
        DownloadEntity.Status.PAUSED -> "Paused · ${progress()}"
        DownloadEntity.Status.FAILED -> "Failed · ${sizeText(downloaded)} downloaded"
        DownloadEntity.Status.CANCELLED -> "Cancelled"
        DownloadEntity.Status.QUEUED -> buildString {
            append(if (queuePosition != null) "Waiting · #$queuePosition in line" else "Waiting")
            if (downloaded > 0) append(" · ${progress()}")
        }
        else -> when {
            // Server conversions (3DS / RVZ → ISO, CHD extraction) can take
            // 10+ seconds before the first byte arrives.
            downloaded == 0L && total <= 0L -> "Connecting to server…"
            // Bytes flowing without a Content-Length: show motion anyway.
            total <= 0L -> "Downloading · ${sizeText(downloaded)}"
            else -> progress()
        }
    }
}

/** [formatBytes] renders 0 as blank; a progress line wants "0 B". */
private fun sizeText(bytes: Long): String = if (bytes <= 0L) "0 B" else formatBytes(bytes)

private fun formatDuration(seconds: Long): String {
    if (seconds < 60) return "${seconds}s"
    val minutes = seconds / 60
    val secs = seconds % 60
    if (minutes < 60) return "${minutes}m ${secs}s"
    val hours = minutes / 60
    val mins = minutes % 60
    return "${hours}h ${mins}m"
}
