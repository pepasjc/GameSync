package com.savesync.android.ui.components

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.LocalContentColor
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp

/** Stable ordering for the four top-level tabs, mirrored by MainActivity. */
val TAB_LABELS: List<String> = listOf("Saves", "Catalog", "Installed", "Downloads")

/**
 * Compact pill-style tab switcher designed to live inside a TopAppBar's
 * `title` slot. Replaces the separate bottom/top TabRow so landscape
 * layouts get a single row for navigation + actions.
 *
 * Uses [LocalContentColor] so the text + selection pill blend with the
 * surrounding AppBar (e.g. onPrimary for the primary-colored SavesScreen
 * bar, onSurface for the default-colored Catalog/Installed bars).
 */
@Composable
fun TabSwitchBar(
    activeTabIndex: Int,
    onTabClick: (Int) -> Unit,
    modifier: Modifier = Modifier,
) {
    val color = LocalContentColor.current
    Row(
        modifier = modifier,
        horizontalArrangement = Arrangement.spacedBy(4.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        TAB_LABELS.forEachIndexed { idx, label ->
            val selected = idx == activeTabIndex
            Box(
                modifier = Modifier
                    .clip(RoundedCornerShape(16.dp))
                    .background(
                        if (selected) color.copy(alpha = 0.22f) else Color.Transparent
                    )
                    .clickable { onTabClick(idx) }
                    .padding(horizontal = 12.dp, vertical = 6.dp),
            ) {
                Text(
                    text = label,
                    style = MaterialTheme.typography.titleSmall,
                    fontWeight = if (selected) FontWeight.Bold else FontWeight.Medium,
                    color = color,
                )
            }
        }
    }
}
