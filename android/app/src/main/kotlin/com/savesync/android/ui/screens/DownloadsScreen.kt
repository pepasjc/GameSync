package com.savesync.android.ui.screens

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
import androidx.compose.material.icons.filled.Cancel
import androidx.compose.material.icons.filled.CheckCircle
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material.icons.filled.Download
import androidx.compose.material.icons.filled.Error
import androidx.compose.material.icons.filled.Pause
import androidx.compose.material.icons.filled.PlayArrow
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateMapOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.savesync.android.storage.DownloadEntity
import com.savesync.android.sync.DownloadManager
import com.savesync.android.ui.MainViewModel
import com.savesync.android.ui.components.TabSwitchBar

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

    Scaffold(
        topBar = {
            TopAppBar(
                title = {
                    Row(
                        verticalAlignment = Alignment.CenterVertically,
                        horizontalArrangement = Arrangement.spacedBy(10.dp),
                    ) {
                        TabSwitchBar(
                            activeTabIndex = 3,
                            onTabClick = onNavigateToTab,
                        )
                    }
                },
                actions = {
                    if (finishedCount > 0) {
                        TextButton(onClick = { viewModel.clearFinishedDownloads() }) {
                            Text("Clear finished")
                        }
                    }
                },
            )
        },
    ) { padding ->
        if (downloads.isEmpty()) {
            EmptyDownloads(padding)
        } else {
            LazyColumn(
                modifier = Modifier
                    .fillMaxSize()
                    .padding(padding)
                    .padding(horizontal = 12.dp, vertical = 8.dp),
                verticalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                if (activeCount > 0) {
                    item {
                        Text(
                            "$activeCount active · $finishedCount finished",
                            style = MaterialTheme.typography.labelSmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                            modifier = Modifier.padding(horizontal = 4.dp, vertical = 4.dp),
                        )
                    }
                }
                items(downloads, key = { it.id }) { download ->
                    DownloadRow(
                        download = download,
                        liveProgress = liveProgress[download.id],
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

@Composable
private fun EmptyDownloads(padding: PaddingValues) {
    Box(
        modifier = Modifier
            .fillMaxSize()
            .padding(padding)
            .padding(32.dp),
        contentAlignment = Alignment.Center,
    ) {
        Column(horizontalAlignment = Alignment.CenterHorizontally) {
            Icon(
                Icons.Filled.Download,
                contentDescription = null,
                modifier = Modifier.size(48.dp),
                tint = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            Spacer(Modifier.height(12.dp))
            Text(
                "No downloads yet",
                style = MaterialTheme.typography.titleMedium,
            )
            Spacer(Modifier.height(4.dp))
            Text(
                "Pick a ROM from the Catalog tab — its progress will show up here.",
                style = MaterialTheme.typography.bodyMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
    }
}

@Composable
private fun DownloadRow(
    download: DownloadEntity,
    liveProgress: DownloadManager.ProgressEvent?,
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

    Card(
        modifier = Modifier.fillMaxWidth(),
        elevation = CardDefaults.cardElevation(defaultElevation = 2.dp),
    ) {
        Column(modifier = Modifier.padding(12.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                StatusIcon(download.status)
                Spacer(Modifier.size(8.dp))
                Column(modifier = Modifier.weight(1f)) {
                    Text(
                        download.displayName,
                        fontWeight = FontWeight.SemiBold,
                        maxLines = 1,
                    )
                    Text(
                        text = "${download.system} · ${download.filename}",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                        maxLines = 1,
                    )
                }
                StatusActions(
                    status = download.status,
                    onPause = onPause,
                    onResume = onResume,
                    onCancel = onCancel,
                    onRemove = onRemove,
                    onRetry = onRetry,
                )
            }

            Spacer(Modifier.height(8.dp))

            if (fraction != null) {
                // Lambda form is the supported overload in Material 3 1.2+
                // — the older Float form is deprecated and gone in 1.3.
                LinearProgressIndicator(
                    progress = { fraction },
                    modifier = Modifier.fillMaxWidth(),
                )
            } else if (download.status == DownloadEntity.Status.DOWNLOADING ||
                download.status == DownloadEntity.Status.QUEUED
            ) {
                LinearProgressIndicator(modifier = Modifier.fillMaxWidth())
            }

            Spacer(Modifier.height(6.dp))
            Text(
                text = buildSubtitle(download, downloaded, total, liveProgress?.bytesPerSecond),
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )

            val error = download.errorMessage
            if (!error.isNullOrBlank() && download.status == DownloadEntity.Status.FAILED) {
                Spacer(Modifier.height(4.dp))
                Text(
                    text = error,
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.error,
                )
            }
        }
    }
}

@Composable
private fun StatusIcon(status: String) {
    when (status) {
        DownloadEntity.Status.COMPLETED -> Icon(
            Icons.Filled.CheckCircle,
            contentDescription = "Completed",
            tint = Color(0xFF2E7D32),
        )
        DownloadEntity.Status.FAILED -> Icon(
            Icons.Filled.Error,
            contentDescription = "Failed",
            tint = MaterialTheme.colorScheme.error,
        )
        DownloadEntity.Status.PAUSED -> Icon(
            Icons.Filled.Pause,
            contentDescription = "Paused",
            tint = MaterialTheme.colorScheme.onSurfaceVariant,
        )
        DownloadEntity.Status.CANCELLED -> Icon(
            Icons.Filled.Cancel,
            contentDescription = "Cancelled",
            tint = MaterialTheme.colorScheme.onSurfaceVariant,
        )
        else -> Icon(
            Icons.Filled.Download,
            contentDescription = "Downloading",
            tint = MaterialTheme.colorScheme.primary,
        )
    }
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
                    Icon(Icons.Filled.Pause, contentDescription = "Pause")
                }
                IconButton(onClick = onCancel) {
                    Icon(Icons.Filled.Cancel, contentDescription = "Cancel")
                }
            }
            DownloadEntity.Status.PAUSED -> {
                IconButton(onClick = onResume) {
                    Icon(Icons.Filled.PlayArrow, contentDescription = "Resume")
                }
                IconButton(onClick = onCancel) {
                    Icon(Icons.Filled.Cancel, contentDescription = "Cancel")
                }
            }
            DownloadEntity.Status.FAILED,
            DownloadEntity.Status.CANCELLED,
            -> {
                IconButton(onClick = onRetry) {
                    Icon(Icons.Filled.Refresh, contentDescription = "Retry")
                }
                IconButton(onClick = onRemove) {
                    Icon(Icons.Filled.Delete, contentDescription = "Remove")
                }
            }
            DownloadEntity.Status.COMPLETED -> {
                IconButton(onClick = onRemove) {
                    Icon(Icons.Filled.Delete, contentDescription = "Remove from list")
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
