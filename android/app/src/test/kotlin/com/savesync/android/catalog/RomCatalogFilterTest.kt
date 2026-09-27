package com.savesync.android.catalog

import com.savesync.android.api.RomEntry
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class RomCatalogFilterTest {

    private fun rom(
        titleId: String,
        system: String,
        name: String,
        filename: String,
        size: Long = 0,
    ) = RomEntry(
        rom_id = "$system-$titleId",
        title_id = titleId,
        system = system,
        name = name,
        filename = filename,
        path = "$system/$filename",
        size = size,
    )

    private val catalog = listOf(
        rom("SLUS01324", "PS1", "Breath of Fire IV (USA)", "Breath of Fire IV (USA).chd"),
        rom("SLUS01041", "PS1", "Final Fantasy VII (USA) (Disc 1)", "Final Fantasy VII (USA) (Disc 1).chd"),
        rom("SAT_T-4507G", "SAT", "Grandia (Japan) (Disc 1)", "Grandia (Japan) (Disc 1) (4M).chd"),
        rom("GBA_pokemon_emerald", "GBA", "Pokemon Emerald (USA)", "Pokemon - Emerald Version (USA).gba"),
        rom("NDS_chrono_trigger", "NDS", "Chrono Trigger (USA)", "Chrono Trigger (USA) (En,Fr).nds"),
    )

    @Test
    fun `empty query returns everything sorted by system`() {
        val result = RomCatalogFilter.filter(catalog)
        assertEquals(catalog.size, result.size)
        // Sorted by system code — GBA comes before PS1 alphabetically.
        assertEquals("GBA", result.first().system)
    }

    @Test
    fun `system filter narrows to just that platform`() {
        val result = RomCatalogFilter.filter(catalog, system = "PS1")
        assertEquals(setOf("SLUS01324", "SLUS01041"), result.map { it.title_id }.toSet())
    }

    @Test
    fun `token order does not matter`() {
        val result = RomCatalogFilter.filter(catalog, query = "fire breath")
        assertEquals(1, result.size)
        assertEquals("SLUS01324", result[0].title_id)
    }

    @Test
    fun `arabic numeral matches a roman catalog entry`() {
        val result = RomCatalogFilter.filter(catalog, query = "final fantasy 7")
        assertEquals(listOf("SLUS01041"), result.map { it.title_id })
    }

    @Test
    fun `roman numeral matches arabic query form too`() {
        val result = RomCatalogFilter.filter(catalog, query = "breath of fire iv")
        assertEquals(listOf("SLUS01324"), result.map { it.title_id })
    }

    @Test
    fun `region tag stripping lets plain names match`() {
        val result = RomCatalogFilter.filter(catalog, query = "chrono trigger")
        assertEquals(listOf("NDS"), result.map { it.system })
    }

    @Test
    fun `partial product code fragment matches title id`() {
        val result = RomCatalogFilter.filter(catalog, query = "slus013")
        assertEquals(setOf("SLUS01324"), result.map { it.title_id }.toSet())
    }

    @Test
    fun `usa token picks up every usa rom`() {
        val systems = RomCatalogFilter.filter(catalog, query = "usa").map { it.system }.toSet()
        assertTrue("PS1" in systems)
        assertTrue("GBA" in systems)
        assertFalse("SAT" in systems)  // Grandia is Japan
    }

    @Test
    fun `matches respects system filter even when name matches`() {
        val r = rom("SLUS01324", "PS1", "Breath of Fire IV (USA)", "bof4.chd")
        assertTrue(RomCatalogFilter.matches(r, "breath", system = "PS1"))
        assertFalse(RomCatalogFilter.matches(r, "breath", system = "GBA"))
    }

    @Test
    fun `uniqueSystems returns sorted dedup list`() {
        assertEquals(listOf("GBA", "NDS", "PS1", "SAT"), RomCatalogFilter.uniqueSystems(catalog))
    }

    // ── RA only (X on the catalog) ───────────────────────────────────────

    private val raCatalog = listOf(
        rom("SLUS01324", "PS1", "Breath of Fire IV (USA)", "Breath of Fire IV (USA).chd")
            .copy(raAchievements = 57, raMatch = "title"),
        rom("GBA_pokemon_emerald", "GBA", "Pokemon Emerald (USA)", "Pokemon - Emerald Version (USA).gba")
            .copy(raAchievements = 81, raMatch = "hash"),
        // Registered with RA but no published set: promises nothing.
        rom("GBA_no_set", "GBA", "No Set (USA)", "No Set (USA).gba")
            .copy(raAchievements = 0, raMatch = "hash"),
        // Server had no RA key and could not read the count.
        rom("NDS_unknown", "NDS", "Unknown (USA)", "Unknown (USA).nds")
            .copy(raAchievements = -1),
        rom("SAT_T-4507G", "SAT", "Grandia (Japan) (Disc 1)", "Grandia (Japan) (Disc 1) (4M).chd"),
    )

    @Test
    fun `raOnly keeps games with a published set, title matches included`() {
        val result = RomCatalogFilter.filter(raCatalog, raOnly = true)
        assertEquals(listOf("GBA_pokemon_emerald", "SLUS01324"), result.map { it.title_id })
    }

    @Test
    fun `raOnly off shows everything`() {
        assertEquals(raCatalog.size, RomCatalogFilter.filter(raCatalog).size)
    }

    @Test
    fun `raOnly stacks with system and search`() {
        assertEquals(
            listOf("SLUS01324"),
            RomCatalogFilter.filter(raCatalog, system = "PS1", raOnly = true).map { it.title_id },
        )
        assertTrue(RomCatalogFilter.filter(raCatalog, query = "grandia", raOnly = true).isEmpty())
    }

    @Test
    fun `raOnly systems list skips systems without an RA game`() {
        assertEquals(listOf("GBA", "PS1"), RomCatalogFilter.uniqueSystems(raCatalog, raOnly = true))
        assertEquals(listOf("GBA", "NDS", "PS1", "SAT"), RomCatalogFilter.uniqueSystems(raCatalog))
    }
}
