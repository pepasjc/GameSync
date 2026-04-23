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
 * Kotlin implementation of the 3DSS binary bundle format.
 *
 * Create: always v4 (32-byte ASCII null-padded title ID, zlib-compressed).
 *
 * Parse: accepts every version the server may serve:
 *   v1 — 8-byte big-endian u64 title ID, UNCOMPRESSED payload (legacy 3DS).
 *   v2 — 8-byte big-endian u64 title ID, zlib-compressed payload (3DS + NDS).
 *   v3 — 16-byte ASCII null-padded title ID, zlib-compressed (legacy PSP/Vita).
 *   v4 — 32-byte ASCII null-padded title ID, zlib-compressed (PSP + 3DS emu).
 *   v5 — 64-byte ASCII null-padded title ID, zlib-compressed (PS3).
 *
 * 3DS saves uploaded from the 3DS homebrew client come back as v2 bundles
 * (the 3DS client always uses the compressed integer-title-id variant), so
 * the Android emulator client must parse v2 to download those saves.
 *
 * Payload structure (same for all versions):
 *   File table: for each file — [2B path length][NB path UTF-8][4B size][32B SHA-256]
 *   File data:  for each file in the same order — [NB raw bytes]
 *
 * The server's hash computation is:
 *   sha256( concat(file1.data, file2.data, ...) )  — in bundle order
 *
 * Files are always added in deterministic order so the hash matches the server.
 */
object BundleUtils {

    private val MAGIC = byteArrayOf('3'.code.toByte(), 'D'.code.toByte(), 'S'.code.toByte(), 'S'.code.toByte())
    private const val VERSION_V1 = 1
    private const val VERSION_V2 = 2
    private const val VERSION_V3 = 3
    private const val VERSION_V4 = 4
    private const val VERSION_V5 = 5

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

    /**
     * Creates a v4 bundle from every file under [saveDir], recursively sorted by relative path.
     * Used for 3DS save trees where the save archive contains nested directories.
     */
    fun createTreeBundle(titleId: String, saveDir: File): ByteArray {
        val files = saveDir.walkTopDown()
            .filter { it.isFile }
            .sortedBy { it.relativeTo(saveDir).path.replace('\\', '/') }
            .toList()

        val payload = buildPayload(files) { file ->
            file.relativeTo(saveDir).path.replace('\\', '/')
        }

        val compressed = ByteArrayOutputStream().also { baos ->
            DeflaterOutputStream(baos, java.util.zip.Deflater(6)).use { it.write(payload) }
        }.toByteArray()

        val timestamp = (System.currentTimeMillis() / 1000L).toInt()

        return ByteArrayOutputStream().apply {
            write(MAGIC)
            write(int32LE(VERSION_V4))
            val tidBytes = titleId.toByteArray(Charsets.US_ASCII)
            val field = ByteArray(32)
            tidBytes.copyInto(field, 0, 0, minOf(tidBytes.size, 31))
            write(field)
            write(int32LE(timestamp))
            write(int32LE(files.size))
            write(int32LE(payload.size))
            write(compressed)
        }.toByteArray()
    }

    private fun buildPayload(
        files: List<File>,
        nameForFile: (File) -> String = { it.name }
    ): ByteArray =
        ByteArrayOutputStream().apply {
            // File table
            for (f in files) {
                val nameBytes = nameForFile(f).toByteArray(Charsets.UTF_8)
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
     * Parses a 3DSS bundle of any supported version (v1–v5).
     *
     * v1/v2 use an 8-byte big-endian u64 title ID (3DS + NDS homebrew clients).
     * v3/v4/v5 use an ASCII null-padded string title ID of 16/32/64 bytes (PSP/Vita/PS3).
     *
     * v1 is the only uncompressed variant; every other version has a zlib-
     * compressed payload. The last header field is "total size" for v1 and
     * "uncompressed size" for v2+ — we skip size validation for v1 because
     * total_size is the sum of file sizes, not the payload length (which also
     * includes the file table).
     *
     * @return Ordered list of (filename, data) pairs — same order as in the bundle.
     * @throws IllegalArgumentException if the bundle is malformed.
     */
    fun parseBundle(data: ByteArray): List<Pair<String, ByteArray>> {
        val buf = ByteBuffer.wrap(data).order(ByteOrder.LITTLE_ENDIAN)

        val magic = ByteArray(4).also { buf.get(it) }
        require(magic.contentEquals(MAGIC)) { "Invalid 3DSS magic: ${magic.decodeToString()}" }

        val version = buf.int

        // Title ID field width depends on version. v1/v2 use a raw u64, the
        // string variants pad to a fixed byte width. We don't need the title
        // ID here — the caller already knows it from the URL — so just skip.
        val titleIdFieldSize = when (version) {
            VERSION_V1, VERSION_V2 -> 8
            VERSION_V3 -> 16
            VERSION_V4 -> 32
            VERSION_V5 -> 64
            else -> throw IllegalArgumentException("Unsupported bundle version: $version")
        }
        buf.position(buf.position() + titleIdFieldSize)

        /* val timestamp = */ buf.int
        val fileCount  = buf.int
        /* last size field: "total data size" (v1) or "uncompressed payload size" (v2+). */
        val sizeField = buf.int

        val payload = if (version == VERSION_V1) {
            // v1: remainder is the raw payload — no decompression.
            ByteArray(buf.remaining()).also { buf.get(it) }
        } else {
            val compressed = ByteArray(buf.remaining()).also { buf.get(it) }
            val inflated = InflaterInputStream(ByteArrayInputStream(compressed))
                .use { it.readBytes() }
            require(inflated.size == sizeField) {
                "Decompressed size mismatch: expected $sizeField, got ${inflated.size}"
            }
            inflated
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
