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

        val loggingInterceptor = HttpLoggingInterceptor().apply {
            level = if (BuildConfig.DEBUG) {
                HttpLoggingInterceptor.Level.BODY
            } else {
                HttpLoggingInterceptor.Level.NONE
            }
        }

        val okHttpClient = OkHttpClient.Builder()
            .addInterceptor(ApiKeyInterceptor(apiKey))
            .addInterceptor(RomDownloadTimeoutInterceptor())
            .addInterceptor(loggingInterceptor)
            .connectTimeout(30, TimeUnit.SECONDS)
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
