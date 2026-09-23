package com.savesync.android.emulators.impl

import com.savesync.android.sync.SegaCdSyncFormat
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

class RetroArchEmulatorSegaCdPathTest {

    private fun tempSavesDir(name: String): File =
        File("build/$name").apply { deleteRecursively(); mkdirs() }

    @Test
    fun `genesis plus gx predicts a brm file`() {
        val savesDir = tempSavesDir("test-segacd-gpgx")
        val emulator = RetroArchEmulator(segaCdSyncFormat = SegaCdSyncFormat.GENESIS_PLUS_GX)

        val target = emulator.expectedRetroArchSegaCdSaveFile(savesDir, "Shining Force CD (USA) (5R, 6R)")

        assertEquals(File(savesDir, "Shining Force CD (USA) (5R, 6R).brm"), target)
    }

    @Test
    fun `picodrive predicts an srm file`() {
        val savesDir = tempSavesDir("test-segacd-picodrive")
        val emulator = RetroArchEmulator(segaCdSyncFormat = SegaCdSyncFormat.PICODRIVE)

        val target = emulator.expectedRetroArchSegaCdSaveFile(savesDir, "Snatcher (USA)")

        assertEquals(File(savesDir, "Snatcher (USA).srm"), target)
    }

    @Test
    fun `an existing file from the other core wins over the prediction`() {
        val savesDir = tempSavesDir("test-segacd-existing")
        val existing = File(savesDir, "Sonic CD (USA).srm").apply { writeBytes(ByteArray(8192)) }
        val emulator = RetroArchEmulator(segaCdSyncFormat = SegaCdSyncFormat.GENESIS_PLUS_GX)

        val target = emulator.expectedRetroArchSegaCdSaveFile(savesDir, "Sonic CD (USA)")

        assertEquals(existing, target)
    }

    @Test
    fun `the configured format wins when both cores have a file`() {
        val savesDir = tempSavesDir("test-segacd-both")
        File(savesDir, "Lunar (USA).srm").writeBytes(ByteArray(8192))
        val brm = File(savesDir, "Lunar (USA).brm").apply { writeBytes(ByteArray(8192)) }
        val emulator = RetroArchEmulator(segaCdSyncFormat = SegaCdSyncFormat.GENESIS_PLUS_GX)

        assertEquals(brm, emulator.expectedRetroArchSegaCdSaveFile(savesDir, "Lunar (USA)"))
    }

    @Test
    fun `per content folder is honoured for new downloads`() {
        val savesDir = tempSavesDir("test-segacd-percontent")
        val emulator = RetroArchEmulator(
            segaCdSyncFormat = SegaCdSyncFormat.GENESIS_PLUS_GX,
            cdGamesPerContentFolder = true
        )

        val target = emulator.expectedRetroArchSegaCdSaveFile(savesDir, "Popful Mail (USA) (Disc 1)")

        assertEquals(
            File(File(savesDir, "Popful Mail (USA)"), "Popful Mail (USA) (Disc 1).brm"),
            target
        )
    }

    @Test
    fun `only the configured extension is tracked for sega cd`() {
        val gpgx = RetroArchEmulator(segaCdSyncFormat = SegaCdSyncFormat.GENESIS_PLUS_GX)
        assertTrue(gpgx.shouldTrackRetroArchSaveFile(File("Shining Force CD.brm"), "SEGACD"))
        assertFalse(gpgx.shouldTrackRetroArchSaveFile(File("Shining Force CD.srm"), "SEGACD"))

        val pico = RetroArchEmulator(segaCdSyncFormat = SegaCdSyncFormat.PICODRIVE)
        assertTrue(pico.shouldTrackRetroArchSaveFile(File("Shining Force CD.srm"), "SEGACD"))
        assertFalse(pico.shouldTrackRetroArchSaveFile(File("Shining Force CD.brm"), "SEGACD"))
    }

    @Test
    fun `brm is never tracked under a non sega cd system`() {
        val gpgx = RetroArchEmulator(segaCdSyncFormat = SegaCdSyncFormat.GENESIS_PLUS_GX)
        assertFalse(gpgx.shouldTrackRetroArchSaveFile(File("Phantasy Star IV.brm"), "MD"))
        assertTrue(gpgx.shouldTrackRetroArchSaveFile(File("Phantasy Star IV.srm"), "MD"))
    }

    @Test
    fun `default save file honours the sega cd format`() {
        val ext = File("build/test-segacd-default").apply { deleteRecursively(); mkdirs() }
        File(ext, "RetroArch/saves").mkdirs()

        val brm = RetroArchEmulator.defaultSaveFile(
            externalStorage = ext,
            system = "SEGACD",
            label = "Shining Force CD (USA) (5R, 6R)",
            segaCdSyncFormat = SegaCdSyncFormat.GENESIS_PLUS_GX
        )
        assertEquals("Shining Force CD (USA) (5R, 6R).brm", brm?.name)

        val srm = RetroArchEmulator.defaultSaveFile(
            externalStorage = ext,
            system = "SEGACD",
            label = "Shining Force CD (USA) (5R, 6R)",
            segaCdSyncFormat = SegaCdSyncFormat.PICODRIVE
        )
        assertEquals("Shining Force CD (USA) (5R, 6R).srm", srm?.name)
    }
}
