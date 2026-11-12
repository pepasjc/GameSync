package com.savesync.android.emulators

import com.savesync.android.emulators.impl.HakuXEmulator
import com.savesync.android.sync.BundleUtils
import com.savesync.android.sync.HashUtils
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import java.io.File

/**
 * Xbox saves (hakuX, X1 BOX) travel like the Xbox client's: '\' paths in
 * '\'-path order, which is also the order the PS5 client hashes in.
 */
class XboxSavesTest {

    @get:Rule
    val tmp = TemporaryFolder()

    private fun saveDir(): File {
        val dir = tmp.newFolder("4D53006E")
        File(dir, "Save").mkdirs()
        File(dir, "Save/a.dat").writeText("A")
        File(dir, "SaveB").writeText("B")
        File(dir, "TitleMeta.xbx").writeBytes(
            byteArrayOf(0xFF.toByte(), 0xFE.toByte()) + "TitleName=Halo\r\n".toByteArray(Charsets.UTF_16LE)
        )
        return dir
    }

    @Test
    fun `sorts by the backslash path, not the slash one`() {
        // '\' sorts after 'B', '/' before it: the two orders differ here.
        val names = XboxSaves.treeFiles(saveDir()).map { it.first }
        assertEquals(listOf("SaveB", "Save\\a.dat", "TitleMeta.xbx"), names)
    }

    @Test
    fun `entry hash follows the bundle order`() {
        val dir = saveDir()
        val entry = SaveEntry("4D53006E", "Halo", "XBOX", null, saveDir = dir)
        assertTrue(entry.isTreeSaveDir)
        val files = BundleUtils.parseBundle(BundleUtils.createTreeBundle("4D53006E", dir, xboxPaths = true))
        assertEquals(listOf("SaveB", "Save\\a.dat", "TitleMeta.xbx"), files.map { it.first })
        val bundleHash = HashUtils.sha256Bytes(files.fold(ByteArray(0)) { acc, f -> acc + f.second })
        assertEquals(bundleHash, entry.computeHash())
    }

    @Test
    fun `reads the title name from TitleMeta`() {
        assertEquals("Halo", XboxSaves.titleName(saveDir()))
        assertNull(XboxSaves.titleName(tmp.newFolder("empty")))
    }

    @Test
    fun `game title ids start with two publisher letters`() {
        assertTrue(XboxSaves.isGameTitleId("4D53006E"))
        assertTrue(XboxSaves.isGameTitleId("4d53006e"))
        assertFalse(XboxSaves.isGameTitleId("FFFE0000"))   // the dashboard's own folder
        assertFalse(XboxSaves.isGameTitleId("4D53006"))
    }

    @Test
    fun `automatic picks the installed emulator, hakuX first`() {
        val none = { _: String -> false }
        val both = { _: String -> true }
        val x1Only = { p: String -> p == X1BOX_PACKAGE }
        assertEquals(XboxEmulatorChoice.HAKUX, XboxEmulatorChoice.AUTO.resolve(none))
        assertEquals(XboxEmulatorChoice.HAKUX, XboxEmulatorChoice.AUTO.resolve(both))
        assertEquals(XboxEmulatorChoice.X1BOX, XboxEmulatorChoice.AUTO.resolve(x1Only))
        // A choice whose app isn't installed falls back to the one that is.
        assertEquals(XboxEmulatorChoice.X1BOX, XboxEmulatorChoice.HAKUX.resolve(x1Only))
        assertEquals(XboxEmulatorChoice.X1BOX, XboxEmulatorChoice.X1BOX.resolve(both))
        assertEquals(XboxEmulatorChoice.X1BOX, XboxEmulatorChoice.X1BOX.resolve(none))
        assertEquals(XboxEmulatorChoice.AUTO, XboxEmulatorChoice.fromWireValue("bogus"))
    }

    @Test
    fun `hakuX saves are its E partition's UDATA title folders`() {
        val root = tmp.newFolder("ext")
        val udata = File(root, HakuXEmulator.HDD_PATH + "/Partition1/UDATA").apply { mkdirs() }
        File(udata, "4D53006E/slot").mkdirs()
        File(udata, "4D53006E/slot/save.dat").writeText("x")
        File(udata, "FFFE0000").mkdirs()
        assertNull(HakuXEmulator.problem(root))
        assertEquals(udata, HakuXEmulator.udataDir(root))
        assertEquals(File(udata, "4D53006E"), HakuXEmulator.titleDir(udata, "4d53006e"))
        assertEquals(File(udata, "4C410002"), HakuXEmulator.titleDir(udata, "4c410002"))
        assertTrue(HakuXEmulator.problem(tmp.newFolder("unpatched")) != null)
    }
}
