package com.savesync.android.emulators.impl

import com.savesync.android.sync.SaturnSyncFormat
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

class RetroArchEmulatorSaturnPathTest {

    @Test
    fun `mednafen path prefers beetle saturn subfolder for new downloads`() {
        val savesDir = File("build/test-retroarch-saves")
        val emulator = RetroArchEmulator(saturnSyncFormat = SaturnSyncFormat.MEDNAFEN)

        val target = emulator.expectedRetroArchSaturnSaveFile(
            savesDir,
            "Grandia (Japan) (Disc 1) (4M) [T-En by TrekkiesUnite118 v0.9.3 RC]"
        )

        assertEquals(
            File(
                File(savesDir, "Beetle Saturn"),
                "Grandia (Japan) (Disc 1) (4M) [T-En by TrekkiesUnite118 v0.9.3 RC].bkr"
            ),
            target
        )
    }

    @Test
    fun `mednafen path uses saves root when per-core folder is disabled`() {
        val savesDir = File("build/test-retroarch-saves-flat")
        val emulator = RetroArchEmulator(
            saturnSyncFormat = SaturnSyncFormat.MEDNAFEN,
            beetleSaturnPerCoreFolder = false
        )

        val target = emulator.expectedRetroArchSaturnSaveFile(
            savesDir,
            "Grandia (Japan) (Disc 1)"
        )

        assertEquals(File(savesDir, "Grandia (Japan) (Disc 1).bkr"), target)
    }

    @Test
    fun `mednafen path layers per-content folder under per-core when both toggles on`() {
        val savesDir = File("build/test-retroarch-saves-pc-on")
        val emulator = RetroArchEmulator(
            saturnSyncFormat = SaturnSyncFormat.MEDNAFEN,
            beetleSaturnPerCoreFolder = true,
            cdGamesPerContentFolder = true
        )

        val target = emulator.expectedRetroArchSaturnSaveFile(
            savesDir,
            "Grandia (Japan) (Disc 1)"
        )

        // saves/Beetle Saturn/Grandia (Japan)/Grandia (Japan) (Disc 1).bkr
        assertEquals(
            File(
                File(File(savesDir, "Beetle Saturn"), "Grandia (Japan)"),
                "Grandia (Japan) (Disc 1).bkr"
            ),
            target
        )
    }

    @Test
    fun `mednafen path lands in per-content folder at root when per-core off but per-content on`() {
        val savesDir = File("build/test-retroarch-saves-pc-only")
        val emulator = RetroArchEmulator(
            saturnSyncFormat = SaturnSyncFormat.MEDNAFEN,
            beetleSaturnPerCoreFolder = false,
            cdGamesPerContentFolder = true
        )

        val target = emulator.expectedRetroArchSaturnSaveFile(
            savesDir,
            "Grandia (Japan) (Disc 1)"
        )

        assertEquals(
            File(File(savesDir, "Grandia (Japan)"), "Grandia (Japan) (Disc 1).bkr"),
            target
        )
    }

    @Test
    fun `yabasanshiro shared backup is never wrapped in a per-content folder`() {
        val savesDir = File("build/test-retroarch-saves-yaba-pc")
        val emulator = RetroArchEmulator(
            saturnSyncFormat = SaturnSyncFormat.YABASANSHIRO,
            cdGamesPerContentFolder = true
        )

        val target = emulator.expectedRetroArchSaturnSaveFile(
            savesDir,
            "Grandia (Japan) (Disc 1)"
        )

        // Still saves/yabasanshiro/backup.bin — single shared container
        assertEquals(File(File(savesDir, "yabasanshiro"), "backup.bin"), target)
    }

    @Test
    fun `applyPerContentFolder wraps a flat ps1 save when toggle is on`() {
        val savesDir = File("build/test-retroarch-ps1-pc")
        val flat = File(savesDir, "Final Fantasy VII (USA) (Disc 1).srm")

        val wrapped = RetroArchEmulator.applyPerContentFolder(
            baseFile = flat,
            romName = "Final Fantasy VII (USA) (Disc 1)",
            system = "PS1",
            enabled = true
        )

        assertEquals(
            File(File(savesDir, "Final Fantasy VII (USA)"), "Final Fantasy VII (USA) (Disc 1).srm"),
            wrapped
        )
    }

    @Test
    fun `applyPerContentFolder leaves non-cd systems alone`() {
        val savesDir = File("build/test-retroarch-gba-pc")
        val flat = File(savesDir, "Pokemon Emerald (USA).srm")

        val wrapped = RetroArchEmulator.applyPerContentFolder(
            baseFile = flat,
            romName = "Pokemon Emerald (USA)",
            system = "GBA",
            enabled = true
        )

        assertEquals(flat, wrapped)
    }

    @Test
    fun `yabause path uses default retroarch saves root with srm extension`() {
        val savesDir = File("build/test-retroarch-saves")
        val emulator = RetroArchEmulator(saturnSyncFormat = SaturnSyncFormat.YABAUSE)

        val target = emulator.expectedRetroArchSaturnSaveFile(
            savesDir,
            "Grandia (Japan) (Disc 1) (4M) [T-En by TrekkiesUnite118 v0.9.3 RC]"
        )

        assertEquals(
            File(savesDir, "Grandia (Japan) (Disc 1) (4M) [T-En by TrekkiesUnite118 v0.9.3 RC].srm"),
            target
        )
    }

    @Test
    fun `yabasanshiro path targets shared backup container`() {
        val savesDir = File("build/test-retroarch-saves")
        val emulator = RetroArchEmulator(saturnSyncFormat = SaturnSyncFormat.YABASANSHIRO)

        val target = emulator.expectedRetroArchSaturnSaveFile(
            savesDir,
            "Grandia (Japan) (Disc 1) (4M) [T-En by TrekkiesUnite118 v0.9.3 RC]"
        )

        assertEquals(File(File(savesDir, "yabasanshiro"), "backup.bin"), target)
    }

    @Test
    fun `saturn save discovery only accepts current local format`() {
        val mednafen = RetroArchEmulator(saturnSyncFormat = SaturnSyncFormat.MEDNAFEN)
        assertTrue(mednafen.shouldTrackRetroArchSaveFile(File("Grandia.bkr"), "SAT"))
        assertFalse(mednafen.shouldTrackRetroArchSaveFile(File("Grandia.srm"), "SAT"))

        val yabause = RetroArchEmulator(saturnSyncFormat = SaturnSyncFormat.YABAUSE)
        assertTrue(yabause.shouldTrackRetroArchSaveFile(File("Grandia.srm"), "SAT"))
        assertFalse(yabause.shouldTrackRetroArchSaveFile(File("Grandia.bkr"), "SAT"))

        val yabasanshiro = RetroArchEmulator(saturnSyncFormat = SaturnSyncFormat.YABASANSHIRO)
        assertTrue(yabasanshiro.shouldTrackRetroArchSaveFile(File("backup.bin"), "SAT"))
        assertFalse(yabasanshiro.shouldTrackRetroArchSaveFile(File("Grandia.bin"), "SAT"))
        assertFalse(yabasanshiro.shouldTrackRetroArchSaveFile(File("Grandia.bkr"), "SAT"))
    }
}
