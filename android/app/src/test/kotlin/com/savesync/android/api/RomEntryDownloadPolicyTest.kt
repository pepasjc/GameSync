package com.savesync.android.api

import com.google.gson.Gson
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class RomEntryDownloadPolicyTest {

    private fun rom(
        system: String,
        extractFormat: String? = null,
        extractFormats: List<String> = emptyList(),
        isBundle: Boolean = false,
        filename: String = "Example.rom",
    ) = RomEntry(
        rom_id = "rom-1",
        title_id = "title-1",
        system = system,
        name = "Example",
        filename = filename,
        path = filename,
        size = 123,
        extractFormat = extractFormat,
        extractFormats = extractFormats,
        isBundle = isBundle,
    )

    @Test
    fun `3DS prefers decrypted CCI for emulator downloads`() {
        val entry = rom(
            system = "3DS",
            extractFormat = "3ds",
            extractFormats = listOf("cia", "decrypted_cci"),
            // The policy only converts sources it can convert (.3ds/.cci/
            // .zip); the generic "Example.rom" fixture is not one of them.
            filename = "Example.3ds",
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

    @Test
    fun `Wii U takes the raw WUP bundle when no decrypter is advertised`() {
        // Cemu decrypts a WUP set itself from the bundled ticket, so the raw
        // bundle is usable as-is.  Requesting a format the server never
        // advertised would only earn a 503.
        val entry = rom(
            system = "WIIU",
            isBundle = true,
            filename = "Super Mario 3D World (USA).zip",
        )
        assertNull(entry.preferredDownloadExtractFormat())
    }

    @Test
    fun `Wii U uses loadiine only when the server advertises it`() {
        val entry = rom(
            system = "WIIU",
            extractFormats = listOf("loadiine", "wua"),
            isBundle = true,
            filename = "Super Mario 3D World (USA).zip",
        )
        assertEquals("loadiine", entry.preferredDownloadExtractFormat())
        assertEquals(
            "Super Mario 3D World (USA).zip",
            entry.preferredDownloadFilename("loadiine"),
        )
    }

    @Test
    fun `Wii U single-file image needs no conversion`() {
        val entry = rom(system = "WIIU", filename = "Bayonetta 2.wua")
        assertNull(entry.preferredDownloadExtractFormat())
        assertEquals("Bayonetta 2.wua", entry.preferredDownloadFilename(null))
    }

    /**
     * Regression: Gson instantiates via Unsafe and skips the Kotlin
     * constructor, so a `= emptyList()` default is NOT applied when the key is
     * missing from the JSON.  A non-null-typed access then NPEs on the very
     * first call.  The server omits `extract_formats` whenever it has nothing
     * to advertise — e.g. a Wii U bundle on a host with no decrypter — which
     * crashed the catalog screen the moment Download was tapped.
     */
    @Test
    fun `absent extract_formats deserializes without crashing`() {
        val json = """
            {"rom_id":"0005000010145C00","title_id":"0005000010145C00",
             "system":"WIIU","name":"Super Mario 3D World (USA)",
             "filename":"Super Mario 3D World (USA).zip",
             "path":"wiiu/Super Mario 3D World (USA)","size":1752408756,
             "is_bundle":true}
        """.trimIndent()
        val entry = Gson().fromJson(json, RomEntry::class.java)

        assertEquals(emptyList<String>(), entry.extractFormatList)
        assertNull(entry.preferredDownloadExtractFormat())
    }

    @Test
    fun `present extract_formats survives deserialization`() {
        val json = """
            {"rom_id":"r","title_id":"t","system":"WIIU","name":"n",
             "filename":"n.zip","path":"p","size":1,"is_bundle":true,
             "extract_formats":["loadiine","wua"]}
        """.trimIndent()
        val entry = Gson().fromJson(json, RomEntry::class.java)

        assertEquals(listOf("loadiine", "wua"), entry.extractFormatList)
        assertEquals("loadiine", entry.preferredDownloadExtractFormat())
    }
}
