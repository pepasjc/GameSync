package com.savesync.android.sync

import com.savesync.android.SaveSyncApp
import java.io.File

object SaturnArchiveStateStore {
    private const val STATE_FILE_NAME = "saturn_archive_names.tsv"
    private const val TITLE_SEPARATOR = '\t'
    private const val NAME_SEPARATOR = '|'

    @Synchronized
    fun get(titleId: String): List<String> {
        return readAll()[titleId].orEmpty()
    }

    @Synchronized
    fun put(titleId: String, archiveNames: List<String>) {
        val normalized = archiveNames
            .map { it.trim() }
            .filter { it.isNotEmpty() }
            .distinct()
        if (normalized.isEmpty()) return

        val all = readAll().toMutableMap()
        all[titleId] = normalized
        writeAll(all)
    }

    private fun stateFile(): File {
        return File(SaveSyncApp.instance.filesDir, STATE_FILE_NAME)
    }

    private fun readAll(): Map<String, List<String>> {
        val file = stateFile()
        if (!file.exists()) return emptyMap()

        val entries = mutableMapOf<String, List<String>>()
        file.forEachLine { line ->
            val separatorIndex = line.indexOf(TITLE_SEPARATOR)
            if (separatorIndex <= 0) return@forEachLine

            val titleId = line.substring(0, separatorIndex).trim()
            val archiveNames = line.substring(separatorIndex + 1)
                .split(NAME_SEPARATOR)
                .map { it.trim() }
                .filter { it.isNotEmpty() }
                .distinct()

            if (titleId.isNotEmpty() && archiveNames.isNotEmpty()) {
                entries[titleId] = archiveNames
            }
        }
        return entries
    }

    private fun writeAll(entries: Map<String, List<String>>) {
        val file = stateFile()
        file.parentFile?.mkdirs()
        file.writeText(
            entries.entries
                .sortedBy { it.key }
                .joinToString("\n") { (titleId, archiveNames) ->
                    "$titleId$TITLE_SEPARATOR${archiveNames.joinToString(NAME_SEPARATOR.toString())}"
                }
        )
    }
}
