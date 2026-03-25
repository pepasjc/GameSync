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
     * Returns all candidates (existing or not) for diagnostics.
     */
    fun diagnosticPaths(): List<Pair<String, Boolean>> = emptyList()
    open fun retroarchDiagnosticPaths(): List<Pair<String, Boolean>> = emptyList()
}
