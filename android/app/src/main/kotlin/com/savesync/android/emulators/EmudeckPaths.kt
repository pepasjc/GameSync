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

    fun ppssppRoot(emudeckDir: String): File? =
        storageDir(emudeckDir)?.let { File(it, "PPSSPP") }
}
