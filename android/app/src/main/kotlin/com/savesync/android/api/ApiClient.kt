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
 * Raises the per-call read/write timeout for ROM catalog downloads that
 * trigger a server-side conversion (``/api/v1/roms/{id}?extract=...``).
 *
 * The server runs ``chdman`` / ``DolphinTool`` / ``mount_cci`` synchronously
 * before streaming the response, which can easily take several minutes for a
 * multi-GB 3DS cart image on a Raspberry Pi. Without this override the default
 * 60 s read timeout fires long before the conversion finishes, and the user
 * sees a misleading "ROM not found on server" error. 30 minutes matches the
 * server-side ``subprocess.run`` timeout for the slowest converter (3DS).
 */
class RomExtractTimeoutInterceptor : Interceptor {
    override fun intercept(chain: Interceptor.Chain): Response {
        val request = chain.request()
        val url = request.url
        val isRomExtract = url.encodedPathSegments.size >= 3 &&
            url.encodedPathSegments[0] == "api" &&
            url.encodedPathSegments[1] == "v1" &&
            url.encodedPathSegments[2] == "roms" &&
            url.queryParameter("extract")?.isNotBlank() == true
        return if (isRomExtract) {
            chain.withReadTimeout(30, TimeUnit.MINUTES)
                .withWriteTimeout(30, TimeUnit.MINUTES)
                .proceed(request)
        } else {
            chain.proceed(request)
        }
    }
}

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
            .addInterceptor(RomExtractTimeoutInterceptor())
            .addInterceptor(loggingInterceptor)
            .connectTimeout(30, TimeUnit.SECONDS)
            .readTimeout(60, TimeUnit.SECONDS)
            .writeTimeout(60, TimeUnit.SECONDS)
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
