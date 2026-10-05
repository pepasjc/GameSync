package com.savesync.android.catalog

import com.savesync.android.api.RomEntry
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import java.io.File
import java.io.IOException

/**
 * Mirrors shared/tests for catalog_cache.py: plan / put / drop and the
 * on-disk round trip, plus the load strategy (fresh vs stale, offline,
 * pre-fingerprint server).
 */
class CatalogCacheTest {

    @get:Rule
    val tmp = TemporaryFolder()

    private fun cacheFile(): File = File(tmp.root, CatalogCache.FILE_NAME)

    private fun rom(system: String, name: String, raAchievements: Int? = null) = RomEntry(
        rom_id = "$system-$name",
        title_id = "${system}_$name",
        system = system,
        name = name,
        filename = "$name.bin",
        path = "$system/$name.bin",
        size = 1234,
        raAchievements = raAchievements,
        extractFormats = listOf("cue"),
    )

    @Test
    fun `plan splits fresh and stale and drops vanished systems`() {
        val cache = CatalogCache(cacheFile())
        cache.put("SNES", "a1", listOf(rom("SNES", "Zelda")))
        cache.put("GBA", "b1", listOf(rom("GBA", "Minish")))
        cache.put("N64", "c1", listOf(rom("N64", "Mario")))

        val plan = cache.plan(mapOf("SNES" to "a1", "GBA" to "b2", "PS1" to "d1"))

        assertEquals(listOf("SNES"), plan.fresh)
        assertEquals(listOf("GBA", "PS1"), plan.stale.sorted())
        // N64 is no longer on the server.
        assertEquals(listOf("GBA", "SNES"), cache.systems())
        assertNull(cache.fingerprint("N64"))
    }

    @Test
    fun `plan with explicit wanted list drops wanted systems the server lacks`() {
        val cache = CatalogCache(cacheFile())
        cache.put("SNES", "a1", listOf(rom("SNES", "Zelda")))
        val plan = cache.plan(mapOf("GBA" to "x"), wanted = listOf("SNES", "GBA"))
        assertEquals(emptyList<String>(), plan.fresh)
        assertEquals(listOf("GBA"), plan.stale)
        assertTrue(cache.systems().isEmpty())
    }

    @Test
    fun `put replaces a system and drop removes it`() {
        val cache = CatalogCache(cacheFile())
        cache.put("SNES", "a1", listOf(rom("SNES", "Zelda")))
        cache.put("SNES", "a2", listOf(rom("SNES", "Metroid"), rom("SNES", "Mana")))
        assertEquals("a2", cache.fingerprint("SNES"))
        assertEquals(2, cache.rowCount)
        cache.drop("SNES")
        assertTrue(cache.isEmpty())
        cache.drop("SNES") // no-op
    }

    @Test
    fun `save and load round trip keeps rows and fingerprints`() {
        val cache = CatalogCache(cacheFile())
        cache.put("SNES", "a1", listOf(rom("SNES", "Zelda", raAchievements = 42)))
        cache.put("GBA", "b1", listOf(rom("GBA", "Minish")))
        assertTrue(cache.save())
        assertTrue(cacheFile().isFile)
        assertFalse(File(cacheFile().path + ".part").exists())

        val reloaded = CatalogCache(cacheFile())
        assertEquals(listOf("GBA", "SNES"), reloaded.systems())
        assertEquals("a1", reloaded.fingerprint("SNES"))
        val zelda = reloaded.rows("SNES").single()
        assertEquals("Zelda", zelda.name)
        assertEquals(42, zelda.raAchievements)
        assertEquals(listOf("cue"), zelda.extractFormats)
        assertEquals(1234L, zelda.size)
        // allRows: systems sorted, GBA first.
        assertEquals(listOf("Minish", "Zelda"), reloaded.allRows().map { it.name })
    }

    @Test
    fun `other version or corrupt file is ignored`() {
        val old = CatalogCache(cacheFile(), version = CatalogCache.VERSION + 1)
        old.put("SNES", "a1", listOf(rom("SNES", "Zelda")))
        assertTrue(old.save())
        assertTrue(CatalogCache(cacheFile()).isEmpty())

        cacheFile().writeText("{not json")
        assertTrue(CatalogCache(cacheFile()).isEmpty())
    }

    @Test
    fun `clear is persisted`() {
        val cache = CatalogCache(cacheFile())
        cache.put("SNES", "a1", listOf(rom("SNES", "Zelda")))
        cache.save()
        cache.clear()
        cache.save()
        assertTrue(CatalogCache(cacheFile()).isEmpty())
    }

    @Test
    fun `load refetches only stale systems`() = runBlocking {
        val cache = CatalogCache(cacheFile())
        cache.put("SNES", "a1", listOf(rom("SNES", "Zelda")))
        cache.put("GBA", "b1", listOf(rom("GBA", "Old")))
        cache.save()

        val fetched = mutableListOf<String?>()
        val result = loadCatalog(
            CatalogCache(cacheFile()),
            fetchFingerprints = { mapOf("SNES" to "a1", "GBA" to "b2") },
            fetchSystem = { system ->
                fetched += system
                // A stray row from another system is filtered out.
                listOf(rom("GBA", "New"), rom("SNES", "Stray"))
            },
        )
        assertEquals(listOf<String?>("GBA"), fetched)
        assertEquals(CatalogSource.CACHE, result.source)
        assertEquals(1, result.refreshed)
        assertEquals(1, result.unchanged)
        assertEquals(listOf("New", "Zelda"), result.rows.map { it.name })
        // Persisted: a second load with the same fingerprints fetches nothing.
        val again = loadCatalog(
            CatalogCache(cacheFile()),
            fetchFingerprints = { mapOf("SNES" to "a1", "GBA" to "b2") },
            fetchSystem = { fail("nothing is stale"); emptyList() },
        )
        assertEquals(listOf("New", "Zelda"), again.rows.map { it.name })
    }

    @Test
    fun `unreachable server falls back to the cached copy`() = runBlocking {
        val cache = CatalogCache(cacheFile())
        cache.put("SNES", "a1", listOf(rom("SNES", "Zelda")))
        val result = loadCatalog(
            cache,
            fetchFingerprints = { throw IOException("connection refused") },
            fetchSystem = { fail("offline"); emptyList() },
        )
        assertEquals(CatalogSource.OFFLINE, result.source)
        assertEquals(listOf("Zelda"), result.rows.map { it.name })
    }

    @Test
    fun `unreachable server with empty cache propagates the error`() {
        runBlocking {
            try {
                loadCatalog(
                    CatalogCache(cacheFile()),
                    fetchFingerprints = { throw IOException("connection refused") },
                    fetchSystem = { emptyList() },
                )
                fail("expected IOException")
            } catch (_: IOException) {
                // expected
            }
        }
    }

    @Test
    fun `pre-fingerprint server is fetched whole and nothing is cached`() = runBlocking {
        val cache = CatalogCache(cacheFile())
        val fetched = mutableListOf<String?>()
        val result = loadCatalog(
            cache,
            fetchFingerprints = { null },
            fetchSystem = { system ->
                fetched += system
                listOf(rom("SNES", "Zelda"), rom("GBA", "Minish"))
            },
        )
        assertEquals(listOf<String?>(null), fetched)
        assertEquals(CatalogSource.LIVE, result.source)
        assertEquals(2, result.rows.size)
        assertTrue(cache.isEmpty())
        assertFalse(cacheFile().exists())
    }
}
