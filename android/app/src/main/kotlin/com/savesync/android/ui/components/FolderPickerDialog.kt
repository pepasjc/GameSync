package com.savesync.android.ui.components

import android.os.Environment
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.CheckCircle
import androidx.compose.material.icons.filled.Folder
import androidx.compose.material.icons.filled.KeyboardArrowUp
import androidx.compose.material.icons.filled.Storage
import androidx.compose.material3.Button
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.window.Dialog
import androidx.compose.ui.window.DialogProperties
import java.io.File

@Composable
fun FolderPickerDialog(
    /** Pre-selected path to open in (blank = external storage root) */
    initialPath: String = "",
    onDismiss: () -> Unit,
    onFolderSelected: (String) -> Unit
) {
    val startDir = remember(initialPath) {
        when {
            initialPath.isNotBlank() -> {
                val f = File(initialPath)
                if (f.exists() && f.isDirectory) f else Environment.getExternalStorageDirectory()
            }
            else -> Environment.getExternalStorageDirectory()
        }
    }

    var currentDir by remember { mutableStateOf(startDir) }

    val subDirs = remember(currentDir) {
        currentDir.listFiles()
            ?.filter { it.isDirectory && !it.name.startsWith(".") }
            ?.sortedBy { it.name.lowercase() }
            ?: emptyList()
    }

    // Discover available storage volumes (/storage/emulated/0 + any SD cards)
    val storageRoots = remember {
        buildList {
            add(Environment.getExternalStorageDirectory())
            try {
                File("/storage").listFiles()
                    ?.filter { it.isDirectory && it.name != "emulated" && it.name != "self" }
                    ?.forEach { add(it) }
            } catch (_: Exception) {}
        }.distinctBy { it.absolutePath }
    }

    Dialog(
        onDismissRequest = onDismiss,
        properties = DialogProperties(usePlatformDefaultWidth = false)
    ) {
        Surface(
            modifier = Modifier
                .fillMaxWidth()
                .padding(horizontal = 16.dp, vertical = 32.dp),
            shape = RoundedCornerShape(16.dp),
            color = MaterialTheme.colorScheme.surface,
            tonalElevation = 6.dp
        ) {
            // Use Column with weight so the list is flexible and buttons always stay at bottom
            Column(
                modifier = Modifier
                    .fillMaxWidth()
                    .heightIn(max = 560.dp)
                    .padding(horizontal = 16.dp, vertical = 16.dp)
            ) {

                // ── Title ───────────────────────────────────────────────
                Text("Select Folder", style = MaterialTheme.typography.titleLarge)
                Spacer(Modifier.size(4.dp))

                // ── Current path ─────────────────────────────────────────
                Text(
                    text = currentDir.absolutePath,
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.primary,
                    maxLines = 2,
                    overflow = TextOverflow.Ellipsis
                )
                Spacer(Modifier.size(6.dp))
                HorizontalDivider()

                // ── Storage shortcut buttons (when multiple volumes found) ─
                if (storageRoots.size > 1) {
                    Row(modifier = Modifier.padding(vertical = 4.dp)) {
                        storageRoots.forEach { root ->
                            val label = if (root.absolutePath ==
                                Environment.getExternalStorageDirectory().absolutePath)
                                "Internal" else root.name
                            OutlinedButton(
                                onClick = { currentDir = root },
                                modifier = Modifier.padding(end = 8.dp)
                            ) {
                                Icon(
                                    Icons.Default.Storage,
                                    contentDescription = null,
                                    modifier = Modifier.size(14.dp)
                                )
                                Spacer(Modifier.width(4.dp))
                                Text(label, style = MaterialTheme.typography.labelSmall)
                            }
                        }
                    }
                    HorizontalDivider()
                }

                // ── Directory listing — fills all remaining space ─────────
                LazyColumn(
                    modifier = Modifier
                        .fillMaxWidth()
                        .weight(1f)   // ← takes all leftover space; buttons stay below
                ) {
                    // Go up row
                    val parent = currentDir.parentFile
                    if (parent != null && parent.canRead()) {
                        item {
                            FolderRow(
                                icon = Icons.Default.KeyboardArrowUp,
                                name = "..",
                                onClick = { currentDir = parent }
                            )
                        }
                    }

                    if (subDirs.isEmpty()) {
                        item {
                            Text(
                                "(empty folder)",
                                style = MaterialTheme.typography.bodySmall,
                                color = MaterialTheme.colorScheme.onSurfaceVariant,
                                modifier = Modifier.padding(12.dp)
                            )
                        }
                    } else {
                        items(subDirs, key = { it.absolutePath }) { dir ->
                            FolderRow(
                                icon = Icons.Default.Folder,
                                name = dir.name,
                                onClick = { currentDir = dir }
                            )
                        }
                    }
                }

                // ── Action buttons — always visible at the bottom ─────────
                HorizontalDivider()
                Spacer(Modifier.size(10.dp))
                Row(modifier = Modifier.fillMaxWidth()) {
                    OutlinedButton(
                        onClick = onDismiss,
                        modifier = Modifier.weight(1f)
                    ) {
                        Text("Cancel")
                    }
                    Spacer(Modifier.width(12.dp))
                    Button(
                        onClick = { onFolderSelected(currentDir.absolutePath) },
                        modifier = Modifier.weight(2f)
                    ) {
                        Icon(
                            Icons.Default.CheckCircle,
                            contentDescription = null,
                            modifier = Modifier.size(16.dp)
                        )
                        Spacer(Modifier.width(6.dp))
                        Text(
                            text = "Select \"${currentDir.name}\"",
                            maxLines = 1,
                            overflow = TextOverflow.Ellipsis
                        )
                    }
                }
            }
        }
    }
}

@Composable
private fun FolderRow(
    icon: androidx.compose.ui.graphics.vector.ImageVector,
    name: String,
    onClick: () -> Unit
) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .clickable(onClick = onClick)
            .padding(horizontal = 4.dp, vertical = 12.dp),
        verticalAlignment = Alignment.CenterVertically
    ) {
        Icon(
            imageVector = icon,
            contentDescription = null,
            tint = MaterialTheme.colorScheme.primary,
            modifier = Modifier.size(20.dp)
        )
        Spacer(Modifier.width(10.dp))
        Text(
            text = name,
            style = MaterialTheme.typography.bodyMedium,
            fontWeight = FontWeight.Medium,
            maxLines = 1,
            overflow = TextOverflow.Ellipsis
        )
    }
}
