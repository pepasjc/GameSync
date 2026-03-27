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
