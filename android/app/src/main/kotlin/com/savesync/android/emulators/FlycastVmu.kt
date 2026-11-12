package com.savesync.android.emulators

/**
 * File names Flycast gives a per-game VMU (port A1), the only Dreamcast save
 * layout that maps onto one `DC_<serial>` slot.  Both are a bare 128 KB VMU
 * image — the same bytes a MemCard PRO DC or openMenu card holds — so they
 * upload through `/raw` and interchange with those devices.
 *
 * - RetroArch's Flycast core ("Per-Game VMUs" on): `<saves>/<id>.A1.bin`
 * - Standalone Flycast ("Per Game VMU A1" on): `<home>/data/<id>_vmu_save_A1.bin`
 *
 * `<id>` is the disc's IP.BIN product number (trailing spaces trimmed,
 * `" /\:*?|<>` replaced by `_`, e.g. `T1401D__50`), so the file name alone
 * gives the save's serial.  When that file doesn't exist yet Flycast falls
 * back to a legacy name with the ROM file name as `<id>` — it loads that one
 * and saves to the product-number file from then on — so both forms are read.
 *
 * With per-game VMUs off, both write one shared `vmu_save_A1.bin` that holds
 * every game; that file matches neither pattern and is left alone.
 *
 * Naming from Flycast's `hostfs::getVmuPath()` (core/oslib/oslib.cpp and
 * shell/libretro/oslib.cpp) and `settings.content.gameId` (core/emulator.cpp),
 * https://github.com/flyinghead/flycast (GPL-2.0+); nothing is copied, only
 * the file-name convention.
 */
object FlycastVmu {
    const val LIBRETRO_SUFFIX = ".A1.bin"
    const val STANDALONE_SUFFIX = "_vmu_save_A1.bin"

    /** A product number as Flycast writes it into a file name: ≤ 10 chars, upper-case. */
    private val GAME_ID_RE = Regex("""^(?=.*\d)[A-Z0-9][A-Z0-9_\-.]{0,9}$""")

    /** `<id>` of a RetroArch-core per-game VMU, or null for any other file. */
    fun libretroGameStem(fileName: String): String? = stemBefore(fileName, LIBRETRO_SUFFIX)

    /** `<id>` of a standalone-Flycast per-game VMU, or null for any other file. */
    fun standaloneGameStem(fileName: String): String? = stemBefore(fileName, STANDALONE_SUFFIX)

    fun libretroFileName(romStem: String): String = romStem + LIBRETRO_SUFFIX

    fun standaloneFileName(romStem: String): String = romStem + STANDALONE_SUFFIX

    /**
     * `DC_<serial>` when [stem] is a product number (the current naming),
     * null when it is a ROM name (the legacy naming) — resolve that one
     * through the ROM / DAT like any other ROM-named save.
     */
    fun titleIdFromGameId(stem: String): String? =
        if (GAME_ID_RE.matches(stem)) DreamcastSerial.titleId(stem) else null

    /**
     * Collapse VMUs that resolve to the same title — a legacy ROM-named card
     * and the product-number card — to the one Flycast actually loads: the
     * product-number card whenever it exists, else the legacy one.
     */
    fun <T> loadedPerTitle(items: List<T>, titleId: (T) -> String, stem: (T) -> String): List<T> =
        items.groupBy(titleId).values.map { group ->
            group.firstOrNull { titleIdFromGameId(stem(it)) != null } ?: group.first()
        }

    private fun stemBefore(fileName: String, suffix: String): String? {
        if (!fileName.endsWith(suffix, ignoreCase = true)) return null
        return fileName.dropLast(suffix.length).takeIf { it.isNotBlank() }
    }
}
