package com.savesync.android.api

import com.google.gson.annotations.SerializedName

data class SyncTitle(
    val title_id: String,
    val save_hash: String,
    val timestamp: Long,
    val size: Long,               // required by server's TitleSyncInfo
    val last_synced_hash: String?,
    val console_id: String? = null
)

data class SyncRequest(
    val titles: List<SyncTitle>,
    val console_id: String
)

data class SyncResponse(
    val upload: List<String>,
    val download: List<String>,
    val conflict: List<String>,
    val up_to_date: List<String>,
    val server_only: List<String>,
    val rom_available: List<String> = emptyList()
)

data class SaveMeta(
    val title_id: String,
    val save_hash: String?,
    val save_size: Long?,
    val client_timestamp: Long?,
    val platform: String?,
    val server_timestamp: String? = null
)

data class UploadResponse(
    val status: String,
    val timestamp: String,   // ISO datetime string returned by server
    val sha256: String
)

data class StatusResponse(
    val status: String,
    val version: String
)

data class TitleInfo(
    val title_id: String,
    val name: String?,
    val game_name: String?,
    val platform: String?,
    val system: String?,
    @SerializedName("console_type")
    val consoleType: String? = null,
    val save_hash: String?,
    val save_size: Long?
)

data class TitlesResponse(val titles: List<TitleInfo>)

// ── ROM normalization ──────────────────────────────────────────────────────

data class NormalizeRomEntry(
    val system: String,
    val filename: String,
    val crc32: String? = null
)

data class NormalizeRequest(val roms: List<NormalizeRomEntry>)

data class NormalizeResult(
    val system: String,
    val original_filename: String,
    val canonical_name: String,
    val title_id: String,
    /** "dat_crc32" | "dat_filename" | "filename" */
    val source: String,
    val alternatives: List<String> = emptyList()
)

data class NormalizeResponse(val results: List<NormalizeResult>)

// ── PSP/game name lookup ──────────────────────────────────────────────────
data class GameNameRequest(val codes: List<String>)

/**
 * Fills in server-side names for titles stored under their raw id.
 *
 * [codes] are product codes the server resolves through its own DATs;
 * [names] are display names for titles no DAT covers — a Wii U save is keyed
 * by a 16-hex title id whose low word is not the product code, so the only
 * source is the title's meta.xml on whichever device has the game.
 */
data class NameHintRequest(
    val codes: Map<String, String> = emptyMap(),
    val names: Map<String, String> = emptyMap()
)
data class GameNameResponse(
    val names: Map<String, String>,
    val types: Map<String, String>,
    val retail_serials: Map<String, String>? = null
)

data class SaturnArchiveLookupRequest(
    val title_id: String,
    val archive_names: List<String>
)

// ── Canonical name lookup ─────────────────────────────────────────────────
// POST /api/v1/titles/canonical-names — resolves emulator-style title_ids
// (e.g. "SNES_super_mario_world_usa") to their canonical No-Intro/DAT name
// ("Super Mario World (USA)"). Used to construct save filenames that match
// what the emulator writes to disk, rather than a slug-derived approximation.
data class CanonicalNamesRequest(val title_ids: List<String>)
data class CanonicalNamesResponse(val names: Map<String, String>)

data class SaturnArchiveCandidate(
    val title_id: String,
    val game_name: String
)

data class SaturnArchiveLookupResult(
    val archive_family: String,
    val archive_names: List<String>,
    val status: String,
    val matches_current_title: Boolean,
    val candidates: List<SaturnArchiveCandidate>
)

data class SaturnArchiveLookupResponse(
    val title_id: String,
    val results: List<SaturnArchiveLookupResult>
)

// ── ROM catalog ────────────────────────────────────────────────────────────

data class RomEntry(
    val rom_id: String? = null,
    val title_id: String,
    val system: String,
    val name: String,
    val filename: String,
    val path: String,
    val size: Long,
    val crc32: String? = null,
    val source: String? = null,
    @SerializedName("extract_format")
    val extractFormat: String? = null,
    /**
     * Nullable on purpose.  Gson instantiates via Unsafe and does not run the
     * Kotlin constructor, so a declared `= emptyList()` default is NOT applied
     * when the key is absent from the JSON — the field is left null and any
     * non-null-typed access NPEs at the first call.  The server omits this key
     * for systems with nothing to advertise, so read it through [extractFormatList].
     */
    @SerializedName("extract_formats")
    val extractFormats: List<String>? = null,
    /** True for folder-shaped catalog entries (PS3 packages, Wii U WUP sets). */
    @SerializedName("is_bundle")
    val isBundle: Boolean = false,
    /**
     * `msu1` / `msu-md` / `mdplus` when the bundle is an enhanced-audio pack
     * (see [com.savesync.android.sync.MsuPack]); absent for every other entry.
     */
    @SerializedName("bundle_kind")
    val bundleKind: String? = null,
    /** Wii U only: `game` / `update` / `dlc` / `demo`. */
    @SerializedName("content_type")
    val contentType: String? = null,
    /** Wii U only: the base-game title id this entry belongs to. */
    @SerializedName("base_title_id")
    val baseTitleId: String? = null,
    /**
     * Wii U only: the *other* pieces of this game (its update, DLC, or the
     * base game if this is one of those), server-ordered game → update → DLC.
     * Nullable for the same Gson reason as [extractFormats].
     */
    @SerializedName("related_rom_ids")
    val relatedRomIds: List<String>? = null
)

/**
 * The MSU pack kind of this entry, or null when it is not a pack.  Only a
 * bundle can be one, and only kinds this client knows how to lay out count.
 */
val RomEntry.msuPackKind: String?
    get() = bundleKind?.trim()?.lowercase()
        ?.takeIf { isBundle && it in com.savesync.android.sync.MsuPack.KINDS }

/**
 * Install order for a Wii U title's pieces.  MCP rejects an update or DLC
 * whose base game is not on the console yet, so downloading — and therefore
 * installing — has to follow this sequence.
 */
private val WIIU_CONTENT_ORDER = listOf("game", "update", "dlc", "demo")

/**
 * This entry together with its updates / DLC, in install order.
 *
 * Returns just `this` for systems with no grouping, or for a Wii U title the
 * server found no siblings for.  Tapping *any* piece yields the whole set:
 * an update on its own is unusable, so queuing the group is what the user
 * actually wants either way.
 */
fun RomEntry.withRelated(catalog: List<RomEntry>): List<RomEntry> {
    val ids = relatedRomIds.orEmpty()
    if (ids.isEmpty()) return listOf(this)

    val byId = catalog.associateBy { it.rom_id ?: it.title_id }
    return (listOf(this) + ids.mapNotNull { byId[it] })
        .distinctBy { it.rom_id ?: it.title_id }
        .sortedBy {
            val idx = WIIU_CONTENT_ORDER.indexOf(it.contentType?.trim()?.lowercase())
            if (idx < 0) WIIU_CONTENT_ORDER.size else idx
        }
}

/** Server-advertised extract formats, lowercased; empty when none. */
val RomEntry.extractFormatList: List<String>
    get() = extractFormats.orEmpty().mapNotNull { it.trim().lowercase().ifEmpty { null } }

data class RomsResponse(
    val roms: List<RomEntry>,
    val total: Int
)

data class RomsSystemsResponse(
    val systems: List<String>,
    val stats: Map<String, Int>
)

private const val REQUIRED_3DS_EXTRACT_FORMAT = "decrypted_cci"

/** Source filename extensions the server's 3DS extractor can convert. */
private val CONVERTIBLE_3DS_SOURCE_EXTENSIONS = setOf("3ds", "cci", "zip")

/** Xbox systems that use xemu (which only supports ISO, not CCI). */
private val XBOX_SYSTEMS = setOf("XBOX", "X360", "XBOX360")

/**
 * For 3DS catalog entries with a convertible source extension (.3ds / .cci /
 * .zip), always request ``decrypted_cci`` so the server hands back a
 * decrypted .cci that Azahar / Citra forks can load directly.
 *
 * For Xbox/X360 entries, always request ``iso`` because xemu does not
 * support CCI format.  If the source is already .iso the server streams
 * it directly (no conversion overhead); if it's .cci the server runs
 * XGDTool conversion and caches the result.
 *
 * We deliberately don't gate on the catalog's ``extract_formats`` list — if
 * the server's converter isn't configured the endpoint returns a 503 with an
 * actionable error message, which is much more useful than silently falling
 * back to the raw (possibly encrypted) source file.
 *
 * Returns null for entries where the raw download path is the only sensible
 * fallback.
 */
fun RomEntry.preferredDownloadExtractFormat(): String? {
    val sysUp = system.uppercase()

    if (sysUp == "3DS") {
        val sourceExt = filename.substringAfterLast('.', "").lowercase()
        if (sourceExt !in CONVERTIBLE_3DS_SOURCE_EXTENSIONS) {
            val legacy = extractFormat?.trim()?.lowercase().orEmpty()
            return legacy.takeIf { it == REQUIRED_3DS_EXTRACT_FORMAT }
        }
        return REQUIRED_3DS_EXTRACT_FORMAT
    }

    if (sysUp in XBOX_SYSTEMS) {
        // xemu only loads ISO — always request iso format.  Server handles
        // no-op pass-through when source is already .iso, or CCI→ISO
        // conversion via XGDTool when source is .cci.
        return "iso"
    }

    if (sysUp == "WIIU") {
        // Unlike 3DS/Xbox above, a Wii U WUP bundle is directly usable as
        // downloaded: Cemu decrypts it from the bundled ticket, exactly as
        // the console does.  So take the raw bundle unless this server has
        // actually advertised a decrypted format — requesting one it can't
        // produce would turn a working download into a 503.
        return if (isBundle && "loadiine" in extractFormatList) "loadiine" else null
    }

    return null
}

/**
 * On-disk filename for a download given the chosen [extractFormat].  Mirrors
 * the server's filename rewrite (``<stem>.cci`` for ``decrypted_cci``,
 * ``<stem>.cia`` for ``cia``) so the local copy ends with the actual content
 * type instead of the source extension.
 *
 * Without this, a download triggered with ``extract=decrypted_cci`` would
 * land at ``<stem>.3ds`` (or ``<stem>.zip``), and Azahar wouldn't recognise
 * the contents.
 */
fun RomEntry.preferredDownloadFilename(extractFormat: String?): String {
    val stem = filename.substringBeforeLast('.', filename)
    return when (extractFormat?.trim()?.lowercase()) {
        "decrypted_cci" -> "$stem.cci"
        "cia"           -> "$stem.cia"
        "psp", "iso"    -> "$stem.iso"
        "cso"           -> "$stem.cso"
        "rvz"           -> "$stem.iso"  // server's rvz → iso conversion
        "gdi"           -> "$stem.gdi"
        "cue"           -> "$stem.cue"
        // The ZIP is a staging artifact only — DownloadManager unpacks it to
        // "<stem>/" and deletes it.  Naming it here keeps the .part path and
        // the extract target derivable from one field.
        "loadiine"      -> "$stem.zip"
        else            -> filename
    }
}
