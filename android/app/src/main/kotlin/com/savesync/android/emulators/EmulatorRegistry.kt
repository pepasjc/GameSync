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

/**
 * Lightweight description of one emulator for the Emulator Configuration
 * screen.  Pairs the persistent settings key with the user-facing label and
 * a one-line "what it syncs" hint.  Order here is also the order shown on
 * screen, so put the most commonly configured emulators first.
 */
data class EmulatorDescriptor(
    val key: String,
    val displayName: String,
    val systemHint: String,
    val defaultPathHint: String
)

object EmulatorCatalog {
    /** All known emulators, in display order. */
    val ALL: List<EmulatorDescriptor> = listOf(
        EmulatorDescriptor(
            key = RetroArchEmulator.EMULATOR_KEY,
            displayName = "RetroArch",
            systemHint = "Multi-system (saves/)",
            defaultPathHint = "Auto-detected via retroarch.cfg or <RetroArch>/saves/"
        ),
        EmulatorDescriptor(
            key = PpssppEmulator.EMULATOR_KEY,
            displayName = "PPSSPP",
            systemHint = "PSP",
            defaultPathHint = "<PPSSPP>/PSP/SAVEDATA/"
        ),
        EmulatorDescriptor(
            key = DuckStationEmulator.EMULATOR_KEY,
            displayName = "DuckStation",
            systemHint = "PS1",
            defaultPathHint = "Android/data/com.github.stenzek.duckstation/files/memcards/"
        ),
        EmulatorDescriptor(
            key = AetherSX2Emulator.EMULATOR_KEY,
            displayName = "AetherSX2 / NetherSX2",
            systemHint = "PS2",
            defaultPathHint = "Android/data/xyz.aethersx2.android/files/memcards/"
        ),
        EmulatorDescriptor(
            key = DolphinEmulator.EMULATOR_KEY,
            displayName = "Dolphin",
            systemHint = "GameCube",
            defaultPathHint = "<dolphin-mmjr>/GC/<region>/Card A/"
        ),
        EmulatorDescriptor(
            key = AzaharEmulator.EMULATOR_KEY,
            displayName = "Azahar",
            systemHint = "Nintendo 3DS",
            defaultPathHint = "sdmc/Nintendo 3DS/.../title/"
        ),
        EmulatorDescriptor(
            key = MelonDsEmulator.EMULATOR_KEY,
            displayName = "melonDS",
            systemHint = "Nintendo DS",
            defaultPathHint = "<external>/melonDS/"
        ),
        EmulatorDescriptor(
            key = DraSticEmulator.EMULATOR_KEY,
            displayName = "DraStic",
            systemHint = "Nintendo DS",
            defaultPathHint = "<external>/drastic/backup/"
        ),
        EmulatorDescriptor(
            key = MgbaEmulator.EMULATOR_KEY,
            displayName = "mGBA",
            systemHint = "GBA",
            defaultPathHint = "<external>/mGBA/saves/"
        )
    )
}

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
        emudeckDir: String = "",
        romDirOverrides: Map<String, String> = emptyMap(),
        saveDirOverrides: Map<String, String> = emptyMap(),
        saturnSyncFormat: SaturnSyncFormat = SaturnSyncFormat.MEDNAFEN,
        beetleSaturnPerCoreFolder: Boolean = true,
        cdGamesPerContentFolder: Boolean = false
    ): List<EmulatorBase> {
        // Helper: per-emulator save-dir override, blank-treated-as-null so an
        // empty string in the map (e.g. legacy data) doesn't accidentally
        // disable auto-detection.
        fun ovr(key: String): String? = saveDirOverrides[key]?.takeIf { it.isNotBlank() }
        return listOf(
            AzaharEmulator(
                storageBaseDir = EmudeckPaths.azaharRoot(emudeckDir),
                saveDirOverride = ovr(AzaharEmulator.EMULATOR_KEY)
            ),
            RetroArchEmulator(
                romScanDir = romScanDir,
                romDirOverrides = romDirOverrides,
                saturnSyncFormat = saturnSyncFormat,
                beetleSaturnPerCoreFolder = beetleSaturnPerCoreFolder,
                cdGamesPerContentFolder = cdGamesPerContentFolder,
                saveDirOverride = ovr(RetroArchEmulator.EMULATOR_KEY)
            ),
            PpssppEmulator(
                romScanDir = romScanDir,
                romDirOverrides = romDirOverrides,
                storageBaseDir = EmudeckPaths.ppssppRoot(emudeckDir),
                saveDirOverride = ovr(PpssppEmulator.EMULATOR_KEY)
            ),
            DuckStationEmulator(
                romScanDir = romScanDir,
                saveDirOverride = ovr(DuckStationEmulator.EMULATOR_KEY)
            ),
            DraSticEmulator(
                romScanDir = romScanDir,
                saveDirOverride = ovr(DraSticEmulator.EMULATOR_KEY)
            ),
            MelonDsEmulator(
                romScanDir = romScanDir,
                saveDirOverride = ovr(MelonDsEmulator.EMULATOR_KEY)
            ),
            MgbaEmulator(
                saveDirOverride = ovr(MgbaEmulator.EMULATOR_KEY)
            ),
            DolphinEmulator(
                dolphinMemCardDir = ovr(DolphinEmulator.EMULATOR_KEY) ?: "",
                dolphinRootDir = EmudeckPaths.dolphinRoot(emudeckDir)
            ),
            AetherSX2Emulator(
                romScanDir = romScanDir,
                storageBaseDir = EmudeckPaths.netherSx2Root(emudeckDir),
                saveDirOverride = ovr(AetherSX2Emulator.EMULATOR_KEY)
            )
        )
    }

    /**
     * Discover all saves, applying user-chosen system overrides where present.
     * @param overrides  map of absolute file path → user-chosen system (e.g. "GBA")
     * @param romScanDir Optional directory whose subfolders are scanned for ROMs
     * @param romDirOverrides Per-system folder overrides: system code → absolute path
     */
    fun discoverAllSaves(
        overrides: Map<String, String> = emptyMap(),
        romScanDir: String = "",
        emudeckDir: String = "",
        romDirOverrides: Map<String, String> = emptyMap(),
        saveDirOverrides: Map<String, String> = emptyMap(),
        saturnSyncFormat: SaturnSyncFormat = SaturnSyncFormat.MEDNAFEN,
        beetleSaturnPerCoreFolder: Boolean = true,
        cdGamesPerContentFolder: Boolean = false
    ): List<SaveEntry> {
        return buildAll(
            romScanDir = romScanDir,
            emudeckDir = emudeckDir,
            romDirOverrides = romDirOverrides,
            saveDirOverrides = saveDirOverrides,
            saturnSyncFormat = saturnSyncFormat,
            beetleSaturnPerCoreFolder = beetleSaturnPerCoreFolder,
            cdGamesPerContentFolder = cdGamesPerContentFolder
        ).flatMap { emulator ->
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
        emudeckDir: String = "",
        romDirOverrides: Map<String, String> = emptyMap(),
        saveDirOverrides: Map<String, String> = emptyMap(),
        saturnSyncFormat: SaturnSyncFormat = SaturnSyncFormat.MEDNAFEN,
        beetleSaturnPerCoreFolder: Boolean = true,
        cdGamesPerContentFolder: Boolean = false
    ): Map<String, SaveEntry> {
        val result = mutableMapOf<String, SaveEntry>()
        buildAll(
            romScanDir = romScanDir,
            emudeckDir = emudeckDir,
            romDirOverrides = romDirOverrides,
            saveDirOverrides = saveDirOverrides,
            saturnSyncFormat = saturnSyncFormat,
            beetleSaturnPerCoreFolder = beetleSaturnPerCoreFolder,
            cdGamesPerContentFolder = cdGamesPerContentFolder
        ).forEach { emulator ->
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
