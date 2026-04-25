package com.savesync.android.emulators.impl

import com.savesync.android.emulators.EmulatorBase
import com.savesync.android.emulators.SaveEntry
import com.savesync.android.sync.SaturnSyncFormat
import org.json.JSONObject
import java.io.File

/**
 * @param romScanDir If non-empty, its immediate subfolders are scanned for ROMs as a
 *   Tier-4 discovery step. Subfolder names are mapped to systems using the same
 *   heuristics as playlist/core_name resolution.
 *   E.g. "/storage/sdcard1/Isos" with subfolders GBA/, MegaDrive/, PS1/, …
 */
class RetroArchEmulator(
    private val romScanDir: String = "",
    private val romDirOverrides: Map<String, String> = emptyMap(),
    private val saturnSyncFormat: SaturnSyncFormat = SaturnSyncFormat.MEDNAFEN,
    /**
     * When true, predicted Saturn-Mednafen save downloads land under
     * saves/Beetle Saturn/<rom>.bkr (matching RetroArch's "Sort Saves into
     * Folders by Core Name" option for the Beetle Saturn core). When false,
     * they land at saves/<rom>.bkr.  Existing files are still discovered in
     * either location regardless of this toggle.
     *
     * TODO(per-core refactor): replace with a Map<CoreName, Boolean> once the
     * UI exposes the option per core.
     */
    private val beetleSaturnPerCoreFolder: Boolean = true,
    /**
     * When true, predicted save paths for CD-based systems (PS1, PS2, Saturn,
     * Sega CD, Dreamcast, PC Engine, Neo Geo CD) gain a per-game subfolder
     * named after the disc-tag-stripped game stem.  Mirrors RetroArch's
     * "Sort Saves into Folders by Content Directory" config, scoped to CD
     * systems where it actually matters.  Saturn YabaSanshiro is exempt
     * (single shared backup.bin).
     */
    private val cdGamesPerContentFolder: Boolean = false,
    /**
     * Optional explicit save folder override.  When set, takes precedence
     * over both the user's ``retroarch.cfg`` ``savefile_directory`` and the
     * built-in ``<base>/saves/`` auto-detection.  Configured in the
     * Emulator Configuration screen and persisted in
     * ``Settings.saveDirOverrides[EMULATOR_KEY]``.
     */
    private val saveDirOverride: String? = null
) : EmulatorBase() {

    override val name: String = "RetroArch"
    override val systemPrefix: String = "RETRO"

    companion object {
        /** Key used in [com.savesync.android.storage.Settings.saveDirOverrides]. */
        const val EMULATOR_KEY = "RetroArch"

        /**
         * Locations RetroArch may be installed to on Android, in priority order.
         * Mirrors [retroArchBaseCandidates] but exposed statically so other
         * callers (server-only placeholder builders) can find a saves root
         * without constructing a full emulator instance.
         */
        private fun candidateBases(externalStorage: File): List<File> = listOf(
            File(externalStorage, "RetroArch"),
            File(externalStorage, "retroarch"),
            File(externalStorage, "Android/data/com.retroarch.aarch64/files"),
            File(externalStorage, "Android/data/com.retroarch/files"),
            File(externalStorage, "Android/data/com.retroarch.ra32/files"),
            File(externalStorage, "Android/data/com.retroarch.plus/files"),
        )

        /**
         * Returns the first existing RetroArch saves directory, or — when
         * [allowNonExistent] is true — the best-guess predicted path so server
         * downloads still have somewhere to land before the user installs
         * RetroArch or creates a save.
         */
        fun findSavesDir(externalStorage: File, allowNonExistent: Boolean = false): File? {
            val bases = candidateBases(externalStorage)
            val existingBase = bases.firstOrNull { it.exists() && it.isDirectory }
            if (existingBase != null) {
                val saves = File(existingBase, "saves")
                if (saves.exists() && saves.isDirectory) return saves
                if (allowNonExistent) return saves
            }
            return if (allowNonExistent) File(bases.first(), "saves") else null
        }

        /**
         * Predicted ``<savesDir>/<stem>.<ext>`` for a retroarch-backed system.
         * The stem is derived from [label] with disc/bracket tags stripped so
         * multi-disc titles don't end up with per-disc duplicate saves.
         *
         * For Saturn (Mednafen), [beetleSaturnPerCoreFolder] mirrors RetroArch's
         * "Sort Saves into Folders by Core Name" setting: when true the file
         * lands under ``saves/Beetle Saturn/<stem>.bkr``, otherwise at
         * ``saves/<stem>.bkr``.
         *
         * For CD-based systems, [cdGamesPerContentFolder] mirrors RetroArch's
         * "Sort Saves into Folders by Content Directory" setting and adds a
         * per-game subfolder (e.g. ``saves/Grandia/Grandia (Disc 1).bkr``).
         * Layered after the per-core folder so both can be on at once.
         */
        fun defaultSaveFile(
            externalStorage: File,
            system: String,
            label: String,
            saturnSyncFormat: SaturnSyncFormat = SaturnSyncFormat.MEDNAFEN,
            beetleSaturnPerCoreFolder: Boolean = true,
            cdGamesPerContentFolder: Boolean = false,
        ): File? {
            val savesDir = findSavesDir(externalStorage, allowNonExistent = true) ?: return null
            val stem = sanitizeLabel(label)
            val sys = system.uppercase()
            val base = if (sys == "SAT") {
                when (saturnSyncFormat) {
                    SaturnSyncFormat.MEDNAFEN -> {
                        if (beetleSaturnPerCoreFolder) {
                            File(File(savesDir, "Beetle Saturn"), "$stem.bkr")
                        } else {
                            File(savesDir, "$stem.bkr")
                        }
                    }
                    SaturnSyncFormat.YABAUSE -> File(savesDir, "$stem.srm")
                    SaturnSyncFormat.YABASANSHIRO -> File(savesDir, "backup.bin")
                }
            } else {
                File(savesDir, "$stem.srm")
            }
            return applyPerContentFolder(base, stem, sys, cdGamesPerContentFolder)
        }

        /**
         * System codes that store games on CD/disc images (cue/bin/iso/chd).
         * Used to scope the "Sort Saves into Folders by Content Directory"
         * toggle so flat-ROM systems (GBA, SNES, NES …) keep their default
         * single-file save layout.
         */
        internal val CD_SYSTEMS: Set<String> = setOf(
            "PS1", "PS2", "SAT", "SEGACD", "DC", "PCE", "NEOCD"
        )

        /**
         * Sanitised per-game subfolder name used when ``cdGamesPerContentFolder``
         * is on.  Strips disc/side/cd tags so multi-disc titles share a
         * folder, then strips filesystem-unsafe characters.  Returns null when
         * the result would be empty (caller falls back to the flat layout).
         */
        internal fun perContentFolderName(romName: String): String? {
            val stripped = romName
                .replace(
                    Regex(
                        """\s*[\(\[]\s*(disc|cd|side)\s*\d+(?:\s*of\s*\d+)?\s*[\)\]]""",
                        RegexOption.IGNORE_CASE
                    ), ""
                )
                .replace(Regex("""[\\/:*?"<>|]"""), "")
                .replace(Regex("""\s+"""), " ")
                .trim()
            return stripped.ifBlank { null }
        }

        /**
         * Wraps [baseFile] in a per-game subfolder when ``cdGamesPerContentFolder``
         * applies for [system].  When [enabled] is false or the system isn't
         * CD-based, returns [baseFile] unchanged.  Used by every Saturn/CD
         * path predictor so the toggle is honoured uniformly.
         */
        internal fun applyPerContentFolder(
            baseFile: File,
            romName: String,
            system: String,
            enabled: Boolean
        ): File {
            if (!enabled) return baseFile
            if (system.uppercase() !in CD_SYSTEMS) return baseFile
            // YabaSanshiro stores everything in a single shared backup.bin —
            // a per-game subfolder doesn't make sense for it.
            if (baseFile.name.equals("backup.bin", ignoreCase = true)) return baseFile
            val subfolderName = perContentFolderName(romName) ?: return baseFile
            val parent = baseFile.parentFile ?: return baseFile
            return File(File(parent, subfolderName), baseFile.name)
        }

        /**
         * Strip disc/bracket tags and filesystem-unsafe characters so the
         * predicted save path stays writable.  Mirrors the Steam Deck's
         * ``_clean_stem`` so both clients converge on the same on-disk name
         * when only server metadata is available.
         */
        private fun sanitizeLabel(label: String): String {
            return label
                .replace(Regex("""\s*[\(\[]\s*(disc|cd|side)\s*\d+(?:\s*of\s*\d+)?\s*[\)\]]""",
                    RegexOption.IGNORE_CASE), "")
                .replace(Regex("""\s*[\(\[][A-Z]{4}[-_ ]?\d{5}.*?[\)\]]""",
                    RegexOption.IGNORE_CASE), "")
                .replace(Regex("""[\\/:*?"<>|]"""), "")
                .replace(Regex("""\s+"""), " ")
                .trim()
                .ifBlank { "game" }
        }
    }

    private val saveExtensions = setOf("srm", "sav", "savestate", "state", "saveram", "bkr")

    // PS1 disc image extensions that readPs1Serial() can handle
    private val ps1RomExtensions = setOf("iso", "bin", "cue", "img", "mdf")

    // Saturn disc image extensions recognised for ROM path mapping and product-code lookup
    private val satRomExtensions = setOf("iso", "bin", "cue", "img", "chd")

    internal fun defaultSaveExtension(system: String): String {
        return when (system.uppercase()) {
            "SAT" -> when (saturnSyncFormat) {
                SaturnSyncFormat.MEDNAFEN -> "bkr"
                SaturnSyncFormat.YABAUSE -> "srm"
                SaturnSyncFormat.YABASANSHIRO -> "bin"
            }
            else -> "srm"
        }
    }

    internal fun expectedRetroArchSaturnSaveFile(savesDir: File, romName: String): File {
        val direct = when (saturnSyncFormat) {
            SaturnSyncFormat.MEDNAFEN -> File(savesDir, "$romName.bkr")
            SaturnSyncFormat.YABAUSE -> File(savesDir, "$romName.srm")
            SaturnSyncFormat.YABASANSHIRO -> File(savesDir, "backup.bin")
        }

        val coreSpecific = when (saturnSyncFormat) {
            SaturnSyncFormat.MEDNAFEN -> File(File(savesDir, "Beetle Saturn"), "$romName.bkr")
            SaturnSyncFormat.YABAUSE -> File(File(savesDir, "yabause"), "$romName.srm")
            SaturnSyncFormat.YABASANSHIRO -> File(File(savesDir, "yabasanshiro"), "backup.bin")
        }

        // Per-content-folder variants of each layout, so a save that's already
        // on disk in the per-game subfolder layout is found first regardless
        // of the user's current toggle state.
        val directPerContent = applyPerContentFolder(direct, romName, "SAT", true)
        val coreSpecificPerContent = applyPerContentFolder(coreSpecific, romName, "SAT", true)

        // Existing files always win regardless of the toggles, so users with
        // saves already on disk in any layout keep working without a
        // migration step.
        if (coreSpecificPerContent.exists()) return coreSpecificPerContent
        if (coreSpecific.exists()) return coreSpecific
        if (directPerContent.exists()) return directPerContent
        if (direct.exists()) return direct

        // Neither file exists yet — pick the default location for new
        // downloads.  The Beetle Saturn (Mednafen) toggle mirrors RetroArch's
        // "Sort Saves into Folders by Core Name" config; the other Saturn
        // cores keep their established defaults.  Then layer the
        // "Sort Saves into Folders by Content Directory" toggle on top.
        val baseDefault = when (saturnSyncFormat) {
            SaturnSyncFormat.MEDNAFEN -> if (beetleSaturnPerCoreFolder) coreSpecific else direct
            SaturnSyncFormat.YABAUSE -> direct
            SaturnSyncFormat.YABASANSHIRO -> coreSpecific
        }
        return applyPerContentFolder(baseDefault, romName, "SAT", cdGamesPerContentFolder)
    }

    internal fun shouldTrackRetroArchSaveFile(file: File, system: String): Boolean {
        val ext = file.extension.lowercase()
        if (ext !in saveExtensions) return false
        if (system != "SAT") return true

        return when (saturnSyncFormat) {
            SaturnSyncFormat.MEDNAFEN -> ext == "bkr"
            SaturnSyncFormat.YABAUSE -> ext == "srm"
            SaturnSyncFormat.YABASANSHIRO -> file.name.equals("backup.bin", ignoreCase = true)
        }
    }

    private fun isSharedYabaSanshiroBackup(file: File, system: String): Boolean {
        return system == "SAT" &&
            saturnSyncFormat == SaturnSyncFormat.YABASANSHIRO &&
            file.name.equals("backup.bin", ignoreCase = true)
    }

    // Playlist filename keyword → system prefix
    // RetroArch playlist names follow the No-Intro / Redump naming convention
    private val playlistSystemMap = mapOf(
        "game boy advance"          to "GBA",
        "gba"                       to "GBA",
        "super nintendo"            to "SNES",
        "snes"                      to "SNES",
        "nintendo entertainment"    to "NES",
        " nes"                      to "NES",
        "game boy color"            to "GBC",
        "game boy"                  to "GB",       // must be after "game boy color/advance"
        "nintendo 64"               to "N64",
        "n64"                       to "N64",
        "playstation"               to "PS1",
        "psx"                       to "PS1",
        "sega genesis"              to "MD",
        "mega drive"                to "MD",
        "genesis"                   to "MD",
        "sega master"               to "SMS",
        "master system"             to "SMS",
        "game gear"                 to "GG",
        "sega cd"                   to "SEGACD",
        "sega saturn"               to "SAT",
        "saturn"                    to "SAT",
        "pc engine"                 to "PCE",
        "turbografx"                to "PCE",
        "neo geo pocket"            to "NGP",
        "wonderswan color"          to "WSWANC",
        "wonderswan"                to "WSWAN",
        "atari 2600"                to "A2600",
        "atari 7800"                to "A7800",
        "atari lynx"                to "LYNX",
        "nintendo ds"               to "NDS",
        "mame"                      to "MAME",
        "arcade"                    to "ARCADE",
        "fba"                       to "FBA",
        "final burn"                to "FBA",
        "dreamcast"                 to "DC",
        "nintendo - gamecube"       to "GC",
        "gamecube"                  to "GC",
        "psp"                       to "PSP"
    )

    // Known system subfolder names → system prefix
    private val systemFolderMap = mapOf(
        "GBA" to "GBA", "SNES" to "SNES", "NES" to "NES",
        "GB" to "GB", "GBC" to "GBC", "N64" to "N64",
        "PS1" to "PS1", "PSX" to "PS1", "PSP" to "PSP",
        "GEN" to "MD", "GENESIS" to "MD", "MEGADRIVE" to "MD", "MD" to "MD",
        "SMS" to "SMS", "GG" to "GG", "PCE" to "PCE",
        "SATURN" to "SAT", "SAT" to "SAT", "BEETLE SATURN" to "SAT",
        "KRONOS" to "SAT", "YABAUSE" to "SAT", "YABASANSHIRO" to "SAT",
        "YABASANSHIRO 2" to "SAT", "DC" to "DC",
        "ATARI" to "A2600", "LYNX" to "LYNX", "NGP" to "NGP",
        "WS" to "WSWAN", "WSWAN" to "WSWAN", "WSWANC" to "WSWANC",
        "WONDERSWAN" to "WSWAN", "WONDERSWANCOLOR" to "WSWANC",
        "MAME" to "MAME", "FBA" to "FBA", "ARCADE" to "ARCADE",
        "NDS" to "NDS", "GC" to "GC"
    )

    // All candidate base directories where RetroArch might be installed
    private fun retroArchBaseCandidates(): List<File> {
        val ext = baseDir
        return listOf(
            // Standard public external storage
            File(ext, "RetroArch"),
            File(ext, "retroarch"),
            // 64-bit package (most common on modern Android)
            File(ext, "Android/data/com.retroarch.aarch64/files"),
            // Standard package
            File(ext, "Android/data/com.retroarch/files"),
            // 32-bit package
            File(ext, "Android/data/com.retroarch.ra32/files"),
            // Some OEM/custom builds
            File(ext, "Android/data/com.retroarch.plus/files"),
        ).filter { it.exists() && it.isDirectory }
    }

    override fun retroarchDiagnosticPaths(): List<Pair<String, Boolean>> {
        val ext = baseDir
        return listOf(
            "RetroArch", "retroarch",
            "Android/data/com.retroarch.aarch64/files",
            "Android/data/com.retroarch/files",
            "Android/data/com.retroarch.ra32/files"
        ).map { rel ->
            val f = File(ext, rel)
            "$rel/saves" to File(f, "saves").exists()
        }
    }

    /**
     * Returns the RetroArch save directory, preferring a custom path configured in retroarch.cfg.
     *
     * @param allowNonExistent if true, returns a best-guess path even if the directory doesn't
     *   exist yet — used by [discoverRomEntries] so server-only entries still get a correct
     *   expected save path (the file just hasn't been downloaded yet).
     */
    private fun resolveSavesDir(bases: List<File>, allowNonExistent: Boolean = false): File? {
        // Priority 0: explicit user override from EmulatorsScreen.  Wins over
        // both retroarch.cfg and the auto-detected paths so a user who points
        // RetroArch saves at a custom SD-card folder always sees that one.
        if (!saveDirOverride.isNullOrBlank()) {
            val overrideDir = File(saveDirOverride)
            if (overrideDir.exists() && overrideDir.isDirectory) return overrideDir
            if (allowNonExistent) return overrideDir
        }

        // Priority 1: savefile_directory in retroarch.cfg at the base root
        val fromRootCfg = bases.firstNotNullOfOrNull { base ->
            parseSavefileDirectory(File(base, "retroarch.cfg"))
                ?.let { File(it) }?.takeIf { it.exists() && it.isDirectory }
        }
        if (fromRootCfg != null) return fromRootCfg

        // Priority 2: savefile_directory in config/retroarch.cfg (some installations)
        val fromSubCfg = bases.firstNotNullOfOrNull { base ->
            parseSavefileDirectory(File(base, "config/retroarch.cfg"))
                ?.let { File(it) }?.takeIf { it.exists() && it.isDirectory }
        }
        if (fromSubCfg != null) return fromSubCfg

        // Priority 3: standard saves/ subdirectory under any base
        val standard = bases.map { File(it, "saves") }.firstOrNull { it.exists() }
        if (standard != null) return standard

        // Priority 4 (allowNonExistent only): expected path even if dir is absent
        if (allowNonExistent) return bases.firstOrNull()?.let { File(it, "saves") }

        return null
    }

    override fun discoverSaves(): List<SaveEntry> {
        val bases = retroArchBaseCandidates()
        if (bases.isEmpty()) return emptyList()

        // Build playlist-based rom→system map (merging all bases)
        val romSystemMap = bases.fold(mutableMapOf<String, String>()) { acc, base ->
            acc.putAll(buildRomSystemMapFromPlaylists(File(base, "playlists")))
            acc
        }

        // Build a fallback map from the user-specified ROM scan directory.
        // This resolves system for saves whose game was never added to a RetroArch playlist
        // (e.g. launched from the file browser). Keys are lowercase ROM names without extension.
        val romScanSystemMap: Map<String, String> = buildRomScanSystemMap()

        // Build a map of lowercase-rom-name → ROM File path for NDS games.
        // Used to look up the gamecode at ROM offset 0x0C so title IDs match the NDS client.
        val ndsRomPathMap: Map<String, File> = bases.fold(mutableMapOf<String, File>()) { acc, base ->
            acc.putAll(buildNdsRomPathMapFromPlaylists(File(base, "playlists")))
            acc
        }.also { map ->
            // Also populate from the user-specified ROM scan dir (NDS subfolder)
            ndsRomSearchDirs(romScanDir).forEach { dir ->
                dir.listFiles()?.forEach { f ->
                    if (f.isFile && f.extension.lowercase() in setOf("nds", "dsi")) {
                        map.putIfAbsent(f.nameWithoutExtension.lowercase(), f)
                    }
                }
            }
        }

        // Build a map of lowercase-rom-name → ROM File path for PS1 games.
        // Used to read the disc serial from SYSTEM.CNF inside the disc image so that
        // title IDs match the product-code format used by the PSP/Vita clients and PPSSPP.
        val ps1RomPathMap: Map<String, File> = bases.fold(mutableMapOf<String, File>()) { acc, base ->
            acc.putAll(buildPs1RomPathMapFromPlaylists(File(base, "playlists")))
            acc
        }.also { map ->
            if (romScanDir.isNotBlank()) {
                val scanRoot = File(romScanDir)
                listOf("PS1", "ps1", "PSX", "psx", "PlayStation", "playstation",
                       "PlayStation 1", "PlayStation1").forEach { subName ->
                    val dir = File(scanRoot, subName)
                    if (!dir.exists() || !dir.isDirectory) return@forEach
                    dir.listFiles()?.forEach { f ->
                        when {
                            f.isFile && f.extension.lowercase() in ps1RomExtensions ->
                                map.putIfAbsent(f.nameWithoutExtension.lowercase(), f)
                            // Multi-disc games stored in per-game subfolders: use folder name as key,
                            // first disc image inside as the file to read the serial from
                            f.isDirectory -> {
                                val disc = f.listFiles()
                                    ?.firstOrNull { it.isFile && it.extension.lowercase() in ps1RomExtensions }
                                if (disc != null) map.putIfAbsent(f.name.lowercase(), disc)
                            }
                        }
                    }
                }
            }
        }

        // Build a map of lowercase-rom-name → ROM File path for Saturn games.
        // Used to read the product code from the IP.BIN header so title IDs match
        // the server's SAT_<product_code> format (e.g. SAT_T-12705H).
        val satRomPathMap: Map<String, File> = bases.fold(mutableMapOf<String, File>()) { acc, base ->
            acc.putAll(buildSaturnRomPathMapFromPlaylists(File(base, "playlists")))
            acc
        }.also { map ->
            val satDirs = mutableListOf<File>()
            if (romScanDir.isNotBlank()) {
                val scanRoot = File(romScanDir)
                listOf("Saturn", "Sega Saturn", "Sega - Saturn", "SAT", "sat").forEach { subName ->
                    val dir = File(scanRoot, subName)
                    if (dir.exists() && dir.isDirectory) satDirs.add(dir)
                }
            }
            romDirOverrides["SAT"]?.let { File(it) }?.takeIf { it.isDirectory }?.let { satDirs.add(it) }
            satDirs.forEach { dir ->
                dir.listFiles()?.forEach { f ->
                    when {
                        f.isFile && f.extension.lowercase() in satRomExtensions ->
                            map.putIfAbsent(f.nameWithoutExtension.lowercase(), f)
                        // Per-game subfolder: use CUE file inside (preferred) or first BIN/ISO
                        f.isDirectory -> {
                            val disc = f.listFiles()?.firstOrNull {
                                it.isFile && it.extension.lowercase() == "cue"
                            } ?: f.listFiles()?.firstOrNull {
                                it.isFile && it.extension.lowercase() in satRomExtensions
                            }
                            // Key by CUE filename (matches RetroArch save name) and by folder name
                            if (disc != null) {
                                map.putIfAbsent(disc.nameWithoutExtension.lowercase(), disc)
                                map.putIfAbsent(f.name.lowercase(), disc)
                            }
                        }
                    }
                }
            }
        }

        // Prefer the save dir declared in retroarch.cfg; fall back to the standard path.
        val savesDir: File = resolveSavesDir(bases) ?: return emptyList()

        val result = mutableListOf<SaveEntry>()

        // Resolve a save file → SaveEntry, applying system-specific title ID
        // lookups (NDS gamecode, PS1/Saturn product code, …).  Returns null
        // when the file isn't trackable for [forcedSystem] (or when system
        // resolution is ambiguous and forcedSystem is null).
        fun trackSave(file: File, forcedSystem: String? = null): SaveEntry? {
            if (!file.isFile) return null
            val romName = file.nameWithoutExtension
            val lc = romName.lowercase()
            val system = forcedSystem ?: (
                romSystemMap[lc]
                    ?: romSystemMap[romName]
                    ?: romScanSystemMap[lc]
                    ?: systemPrefix
            )
            if (!shouldTrackRetroArchSaveFile(file, system)) return null
            if (isSharedYabaSanshiroBackup(file, system)) return null
            val titleId = when (system) {
                "NDS" -> ndsRomPathMap[lc]?.let { readNdsGamecode(it) } ?: toTitleId(romName, system)
                "PS1" -> ps1RomPathMap[lc]?.let { readPs1Serial(it) } ?: toPs1TitleId(romName)
                "SAT" -> satRomPathMap[lc]?.let { readSaturnProductCode(it) }
                    ?: lookupSaturnSerial(romName)
                    ?: toTitleId(romName, system)
                else  -> toTitleId(romName, system)
            }
            return SaveEntry(
                titleId = titleId,
                displayName = romName,
                systemName = system,
                saveFile = file,
                saveDir = null
            )
        }

        savesDir.listFiles()?.forEach { entry ->
            if (entry.isFile) {
                trackSave(entry)?.let { result.add(it) }
            } else if (entry.isDirectory) {
                val mappedSystem = systemFolderMap[entry.name.uppercase()]
                if (mappedSystem != null) {
                    // Existing behaviour: known system (or core) subfolder.
                    // Walk one level for files; also recurse into per-content
                    // sub-subfolders (e.g. saves/Beetle Saturn/Grandia/Grandia.bkr).
                    entry.listFiles()?.forEach { inner ->
                        when {
                            inner.isFile -> trackSave(inner, mappedSystem)?.let { result.add(it) }
                            inner.isDirectory -> inner.listFiles()?.forEach { f ->
                                if (f.isFile) trackSave(f, mappedSystem)?.let { result.add(it) }
                            }
                        }
                    }
                } else {
                    // Unmapped subfolder — treat as a possible per-content
                    // folder for a CD game.  We can't tell what system it
                    // belongs to from the folder name, so let trackSave()
                    // resolve via the rom-name lookup like at root level.
                    entry.listFiles()?.forEach { inner ->
                        if (inner.isFile) trackSave(inner)?.let { result.add(it) }
                    }
                }
            }
        }

        return result
    }

    /**
     * Scans all *.lpl playlist files in [playlistsDir] for NDS ROM entries and returns
     * a map of lowercase-rom-name → ROM File path. Used for gamecode-based title ID lookup.
     */
    private fun buildNdsRomPathMapFromPlaylists(playlistsDir: File): Map<String, File> {
        if (!playlistsDir.exists()) return emptyMap()
        val map = mutableMapOf<String, File>()
        playlistsDir.listFiles()
            ?.filter { it.isFile && it.extension.lowercase() == "lpl" }
            ?.forEach { lpl ->
                try {
                    val json = JSONObject(lpl.readText())
                    val items = json.optJSONArray("items") ?: return@forEach
                    for (i in 0 until items.length()) {
                        val item = items.optJSONObject(i) ?: continue
                        val path = item.optString("path").takeIf { it.isNotBlank() } ?: continue
                        val coreName = item.optString("core_name").orEmpty()
                        val romFile = File(path)
                        val system = resolveSystemFromCoreName(coreName)
                            ?: resolveSystemFromFolderName(romFile.parentFile?.name.orEmpty())
                            ?: continue
                        if (system == "NDS" && romFile.exists()) {
                            map.putIfAbsent(romFile.nameWithoutExtension.lowercase(), romFile)
                        }
                    }
                } catch (_: Exception) {}
            }
        return map
    }

    /**
     * Scans all *.lpl playlist files in [playlistsDir] for PS1 ROM entries and returns
     * a map of lowercase-rom-name → ROM File path. Used for serial-based title ID lookup.
     */
    private fun buildPs1RomPathMapFromPlaylists(playlistsDir: File): Map<String, File> {
        if (!playlistsDir.exists()) return emptyMap()
        val map = mutableMapOf<String, File>()
        playlistsDir.listFiles()
            ?.filter { it.isFile && it.extension.lowercase() == "lpl" }
            ?.forEach { lpl ->
                try {
                    val json = JSONObject(lpl.readText())
                    val items = json.optJSONArray("items") ?: return@forEach
                    for (i in 0 until items.length()) {
                        val item = items.optJSONObject(i) ?: continue
                        val path = item.optString("path").takeIf { it.isNotBlank() } ?: continue
                        val coreName = item.optString("core_name").orEmpty()
                        val romFile = File(path)
                        val system = resolveSystemFromCoreName(coreName)
                            ?: resolveSystemFromFolderName(romFile.parentFile?.name.orEmpty())
                            ?: continue
                        if (system == "PS1" && romFile.extension.lowercase() in ps1RomExtensions && romFile.exists()) {
                            map.putIfAbsent(romFile.nameWithoutExtension.lowercase(), romFile)
                        }
                    }
                } catch (_: Exception) {}
            }
        return map
    }

    /**
     * Scans all *.lpl playlist files in [playlistsDir] for Saturn ROM entries and returns
     * a map of lowercase-rom-name → ROM File path. Used for product-code title ID lookup.
     * CUE/BIN/ISO/CHD are all included for path mapping.
     */
    private fun buildSaturnRomPathMapFromPlaylists(playlistsDir: File): Map<String, File> {
        if (!playlistsDir.exists()) return emptyMap()
        val map = mutableMapOf<String, File>()
        playlistsDir.listFiles()
            ?.filter { it.isFile && it.extension.lowercase() == "lpl" }
            ?.forEach { lpl ->
                try {
                    val json = JSONObject(lpl.readText())
                    val items = json.optJSONArray("items") ?: return@forEach
                    for (i in 0 until items.length()) {
                        val item = items.optJSONObject(i) ?: continue
                        val path = item.optString("path").takeIf { it.isNotBlank() } ?: continue
                        val coreName = item.optString("core_name").orEmpty()
                        val romFile = File(path)
                        val system = resolveSystemFromCoreName(coreName)
                            ?: resolveSystemFromFolderName(romFile.parentFile?.name.orEmpty())
                            ?: continue
                        if (system == "SAT" && romFile.extension.lowercase() in satRomExtensions && romFile.exists()) {
                            map.putIfAbsent(romFile.nameWithoutExtension.lowercase(), romFile)
                        }
                    }
                } catch (_: Exception) {}
            }
        return map
    }

    /**
     * Builds a map of  lowercase-rom-name → system  by scanning the user's ROM directory.
     * Used as a fallback when a save file isn't covered by any RetroArch playlist.
     */
    private fun buildRomScanSystemMap(): Map<String, String> {
        if (romScanDir.isBlank()) return emptyMap()
        val scanRoot = File(romScanDir)
        if (!scanRoot.exists() || !scanRoot.isDirectory) return emptyMap()

        val map = mutableMapOf<String, String>()

        fun scanSystemDir(dir: File, system: String) {
            dir.listFiles()?.forEach { file ->
                when {
                    file.isFile -> map[file.nameWithoutExtension.lowercase()] = system
                    // CD games in per-game subfolders.
                    // RetroArch names saves after the CUE/CHD *file* it loaded, NOT the folder.
                    // e.g. Saturn/Nights into Dreams/Nights into Dreams (USA).cue
                    //   → save = "Nights into Dreams (USA).bkr"
                    // So we add both the folder name (fallback) AND all CUE/CHD names inside.
                    file.isDirectory -> {
                        map.putIfAbsent(file.name.lowercase(), system)
                        file.listFiles()
                            ?.filter { it.isFile && it.extension.lowercase() in setOf("cue", "chd") }
                            ?.forEach { disc -> map[disc.nameWithoutExtension.lowercase()] = system }
                    }
                }
            }
        }

        // Scan romScanDir subfolders (auto-detected system layout)
        scanRoot.listFiles()?.filter { it.isDirectory }?.forEach { systemDir ->
            val system = resolveSystemFromFolderName(systemDir.name) ?: return@forEach
            scanSystemDir(systemDir, system)
        }

        // Also scan user-specified override directories
        romDirOverrides.forEach { (system, overridePath) ->
            val dir = File(overridePath)
            if (dir.exists() && dir.isDirectory) scanSystemDir(dir, system)
        }

        return map
    }

    /**
     * Parses the `savefile_directory` key from a retroarch.cfg file.
     * Returns null if the file doesn't exist, the key is absent, or value is "default".
     */
    private fun parseSavefileDirectory(cfgFile: File): String? {
        if (!cfgFile.exists()) return null
        return try {
            cfgFile.useLines { lines ->
                lines.firstNotNullOfOrNull { line ->
                    val trimmed = line.trim()
                    if (trimmed.startsWith("savefile_directory")) {
                        trimmed.substringAfter('=').trim().removeSurrounding("\"").trim()
                            .takeIf { it.isNotBlank() && it != "default" && it != ":" }
                    } else null
                }
            }
        } catch (_: Exception) { null }
    }

    /**
     * Returns every ROM the device has for RetroArch, keyed by titleId, with the
     * expected save-file path set (even if the file doesn't exist yet).
     *
     * Three-tier discovery (most reliable → least):
     *  1. Per-system playlists in playlists/ subdir — unlimited, from library scan
     *  2. content_history.lpl + any other root-level .lpl — limited (200 entries default)
     *  3. Direct scan of ROM directories seen in the above playlists — catches games
     *     never launched and handles the circular history buffer limit
     */
    override fun discoverRomEntries(): Map<String, SaveEntry> {
        val bases = retroArchBaseCandidates()
        if (bases.isEmpty()) return emptyMap()

        // Use the same cfg-aware resolution as discoverSaves() so that the saveFile path
        // on server-only entries matches exactly where RetroArch actually stores saves.
        // allowNonExistent=true so entries still get a path even before any save exists.
        val savesDir = resolveSavesDir(bases, allowNonExistent = true) ?: return emptyMap()

        // key = romName.lowercase(), value = (system, originalRomName)
        val romInfo = mutableMapOf<String, Pair<String, String>>()
        // ROM directories seen in playlists — we'll scan these directly too
        val romDirs = mutableSetOf<File>()

        bases.forEach { base ->
            // Tier 1: per-system playlists in playlists/ subdir
            val playlistsSubdir = File(base, "playlists")
            // Tier 2: root-level .lpl files (content_history.lpl etc.)
            val lplFiles = buildList {
                playlistsSubdir.listFiles()
                    ?.filter { it.isFile && it.extension.lowercase() == "lpl" }
                    ?.let { addAll(it) }
                base.listFiles()
                    ?.filter { it.isFile && it.extension.lowercase() == "lpl" }
                    ?.let { addAll(it) }
            }

            lplFiles.forEach { lpl ->
                try {
                    val json = JSONObject(lpl.readText())
                    val items = json.optJSONArray("items") ?: return@forEach
                    for (i in 0 until items.length()) {
                        val item = items.optJSONObject(i) ?: continue
                        val path = item.optString("path").takeIf { it.isNotBlank() } ?: continue
                        val coreName = item.optString("core_name").orEmpty()
                        val romFile = File(path)
                        val originalName = romFile.nameWithoutExtension
                        val parentDir = romFile.parentFile

                        val system = resolveSystemFromCoreName(coreName)
                            ?: resolveSystemFromFolderName(parentDir?.name.orEmpty())
                            ?: continue

                        romInfo.putIfAbsent(originalName.lowercase(), Pair(system, originalName))

                        // Collect parent directory for tier-3 direct scan
                        if (parentDir != null && parentDir.exists()) {
                            romDirs.add(parentDir)
                        }
                    }
                } catch (_: Exception) {}
            }
        }

        // Tier 3: scan every ROM directory we saw in playlists — finds games that were
        // never launched (not in history) and games dropped from the circular buffer
        val romExtensions = setOf(
            // Nintendo handhelds
            "gba", "agb",                           // GBA
            "gb", "gbc", "sgb",                     // GB / GBC
            "nds", "dsi",                           // NDS
            // Nintendo home
            "nes", "unf", "fds",                    // NES / Famicom Disk
            "sfc", "smc", "snes", "fig", "swc",     // SNES (fig/swc are common dumps)
            "n64", "z64", "v64", "n64.zip",         // N64
            // Sony
            "iso", "bin", "cue", "img", "mdf",      // PS1 / PS2 / Saturn / Dreamcast discs
            "pbp", "cso", "psv",                    // PSP / PS1 compressed
            "chd",                                  // universal compressed disc format
            // Sega
            "md", "gen", "smd", "32x", "68k",       // Mega Drive / Genesis / 32X
            "sg", "sms",                            // Sega Master System / SG-1000
            "gg",                                   // Game Gear
            "gdi", "cdi",                           // Dreamcast
            // Arcade / other
            "zip", "7z",                            // MAME / FBA (compressed romsets)
            "pce", "pce.zip",                       // PC Engine
            "ws", "wsc",                            // WonderSwan
            "ngp", "ngc", "ngpc",                   // Neo Geo Pocket
            "lnx",                                  // Atari Lynx
            "a26", "a52", "a78",                    // Atari 2600 / 5200 / 7800
            "col",                                  // ColecoVision
            "rvz", "gcz", "gcm",                    // GameCube compressed
            "wbfs", "wia"                           // Wii
        )
        romDirs.forEach { dir ->
            val system = resolveSystemFromFolderName(dir.name) ?: return@forEach
            dir.listFiles()?.filter { it.isFile && it.extension.lowercase() in romExtensions }
                ?.forEach { romFile ->
                    val originalName = romFile.nameWithoutExtension
                    romInfo.putIfAbsent(originalName.lowercase(), Pair(system, originalName))
                }
        }

        // Tier 4: scan user-specified ROM directory's immediate subfolders.
        // Each subfolder name is matched to a system (e.g. "MegaDrive" → GEN).
        // We scan two levels deep:
        //   Level 1 — files directly inside the system folder (e.g. Isos/GBA/game.gba)
        //   Level 2 — one subfolder per game (e.g. Isos/Saturn/Sonic Jam/disc1.cue)
        //             Common for CD/multi-disc games (PS1, Saturn, Dreamcast, etc.)
        if (romScanDir.isNotBlank()) {
            val scanRoot = File(romScanDir)
            if (scanRoot.exists() && scanRoot.isDirectory) {
                scanRoot.listFiles()?.filter { it.isDirectory }?.forEach { systemDir ->
                    val system = resolveSystemFromFolderName(systemDir.name) ?: return@forEach

                    systemDir.listFiles()?.forEach { entry ->
                        when {
                            // Level 1: ROM file directly in the system folder
                            entry.isFile && entry.extension.lowercase() in romExtensions -> {
                                val name = entry.nameWithoutExtension
                                romInfo.putIfAbsent(name.lowercase(), Pair(system, name))
                            }
                            // Level 2: per-game subfolder (CUE/BIN or CHD layout).
                            // Use the CUE filename as the primary key — RetroArch saves use
                            // the loaded file's name, not the folder name. Also add the folder
                            // name as a fallback so older uploads still match.
                            entry.isDirectory -> {
                                val innerFiles = entry.listFiles() ?: return@forEach
                                val cueFiles = innerFiles.filter {
                                    it.isFile && it.extension.lowercase() == "cue"
                                }
                                val chdFiles = innerFiles.filter {
                                    it.isFile && it.extension.lowercase() == "chd"
                                }
                                val discFiles = (cueFiles + chdFiles).ifEmpty {
                                    // Fallback: any recognised ROM format (e.g. iso, bin)
                                    innerFiles.filter {
                                        it.isFile && it.extension.lowercase() in romExtensions
                                    }.take(1)
                                }
                                if (discFiles.isNotEmpty()) {
                                    // Primary entries: one per CUE/CHD file (matches save names)
                                    discFiles.forEach { disc ->
                                        val name = disc.nameWithoutExtension
                                        romInfo.putIfAbsent(name.lowercase(), Pair(system, name))
                                    }
                                    // Folder-name fallback (matches saves from older app versions)
                                    romInfo.putIfAbsent(entry.name.lowercase(), Pair(system, entry.name))
                                }
                            }
                        }
                    }
                }
            }
        }

        // Tier 5: scan user-specified per-system override directories.
        // Same two-level logic as Tier 4: files directly inside OR per-game subfolders with CUE/CHD.
        romDirOverrides.forEach { (system, overridePath) ->
            val overrideDir = File(overridePath)
            if (!overrideDir.exists() || !overrideDir.isDirectory) return@forEach
            overrideDir.listFiles()?.forEach { entry ->
                when {
                    entry.isFile && entry.extension.lowercase() in romExtensions -> {
                        val name = entry.nameWithoutExtension
                        romInfo.putIfAbsent(name.lowercase(), Pair(system, name))
                    }
                    entry.isDirectory -> {
                        val innerFiles = entry.listFiles() ?: return@forEach
                        val cueFiles = innerFiles.filter { it.isFile && it.extension.lowercase() == "cue" }
                        val chdFiles = innerFiles.filter { it.isFile && it.extension.lowercase() == "chd" }
                        val discFiles = (cueFiles + chdFiles).ifEmpty {
                            innerFiles.filter { it.isFile && it.extension.lowercase() in romExtensions }.take(1)
                        }
                        if (discFiles.isNotEmpty()) {
                            discFiles.forEach { disc ->
                                val name = disc.nameWithoutExtension
                                romInfo.putIfAbsent(name.lowercase(), Pair(system, name))
                            }
                            romInfo.putIfAbsent(entry.name.lowercase(), Pair(system, entry.name))
                        }
                    }
                }
            }
        }

        // Build a saturn product-code map for this emulator's ROM entries too,
        // so discoverRomEntries anchors server-only SAT saves by product code.
        val satRomPathForEntries: Map<String, File> = run {
            val map = mutableMapOf<String, File>()
            bases.forEach { base ->
                map.putAll(buildSaturnRomPathMapFromPlaylists(File(base, "playlists")))
            }
            val satDirs = mutableListOf<File>()
            if (romScanDir.isNotBlank()) {
                val scanRoot = File(romScanDir)
                listOf("Saturn", "Sega Saturn", "Sega - Saturn", "SAT", "sat").forEach { sub ->
                    val d = File(scanRoot, sub)
                    if (d.exists() && d.isDirectory) satDirs.add(d)
                }
            }
            romDirOverrides["SAT"]?.let { File(it) }?.takeIf { it.isDirectory }?.let { satDirs.add(it) }
            satDirs.forEach { dir ->
                dir.listFiles()?.forEach { f ->
                    when {
                        f.isFile && f.extension.lowercase() in satRomExtensions ->
                            map.putIfAbsent(f.nameWithoutExtension.lowercase(), f)
                        f.isDirectory -> {
                            val disc = f.listFiles()?.firstOrNull { it.isFile && it.extension.lowercase() == "cue" }
                                ?: f.listFiles()?.firstOrNull { it.isFile && it.extension.lowercase() in satRomExtensions }
                            if (disc != null) {
                                map.putIfAbsent(disc.nameWithoutExtension.lowercase(), disc)
                                map.putIfAbsent(f.name.lowercase(), disc)
                            }
                        }
                    }
                }
            }
            map
        }

        return romInfo.values.mapNotNull { (system, romName) ->
            val titleId = when (system) {
                "SAT" -> satRomPathForEntries[romName.lowercase()]
                    ?.let { readSaturnProductCode(it) }
                    ?: lookupSaturnSerial(romName)
                    ?: toTitleId(romName, system)
                else -> toTitleId(romName, system)
            }
            // Saturn has its own multi-layout resolver (per-core, per-content,
            // shared backup.bin).  All other RetroArch-backed systems use the
            // simple flat layout, optionally wrapped in a per-content
            // subfolder for CD-based systems when the toggle is on.
            val saveFile = if (system == "SAT") {
                expectedRetroArchSaturnSaveFile(savesDir, romName)
            } else {
                val flat = File(savesDir, "$romName.${defaultSaveExtension(system)}")
                val perContent = applyPerContentFolder(flat, romName, system, true)
                when {
                    perContent.exists() -> perContent
                    flat.exists() -> flat
                    else -> applyPerContentFolder(flat, romName, system, cdGamesPerContentFolder)
                }
            }
            titleId to SaveEntry(
                titleId = titleId,
                displayName = romName,
                systemName = system,
                saveFile = saveFile,
                saveDir = null
            )
        }.toMap()
    }

    /**
     * Parses all *.lpl files in the given playlists directory.
     * Uses two signals (in priority order):
     *   1. core_name field  e.g. "Nintendo - Game Boy Advance (mGBA)" → GBA
     *   2. ROM parent folder name  e.g. /Isos/GBA/ → GBA
     *
     * Returns map: lowercase rom-filename-without-extension → system prefix
     */
    private fun buildRomSystemMapFromPlaylists(playlistsDir: File): Map<String, String> {
        if (!playlistsDir.exists()) return emptyMap()

        val map = mutableMapOf<String, String>()
        playlistsDir.listFiles()
            ?.filter { it.isFile && it.extension.lowercase() == "lpl" }
            ?.forEach { lpl ->
                try {
                    parseLplIntoMap(lpl, map)
                } catch (_: Exception) { /* malformed, skip */ }
            }
        return map
    }

    private fun parseLplIntoMap(lpl: File, map: MutableMap<String, String>) {
        val json = JSONObject(lpl.readText())
        val items = json.optJSONArray("items") ?: return
        for (i in 0 until items.length()) {
            val item = items.optJSONObject(i) ?: continue
            val path = item.optString("path").takeIf { it.isNotBlank() } ?: continue
            val coreName = item.optString("core_name").orEmpty()
            val romFile = File(path)

            // Strip zip/compressed wrapper extension too (RetroArch saves use inner-name)
            val baseName = romFile.nameWithoutExtension.lowercase()

            val system = resolveSystemFromCoreName(coreName)
                ?: resolveSystemFromFolderName(romFile.parentFile?.name.orEmpty())
                ?: continue

            // Don't overwrite a better (non-fallback) match already in the map
            if (!map.containsKey(baseName)) {
                map[baseName] = system
            }
        }
    }

    /**
     * Maps core_name string → system prefix.
     * Checks substrings so it works across all core variants.
     */
    private fun resolveSystemFromCoreName(coreName: String): String? {
        val lower = coreName.lowercase()
        return when {
            "game boy advance" in lower                          -> "GBA"
            "snes" in lower || "super nes" in lower
                    || "super nintendo" in lower                 -> "SNES"
            "nintendo 64" in lower || "n64" in lower
                    || "parallel n64" in lower
                    || "mupen64" in lower                        -> "N64"
            "game boy color" in lower                           -> "GBC"
            "game boy" in lower                                 -> "GB"
            "nes)" in lower || "famicom" in lower
                    || "nintendo entertainment" in lower         -> "NES"
            "playstation" in lower && "2" !in lower             -> "PS1"
            "saturn" in lower                                   -> "SAT"
            "neo geo cd" in lower || "neocd" in lower           -> "NEOCD"
            "neo geo pocket" in lower || "ngpc" in lower
                    || "race)" in lower                         -> "NGP"
            // Genesis/Mega Drive cores cover MS/GG/MD/CD — use folder to disambiguate
            // (handled by folder fallback below)
            "genesis" in lower || "mega drive" in lower
                    || "picodrive" in lower
                    || "genesis plus" in lower                   -> null  // defer to folder
            "dreamcast" in lower                                -> "DC"
            "pc engine" in lower || "turbografx" in lower       -> "PCE"
            "arcade" in lower || "fbneo" in lower
                    || "fbalpha" in lower || "mame" in lower     -> "ARCADE"
            "wonderswan color" in lower                      -> "WSWANC"
            "wonderswan" in lower                               -> "WSWAN"
            "atari lynx" in lower || "mednafen_lynx" in lower   -> "LYNX"
            "psp" in lower                                      -> "PSP"
            else                                                -> null
        }
    }

    /**
     * Maps ROM parent folder name → system prefix.
     * Handles all the common naming conventions users put on their SD cards,
     * e.g. "GBA", "GameBoyAdvance", "MegaDrive", "Mega Drive", "NeoGeoCD", etc.
     */
    internal fun resolveSystemFromFolderName(folder: String): String? {
        val upper = folder.uppercase().replace(" ", "").replace("_", "").replace("-", "")
        return when {
            // ── Nintendo handhelds ──────────────────────────────────────
            "GBA" in upper || "GAMEBOYADVANCE" in upper         -> "GBA"
            "GBC" in upper || "GAMEBOYCOLOR" in upper
                    || "GAMEBOY COLOR" in folder.uppercase()     -> "GBC"
            // "GAMEBOY" must come after GBC/GBA checks
            "GAMEBOY" in upper || upper == "GB"                 -> "GB"
            "NDS" in upper || "NINTENDODS" in upper
                    || "DS" == upper || "NDS" == upper           -> "NDS"
            "3DS" in upper                                      -> "3DS"

            // ── Nintendo home consoles ───────────────────────────────────
            "SNES" in upper || "SFC" in upper
                    || "SUPERNINTENDO" in upper
                    || "SUPERNES" in upper                       -> "SNES"
            "NES" in upper || "FAMICOM" in upper
                    || upper == "NES"                            -> "NES"
            "N64" in upper || "NINTENDO64" in upper             -> "N64"
            "GAMECUBE" in upper || upper == "GC"                -> "GC"
            "WII" in upper                                      -> "WII"

            // ── Sony ────────────────────────────────────────────────────
            // PS2 before PS1 so "PS2" doesn't match "PS1" check
            "PS2" in upper || "PLAYSTATION2" in upper           -> "PS2"
            "PS1" in upper || "PSX" in upper
                    || "PLAYSTATION1" in upper
                    || upper == "PLAYSTATION"
                    || upper == "PSX"                           -> "PS1"
            "PSP" in upper                                      -> "PSP"

            // ── Sega ────────────────────────────────────────────────────
            // MegaCD / Sega CD must come before MegaDrive / Genesis
            "MEGACD" in upper || "SEGACD" in upper
                    || "MEGACD" in upper                         -> "SEGACD"
            "MEGADRIVE" in upper || "MEGA DRIVE" in folder.uppercase()
                    || "GENESIS" in upper                        -> "MD"
            "SATURN" in upper                                   -> "SAT"
            "DREAMCAST" in upper || upper == "DC"               -> "DC"
            // Master System before Game Gear to avoid substring conflict
            "MASTERSYSTEM" in upper || "SEGAMASTERSYSTEM" in upper
                    || upper == "SMS"                           -> "SMS"
            // Game Gear — "GG" alone, or longer forms
            upper == "GG" || "GAMEGEAR" in upper
                    || "GAME GEAR" in folder.uppercase()        -> "GG"

            // ── SNK ─────────────────────────────────────────────────────
            "NEOGEOCD" in upper || "NEOCD" in upper
                    || "NCD" in upper                            -> "NEOCD"
            "NGPC" in upper || "NEOGEOPOCKET" in upper
                    || "NGP" in upper                           -> "NGP"

            // ── Arcade ──────────────────────────────────────────────────
            "ARCADE" in upper || "FBA" in upper
                    || "MAME" in upper                           -> "ARCADE"

            // ── NEC ─────────────────────────────────────────────────────
            "PCE" in upper || "PCENGINE" in upper
                    || "TURBOGRAFX" in upper                     -> "PCE"

            // ── Bandai / Atari / other ───────────────────────────────────
            "WONDERSWANCOLOR" in upper || "WSWANC" in upper
                    || upper == "WONDERSWAN COLOR"               -> "WSWANC"
            "WONDERSWAN" in upper || upper == "WS"
                    || upper == "WSWAN"                          -> "WSWAN"
            "LYNX" in upper                                     -> "LYNX"
            "ATARI2600" in upper || "A2600" in upper            -> "A2600"
            "ATARI7800" in upper || "A7800" in upper            -> "A7800"

            else                                                -> null
        }
    }

    private fun resolveSystemFromPlaylistName(playlistName: String): String? {
        val lower = playlistName.lowercase()
        for ((keyword, system) in playlistSystemMap) {
            if (lower.contains(keyword)) return system
        }
        return null
    }
}
