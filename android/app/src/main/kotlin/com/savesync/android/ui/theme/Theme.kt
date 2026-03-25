package com.savesync.android.ui.theme

import android.app.Activity
import android.os.Build
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.dynamicDarkColorScheme
import androidx.compose.material3.dynamicLightColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.SideEffect
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.toArgb
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalView
import androidx.core.view.WindowCompat

// Deep indigo / purple palette
private val md_primary = Color(0xFF5C6BC0)          // Indigo 400
private val md_on_primary = Color(0xFFFFFFFF)
private val md_primary_container = Color(0xFF3949AB) // Indigo 600
private val md_on_primary_container = Color(0xFFE8EAF6)
private val md_secondary = Color(0xFF7E57C2)         // Deep Purple 400
private val md_on_secondary = Color(0xFFFFFFFF)
private val md_tertiary = Color(0xFF5E35B1)          // Deep Purple 600
private val md_on_tertiary = Color(0xFFFFFFFF)
private val md_background_dark = Color(0xFF121212)
private val md_surface_dark = Color(0xFF1E1E2E)
private val md_background_light = Color(0xFFF5F5FF)
private val md_surface_light = Color(0xFFFFFFFF)

private val DarkColorScheme = darkColorScheme(
    primary = md_primary,
    onPrimary = md_on_primary,
    primaryContainer = md_primary_container,
    onPrimaryContainer = md_on_primary_container,
    secondary = md_secondary,
    onSecondary = md_on_secondary,
    tertiary = md_tertiary,
    onTertiary = md_on_tertiary,
    background = md_background_dark,
    surface = md_surface_dark,
)

private val LightColorScheme = lightColorScheme(
    primary = md_primary_container,
    onPrimary = md_on_primary,
    primaryContainer = Color(0xFFE8EAF6),
    onPrimaryContainer = Color(0xFF1A237E),
    secondary = md_secondary,
    onSecondary = md_on_secondary,
    tertiary = md_tertiary,
    onTertiary = md_on_tertiary,
    background = md_background_light,
    surface = md_surface_light,
)

@Composable
fun SaveSyncTheme(
    darkTheme: Boolean = isSystemInDarkTheme(),
    dynamicColor: Boolean = true,
    content: @Composable () -> Unit
) {
    val colorScheme = when {
        dynamicColor && Build.VERSION.SDK_INT >= Build.VERSION_CODES.S -> {
            val context = LocalContext.current
            if (darkTheme) dynamicDarkColorScheme(context) else dynamicLightColorScheme(context)
        }
        darkTheme -> DarkColorScheme
        else -> LightColorScheme
    }

    val view = LocalView.current
    if (!view.isInEditMode) {
        SideEffect {
            val window = (view.context as Activity).window
            window.statusBarColor = colorScheme.primary.toArgb()
            WindowCompat.getInsetsController(window, view).isAppearanceLightStatusBars = !darkTheme
        }
    }

    MaterialTheme(
        colorScheme = colorScheme,
        content = content
    )
}
