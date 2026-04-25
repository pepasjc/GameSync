package com.savesync.android.installed

import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import java.io.File

class ResolveRomTargetDirTest {

    @get:Rule
    val tmp = TemporaryFolder()

    private fun scanRoot(): File = tmp.root

    // --- candidate search ------------------------------------------------

    @Test
    fun `picks first existing candidate folder under scan root`() {
        File(scanRoot(), "PSX").mkdirs()
        val dir = InstalledRomsScanner.resolveRomTargetDir(scanRoot(), "PS1")
        assertEquals(File(scanRoot(), "PSX"), dir)
    }

    @Test
    fun `falls back to the first candidate when no existing folder matches`() {
        val dir = InstalledRomsScanner.resolveRomTargetDir(scanRoot(), "PS1")
        // SYSTEM_ROM_DIRS for PS1 starts with "psx" to match EmuDeck.
        assertEquals(File(scanRoot(), "psx"), dir)
        // Helper must NOT create the folder — caller handles mkdirs().
        assertEquals(false, dir.exists())
    }

    @Test
    fun `handles unknown system by falling back to the raw code`() {
        val dir = InstalledRomsScanner.resolveRomTargetDir(scanRoot(), "NOPE")
        assertEquals(File(scanRoot(), "NOPE"), dir)
    }

    // --- alias canonicalisation (the SCD bug) ---------------------------

    @Test
    fun `SCD lands in the same folder as SEGACD`() {
        // Regression: the server emitted "SCD" for Sega CD, the download
        // path missed it and created roms/SCD/ next to the user's
        // existing roms/segacd/.  After canonicalisation both codes
        // should resolve to identical paths.
        File(scanRoot(), "segacd").mkdirs()
        val viaAlias = InstalledRomsScanner.resolveRomTargetDir(scanRoot(), "SCD")
        val viaCanonical = InstalledRomsScanner.resolveRomTargetDir(scanRoot(), "SEGACD")
        assertEquals(viaCanonical, viaAlias)
        assertEquals(File(scanRoot(), "segacd"), viaAlias)
    }

    @Test
    fun `GEN Genesis MEGADRIVE all collapse to MD folder`() {
        File(scanRoot(), "megadrive").mkdirs()
        val md = InstalledRomsScanner.resolveRomTargetDir(scanRoot(), "MD")
        val gen = InstalledRomsScanner.resolveRomTargetDir(scanRoot(), "GEN")
        val genesis = InstalledRomsScanner.resolveRomTargetDir(scanRoot(), "Genesis")
        val megadrive = InstalledRomsScanner.resolveRomTargetDir(scanRoot(), "Mega Drive")
        assertEquals(md, gen)
        assertEquals(md, genesis)
        assertEquals(md, megadrive)
    }

    @Test
    fun `ATARI5200 resolves to the Atari 5200 candidate list`() {
        // The old when-expression in SyncEngine.downloadRom was missing
        // a case for ATARI5200 / A5200, so downloads fell into
        // roms/ATARI5200/ instead of the InstalledRomsScanner-expected
        // ``atari5200`` folder.
        val viaLegacy = InstalledRomsScanner.resolveRomTargetDir(scanRoot(), "ATARI5200")
        val viaCanonical = InstalledRomsScanner.resolveRomTargetDir(scanRoot(), "A5200")
        assertEquals(viaCanonical, viaLegacy)
        assertEquals(File(scanRoot(), "atari5200"), viaLegacy)
    }

    // --- overrides -------------------------------------------------------

    @Test
    fun `override beats the candidate search`() {
        File(scanRoot(), "PSX").mkdirs()
        val custom = tmp.newFolder("extra-sd", "playstation").absolutePath
        val dir = InstalledRomsScanner.resolveRomTargetDir(
            scanRoot(),
            "PS1",
            mapOf("PS1" to custom),
        )
        assertEquals(File(custom), dir)
    }

    @Test
    fun `override keyed on an alias still applies`() {
        val custom = tmp.newFolder("external", "segacd").absolutePath
        // User happens to have the override keyed under the legacy "SCD"
        // code (e.g. hand-edited settings).  We canonicalise the map keys
        // so the override still wins.
        val dir = InstalledRomsScanner.resolveRomTargetDir(
            scanRoot(),
            "SEGACD",
            mapOf("SCD" to custom),
        )
        assertEquals(File(custom), dir)
    }

    @Test
    fun `blank override falls through to candidate search`() {
        File(scanRoot(), "PSX").mkdirs()
        val dir = InstalledRomsScanner.resolveRomTargetDir(
            scanRoot(),
            "PS1",
            mapOf("PS1" to "   "),
        )
        assertEquals(File(scanRoot(), "PSX"), dir)
    }

    @Test
    fun `relative override resolves under the scan root`() {
        val dir = InstalledRomsScanner.resolveRomTargetDir(
            scanRoot(),
            "PS1",
            mapOf("PS1" to "my-ps1-games"),
        )
        assertEquals(File(scanRoot(), "my-ps1-games"), dir)
    }

    @Test
    fun `override for a different system does not leak to PS1`() {
        File(scanRoot(), "PSX").mkdirs()
        val dir = InstalledRomsScanner.resolveRomTargetDir(
            scanRoot(),
            "PS1",
            mapOf("GBA" to "/tmp/gba"),
        )
        assertEquals(File(scanRoot(), "PSX"), dir)
    }

    // --- prepareRomFolders ----------------------------------------------

    @Test
    fun `prepare creates every system folder under an empty root`() {
        val report = InstalledRomsScanner.prepareRomFolders(scanRoot())
        // Every entry in SYSTEM_ROM_DIRS gets a new folder — no
        // pre-existing aliases, no errors.
        assertEquals(
            InstalledRomsScanner.SYSTEM_ROM_DIRS.size,
            report.createdCount,
        )
        assertEquals(0, report.existing.size)
        assertEquals(0, report.errors.size)
        // And the first candidate in each system's list is the one
        // created (matches resolveRomTargetDir's fallback).
        for ((system, candidates) in InstalledRomsScanner.SYSTEM_ROM_DIRS) {
            val expected = File(scanRoot(), candidates.first())
            org.junit.Assert.assertTrue(
                "$system should have created ${expected.absolutePath}",
                expected.isDirectory,
            )
        }
    }

    @Test
    fun `prepare leaves existing alias folders untouched`() {
        File(scanRoot(), "PSX").mkdirs()  // PS1 alias
        File(scanRoot(), "Mega Drive").mkdirs()  // MD alias
        val report = InstalledRomsScanner.prepareRomFolders(scanRoot())

        val existing = report.existing.map { it.first }.toSet()
        org.junit.Assert.assertTrue("PS1" in existing)
        org.junit.Assert.assertTrue("MD" in existing)
        // No duplicate PS1 folder got created.
        org.junit.Assert.assertFalse(File(scanRoot(), "psx").exists())
        org.junit.Assert.assertFalse(File(scanRoot(), "PlayStation").exists())
    }

    @Test
    fun `prepare honours per-system overrides`() {
        val custom = tmp.newFolder("external", "my-saturn")
        // Remove the folder so prepare has something to create.
        custom.delete()
        val report = InstalledRomsScanner.prepareRomFolders(
            scanRoot(),
            mapOf("SAT" to custom.absolutePath),
        )
        org.junit.Assert.assertTrue(custom.isDirectory)
        // And the default ``saturn`` folder was NOT created under the
        // scan root — the override won.
        org.junit.Assert.assertFalse(File(scanRoot(), "saturn").exists())
        val createdTargets = report.created.map { it.second }
        org.junit.Assert.assertTrue(custom in createdTargets)
    }

    @Test
    fun `prepare canonicalises alias override keys`() {
        // Legacy-keyed override under "SCD" should route to the SEGACD
        // iteration (not create a separate "SCD" folder).
        val custom = tmp.newFolder("external", "segacd-alt")
        custom.delete()
        val report = InstalledRomsScanner.prepareRomFolders(
            scanRoot(),
            mapOf("SCD" to custom.absolutePath),
        )
        org.junit.Assert.assertTrue(custom.isDirectory)
        val createdSystems = report.created.map { it.first }
        org.junit.Assert.assertTrue("SEGACD" in createdSystems)
    }

    @Test
    fun `prepare is idempotent`() {
        val first = InstalledRomsScanner.prepareRomFolders(scanRoot())
        val second = InstalledRomsScanner.prepareRomFolders(scanRoot())
        org.junit.Assert.assertTrue(first.createdCount > 0)
        assertEquals(0, second.createdCount)
        // Every system is now in the "existing" column.
        assertEquals(first.createdCount, second.existing.size)
    }
}
