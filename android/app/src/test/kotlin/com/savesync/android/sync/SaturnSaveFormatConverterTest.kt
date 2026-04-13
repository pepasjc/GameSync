package com.savesync.android.sync

import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.nio.charset.Charset

class SaturnSaveFormatConverterTest {

    @Test
    fun `yabause roundtrip preserves canonical saturn data`() {
        val canonical = buildCanonicalSample(
            name = "GRANDIA_001",
            comment = "Feena's Ho",
            rawData = ByteArray(100) { ((it * 3) and 0xFF).toByte() }
        )

        val yabause = SaturnSaveFormatConverter.fromCanonical(canonical, SaturnSyncFormat.YABAUSE)
        val rebuilt = SaturnSaveFormatConverter.toCanonical(yabause)

        assertEquals(0x10000, yabause.size)
        assertTrue(yabause.indices.filter { it % 2 == 0 }.all { yabause[it] == 0xFF.toByte() })
        assertArrayEquals(canonical, ByteArray(canonical.size) { idx -> yabause[idx * 2 + 1] })
        assertArrayEquals(canonical, rebuilt)
    }

    @Test
    fun `yabasanshiro roundtrip preserves canonical saturn data`() {
        val canonical = buildCanonicalSample(
            name = "GRANDIA_001",
            comment = "Feena's Ho",
            rawData = ByteArray(100) { ((it * 5) and 0xFF).toByte() }
        )

        val yabasanshiro = SaturnSaveFormatConverter.fromCanonical(canonical, SaturnSyncFormat.YABASANSHIRO)
        val rebuilt = SaturnSaveFormatConverter.toCanonical(yabasanshiro)

        assertEquals(0x800000, yabasanshiro.size)
        assertTrue(yabasanshiro.indices.filter { it % 2 == 0 }.all { yabasanshiro[it] == 0xFF.toByte() })
        assertArrayEquals(canonical, ByteArray(canonical.size) { idx -> yabasanshiro[idx * 2 + 1] })
        assertArrayEquals(canonical, rebuilt)
    }

    @Test
    fun `yabause roundtrip preserves larger saturn saves with block list overflow`() {
        val canonical = buildCanonicalSample(
            name = "GRANDIA_001",
            comment = "Feena's Ho",
            rawData = ByteArray(3040) { (it % 251).toByte() }
        )

        val yabause = SaturnSaveFormatConverter.fromCanonical(canonical, SaturnSyncFormat.YABAUSE)
        val rebuilt = SaturnSaveFormatConverter.toCanonical(yabause)

        assertEquals(0x10000, yabause.size)
        assertTrue(yabause.indices.filter { it % 2 == 0 }.all { yabause[it] == 0xFF.toByte() })
        assertArrayEquals(canonical, rebuilt)
    }

    @Test
    fun `yabasanshiro merge preserves existing saves and adds incoming title`() {
        val existingCanonical = buildCanonicalSample(
            name = "DRACULAX_01",
            comment = "Richter",
            rawData = ByteArray(64) { (it and 0xFF).toByte() }
        )
        val incomingCanonical = buildCanonicalSample(
            name = "GRANDIA_001",
            comment = "Feena's Ho",
            rawData = ByteArray(3040) { (it % 251).toByte() }
        )

        val existingContainer = SaturnSaveFormatConverter.fromCanonical(
            existingCanonical,
            SaturnSyncFormat.YABASANSHIRO
        )
        val mergedContainer = SaturnSaveFormatConverter.mergeCanonicalIntoYabaSanshiro(
            existingContainer,
            incomingCanonical
        )

        assertEquals(0x800000, mergedContainer.size)
        val names = parseArchiveNames(mergedContainer)
        assertTrue(names.contains("DRACULAX_01"))
        assertTrue(names.contains("GRANDIA_001"))
    }

    @Test
    fun `extract canonical keeps only requested archive from shared yabasanshiro container`() {
        val draculaCanonical = buildCanonicalSample(
            name = "DRACULAX_01",
            comment = "Richter",
            rawData = ByteArray(64) { (it and 0xFF).toByte() }
        )
        val grandiaCanonical = buildCanonicalSample(
            name = "GRANDIA_001",
            comment = "Feena's Ho",
            rawData = ByteArray(3040) { (it % 251).toByte() }
        )

        val sharedContainer = SaturnSaveFormatConverter.mergeCanonicalIntoYabaSanshiro(
            SaturnSaveFormatConverter.fromCanonical(
                draculaCanonical,
                SaturnSyncFormat.YABASANSHIRO
            ),
            grandiaCanonical
        )

        val extracted = SaturnSaveFormatConverter.extractCanonical(
            sharedContainer,
            listOf("GRANDIA_001")
        )

        assertArrayEquals(grandiaCanonical, extracted)
        assertEquals(listOf("GRANDIA_001"), SaturnSaveFormatConverter.archiveNames(extracted))
    }

    private fun buildCanonicalSample(
        name: String,
        comment: String,
        rawData: ByteArray
    ): ByteArray {
        val buffer = ByteArray(0x8000)
        val magic = "BackUpRam Format".toByteArray(Charsets.US_ASCII)
        var offset = 0
        while (offset < 0x40) {
            val chunkSize = minOf(magic.size, 0x40 - offset)
            magic.copyInto(buffer, offset, 0, chunkSize)
            offset += magic.size
        }

        writeIntBE(buffer, 0x80, 0x80000000.toInt())
        name.toByteArray(Charsets.US_ASCII).copyOf(11).copyInto(buffer, 0x84)
        comment.toByteArray(Charset.forName("Shift_JIS")).copyOf(10).copyInto(buffer, 0x90)
        writeIntBE(buffer, 0x9A, 23797305)
        writeIntBE(buffer, 0x9E, rawData.size)

        val blockList = listOf(3, 4)
        writeU16BE(buffer, 0xA2, blockList[0])
        writeU16BE(buffer, 0xA4, blockList[1])
        writeU16BE(buffer, 0xA6, 0)

        var rawOffset = 0
        val inlineSize = minOf(rawData.size, 0x40 - 0x28)
        rawData.copyInto(buffer, 0xA8, 0, inlineSize)
        rawOffset += inlineSize

        for (blockNum in blockList) {
            val blockOffset = blockNum * 0x40
            writeIntBE(buffer, blockOffset, 0)
            val chunkSize = minOf(rawData.size - rawOffset, 0x3C)
            if (chunkSize > 0) {
                rawData.copyInto(buffer, blockOffset + 4, rawOffset, rawOffset + chunkSize)
                rawOffset += chunkSize
            }
        }

        return buffer
    }

    private fun parseArchiveNames(byteExpandedData: ByteArray): List<String> {
        val collapsed = ByteArray(byteExpandedData.size / 2) { idx -> byteExpandedData[idx * 2 + 1] }
        val names = mutableListOf<String>()
        val totalBlocks = collapsed.size / 0x40
        for (blockNum in 2 until totalBlocks) {
            val offset = blockNum * 0x40
            if (readIntBE(collapsed, offset) != 0x80000000.toInt()) continue
            val nameBytes = collapsed.copyOfRange(offset + 0x04, offset + 0x0F)
            val end = nameBytes.indexOf(0).let { if (it >= 0) it else nameBytes.size }
            names += nameBytes.copyOf(end).toString(Charsets.US_ASCII)
        }
        return names
    }

    private fun writeU16BE(target: ByteArray, offset: Int, value: Int) {
        target[offset] = ((value ushr 8) and 0xFF).toByte()
        target[offset + 1] = (value and 0xFF).toByte()
    }

    private fun writeIntBE(target: ByteArray, offset: Int, value: Int) {
        target[offset] = ((value ushr 24) and 0xFF).toByte()
        target[offset + 1] = ((value ushr 16) and 0xFF).toByte()
        target[offset + 2] = ((value ushr 8) and 0xFF).toByte()
        target[offset + 3] = (value and 0xFF).toByte()
    }
}
