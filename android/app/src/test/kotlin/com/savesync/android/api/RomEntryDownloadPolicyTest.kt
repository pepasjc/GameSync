package com.savesync.android.api

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class RomEntryDownloadPolicyTest {

    private fun rom(
        system: String,
        extractFormat: String? = null,
        extractFormats: List<String> = emptyList(),
    ) = RomEntry(
        rom_id = "rom-1",
        title_id = "title-1",
        system = system,
        name = "Example",
        filename = "Example.rom",
        path = "Example.rom",
        size = 123,
        extractFormat = extractFormat,
        extractFormats = extractFormats,
    )

    @Test
    fun `3DS prefers decrypted CCI for emulator downloads`() {
        val entry = rom(
            system = "3DS",
            extractFormat = "3ds",
            extractFormats = listOf("cia", "decrypted_cci"),
        )

        assertEquals("decrypted_cci", entry.preferredDownloadExtractFormat())
    }

    @Test
    fun `3DS does not fall back to CIA outputs`() {
        assertNull(
            rom(system = "3DS", extractFormats = listOf("cia"))
                .preferredDownloadExtractFormat(),
        )
    }

    @Test
    fun `non-3DS non-Xbox systems ignore server extract hints`() {
        val entry = rom(system = "PS1", extractFormat = "cue")
        assertNull(entry.preferredDownloadExtractFormat())
    }

    @Test
    fun `Xbox always requests ISO for xemu compatibility`() {
        val entry = rom(
            system = "XBOX",
            extractFormat = "xbox",
            extractFormats = listOf("cci", "iso", "folder"),
        )
        assertEquals("iso", entry.preferredDownloadExtractFormat())
    }

    @Test
    fun `Xbox requests ISO even without extract_formats advertised`() {
        val entry = rom(system = "XBOX")
        assertEquals("iso", entry.preferredDownloadExtractFormat())
    }

    @Test
    fun `X360 also requests ISO`() {
        val entry = rom(
            system = "X360",
            extractFormats = listOf("cci", "iso", "folder"),
        )
        assertEquals("iso", entry.preferredDownloadExtractFormat())
    }
}
