package com.savesync.android.ui.theme

import android.app.Activity
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.SideEffect
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.toArgb
import androidx.compose.ui.platform.LocalView
import androidx.core.view.WindowCompat

/**
 * The palette every GameSync console client draws with (3ds/source/gui.h,
 * xbox/source/ui.h, ...), so the Android app reads as the same product.
 * Keep the hex values in step with those headers.
 */
object GsColors {
    val Bg = Color(0xFF0F1720)
    val Bg2 = Color(0xFF1B2633)
    val Panel = Color(0xFF243244)
    val PanelHi = Color(0xFF2C3D52)
    val Line = Color(0xFF33465C)
    val Text = Color(0xFFE6EDF3)
    val Dim = Color(0xFF8DA2B5)
    val Muted = Color(0xFF5E7286)
    val Accent = Color(0xFF2EC4B6)
    val Accent2 = Color(0xFF3DDBD9)
    val Ok = Color(0xFF3FB950)
    val Warn = Color(0xFFF0B429)
    val Info = Color(0xFF58A6FF)
    val Err = Color(0xFFF85149)
    val Ra = Color(0xFFE5B143)
    /** Dark text on accent / status fills. */
    val Ink = Color(0xFF0F1720)

    // Controller face-button glyph colours.
    val BtnA = Color(0xFF5FBF3F)
    val BtnB = Color(0xFFE5483F)
    val BtnX = Color(0xFF3D8FE0)
    val BtnY = Color(0xFFF2C230)
}

/**
 * Always dark, like every console client: no light scheme and no dynamic
 * (wallpaper) colour, so the app looks the same on every device.
 */
private val GameSyncColorScheme = darkColorScheme(
    primary = GsColors.Accent,
    onPrimary = GsColors.Ink,
    primaryContainer = GsColors.PanelHi,
    onPrimaryContainer = GsColors.Accent2,
    inversePrimary = GsColors.Accent2,
    secondary = GsColors.Accent2,
    onSecondary = GsColors.Ink,
    secondaryContainer = GsColors.PanelHi,
    onSecondaryContainer = GsColors.Text,
    tertiary = GsColors.Info,
    onTertiary = GsColors.Ink,
    tertiaryContainer = GsColors.PanelHi,
    onTertiaryContainer = GsColors.Info,
    background = GsColors.Bg,
    onBackground = GsColors.Text,
    surface = GsColors.Bg2,
    onSurface = GsColors.Text,
    surfaceVariant = GsColors.Panel,
    onSurfaceVariant = GsColors.Dim,
    surfaceTint = GsColors.Accent,
    inverseSurface = GsColors.Text,
    inverseOnSurface = GsColors.Bg,
    error = GsColors.Err,
    onError = GsColors.Ink,
    errorContainer = GsColors.Panel,
    onErrorContainer = GsColors.Err,
    outline = GsColors.Line,
    outlineVariant = GsColors.Line,
    scrim = Color(0xFF000000),
    surfaceBright = GsColors.PanelHi,
    surfaceDim = GsColors.Bg,
    surfaceContainer = GsColors.Bg2,
    surfaceContainerHigh = GsColors.Panel,
    surfaceContainerHighest = GsColors.Panel,
    surfaceContainerLow = GsColors.Bg2,
    surfaceContainerLowest = GsColors.Bg,
)

@Composable
fun SaveSyncTheme(
    content: @Composable () -> Unit
) {
    val colorScheme = GameSyncColorScheme

    val view = LocalView.current
    if (!view.isInEditMode) {
        SideEffect {
            val window = (view.context as Activity).window
            window.statusBarColor = GsColors.Bg.toArgb()
            window.navigationBarColor = GsColors.Bg.toArgb()
            val insets = WindowCompat.getInsetsController(window, view)
            insets.isAppearanceLightStatusBars = false
            insets.isAppearanceLightNavigationBars = false
        }
    }

    MaterialTheme(
        colorScheme = colorScheme,
        content = content
    )
}
