package com.savesync.android.systems

/**
 * Single source of truth for translating server/legacy system codes into
 * Android's canonical set.
 *
 * Mirrors the small subset of ``shared/systems.py::normalize_system_code``
 * that the Android client actually encounters.  Keep this map in sync with
 * the Python helper (repo root ``shared/systems.py``) — in particular, any
 * new alias added there needs to land here so catalog downloads don't
 * scatter ROMs into alias-named folders (e.g. ``roms/SCD/`` next to
 * ``roms/segacd/`` — the bug that motivated this file).
 */
object SystemAliases {

    /**
     * Legacy / server-side system codes → canonical Android codes.
     *
     * The raw catalog can emit codes like ``SCD`` (Sega CD), ``GEN``
     * (Genesis), or ``WS`` (WonderSwan); Android's folder map in
     * [com.savesync.android.installed.InstalledRomsScanner.SYSTEM_ROM_DIRS]
     * is keyed on the canonical forms (``SEGACD``, ``MD``, ``WSWAN``).
     * Without this translation layer, downloads for alias codes fall
     * through to the else branch and create fragmented folders next to
     * the ones the scanner already walks.
     */
    val SERVER_TO_CANONICAL: Map<String, String> = mapOf(
        // Sega — GENESIS, MEGADRIVE, MEGA-DRIVE and GEN all collapse to MD
        "GENESIS"    to "MD",
        "MEGADRIVE"  to "MD",
        "MEGA-DRIVE" to "MD",
        "GEN"        to "MD",
        // SCD is a legacy alias for SEGACD (older uploads used SCD)
        "SCD"        to "SEGACD",
        "MEGACD"     to "SEGACD",
        "MEGA-CD"    to "SEGACD",
        // WS is a legacy alias for WSWAN
        "WS"         to "WSWAN",
        "WSC"        to "WSWANC",
        // Atari legacy names
        "ATARI2600"  to "A2600",
        "ATARI5200"  to "A5200",
        "ATARI7800"  to "A7800",
        "ATARI800"   to "A800",
        "ATARILYNX"  to "LYNX",
        "ATARIJAGUAR" to "JAGUAR",
        // PPSSPP was the old Android system name for PSP
        "PPSSPP"     to "PSP",
        // NEC / SNK spelling variants
        "TG16"       to "PCE",
        "TURBOGRAFX" to "PCE",
        "TGCD"       to "PCECD",
        "NEOGEOPOCKET" to "NGP",
        "NEOGEOPOCKETCOLOR" to "NGPC",
        "NEOGEOCD"   to "NEOCD",
        // Sony spelling variants
        "PSX"        to "PS1",
        "PLAYSTATION" to "PS1",
        "PLAYSTATION1" to "PS1",
        "PLAYSTATION2" to "PS2",
        "PLAYSTATION3" to "PS3",
        "PLAYSTATIONPORTABLE" to "PSP",
        "PSVITA"     to "VITA",
        "PS VITA"    to "VITA",
        // Nintendo spelling variants
        "NINTENDO64" to "N64",
        "NINTENDODS" to "NDS",
        "NINTENDOGAMECUBE" to "GC",
        "GAMECUBE"   to "GC",
        "NINTENDO3DS" to "3DS",
        "N3DS"       to "3DS",
        "VIRTUALBOY" to "VB",
        "GAMEBOY"    to "GB",
        "GAMEBOYCOLOR" to "GBC",
        "GAMEBOYADVANCE" to "GBA",
        "SUPERNINTENDO" to "SNES",
        "FAMICOM"    to "NES",
        // Sega variants
        "MASTERSYSTEM" to "SMS",
        "GAMEGEAR"   to "GG",
        "SEGA32X"    to "32X",
        "SATURN"     to "SAT",
        "SEGASATURN" to "SAT",
        "DREAMCAST"  to "DC",
        "SEGADREAMCAST" to "DC",
    )

    /**
     * Reverse map: each canonical code → every server alias that points
     * at it.  Used by title-matching code that needs to try multiple
     * prefix spellings when querying a server save keyed under a legacy
     * system code.
     */
    val CANONICAL_TO_SERVER: Map<String, List<String>> =
        SERVER_TO_CANONICAL.entries.groupBy({ it.value }, { it.key })

    /**
     * Collapse a free-form system identifier to its canonical code.
     *
     * Handles canonical codes unchanged, alias codes via [SERVER_TO_CANONICAL],
     * and folder-style names with separators (``"Mega Drive"`` / ``"mega-drive"``
     * / ``"Mega_Drive"`` all resolve to ``MD``).  Empty or null input returns
     * the original string so callers can freely chain without null handling.
     *
     * The result is always upper-case when a meaningful normalization
     * happened, matching ``shared/systems.py::normalize_system_code``.
     */
    fun normalizeSystemCode(system: String?): String {
        if (system.isNullOrBlank()) return system ?: ""
        val text = system.trim()
        // Strip separators so "Mega Drive" / "mega-drive" / "Mega_Drive"
        // collapse to the same key — matches shared/systems.py behaviour.
        val compact = text.filter { it.isLetterOrDigit() }.uppercase()
        if (compact.isEmpty()) return text
        SERVER_TO_CANONICAL[compact]?.let { return it }
        // Already canonical (or unknown but already a plain code).  Return
        // the compacted upper-case form so downstream map lookups (which
        // key on upper-case) hit cleanly regardless of the caller's input
        // style.
        return compact
    }

    /**
     * Non-destructive variant that preserves the caller's casing when no
     * alias mapping applies.  Use this from code paths that compare the
     * return value against the original input verbatim (e.g. "is this
     * titleId prefix already in canonical form?").
     */
    fun canonicalOrSelf(system: String): String =
        SERVER_TO_CANONICAL[system.uppercase()] ?: system
}
