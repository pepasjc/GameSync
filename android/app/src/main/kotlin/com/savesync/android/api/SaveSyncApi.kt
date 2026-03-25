package com.savesync.android.api

import okhttp3.RequestBody
import okhttp3.ResponseBody
import retrofit2.Response
import retrofit2.http.Body
import retrofit2.http.GET
import retrofit2.http.Headers
import retrofit2.http.POST
import retrofit2.http.Path
import retrofit2.http.Query
import retrofit2.http.Streaming

interface SaveSyncApi {

    @GET("api/v1/status")
    suspend fun getStatus(): StatusResponse

    @GET("api/v1/saves/{title_id}/meta")
    suspend fun getSaveMeta(
        @Path("title_id") titleId: String
    ): SaveMeta

    @Streaming
    @GET("api/v1/saves/{title_id}/raw")
    suspend fun downloadSaveRaw(
        @Path("title_id") titleId: String
    ): Response<ResponseBody>

    @POST("api/v1/saves/{title_id}/raw")
    suspend fun uploadSaveRaw(
        @Path("title_id") titleId: String,
        @Query("source") source: String = "android",
        @Query("console_id") consoleId: String,
        @Body body: RequestBody
    ): UploadResponse

    /** Download a full bundle (v3/v4) — used for PPSSPP and any multi-file save. */
    @Streaming
    @GET("api/v1/saves/{title_id}")
    suspend fun downloadSaveBundle(
        @Path("title_id") titleId: String
    ): Response<ResponseBody>

    /** Upload a full bundle (v4) — used for PPSSPP saves; matches the PSP homebrew client. */
    @POST("api/v1/saves/{title_id}")
    @Headers("Content-Type: application/octet-stream")
    suspend fun uploadSaveBundle(
        @Path("title_id") titleId: String,
        @Query("source") source: String = "psp_emu",
        @Query("force") force: Boolean = true,
        @Query("console_id") consoleId: String,
        @Body body: RequestBody
    ): UploadResponse

    @POST("api/v1/sync")
    suspend fun sync(
        @Body request: SyncRequest
    ): SyncResponse

    @GET("api/v1/titles")
    suspend fun getTitles(): TitlesResponse

    /**
     * Normalize ROM filenames to canonical No-Intro names.
     * Returns null gracefully if server doesn't have the endpoint (older server).
     */
    @POST("api/v1/normalize/batch")
    suspend fun normalizeRoms(
        @Body request: NormalizeRequest
    ): NormalizeResponse

    /**
     * Look up game names for product codes (PSP, PS1, NDS, etc.).
     * Returns names and platform types keyed by product code.
     */
    @POST("api/v1/titles/names")
    suspend fun lookupGameNames(
        @Body request: GameNameRequest
    ): GameNameResponse
}
