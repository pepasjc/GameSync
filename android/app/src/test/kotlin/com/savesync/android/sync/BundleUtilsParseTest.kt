package com.savesync.android.sync

import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertThrows
import org.junit.Test
import java.io.ByteArrayOutputStream
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.security.MessageDigest
import java.util.zip.Deflater
import java.util.zip.DeflaterOutputStream

/**
 * Regression tests for [BundleUtils.parseBundle].
 *
 * The original implementation only accepted v3/v4 bundles, so 3DS saves
 * uploaded by the 3DS homebrew client (always v2 — compressed integer title
 * ID) were unreadable on Android and surfaced as "No save found on server".
 */
class BundleUtilsParseTest {

    @Test
    fun `parses a v1 uncompressed integer-title-id bundle`() {
        val files = listOf(
            "save.bin" to byteArrayOf(0x01, 0x02, 0x03, 0x04),
            "extra.dat" to byteArrayOf(0x10, 0x20)
        )
        val bundle = buildBundleV1(titleId = 0x0004000000055D00L, files = files)

        val parsed = BundleUtils.parseBundle(bundle)

        assertFilesMatch(files, parsed)
    }

    @Test
    fun `parses a v2 compressed integer-title-id bundle`() {
        // This is the format the 3DS homebrew client always uploads, and
        // therefore what an Android emulator client will download for 3DS
        // saves. Regression test for the "No save found on server" bug.
        val files = listOf(
            "00000001.sav" to ByteArray(512) { (it and 0xFF).toByte() },
            "nested/extdata.bin" to ByteArray(128) { ((it * 7) and 0xFF).toByte() }
        )
        val bundle = buildBundleV2(titleId = 0x0004000000055D00L, files = files)

        val parsed = BundleUtils.parseBundle(bundle)

        assertFilesMatch(files, parsed)
    }

    @Test
    fun `parses a v4 string-title-id bundle (smoke — unchanged path)`() {
        // The createBundle/createTreeBundle factories already emit v4, so just
        // round-trip one to make sure the refactor didn't regress that case.
        val dir = createTempDir()
        try {
            val f1 = java.io.File(dir, "file-a.bin").also { it.writeBytes(byteArrayOf(1, 2, 3)) }
            @Suppress("UNUSED_VARIABLE")
            val f2 = java.io.File(dir, "file-b.bin").also { it.writeBytes(byteArrayOf(4, 5)) }

            val bytes = BundleUtils.createBundle("GBA_test", dir)
            val parsed = BundleUtils.parseBundle(bytes).toMap()

            assertArrayEquals(byteArrayOf(1, 2, 3), parsed["file-a.bin"])
            assertArrayEquals(byteArrayOf(4, 5), parsed["file-b.bin"])
            assertEquals(2, parsed.size)
            // silence "unused" — reading f1's content above is what matters
            assertEquals(3, f1.length())
        } finally {
            dir.deleteRecursively()
        }
    }

    @Test
    fun `rejects unsupported bundle versions`() {
        val bundle = ByteArrayOutputStream().apply {
            write(byteArrayOf('3'.code.toByte(), 'D'.code.toByte(), 'S'.code.toByte(), 'S'.code.toByte()))
            write(int32LE(99))                  // version
            write(ByteArray(8))                 // bogus title ID
            write(int32LE(0))                   // timestamp
            write(int32LE(0))                   // file count
            write(int32LE(0))                   // size
        }.toByteArray()

        assertThrows(IllegalArgumentException::class.java) {
            BundleUtils.parseBundle(bundle)
        }
    }

    // ── helpers ───────────────────────────────────────────────────────────────

    private fun assertFilesMatch(
        expected: List<Pair<String, ByteArray>>,
        actual: List<Pair<String, ByteArray>>
    ) {
        assertEquals("file count", expected.size, actual.size)
        expected.forEachIndexed { i, (name, bytes) ->
            assertEquals("filename at index $i", name, actual[i].first)
            assertArrayEquals("bytes for $name", bytes, actual[i].second)
        }
    }

    private fun buildPayload(files: List<Pair<String, ByteArray>>): ByteArray {
        return ByteArrayOutputStream().apply {
            for ((name, bytes) in files) {
                val nameBytes = name.toByteArray(Charsets.UTF_8)
                write(int16LE(nameBytes.size))
                write(nameBytes)
                write(int32LE(bytes.size))
                write(sha256(bytes))
            }
            for ((_, bytes) in files) write(bytes)
        }.toByteArray()
    }

    private fun buildBundleV1(titleId: Long, files: List<Pair<String, ByteArray>>): ByteArray {
        val payload = buildPayload(files)
        val totalFileBytes = files.sumOf { it.second.size }
        return ByteArrayOutputStream().apply {
            write(byteArrayOf('3'.code.toByte(), 'D'.code.toByte(), 'S'.code.toByte(), 'S'.code.toByte()))
            write(int32LE(1))
            write(int64BE(titleId))
            write(int32LE(0))                         // timestamp
            write(int32LE(files.size))
            write(int32LE(totalFileBytes))            // "total size" in v1
            write(payload)
        }.toByteArray()
    }

    private fun buildBundleV2(titleId: Long, files: List<Pair<String, ByteArray>>): ByteArray {
        val payload = buildPayload(files)
        val compressed = ByteArrayOutputStream().also { baos ->
            DeflaterOutputStream(baos, Deflater(6)).use { it.write(payload) }
        }.toByteArray()
        return ByteArrayOutputStream().apply {
            write(byteArrayOf('3'.code.toByte(), 'D'.code.toByte(), 'S'.code.toByte(), 'S'.code.toByte()))
            write(int32LE(2))
            write(int64BE(titleId))
            write(int32LE(0))                         // timestamp
            write(int32LE(files.size))
            write(int32LE(payload.size))              // uncompressed size
            write(compressed)
        }.toByteArray()
    }

    private fun int16LE(v: Int): ByteArray =
        ByteBuffer.allocate(2).order(ByteOrder.LITTLE_ENDIAN).putShort(v.toShort()).array()

    private fun int32LE(v: Int): ByteArray =
        ByteBuffer.allocate(4).order(ByteOrder.LITTLE_ENDIAN).putInt(v).array()

    private fun int64BE(v: Long): ByteArray =
        ByteBuffer.allocate(8).order(ByteOrder.BIG_ENDIAN).putLong(v).array()

    private fun sha256(bytes: ByteArray): ByteArray =
        MessageDigest.getInstance("SHA-256").digest(bytes)

    private fun createTempDir(): java.io.File {
        return java.io.File.createTempFile("bundle-utils-test", "").apply {
            delete()
            mkdirs()
        }
    }
}
