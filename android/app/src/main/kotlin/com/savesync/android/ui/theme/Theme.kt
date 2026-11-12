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
    val Bg = Color(0xFF0B1020)
    val Bg2 = Color(0xFF141B33)
    val Panel = Color(0xFF1D2746)
    val PanelHi = Color(0xFF2A3760)
    val Line = Color(0xFF36446E)
    val Text = Color(0xFFEEF2FA)
    val Dim = Color(0xFF9BA9CD)
    val Muted = Color(0xFF6B789F)
    val Accent = Color(0xFF22C3F6)
    val Accent2 = Color(0xFF7BDDFF)
    val Ok = Color(0xFF3DD68C)
    val Warn = Color(0xFFFFB938)
    val Info = Color(0xFF6C93FF)
    val Err = Color(0xFFFF5D6C)
    val Ra = Color(0xFFE5B143)
    /** The logo's gradient, left to right (see GsBrandRule). */
    val BrandA = Color(0xFF00D6FF)
    val BrandB = Color(0xFF3A78FF)
    val BrandC = Color(0xFFA45CFF)
    val BrandGradient = listOf(BrandA, BrandB, BrandC)
    /** Dark text on accent / status fills. */
    val Ink = Color(0xFF0B1020)

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
