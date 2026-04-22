package com.savesync.android.catalog

import com.savesync.android.api.RomEntry

/**
 * Client-side smart search over the server's ROM catalog, mirroring the
 * Steam Deck ``scanner/catalog_search.py`` logic.
 *
 * The server already accepts a ``search`` query parameter, but filtering
 * locally against a cached catalog keeps typing responsive (no 200ms
 * round-trip per keystroke) and gives us roman-numeral + region-tag
 * tolerance without changing the server API.
 *
 * Matching rules:
 *   1. Strip region / language tags in parens or brackets.
 *   2. Split the query and the ROM's name/filename/title_id/system into
 *      lowercase alphanumeric tokens.
 *   3. Every query token must match — either as a substring of the
 *      concatenated normalized haystack or as an entry in the ROM's
 *      token set (which also holds roman↔arabic variants of the
 *      display slug).
 *   4. The system filter is applied before the text filter.
 *
 * "breath of fire 4" matches "Breath of Fire IV (USA)"; "final fantasy
 * 7" matches "Final Fantasy VII (Disc 1)"; "chrono trigger" matches
 * "Chrono Trigger (USA) (En,Fr)".
 */
object RomCatalogFilter {

    private val TAG_RE = Regex("""\s*[\(\[][^\)\]]*[\)\]]""")
    private val NON_ALNUM_RE = Regex("""[^a-z0-9]+""")
    private val ROMAN_TO_ARABIC = mapOf(
        "i" to "1", "ii" to "2", "iii" to "3", "iv" to "4", "v" to "5",
        "vi" to "6", "vii" to "7", "viii" to "8", "ix" to "9", "x" to "10",
        "xi" to "11", "xii" to "12", "xiii" to "13", "xiv" to "14", "xv" to "15"
    )
    private val ARABIC_TO_ROMAN: Map<String, String> =
        ROMAN_TO_ARABIC.entries.associate { (k, v) -> v to k }

    private data class Indexed(
        val haystack: String,
        val tokens: Set<String>,
    )

    /** Strip a trailing extension, bracketed tags, and collapse runs of
     *  non-alphanumerics to underscores. ``"Breath of Fire IV (USA).chd"``
     *  → ``"breath_of_fire_iv"``. */
    fun coreNameSlug(label: String?): String {
        if (label.isNullOrEmpty()) return ""
        var name = label
        val dot = name.lastIndexOf('.')
        if (dot in 1..(name.length - 2)) {
            val ext = name.substring(dot + 1)
            if (ext.length in 1..5 && ext.all { it.isLetterOrDigit() }) {
                name = name.substring(0, dot)
            }
        }
        val stripped = TAG_RE.replace(name, "").trim().lowercase()
        return NON_ALNUM_RE.replace(stripped, "_").trim('_')
    }

    /** Like [coreNameSlug] but keeps the region/language tag content so
     *  "(USA)" still contributes tokens to the haystack (users do type
     *  "usa" to narrow results). */
    fun normalizeRomName(label: String?): String {
        if (label.isNullOrEmpty()) return ""
        var name = label
        val dot = name.lastIndexOf('.')
        if (dot in 1..(name.length - 2)) {
            val ext = name.substring(dot + 1)
            if (ext.length in 1..5 && ext.all { it.isLetterOrDigit() }) {
                name = name.substring(0, dot)
            }
        }
        return NON_ALNUM_RE.replace(name.lowercase(), "_").trim('_')
    }

    private fun slugRomanVariants(slug: String): List<String> {
        if (slug.isEmpty()) return emptyList()
        val parts = slug.split('_')
        val out = mutableListOf<String>()
        parts.forEachIndexed { idx, part ->
            val replacement = ROMAN_TO_ARABIC[part] ?: ARABIC_TO_ROMAN[part]
            if (replacement != null) {
                val variant = parts.toMutableList().apply { this[idx] = replacement }
                out += variant.joinToString("_")
            }
        }
        return out
    }

    private fun indexRom(rom: RomEntry): Indexed {
        val display = coreNameSlug(rom.name)
        val full = normalizeRomName(rom.filename).ifEmpty { display }

        val tokens = mutableSetOf<String>()
        for (label in listOf(rom.name, rom.filename, rom.title_id)) {
            NON_ALNUM_RE.split(label.lowercase())
                .filter { it.isNotEmpty() }
                .forEach { tokens.add(it) }
        }
        if (display.isNotEmpty()) tokens.addAll(display.split('_').filter { it.isNotEmpty() })
        if (full.isNotEmpty()) tokens.addAll(full.split('_').filter { it.isNotEmpty() })
        for (variant in slugRomanVariants(display)) {
            tokens.addAll(variant.split('_').filter { it.isNotEmpty() })
        }

        val haystack = listOf(
            rom.system.lowercase(),
            rom.title_id.lowercase(),
            full,
            display,
        ).filter { it.isNotEmpty() }.joinToString("_")

        return Indexed(haystack, tokens)
    }

    private fun queryTokens(query: String): List<String> =
        NON_ALNUM_RE.split(query.lowercase()).filter { it.isNotEmpty() }

    private fun expandRomanVariants(token: String): Set<String> {
        val out = mutableSetOf(token)
        ROMAN_TO_ARABIC[token]?.let { out += it }
        ARABIC_TO_ROMAN[token]?.let { out += it }
        return out
    }

    /** True when *rom* matches *query* (every token) and *system* (if set). */
    fun matches(rom: RomEntry, query: String, system: String? = null): Boolean {
        if (!system.isNullOrBlank() && !rom.system.equals(system, ignoreCase = true)) {
            return false
        }
        val trimmed = query.trim()
        if (trimmed.isEmpty()) return true

        val (haystack, tokens) = indexRom(rom)
        for (raw in queryTokens(trimmed)) {
            val variants = expandRomanVariants(raw)
            val hit = variants.any { v -> v in haystack || v in tokens }
            if (!hit) return false
        }
        return true
    }

    /** Filter + sort by (system, name). */
    fun filter(
        catalog: List<RomEntry>,
        query: String = "",
        system: String? = null,
    ): List<RomEntry> {
        if (catalog.isEmpty()) return emptyList()
        val normalizedSystem = system?.trim()?.takeIf { it.isNotEmpty() }
        return catalog
            .filter { matches(it, query, normalizedSystem) }
            .sortedWith(
                compareBy(
                    { it.system.uppercase() },
                    { (it.name.ifEmpty { it.filename }).lowercase() },
                )
            )
    }

    /** The set of system codes the catalog actually contains, sorted. */
    fun uniqueSystems(catalog: List<RomEntry>): List<String> =
        catalog.mapNotNull { it.system.trim().takeIf { s -> s.isNotEmpty() } }
            .map { it.uppercase() }
            .distinct()
            .sorted()
}
