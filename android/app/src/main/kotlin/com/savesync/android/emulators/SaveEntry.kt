package com.savesync.android.emulators

import com.savesync.android.sync.HashUtils
import com.savesync.android.sync.Ps2CardEcc
import java.io.ByteArrayOutputStream
import java.io.File
import java.util.zip.ZipEntry
import java.util.zip.ZipOutputStream

data class SaveEntry(
    val titleId: String,
    val displayName: String,         // original filename on disk (never renamed)
    val systemName: String,
    val saveFile: File?,             // null if multi-file or server-only
    val extraFiles: List<File> = emptyList(),  // optional companion files (e.g. DuckStation slot 2)
    val saveDir: File?,              // non-null if multi-file (e.g. PPSSPP)
    val isMultiFile: Boolean = saveDir != null,
    val isServerOnly: Boolean = false,
    /** Canonical No-Intro/Redump name from DAT lookup — null if not matched */
    val canonicalName: String? = null,
    /**
     * Product code the server can resolve a game name from when its own
     * title-id lookup can't.  Wii U saves are keyed by a 16-hex title id whose
     * low word is *not* the product code, so Cemu reports "WIIU_ARDE" (read
     * from meta.xml) and the upload passes it as ``game_code``.
     */
    val gameCode: String? = null
) {
    /**
     * True when this entry is a PSP/PSX save slot directory (DATA.BIN + PARAM.SFO + etc.),
     * as opposed to a single save file or a generic multi-file directory.
     * This drives the PSP bundle upload/download path in SyncEngine, independently of
     * whether the system is "PSP" (PSP game) or "PS1" (PSone Classic under PPSSPP).
     */
    val isPspSlot: Boolean get() = saveDir != null && !isMultiFile

    /**
     * True for save archives that are recursive directory trees bundled with
     * relative paths: 3DS (Azahar) and Wii U (Cemu).  Both hash as the
     * concatenation of file contents in relative-path order, which is what the
     * server computes for the bundle.
     */
    val isTreeSaveDir: Boolean
        get() = (systemName == "3DS" || systemName == "WIIU") && isMultiFile && saveDir != null

    fun computeHash(): String {
        return when {
            isServerOnly -> ""
            // PSP slot dirs: sha256 of all file contents sorted by filename (no paths).
            // Matches the server's bundle hash and the PSP homebrew client's algorithm.
            isPspSlot -> HashUtils.sha256DirFiles(saveDir!!)
            // 3DS/Wii U save archives are recursive directory trees bundled with relative
            // paths, but the server compares only concatenated file contents in bundle order.
            isTreeSaveDir -> HashUtils.sha256DirTreeFiles(saveDir!!)
            saveFile != null && extraFiles.isNotEmpty() -> {
                val files = (listOf(saveFile) + extraFiles).filter { it.exists() }.sortedBy { it.name }
                HashUtils.sha256Files(files)
            }
            isMultiFile && saveDir != null -> HashUtils.sha256Dir(saveDir)
            // PS2 cards hash with recomputed ECC, as the server does — see Ps2CardEcc.
            saveFile != null && systemName == "PS2" ->
                Ps2CardEcc.normalizedHash(saveFile) ?: HashUtils.sha256File(saveFile)
            saveFile != null -> HashUtils.sha256File(saveFile)
            else -> ""
        }
    }

    fun readBytes(): ByteArray {
        return when {
            isServerOnly -> ByteArray(0)
            isMultiFile && saveDir != null -> zipDirectory(saveDir)
            saveFile != null -> saveFile.readBytes()
            else -> ByteArray(0)
        }
    }

    /**
     * A server-only entry is a prediction: "if this save were downloaded, it
     * would land at [saveFile] / [saveDir]".  Once that path really exists —
     * after a download, or because the emulator wrote it while the scanned list
     * was stale — the prediction is a real local save and must be treated as
     * one.  Left as-is, [exists] and [computeHash] keep answering "nothing
     * local", so the detail screen shows no hash, disables upload, and a smart
     * sync downloads straight over newer play.
     *
     * Shared Saturn containers (YabaSanshiro `backup.bin`) hold many games and
     * are deliberately kept server-only; SyncEngine handles them through the
     * archive-selection path.  A predicted directory only counts once it holds
     * at least one file, so an empty folder does not turn into an "empty save".
     */
    fun reconcileWithDisk(): SaveEntry {
        if (!isServerOnly) return this
        if (systemName == "SAT" && saveFile?.name.equals("backup.bin", ignoreCase = true)) return this
        val onDisk = when {
            saveDir != null -> saveDir.isDirectory && saveDir.walkTopDown().any { it.isFile }
            saveFile != null -> saveFile.isFile
            else -> false
        }
        return if (onDisk) copy(isServerOnly = false) else this
    }

    fun exists(): Boolean {
        if (isServerOnly) return false
        return when {
            isPspSlot -> saveDir!!.exists() && saveDir.isDirectory
            saveFile != null && extraFiles.isNotEmpty() ->
                saveFile.exists() || extraFiles.any { it.exists() }
            isMultiFile && saveDir != null -> saveDir.exists() && saveDir.isDirectory
            saveFile != null -> saveFile.exists() && saveFile.isFile
            else -> false
        }
    }

    fun getTimestamp(): Long {
        return when {
            isServerOnly -> 0L
            // Use most-recently-modified file inside the slot directory
            isPspSlot -> {
                saveDir!!.listFiles()
                    ?.filter { it.isFile }
                    ?.maxOfOrNull { it.lastModified() }
                    ?: saveDir.lastModified()
            }
            saveFile != null && extraFiles.isNotEmpty() -> {
                (listOf(saveFile) + extraFiles)
                    .filter { it.exists() }
                    .maxOfOrNull { it.lastModified() }
                    ?: 0L
            }
            isMultiFile && saveDir != null -> {
                // Use the most recently modified file in the directory
                saveDir.walkTopDown()
                    .filter { it.isFile }
                    .maxOfOrNull { it.lastModified() } ?: saveDir.lastModified()
            }
            saveFile != null -> saveFile.lastModified()
            else -> 0L
        }
    }
}

fun zipDirectory(dir: File): ByteArray {
    val baos = ByteArrayOutputStream()
    ZipOutputStream(baos).use { zos ->
        dir.walkTopDown().forEach { file ->
            if (file.isFile) {
                val entryName = file.relativeTo(dir).path.replace('\\', '/')
                val entry = ZipEntry(entryName)
                entry.time = file.lastModified()
                zos.putNextEntry(entry)
                file.inputStream().use { it.copyTo(zos) }
                zos.closeEntry()
            }
        }
    }
    return baos.toByteArray()
}
