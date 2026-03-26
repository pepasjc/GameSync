package com.savesync.android.emulators

import android.os.Environment
import java.io.File

abstract class EmulatorBase {
    abstract val name: String
    abstract val systemPrefix: String  // e.g. "GBA", "PPSSPP", "NDS"

    abstract fun discoverSaves(): List<SaveEntry>

    /**
     * Converts a ROM name to a title ID slug.
     * Lowercases, replaces non-alphanumeric characters with underscores,
     * collapses multiple underscores, trims, and prepends systemPrefix_.
     */
    protected fun toTitleId(romName: String): String {
        val slug = romName
            .lowercase()
            .replace(Regex("[^a-z0-9]+"), "_")
            .trim('_')
        return "${systemPrefix}_$slug"
    }

    protected val baseDir: File
        get() = Environment.getExternalStorageDirectory()

    /**
     * Returns the first existing directory from the provided relative path candidates
     * (relative to external storage root).
     */
    protected fun firstExisting(vararg paths: String): File? {
        return paths.map { File(baseDir, it) }.firstOrNull { it.exists() && it.isDirectory }
    }

    /**
     * Like firstExisting but accepts absolute File objects directly.
     */
    protected fun firstExistingAbsolute(vararg files: File): File? {
        return files.firstOrNull { it.exists() && it.isDirectory }
    }

    /**
     * Returns a map of titleId → SaveEntry for every ROM the emulator knows about,
     * regardless of whether a save file already exists.  The SaveEntry contains the
     * *expected* save-file path so we can write there on download.
     *
     * Default implementation returns empty — override in emulators that have a ROM list
     * (e.g. RetroArch playlists).
     */
    open fun discoverRomEntries(): Map<String, SaveEntry> = emptyMap()

    /**
     * Reads the 4-byte game code from an NDS ROM file at offset 0x0C and returns a
     * 16-char uppercase hex title ID matching the NDS homebrew client format:
     *   "00048000" + hex(gamecode bytes)
     * e.g. gamecode "AMKJ" (0x41 0x4D 0x4B 0x4A) → "00048000414D4B4A"
     *
     * Returns null if the file is missing, too short, or unreadable.
     */
    protected fun readNdsGamecode(romFile: File): String? {
        return try {
            val bytes = ByteArray(4)
            romFile.inputStream().use { stream ->
                val skipped = stream.skip(0x0C)
                if (skipped < 0x0C) return null
                val read = stream.read(bytes)
                if (read < 4) return null
            }
            "00048000%02X%02X%02X%02X".format(
                bytes[0].toInt() and 0xFF,
                bytes[1].toInt() and 0xFF,
                bytes[2].toInt() and 0xFF,
                bytes[3].toInt() and 0xFF
            )
        } catch (_: Exception) { null }
    }

    /**
     * Searches for an NDS ROM file whose name (without extension) matches [romName]
     * (case-insensitive) across the given [searchDirs].
     * Returns the first matching file, or null if none found.
     */
    protected fun findNdsRom(romName: String, searchDirs: List<File>): File? {
        val ndsExtensions = setOf("nds", "dsi")
        for (dir in searchDirs) {
            if (!dir.exists() || !dir.isDirectory) continue
            val found = dir.listFiles()?.firstOrNull { file ->
                file.isFile &&
                file.extension.lowercase() in ndsExtensions &&
                file.nameWithoutExtension.equals(romName, ignoreCase = true)
            }
            if (found != null) return found
        }
        return null
    }

    /**
     * Builds a list of candidate NDS ROM directories to search for gamecode lookup.
     * Checks common on-device locations plus an optional user-specified scan root.
     */
    protected fun ndsRomSearchDirs(romScanDir: String = ""): List<File> {
        val dirs = mutableListOf<File>()
        // Common NDS ROM directories on external storage
        listOf("NDS", "nds", "DS", "Nintendo DS", "roms/NDS", "Roms/NDS",
               "ROMs/NDS", "Games/NDS", "games/NDS").forEach { rel ->
            val f = File(baseDir, rel)
            if (f.exists() && f.isDirectory) dirs.add(f)
        }
        // User-specified ROM scan root → look for NDS subfolder
        if (romScanDir.isNotBlank()) {
            val scanRoot = File(romScanDir)
            listOf("NDS", "nds", "DS", "Nintendo DS", "Nintendo - DS").forEach { sub ->
                val f = File(scanRoot, sub)
                if (f.exists() && f.isDirectory) dirs.add(f)
            }
        }
        return dirs
    }

    /**
     * Returns all candidates (existing or not) for diagnostics.
     */
    fun diagnosticPaths(): List<Pair<String, Boolean>> = emptyList()
    open fun retroarchDiagnosticPaths(): List<Pair<String, Boolean>> = emptyList()
}
