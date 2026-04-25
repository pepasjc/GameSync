package com.savesync.android.emulators.impl

import com.savesync.android.emulators.EmulatorBase
import com.savesync.android.emulators.SaveEntry
import java.io.File

class AzaharEmulator(
    private val candidateTitleRoots: List<File>? = null,
    private val storageBaseDir: File? = null,
    /**
     * Optional explicit save folder override, configured in the Emulator
     * Configuration screen.  When set, points directly at the
     * ``sdmc/Nintendo 3DS/.../title`` root and bypasses the auto-detection
     * candidates.
     */
    private val saveDirOverride: String? = null
) : EmulatorBase() {

    override val name: String = "Azahar"
    override val systemPrefix: String = "3DS"

    override fun discoverSaves(): List<SaveEntry> {
        val titleRoot = resolveTitleRoot(allowNonExistent = false) ?: return emptyList()
        if (!titleRoot.exists() || !titleRoot.isDirectory) return emptyList()

        val results = mutableListOf<SaveEntry>()

        titleRoot.listFiles()
            ?.filter { it.isDirectory && HEX8_REGEX.matches(it.name) }
            ?.sortedBy { it.name }
            ?.forEach { highDir ->
                highDir.listFiles()
                    ?.filter { it.isDirectory && HEX8_REGEX.matches(it.name) }
                    ?.sortedBy { it.name }
                    ?.forEach { lowDir ->
                        val saveDir = File(lowDir, "data/00000001")
                        if (!saveDir.exists() || !saveDir.isDirectory) return@forEach
                        val files = saveDir.walkTopDown().filter { it.isFile }.toList()
                        if (files.isEmpty()) return@forEach

                        val titleId = (highDir.name + lowDir.name).uppercase()
                        results.add(
                            SaveEntry(
                                titleId = titleId,
                                displayName = titleId,
                                systemName = "3DS",
                                saveFile = null,
                                saveDir = saveDir,
                                isMultiFile = true
                            )
                        )
                    }
            }

        return results
    }

    private fun resolveTitleRoot(allowNonExistent: Boolean): File? {
        if (!saveDirOverride.isNullOrBlank()) {
            val overrideDir = File(saveDirOverride)
            if (overrideDir.exists() && overrideDir.isDirectory) return overrideDir
            if (allowNonExistent) return overrideDir
        }
        return findTitleRoot(
            storageBaseDir = storageBaseDir ?: baseDir,
            allowNonExistent = allowNonExistent,
            candidateTitleRoots = candidateTitleRoots
        )
    }

    companion object {
        /** Key used in [com.savesync.android.storage.Settings.saveDirOverrides]. */
        const val EMULATOR_KEY = "Azahar"

        private const val ZERO_ID = "00000000000000000000000000000000"
        private val HEX8_REGEX = Regex("^[0-9A-Fa-f]{8}$")
        private val TITLE_ID_REGEX = Regex("^[0-9A-Fa-f]{16}$")

        fun findTitleRoot(
            storageBaseDir: File,
            allowNonExistent: Boolean = false,
            candidateTitleRoots: List<File>? = null
        ): File? {
            val candidates = candidateTitleRoots ?: buildCandidateTitleRoots(storageBaseDir)
            return when {
                allowNonExistent -> candidates.firstOrNull()
                else -> candidates.firstOrNull { it.exists() && it.isDirectory }
            }
        }

        fun defaultSaveDir(
            storageBaseDir: File,
            titleId: String,
            candidateTitleRoots: List<File>? = null
        ): File? {
            if (!TITLE_ID_REGEX.matches(titleId)) return null
            val titleRoot = findTitleRoot(
                storageBaseDir = storageBaseDir,
                allowNonExistent = true,
                candidateTitleRoots = candidateTitleRoots
            ) ?: return null
            val upper = titleId.uppercase()
            return File(
                File(
                    File(titleRoot, upper.substring(0, 8)),
                    upper.substring(8)
                ),
                "data/00000001"
            )
        }

        private fun buildCandidateTitleRoots(storageBaseDir: File): List<File> {
            val suffix = "Nintendo 3DS/$ZERO_ID/$ZERO_ID/title"
            val candidates = listOf(
                "sdmc/$suffix",
                "Azahar/sdmc/$suffix",
                "azahar/sdmc/$suffix",
                "Azahar-emu/sdmc/$suffix",
                "azahar-emu/sdmc/$suffix",
                "citra-emu/sdmc/$suffix",
                "lime3ds-emu/sdmc/$suffix",
                "Android/data/org.azahar_emu.azahar/files/sdmc/$suffix",
                "Android/data/org.citra.citra_emu/files/sdmc/$suffix",
                "Android/data/io.github.lime3ds.android/files/sdmc/$suffix",
            )
            return candidates.map { relative -> File(storageBaseDir, relative) }
        }
    }
}
