package com.savesync.android.sync

import com.savesync.android.api.RomEntry
import com.savesync.android.api.msuPackKind
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** Mirrors `shared/tests/test_msu.py` — keep the cases in step. */
class MsuPackTest {

    @Test
    fun `wrapping folder is hoisted and junk dropped`() {
        val plan = MsuPack.planExtraction(
            listOf(
                "Pack/", "Pack/Game.sfc", "Pack/Game.msu", "Pack/Game-1.pcm",
                "Pack/Game.srm", "Pack/../evil.sfc",
            )
        )
        assertEquals(
            listOf(
                "Pack/Game.sfc" to "Game.sfc",
                "Pack/Game.msu" to "Game.msu",
                "Pack/Game-1.pcm" to "Game-1.pcm",
            ),
            plan,
        )
    }

    @Test
    fun `rootless pack maps onto itself`() {
        val plan = MsuPack.planExtraction(listOf("G.md", "G.cue", "G.bin", "Thumbs.db"))
        assertEquals(listOf("G.md" to "G.md", "G.cue" to "G.cue", "G.bin" to "G.bin"), plan)
    }

    @Test
    fun `common root needs every member under one folder`() {
        assertEquals("Pack", MsuPack.commonRoot(listOf("Pack/", "Pack/a", "Pack/b")))
        assertEquals("", MsuPack.commonRoot(listOf("A/x", "B/y")))
        assertEquals("", MsuPack.commonRoot(listOf("A/x", "y")))
        assertEquals("", MsuPack.commonRoot(emptyList()))
    }

    @Test
    fun `junk is by extension or well-known name`() {
        assertTrue(MsuPack.isJunk("Game.srm"))
        assertTrue(MsuPack.isJunk("bb_msu.asm"))
        assertTrue(MsuPack.isJunk("Thumbs.db"))
        assertTrue(!MsuPack.isJunk("Game.bml"))
        assertTrue(!MsuPack.isJunk("Game-12.pcm"))
    }

    private fun rom(isBundle: Boolean, kind: String?) = RomEntry(
        rom_id = "r", title_id = "t", system = "SNES", name = "n",
        filename = "n.zip", path = "snes/n.zip", size = 1,
        isBundle = isBundle, bundleKind = kind,
    )

    @Test
    fun `only a bundle with a known kind is a pack`() {
        assertEquals("msu1", rom(true, "msu1").msuPackKind)
        assertEquals("msu-md", rom(true, "MSU-MD").msuPackKind)
        assertNull(rom(false, "msu1").msuPackKind)
        assertNull(rom(true, "weird").msuPackKind)
        assertNull(rom(true, null).msuPackKind)
        assertEquals("MD+ pack", MsuPack.label("mdplus"))
        assertEquals("bundle", MsuPack.label(null))
    }
}
