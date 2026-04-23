package com.savesync.android.emulators.impl

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import java.io.File

class AzaharEmulatorTest {

    @get:Rule
    val tmp = TemporaryFolder()

    @Test
    fun `discoverSaves reads 3ds title directories`() {
        val titleRoot = File(tmp.root, "azahar-title-root")
        val saveDir = File(titleRoot, "00040000/00054000/data/00000001")
        saveDir.mkdirs()
        File(saveDir, "progress.sav").writeBytes(byteArrayOf(1, 2, 3))
        File(saveDir, "nested").mkdirs()
        File(saveDir, "nested/config.bin").writeBytes(byteArrayOf(4, 5))

        val emulator = AzaharEmulator(candidateTitleRoots = listOf(titleRoot))
        val entries = emulator.discoverSaves()

        assertEquals(1, entries.size)
        val entry = entries.single()
        assertEquals("0004000000054000", entry.titleId)
        assertEquals("3DS", entry.systemName)
        assertTrue(entry.isMultiFile)
        assertEquals(saveDir.absolutePath, entry.saveDir?.absolutePath)
    }

    @Test
    fun `defaultSaveDir builds the expected title path`() {
        val titleRoot = File(tmp.root, "preferred-root")
        val saveDir = AzaharEmulator.defaultSaveDir(
            storageBaseDir = tmp.root,
            titleId = "0004000000030800",
            candidateTitleRoots = listOf(titleRoot)
        )

        assertEquals(
            File(titleRoot, "00040000/00030800/data/00000001").absolutePath,
            saveDir?.absolutePath
        )
    }
}
