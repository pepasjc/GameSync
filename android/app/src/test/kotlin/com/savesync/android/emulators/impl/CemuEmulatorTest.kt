package com.savesync.android.emulators.impl

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import java.io.File
import java.security.MessageDigest

/**
 * Cemu Wii U scanning: mlc01 layout, meta.xml names, tree hashing and the
 * predicted download path.
 *
 * Everything runs against the companion helpers so the tests never touch
 * Android's Environment.
 */
class CemuEmulatorTest {

    @get:Rule
    val tmp = TemporaryFolder()

    private val metaXml = """
        <?xml version="1.0" encoding="utf-8"?>
        <menu>
          <product_code type="string" length="32">WUP-P-ARDE</product_code>
          <longname_ja type="string" length="512">マリオ</longname_ja>
          <longname_en type="string" length="512">Super Mario 3D World&apos;s
        Special Edition</longname_en>
        </menu>
    """.trimIndent()

    private val looseGameMetaXml = """
        <?xml version="1.0" encoding="utf-8"?>
        <menu>
          <title_id type="hexBinary" length="8">0005000010101d00</title_id>
          <product_code type="string" length="32">WUP-P-AGMP</product_code>
          <longname_en type="string" length="512">Splatoon</longname_en>
        </menu>
    """.trimIndent()

    private val updateMetaXml = """
        <?xml version="1.0" encoding="utf-8"?>
        <menu>
          <title_id type="hexBinary" length="8">0005000e10143500</title_id>
          <product_code type="string" length="32">WUP-P-ARDE</product_code>
          <longname_en type="string" length="512">Patched Disc Game</longname_en>
        </menu>
    """.trimIndent()

    private fun makeSave(mlc: File, tidlo: String = "10143500"): File {
        val saveDir = File(mlc, "usr/save/00050000/$tidlo/user")
        File(saveDir, "80000001").mkdirs()
        File(saveDir, "common").mkdirs()
        File(saveDir, "80000001/game.dat").writeText("slot-one")
        File(saveDir, "common/shared.bin").writeText("common-data")
        return saveDir
    }

    private fun writeMeta(mlc: File, tidlo: String = "10143500", high: String = "00050000") {
        val metaDir = File(mlc, "usr/title/$high/$tidlo/meta")
        metaDir.mkdirs()
        File(metaDir, "meta.xml").writeText(metaXml)
    }

    @Test
    fun `scan reads the mlc01 save layout`() {
        val mlc = tmp.newFolder("mlc01")
        val saveDir = makeSave(mlc)
        writeMeta(mlc)

        val entries = CemuEmulator.discoverSaves(mlc)

        assertEquals(1, entries.size)
        val entry = entries[0]
        assertEquals("0005000010143500", entry.titleId)
        assertEquals("WIIU", entry.systemName)
        assertEquals(saveDir, entry.saveDir)
        assertTrue(entry.isMultiFile)
        assertEquals("Super Mario 3D World's Special Edition", entry.displayName)
        assertEquals("WIIU_ARDE", entry.gameCode)
    }

    @Test
    fun `save hash matches the server bundle hash`() {
        val mlc = tmp.newFolder("mlc01")
        makeSave(mlc)

        val entry = CemuEmulator.discoverSaves(mlc).single()

        // Bundle order is the relative-path sort: 80000001/game.dat, then
        // common/shared.bin.  The server hashes only the concatenated bytes.
        val digest = MessageDigest.getInstance("SHA-256")
        digest.update("slot-one".toByteArray())
        digest.update("common-data".toByteArray())
        val expected = digest.digest().joinToString("") { "%02x".format(it) }

        assertEquals(expected, entry.computeHash())
    }

    @Test
    fun `disc games are named from the update title meta`() {
        val mlc = tmp.newFolder("mlc01")
        makeSave(mlc)
        writeMeta(mlc, high = "0005000E")

        val entry = CemuEmulator.discoverSaves(mlc).single()

        assertEquals("Super Mario 3D World's Special Edition", entry.displayName)
        assertEquals("WIIU_ARDE", entry.gameCode)
    }

    @Test
    fun `a title without meta falls back to its title id`() {
        val mlc = tmp.newFolder("mlc01")
        makeSave(mlc)

        val entry = CemuEmulator.discoverSaves(mlc).single()

        assertEquals("0005000010143500", entry.displayName)
        assertNull(entry.gameCode)
    }

    @Test
    fun `an empty user dir is skipped`() {
        val mlc = tmp.newFolder("mlc01")
        File(mlc, "usr/save/00050000/10143500/user").mkdirs()

        assertTrue(CemuEmulator.discoverSaves(mlc).isEmpty())
    }

    @Test
    fun `override may name the mlc01 dir or its parent`() {
        val cemuDir = tmp.newFolder("Cemu")
        val mlc = File(cemuDir, "mlc01")
        makeSave(mlc)
        val unrelated = tmp.newFolder("external")

        assertEquals(
            mlc,
            CemuEmulator.resolveMlcRoot(null, mlc.absolutePath, unrelated)
        )
        assertEquals(
            mlc,
            CemuEmulator.resolveMlcRoot(null, cemuDir.absolutePath, unrelated)
        )
    }

    @Test
    fun `external storage candidates are searched when nothing is configured`() {
        val external = tmp.newFolder("external")
        val mlc = File(external, "Android/data/info.cemu.Cemu/files/mlc01")
        makeSave(mlc)

        assertEquals(mlc, CemuEmulator.resolveMlcRoot(null, null, external))
    }

    @Test
    fun `an override pointing nowhere falls back to auto-detection`() {
        val external = tmp.newFolder("external")
        val mlc = File(external, "Cemu/mlc01")
        makeSave(mlc)

        assertEquals(
            mlc,
            CemuEmulator.resolveMlcRoot(null, "/does/not/exist", external)
        )
    }

    @Test
    fun `default save dir is predictable from the title id`() {
        val external = tmp.newFolder("external")
        val mlc = File(external, "Cemu/mlc01")
        makeSave(mlc)

        val predicted = CemuEmulator.defaultSaveDir(
            storageBaseDir = null,
            saveDirOverride = null,
            externalRoot = external,
            titleId = "0005000010101D00"
        )

        assertEquals(File(mlc, "usr/save/00050000/10101d00/user"), predicted)
    }

    @Test
    fun `a loose game dump names a save the mlc knows nothing about`() {
        // The common Cemu library: unpacked dumps that were never installed,
        // so the save's only naming source is the game folder's meta.xml.
        val mlc = tmp.newFolder("mlc01")
        makeSave(mlc, tidlo = "10101d00")

        val games = tmp.newFolder("games")
        val gameMeta = File(games, "Splatoon/meta")
        gameMeta.mkdirs()
        File(gameMeta, "meta.xml").writeText(looseGameMetaXml)

        val entry = CemuEmulator.discoverSaves(mlc, listOf(games)).single()

        assertEquals("0005000010101D00", entry.titleId)
        assertEquals("Splatoon", entry.displayName)
        assertEquals("WIIU_AGMP", entry.gameCode)
    }

    @Test
    fun `an update meta title id folds onto the application id`() {
        val mlc = tmp.newFolder("mlc01")
        makeSave(mlc, tidlo = "10143500")

        val games = tmp.newFolder("games")
        val gameMeta = File(games, "Disc Game/meta")
        gameMeta.mkdirs()
        File(gameMeta, "meta.xml").writeText(updateMetaXml)

        val entry = CemuEmulator.discoverSaves(mlc, listOf(games)).single()

        assertEquals("0005000010143500", entry.titleId)
        assertEquals("Patched Disc Game", entry.displayName)
    }

    @Test
    fun `non wii u title ids have no default save dir`() {
        val external = tmp.newFolder("external")
        makeSave(File(external, "Cemu/mlc01"))

        assertNull(
            CemuEmulator.defaultSaveDir(
                storageBaseDir = null,
                saveDirOverride = null,
                externalRoot = external,
                titleId = "0004000000030800"
            )
        )
    }
}
