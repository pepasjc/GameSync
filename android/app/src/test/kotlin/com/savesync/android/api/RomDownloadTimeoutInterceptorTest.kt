package com.savesync.android.api

import okhttp3.HttpUrl.Companion.toHttpUrl
import okhttp3.Interceptor
import okhttp3.Protocol
import okhttp3.Request
import okhttp3.Response
import okhttp3.ResponseBody.Companion.toResponseBody
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.util.concurrent.TimeUnit

/**
 * Verifies the URL-classifier behind [RomDownloadTimeoutInterceptor].
 *
 * Background — the original interceptor only widened the timeout for
 * ``?extract=`` requests, so plain ROM streams (multi-GB ISOs over slow
 * wifi) still hit the 60 s ceiling and crashed mid-download.  These
 * tests document the fix and guard against regressions.
 */
class RomDownloadTimeoutInterceptorTest {

    /**
     * Stub Chain that records whether ``withReadTimeout`` was called.
     * We can't actually exercise OkHttp from a unit test, but the
     * interceptor's job is exactly that one decision so the test only
     * needs to observe it.
     */
    private class RecordingChain(private val request: Request) : Interceptor.Chain {
        var readTimeoutChanged = false
        var writeTimeoutChanged = false
        var lastReadTimeoutMillis: Int = 0
        var lastWriteTimeoutMillis: Int = 0

        override fun request(): Request = request
        override fun proceed(request: Request): Response =
            Response.Builder()
                .request(request)
                .protocol(Protocol.HTTP_1_1)
                .code(200)
                .message("OK")
                .body("".toResponseBody(null))
                .build()

        override fun connection() = null
        override fun call() = throw UnsupportedOperationException()
        override fun connectTimeoutMillis(): Int = 0
        override fun readTimeoutMillis(): Int = lastReadTimeoutMillis
        override fun writeTimeoutMillis(): Int = lastWriteTimeoutMillis
        override fun withConnectTimeout(timeout: Int, unit: TimeUnit): Interceptor.Chain = this
        override fun withReadTimeout(timeout: Int, unit: TimeUnit): Interceptor.Chain {
            readTimeoutChanged = true
            lastReadTimeoutMillis = unit.toMillis(timeout.toLong()).toInt()
            return this
        }
        override fun withWriteTimeout(timeout: Int, unit: TimeUnit): Interceptor.Chain {
            writeTimeoutChanged = true
            lastWriteTimeoutMillis = unit.toMillis(timeout.toLong()).toInt()
            return this
        }
    }

    private fun makeRequest(url: String): Request =
        Request.Builder().url(url.toHttpUrl()).build()

    @Test
    fun `widens timeout for plain ROM download`() {
        // Regression: this used to fall through to the default 60s timeout
        // and crash long downloads.  After the fix, plain GETs against
        // ``/api/v1/roms/{rom_id}`` get the 30-minute window.
        val chain = RecordingChain(makeRequest("https://srv/api/v1/roms/myrom_id"))
        RomDownloadTimeoutInterceptor().intercept(chain)
        assertTrue(chain.readTimeoutChanged)
        assertTrue(chain.writeTimeoutChanged)
        assertEquals(TimeUnit.MINUTES.toMillis(30).toInt(), chain.lastReadTimeoutMillis)
    }

    @Test
    fun `widens timeout for ROM extract`() {
        val chain = RecordingChain(
            makeRequest("https://srv/api/v1/roms/abc?extract=cia")
        )
        RomDownloadTimeoutInterceptor().intercept(chain)
        assertTrue(chain.readTimeoutChanged)
    }

    @Test
    fun `widens timeout for resumed range request`() {
        // Range-resume re-uses the same endpoint — the interceptor must
        // still trigger.  Anything else lets resume time out the same way
        // the original download did.
        val chain = RecordingChain(makeRequest("https://srv/api/v1/roms/abc"))
        RomDownloadTimeoutInterceptor().intercept(chain)
        assertTrue(chain.readTimeoutChanged)
    }

    @Test
    fun `does not widen timeout for catalog index`() {
        // /api/v1/roms (no rom id) is the catalog list — should keep the
        // short default.
        val chain = RecordingChain(makeRequest("https://srv/api/v1/roms"))
        RomDownloadTimeoutInterceptor().intercept(chain)
        assertFalse(chain.readTimeoutChanged)
    }

    @Test
    fun `does not widen timeout for systems endpoint`() {
        val chain = RecordingChain(makeRequest("https://srv/api/v1/roms/systems"))
        RomDownloadTimeoutInterceptor().intercept(chain)
        assertFalse(chain.readTimeoutChanged)
    }

    @Test
    fun `does not widen timeout for normalize endpoint`() {
        val chain = RecordingChain(makeRequest("https://srv/api/v1/roms/normalize"))
        RomDownloadTimeoutInterceptor().intercept(chain)
        assertFalse(chain.readTimeoutChanged)
    }

    @Test
    fun `does not widen timeout for save endpoint`() {
        val chain = RecordingChain(
            makeRequest("https://srv/api/v1/saves/0004000000055D00")
        )
        RomDownloadTimeoutInterceptor().intercept(chain)
        assertFalse(chain.readTimeoutChanged)
    }
}
