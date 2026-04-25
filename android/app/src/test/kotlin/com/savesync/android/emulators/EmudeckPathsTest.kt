package com.savesync.android.emulators

import com.savesync.android.emulators.impl.DolphinEmulator
import com.savesync.android.emulators.impl.PpssppEmulator
import com.savesync.android.emulators.impl.AzaharEmulator
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import java.io.File

class EmudeckPathsTest {

    @get:Rule
    val tmp = TemporaryFolder()

    @Test
    fun `maps configured emudeck folder to emulator storage roots`() {
        val root = File("/sdcard/Emulation")

        assertEquals(File(root, "roms"), EmudeckPaths.romsDir(root.absolutePath))
        assertEquals(File(root, "storage/Azahar"), EmudeckPaths.azaharRoot(root.absolutePath))
        assertEquals(File(root, "storage/Dolphin"), EmudeckPaths.dolphinRoot(root.absolutePath))
        assertEquals(File(root, "storage/NetherSX2"), EmudeckPaths.netherSx2Root(root.absolutePath))
        assertEquals(File(root, "storage/PPSSPP"), EmudeckPaths.ppssppRoot(root.absolutePath))
    }

    @Test
    fun `ppsspp scans savedata under emudeck ppsspp root`() {
        val ppssppRoot = File(tmp.root, "storage/PPSSPP")
        val slotDir = File(ppssppRoot, "PSP/SAVEDATA/ULUS10567DATA").also { it.mkdirs() }
        File(slotDir, "DATA.BIN").writeBytes(byteArrayOf(1, 2, 3))

        val entries = PpssppEmulator(storageBaseDir = ppssppRoot).discoverSaves()

        assertEquals(1, entries.size)
        assertEquals("ULUS10567DATA", entries.single().titleId)
        assertEquals(slotDir.absolutePath, entries.single().saveDir?.absolutePath)
    }

    @Test
    fun `azahar scans sdmc under emudeck azahar root`() {
        val azaharRoot = File(tmp.root, "storage/Azahar")
        val saveDir = File(
            azaharRoot,
            "sdmc/Nintendo 3DS/00000000000000000000000000000000/" +
                "00000000000000000000000000000000/title/00040000/00054000/data/00000001"
        ).also { it.mkdirs() }
        File(saveDir, "progress.sav").writeBytes(byteArrayOf(1, 2, 3))

        val entries = AzaharEmulator(storageBaseDir = azaharRoot).discoverSaves()

        assertEquals(1, entries.size)
        assertEquals("0004000000054000", entries.single().titleId)
        assertEquals(saveDir.absolutePath, entries.single().saveDir?.absolutePath)
    }

    @Test
    fun `dolphin scans gc card folder under emudeck dolphin root`() {
        val dolphinRoot = File(tmp.root, "storage/Dolphin")
        val cardDir = File(dolphinRoot, "GC/USA/Card A").also { it.mkdirs() }
        val gci = File(cardDir, "01-GM4E-MarioKart Double Dash!!.gci")
        gci.writeBytes(byteArrayOf(1, 2, 3))

        val entries = DolphinEmulator(dolphinRootDir = dolphinRoot).discoverSaves()

        assertEquals(1, entries.size)
        assertEquals("GC_gm4e", entries.single().titleId)
        assertEquals(gci.absolutePath, entries.single().saveFile?.absolutePath)
    }
}
