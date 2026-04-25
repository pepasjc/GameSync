package com.savesync.android.emulators

import com.savesync.android.emulators.impl.AetherSX2Emulator
import com.savesync.android.emulators.impl.DolphinEmulator
import com.savesync.android.emulators.impl.DraSticEmulator
import com.savesync.android.emulators.impl.DuckStationEmulator
import com.savesync.android.emulators.impl.MelonDsEmulator
import com.savesync.android.emulators.impl.MgbaEmulator
import com.savesync.android.emulators.impl.PpssppEmulator
import com.savesync.android.emulators.impl.RetroArchEmulator
import com.savesync.android.emulators.impl.AzaharEmulator
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Smoke tests covering the per-emulator save-folder override plumbing.
 *
 * The actual override resolution happens inside private path-helpers on each
 * emulator and most of those require a real on-disk layout to exercise.
 * These tests pin down the contract callers depend on:
 *
 *   1. Every emulator exposes a stable [EMULATOR_KEY] constant
 *   2. [EmulatorCatalog.ALL] enumerates every key exactly once
 *   3. The catalog keys match the emulator constants (no drift)
 */
class SaveDirOverrideTest {

    @Test
    fun `every emulator exposes a stable EMULATOR_KEY constant`() {
        val keys = setOf(
            RetroArchEmulator.EMULATOR_KEY,
            PpssppEmulator.EMULATOR_KEY,
            DuckStationEmulator.EMULATOR_KEY,
            DraSticEmulator.EMULATOR_KEY,
            MelonDsEmulator.EMULATOR_KEY,
            MgbaEmulator.EMULATOR_KEY,
            DolphinEmulator.EMULATOR_KEY,
            AetherSX2Emulator.EMULATOR_KEY,
            AzaharEmulator.EMULATOR_KEY,
        )
        // No duplicates means each constant is unique
        assertEquals(9, keys.size)
        // No blanks, all distinct identifiers
        assertTrue(keys.all { it.isNotBlank() })
    }

    @Test
    fun `EmulatorCatalog ALL covers every emulator key exactly once`() {
        val constantKeys = listOf(
            RetroArchEmulator.EMULATOR_KEY,
            PpssppEmulator.EMULATOR_KEY,
            DuckStationEmulator.EMULATOR_KEY,
            DraSticEmulator.EMULATOR_KEY,
            MelonDsEmulator.EMULATOR_KEY,
            MgbaEmulator.EMULATOR_KEY,
            DolphinEmulator.EMULATOR_KEY,
            AetherSX2Emulator.EMULATOR_KEY,
            AzaharEmulator.EMULATOR_KEY,
        )
        val catalogKeys = EmulatorCatalog.ALL.map { it.key }
        assertEquals(constantKeys.toSet(), catalogKeys.toSet())
        // Catalog has the same number of entries as constants — no duplicates.
        assertEquals(constantKeys.size, catalogKeys.size)
    }

    @Test
    fun `EmulatorCatalog descriptors all have non-blank display names and hints`() {
        EmulatorCatalog.ALL.forEach { d ->
            assertTrue("${d.key} displayName blank", d.displayName.isNotBlank())
            assertTrue("${d.key} systemHint blank", d.systemHint.isNotBlank())
            assertTrue("${d.key} defaultPathHint blank", d.defaultPathHint.isNotBlank())
        }
    }
}
