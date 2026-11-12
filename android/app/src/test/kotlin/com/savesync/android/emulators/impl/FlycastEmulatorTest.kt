package com.savesync.android.emulators.impl

import com.savesync.android.emulators.FlycastVmu
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import java.io.File

class FlycastEmulatorTest {

    @get:Rule
    val tmp = TemporaryFolder()

    @Test
    fun `vmu names map back to the rom stem`() {
        assertEquals("Crazy Taxi (USA)", FlycastVmu.libretroGameStem("Crazy Taxi (USA).A1.bin"))
        assertEquals("Crazy Taxi (USA)", FlycastVmu.standaloneGameStem("Crazy Taxi (USA)_vmu_save_A1.bin"))
        // Shared VMUs and other ports hold several games: not one slot.
        assertNull(FlycastVmu.standaloneGameStem("vmu_save_A1.bin"))
        assertNull(FlycastVmu.libretroGameStem("vmu_save_A1.bin"))
        assertNull(FlycastVmu.libretroGameStem("Crazy Taxi (USA).B1.bin"))
        assertNull(FlycastVmu.libretroGameStem("Crazy Taxi (USA).bin"))
        assertEquals("X.A1.bin", FlycastVmu.libretroFileName("X"))
        assertEquals("X_vmu_save_A1.bin", FlycastVmu.standaloneFileName("X"))
    }

    @Test
    fun `discovers per-game vmus in the flycast data folder`() {
        val data = tmp.newFolder("Flycast", "data")
        File(data, "Crazy Taxi (USA)_vmu_save_A1.bin").writeBytes(ByteArray(131072))
        File(data, "vmu_save_A1.bin").writeBytes(ByteArray(131072))
        File(data, "Crazy Taxi (USA).nvmem").writeBytes(ByteArray(16))

        val entries = FlycastEmulator(saveDirOverride = data.absolutePath).discoverSaves()

        assertEquals(1, entries.size)
        val entry = entries.single()
        assertEquals("DC", entry.systemName)
        assertEquals("Crazy Taxi (USA)", entry.displayName)
        assertTrue(entry.titleId.startsWith("DC_"))
        assertEquals("Crazy Taxi (USA)_vmu_save_A1.bin", entry.saveFile?.name)
    }

    @Test
    fun `rom entries predict the vmu flycast will write`() {
        val data = tmp.newFolder("home", "data")
        val roms = tmp.newFolder("roms", "dreamcast")
        File(roms, "Shenmue (USA) (Disc 1).chd").writeBytes(ByteArray(4))
        File(tmp.newFolder("roms", "dreamcast", "Crazy Taxi"), "Crazy Taxi (USA).gdi").writeText("")

        val entries = FlycastEmulator(
            romScanDir = File(tmp.root, "roms").absolutePath,
            saveDirOverride = data.absolutePath,
        ).discoverRomEntries().values

        val names = entries.map { it.saveFile?.name }.toSet()
        assertEquals(
            setOf("Shenmue (USA) (Disc 1)_vmu_save_A1.bin", "Crazy Taxi (USA)_vmu_save_A1.bin"),
            names,
        )
        assertTrue(entries.all { it.saveFile?.parentFile == data })
    }

    @Test
    fun `rom entries are only offered when flycast is the chosen dreamcast emulator`() {
        val data = tmp.newFolder("home2", "data")
        File(tmp.newFolder("roms2", "dreamcast"), "Shenmue (USA) (Disc 1).chd").writeBytes(ByteArray(4))
        val emulator = FlycastEmulator(
            romScanDir = File(tmp.root, "roms2").absolutePath,
            saveDirOverride = data.absolutePath,
            providesDownloadTargets = false,
        )
        assertTrue(emulator.discoverRomEntries().isEmpty())
    }

    @Test
    fun `no flycast data folder means no rom entries`() {
        assertTrue(FlycastEmulator().discoverRomEntries().isEmpty())
        assertFalse(FlycastEmulator().discoverSaves().isNotEmpty())
    }

    @Test
    fun `product-number vmu names carry the serial`() {
        // What the Flycast core wrote for Soulcalibur (Europe): IP.BIN "T1401D  50".
        assertEquals("DC_T1401D50", FlycastVmu.titleIdFromGameId("T1401D__50"))
        assertEquals("DC_51035", FlycastVmu.titleIdFromGameId("MK-51035"))
        assertEquals("DC_T1249M", FlycastVmu.titleIdFromGameId("T-1249M"))
        // Legacy ROM-named cards resolve through the ROM / DAT instead.
        assertNull(FlycastVmu.titleIdFromGameId("Crazy Taxi (USA)"))
        assertNull(FlycastVmu.titleIdFromGameId("Soulcalibur"))
    }

    @Test
    fun `product-number card wins over the legacy rom-named card`() {
        // Both resolve to one title: the legacy card through the ROM, the
        // other from its name.  Flycast loads the product-number one.
        val legacy = "Soulcalibur (Europe) (En,Fr,De,Es)" to "DC_T1401D50"
        val current = "T1401D__50" to "DC_T1401D50"
        val kept = FlycastVmu.loadedPerTitle(listOf(legacy, current), { it.second }, { it.first })
        assertEquals(listOf(current), kept)
        // With only the legacy card, that one is kept.
        assertEquals(listOf(legacy), FlycastVmu.loadedPerTitle(listOf(legacy), { it.second }, { it.first }))
    }
}
