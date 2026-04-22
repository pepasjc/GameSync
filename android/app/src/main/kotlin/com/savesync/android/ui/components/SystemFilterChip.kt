package com.savesync.android.ui.components

import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.ArrowDropDown
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.Icon
import androidx.compose.material3.LocalContentColor
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp

/**
 * Compact system-filter chip designed to live inside a TopAppBar's `title`
 * slot alongside [TabSwitchBar]. Intentionally convention-agnostic: the
 * caller supplies the label to display and the list of options to pop up
 * in the dropdown, and gets the chosen label back via [onSelect]. That
 * lets Saves (which uses the string `"All"` as a sentinel) and
 * Catalog/Installed (which use `null`) both target the same chip without
 * leaking their internal sentinels into shared code.
 *
 * Uses [LocalContentColor] so the border + text blend with the
 * surrounding AppBar (onPrimary for SavesScreen's primary-tinted bar,
 * onSurface elsewhere).
 */
@Composable
fun SystemFilterChip(
    label: String,
    options: List<String>,
    onSelect: (String) -> Unit,
    modifier: Modifier = Modifier,
) {
    var expanded by remember { mutableStateOf(false) }
    val color = LocalContentColor.current
    Box(modifier = modifier) {
        Row(
            modifier = Modifier
                .clip(RoundedCornerShape(16.dp))
                .border(
                    width = 1.dp,
                    color = color.copy(alpha = 0.5f),
                    shape = RoundedCornerShape(16.dp),
                )
                .clickable { expanded = true }
                .padding(start = 12.dp, end = 6.dp, top = 6.dp, bottom = 6.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(2.dp),
        ) {
            Text(
                text = label,
                style = MaterialTheme.typography.labelLarge,
                color = color,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
            )
            Icon(
                Icons.Filled.ArrowDropDown,
                contentDescription = "Choose system",
                tint = color,
                modifier = Modifier.size(20.dp),
            )
        }
        DropdownMenu(
            expanded = expanded,
            onDismissRequest = { expanded = false },
        ) {
            options.forEach { option ->
                DropdownMenuItem(
                    text = {
                        Text(
                            option,
                            fontWeight = if (option == label) FontWeight.Bold else FontWeight.Normal,
                        )
                    },
                    onClick = {
                        onSelect(option)
                        expanded = false
                    },
                    leadingIcon = if (option == label) {
                        { Text("✓", fontWeight = FontWeight.Bold) }
                    } else null,
                )
            }
        }
    }
}
