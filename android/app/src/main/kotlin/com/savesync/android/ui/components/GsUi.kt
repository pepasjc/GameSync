package com.savesync.android.ui.components

import android.content.Context
import android.content.res.Configuration
import android.hardware.input.InputManager
import android.view.InputDevice
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.focusable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.IntrinsicSize
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.RowScope
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.compositionLocalOf
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.composed
import androidx.compose.ui.draw.clip
import androidx.compose.ui.focus.onFocusChanged
import androidx.compose.ui.graphics.Shape
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.input.key.Key
import androidx.compose.ui.input.key.KeyEventType
import androidx.compose.ui.input.key.key
import androidx.compose.ui.input.key.onPreviewKeyEvent
import androidx.compose.ui.input.key.type
import androidx.compose.ui.platform.LocalConfiguration
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.window.Dialog
import com.savesync.android.ui.theme.GsColors

// ── Controller glyphs ─────────────────────────────────────────────────────

/** A controller input as drawn in footer hints and dialogs. */
enum class GsButton(val label: String, val color: Color?, val round: Boolean) {
    A("A", GsColors.BtnA, true),
    B("B", GsColors.BtnB, true),
    X("X", GsColors.BtnX, true),
    Y("Y", GsColors.BtnY, true),
    L1("L1", null, false),
    R1("R1", null, false),
    L2R2("L2/R2", null, false),
    SELECT("SELECT", null, false),
    START("START", null, false),
    DPAD("D-pad", null, false),
}

/** Coloured round A/B/X/Y glyph, or a grey rounded-rect shoulder/menu glyph. */
@Composable
fun ButtonGlyph(button: GsButton, modifier: Modifier = Modifier) {
    if (button.round) {
        Box(
            modifier = modifier
                .size(20.dp)
                .clip(CircleShape)
                .background(button.color ?: GsColors.PanelHi),
            contentAlignment = Alignment.Center,
        ) {
            Text(
                button.label,
                color = GsColors.Ink,
                fontSize = 11.sp,
                fontWeight = FontWeight.Bold,
            )
        }
    } else {
        Box(
            modifier = modifier
                .clip(RoundedCornerShape(5.dp))
                .background(GsColors.PanelHi)
                .border(1.dp, GsColors.Line, RoundedCornerShape(5.dp))
                .padding(horizontal = 6.dp, vertical = 1.dp),
            contentAlignment = Alignment.Center,
        ) {
            Text(
                button.label,
                color = GsColors.Dim,
                fontSize = 10.sp,
                fontWeight = FontWeight.Bold,
            )
        }
    }
}

data class GsHint(val button: GsButton, val label: String)

/**
 * Whether the controller footer is drawn.  Provided by MainApp: on when a
 * gamepad is attached (handhelds, Bluetooth pads) or the screen is in
 * landscape; off on a plain phone in portrait, where it would only waste
 * space under the user's thumb.
 */
val LocalShowButtonHints = compositionLocalOf { false }

/** True while any gamepad / joystick input device is attached. */
@Composable
fun rememberGamepadConnected(): Boolean {
    val context = LocalContext.current
    var connected by remember { mutableStateOf(hasGamepad()) }
    DisposableEffect(context) {
        val im = context.getSystemService(Context.INPUT_SERVICE) as? InputManager
        val listener = object : InputManager.InputDeviceListener {
            override fun onInputDeviceAdded(deviceId: Int) { connected = hasGamepad() }
            override fun onInputDeviceRemoved(deviceId: Int) { connected = hasGamepad() }
            override fun onInputDeviceChanged(deviceId: Int) { connected = hasGamepad() }
        }
        im?.registerInputDeviceListener(listener, null)
        connected = hasGamepad()
        onDispose { im?.unregisterInputDeviceListener(listener) }
    }
    return connected
}

/** Footer visibility rule — see [LocalShowButtonHints]. */
@Composable
fun rememberShowButtonHints(): Boolean {
    val gamepad = rememberGamepadConnected()
    val landscape = LocalConfiguration.current.orientation == Configuration.ORIENTATION_LANDSCAPE
    return gamepad || landscape
}

private fun hasGamepad(): Boolean = try {
    InputDevice.getDeviceIds().any { id ->
        val sources = InputDevice.getDevice(id)?.sources ?: 0
        (sources and InputDevice.SOURCE_GAMEPAD) == InputDevice.SOURCE_GAMEPAD ||
            (sources and InputDevice.SOURCE_JOYSTICK) == InputDevice.SOURCE_JOYSTICK
    }
} catch (_: Exception) {
    false
}

/**
 * The controller-hint footer: coloured A/B/X/Y glyphs plus a label each,
 * changing per tab.  Draws nothing when [LocalShowButtonHints] is off.
 */
@Composable
fun GsFooterHints(hints: List<GsHint>, modifier: Modifier = Modifier) {
    if (!LocalShowButtonHints.current || hints.isEmpty()) return
    Column(modifier = modifier.fillMaxWidth().background(GsColors.Bg2)) {
        Box(
            Modifier
                .fillMaxWidth()
                .height(1.dp)
                .background(GsColors.Line)
        )
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .horizontalScroll(rememberScrollState())
                .padding(horizontal = 12.dp, vertical = 6.dp),
            horizontalArrangement = Arrangement.spacedBy(14.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            hints.forEach { hint ->
                Row(verticalAlignment = Alignment.CenterVertically) {
                    ButtonGlyph(hint.button)
                    Spacer(Modifier.width(5.dp))
                    Text(
                        hint.label,
                        color = GsColors.Dim,
                        style = MaterialTheme.typography.labelMedium,
                        maxLines = 1,
                    )
                }
            }
        }
    }
}

/**
 * Teal ring while the element (or something inside it) has focus, so
 * D-pad navigation through ordinary Material controls (Settings) is
 * visible on the dark palette.
 */
fun Modifier.gsFocusRing(shape: Shape = RoundedCornerShape(20.dp)): Modifier = composed {
    var focused by remember { mutableStateOf(false) }
    this
        .onFocusChanged { focused = it.isFocused || it.hasFocus }
        .border(2.dp, if (focused) GsColors.Accent2 else Color.Transparent, shape)
}

// ── Rows, pills, banners ──────────────────────────────────────────────────

/**
 * A list row on a panel.  The selected row (gamepad cursor) is lifted to
 * PANEL_HI with a teal selection bar down its left edge.
 */
@Composable
fun GsListRow(
    isSelected: Boolean,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
    containerColor: Color = GsColors.Panel,
    content: @Composable RowScope.() -> Unit,
) {
    val shape = RoundedCornerShape(8.dp)
    Row(
        modifier = modifier
            .fillMaxWidth()
            .height(IntrinsicSize.Min)
            .clip(shape)
            .background(if (isSelected) GsColors.PanelHi else containerColor)
            .then(
                if (isSelected) Modifier.border(1.dp, GsColors.Accent.copy(alpha = 0.55f), shape)
                else Modifier
            )
            .clickable(onClick = onClick),
    ) {
        Box(
            modifier = Modifier
                .width(4.dp)
                .fillMaxHeight()
                .background(if (isSelected) GsColors.Accent else Color.Transparent)
        )
        Row(
            modifier = Modifier
                .weight(1f)
                .padding(horizontal = 12.dp, vertical = 8.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(10.dp),
            content = content,
        )
    }
}

/** Status pill: tinted fill with coloured text, or solid fill with ink text. */
@Composable
fun GsPill(text: String, color: Color, modifier: Modifier = Modifier, filled: Boolean = false) {
    Box(
        modifier = modifier
            .clip(RoundedCornerShape(10.dp))
            .background(if (filled) color else color.copy(alpha = 0.16f))
            .padding(horizontal = 8.dp, vertical = 3.dp),
        contentAlignment = Alignment.Center,
    ) {
        Text(
            text = text,
            style = MaterialTheme.typography.labelSmall,
            fontWeight = FontWeight.Bold,
            color = if (filled) GsColors.Ink else color,
            maxLines = 1,
        )
    }
}

/** One-line status banner on a panel with a coloured edge (offline notice etc.). */
@Composable
fun GsBanner(text: String, color: Color, modifier: Modifier = Modifier) {
    Row(
        modifier = modifier
            .fillMaxWidth()
            .padding(horizontal = 12.dp, vertical = 4.dp)
            .height(IntrinsicSize.Min)
            .clip(RoundedCornerShape(8.dp))
            .background(GsColors.Panel),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Box(
            Modifier
                .width(4.dp)
                .fillMaxHeight()
                .background(color)
        )
        Text(
            text,
            color = color,
            style = MaterialTheme.typography.bodySmall,
            fontWeight = FontWeight.Medium,
            modifier = Modifier.padding(horizontal = 12.dp, vertical = 8.dp),
        )
    }
}

// ── Dialogs ───────────────────────────────────────────────────────────────

/** One button of a [GsDialog]: pressed with [button] on a controller, or tapped. */
data class GsDialogChoice(
    val button: GsButton,
    val label: String,
    val onClick: () -> Unit,
    val color: Color = GsColors.PanelHi,
    val textColor: Color = GsColors.Text,
)

/**
 * Card dialog over the dimmed backdrop, driven by the controller like the
 * console clients: each choice answers to its own button glyph, B (and the
 * system Back gesture) cancels via [onDismiss] unless a choice claims B,
 * and START presses the A choice when [startConfirms].  Touch users get the
 * same choices as buttons.
 *
 * A Dialog is its own window, so its keys never reach MainActivity's
 * dispatchKeyEvent or the screen behind it — they are handled here.
 */
@Composable
fun GsDialog(
    title: String,
    onDismiss: () -> Unit,
    choices: List<GsDialogChoice>,
    message: String = "",
    startConfirms: Boolean = false,
    body: (@Composable ColumnScope.() -> Unit)? = null,
) {
    val focusRequester = remember { FocusRequester() }
    fun choiceFor(button: GsButton): GsDialogChoice? = choices.firstOrNull { it.button == button }
    Dialog(onDismissRequest = onDismiss) {
        Surface(
            shape = RoundedCornerShape(14.dp),
            color = GsColors.Panel,
            modifier = Modifier
                .widthIn(min = 280.dp, max = 520.dp)
                .border(1.dp, GsColors.Line, RoundedCornerShape(14.dp))
                .focusRequester(focusRequester)
                .focusable()
                .onPreviewKeyEvent { event ->
                    if (event.type != KeyEventType.KeyDown) {
                        return@onPreviewKeyEvent false
                    }
                    when (event.key) {
                        Key.ButtonA, Key.Enter, Key.NumPadEnter, Key.DirectionCenter -> {
                            choiceFor(GsButton.A)?.onClick?.invoke(); true
                        }
                        Key.ButtonX -> { choiceFor(GsButton.X)?.onClick?.invoke(); true }
                        Key.ButtonY -> { choiceFor(GsButton.Y)?.onClick?.invoke(); true }
                        Key.ButtonStart -> {
                            if (startConfirms) choiceFor(GsButton.A)?.onClick?.invoke()
                            true
                        }
                        Key.ButtonB, Key.Escape -> {
                            val b = choiceFor(GsButton.B)
                            if (b != null) b.onClick() else onDismiss()
                            true
                        }
                        else -> false
                    }
                },
        ) {
            Column(modifier = Modifier.padding(20.dp)) {
                Text(
                    title,
                    color = GsColors.Text,
                    style = MaterialTheme.typography.titleMedium,
                    fontWeight = FontWeight.Bold,
                )
                if (message.isNotBlank()) {
                    Spacer(Modifier.height(8.dp))
                    Text(
                        message,
                        color = GsColors.Dim,
                        style = MaterialTheme.typography.bodyMedium,
                    )
                }
                if (body != null) {
                    Spacer(Modifier.height(8.dp))
                    Column(
                        modifier = Modifier
                            .heightIn(max = 360.dp)
                            .verticalScroll(rememberScrollState()),
                        content = body,
                    )
                }
                Spacer(Modifier.height(18.dp))
                FlowRowChoices(choices)
            }
        }
        // Inside the Dialog's own composition: the requester lives there.
        LaunchedEffect(Unit) { runCatching { focusRequester.requestFocus() } }
    }
}

/** Choices right-aligned, wrapping onto a second line on narrow phones. */
@Composable
private fun FlowRowChoices(choices: List<GsDialogChoice>) {
    // Ordered B (cancel) first, A (confirm) last — the console layout.
    val ordered = choices.sortedBy {
        when (it.button) {
            GsButton.B -> 0
            GsButton.Y -> 1
            GsButton.X -> 2
            GsButton.A -> 4
            else -> 3
        }
    }
    Column(
        modifier = Modifier.fillMaxWidth(),
        horizontalAlignment = Alignment.End,
        verticalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        ordered.chunked(3).forEach { line ->
            Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                line.forEach { choice ->
                    DialogChoice(choice.button, choice.label, choice.color, choice.textColor, choice.onClick)
                }
            }
        }
    }
}

/** Yes/no [GsDialog]: A (or START when [startConfirms]) confirms, B cancels. */
@Composable
fun GsConfirmDialog(
    title: String,
    message: String,
    confirmLabel: String,
    onConfirm: () -> Unit,
    onDismiss: () -> Unit,
    dismissLabel: String = "Cancel",
    startConfirms: Boolean = false,
    destructive: Boolean = false,
) {
    GsDialog(
        title = title,
        message = message,
        onDismiss = onDismiss,
        startConfirms = startConfirms,
        choices = listOf(
            GsDialogChoice(GsButton.B, dismissLabel, onDismiss),
            GsDialogChoice(
                GsButton.A,
                confirmLabel,
                onConfirm,
                color = if (destructive) GsColors.Err else GsColors.Accent,
                textColor = GsColors.Ink,
            ),
        ),
    )
}

@Composable
private fun DialogChoice(
    button: GsButton,
    label: String,
    background: Color,
    textColor: Color,
    onClick: () -> Unit,
) {
    Row(
        modifier = Modifier
            .heightIn(min = 40.dp)
            .clip(RoundedCornerShape(20.dp))
            .background(background)
            .clickable(onClick = onClick)
            .padding(horizontal = 14.dp, vertical = 8.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        ButtonGlyph(button)
        Spacer(Modifier.width(8.dp))
        Text(
            label,
            color = textColor,
            fontWeight = FontWeight.SemiBold,
            textAlign = TextAlign.Center,
        )
    }
}
