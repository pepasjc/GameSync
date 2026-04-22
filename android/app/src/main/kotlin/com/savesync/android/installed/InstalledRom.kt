package com.savesync.android.installed

import java.io.File

/**
 * One installed ROM entry displayed on the Installed Games tab.
 *
 * Mirrors the Steam Deck ``scanner.installed_roms.InstalledRom``
 * dataclass so UI + deletion logic can stay in lockstep between
 * clients.  ``companionFiles`` holds the other on-disk tracks that
 * belong to this game (``.bin`` next to a ``.cue``, track files
 * next to a ``.gdi``) — they get deleted together.
 */
data class InstalledRom(
    /** Primary on-disk file (what we show in the list). */
    val path: File,
    /** System code (PS1, GBA, SAT, …) */
    val system: String,
    /** Pretty-printed name with region tags preserved. */
    val displayName: String,
    /** Primary filename (path.name). */
    val filename: String,
    /** Total bytes occupied by primary + companions. */
    val size: Long,
    /** Root folder of this rom's system (e.g. `/sdcard/ROMs/PS1`).
     *  Used by [InstalledRomsScanner.deleteInstalled] to avoid ever
     *  rmtree'ing the system root itself, even when it happens to
     *  contain a single dedicated subfolder. */
    val systemRoot: File,
    /** Extra files grouped under this ROM (disc tracks, bin/cue pairs). */
    val companionFiles: List<File> = emptyList(),
) {
    val totalFiles: Int get() = 1 + companionFiles.size
}

/**
 * Outcome of [InstalledRomsScanner.deleteInstalled].
 *
 * ``removedDir`` is set when a dedicated per-game subfolder was
 * rmtree'd in one call — the UI surfaces this so the user sees that
 * the whole folder is gone, not just the tracked files.
 */
data class DeleteResult(
    val deletedCount: Int,
    val errors: List<String>,
    val removedDir: File? = null,
)
