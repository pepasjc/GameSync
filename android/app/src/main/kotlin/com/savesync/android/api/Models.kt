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
    val server_only: List<String>
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
    val names: Map<String, String>,  // product_code -> game_name
    val types: Map<String, String>,  // product_code -> platform_type
    val retail_serials: Map<String, String>? = null  // PSN code -> retail disc serial (e.g. "NPUJ00662" -> "SLPM86034")
)
