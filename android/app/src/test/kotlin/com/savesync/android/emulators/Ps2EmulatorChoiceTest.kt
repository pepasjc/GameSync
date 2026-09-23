package com.savesync.android.emulators

import com.savesync.android.emulators.impl.AetherSX2Emulator
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import java.io.File

class Ps2EmulatorChoiceTest {

    @get:Rule
    val tmp = TemporaryFolder()

    @Test
    fun `unknown or missing wire value falls back to AetherSX2`() {
        assertEquals(Ps2EmulatorChoice.AETHERSX2, Ps2EmulatorChoice.fromWireValue(null))
        assertEquals(Ps2EmulatorChoice.AETHERSX2, Ps2EmulatorChoice.fromWireValue("bogus"))
        assertEquals(Ps2EmulatorChoice.ARMSX2, Ps2EmulatorChoice.fromWireValue("ARMSX2"))
    }

    @Test
    fun `ARMSX2 resolves its own Android data memcards folder`() {
        val root = tmp.root
        val aether = File(root, "Android/data/xyz.aethersx2.android/files/memcards").apply { mkdirs() }
        val armsx2 = File(root, "Android/data/come.nanodata.armsx2/files/memcards").apply { mkdirs() }

        assertEquals(armsx2, AetherSX2Emulator.findMemcardsDir(root, variant = Ps2EmulatorChoice.ARMSX2))
        assertEquals(aether, AetherSX2Emulator.findMemcardsDir(root, variant = Ps2EmulatorChoice.AETHERSX2))
    }

    @Test
    fun `ARMSX2 never falls back to AetherSX2 folders`() {
        val root = tmp.root
        File(root, "Android/data/xyz.aethersx2.android/files/memcards").mkdirs()
        assertNull(AetherSX2Emulator.findMemcardsDir(root, variant = Ps2EmulatorChoice.ARMSX2))
    }

    @Test
    fun `ARMSX2 has no EmuDeck root`() {
        assertNull(EmudeckPaths.ps2Root("/sdcard/Emulation", Ps2EmulatorChoice.ARMSX2))
        assertEquals(
            File("/sdcard/Emulation/storage/NetherSX2"),
            EmudeckPaths.ps2Root("/sdcard/Emulation", Ps2EmulatorChoice.AETHERSX2)
        )
    }
}
