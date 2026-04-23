package com.savesync.android.sync

import java.nio.charset.Charset

enum class SaturnSyncFormat(val wireValue: String, val label: String) {
    MEDNAFEN("mednafen", "Beetle / Mednafen (.bkr)"),
    YABAUSE("yabause", "Yabause (byte-expanded)"),
    YABASANSHIRO("yabasanshiro", "YabaSanshiro");

    companion object {
        fun fromWireValue(value: String?): SaturnSyncFormat =
            values().firstOrNull { it.wireValue.equals(value, ignoreCase = true) } ?: MEDNAFEN
    }
}

private data class SaturnSaveFile(
    val name: String,
    val languageCode: Int,
    val comment: String,
    val dateCode: Int,
    val rawData: ByteArray,
)

object SaturnSaveFormatConverter {
    private const val BLOCK_SIZE = 0x40
    private const val INTERNAL_SAVE_SIZE = 0x8000
    private const val YABAUSE_SAVE_SIZE = INTERNAL_SAVE_SIZE * 2
    private const val YABASANSHIRO_COLLAPSED_SIZE = 0x400000
    private const val YABASANSHIRO_SAVE_SIZE = YABASANSHIRO_COLLAPSED_SIZE * 2
    private const val BLOCK_TYPE_ARCHIVE = 0x80000000.toInt()
    private const val BLOCK_TYPE_DATA = 0x00000000
    private const val ARCHIVE_NAME_OFFSET = 0x04
    private const val ARCHIVE_NAME_LENGTH = 11
    private const val ARCHIVE_LANGUAGE_OFFSET = 0x0F
    private const val ARCHIVE_COMMENT_OFFSET = 0x10
    private const val ARCHIVE_COMMENT_LENGTH = 10
    private const val ARCHIVE_DATE_OFFSET = 0x1A
    private const val ARCHIVE_SAVE_SIZE_OFFSET = 0x1E
    private const val ARCHIVE_BLOCK_LIST_OFFSET = 0x22
    private const val ARCHIVE_BLOCK_LIST_END = 0x0000
    private const val DATA_BLOCK_DATA_OFFSET = 0x04
    private val MAGIC = "BackUpRam Format".toByteArray(Charsets.US_ASCII)
    private val SHIFT_JIS: Charset = Charset.forName("Shift_JIS")

    fun toCanonical(bytes: ByteArray): ByteArray {
        val saves = parseAnySaturn(bytes)
            ?: throw IllegalArgumentException("Unsupported Saturn save format (${bytes.size} bytes)")
        return buildNativeSaturn(saves, INTERNAL_SAVE_SIZE)
    }

    fun archiveNames(bytes: ByteArray): List<String> {
        val saves = parseAnySaturn(bytes)
            ?: throw IllegalArgumentException("Unsupported Saturn save format (${bytes.size} bytes)")
        return saves.map { it.name }
    }

    fun extractCanonical(bytes: ByteArray, archiveNames: Collection<String>): ByteArray {
        val requestedNames = archiveNames
            .map { it.trim() }
            .filter { it.isNotEmpty() }
            .toSet()
        require(requestedNames.isNotEmpty()) {
            "At least one Saturn archive name is required"
        }

        val saves = parseAnySaturn(bytes)
            ?: throw IllegalArgumentException("Unsupported Saturn save format (${bytes.size} bytes)")
        val matchingSaves = saves.filter { it.name in requestedNames }
        val foundNames = matchingSaves.mapTo(linkedSetOf()) { it.name }
        val missingNames = requestedNames - foundNames
        require(missingNames.isEmpty()) {
            "Saturn save archives not found: ${missingNames.joinToString(", ")}"
        }

        return buildNativeSaturn(matchingSaves, INTERNAL_SAVE_SIZE)
    }

    fun fromCanonical(canonicalBytes: ByteArray, targetFormat: SaturnSyncFormat): ByteArray {
        val saves = parseNativeSaturn(canonicalBytes)
            ?: throw IllegalArgumentException("Canonical Saturn save is not valid")
        return when (targetFormat) {
            SaturnSyncFormat.MEDNAFEN -> buildNativeSaturn(saves, INTERNAL_SAVE_SIZE)
            SaturnSyncFormat.YABAUSE ->
                byteExpand(buildNativeSaturn(saves, INTERNAL_SAVE_SIZE), 0xFF)
            SaturnSyncFormat.YABASANSHIRO ->
                byteExpand(buildNativeSaturn(saves, YABASANSHIRO_COLLAPSED_SIZE), 0xFF)
        }
    }

    fun mergeCanonicalIntoYabaSanshiro(
        existingBytes: ByteArray?,
        canonicalBytes: ByteArray,
    ): ByteArray {
        val incomingSaves = parseNativeSaturn(canonicalBytes)
            ?: throw IllegalArgumentException("Canonical Saturn save is not valid")

        val merged = LinkedHashMap<String, SaturnSaveFile>()
        existingBytes
            ?.let { byteCollapse(it) }
            ?.let { parseNativeSaturn(it) }
            ?.forEach { save -> merged[save.name] = save }

        incomingSaves.forEach { save ->
            merged[save.name] = save
        }

        return byteExpand(
            buildNativeSaturn(merged.values.toList(), YABASANSHIRO_COLLAPSED_SIZE),
            0xFF
        )
    }

    private fun parseAnySaturn(bytes: ByteArray): List<SaturnSaveFile>? {
        parseNativeSaturn(bytes)?.let { return it }
        val collapsed = byteCollapse(bytes) ?: return null
        return parseNativeSaturn(collapsed)
    }

    private fun parseNativeSaturn(data: ByteArray): List<SaturnSaveFile>? {
        if (data.size < BLOCK_SIZE * 2 || data.size % BLOCK_SIZE != 0) return null
        if (!hasValidHeader(data)) return null

        val totalBlocks = data.size / BLOCK_SIZE
        val saves = mutableListOf<SaturnSaveFile>()

        for (blockNum in 2 until totalBlocks) {
            val blockOffset = blockNum * BLOCK_SIZE
            if (readIntBE(data, blockOffset) != BLOCK_TYPE_ARCHIVE) continue

            val name = readCString(data, blockOffset + ARCHIVE_NAME_OFFSET, ARCHIVE_NAME_LENGTH, Charsets.US_ASCII)
            val languageCode = data[blockOffset + ARCHIVE_LANGUAGE_OFFSET].toInt() and 0xFF
            val comment = readCString(data, blockOffset + ARCHIVE_COMMENT_OFFSET, ARCHIVE_COMMENT_LENGTH, SHIFT_JIS)
            val dateCode = readIntBE(data, blockOffset + ARCHIVE_DATE_OFFSET)
            val saveSize = readIntBE(data, blockOffset + ARCHIVE_SAVE_SIZE_OFFSET)
            if (saveSize < 0) return null

            val blockList = mutableListOf<Int>()
            var blockListReadIndex = 0
            var currentBlockNum = blockNum
            var blockListOffset = ARCHIVE_BLOCK_LIST_OFFSET

            while (true) {
                val absoluteOffset = currentBlockNum * BLOCK_SIZE + blockListOffset
                if (absoluteOffset + 2 > data.size) return null

                val entry = readU16BE(data, absoluteOffset)
                if (entry == ARCHIVE_BLOCK_LIST_END) break
                if (entry >= totalBlocks) return null

                blockList.add(entry)
                blockListOffset += 2

                if (blockListOffset >= BLOCK_SIZE) {
                    val nextBlock = blockList[blockListReadIndex]
                    blockListReadIndex += 1
                    if (readIntBE(data, nextBlock * BLOCK_SIZE) != BLOCK_TYPE_DATA) return null
                    currentBlockNum = nextBlock
                    blockListOffset = DATA_BLOCK_DATA_OFFSET
                }
            }

            val dataStart = currentBlockNum * BLOCK_SIZE + blockListOffset + 2
            val raw = ByteArray(saveSize)
            var written = 0

            val initialAvailable = ((currentBlockNum + 1) * BLOCK_SIZE - dataStart).coerceAtLeast(0)
            if (initialAvailable > 0) {
                val chunk = minOf(saveSize, initialAvailable)
                data.copyInto(raw, 0, dataStart, dataStart + chunk)
                written += chunk
            }

            for (db in blockList.drop(blockListReadIndex)) {
                if (written >= saveSize) break
                val dbOffset = db * BLOCK_SIZE
                if (readIntBE(data, dbOffset) != BLOCK_TYPE_DATA) return null
                val chunk = minOf(saveSize - written, BLOCK_SIZE - DATA_BLOCK_DATA_OFFSET)
                data.copyInto(
                    raw,
                    written,
                    dbOffset + DATA_BLOCK_DATA_OFFSET,
                    dbOffset + DATA_BLOCK_DATA_OFFSET + chunk
                )
                written += chunk
            }

            if (written < saveSize) return null

            saves += SaturnSaveFile(
                name = name,
                languageCode = languageCode,
                comment = comment,
                dateCode = dateCode,
                rawData = raw,
            )
        }

        return saves
    }

    private fun buildNativeSaturn(saves: List<SaturnSaveFile>, fileSize: Int): ByteArray {
        require(fileSize >= INTERNAL_SAVE_SIZE && fileSize % BLOCK_SIZE == 0) {
            "Invalid Saturn file size: $fileSize"
        }

        val totalBlocks = fileSize / BLOCK_SIZE
        val buffer = ByteArray(fileSize)
        fillReservedBlocks(buffer)

        var currentBlock = 2
        for (save in saves) {
            val saveBlocks = buildBlocksForSave(save, currentBlock)
            val endBlock = currentBlock + saveBlocks.size
            require(endBlock <= totalBlocks) {
                "Not enough space to hold Saturn save ${save.name}"
            }
            saveBlocks.forEachIndexed { index, block ->
                val destOffset = (currentBlock + index) * BLOCK_SIZE
                block.copyInto(buffer, destOffset)
            }
            currentBlock = endBlock
        }

        return buffer
    }

    private fun buildBlocksForSave(save: SaturnSaveFile, startingBlock: Int): List<ByteArray> {
        val archiveBlock = ByteArray(BLOCK_SIZE)
        writeIntBE(archiveBlock, 0x00, BLOCK_TYPE_ARCHIVE)
        writeCString(archiveBlock, ARCHIVE_NAME_OFFSET, save.name, ARCHIVE_NAME_LENGTH, Charsets.US_ASCII)
        archiveBlock[ARCHIVE_LANGUAGE_OFFSET] = (save.languageCode and 0xFF).toByte()
        writeCString(archiveBlock, ARCHIVE_COMMENT_OFFSET, save.comment, ARCHIVE_COMMENT_LENGTH, SHIFT_JIS)
        writeIntBE(archiveBlock, ARCHIVE_DATE_OFFSET, save.dateCode)
        writeIntBE(archiveBlock, ARCHIVE_SAVE_SIZE_OFFSET, save.rawData.size)

        val numDataBlocks = numberOfDataBlocksRequired(save.rawData.size)
        val blocks = mutableListOf<ByteArray>()

        var currentDataBlockIndex = 0
        var currentBlock = archiveBlock
        var currentOffset = ARCHIVE_BLOCK_LIST_OFFSET

        while (currentDataBlockIndex < numDataBlocks) {
            writeU16BE(currentBlock, currentOffset, startingBlock + currentDataBlockIndex + 1)
            currentOffset += 2
            if (currentOffset >= BLOCK_SIZE) {
                blocks += currentBlock
                currentBlock = ByteArray(BLOCK_SIZE)
                writeIntBE(currentBlock, 0x00, BLOCK_TYPE_DATA)
                currentOffset = DATA_BLOCK_DATA_OFFSET
            }
            currentDataBlockIndex += 1
        }

        writeU16BE(currentBlock, currentOffset, ARCHIVE_BLOCK_LIST_END)
        currentOffset += 2

        var rawOffset = 0
        while (rawOffset < save.rawData.size) {
            if (currentOffset >= BLOCK_SIZE) {
                blocks += currentBlock
                currentBlock = ByteArray(BLOCK_SIZE)
                writeIntBE(currentBlock, 0x00, BLOCK_TYPE_DATA)
                currentOffset = DATA_BLOCK_DATA_OFFSET
            }

            val chunkSize = minOf(save.rawData.size - rawOffset, BLOCK_SIZE - currentOffset)
            save.rawData.copyInto(currentBlock, currentOffset, rawOffset, rawOffset + chunkSize)
            rawOffset += chunkSize
            currentOffset += chunkSize
        }

        blocks += currentBlock
        return blocks
    }

    private fun numberOfDataBlocksRequired(rawSize: Int): Int {
        val archiveCapacity = BLOCK_SIZE - ARCHIVE_BLOCK_LIST_OFFSET
        val dataCapacity = BLOCK_SIZE - DATA_BLOCK_DATA_OFFSET

        var approxBlocks = 0
        while (true) {
            val blockListBytes = (approxBlocks + 1) * 2
            val bytesInDataBlocks = maxOf(rawSize + blockListBytes - archiveCapacity, 0)
            val newApprox = (bytesInDataBlocks + dataCapacity - 1) / dataCapacity
            if (newApprox == approxBlocks) return approxBlocks
            approxBlocks = newApprox
        }
    }

    private fun hasValidHeader(data: ByteArray): Boolean {
        val expectedBlock0 = ByteArray(BLOCK_SIZE)
        var offset = 0
        while (offset < BLOCK_SIZE) {
            val chunkSize = minOf(MAGIC.size, BLOCK_SIZE - offset)
            MAGIC.copyInto(expectedBlock0, offset, 0, chunkSize)
            offset += MAGIC.size
        }
        if (!data.copyOfRange(0, BLOCK_SIZE).contentEquals(expectedBlock0)) return false
        return data.copyOfRange(BLOCK_SIZE, BLOCK_SIZE * 2).all { it == 0.toByte() }
    }

    private fun fillReservedBlocks(buffer: ByteArray) {
        var offset = 0
        while (offset < BLOCK_SIZE) {
            val chunkSize = minOf(MAGIC.size, BLOCK_SIZE - offset)
            MAGIC.copyInto(buffer, offset, 0, chunkSize)
            offset += MAGIC.size
        }
    }

    private fun byteCollapse(bytes: ByteArray): ByteArray? {
        if (bytes.size % 2 != 0) return null
        if (bytes.size != YABAUSE_SAVE_SIZE && bytes.size != YABASANSHIRO_SAVE_SIZE) return null

        var padding: Int? = null
        for (index in 0 until bytes.size step 2) {
            val value = bytes[index].toInt() and 0xFF
            if (padding == null) padding = value
            if (padding != value) return null
        }

        return ByteArray(bytes.size / 2) { index -> bytes[index * 2 + 1] }
    }

    private fun byteExpand(bytes: ByteArray, padding: Int): ByteArray {
        val expanded = ByteArray(bytes.size * 2)
        for (index in bytes.indices) {
            expanded[index * 2] = padding.toByte()
            expanded[index * 2 + 1] = bytes[index]
        }
        return expanded
    }

    private fun readCString(data: ByteArray, offset: Int, maxLength: Int, charset: Charset): String {
        val available = data.copyOfRange(offset, offset + maxLength)
        val end = available.indexOf(0)
        val raw = if (end >= 0) available.copyOf(end) else available
        return raw.toString(charset)
    }

    private fun writeCString(
        target: ByteArray,
        offset: Int,
        value: String,
        maxLength: Int,
        charset: Charset,
    ) {
        val encoded = value.toByteArray(charset).copyOf(maxLength)
        encoded.copyInto(target, offset)
    }

    private fun readU16BE(data: ByteArray, offset: Int): Int =
        ((data[offset].toInt() and 0xFF) shl 8) or (data[offset + 1].toInt() and 0xFF)

    private fun writeU16BE(target: ByteArray, offset: Int, value: Int) {
        target[offset] = ((value ushr 8) and 0xFF).toByte()
        target[offset + 1] = (value and 0xFF).toByte()
    }

    private fun readIntBE(data: ByteArray, offset: Int): Int =
        ((data[offset].toInt() and 0xFF) shl 24) or
            ((data[offset + 1].toInt() and 0xFF) shl 16) or
            ((data[offset + 2].toInt() and 0xFF) shl 8) or
            (data[offset + 3].toInt() and 0xFF)

    private fun writeIntBE(target: ByteArray, offset: Int, value: Int) {
        target[offset] = ((value ushr 24) and 0xFF).toByte()
        target[offset + 1] = ((value ushr 16) and 0xFF).toByte()
        target[offset + 2] = ((value ushr 8) and 0xFF).toByte()
        target[offset + 3] = (value and 0xFF).toByte()
    }
}
