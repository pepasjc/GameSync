package com.savesync.android.sync

import java.io.File
import java.security.MessageDigest

object HashUtils {

    fun sha256File(file: File): String {
        val digest = MessageDigest.getInstance("SHA-256")
        file.inputStream().use { stream ->
            val buffer = ByteArray(8192)
            var read: Int
            while (stream.read(buffer).also { read = it } != -1) {
                digest.update(buffer, 0, read)
            }
        }
        return digest.digest().toHexString()
    }

    fun sha256Bytes(bytes: ByteArray): String {
        val digest = MessageDigest.getInstance("SHA-256")
        digest.update(bytes)
        return digest.digest().toHexString()
    }

    /**
     * Computes SHA-256 of all file contents in [dir] concatenated in ascending filename order,
     * **without** including file paths in the hash.
     *
     * This matches the server's bundle hash:
     *   `sha256(b"".join(f.data for f in bundle.files))`
     * where bundle files are sorted by name — the same algorithm used by the PSP homebrew client.
     *
     * Only direct children are included (no subdirectory recursion) to stay consistent with
     * the flat structure of a PSP SAVEDATA slot.
     */
    fun sha256DirFiles(dir: File): String {
        val digest = MessageDigest.getInstance("SHA-256")
        val files = dir.listFiles()?.filter { it.isFile }?.sortedBy { it.name } ?: return ""
        for (file in files) {
            file.inputStream().use { stream ->
                val buffer = ByteArray(8192)
                var read: Int
                while (stream.read(buffer).also { read = it } != -1) {
                    digest.update(buffer, 0, read)
                }
            }
        }
        return digest.digest().toHexString()
    }

    fun sha256Dir(dir: File): String {
        val digest = MessageDigest.getInstance("SHA-256")
        // Collect all files recursively, sorted by relative path for determinism
        val files = collectFilesRecursively(dir).sortedBy { it.relativeTo(dir).path }
        for (file in files) {
            // Include relative path in hash so renames are detected
            digest.update(file.relativeTo(dir).path.toByteArray(Charsets.UTF_8))
            file.inputStream().use { stream ->
                val buffer = ByteArray(8192)
                var read: Int
                while (stream.read(buffer).also { read = it } != -1) {
                    digest.update(buffer, 0, read)
                }
            }
        }
        return digest.digest().toHexString()
    }

    fun sha256Files(files: List<File>): String {
        val digest = MessageDigest.getInstance("SHA-256")
        for (file in files) {
            file.inputStream().use { stream ->
                val buffer = ByteArray(8192)
                var read: Int
                while (stream.read(buffer).also { read = it } != -1) {
                    digest.update(buffer, 0, read)
                }
            }
        }
        return digest.digest().toHexString()
    }

    private fun collectFilesRecursively(dir: File): List<File> {
        val result = mutableListOf<File>()
        dir.walkTopDown().forEach { file ->
            if (file.isFile) result.add(file)
        }
        return result
    }

    private fun ByteArray.toHexString(): String =
        joinToString("") { "%02x".format(it) }
}
