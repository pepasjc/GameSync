package com.savesync.android.sync

import com.savesync.android.SaveSyncApp
import java.io.File

/**
 * The server's hash for an Xbox save this device downloaded, paired with the
 * hash GameSync computes for the same files.
 *
 * The server hashes a bundle's files in upload order.  The Xbox client
 * uploads in FATX directory order while GameSync hashes sorted by path, so the
 * same save has two hashes; without the pair a save uploaded by the Xbox would
 * look changed on the server after every download (the PS5 client keeps the
 * same pair as ``<id>.server``).  A pair only applies while the local files
 * still hash to its local half; an upload from here (sorted, so both hashes
 * agree) drops it.
 */
object XboxServerHashStore {
    private const val STATE_FILE_NAME = "xbox_server_hashes.tsv"

    /** The server's hash for [localHash], or [localHash] when no pair matches. */
    @Synchronized
    fun serverHash(titleId: String, localHash: String): String {
        val pair = readAll()[titleId] ?: return localHash
        return if (pair.second == localHash) pair.first else localHash
    }

    @Synchronized
    fun put(titleId: String, serverHash: String, localHash: String) {
        val all = readAll().toMutableMap()
        if (serverHash == localHash) all.remove(titleId) else all[titleId] = serverHash to localHash
        writeAll(all)
    }

    @Synchronized
    fun remove(titleId: String) {
        val all = readAll()
        if (titleId in all) writeAll(all - titleId)
    }

    private fun stateFile(): File = File(SaveSyncApp.instance.filesDir, STATE_FILE_NAME)

    private fun readAll(): Map<String, Pair<String, String>> {
        val file = stateFile()
        if (!file.exists()) return emptyMap()
        return file.readLines().mapNotNull { line ->
            val parts = line.split('\t')
            if (parts.size == 3 && parts.all { it.isNotBlank() }) parts[0] to (parts[1] to parts[2]) else null
        }.toMap()
    }

    private fun writeAll(entries: Map<String, Pair<String, String>>) {
        val file = stateFile()
        file.parentFile?.mkdirs()
        file.writeText(
            entries.entries.sortedBy { it.key }
                .joinToString("\n") { (id, pair) -> "$id\t${pair.first}\t${pair.second}" }
        )
    }
}
