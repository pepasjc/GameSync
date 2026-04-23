package com.savesync.android.emulators

import com.savesync.android.SaveSyncApp

/**
 * Parses the bundled "Sega - Saturn (libretro).dat" clrmamepro DAT file (from app assets)
 * to build a case-insensitive game-name → product-serial lookup table.
 *
 * Used as a fallback when the IP.BIN product code cannot be read directly from a disc image
 * (e.g. CHD files, which are compressed and cannot be read with a simple byte offset).
 *
 * Lookup strategy ([lookupSerial]):
 *  1. Strip `[bracket]` tags (translation / hack suffixes added by the patcher).
 *  2. Try exact match against the DAT game name (case-insensitive).
 *  3. Progressively strip trailing `(...)` parenthetical groups and retry, so that
 *     "Game (Japan) (Rev A) (extra tag)" still matches "Game (Japan)" in the DAT.
 *
 * Example: "Grandia (Japan) (Disc 1) (4M) [T-En by TrekkiesUnite118 v0.9.3 RC]"
 *  → strip `[...]`  → "Grandia (Japan) (Disc 1) (4M)"
 *  → exact match    → serial "T-4507G"
 *
 * Thread-safe: [nameToSerial] is written once under [load] and thereafter only read.
 */
internal object SaturnSerialDatabase {

    private const val ASSET_NAME = "Sega - Saturn (libretro).dat"

    /** Lowercase game name → product serial (e.g. "T-4507G"). */
    @Volatile
    private var nameToSerial: Map<String, String>? = null

    /** Strips `[text]` bracket tags (fan-translation / hack markers). */
    private val bracketTagRe = Regex("""\s*\[[^\]]*\]""")

    /** Strips the last `(...)` group from the end of a string. */
    private val trailingParenRe = Regex("""\s*\([^)]*\)\s*$""")

    /**
     * Disc-index suffix pattern: a letter followed by a hyphen and a single digit at
     * the end of the serial (e.g. "T-21301G-0", "GS-9076-2").  These are per-disc
     * variants of the same product code and should be skipped during parsing so the
     * canonical serial (without suffix) is stored.
     */
    private val discIndexRe = Regex("""[A-Za-z]-\d$""")

    /**
     * Returns the product serial for [romName] (bare filename without extension),
     * or null if the DAT has no match.
     */
    fun lookupSerial(romName: String): String? {
        val db = nameToSerial ?: load()

        // Step 1: strip [bracket] tags
        var name = romName.replace(bracketTagRe, "").trim()

        // Step 2: exact match (case-insensitive)
        db[name.lowercase()]?.let { return it }

        // Step 3: progressively strip trailing (...) groups and retry
        while (true) {
            val stripped = trailingParenRe.replace(name, "").trim()
            if (stripped == name || stripped.isBlank()) break
            name = stripped
            db[name.lowercase()]?.let { return it }
        }

        return null
    }

    // ----------------------------------------------------------------------------------
    // Initialisation
    // ----------------------------------------------------------------------------------

    private fun load(): Map<String, String> {
        // Double-checked: another thread may have initialised while we waited.
        nameToSerial?.let { return it }
        val parsed = try {
            SaveSyncApp.instance.assets.open(ASSET_NAME).use { parseDat(it) }
        } catch (_: Exception) {
            emptyMap()
        }
        nameToSerial = parsed
        return parsed
    }

    // ----------------------------------------------------------------------------------
    // DAT parser (clrmamepro format)
    // ----------------------------------------------------------------------------------

    /**
     * Parses the clrmamepro DAT stream and returns lowercase game name → serial.
     *
     * Format summary:
     * ```
     * game (
     *     name "Grandia (Japan) (Disc 1) (4M)"
     *     region "Japan"
     *     serial "T-4507G"
     *     rom ( name "..." ... serial "T-4507G" )
     * )
     * ```
     * The game-level `serial` field (at the first indent level) is used.
     * Per-disc-index variants (e.g. "T-4507G-0") are skipped.
     * When multiple entries share the same name, the first (usually the primary region)
     * is kept via [putIfAbsent].
     */
    private fun parseDat(stream: java.io.InputStream): Map<String, String> {
        val result = mutableMapOf<String, String>()
        var inGame = false
        var curName = ""
        var curSerial = ""

        stream.bufferedReader(Charsets.UTF_8).useLines { lines ->
            for (line in lines) {
                val s = line.trim()
                when {
                    s == "game (" -> {
                        inGame = true
                        curName = ""
                        curSerial = ""
                    }
                    s == ")" && inGame -> {
                        if (curName.isNotEmpty() && curSerial.isNotEmpty()) {
                            result.putIfAbsent(curName.lowercase(), curSerial)
                        }
                        inGame = false
                    }
                    inGame -> {
                        // Only capture the first occurrence of each field per game block.
                        if (curName.isEmpty()) {
                            val m = Regex("""\s+name\s+"(.+)"""").find(line)
                            if (m != null) { curName = m.groupValues[1]; continue }
                        }
                        if (curSerial.isEmpty()) {
                            // Match game-level serial lines (single tab indent, not inside rom (...))
                            val m = Regex("""^\s+serial\s+"([^"]+)"""").find(line)
                            if (m != null) {
                                val serial = m.groupValues[1]
                                // Skip disc-index suffix variants (e.g. T-4507G-0)
                                if (!discIndexRe.containsMatchIn(serial)) {
                                    curSerial = serial
                                }
                            }
                        }
                    }
                }
            }
        }
        return result
    }
}
