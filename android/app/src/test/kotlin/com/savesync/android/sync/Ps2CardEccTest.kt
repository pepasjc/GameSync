package com.savesync.android.sync

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import java.io.File

/**
 * Expected values come from ``server/app/services/ps2_cards.py`` — the client
 * hash has to match what the server reports for ``ps2-card/meta?format=ps2``.
 */
class Ps2CardEccTest {

    @get:Rule
    val tmp = TemporaryFolder()

    private fun hex(b: ByteArray) = b.joinToString("") { "%02x".format(it) }

    private val patternPage = ByteArray(512) { i -> ((i * i * 31 + i * 17 + 5) and 0xFF).toByte() }

    /** Server: sha256(add_ecc(card)) for an FF card whose first page is [patternPage]. */
    private val serverHash = "3522294943dcd2967eb2e3fa6a7317eece5560469269beec6b2e380c2d6c819d"

    @Test
    fun `page spare matches server ECC`() {
        assertEquals("777f7f777f7f777f7f777f7f00000000", hex(Ps2CardEcc.pageSpare(ByteArray(512) { 0xFF.toByte() })))
        val single = ByteArray(512).also { it[37] = 1 }
        assertEquals("70255a777f7f777f7f777f7f00000000", hex(Ps2CardEcc.pageSpare(single)))
        assertEquals("07186707186707186707186700000000", hex(Ps2CardEcc.pageSpare(patternPage)))
    }

    private fun writeCard(name: String, spare: (ByteArray) -> ByteArray?): File {
        val file = tmp.newFile(name)
        file.outputStream().buffered().use { out ->
            val erased = ByteArray(512) { 0xFF.toByte() }
            for (p in 0 until Ps2CardEcc.PAGES_PER_CARD) {
                val page = if (p == 0) patternPage else erased
                out.write(page)
                spare(page)?.let { out.write(it) }
            }
        }
        return file
    }

    @Test
    fun `ps2 card with FF spares hashes like the server`() {
        // Freshly formatted PCSX2/ARMSX2 cards leave erased pages' spares as FF.
        val card = writeCard("ff.ps2") { ByteArray(16) { 0xFF.toByte() } }
        assertEquals(serverHash, Ps2CardEcc.normalizedHash(card))
    }

    @Test
    fun `ps2 card with correct ECC and raw mc2 card hash the same`() {
        val ecc = writeCard("ecc.ps2") { Ps2CardEcc.pageSpare(it) }
        val mc2 = writeCard("raw.mc2") { null }
        assertEquals(serverHash, Ps2CardEcc.normalizedHash(ecc))
        assertEquals(serverHash, Ps2CardEcc.normalizedHash(mc2))
        assertEquals(HashUtils.sha256File(ecc), Ps2CardEcc.normalizedHash(ecc))
    }

    @Test
    fun `non card sizes are not normalized`() {
        val file = tmp.newFile("odd.ps2").apply { writeBytes(ByteArray(1024)) }
        assertNull(Ps2CardEcc.normalizedHash(file))
    }
}
