package com.savesync.android.catalog

import com.savesync.android.api.RomEntry

/**
 * Offer the RetroAchievements-supported version of an installed game.
 *
 * Kotlin port of `shared/ra_variants.py` (MiSTer and Steam Deck) — change
 * one, change the other.  RA builds a set against particular dumps; for
 * systems matched by *title* every region badges in the catalogue, but only
 * the dump RA registered loads the set (RA's Dreamcast Soulcalibur is the
 * Europe disc).  The server marks the dumps RA really recognises
 * (`ra_match == "hash"`, or `ra_dump`), and [RaVariantIndex.variants] answers
 * "is there a verified catalogue version of this installed game, when the
 * installed one is not?".
 *
 * Catalogue rows are taken one per game.  Multi-disc games and bundles are
 * left out on both sides: swapping disc 1 for another region's while disc 2
 * stays would leave a mixed set.
 */
object RaVariants {

    /** RA will recognise this very dump, not merely a game of its name. */
    fun isVerified(row: RomEntry): Boolean =
        (row.raAchievements ?: 0) > 0 &&
            ((row.raMatch ?: "hash").equals("hash", ignoreCase = true) || row.raDump == true)

    private val NOT_A_RELEASE_RE = Regex(
        """\((?:[^)]*\b(?:bonus|demo|beta|proto|prototype|sample|kiosk|trial|preview|msu-?1?|msu-md|md\+)\b[^)]*)\)""",
        RegexOption.IGNORE_CASE,
    )

    /** A retail release: no tags at all, and not a bonus disc or demo. */
    fun isPlainRelease(name: String): Boolean = '[' !in name && !NOT_A_RELEASE_RE.containsMatchIn(name)

    /** A fan translation's tag: `[T-En by Aeon Genesis v1.00]`, `[T-En v1.0 tettsui77]`. */
    private val TRANSLATION_RE = Regex("""\[T-([A-Za-z]{2,3})\b""")

    /** Regions whose releases are in English, and the language list form. */
    private val ENGLISH_RE = Regex(
        """\((?:[^)]*\b(?:USA|World|Europe|Australia|UK|Canada)\b[^)]*)\)""" +
            """|\((?:[A-Za-z]{2},)*En(?:,[A-Za-z]{2})*\)""",
    )

    /** `"en"` for a `[T-En ...]` translation, `""` for anything else. */
    fun translationLanguage(name: String): String =
        TRANSLATION_RE.find(name)?.groupValues?.get(1)?.lowercase() ?: ""

    /**
     * A release that may be offered or replaced: a retail release, or a fan
     * translation (whatever extra patches ride along).  A plain hack is
     * neither — trading it for the badge would lose the hack.
     */
    fun isSwappable(name: String): Boolean {
        if (NOT_A_RELEASE_RE.containsMatchIn(name)) return false
        return '[' !in name || translationLanguage(name).isNotEmpty()
    }

    /**
     * Whether [candidate] keeps the language the player chose: a translation
     * is only replaced by one into the same language or, for English, by a
     * release that is English already — a `[T-En]` Japanese game is never
     * offered its Japanese original.  Retail may be offered either —
     * [RaVariantIndex.variants] keeps the translations only when RA supports
     * no retail version (RA's Ganbare Goemon 3 set is the DDSTranslation patch).
     */
    fun sameLanguage(installed: String, candidate: String): Boolean {
        val language = translationLanguage(installed)
        if (language.isEmpty()) return true
        if (translationLanguage(candidate) == language) return true
        return language == "en" && isPlainRelease(candidate) && ENGLISH_RE.containsMatchIn(candidate)
    }

    private val MULTI_DISC_RE = Regex("""\((?:disc|cd|side)\s*\d""", RegexOption.IGNORE_CASE)

    fun isMultiDisc(name: String): Boolean = MULTI_DISC_RE.containsMatchIn(name)

    // ── shared/ra_titles.normalize ───────────────────────────────────────
    private val RA_TAG_RE = Regex("""^\s*~[^~]*~\s*""")
    private val BRACKETED_RE = Regex("""\([^)]*\)|\[[^\]]*\]""")
    private val WORD_RE = Regex("[a-z0-9]+")
    private val ARTICLES = setOf("the", "a", "an")
    private val ROMAN = mapOf(
        "i" to "1", "ii" to "2", "iii" to "3", "iv" to "4", "v" to "5",
        "vi" to "6", "vii" to "7", "viii" to "8", "ix" to "9", "x" to "10",
    )

    private fun stripExtension(name: String): String {
        val base = name.substringAfterLast('/').substringAfterLast('\\')
        val dot = base.lastIndexOf('.')
        if (dot > 0) {
            val ext = base.substring(dot + 1)
            if (ext.length <= 5 && ext.isNotEmpty() && ext.all { it.isLetterOrDigit() }) {
                return base.substring(0, dot)
            }
        }
        return base
    }

    /** No tags, articles or punctuation: "Soul Calibur (USA)" and "Soulcalibur (Europe)" agree. */
    fun titleKey(name: String): String {
        var text = stripExtension(name)
        text = RA_TAG_RE.replace(text, "")
        text = BRACKETED_RE.replace(text, " ")
        return WORD_RE.findAll(text.lowercase())
            .map { it.value }
            .filter { it !in ARTICLES }
            .joinToString("") { ROMAN[it] ?: it }
    }

    /** "Same game name" key, tags kept: what tells two catalogue entries apart. */
    fun nameKey(name: String): String =
        stripExtension(name).replace('_', ' ').lowercase().replace(Regex("""\s+"""), " ").trim()

    private val REGION_ORDER = listOf("world", "usa", "europe")

    /** Which version to offer first when RA supports several. */
    fun regionRank(name: String): Int {
        val lowered = name.lowercase()
        REGION_ORDER.forEachIndexed { rank, region ->
            if ("($region" in lowered || ", $region" in lowered) return rank
        }
        return REGION_ORDER.size
    }
}

/** The catalogue, indexed once for [variants]. */
class RaVariantIndex(catalog: List<RomEntry>) {

    private val byName = HashMap<Pair<String, String>, RomEntry>()
    private val verified = HashMap<Pair<String, String>, MutableList<RomEntry>>()
    private val verifiedById = HashMap<Pair<String, Int>, MutableList<RomEntry>>()

    /** `system to md5` of every dump RA recognises exactly. */
    private val hashes = HashSet<Pair<String, String>>()

    init {
        for (row in catalog) {
            val name = row.name.ifEmpty { row.filename }
            if (name.isEmpty() || row.isBundle || RaVariants.isMultiDisc(name)) continue
            val system = row.system.uppercase()
            byName.putIfAbsent(system to RaVariants.nameKey(name), row)
            if (!RaVariants.isVerified(row)) continue
            row.raHash?.takeIf { it.isNotEmpty() }?.let { hashes += system to it.lowercase() }
            if (!RaVariants.isSwappable(name)) continue
            val key = RaVariants.titleKey(name)
            if (key.isNotEmpty()) verified.getOrPut(system to key) { mutableListOf() }.add(row)
            row.raGameId?.takeIf { it > 0 }?.let { verifiedById.getOrPut(system to it) { mutableListOf() }.add(row) }
        }
    }

    /**
     * Catalogue versions of an installed game RA verifiably supports, when
     * the installed one is not among them.  Best first.  Versions are found
     * by name and by the RA game the server linked the installed game's
     * entry to ("Front Mission Series - Gun Hazard" finds "Front Mission:
     * Gun Hazard").  Whether the installed copy is supported is decided by
     * [contentHash] (RA's hash of the file, asked for only when there is
     * something to offer) when given, else by its catalogue entry, which
     * must exist.  Nothing is offered once a verified version is installed
     * ([installedNames] holds `system to nameKey` of everything installed).
     */
    fun variants(
        system: String,
        name: String,
        installedNames: Set<Pair<String, String>>,
        contentHash: (() -> String?)? = null,
    ): List<RomEntry> {
        val sys = system.uppercase()
        if (verified.isEmpty() || !RaVariants.isSwappable(name) || RaVariants.isMultiDisc(name)) {
            return emptyList()
        }
        val ownKey = RaVariants.nameKey(name)
        val own = byName[sys to ownKey]
        val ownId = own?.raGameId?.takeIf { it > 0 }
        val pool = LinkedHashSet<RomEntry>()
        pool += verified[sys to RaVariants.titleKey(name)].orEmpty()
        if (ownId != null) pool += verifiedById[sys to ownId].orEmpty()
        val found = mutableListOf<RomEntry>()
        var sameName = false
        for (candidate in pool) {
            val candidateName = candidate.name.ifEmpty { candidate.filename }
            if (!RaVariants.sameLanguage(name, candidateName)) continue
            // A verified entry under the installed one's own name: it can't
            // be installed beside it, and by name the two can't be told apart.
            if (RaVariants.nameKey(candidateName) == ownKey) {
                sameName = true
                continue
            }
            // RA filed them as different games.
            if (ownId != null && candidate.raGameId?.takeIf { it > 0 } != ownId) continue
            found += candidate
        }
        if (found.isEmpty()) return emptyList()

        val md5 = contentHash?.invoke()
        if (!md5.isNullOrEmpty()) {
            if ((sys to md5.lowercase()) in hashes) return emptyList()
        } else if (own == null || RaVariants.isVerified(own) || sameName) {
            return emptyList()
        }

        if (found.any { (sys to RaVariants.nameKey(it.name.ifEmpty { it.filename })) in installedNames }) {
            return emptyList()
        }
        val offered = if (RaVariants.translationLanguage(name).isEmpty()) {
            found.filter { RaVariants.isPlainRelease(it.name.ifEmpty { it.filename }) }.ifEmpty { found }
        } else {
            found
        }
        return offered.sortedWith(
            compareBy({ RaVariants.regionRank(it.name) }, { it.name.lowercase() })
        )
    }
}
