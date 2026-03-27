package com.savesync.android.emulators

import com.savesync.android.sync.HashUtils
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
    val canonicalName: String? = null
) {
    /**
     * True when this entry is a PSP/PSX save slot directory (DATA.BIN + PARAM.SFO + etc.),
     * as opposed to a single save file or a generic multi-file directory.
     * This drives the PSP bundle upload/download path in SyncEngine, independently of
     * whether the system is "PPSSPP" (PSP game) or "PSX" (PSone Classic under PPSSPP).
     */
    val isPspSlot: Boolean get() = saveDir != null && !isMultiFile

    fun computeHash(): String {
        return when {
            isServerOnly -> ""
            // PSP slot dirs: sha256 of all file contents sorted by filename (no paths).
            // Matches the server's bundle hash and the PSP homebrew client's algorithm.
            isPspSlot -> HashUtils.sha256DirFiles(saveDir!!)
            saveFile != null && extraFiles.isNotEmpty() -> {
                val files = (listOf(saveFile) + extraFiles).filter { it.exists() }.sortedBy { it.name }
                HashUtils.sha256Files(files)
            }
            isMultiFile && saveDir != null -> HashUtils.sha256Dir(saveDir)
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
