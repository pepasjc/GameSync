package com.savesync.android.emulators

import java.io.File

object EmudeckPaths {
    private const val STORAGE = "storage"

    fun storageDir(emudeckDir: String): File? {
        if (emudeckDir.isBlank()) return null
        return File(emudeckDir, STORAGE)
    }

    fun romsDir(emudeckDir: String): File? {
        if (emudeckDir.isBlank()) return null
        return File(emudeckDir, "roms")
    }

    fun azaharRoot(emudeckDir: String): File? =
        storageDir(emudeckDir)?.let { File(it, "Azahar") }

    fun dolphinRoot(emudeckDir: String): File? =
        storageDir(emudeckDir)?.let { File(it, "Dolphin") }

    fun netherSx2Root(emudeckDir: String): File? =
        storageDir(emudeckDir)?.let { File(it, "NetherSX2") }

    /**
     * EmuDeck only ships NetherSX2, so ARMSX2 has no EmuDeck folder and
     * resolves against external storage instead.
     */
    fun ps2Root(emudeckDir: String, ps2Emulator: Ps2EmulatorChoice): File? =
        if (ps2Emulator == Ps2EmulatorChoice.AETHERSX2) netherSx2Root(emudeckDir) else null

    fun ppssppRoot(emudeckDir: String): File? =
        storageDir(emudeckDir)?.let { File(it, "PPSSPP") }

    /**
     * Cemu's EmuDeck folder.  EmuDeck lowercases this one ("cemu"), so try
     * both spellings and let the caller fall back to on-device candidates when
     * neither exists.
     */
    fun cemuRoot(emudeckDir: String): File? {
        val storage = storageDir(emudeckDir) ?: return null
        val lower = File(storage, "cemu")
        if (lower.isDirectory) return lower
        return File(storage, "Cemu")
    }
}
