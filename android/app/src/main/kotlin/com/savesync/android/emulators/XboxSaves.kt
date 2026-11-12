package com.savesync.android.emulators

import java.io.File

/**
 * What every Xbox save source shares: the E:\UDATA\<title id> folder a game
 * saves into, and the order its files travel in.
 *
 * The Xbox client (xbox/source/saves.c) bundles a title's folder with '\'
 * paths, and the PS5 client hashes it sorted by those paths (strcmp).  Sorting
 * by the '\' form rather than the '/' one matters: '\' sorts after letters and
 * '/' before them, so "Save\a" and "SaveB" come out in opposite orders.
 */
object XboxSaves {
    /** An E:\UDATA folder name that is a game's save: 8 hex digits, the first two bytes uppercase letters. */
    fun isGameTitleId(name: String): Boolean = XboxHddImage.isGameTitleId(name)

    /** A save folder's files with their bundle paths ('\' separators), in bundle order. */
    fun treeFiles(dir: File): List<Pair<String, File>> =
        dir.walkTopDown()
            .filter { it.isFile }
            .map { it.relativeTo(dir).path.replace('/', '\\') to it }
            .sortedBy { it.first }
            .toList()

    /** A key's value in an .xbx metadata file (UTF-16LE "Key=Value" lines, optional BOM). */
    fun metaValue(file: File?, key: String): String? {
        if (file == null || !file.isFile || file.length() > 65536) return null
        return metaValue(file.readBytes(), key)
    }

    fun metaValue(bytes: ByteArray, key: String): String? {
        val text = if (bytes.size >= 2 && bytes[0] == 0xFF.toByte() && bytes[1] == 0xFE.toByte()) {
            String(bytes, 2, bytes.size - 2, Charsets.UTF_16LE)
        } else {
            String(bytes, Charsets.UTF_16LE)
        }
        return text.lineSequence()
            .map { it.trim('\uFEFF', ' ', '\r', '\u0000') }
            .firstOrNull { it.startsWith("$key=", ignoreCase = true) }
            ?.substringAfter('=')
            ?.trim()
            ?.takeIf { it.isNotEmpty() }
    }

    /** The game's name from its save folder's TitleMeta.xbx, if it has one. */
    fun titleName(titleDir: File): String? =
        titleDir.listFiles()
            ?.firstOrNull { it.isFile && it.name.equals("TitleMeta.xbx", ignoreCase = true) }
            ?.let { metaValue(it, "TitleName") }

    /** [name]'s child in any case (FATX names are case-insensitive), or null. */
    fun child(dir: File, name: String): File? =
        dir.listFiles()?.firstOrNull { it.name.equals(name, ignoreCase = true) }
}
