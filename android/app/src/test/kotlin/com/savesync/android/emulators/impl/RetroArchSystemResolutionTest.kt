package com.savesync.android.emulators.impl

import com.savesync.android.systems.SystemAliases
import org.junit.Assert.assertEquals
import org.junit.Test

/**
 * Guards the PC Engine CD-ROM² split.
 *
 * Beetle PCE / PCE Fast run HuCard and CD content through the same core, and
 * every NEC folder name contains the substring "PCENGINE", so both used to
 * collapse to ``PCE``.  The server keys CD saves under ``PCECD_<slug>``, so
 * the collapse meant a local PCE CD save never matched its server twin and
 * the download target could not be predicted at all.
 */
class RetroArchSystemResolutionTest {

    private val emulator = RetroArchEmulator()

    @Test
    fun `cd folder names resolve to PCECD`() {
        listOf(
            "pcenginecd",       // EmuDeck / Batocera
            "PC Engine CD",
            "tg-cd",            // EmuDeck's TurboGrafx-CD folder
            "TGCD",
            "turbografxcd",
            "PCECD",
        ).forEach { folder ->
            assertEquals(
                "folder '$folder' should resolve to PCECD",
                "PCECD",
                emulator.resolveSystemFromFolderName(folder)
            )
        }
    }

    @Test
    fun `hucard folder names still resolve to PCE`() {
        listOf("pcengine", "PC Engine", "tg16", "TurboGrafx", "PCE").forEach { folder ->
            assertEquals(
                "folder '$folder' should resolve to PCE",
                "PCE",
                emulator.resolveSystemFromFolderName(folder)
            )
        }
    }

    @Test
    fun `supergrafx is not swallowed by the CD branch`() {
        assertEquals("PCSG", emulator.resolveSystemFromFolderName("supergrafx"))
    }

    @Test
    fun `pc-fx folders and core resolve to PCFX, not PCE`() {
        listOf("pcfx", "PCFX", "PC-FX", "NEC - PC-FX").forEach { folder ->
            assertEquals(
                "folder '$folder' should resolve to PCFX",
                "PCFX",
                emulator.resolveSystemFromFolderName(folder)
            )
        }
        assertEquals("PCFX", emulator.resolveSystemFromCoreName("NEC - PC-FX (Beetle PC-FX)"))
        assertEquals("PCFX", SystemAliases.normalizeSystemCode("pcfx"))
        assertEquals(true, "PCFX" in RetroArchEmulator.CD_SYSTEMS)
    }

    @Test
    fun `PCECD is a CD system for the per-content-folder toggle`() {
        assertEquals(true, "PCECD" in RetroArchEmulator.CD_SYSTEMS)
    }

    @Test
    fun `server system spellings normalize to PCECD`() {
        listOf("PCECD", "PC Engine CD", "pcenginecd", "TG-CD", "TurboGrafx-CD").forEach { code ->
            assertEquals(
                "server code '$code' should normalize to PCECD",
                "PCECD",
                SystemAliases.normalizeSystemCode(code)
            )
        }
        assertEquals("PCE", SystemAliases.normalizeSystemCode("PC Engine"))
    }
}
