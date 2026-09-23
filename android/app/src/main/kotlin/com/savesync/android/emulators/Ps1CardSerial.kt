package com.savesync.android.emulators

import java.io.File

/**
 * Reads the canonical PS1 save serial straight out of a raw memory-card image
 * (DuckStation ``.mcd`` / ``.mcr``, RetroArch ``.srm``), matching the server's
 * ``ps1mc.serial_from_filename``.
 *
 * The server keys every PS1 save by the product code embedded in the save
 * block's directory frame (e.g. ``BISLPS-00555...`` → ``SLPS00555``), which the
 * game writes identically across regional / limited editions. The disc serial
 * parsed from SYSTEM.CNF can differ for variant releases (e.g. a "Gentei Box"
 * disc that boots ``SLPS-00545`` but still writes ``BISLPS-00555`` to the card),
 * so preferring the in-card code keeps the local title ID aligned with the
 * server slot regardless of which emulator (or disc edition) produced the card.
 *
 * PS1 cards are 128 KB = 16 blocks * 8 KB.  Block 0 frames 1..15 each hold one
 * directory entry; a frame whose first byte is 0x51 (ST_FIRST) starts a save,
 * with the product-code filename at offset 0x0A (up to 20 bytes).
 */
object Ps1CardSerial {

    private const val BLOCK_SIZE = 8192
    private const val FRAME_SIZE = 128
    private const val CARD_SIZE = BLOCK_SIZE * 16   // 131072
    private const val ST_FIRST = 0x51

    private val SERIAL_RE = Regex(
        "(SLUS|SCUS|SLES|SCES|SLPS|SLPM|SCPS|SLKA|SCAJ|SLPN)[-_ ]?(\\d{3})[.]?(\\d{2})",
        RegexOption.IGNORE_CASE
    )

    /**
     * Returns a bare product code (e.g. "SLPS00555"), or null if [cardFile] is
     * not a recognisable PS1 card or has no save with a parseable serial.
     */
    fun readSaveSerial(cardFile: File): String? =
        readBlock0(cardFile)?.let { block0 -> firstSaveSerial(block0) }

    /**
     * True when [cardFile] is a valid PS1 memory card that holds no saves
     * (a freshly formatted / phantom per-game card). Such a card has nothing
     * to sync, so callers drop it instead of surfacing a bogus row keyed by the
     * card's filename. Returns false for non-card files (let normal handling
     * decide) and for cards that contain at least one save.
     */
    fun isEmptyCard(cardFile: File): Boolean {
        val block0 = readBlock0(cardFile) ?: return false
        for (i in 1 until 16) {
            if ((block0[i * FRAME_SIZE].toInt() and 0xFF) == ST_FIRST) return false
        }
        return true
    }

    /** Reads and validates block 0 (the directory block); null if not a PS1 card. */
    private fun readBlock0(cardFile: File): ByteArray? {
        return try {
            if (cardFile.length().toInt() != CARD_SIZE) return null
            val block0 = ByteArray(BLOCK_SIZE)
            cardFile.inputStream().use { stream ->
                if (stream.read(block0) < BLOCK_SIZE) return null
            }
            if (block0[0] != 'M'.code.toByte() || block0[1] != 'C'.code.toByte()) return null
            block0
        } catch (_: Exception) {
            null
        }
    }

    /** Serial of the first save in directory block 0, or null if none parses. */
    private fun firstSaveSerial(block0: ByteArray): String? {
        for (i in 1 until 16) {
            val frameOff = i * FRAME_SIZE
            if ((block0[frameOff].toInt() and 0xFF) != ST_FIRST) continue
            val nameStart = frameOff + 0x0A
            var nameEnd = nameStart
            while (nameEnd < nameStart + 20 && block0[nameEnd].toInt() != 0) nameEnd++
            val name = String(block0, nameStart, nameEnd - nameStart, Charsets.US_ASCII)
            serialFromSaveName(name)?.let { return it }
        }
        return null
    }

    /**
     * Maps a PS1 save filename (``BISLPS-00555...``) to a compact serial
     * (``SLPS00555``).  Mirrors the server ``ps1mc._PS1_SERIAL_RE``; handles the
     * dotted exe-name form (``SLPS_005.55``) and the run-together card form.
     */
    fun serialFromSaveName(name: String): String? {
        val m = SERIAL_RE.find(name) ?: return null
        return (m.groupValues[1] + m.groupValues[2] + m.groupValues[3]).uppercase()
    }
}
