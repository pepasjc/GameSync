package com.savesync.android.catalog

import com.savesync.android.api.RomEntry
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/** Mirrors mister/tests/test_ra_variant.py — the logic is shared/ra_variants.py. */
class RaVariantsTest {

    private fun row(
        name: String,
        system: String = "DC",
        ach: Int? = null,
        match: String? = null,
        gameId: Int? = null,
        dump: Boolean? = null,
        hash: String? = null,
    ) = RomEntry(
        rom_id = name, title_id = name, system = system, name = name,
        filename = "$name.chd", path = "x/$name.chd", size = 1000,
        raAchievements = ach, raMatch = match, raGameId = gameId, raDump = dump, raHash = hash,
    )

    private fun europe() = row("Soulcalibur (Europe) (En,Fr,De,Es)", ach = 88, match = "title", gameId = 3395, dump = true)
    private fun usa() = row("Soulcalibur (USA)", ach = 88, match = "title", gameId = 3395)

    private fun variants(catalog: List<RomEntry>, installed: List<Pair<String, String>>, which: Int = 0): List<String> {
        val names = installed.map { (sys, n) -> sys to RaVariants.nameKey(n) }.toSet()
        val (sys, name) = installed[which]
        return RaVariantIndex(catalog).variants(sys, name, names).map { it.name }
    }

    @Test
    fun `verified means hash or registered dump`() {
        assertTrue(RaVariants.isVerified(europe()))
        assertTrue(!RaVariants.isVerified(usa()))
        assertTrue(RaVariants.isVerified(row("A", system = "SNES", ach = 5)))
        assertTrue(!RaVariants.isVerified(row("A", ach = 0, dump = true)))
    }

    @Test
    fun `usa copy is offered the europe version`() {
        assertEquals(listOf(europe().name), variants(listOf(usa(), europe()), listOf("DC" to "Soulcalibur (USA)")))
    }

    @Test
    fun `spelling differences still find the variant`() {
        val catalog = listOf(row("Soul Calibur (USA)"), europe())
        assertEquals(listOf(europe().name), variants(catalog, listOf("DC" to "Soul Calibur (USA)")))
    }

    @Test
    fun `nothing when installed copy is verified or ra version installed too`() {
        assertEquals(emptyList<String>(), variants(listOf(europe(), usa()), listOf("DC" to europe().name)))
        assertEquals(
            emptyList<String>(),
            variants(listOf(usa(), europe()), listOf("DC" to "Soulcalibur (USA)", "DC" to europe().name)),
        )
    }

    @Test
    fun `unknown games, hacks, bonus discs and multi-disc games are left alone`() {
        assertEquals(emptyList<String>(), variants(listOf(europe()), listOf("DC" to "Soulcalibur (USA)")))
        val snes = listOf(row("Game (Japan)", "SNES"), row("Game (Japan) [T-En by X]", "SNES", ach = 10), row("Game (USA)", "SNES", ach = 10))
        assertEquals(listOf("Game (USA)"), variants(snes, listOf("SNES" to "Game (Japan)")))
        assertEquals(emptyList<String>(), variants(snes, listOf("SNES" to "Game (Japan) [T-En by X]")))
        val ps1 = listOf(row("Persona 2 (USA) (Bonus Disc)", "PS1"), row("Persona 2 (USA)", "PS1", ach = 40))
        assertEquals(emptyList<String>(), variants(ps1, listOf("PS1" to "Persona 2 (USA) (Bonus Disc)")))
        val discs = listOf(row("Skies (USA) (Disc 1)", match = "title", ach = 5), row("Skies (Europe) (Disc 1)", ach = 5))
        assertEquals(emptyList<String>(), variants(discs, listOf("DC" to "Skies (USA) (Disc 1)")))
    }

    @Test
    fun `translations swap only within their language`() {
        val mine = "Super Aleste (Japan) [T-En by Asiwish v1.2] [FastROM hack]"
        val aleste = listOf(
            row(mine, "SNES"),
            row("Super Aleste (Japan) [T-En v1.0 tettsui77]", "SNES", ach = 30),
            row("Super Aleste (Japan)", "SNES", ach = 30),
        )
        assertEquals(listOf("Super Aleste (Japan) [T-En v1.0 tettsui77]"), variants(aleste, listOf("SNES" to mine)))
        // Never back to the untranslated original...
        val psycho = listOf(row("Psycho Dream (Japan) [T-En by X]", "SNES"), row("Psycho Dream (Japan)", "SNES", ach = 9))
        assertEquals(emptyList<String>(), variants(psycho, listOf("SNES" to "Psycho Dream (Japan) [T-En by X]")))
        // ...but an English retranslation may go back to the English release.
        val sky = listOf(row("Skyblazer (USA) [T-En by K] [Relocalized]", "SNES"), row("Skyblazer (USA)", "SNES", ach = 20))
        assertEquals(listOf("Skyblazer (USA)"), variants(sky, listOf("SNES" to "Skyblazer (USA) [T-En by K] [Relocalized]")))
        // Retail is offered a translation only when RA supports no retail version.
        val goemon = listOf(row("Goemon 3 (Japan)", "SNES"), row("Goemon 3 (Japan) [T-En v4.0]", "SNES", ach = 40))
        assertEquals(listOf("Goemon 3 (Japan) [T-En v4.0]"), variants(goemon, listOf("SNES" to "Goemon 3 (Japan)")))
        val both = goemon + row("Goemon 3 (USA)", "SNES", ach = 40)
        assertEquals(listOf("Goemon 3 (USA)"), variants(both, listOf("SNES" to "Goemon 3 (Japan)")))
        // A plain hack is never swapped.
        val smw = listOf(row("Super Mario World (USA) [Hack by X]", "SNES"), row("Super Mario World (USA)", "SNES", ach = 90))
        assertEquals(emptyList<String>(), variants(smw, listOf("SNES" to "Super Mario World (USA) [Hack by X]")))
    }

    @Test
    fun `different ra games and other systems do not count`() {
        val catalog = listOf(
            row("Game (USA)", "PS1", ach = 5, match = "title", gameId = 1),
            row("Game (Europe)", "PS1", ach = 5, gameId = 2),
        )
        assertEquals(emptyList<String>(), variants(catalog, listOf("PS1" to "Game (USA)")))
        assertEquals(
            emptyList<String>(),
            variants(listOf(usa(), row("Soulcalibur (Europe)", "PS1", ach = 5)), listOf("DC" to "Soulcalibur (USA)")),
        )
    }

    @Test
    fun `usa and world come before other regions`() {
        assertTrue(RaVariants.regionRank("Game (World)") < RaVariants.regionRank("Game (USA)"))
        assertTrue(RaVariants.regionRank("Game (USA)") < RaVariants.regionRank("Game (Europe) (En,Fr)"))
        assertEquals(RaVariants.regionRank("Game (USA)"), RaVariants.regionRank("Game (Japan, USA)"))
        assertEquals("soulcalibur", RaVariants.titleKey("Soul Calibur (USA).chd"))
        assertEquals("castlevaniasymphonyofnight", RaVariants.titleKey("Castlevania - Symphony of the Night (USA)"))
    }

    @Test
    fun `installs the catalogue cannot name are judged by their hash`() {
        val ras = row("Front Mission (Japan) [T-En by Frank Hughes v1.0b]", "SNES", ach = 50, gameId = 2895, hash = "9a8e7a91")
        val index = RaVariantIndex(listOf(ras))
        val mine = "Front Mission (Japan) [T-En by Frank Hughes v1.0b] [Splash screen removed]"
        assertEquals(listOf(ras.name), index.variants("SNES", mine, emptySet()) { "d79d9c07" }.map { it.name })
        assertEquals(emptyList<String>(), index.variants("SNES", mine, emptySet()) { "9A8E7A91" }.map { it.name })
        assertEquals(emptyList<String>(), index.variants("SNES", mine, emptySet()).map { it.name })
    }

    @Test
    fun `versions are linked by ra game when names differ`() {
        val mine = row("Front Mission Series - Gun Hazard (Japan) [T-En by Aeon Genesis v1.01]", "SNES", ach = 0, match = "title", gameId = 1408)
        val ras = row("Front Mission - Gun Hazard (Japan) [T-En Aeon Genesis 1.01]", "SNES", ach = 112, gameId = 1408, hash = "fd473bc0")
        assertEquals(listOf(ras.name), RaVariantIndex(listOf(mine, ras)).variants("SNES", mine.name, emptySet()).map { it.name })
    }
}
