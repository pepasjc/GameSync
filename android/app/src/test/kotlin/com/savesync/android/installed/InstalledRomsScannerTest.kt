package com.savesync.android.installed

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import java.io.File

class InstalledRomsScannerTest {

    @get:Rule
    val tmp = TemporaryFolder()

    private lateinit var romScanDir: File

    @Before
    fun setUp() {
        romScanDir = tmp.newFolder("ROMs")
    }

    private fun writeFile(path: File, contents: ByteArray = ByteArray(100) { it.toByte() }): File {
        path.parentFile?.mkdirs()
        path.writeBytes(contents)
        return path
    }

    @Test
    fun `scan finds cart roms across multiple system folders`() {
        writeFile(File(romScanDir, "GBA/Pokemon Emerald (USA).gba"))
        writeFile(File(romScanDir, "SNES/Chrono Trigger.sfc"))

        val roms = InstalledRomsScanner.scanInstalled(romScanDir.absolutePath)

        assertEquals(2, roms.size)
        assertEquals(listOf("GBA", "SNES"), roms.map { it.system })
        val gba = roms.first { it.system == "GBA" }
        assertEquals("Pokemon Emerald (USA)", gba.displayName)
        assertTrue(gba.companionFiles.isEmpty())
    }

    @Test
    fun `scan recognizes 3DS emulator formats`() {
        writeFile(File(romScanDir, "3DS/Super Mario 3D Land (USA).cci"))
        writeFile(File(romScanDir, "3DS/Animal Crossing - New Leaf (USA).cia"))

        val roms = InstalledRomsScanner.scanInstalled(romScanDir.absolutePath)

        assertEquals(2, roms.size)
        assertEquals(listOf("3DS", "3DS"), roms.map { it.system })
        assertEquals(
            listOf(
                "Animal Crossing - New Leaf (USA)",
                "Super Mario 3D Land (USA)",
            ),
            roms.map { it.displayName },
        )
    }

    @Test
    fun `cue plus bin pair groups under the cue primary`() {
        val cue = File(romScanDir, "PS1/Final Fantasy VII (USA).cue")
        val bin = File(romScanDir, "PS1/Final Fantasy VII (USA).bin")
        cue.parentFile!!.mkdirs()
        cue.writeText("FILE \"Final Fantasy VII (USA).bin\" BINARY\n")
        writeFile(bin, ByteArray(5000))

        val roms = InstalledRomsScanner.scanInstalled(romScanDir.absolutePath)

        assertEquals(1, roms.size)
        val rom = roms[0]
        assertEquals(cue, rom.path)
        assertEquals(listOf(bin), rom.companionFiles)
    }

    @Test
    fun `delete at system root unlinks files but keeps the system folder`() {
        val cue = File(romScanDir, "PS1/Wild Arms.cue")
        val bin = File(romScanDir, "PS1/Wild Arms.bin")
        cue.parentFile!!.mkdirs()
        cue.writeText("FILE \"Wild Arms.bin\" BINARY\n")
        writeFile(bin, ByteArray(2000))

        val roms = InstalledRomsScanner.scanInstalled(romScanDir.absolutePath)
        assertEquals(1, roms.size)
        assertFalse(InstalledRomsScanner.wouldRemoveWholeFolder(roms[0]))

        val result = InstalledRomsScanner.deleteInstalled(roms[0])

        assertEquals(2, result.deletedCount)
        assertTrue(result.errors.isEmpty())
        assertEquals(null, result.removedDir)
        assertFalse(cue.exists())
        assertFalse(bin.exists())
        // System folder itself stays
        assertTrue(File(romScanDir, "PS1").isDirectory)
    }

    @Test
    fun `delete collapses a dedicated per-game subfolder via rmtree`() {
        val gameDir = File(romScanDir, "PS1/Final Fantasy VII")
        gameDir.mkdirs()
        val cue = File(gameDir, "FF7.cue")
        cue.writeText(
            "FILE \"FF7 (Track 01).bin\" BINARY\nFILE \"FF7 (Track 02).bin\" BINARY\n"
        )
        writeFile(File(gameDir, "FF7 (Track 01).bin"), ByteArray(1000))
        writeFile(File(gameDir, "FF7 (Track 02).bin"), ByteArray(500))

        val roms = InstalledRomsScanner.scanInstalled(romScanDir.absolutePath)
        assertEquals(1, roms.size)
        assertTrue(InstalledRomsScanner.wouldRemoveWholeFolder(roms[0]))

        val result = InstalledRomsScanner.deleteInstalled(roms[0])

        assertEquals(gameDir, result.removedDir)
        assertEquals(3, result.deletedCount)
        assertTrue(result.errors.isEmpty())
        assertFalse(gameDir.exists())
        // Parent system folder stays
        assertTrue(File(romScanDir, "PS1").isDirectory)
    }

    @Test
    fun `shared folder with another game keeps the folder intact`() {
        val shared = File(romScanDir, "PS1/Discs")
        shared.mkdirs()
        val aCue = File(shared, "Game A.cue").apply {
            writeText("FILE \"Game A.bin\" BINARY\n")
        }
        val aBin = writeFile(File(shared, "Game A.bin"), ByteArray(100))
        val bCue = File(shared, "Game B.cue").apply {
            writeText("FILE \"Game B.bin\" BINARY\n")
        }
        val bBin = writeFile(File(shared, "Game B.bin"), ByteArray(100))

        val roms = InstalledRomsScanner.scanInstalled(romScanDir.absolutePath)
        assertEquals(2, roms.size)
        val romA = roms.first { it.displayName == "Game A" }
        assertFalse(InstalledRomsScanner.wouldRemoveWholeFolder(romA))

        val result = InstalledRomsScanner.deleteInstalled(romA)

        assertEquals(null, result.removedDir)
        assertEquals(2, result.deletedCount)
        assertFalse(aCue.exists())
        assertFalse(aBin.exists())
        // Game B + the folder itself stick around
        assertTrue(bCue.exists())
        assertTrue(bBin.exists())
        assertTrue(shared.isDirectory)
    }

    @Test
    fun `delete never rmtree's the system root even for a lone game`() {
        val rom = writeFile(File(romScanDir, "GBA/Kirby.gba"), ByteArray(50))
        val roms = InstalledRomsScanner.scanInstalled(romScanDir.absolutePath)
        assertEquals(1, roms.size)
        assertFalse(InstalledRomsScanner.wouldRemoveWholeFolder(roms[0]))

        InstalledRomsScanner.deleteInstalled(roms[0])

        assertFalse(rom.exists())
        assertTrue(File(romScanDir, "GBA").isDirectory)
    }

    @Test
    fun `whole-folder delete sweeps non-rom clutter too`() {
        val gameDir = File(romScanDir, "Dreamcast/Shenmue")
        gameDir.mkdirs()
        File(gameDir, "Shenmue.gdi").writeText(
            "2\n1 0 4 2352 \"Shenmue (Track 01).bin\" 0\n" +
                "2 600 4 2352 \"Shenmue (Track 02).bin\" 0\n"
        )
        writeFile(File(gameDir, "Shenmue (Track 01).bin"), ByteArray(1000))
        writeFile(File(gameDir, "Shenmue (Track 02).bin"), ByteArray(2000))
        writeFile(File(gameDir, "readme.txt"), "hi".toByteArray())
        val thumbs = File(gameDir, ".thumbs")
        thumbs.mkdir()
        writeFile(File(thumbs, "cover.jpg"), ByteArray(400))

        val roms = InstalledRomsScanner.scanInstalled(romScanDir.absolutePath)
        assertEquals(1, roms.size)
        assertTrue(InstalledRomsScanner.wouldRemoveWholeFolder(roms[0]))

        val result = InstalledRomsScanner.deleteInstalled(roms[0])

        assertEquals(gameDir, result.removedDir)
        // gdi + 2 bin tracks + readme + cover = 5
        assertEquals(5, result.deletedCount)
        assertFalse(gameDir.exists())
    }

    @Test
    fun `scan walks nested subfolders`() {
        writeFile(File(romScanDir, "PS1/USA/Crash Bandicoot.chd"), ByteArray(100))
        val roms = InstalledRomsScanner.scanInstalled(romScanDir.absolutePath)
        assertEquals(1, roms.size)
        assertEquals("PS1", roms[0].system)
        assertEquals("Crash Bandicoot.chd", roms[0].filename)
    }

    @Test
    fun `scan ignores non-rom files`() {
        writeFile(File(romScanDir, "GBA/Readme.txt"))
        writeFile(File(romScanDir, "GBA/Boxart.jpg"))
        writeFile(File(romScanDir, "GBA/Real.gba"))

        val roms = InstalledRomsScanner.scanInstalled(romScanDir.absolutePath)
        assertEquals(1, roms.size)
        assertEquals("Real.gba", roms[0].filename)
    }

    @Test
    fun `rom dir overrides take precedence over candidate search`() {
        val custom = tmp.newFolder("CustomSaturn")
        writeFile(File(custom, "Grandia.chd"), ByteArray(200))

        val roms = InstalledRomsScanner.scanInstalled(
            romScanDir.absolutePath,
            mapOf("SAT" to custom.absolutePath)
        )

        assertEquals(1, roms.size)
        assertEquals("SAT", roms[0].system)
        assertEquals(custom, roms[0].systemRoot)
    }
}
