package com.savesync.android.emulators

import com.savesync.android.emulators.impl.AetherSX2Emulator
import com.savesync.android.emulators.impl.DolphinEmulator
import com.savesync.android.emulators.impl.DraSticEmulator
import com.savesync.android.emulators.impl.MelonDsEmulator
import com.savesync.android.emulators.impl.MgbaEmulator
import com.savesync.android.emulators.impl.PpssppEmulator
import com.savesync.android.emulators.impl.RetroArchEmulator

object EmulatorRegistry {

    /**
     * Build the full emulator list.
     * @param romScanDir Optional path whose immediate subfolders are scanned for ROMs
     *   by folder name (e.g. "/sdcard/Isos" with subfolders GBA/, MegaDrive/, PS1/, …).
     */
    fun buildAll(romScanDir: String = ""): List<EmulatorBase> = listOf(
        RetroArchEmulator(romScanDir),
        PpssppEmulator(),
        DraSticEmulator(romScanDir),
        MelonDsEmulator(romScanDir),
        MgbaEmulator(),
        DolphinEmulator(),
        AetherSX2Emulator()
    )

    /**
     * Discover all saves, applying user-chosen system overrides where present.
     * @param overrides  map of absolute file path → user-chosen system (e.g. "GBA")
     * @param romScanDir Optional directory whose subfolders are scanned for ROMs
     */
    fun discoverAllSaves(
        overrides: Map<String, String> = emptyMap(),
        romScanDir: String = ""
    ): List<SaveEntry> {
        return buildAll(romScanDir).flatMap { emulator ->
            try {
                emulator.discoverSaves()
                    .filter { it.exists() }
                    .map { entry -> applyOverride(entry, overrides) }
            } catch (e: Exception) {
                emptyList()
            }
        }
    }

    /**
     * Returns every ROM the device's emulators know about, keyed by titleId.
     * Includes ROMs that have no save file yet (so we can show server-only saves
     * only when the corresponding ROM is installed).
     * @param romScanDir Optional directory whose subfolders are scanned for ROMs
     */
    fun discoverAllRomEntries(romScanDir: String = ""): Map<String, SaveEntry> {
        val result = mutableMapOf<String, SaveEntry>()
        buildAll(romScanDir).forEach { emulator ->
            try { result.putAll(emulator.discoverRomEntries()) } catch (_: Exception) {}
        }
        return result
    }

    private fun applyOverride(entry: SaveEntry, overrides: Map<String, String>): SaveEntry {
        val filePath = (entry.saveFile ?: entry.saveDir)?.absolutePath ?: return entry
        val newSystem = overrides[filePath] ?: return entry
        if (newSystem == entry.systemName) return entry
        // Rebuild title ID with the new system prefix
        val slug = entry.titleId.substringAfter("_")  // keep the rom slug, replace prefix
        return entry.copy(
            systemName = newSystem,
            titleId = "${newSystem}_$slug"
        )
    }
}
