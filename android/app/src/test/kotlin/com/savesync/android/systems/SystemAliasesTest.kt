package com.savesync.android.systems

import org.junit.Assert.assertEquals
import org.junit.Test

class SystemAliasesTest {

    // --- normalizeSystemCode ---------------------------------------------

    @Test
    fun `SCD normalises to SEGACD`() {
        // Regression: the server emitted "SCD" for Sega CD ROMs and the
        // download path landed them in ``roms/SCD`` instead of the user's
        // ``roms/segacd`` folder that InstalledRomsScanner was walking.
        assertEquals("SEGACD", SystemAliases.normalizeSystemCode("SCD"))
        assertEquals("SEGACD", SystemAliases.normalizeSystemCode("scd"))
        assertEquals("SEGACD", SystemAliases.normalizeSystemCode("MegaCD"))
        assertEquals("SEGACD", SystemAliases.normalizeSystemCode("Mega-CD"))
    }

    @Test
    fun `GEN and MEGADRIVE normalise to MD`() {
        assertEquals("MD", SystemAliases.normalizeSystemCode("GEN"))
        assertEquals("MD", SystemAliases.normalizeSystemCode("Genesis"))
        assertEquals("MD", SystemAliases.normalizeSystemCode("Mega Drive"))
        assertEquals("MD", SystemAliases.normalizeSystemCode("mega-drive"))
        assertEquals("MD", SystemAliases.normalizeSystemCode("Mega_Drive"))
        assertEquals("MD", SystemAliases.normalizeSystemCode("MEGADRIVE"))
    }

    @Test
    fun `WS and WSC normalise to WSWAN family`() {
        assertEquals("WSWAN", SystemAliases.normalizeSystemCode("WS"))
        assertEquals("WSWANC", SystemAliases.normalizeSystemCode("WSC"))
    }

    @Test
    fun `legacy ATARI codes normalise to short canonical codes`() {
        assertEquals("A2600", SystemAliases.normalizeSystemCode("ATARI2600"))
        assertEquals("A5200", SystemAliases.normalizeSystemCode("ATARI5200"))
        assertEquals("A7800", SystemAliases.normalizeSystemCode("ATARI7800"))
        assertEquals("A800", SystemAliases.normalizeSystemCode("ATARI800"))
    }

    @Test
    fun `PPSSPP normalises to PSP`() {
        // PPSSPP was the old Android system name for PSP; legacy saves
        // on the server carry that code.
        assertEquals("PSP", SystemAliases.normalizeSystemCode("PPSSPP"))
    }

    @Test
    fun `canonical codes pass through uppercased`() {
        assertEquals("PS1", SystemAliases.normalizeSystemCode("PS1"))
        assertEquals("PS1", SystemAliases.normalizeSystemCode("ps1"))
        assertEquals("GBA", SystemAliases.normalizeSystemCode("gba"))
    }

    @Test
    fun `blank input returns blank`() {
        assertEquals("", SystemAliases.normalizeSystemCode(""))
        assertEquals("   ", SystemAliases.normalizeSystemCode("   "))
        assertEquals("", SystemAliases.normalizeSystemCode(null))
    }

    // --- canonicalOrSelf -------------------------------------------------

    @Test
    fun `canonicalOrSelf rewrites aliases but preserves case for non-aliases`() {
        // Back-compat semantics used by MainViewModel's priority / dedup
        // logic: when no alias hit, return the input verbatim so
        // ``prefix == canonicalOrSelf(prefix)`` remains a valid probe for
        // "is this prefix already canonical?".
        assertEquals("SEGACD", SystemAliases.canonicalOrSelf("SCD"))
        assertEquals("MD", SystemAliases.canonicalOrSelf("GENESIS"))
        // Non-alias input comes back untouched (case preserved).
        assertEquals("PS1", SystemAliases.canonicalOrSelf("PS1"))
        assertEquals("ps1", SystemAliases.canonicalOrSelf("ps1"))
        assertEquals("md", SystemAliases.canonicalOrSelf("md"))
    }

    // --- reverse map -----------------------------------------------------

    @Test
    fun `CANONICAL_TO_SERVER collects every alias for MD`() {
        val aliases = SystemAliases.CANONICAL_TO_SERVER["MD"].orEmpty()
        // Don't over-specify the full list — just ensure the important
        // legacy spellings are all represented so remapping titleId
        // prefixes on the server hits every historical variant.
        assertEquals(true, "GEN" in aliases)
        assertEquals(true, "GENESIS" in aliases)
        assertEquals(true, "MEGADRIVE" in aliases)
    }

    @Test
    fun `CANONICAL_TO_SERVER collects SCD under SEGACD`() {
        val aliases = SystemAliases.CANONICAL_TO_SERVER["SEGACD"].orEmpty()
        assertEquals(true, "SCD" in aliases)
    }
}
