package com.savesync.android.emulators.impl

import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Before
import org.junit.Test
import java.io.File

/**
 * Verifies the companion helpers used by the server-only placeholder
 * builder to predict a downloadable PSP slot directory.  Without a
 * predicted path the Download button in the save detail screen stays
 * disabled for any PSP title whose ROM isn't installed yet.
 */
class PpssppEmulatorDefaultSlotTest {

    private val root = File("build/test-ppsspp-default-slot")

    @Before
    fun setUp() {
        root.deleteRecursively()
        root.mkdirs()
    }

    @After
    fun tearDown() {
        root.deleteRecursively()
    }

    @Test
    fun `defaultSlotDir falls back to primary path when nothing exists yet`() {
        val slot = PpssppEmulator.defaultSlotDir(root, "ULUS10567DATA")
        assertEquals(File(File(root, "PSP/SAVEDATA"), "ULUS10567DATA"), slot)
    }

    @Test
    fun `defaultSlotDir prefers the existing savedata dir when found`() {
        val existing = File(root, "psp/SAVEDATA").also { it.mkdirs() }
        val slot = PpssppEmulator.defaultSlotDir(root, "ULJS00080")
        assertEquals(File(existing, "ULJS00080"), slot)
    }

    @Test
    fun `findSaveDataDir returns null when directory is missing and fallback disabled`() {
        val result = PpssppEmulator.findSaveDataDir(root, allowNonExistent = false)
        assertEquals(null, result)
    }
}
