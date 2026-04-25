package com.savesync.android.emulators.impl

import com.savesync.android.emulators.EmulatorBase
import com.savesync.android.emulators.SaveEntry
import java.io.File

class AetherSX2Emulator(
    private val romScanDir: String = "",
    private val storageBaseDir: File? = null,
    /**
     * Optional explicit memcards folder override, configured in the Emulator
     * Configuration screen.  Wins over [storageBaseDir] / the auto-detected
     * AetherSX2 / NetherSX2 paths when set.
     */
    private val saveDirOverride: String? = null
) : EmulatorBase() {

    override val name: String = "AetherSX2 / NetherSX2"
    override val systemPrefix: String = "PS2"

    private val romExtensions = setOf("iso", "bin", "img", "mdf", "cue", "chd")
    private val sharedCardRegex = Regex("""(?i)^mcd\d{3}$""")
    private val compactSerialRegex = Regex("""^[A-Z]{4}\d{5}$""")

    override fun discoverSaves(): List<SaveEntry> {
        val memcardsDir = findMemcardsDir() ?: return emptyList()
        val romSerialsByStem = buildPs2RomSerialMap()

        val result = mutableListOf<SaveEntry>()
        memcardsDir.listFiles()?.forEach { file ->
            if (!file.isFile) return@forEach
            val ext = file.extension.lowercase()
            if (ext !in setOf("ps2", "mc2")) return@forEach

            val stem = file.nameWithoutExtension
            // AetherSX2's shared default cards (Mcd001/Mcd002) are not game-specific saves,
            // so skip them here and let the user manage per-game downloads explicitly.
            if (sharedCardRegex.matches(stem)) return@forEach
            val serial = romSerialsByStem[stem]
            // Server-only downloads are named as TITLEID_gamename.ps2 so they stay
            // readable in Aether while still round-tripping the exact server title ID.
            val embeddedSerial = extractEmbeddedSerial(stem)
            val titleId = serial ?: embeddedSerial ?: toTitleId(stem)
            result.add(
                SaveEntry(
                    titleId = titleId,
                    displayName = stem,
                    systemName = systemPrefix,
                    saveFile = file,
                    saveDir = null
                )
            )
        }

        return result
    }

    override fun discoverRomEntries(): Map<String, SaveEntry> {
        val memcardsDir = findMemcardsDir() ?: return emptyMap()
        val romFiles = ps2RomFiles()
        if (romFiles.isEmpty()) return emptyMap()

        val result = linkedMapOf<String, SaveEntry>()
        for (rom in romFiles) {
            val serial = readPs2Serial(rom) ?: continue
            val stem = rom.nameWithoutExtension
            val savePath = File(memcardsDir, "$stem.ps2")
            result[serial] = SaveEntry(
                titleId = serial,
                displayName = stem,
                systemName = systemPrefix,
                saveFile = savePath,
                saveDir = null,
                isServerOnly = false,
                canonicalName = serial
            )
        }
        return result
    }

    private fun findMemcardsDir(): File? {
        if (!saveDirOverride.isNullOrBlank()) {
            val overrideDir = File(saveDirOverride)
            if (overrideDir.exists() && overrideDir.isDirectory) return overrideDir
        }
        return findMemcardsDir(storageBaseDir ?: baseDir)
    }

    private fun buildPs2RomSerialMap(): Map<String, String> {
        val result = mutableMapOf<String, String>()
        for (rom in ps2RomFiles()) {
            val serial = readPs2Serial(rom) ?: continue
            result[rom.nameWithoutExtension] = serial
        }
        return result
    }

    private fun ps2RomFiles(): List<File> {
        val dirs = mutableListOf<File>()

        listOf(
            "PS2",
            "ps2",
            "PlayStation2",
            "PlayStation 2",
            "roms/PS2",
            "ROMs/PS2",
            "Games/PS2",
            "games/PS2"
        ).forEach { rel ->
            val dir = File(storageBaseDir ?: baseDir, rel)
            if (dir.exists() && dir.isDirectory) dirs.add(dir)
        }

        if (romScanDir.isNotBlank()) {
            val scanRoot = File(romScanDir)
            listOf(
                "PS2",
                "ps2",
                "PlayStation2",
                "PlayStation 2",
                "Sony - PlayStation 2"
            ).forEach { sub ->
                val dir = File(scanRoot, sub)
                if (dir.exists() && dir.isDirectory) dirs.add(dir)
            }
        }

        return dirs
            .distinctBy { it.absolutePath }
            .flatMap { dir ->
                dir.walkTopDown()
                    .filter { it.isFile && it.extension.lowercase() in romExtensions }
                    .toList()
            }
    }

    companion object {
        /** Key used in [com.savesync.android.storage.Settings.saveDirOverrides]. */
        const val EMULATOR_KEY = "AetherSX2"

        private val embeddedSerialRegex = Regex("""^([A-Z]{4}\d{5})(?:[_\-\s].+)?$""", RegexOption.IGNORE_CASE)

        fun findMemcardsDir(baseDir: File, allowNonExistent: Boolean = false): File? {
            val candidates = listOf(
                "Android/data/xyz.aethersx2.android/files/memcards",
                "Android/data/xyz.aethersx2.android/files/Memcards",
                "Android/data/xyz.aethersx2.android/files/memorycards",
                "Android/data/xyz.aethersx2.android/files/MemoryCards",
                "memcards",
                "Memcards",
                "memorycards",
                "MemoryCards",
                "AetherSX2/memcards",
                "NetherSX2/memcards",
                "aethersx2/memcards",
                "nethersx2/memcards"
            )
            val dirs = candidates
                .map { File(baseDir, it) }
            return dirs.firstOrNull { it.exists() && it.isDirectory }
                ?: if (allowNonExistent) {
                    if (baseDir.name.equals("NetherSX2", ignoreCase = true)) {
                        File(baseDir, "memcards")
                    } else {
                        dirs.firstOrNull()
                    }
                } else null
        }

        fun sanitizeServerCardName(name: String): String {
            return name
                .replace(Regex("""[\\/:*?"<>|]"""), "_")
                .trim()
                .ifBlank { "PS2 Save" }
        }

        fun extractEmbeddedSerial(stem: String): String? {
            val match = embeddedSerialRegex.matchEntire(stem) ?: return null
            val serial = match.groupValues[1].uppercase()
            return serial.takeIf { Regex("""^[A-Z]{4}\d{5}$""").matches(it) }
        }
    }
}
