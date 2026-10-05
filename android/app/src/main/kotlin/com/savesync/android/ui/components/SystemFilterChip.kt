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
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.width
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import com.savesync.android.ui.theme.GsColors

/**
 * Compact system-filter chip for the header's sub-tab row ([GsTopBar]). Intentionally convention-agnostic: the
 * caller supplies the label to display and the list of options to pop up
 * in the dropdown, and gets the chosen label back via [onSelect]. That
 * lets Saves (which uses the string `"All"` as a sentinel) and
 * Catalog/Installed (which use `null`) both target the same chip without
 * leaking their internal sentinels into shared code.
 *
 * Drawn as the active sub-tab chip of the header's chip row (SELECT steps
 * through the options; tap opens the full list).
 */
@Composable
fun SystemFilterChip(
    label: String,
    options: List<String>,
    onSelect: (String) -> Unit,
    modifier: Modifier = Modifier,
) {
    var expanded by remember { mutableStateOf(false) }
    val color = GsColors.Text
    Box(modifier = modifier) {
        Row(
            modifier = Modifier
                .clip(RoundedCornerShape(16.dp))
                .background(GsColors.PanelHi)
                .border(
                    width = 1.dp,
                    color = GsColors.Accent.copy(alpha = 0.7f),
                    shape = RoundedCornerShape(16.dp),
                )
                .clickable { expanded = true }
                .padding(start = 8.dp, end = 6.dp, top = 4.dp, bottom = 4.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(2.dp),
        ) {
            if (LocalShowButtonHints.current) {
                ButtonGlyph(GsButton.SELECT)
                Spacer(Modifier.width(4.dp))
            }
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
                tint = GsColors.Accent,
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
