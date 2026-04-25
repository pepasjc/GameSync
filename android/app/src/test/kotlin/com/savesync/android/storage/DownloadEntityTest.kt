package com.savesync.android.storage

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Unit tests for the [DownloadEntity] helpers consumed by the Downloads
 * screen.  These properties drive UI gating — getting them wrong shows
 * stale buttons or never-completing progress bars in production.
 */
class DownloadEntityTest {

    private fun base(
        status: String,
        downloaded: Long = 0,
        total: Long = -1,
    ) = DownloadEntity(
        id = "test-id",
        romId = "rom-1",
        system = "PS2",
        displayName = "Game",
        filename = "game.iso",
        partFilePath = "/tmp/game.iso.part",
        finalFilePath = "/tmp/game.iso",
        totalBytes = total,
        downloadedBytes = downloaded,
        status = status,
        errorMessage = null,
        extractFormat = null,
        createdAt = 1000L,
        updatedAt = 1000L,
    )

    @Test
    fun `isTerminal is true for completed`() {
        assertTrue(base(DownloadEntity.Status.COMPLETED).isTerminal)
    }

    @Test
    fun `isTerminal is true for failed`() {
        assertTrue(base(DownloadEntity.Status.FAILED).isTerminal)
    }

    @Test
    fun `isTerminal is true for cancelled`() {
        assertTrue(base(DownloadEntity.Status.CANCELLED).isTerminal)
    }

    @Test
    fun `isTerminal is false for downloading`() {
        assertFalse(base(DownloadEntity.Status.DOWNLOADING).isTerminal)
    }

    @Test
    fun `isTerminal is false for paused`() {
        assertFalse(base(DownloadEntity.Status.PAUSED).isTerminal)
    }

    @Test
    fun `isTerminal is false for queued`() {
        assertFalse(base(DownloadEntity.Status.QUEUED).isTerminal)
    }

    @Test
    fun `progressFraction is null when total unknown`() {
        assertNull(base(DownloadEntity.Status.DOWNLOADING, downloaded = 100L, total = -1).progressFraction)
    }

    @Test
    fun `progressFraction is null when total is zero`() {
        assertNull(base(DownloadEntity.Status.DOWNLOADING, downloaded = 100L, total = 0).progressFraction)
    }

    @Test
    fun `progressFraction matches downloaded over total`() {
        val fraction = base(DownloadEntity.Status.DOWNLOADING, downloaded = 250L, total = 1000L).progressFraction
        assertEquals(0.25f, fraction!!, 0.0001f)
    }

    @Test
    fun `progressFraction clamps above one`() {
        // Defensive — if a buggy server sends more bytes than Content-Length
        // promised, the bar must not overflow.
        val fraction = base(DownloadEntity.Status.DOWNLOADING, downloaded = 1500L, total = 1000L).progressFraction
        assertEquals(1f, fraction!!, 0.0001f)
    }

    @Test
    fun `progressFraction clamps below zero`() {
        // Very defensive — negative downloaded should never happen but is
        // still safer to clamp.
        val fraction = base(DownloadEntity.Status.DOWNLOADING, downloaded = -50L, total = 1000L).progressFraction
        assertEquals(0f, fraction!!, 0.0001f)
    }
}
