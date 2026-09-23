package com.savesync.android.emulators

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import java.io.File

/**
 * Dreamcast title IDs are the disc serial (`DC_T1249M`), matching
 * `shared/rom_id/dreamcast.py` and the folders MemCard PRO DC / openMenu create.
 * These assertions must stay in step with `shared/tests/test_sync_id.py`.
 */
class DreamcastSerialTest {

    @get:Rule
    val tmp = TemporaryFolder()

    // ------------------------------------------------------------------
    // Canonical serial
    // ------------------------------------------------------------------

    @Test
    fun `folds both spellings of a Sega serial onto one id`() {
        // The disc says MK-51000; the Redump DAT says 51000.
        assertEquals("51000", DreamcastSerial.canonical("MK-51000"))
        assertEquals("51000", DreamcastSerial.canonical("51000"))
        assertEquals("DC_51000", DreamcastSerial.titleId("MK-51000"))
        assertEquals("DC_51000", DreamcastSerial.titleId("51000"))
    }

    @Test
    fun `keeps regions apart`() {
        // MK-51064-50 is the PAL disc, MK-51064 the NTSC one.
        assertEquals("DC_5106450", DreamcastSerial.titleId("MK-51064-50"))
        assertEquals("DC_51064", DreamcastSerial.titleId("MK-51064"))
    }

    @Test
    fun `leaves third-party codes alone`() {
        assertEquals("DC_T1249M", DreamcastSerial.titleId("T-1249M"))
        assertEquals("DC_T3601N", DreamcastSerial.titleId("t-3601n"))
        assertEquals("DC_HDR0080", DreamcastSerial.titleId("HDR-0080"))
    }

    @Test
    fun `returns null for nothing usable`() {
        assertNull(DreamcastSerial.titleId(null))
        assertNull(DreamcastSerial.titleId("   "))
    }

    // ------------------------------------------------------------------
    // DAT parsing
    // ------------------------------------------------------------------

    private val dat = """
        clrmamepro (
            name "Sega - Dreamcast"
        )

        game (
            name "Capcom vs. SNK 2 - Millionaire Fighting 2001 (Japan)"
            region "Japan"
            serial "T-1249M"
            rom ( name "track03.bin" size 1185760800 serial "T-1249M" )
        )
        game (
            name "Sonic Adventure (USA)"
            region "USA"
            serial "51000"
            rom ( name "track03.bin" size 1185760800 serial "51000" )
        )
    """.trimIndent()

    @Test
    fun `parses game level serials and ignores the per-rom copy`() {
        val parsed = DreamcastSerialDatabase.parseDat(dat.byteInputStream())
        assertEquals(2, parsed.size)
        assertEquals("T-1249M", parsed["capcom vs. snk 2 - millionaire fighting 2001 (japan)"])
        assertEquals("51000", parsed["sonic adventure (usa)"])
    }

    // ------------------------------------------------------------------
    // IP.BIN reading
    // ------------------------------------------------------------------

    /** A 256-byte IP.BIN carrying [product], optionally offset into the sector. */
    private fun ipBin(product: String, leadingBytes: Int = 0): ByteArray {
        val out = ByteArray(leadingBytes + 256) { ' '.code.toByte() }
        for (i in 0 until leadingBytes) out[i] = 0
        fun put(offset: Int, text: String) {
            val bytes = text.toByteArray(Charsets.US_ASCII)
            System.arraycopy(bytes, 0, out, leadingBytes + offset, bytes.size)
        }
        put(0x00, "SEGA SEGAKATANA ")
        put(0x40, product)
        put(0x80, "TEST GAME")
        return out
    }

    @Test
    fun `reads the product number from a 2048 byte sector image`() {
        val iso = tmp.newFile("game.iso")
        iso.writeBytes(ipBin("T-1249M"))
        assertEquals("T-1249M", DreamcastDisc.readProductCode(iso))
    }

    @Test
    fun `reads the product number from a raw 2352 byte sector image`() {
        // Raw sectors put the payload after a 16-byte sync + header.
        val bin = tmp.newFile("track03.bin")
        bin.writeBytes(ipBin("MK-51000", leadingBytes = 16))
        assertEquals("MK-51000", DreamcastDisc.readProductCode(bin))
        assertEquals("DC_51000", DreamcastSerial.titleId(DreamcastDisc.readProductCode(bin)))
    }

    @Test
    fun `follows a gdi sheet to its data track`() {
        val dir = tmp.newFolder("game")
        File(dir, "track01.bin").writeBytes(ByteArray(4096))
        File(dir, "track03.bin").writeBytes(ipBin("T-3601N"))
        val gdi = File(dir, "disc.gdi")
        gdi.writeText(
            """
            2
            1 0 4 2352 track01.bin 0
            3 45000 4 2352 track03.bin 0
            """.trimIndent()
        )
        assertEquals("T-3601N", DreamcastDisc.readProductCode(gdi))
    }

    @Test
    fun `handles quoted track names in a gdi sheet`() {
        val dir = tmp.newFolder("quoted")
        File(dir, "Some Game (USA)03.bin").writeBytes(ipBin("T-8106N"))
        val gdi = File(dir, "Some Game (USA).gdi")
        gdi.writeText(
            """
            1
            3 45000 4 2352 "Some Game (USA)03.bin" 0
            """.trimIndent()
        )
        assertEquals("T-8106N", DreamcastDisc.readProductCode(gdi))
    }

    @Test
    fun `returns null for an image with no dreamcast header`() {
        val chd = tmp.newFile("game.chd")
        chd.writeBytes(ByteArray(8192))
        assertNull(DreamcastDisc.readProductCode(chd))
    }
}
