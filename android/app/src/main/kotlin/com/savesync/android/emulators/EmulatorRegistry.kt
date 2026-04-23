package com.savesync.android.emulators

import com.savesync.android.emulators.impl.AetherSX2Emulator
import com.savesync.android.emulators.impl.AzaharEmulator
import com.savesync.android.emulators.impl.DolphinEmulator
import com.savesync.android.emulators.impl.DraSticEmulator
import com.savesync.android.emulators.impl.DuckStationEmulator
import com.savesync.android.emulators.impl.MelonDsEmulator
import com.savesync.android.emulators.impl.MgbaEmulator
import com.savesync.android.emulators.impl.PpssppEmulator
import com.savesync.android.emulators.impl.RetroArchEmulator
import com.savesync.android.sync.SaturnSyncFormat
import java.io.File

object EmulatorRegistry {

    /**
     * Build the full emulator list.
     * @param romScanDir Optional path whose immediate subfolders are scanned for ROMs
     *   by folder name (e.g. "/sdcard/Isos" with subfolders GBA/, MegaDrive/, PS1/, …).
     * @param romDirOverrides Per-system folder overrides: system code → absolute path.
     *   Takes precedence over auto-detected subfolders of [romScanDir].
     */
    fun buildAll(
        romScanDir: String = "",
        dolphinMemCardDir: String = "",
        romDirOverrides: Map<String, String> = emptyMap(),
        saturnSyncFormat: SaturnSyncFormat = SaturnSyncFormat.MEDNAFEN
    ): List<EmulatorBase> = listOf(
        AzaharEmulator(),
        RetroArchEmulator(romScanDir, romDirOverrides, saturnSyncFormat),
        PpssppEmulator(romScanDir, romDirOverrides),
        DuckStationEmulator(romScanDir),
        DraSticEmulator(romScanDir),
        MelonDsEmulator(romScanDir),
        MgbaEmulator(),
        DolphinEmulator(dolphinMemCardDir),
        AetherSX2Emulator(romScanDir)
    )

    /**
     * Discover all saves, applying user-chosen system overrides where present.
     * @param overrides  map of absolute file path → user-chosen system (e.g. "GBA")
     * @param romScanDir Optional directory whose subfolders are scanned for ROMs
     * @param romDirOverrides Per-system folder overrides: system code → absolute path
     */
    fun discoverAllSaves(
        overrides: Map<String, String> = emptyMap(),
        romScanDir: String = "",
        dolphinMemCardDir: String = "",
        romDirOverrides: Map<String, String> = emptyMap(),
        saturnSyncFormat: SaturnSyncFormat = SaturnSyncFormat.MEDNAFEN
    ): List<SaveEntry> {
        return buildAll(romScanDir, dolphinMemCardDir, romDirOverrides, saturnSyncFormat).flatMap { emulator ->
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
     * @param romDirOverrides Per-system folder overrides: system code → absolute path
     */
    fun discoverAllRomEntries(
        romScanDir: String = "",
        dolphinMemCardDir: String = "",
        romDirOverrides: Map<String, String> = emptyMap(),
        saturnSyncFormat: SaturnSyncFormat = SaturnSyncFormat.MEDNAFEN
    ): Map<String, SaveEntry> {
        val result = mutableMapOf<String, SaveEntry>()
        buildAll(romScanDir, dolphinMemCardDir, romDirOverrides, saturnSyncFormat).forEach { emulator ->
            try { result.putAll(emulator.discoverRomEntries()) } catch (_: Exception) {}
        }
        return result
    }

    /**
     * Scans [romScanDir]'s immediate subfolders and returns a map of
     * system code → absolute folder path for each subfolder that resolves to a
     * known system. Used to populate the per-system folder override UI with
     * sensible defaults before the user makes any manual changes.
     */
    fun detectSystemFolders(romScanDir: String): Map<String, String> {
        if (romScanDir.isBlank()) return emptyMap()
        val scanRoot = File(romScanDir)
        if (!scanRoot.exists() || !scanRoot.isDirectory) return emptyMap()
        val helper = RetroArchEmulator(romScanDir)
        val result = mutableMapOf<String, String>()
        scanRoot.listFiles()
            ?.filter { it.isDirectory && !it.name.startsWith(".") }
            ?.forEach { dir ->
                val system = helper.resolveSystemFromFolderName(dir.name) ?: return@forEach
                result.putIfAbsent(system, dir.absolutePath)
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
