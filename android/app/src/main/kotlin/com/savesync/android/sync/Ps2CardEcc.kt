package com.savesync.android.sync

import java.io.File
import java.security.MessageDigest

/**
 * PS2 memory-card ECC, ported from ``server/app/services/ps2_cards.py`` — keep
 * the two in step.
 *
 * A PCSX2-style ``.ps2`` card stores a 16-byte spare area after every 512-byte
 * page.  The server keeps cards ECC-stripped (``card.mc2``) and reports the
 * ``format=ps2`` hash of the card with the spare areas *recomputed*.  The
 * spare bytes an emulator leaves on disk don't always match that — a freshly
 * formatted PCSX2/ARMSX2 card has ``FF`` spares on erased pages — so hashing
 * the raw file would never agree with the server, no matter how often it is
 * uploaded.  [normalizedHash] hashes the card the way the server does.
 */
object Ps2CardEcc {
    const val PAGE_SIZE = 512
    const val SPARE_SIZE = 16
    const val PAGES_PER_CARD = 16384
    const val MC2_SIZE = PAGE_SIZE * PAGES_PER_CARD
    const val PS2_SIZE = (PAGE_SIZE + SPARE_SIZE) * PAGES_PER_CARD

    private val parityTable = IntArray(256) { b ->
        var v = b
        v = v xor (v shr 1)
        v = v xor (v shr 2)
        v = v xor (v shr 4)
        v and 1
    }

    private val columnParityMasks = IntArray(256) { b ->
        val cpMasks = intArrayOf(0x55, 0x33, 0x0F, 0x00, 0xAA, 0xCC, 0xF0)
        var mask = 0
        cpMasks.forEachIndexed { idx, cp -> mask = mask or (parityTable[b and cp] shl idx) }
        mask
    }

    /** Writes the 16-byte spare area for the 512-byte page at [page]/[offset] into [out]. */
    fun pageSpare(page: ByteArray, offset: Int = 0, out: ByteArray = ByteArray(SPARE_SIZE)): ByteArray {
        for (chunk in 0 until 4) {
            var columnParity = 0x77
            var lineParity0 = 0x7F
            var lineParity1 = 0x7F
            val base = offset + chunk * 128
            for (idx in 0 until 128) {
                val byte = page[base + idx].toInt() and 0xFF
                columnParity = columnParity xor columnParityMasks[byte]
                if (parityTable[byte] != 0) {
                    lineParity0 = lineParity0 xor idx.inv()
                    lineParity1 = lineParity1 xor idx
                }
            }
            out[chunk * 3] = (columnParity and 0xFF).toByte()
            out[chunk * 3 + 1] = (lineParity0 and 0x7F).toByte()
            out[chunk * 3 + 2] = (lineParity1 and 0x7F).toByte()
        }
        out.fill(0, 12, SPARE_SIZE)
        return out
    }

    /**
     * SHA-256 of [file] rendered as a ``.ps2`` card with freshly computed ECC —
     * the same bytes the server hashes for ``ps2-card/meta?format=ps2``.
     * Accepts both ``.ps2`` (ECC) and ``.mc2`` (raw) card sizes; returns null
     * for anything else so callers can fall back to a plain file hash.
     */
    fun normalizedHash(file: File): String? {
        val size = file.length()
        val stride = when (size) {
            PS2_SIZE.toLong() -> PAGE_SIZE + SPARE_SIZE
            MC2_SIZE.toLong() -> PAGE_SIZE
            else -> return null
        }
        val digest = MessageDigest.getInstance("SHA-256")
        val buf = ByteArray(stride)
        val spare = ByteArray(SPARE_SIZE)
        file.inputStream().buffered(1 shl 16).use { input ->
            repeat(PAGES_PER_CARD) {
                var read = 0
                while (read < stride) {
                    val n = input.read(buf, read, stride - read)
                    if (n < 0) return null
                    read += n
                }
                digest.update(buf, 0, PAGE_SIZE)
                digest.update(pageSpare(buf, 0, spare))
            }
        }
        return digest.digest().joinToString("") { "%02x".format(it) }
    }
}
