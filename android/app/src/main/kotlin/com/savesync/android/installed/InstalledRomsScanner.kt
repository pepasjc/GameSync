package com.savesync.android.installed

import com.savesync.android.systems.SystemAliases
import java.io.File

/**
 * Walks the user's on-device ROM directories, groups disc track sets
 * (cue + bin, gdi + tracks) under a single primary, and exposes a
 * delete helper that collapses a dedicated per-game subfolder into a
 * single recursive-delete when safe.
 *
 * Kept free of Android-framework deps so it can be unit tested under
 * plain JUnit.
 */
object InstalledRomsScanner {

    /** System code → candidate subfolder names, in preference order.
     *  First existing folder wins.  Mirrors ``SYSTEM_ROM_DIRS`` from
     *  the Steam Deck scanner and the per-system candidates in
     *  ``SyncEngine.downloadRom``. */
    val SYSTEM_ROM_DIRS: Map<String, List<String>> = linkedMapOf(
        // Sony
        "PS1"    to listOf("PS1", "PSX", "PlayStation", "PlayStation 1", "psx", "ps1"),
        "PS2"    to listOf("PS2", "PlayStation 2", "PlayStation2", "ps2"),
        "PS3"    to listOf("PS3", "PlayStation 3", "PlayStation3", "ps3"),
        "PSP"    to listOf("PSP", "PlayStation Portable", "psp"),
        "VITA"   to listOf("psvita", "PSVITA", "Vita", "PS Vita"),
        // Nintendo
        "GBA"    to listOf("GBA", "Game Boy Advance", "GameBoyAdvance", "gba"),
        "GB"     to listOf("GB", "Game Boy", "GameBoy", "gb"),
        "GBC"    to listOf("GBC", "Game Boy Color", "GameBoyColor", "gbc"),
        "NES"    to listOf("NES", "Nintendo", "Famicom", "nes"),
        "SNES"   to listOf("SNES", "Super Nintendo", "SuperNintendo", "snes"),
        "N64"    to listOf("N64", "Nintendo 64", "Nintendo64", "n64"),
        "GC"     to listOf("GC", "GameCube", "Nintendo GameCube", "gc"),
        "WII"    to listOf("Wii", "wii"),
        "NDS"    to listOf("NDS", "DS", "Nintendo DS", "nds"),
        "3DS"    to listOf("3DS", "Nintendo 3DS", "3ds", "n3ds"),
        "VB"     to listOf("VirtualBoy", "Virtual Boy", "VB", "virtualboy"),
        // Sega
        "MD"     to listOf("Mega Drive", "Genesis", "MegaDrive", "MD", "md", "megadrive", "genesis"),
        "SEGACD" to listOf("Sega CD", "Mega CD", "SegaCD", "MegaCD", "segacd", "megacd"),
        "SMS"    to listOf("Master System", "Sega Master System", "SMS", "mastersystem"),
        "GG"     to listOf("Game Gear", "GameGear", "GG", "gamegear"),
        "SAT"    to listOf("Saturn", "Sega Saturn", "Sega - Saturn", "SAT", "sat", "saturn"),
        "DC"     to listOf("Dreamcast", "Sega Dreamcast", "DC", "dc", "dreamcast"),
        "32X"    to listOf("32X", "Sega 32X", "sega32x", "32x"),
        // NEC / SNK / misc
        "PCE"    to listOf("PC Engine", "TurboGrafx", "PCEngine", "PCE", "pcengine", "tg16"),
        "PCECD"  to listOf("PC Engine CD", "PCECD", "pcenginecd", "tgcd"),
        "NEOGEO" to listOf("NeoGeo", "NEOGEO", "neogeo"),
        "NEOCD"  to listOf("Neo Geo CD", "NeoGeoCD", "NEOCD", "neogeocd"),
        "NGP"    to listOf("Neo Geo Pocket", "NeoGeoPocket", "NGP", "ngp"),
        "NGPC"   to listOf("Neo Geo Pocket Color", "NGPC", "ngpc"),
        "WSWAN"  to listOf("WonderSwan", "WSWAN", "wonderswan"),
        "WSWANC" to listOf("WonderSwan Color", "WonderSwanColor", "WSWANC", "wonderswancolor"),
        // Atari
        "A2600"  to listOf("Atari 2600", "A2600", "atari2600"),
        "A5200"  to listOf("Atari 5200", "A5200", "atari5200"),
        "A7800"  to listOf("Atari 7800", "A7800", "atari7800"),
        "A800"   to listOf("Atari 800", "A800", "atari800"),
        "LYNX"   to listOf("Lynx", "Atari Lynx", "lynx"),
        "JAGUAR" to listOf("Jaguar", "Atari Jaguar", "jaguar"),
        // Misc
        "3DO"    to listOf("3DO", "3do"),
        "POKEMINI" to listOf("Pokemon Mini", "PokemonMini", "pokemini"),
        "ARCADE" to listOf("Arcade", "arcade", "MAME", "mame", "FBNeo", "fbneo"),
    )

    /** ROM file extensions the scanner considers.  Superset of what the
     *  RetroArch scanner on the Steam Deck uses; .bin / .iso / .cue etc.
     *  are included because a disc without those wouldn't be playable. */
    val ROM_EXTENSIONS: Set<String> = setOf(
        // Nintendo handhelds
        "gba", "agb", "gb", "gbc", "sgb", "nds", "dsi", "3ds",
        // Nintendo home
        "nes", "unf", "fds",
        "sfc", "smc", "snes", "fig", "swc",
        "n64", "z64", "v64",
        "gcm", "gcz", "iso", "rvz", "wbfs", "wia",
        // Sony
        "bin", "cue", "img", "mdf",
        "pbp", "cso", "psv",
        "chd",
        // Sega
        "md", "gen", "smd", "32x", "68k",
        "sg", "sms", "gg",
        "gdi", "cdi",
        // Arcade / other
        "zip", "7z",
        "pce",
        "ws", "wsc",
        "ngp", "ngc", "ngpc",
        "lnx",
        "a26", "a52", "a78",
        "col", "int", "vb", "min",
        // Virtual Boy, Atari Jaguar, Amstrad, ZX, etc.
        "vb", "j64", "jag",
        "d64", "t64", "prg", "crt",
        "adf", "ipf", "hdf", "adz",
        "dsk", "cas", "rom",
        "cpc", "tzx", "tap", "sna", "z80",
    )

    private val PRIMARY_PRIORITY: Map<String, Int> = mapOf(
        "cue" to 10, "gdi" to 10,
        "chd" to 9, "rvz" to 9,
        "iso" to 8, "cso" to 8, "cdi" to 8,
        "m3u" to 7,
    )

    private val CART_PRIORITY: Map<String, Int> = mapOf(
        "gba" to 6, "nds" to 6, "3ds" to 6,
        "gb" to 6, "gbc" to 6,
        "nes" to 6, "smc" to 6, "sfc" to 6,
        "md" to 6, "gen" to 6, "smd" to 6,
        "n64" to 6, "z64" to 6, "v64" to 6,
        "pce" to 6, "a26" to 6, "a78" to 6,
        "lnx" to 6, "ngp" to 6, "ngc" to 6,
        "ws" to 6, "wsc" to 6,
        "32x" to 6, "sms" to 6, "gg" to 6,
        "min" to 6, "vb" to 6, "j64" to 6,
    )

    private val CUE_FILE_RE = Regex("""FILE\s+"([^"]+)"""", RegexOption.IGNORE_CASE)
    private val GDI_LINE_RE = Regex("""^\s*\d+\s+\d+\s+\d+\s+\d+\s+(?:"([^"]+)"|(\S+))""")

    /**
     * Pick the on-disk folder a newly downloaded ROM for [system] should
     * land in, mirroring the Steam Deck's ``resolve_rom_target_dir``.
     *
     * Resolution order:
     *  1. If [romDirOverrides] has a non-blank entry under the canonical
     *     system code, use it verbatim.  Absolute paths are returned as-is;
     *     relative ones resolve under [scanRoot].
     *  2. Otherwise, prefer an existing folder matching any alias from
     *     [SYSTEM_ROM_DIRS] under [scanRoot] (so ``roms/segacd`` wins over
     *     creating a new ``roms/Sega CD``).
     *  3. Otherwise, fall back to the first candidate — which is also the
     *     primary display name — under [scanRoot].  Caller is responsible
     *     for ``mkdirs()`` on the returned file.
     *
     * Always canonicalises [system] first via [SystemAliases.normalizeSystemCode]
     * so alias codes (``SCD``, ``GEN``, ``WS``) share a folder with their
     * canonical siblings (``SEGACD``, ``MD``, ``WSWAN``).
     */
    fun resolveRomTargetDir(
        scanRoot: File,
        system: String,
        romDirOverrides: Map<String, String> = emptyMap(),
    ): File {
        val canonical = SystemAliases.normalizeSystemCode(system)
            .ifBlank { system }.uppercase()

        // Canonicalise override keys so a settings file carrying a legacy
        // alias like ``SCD`` still applies to a SEGACD download.
        val override = romDirOverrides.entries
            .firstOrNull { (k, _) ->
                SystemAliases.normalizeSystemCode(k).uppercase() == canonical
            }
            ?.value?.trim()?.takeIf { it.isNotEmpty() }
        if (override != null) {
            val overrideFile = File(override)
            return if (overrideFile.isAbsolute) overrideFile else File(scanRoot, override)
        }

        val candidates = SYSTEM_ROM_DIRS[canonical]
            ?: listOf(canonical, canonical.lowercase())
        val existing = candidates.firstOrNull { File(scanRoot, it).isDirectory }
        return File(scanRoot, existing ?: candidates.first())
    }

    /**
     * Summary of a [prepareRomFolders] run, returned so the UI can show a
     * "created N folders" confirmation with a sensible failure list.
     */
    data class PrepareReport(
        val created: List<Pair<String, File>>,
        val existing: List<Pair<String, File>>,
        val errors: List<Pair<String, String>>,
    ) {
        val createdCount: Int get() = created.size
    }

    /**
     * Create the canonical per-system ROM folders under [scanRoot].
     *
     * For every system in [SYSTEM_ROM_DIRS] (union'd with any system with
     * an explicit override), resolve the target folder via
     * [resolveRomTargetDir] and ``mkdirs()`` it if missing.  Never
     * touches an existing alias folder — we fill in the layout around
     * whatever the user already has so catalog downloads stop inventing
     * stray folder names.
     */
    fun prepareRomFolders(
        scanRoot: File,
        romDirOverrides: Map<String, String> = emptyMap(),
    ): PrepareReport {
        val created = mutableListOf<Pair<String, File>>()
        val existing = mutableListOf<Pair<String, File>>()
        val errors = mutableListOf<Pair<String, String>>()

        val overrideSystems = romDirOverrides.keys
            .map { SystemAliases.normalizeSystemCode(it).ifBlank { it }.uppercase() }
            .filter { it.isNotEmpty() }
        val systems = (SYSTEM_ROM_DIRS.keys + overrideSystems).toSortedSet()

        for (system in systems) {
            val target = resolveRomTargetDir(scanRoot, system, romDirOverrides)
            if (target.isDirectory) {
                existing += system to target
                continue
            }
            try {
                if (target.mkdirs() || target.isDirectory) {
                    created += system to target
                } else {
                    errors += system to "mkdirs returned false"
                }
            } catch (e: SecurityException) {
                errors += system to (e.message ?: "SecurityException")
            }
        }
        return PrepareReport(created, existing, errors)
    }

    /**
     * Return every installed ROM found under the configured roots.
     *
     * ``romScanDir`` is treated as the parent directory that contains
     * per-system subfolders (matching how [com.savesync.android.sync.SyncEngine.downloadRom]
     * writes files).  ``romDirOverrides`` maps system codes to explicit
     * absolute paths that win over the candidate search.
     */
    fun scanInstalled(
        romScanDir: String,
        romDirOverrides: Map<String, String> = emptyMap(),
    ): List<InstalledRom> {
        val scanRoot = romScanDir.trim().takeIf { it.isNotEmpty() }?.let { File(it) }
        val results = mutableListOf<InstalledRom>()
        val seenRoots = HashSet<String>()

        // Canonicalise override keys too — a settings file carrying a
        // legacy alias like ``SCD`` should still win over the default
        // ``roms/segacd`` candidate search.
        val normalizedOverrides = romDirOverrides
            .mapKeys { (k, _) -> SystemAliases.normalizeSystemCode(k).uppercase() }

        for ((system, candidates) in SYSTEM_ROM_DIRS) {
            val override = normalizedOverrides[system]?.trim()?.takeIf { it.isNotEmpty() }
            val folder: File = when {
                override != null -> File(override).takeIf { it.isDirectory }
                scanRoot?.isDirectory == true -> candidates
                    .map { File(scanRoot, it) }
                    .firstOrNull { it.isDirectory }
                else -> null
            } ?: continue

            val canonical = try { folder.canonicalPath } catch (_: Exception) { folder.absolutePath }
            if (!seenRoots.add(canonical)) continue
            results += scanFolder(folder, system)
        }

        return results.sortedWith(
            compareBy(
                { it.system.uppercase() },
                { it.displayName.lowercase() },
                { it.filename.lowercase() },
            )
        )
    }

    /** Delete *rom* and its companions.  If the primary lives in a
     *  dedicated per-game subfolder (not the system root), the whole
     *  subfolder is rmtree'd in one pass — readmes, box art, thumbnail
     *  caches and all.  Otherwise falls back to file-by-file delete. */
    fun deleteInstalled(rom: InstalledRom): DeleteResult {
        val parent = rom.path.parentFile ?: return DeleteResult(0, listOf("Missing parent"), null)
        val group = listOf(rom.path) + rom.companionFiles

        if (canRemoveWholeFolder(parent, rom.systemRoot, group)) {
            val fileCount = countFilesRecursive(parent)
            val ok = parent.deleteRecursively()
            return if (ok) {
                DeleteResult(fileCount, emptyList(), parent)
            } else {
                deleteFiles(group, initialErrors = listOf("${parent.absolutePath}: rmtree failed"))
            }
        }
        return deleteFiles(group)
    }

    /** Public mirror of the whole-folder check — the UI uses this to
     *  phrase the confirm dialog accurately before deletion runs. */
    fun wouldRemoveWholeFolder(rom: InstalledRom): Boolean {
        val parent = rom.path.parentFile ?: return false
        return canRemoveWholeFolder(parent, rom.systemRoot, listOf(rom.path) + rom.companionFiles)
    }

    // ── Internals ──────────────────────────────────────────────────

    private fun scanFolder(folder: File, system: String): List<InstalledRom> {
        val allFiles = walkRomFiles(folder)

        // Phase 1: parse .cue/.gdi sheets for their track companions so
        // multi-track rips (``FF7 (Track 01).bin``) get grouped under
        // the disc primary instead of becoming orphan rows.
        val sheetGroups = mutableListOf<Pair<File, List<File>>>()
        val owned = HashSet<String>()
        for (f in allFiles) {
            val ext = f.extension.lowercase()
            if (ext != "cue" && ext != "gdi") continue
            val companions = parseSheetCompanions(f)
            sheetGroups += f to companions
            owned += f.absolutePath
            companions.forEach { owned += it.absolutePath }
        }

        // Phase 2: group remaining files by (parent, stem).
        val stemGroups = LinkedHashMap<Pair<String, String>, MutableList<File>>()
        for (f in allFiles) {
            if (f.absolutePath in owned) continue
            val key = f.parentFile!!.absolutePath to f.nameWithoutExtension.lowercase()
            stemGroups.getOrPut(key) { mutableListOf() } += f
        }

        val out = mutableListOf<InstalledRom>()
        for ((primary, companions) in sheetGroups) {
            out += buildEntry(primary, companions, system, folder)
        }
        for (files in stemGroups.values) {
            val primary = pickPrimary(files)
            val companions = files.filter { it != primary }
            out += buildEntry(primary, companions, system, folder)
        }
        return out
    }

    private fun walkRomFiles(folder: File): List<File> {
        val out = mutableListOf<File>()
        folder.walkTopDown().forEach { f ->
            if (f.isFile && f.extension.lowercase() in ROM_EXTENSIONS) {
                out += f
            }
        }
        return out
    }

    private fun buildEntry(
        primary: File,
        companions: List<File>,
        system: String,
        systemRoot: File,
    ): InstalledRom {
        val total = runCatching { primary.length() }.getOrDefault(0L) +
            companions.sumOf { runCatching { it.length() }.getOrDefault(0L) }
        return InstalledRom(
            path = primary,
            system = system,
            displayName = prettyName(primary.nameWithoutExtension),
            filename = primary.name,
            size = total,
            systemRoot = systemRoot,
            companionFiles = companions,
        )
    }

    private fun pickPrimary(files: List<File>): File = files.maxWith(
        compareBy<File> { PRIMARY_PRIORITY[it.extension.lowercase()] ?: CART_PRIORITY[it.extension.lowercase()] ?: 0 }
            .thenBy { if (it.extension.equals("bin", ignoreCase = true)) 0 else 1 }
            .thenBy { runCatching { it.length() }.getOrDefault(0L) }
    )

    private fun parseSheetCompanions(sheet: File): List<File> {
        val text = runCatching { sheet.readText() }.getOrNull() ?: return emptyList()
        val names = mutableListOf<String>()
        val parent = sheet.parentFile ?: return emptyList()

        when (sheet.extension.lowercase()) {
            "cue" -> CUE_FILE_RE.findAll(text).forEach { match -> names += match.groupValues[1] }
            "gdi" -> text.lineSequence().forEach { line ->
                val m = GDI_LINE_RE.find(line) ?: return@forEach
                names += (m.groupValues[1].ifEmpty { m.groupValues[2] })
            }
        }

        val seen = HashSet<String>()
        val out = mutableListOf<File>()
        for (name in names) {
            val f = File(parent, name)
            if (!f.isFile) continue
            val key = runCatching { f.canonicalPath }.getOrDefault(f.absolutePath)
            if (seen.add(key) && f != sheet) out += f
        }
        return out
    }

    private fun canRemoveWholeFolder(
        parent: File,
        systemRoot: File?,
        groupPaths: List<File>,
    ): Boolean {
        // Never remove the system folder (psx/, gba/, saturn/…).
        if (systemRoot != null) {
            val parentCanon = runCatching { parent.canonicalPath }.getOrDefault(parent.absolutePath)
            val rootCanon = runCatching { systemRoot.canonicalPath }.getOrDefault(systemRoot.absolutePath)
            if (parentCanon == rootCanon) return false
        }

        val groupCanon = groupPaths
            .filter { it.exists() }
            .map { runCatching { it.canonicalPath }.getOrDefault(it.absolutePath) }
            .toHashSet()

        var allowed = true
        parent.walkTopDown().forEach { f ->
            if (!allowed) return@forEach
            if (!f.isFile) return@forEach
            // Only ROM files gate the rmtree — readmes, box art,
            // metadata caches get swept along with the folder.  That's
            // the point of the whole-folder delete.
            if (f.extension.lowercase() !in ROM_EXTENSIONS) return@forEach
            val canon = runCatching { f.canonicalPath }.getOrDefault(f.absolutePath)
            if (canon !in groupCanon) allowed = false
        }
        return allowed
    }

    private fun deleteFiles(
        files: List<File>,
        initialErrors: List<String> = emptyList(),
    ): DeleteResult {
        var deleted = 0
        val errors = initialErrors.toMutableList()
        for (f in files) {
            if (!f.exists()) {
                deleted += 1
                continue
            }
            if (f.delete()) {
                deleted += 1
            } else {
                errors += "${f.name}: delete failed"
            }
        }
        return DeleteResult(deleted, errors, null)
    }

    private fun countFilesRecursive(folder: File): Int =
        folder.walkTopDown().count { it.isFile }

    private fun prettyName(stem: String): String {
        val cleaned = stem.replace('_', ' ').trim()
        return Regex("""\s+""").replace(cleaned, " ").ifEmpty { stem }
    }
}
