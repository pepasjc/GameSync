package com.savesync.android.sync

import java.io.ByteArrayInputStream
import java.io.ByteArrayOutputStream
import java.io.File
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.security.MessageDigest
import java.util.zip.DeflaterOutputStream
import java.util.zip.InflaterInputStream

/**
 * Kotlin implementation of the 3DSS binary bundle format (v3/v4).
 *
 * v3: 16-byte ASCII null-padded title ID, zlib-compressed payload.
 * v4: 32-byte ASCII null-padded title ID, zlib-compressed payload.
 *
 * Payload structure (same for all versions):
 *   File table: for each file — [2B path length][NB path UTF-8][4B size][32B SHA-256]
 *   File data:  for each file in the same order — [NB raw bytes]
 *
 * The server's hash computation is:
 *   sha256( concat(file1.data, file2.data, ...) )  — in bundle order
 *
 * Files are always added in ascending filename order so the hash is deterministic
 * and matches the PSP homebrew client.
 */
object BundleUtils {

    private val MAGIC = byteArrayOf('3'.code.toByte(), 'D'.code.toByte(), 'S'.code.toByte(), 'S'.code.toByte())
    private const val VERSION_V3 = 3
    private const val VERSION_V4 = 4

    // ── create ───────────────────────────────────────────────────────────────

    /**
     * Creates a v4 bundle from every file in [slotDir], sorted by filename (ascending).
     * The resulting bytes can be uploaded directly to `POST /api/v1/saves/{title_id}`.
     */
    fun createBundle(titleId: String, slotDir: File): ByteArray {
        val files = slotDir.listFiles()
            ?.filter { it.isFile }
            ?.sortedBy { it.name }
            ?: emptyList()

        val payload = buildPayload(files)

        // zlib-compress the payload (level 6 matches the PSP client)
        val compressed = ByteArrayOutputStream().also { baos ->
            DeflaterOutputStream(baos, java.util.zip.Deflater(6)).use { it.write(payload) }
        }.toByteArray()

        val timestamp = (System.currentTimeMillis() / 1000L).toInt()

        return ByteArrayOutputStream().apply {
            write(MAGIC)
            write(int32LE(VERSION_V4))
            // 32-byte null-padded ASCII title ID (max 31 chars + null terminator)
            val tidBytes = titleId.toByteArray(Charsets.US_ASCII)
            val field = ByteArray(32)
            tidBytes.copyInto(field, 0, 0, minOf(tidBytes.size, 31))
            write(field)
            write(int32LE(timestamp))
            write(int32LE(files.size))
            write(int32LE(payload.size))      // uncompressed size
            write(compressed)
        }.toByteArray()
    }

    private fun buildPayload(files: List<File>): ByteArray =
        ByteArrayOutputStream().apply {
            // File table
            for (f in files) {
                val nameBytes = f.name.toByteArray(Charsets.UTF_8)
                write(int16LE(nameBytes.size))
                write(nameBytes)
                write(int32LE(f.length().toInt()))
                write(sha256File(f))
            }
            // File data
            for (f in files) write(f.readBytes())
        }.toByteArray()

    // ── parse ────────────────────────────────────────────────────────────────

    /**
     * Parses a v3 or v4 bundle.
     * @return Ordered list of (filename, data) pairs — same order as in the bundle.
     * @throws IllegalArgumentException if the bundle is malformed.
     */
    fun parseBundle(data: ByteArray): List<Pair<String, ByteArray>> {
        val buf = ByteBuffer.wrap(data).order(ByteOrder.LITTLE_ENDIAN)

        val magic = ByteArray(4).also { buf.get(it) }
        require(magic.contentEquals(MAGIC)) { "Invalid 3DSS magic: ${magic.decodeToString()}" }

        val version = buf.int

        // Title ID field width depends on version
        val titleIdFieldSize = when (version) {
            VERSION_V3 -> 16
            VERSION_V4 -> 32
            else -> throw IllegalArgumentException("Unsupported bundle version: $version")
        }
        buf.position(buf.position() + titleIdFieldSize)  // skip title ID

        /* val timestamp = */ buf.int
        val fileCount  = buf.int
        val uncompressedSize = buf.int

        // Rest is zlib-compressed payload
        val compressed = ByteArray(buf.remaining()).also { buf.get(it) }
        val payload = InflaterInputStream(ByteArrayInputStream(compressed))
            .use { it.readBytes() }
        require(payload.size == uncompressedSize) {
            "Decompressed size mismatch: expected $uncompressedSize, got ${payload.size}"
        }

        return parsePayload(payload, fileCount)
    }

    private fun parsePayload(data: ByteArray, fileCount: Int): List<Pair<String, ByteArray>> {
        val buf = ByteBuffer.wrap(data).order(ByteOrder.LITTLE_ENDIAN)

        data class Entry(val name: String, val size: Int, val sha256: ByteArray)
        val table = ArrayList<Entry>(fileCount)

        // File table
        repeat(fileCount) {
            val nameLen = buf.short.toInt() and 0xFFFF
            val name = ByteArray(nameLen).also { buf.get(it) }.toString(Charsets.UTF_8)
            val size = buf.int
            val hash = ByteArray(32).also { buf.get(it) }
            table += Entry(name, size, hash)
        }

        // File data
        return table.map { entry ->
            val fileData = ByteArray(entry.size).also { buf.get(it) }
            val actual = sha256Bytes(fileData)
            require(actual.contentEquals(entry.sha256)) {
                "Hash mismatch for ${entry.name}: expected ${entry.sha256.hex()}, got ${actual.hex()}"
            }
            entry.name to fileData
        }
    }

    // ── helpers ──────────────────────────────────────────────────────────────

    private fun sha256File(file: File): ByteArray {
        val digest = MessageDigest.getInstance("SHA-256")
        file.inputStream().use { stream ->
            val buf = ByteArray(8192)
            var n: Int
            while (stream.read(buf).also { n = it } != -1) digest.update(buf, 0, n)
        }
        return digest.digest()
    }

    private fun sha256Bytes(data: ByteArray): ByteArray =
        MessageDigest.getInstance("SHA-256").digest(data)

    private fun ByteArray.hex(): String = joinToString("") { "%02x".format(it) }

    private fun int16LE(v: Int): ByteArray =
        ByteBuffer.allocate(2).order(ByteOrder.LITTLE_ENDIAN).putShort(v.toShort()).array()

    private fun int32LE(v: Int): ByteArray =
        ByteBuffer.allocate(4).order(ByteOrder.LITTLE_ENDIAN).putInt(v).array()
}
