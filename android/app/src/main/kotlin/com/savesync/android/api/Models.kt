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
data class GameNameResponse(
    val names: Map<String, String>,
    val types: Map<String, String>,
    val retail_serials: Map<String, String>? = null
)

data class SaturnArchiveLookupRequest(
    val title_id: String,
    val archive_names: List<String>
)

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
    @SerializedName("extract_formats")
    val extractFormats: List<String> = emptyList()
)

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

/**
 * For 3DS catalog entries with a convertible source extension (.3ds / .cci /
 * .zip), always request ``decrypted_cci`` so the server hands back a
 * decrypted .cci that Azahar / Citra forks can load directly.
 *
 * We deliberately don't gate on the catalog's ``extract_formats`` list — if
 * the server's converter isn't configured the endpoint returns a 503 with an
 * actionable error message, which is much more useful than silently falling
 * back to the raw (possibly encrypted) source file.
 *
 * Returns null for non-3DS entries and for 3DS source files the server
 * can't convert (e.g. raw .cia, .app), where the raw download path is the
 * only sensible fallback.
 */
fun RomEntry.preferredDownloadExtractFormat(): String? {
    if (!system.equals("3DS", ignoreCase = true)) return null

    val sourceExt = filename.substringAfterLast('.', "").lowercase()
    if (sourceExt !in CONVERTIBLE_3DS_SOURCE_EXTENSIONS) {
        // Not a convertible source — fall back to raw download so the user
        // at least gets the file.  Honours legacy ``extract_format`` field
        // if the server somehow still advertises one.
        val legacy = extractFormat?.trim()?.lowercase().orEmpty()
        return legacy.takeIf { it == REQUIRED_3DS_EXTRACT_FORMAT }
    }

    return REQUIRED_3DS_EXTRACT_FORMAT
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
        else            -> filename
    }
}
