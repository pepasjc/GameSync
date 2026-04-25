package com.savesync.android.emulators.impl

import com.savesync.android.sync.SaturnSyncFormat
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Before
import org.junit.Test
import java.io.File

/**
 * Covers the static path prediction used by the server-only placeholder
 * builder so RetroArch-backed downloads land at the expected .srm / .bkr
 * location even when the user hasn't installed a local ROM yet.
 */
class RetroArchEmulatorDefaultSaveFileTest {

    private val root = File("build/test-retroarch-default-save")

    @Before
    fun setUp() {
        root.deleteRecursively()
        root.mkdirs()
    }

    @After
    fun tearDown() {
        root.deleteRecursively()
    }

    @Test
    fun `gba prediction is srm at saves root`() {
        val target = RetroArchEmulator.defaultSaveFile(
            externalStorage = root,
            system = "GBA",
            label = "Pokemon Emerald (USA)"
        )
        assertEquals(
            File(File(File(root, "RetroArch"), "saves"), "Pokemon Emerald (USA).srm"),
            target
        )
    }

    @Test
    fun `snes prediction strips disc tags and filesystem-unsafe characters`() {
        val target = RetroArchEmulator.defaultSaveFile(
            externalStorage = root,
            system = "SNES",
            label = "Super Mario World (Disc 1) / Bonus*"
        )
        assertEquals(
            File(File(File(root, "RetroArch"), "saves"), "Super Mario World Bonus.srm"),
            target
        )
    }

    @Test
    fun `saturn with mednafen and per-core folder enabled lands in beetle saturn subfolder`() {
        val target = RetroArchEmulator.defaultSaveFile(
            externalStorage = root,
            system = "SAT",
            label = "Grandia (USA)",
            saturnSyncFormat = SaturnSyncFormat.MEDNAFEN,
            beetleSaturnPerCoreFolder = true
        )
        assertEquals(
            File(
                File(File(File(root, "RetroArch"), "saves"), "Beetle Saturn"),
                "Grandia (USA).bkr"
            ),
            target
        )
    }

    @Test
    fun `saturn with mednafen and per-core folder disabled lands at saves root`() {
        val target = RetroArchEmulator.defaultSaveFile(
            externalStorage = root,
            system = "SAT",
            label = "Grandia (USA)",
            saturnSyncFormat = SaturnSyncFormat.MEDNAFEN,
            beetleSaturnPerCoreFolder = false
        )
        assertEquals(
            File(File(File(root, "RetroArch"), "saves"), "Grandia (USA).bkr"),
            target
        )
    }

    @Test
    fun `saturn with yabause format uses srm extension`() {
        val target = RetroArchEmulator.defaultSaveFile(
            externalStorage = root,
            system = "SAT",
            label = "Grandia (USA)",
            saturnSyncFormat = SaturnSyncFormat.YABAUSE
        )
        assertEquals(
            File(File(File(root, "RetroArch"), "saves"), "Grandia (USA).srm"),
            target
        )
    }

    @Test
    fun `saturn with yabasanshiro format lands in shared backup container`() {
        val target = RetroArchEmulator.defaultSaveFile(
            externalStorage = root,
            system = "SAT",
            label = "Grandia (USA)",
            saturnSyncFormat = SaturnSyncFormat.YABASANSHIRO
        )
        assertEquals(
            File(File(File(root, "RetroArch"), "saves"), "backup.bin"),
            target
        )
    }

    @Test
    fun `ps1 save with cd per-content folder enabled lands in per-game subfolder`() {
        val target = RetroArchEmulator.defaultSaveFile(
            externalStorage = root,
            system = "PS1",
            label = "Final Fantasy VII (USA) (Disc 1)",
            cdGamesPerContentFolder = true
        )
        // saves/Final Fantasy VII (USA)/Final Fantasy VII (USA).srm
        // (defaultSaveFile() sanitises the stem, so the filename also drops the disc tag)
        assertEquals(
            File(
                File(File(File(root, "RetroArch"), "saves"), "Final Fantasy VII (USA)"),
                "Final Fantasy VII (USA).srm"
            ),
            target
        )
    }

    @Test
    fun `gba save ignores cd per-content folder toggle`() {
        val target = RetroArchEmulator.defaultSaveFile(
            externalStorage = root,
            system = "GBA",
            label = "Pokemon Emerald (USA)",
            cdGamesPerContentFolder = true
        )
        // GBA isn't in CD_SYSTEMS, so the toggle has no effect.
        assertEquals(
            File(File(File(root, "RetroArch"), "saves"), "Pokemon Emerald (USA).srm"),
            target
        )
    }

    @Test
    fun `saturn mednafen with both per-core and per-content toggles on`() {
        val target = RetroArchEmulator.defaultSaveFile(
            externalStorage = root,
            system = "SAT",
            label = "Grandia (USA) (Disc 1)",
            saturnSyncFormat = SaturnSyncFormat.MEDNAFEN,
            beetleSaturnPerCoreFolder = true,
            cdGamesPerContentFolder = true
        )
        assertEquals(
            File(
                File(
                    File(File(File(root, "RetroArch"), "saves"), "Beetle Saturn"),
                    "Grandia (USA)"
                ),
                "Grandia (USA).bkr"
            ),
            target
        )
    }

    @Test
    fun `prefers an existing install base when one is on disk`() {
        // Seed the 64-bit package directory and its saves dir so findSavesDir
        // picks it up ahead of the generic RetroArch/ fallback.
        val existingBase = File(root, "Android/data/com.retroarch.aarch64/files/saves")
        existingBase.mkdirs()

        val target = RetroArchEmulator.defaultSaveFile(
            externalStorage = root,
            system = "GBA",
            label = "Metroid Fusion"
        )
        assertEquals(File(existingBase, "Metroid Fusion.srm"), target)
    }
}
