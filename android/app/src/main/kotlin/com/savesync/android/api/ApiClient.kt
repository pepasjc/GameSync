package com.savesync.android.api

import com.savesync.android.BuildConfig
import okhttp3.Interceptor
import okhttp3.OkHttpClient
import okhttp3.Response
import okhttp3.logging.HttpLoggingInterceptor
import retrofit2.Retrofit
import retrofit2.converter.gson.GsonConverterFactory
import java.util.concurrent.TimeUnit

class ApiKeyInterceptor(private val apiKey: String) : Interceptor {
    override fun intercept(chain: Interceptor.Chain): Response {
        val request = chain.request().newBuilder()
            .addHeader("X-API-Key", apiKey)
            .build()
        return chain.proceed(request)
    }
}

/**
 * Raises the per-call read/write timeout for ROM catalog downloads.
 *
 * Two distinct slow paths share this rule:
 *
 *  1. ``/api/v1/roms/{id}?extract=...`` — the server runs ``chdman`` /
 *     ``DolphinTool`` / ``mount_cci`` synchronously before streaming the
 *     response, which can take several minutes for a multi-GB 3DS cart
 *     image on a Raspberry Pi.
 *  2. ``/api/v1/roms/{id}`` (plain download) — multi-GB ROMs over slow
 *     wifi can stream for many minutes too.  The default OkHttp 60 s
 *     read timeout is per-read, but a slow-but-steady transfer can
 *     intermittently stall longer than 60 s and trip the watchdog —
 *     observed as "app crashes during long downloads".
 *
 * Both cases now get the same 30-minute window, matching the server-side
 * ``subprocess.run`` timeout for the slowest converter (3DS).
 */
class RomDownloadTimeoutInterceptor : Interceptor {
    override fun intercept(chain: Interceptor.Chain): Response {
        val request = chain.request()
        val url = request.url
        val isRomDownload = url.encodedPathSegments.size >= 4 &&
            url.encodedPathSegments[0] == "api" &&
            url.encodedPathSegments[1] == "v1" &&
            url.encodedPathSegments[2] == "roms" &&
            // Skip the catalog index endpoints (``/roms``, ``/roms/systems``,
            // ``/roms/normalize``).  Only individual-rom GETs need the long
            // window — those have a 4th segment that's the rom id.
            url.encodedPathSegments[3].isNotBlank() &&
            url.encodedPathSegments[3] != "systems" &&
            url.encodedPathSegments[3] != "normalize"
        return if (isRomDownload) {
            chain.withReadTimeout(30, TimeUnit.MINUTES)
                .withWriteTimeout(30, TimeUnit.MINUTES)
                .proceed(request)
        } else {
            chain.proceed(request)
        }
    }
}

/**
 * Backwards-compatible alias.  Older code paths (and external integrations)
 * may still reference [RomExtractTimeoutInterceptor]; this typealias keeps
 * them compiling without forcing every caller to rename at once.
 */
typealias RomExtractTimeoutInterceptor = RomDownloadTimeoutInterceptor

object ApiClient {

    private var currentApi: SaveSyncApi? = null
    private var currentBaseUrl: String? = null
    private var currentApiKey: String? = null

    fun create(baseUrl: String, apiKey: String): SaveSyncApi {
        val normalizedUrl = if (baseUrl.endsWith("/")) baseUrl else "$baseUrl/"

        if (currentApi != null &&
            currentBaseUrl == normalizedUrl &&
            currentApiKey == apiKey
        ) {
            return currentApi!!
        }

        // Two loggers. ROM downloads need HEADERS-level (or below) because
        // BODY-level buffers the entire response body in RAM to log it —
        // silently defeats @Streaming and OOMs the app on multi-GB ROMs.
        // Every other endpoint (especially save downloads) keeps the
        // historical BODY-level behavior, which is what the rest of the
        // SyncEngine code path was tested against. Switching saves away
        // from BODY caused a regression in the post-receipt processing
        // path that nobody had time to track down — keeping BODY here
        // sidesteps it entirely.
        val romLogger = HttpLoggingInterceptor().apply {
            level = if (BuildConfig.DEBUG) {
                HttpLoggingInterceptor.Level.HEADERS
            } else {
                HttpLoggingInterceptor.Level.NONE
            }
        }
        val defaultLogger = HttpLoggingInterceptor().apply {
            level = if (BuildConfig.DEBUG) {
                HttpLoggingInterceptor.Level.BODY
            } else {
                HttpLoggingInterceptor.Level.NONE
            }
        }
        val routedLogger = Interceptor { chain ->
            val segs = chain.request().url.encodedPathSegments
            val isRomDownload = segs.size >= 4 &&
                segs[0] == "api" && segs[1] == "v1" && segs[2] == "roms" &&
                segs[3].isNotBlank() &&
                segs[3] != "systems" && segs[3] != "normalize"
            if (isRomDownload) romLogger.intercept(chain)
            else defaultLogger.intercept(chain)
        }

        val okHttpClient = OkHttpClient.Builder()
            .addInterceptor(ApiKeyInterceptor(apiKey))
            .addInterceptor(RomDownloadTimeoutInterceptor())
            .addInterceptor(routedLogger)
            // 60 s connect: cellular DNS + TLS handshake to a duckdns
            // endpoint over weak signal can easily exceed 30 s. We'd
            // rather wait an extra half-minute than fail to start.
            .connectTimeout(60, TimeUnit.SECONDS)
            .readTimeout(60, TimeUnit.SECONDS)
            .writeTimeout(60, TimeUnit.SECONDS)
            // Disable OkHttp's request-level call timeout — the per-call
            // read/write timeouts already cover stalled streams, and a
            // global call timeout would prematurely kill multi-hour ROM
            // downloads on slow connections.
            .callTimeout(0, TimeUnit.MILLISECONDS)
            .build()

        val retrofit = Retrofit.Builder()
            .baseUrl(normalizedUrl)
            .client(okHttpClient)
            .addConverterFactory(GsonConverterFactory.create())
            .build()

        val api = retrofit.create(SaveSyncApi::class.java)
        currentApi = api
        currentBaseUrl = normalizedUrl
        currentApiKey = apiKey
        return api
    }

    fun invalidate() {
        currentApi = null
        currentBaseUrl = null
        currentApiKey = null
    }
}
