package com.savesync.android.emulators

import android.os.Environment
import java.io.File

abstract class EmulatorBase {
    abstract val name: String
    abstract val systemPrefix: String  // e.g. "GBA", "PPSSPP", "NDS"

    abstract fun discoverSaves(): List<SaveEntry>

    /**
     * Known geographic region names that are preserved in title ID slugs.
     * Matches the desktop sync_engine.py `_REGION_NAMES` set.
     */
    private val regionNames = setOf(
        "usa", "europe", "japan", "world", "germany", "france", "italy", "spain",
        "australia", "brazil", "korea", "china", "netherlands", "sweden",
        "denmark", "norway", "finland", "asia"
    )

    /** Regex matching any `(...)` or `[...]` tag (with optional leading whitespace). */
    private val tagRegex = Regex("""\s*[\(\[][^\)\]]*[\)\]]""")

    /** Regex matching the inner content of `(...)` groups for region extraction. */
    private val parenContentRegex = Regex("""\(([^)]+)\)""")

    /**
     * Extracts geographic region tokens from parenthetical tags in a name.
     * e.g. "Sonic (USA, Europe)" → ["usa", "europe"]
     */
    private fun extractRegions(name: String): List<String> {
        val regions = mutableListOf<String>()
        val seen = mutableSetOf<String>()
        for (match in parenContentRegex.findAll(name)) {
            for (part in match.groupValues[1].split(",")) {
                val token = part.trim().lowercase()
                if (token in regionNames && token !in seen) {
                    seen.add(token)
                    regions.add(token)
                }
            }
        }
        return regions
    }

    /**
     * Converts a ROM name to a title ID slug.
     *
     * Strips all parenthetical/bracket tags, then re-appends geographic region
     * names so that regional saves stay in separate server slots.  Matches the
     * desktop sync_engine.py `_make_title_id_with_region` logic.
     *
     * "Shining Force CD (USA) (3R)" → "SEGACD_shining_force_cd_usa"
     * "Sonic (USA, Europe)"         → "MD_sonic_usa_europe"
     * "Super Mario World"           → "SNES_super_mario_world"
     */
    protected fun toTitleId(romName: String): String = toTitleId(romName, systemPrefix)

    /**
     * Overload accepting an explicit system prefix (for callers like RetroArch
     * that resolve the system dynamically per-ROM rather than using [systemPrefix]).
     */
    protected fun toTitleId(romName: String, system: String): String {
        val regions = extractRegions(romName)
        val stripped = romName.replace(tagRegex, "").trim()
        val slug = stripped
            .lowercase()
            .replace(Regex("[^a-z0-9]+"), "_")
            .trim('_')
        val base = "${system}_$slug"
        return if (regions.isNotEmpty()) "${base}_${regions.joinToString("_")}" else base
    }

    /**
     * Builds a PS1 title ID slug from a game name, stripping region tags and disc
     * numbers so that all discs of a multi-disc game share the same ID.
     *
     * "Parasite Eve (USA) (Disc 1)" → "PS1_parasite_eve"
     * "Final Fantasy VII [Disc2of3]" → "PS1_final_fantasy_vii"
     */
    protected fun toPs1TitleId(name: String): String {
        val stripped = name
            .replace(Regex("""\s*[\(\[][^\)\]]*[\)\]]"""), "")  // strip (tags) and [tags]
            .trim()
        val slug = stripped
            .lowercase()
            .replace(Regex("[^a-z0-9]+"), "_")
            .trim('_')
        return "PS1_$slug"
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
     * Reads the PS1 disc serial from an ISO/BIN/CUE disc image by parsing the ISO 9660
     * Primary Volume Descriptor and locating SYSTEM.CNF on the disc.
     *
     * Supported formats:
     *  - `.iso`        — standard 2048-byte/sector images
     *  - `.bin` / `.img` / `.mdf` — raw 2352-byte/sector images (data at byte offset 24)
     *  - `.cue`        — resolved to the referenced .bin file
     *
     * Returns a bare product code (e.g. "SLUS01234"), or null if it cannot be determined.
     */
    protected fun readPs1Serial(romFile: File): String? {
        return try {
            val resolved = resolvePsDiscImage(romFile) ?: return null
            val offsets = buildDiscOffsets(resolved)

            for ((sectorSize, dataOffset) in offsets) {
                val serial = readPsSerialFromIso(resolved, sectorSize, dataOffset)
                if (serial != null) return serial
            }
            null
        } catch (_: Exception) { null }
    }

    /**
     * Reads the PS2 disc serial from an ISO/BIN/CUE image.
     *
     * PS2 discs use the same SYSTEM.CNF/BOOT parsing strategy as PS1, including
     * BOOT2 entries like `BOOT2 = cdrom:\SLUS_20002.00;1`, so we can reuse the
     * same low-level parser and return the compact product code (e.g. SLUS20002).
     */
    protected fun readPs2Serial(romFile: File): String? = readPs1Serial(romFile)

    private fun resolvePsDiscImage(romFile: File): File? {
        if (romFile.extension.lowercase() != "cue") return romFile

        val fileLine = romFile.readLines().firstOrNull {
            it.trimStart().uppercase().startsWith("FILE")
        } ?: return null
        val referencedName = Regex("FILE\\s+\"(.+?)\"", RegexOption.IGNORE_CASE)
            .find(fileLine)
            ?.groupValues
            ?.getOrNull(1)
            ?: return null
        return File(romFile.parent, referencedName).takeIf { it.exists() }
    }

    /**
     * Raw BIN-like images are not consistent: MODE1/2352 usually stores user data at
     * byte 16, while MODE2/2352 commonly uses byte 24. We try both so cue/bin dumps from
     * different tools still resolve.
     */
    private fun buildDiscOffsets(file: File): List<Pair<Int, Long>> {
        return when (file.extension.lowercase()) {
            "bin", "img", "mdf" -> listOf(2352 to 24L, 2352 to 16L, 2048 to 0L)
            else -> listOf(2048 to 0L, 2352 to 24L, 2352 to 16L)
        }
    }

    private fun readPsSerialFromIso(file: File, sectorSize: Int, dataOffset: Long): String? {
        java.io.RandomAccessFile(file, "r").use { raf ->
            fun le32(buf: ByteArray, off: Int): Int =
                (buf[off].toInt() and 0xFF) or
                ((buf[off + 1].toInt() and 0xFF) shl 8) or
                ((buf[off + 2].toInt() and 0xFF) shl 16) or
                ((buf[off + 3].toInt() and 0xFF) shl 24)

            fun sector(lba: Int): ByteArray? {
                val pos = lba.toLong() * sectorSize + dataOffset
                if (pos < 0 || pos + 2048 > raf.length()) return null
                val buf = ByteArray(2048)
                raf.seek(pos)
                raf.readFully(buf)
                return buf
            }

            val pvd = sector(16) ?: return null
            if (String(pvd, 1, 5, Charsets.US_ASCII) != "CD001") return null

            val rootRecordOffset = 156
            val rootLba = le32(pvd, rootRecordOffset + 2)
            val rootSize = le32(pvd, rootRecordOffset + 10)
            if (rootLba <= 0 || rootSize <= 0) return null

            val rootDirBytes = ByteArray(rootSize)
            var copied = 0
            var lba = rootLba
            while (copied < rootSize) {
                val sec = sector(lba++) ?: break
                val remaining = rootSize - copied
                val count = minOf(sec.size, remaining)
                System.arraycopy(sec, 0, rootDirBytes, copied, count)
                copied += count
            }
            if (copied <= 0) return null

            var pos = 0
            while (pos < copied) {
                val recLen = rootDirBytes[pos].toInt() and 0xFF
                if (recLen == 0) {
                    pos = ((pos / 2048) + 1) * 2048
                    continue
                }
                if (pos + recLen > copied) break

                val flags = rootDirBytes[pos + 25].toInt() and 0xFF
                val nameLen = rootDirBytes[pos + 32].toInt() and 0xFF
                if (nameLen > 0 && flags and 0x02 == 0) {
                    val rawName = String(rootDirBytes, pos + 33, nameLen, Charsets.US_ASCII)
                    val name = rawName.substringBefore(';').uppercase()
                    if (name == "SYSTEM.CNF") {
                        val fileLba = le32(rootDirBytes, pos + 2)
                        val fileSize = le32(rootDirBytes, pos + 10)
                        if (fileLba <= 0 || fileSize <= 0) return null

                        val cnfBytes = ByteArray(minOf(fileSize, 4096))
                        var fileCopied = 0
                        var fileSector = fileLba
                        while (fileCopied < cnfBytes.size) {
                            val sec = sector(fileSector++) ?: break
                            val remaining = cnfBytes.size - fileCopied
                            val count = minOf(sec.size, remaining)
                            System.arraycopy(sec, 0, cnfBytes, fileCopied, count)
                            fileCopied += count
                        }
                        val cnf = String(cnfBytes, 0, fileCopied, Charsets.US_ASCII)
                        val match = Regex(
                            """BOOT\d?\s*=\s*cdrom\d*[:\\]+([A-Z]{4})[_-](\d{5})""",
                            RegexOption.IGNORE_CASE
                        ).find(cnf) ?: return null
                        return match.groupValues[1].uppercase() + match.groupValues[2]
                    }
                }
                pos += recLen
            }
        }
        return null
    }

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
