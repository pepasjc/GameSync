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
