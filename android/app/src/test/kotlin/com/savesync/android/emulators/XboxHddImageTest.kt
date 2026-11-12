package com.savesync.android.emulators

import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import java.io.File
import java.io.IOException
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.nio.channels.FileChannel
import java.nio.file.StandardOpenOption
import java.util.TreeMap
import java.util.zip.Deflater

/**
 * Mirrors ps5/tests/test_xbox_hdd.c against the disk xbox_hdd_fixture.py
 * builds (ported below): a sparse qcow2 Xbox disk with a FATX E: whose data
 * clusters are stored zlib-compressed, as `qemu-img convert -c` writes them.
 * Also the same disk as a raw (sparse) image, the way X1 BOX may keep it.
 */
class XboxHddImageTest {

    @get:Rule
    val tmp = TemporaryFolder()

    // ------------------------------------------------------------ the fixture

    private object Fx {
        const val QCOW_CLUSTER_BITS = 16
        const val QCOW_CLUSTER = 1 shl QCOW_CLUSTER_BITS
        const val DISK_SIZE = 8L shl 30
        const val E_OFFSET = 0xABE80000L
        const val E_SIZE = 0x1312D6000L
        const val SPC = 32
        const val FATX_CLUSTER = SPC * 512
        const val CLUSTERS = E_SIZE / FATX_CLUSTER
        val FAT_ENTRY = if (CLUSTERS < 0xFFF0) 2 else 4
        val FAT_BYTES = ((CLUSTERS * FAT_ENTRY + 4095) / 4096 * 4096).toInt()
        val DATA = E_OFFSET + 0x1000 + FAT_BYTES
        const val END = 0xFFFFFFFFL

        val BIG = ByteArray(FATX_CLUSTER + 5000) { (it * 7).toByte() } // two clusters
        val FILES = mapOf(
            "SaveMeta.xbx" to "Title=Fixture\r\n".toByteArray(),
            "AB12CD34EF56\\big.dat" to BIG,
            "AB12CD34EF56\\SaveMeta.xbx" to "Name=Slot 1\r\n".toByteArray(),
            "empty.bin" to ByteArray(0),
        )
    }

    private fun dirent(name: String, attr: Int, first: Long, size: Int): ByteArray {
        val b = ByteBuffer.allocate(64).order(ByteOrder.LITTLE_ENDIAN)
        val raw = name.toByteArray(Charsets.ISO_8859_1)
        b.put(raw.size.toByte()).put(attr.toByte()).put(raw)
        repeat(42 - raw.size) { b.put(0xFF.toByte()) }
        b.putInt(first.toInt()).putInt(size)
        return b.array()
    }

    private fun deleted(name: String): ByteArray = dirent(name, 0, 0, 0).also { it[0] = 0xE5.toByte() }

    private fun bytes(n: Int, v: Int) = ByteArray(n) { v.toByte() }

    private fun cat(vararg parts: ByteArray): ByteArray {
        val out = java.io.ByteArrayOutputStream()
        parts.forEach { out.write(it) }
        return out.toByteArray()
    }

    /** guest offset -> bytes */
    private fun buildGuest(): Map<Long, ByteArray> {
        val fat = HashMap<Long, Long>()
        val writes = TreeMap<Long, ByteArray>()
        fun putCluster(c: Long, data: ByteArray) {
            writes[Fx.DATA + (c - 1) * Fx.FATX_CLUSTER] = data.copyOf(Fx.FATX_CLUSTER)
        }
        fun chain(vararg cs: Long) {
            for (i in 0 until cs.size - 1) fat[cs[i]] = cs[i + 1]
            fat[cs.last()] = Fx.END
        }
        val f = Fx.FILES
        // 1 root, 2 UDATA, 3 the game, 4 TDSV0000 (not a game id), 5 save dir,
        // 6 SaveMeta.xbx, 7+9 big.dat (not contiguous), 8 the slot's SaveMeta,
        // 10 another title
        putCluster(1, cat(dirent("UDATA", 0x10, 2, 0), dirent("TDATA", 0x10, 10, 0), bytes(64, 0xFF)))
        putCluster(2, cat(deleted("4D530001"), dirent("4d53006e", 0x10, 3, 0),
            dirent("TDSV0000", 0x10, 4, 0), bytes(64, 0)))
        putCluster(3, cat(dirent("SaveMeta.xbx", 0, 6, f.getValue("SaveMeta.xbx").size),
            dirent("AB12CD34EF56", 0x10, 5, 0), dirent("empty.bin", 0, 0, 0), bytes(64, 0xFF)))
        putCluster(4, bytes(64, 0xFF))
        putCluster(5, cat(dirent("big.dat", 0, 7, Fx.BIG.size),
            dirent("SaveMeta.xbx", 0, 8, f.getValue("AB12CD34EF56\\SaveMeta.xbx").size), bytes(64, 0xFF)))
        putCluster(6, f.getValue("SaveMeta.xbx"))
        putCluster(7, Fx.BIG.copyOfRange(0, Fx.FATX_CLUSTER))
        putCluster(9, Fx.BIG.copyOfRange(Fx.FATX_CLUSTER, Fx.BIG.size))
        putCluster(8, f.getValue("AB12CD34EF56\\SaveMeta.xbx"))
        putCluster(10, bytes(64, 0xFF))
        for (c in longArrayOf(1, 2, 3, 4, 5, 6, 8, 10)) chain(c)
        chain(7, 9)
        val fatBytes = ByteBuffer.allocate(Fx.FAT_BYTES).order(ByteOrder.LITTLE_ENDIAN)
        for ((c, n) in fat) {
            if (Fx.FAT_ENTRY == 2) fatBytes.putShort((c * 2).toInt(), n.toShort())
            else fatBytes.putInt((c * 4).toInt(), n.toInt())
        }
        val sb = ByteBuffer.allocate(4096).order(ByteOrder.LITTLE_ENDIAN)
        sb.put("FATX".toByteArray()).putInt(0x1234).putInt(Fx.SPC).putInt(1)
        writes[Fx.E_OFFSET] = sb.array()
        writes[Fx.E_OFFSET + 0x1000] = fatBytes.array()
        return writes
    }

    /** A growable byte buffer standing in for the python bytearray. */
    private class Out {
        var buf = ByteArray(1 shl 20)
        var size = 0
        fun ensure(n: Int) {
            if (n > buf.size) buf = buf.copyOf(maxOf(n, buf.size * 2))
            if (n > size) size = n
        }
        fun extend(b: ByteArray) {
            val at = size
            ensure(size + b.size)
            System.arraycopy(b, 0, buf, at, b.size)
        }
        fun padTo(m: Int) {
            while (size % m != 0) ensure(size + 1)
        }
        fun alloc(data: ByteArray): Int {
            padTo(Fx.QCOW_CLUSTER)
            val at = size
            extend(data)
            return at
        }
        fun putLong(at: Int, v: Long) = ByteBuffer.wrap(buf).putLong(at, v)
        fun bytes() = buf.copyOf(size)
    }

    private fun makeQcow(path: File) {
        val guest = TreeMap<Long, ByteArray>() // qcow2 cluster index -> data
        for ((off0, data0) in buildGuest()) {
            var off = off0
            var pos = 0
            while (pos < data0.size) {
                val vc = off / Fx.QCOW_CLUSTER
                val inner = (off % Fx.QCOW_CLUSTER).toInt()
                val k = minOf(data0.size - pos, Fx.QCOW_CLUSTER - inner)
                val buf = guest.getOrPut(vc) { ByteArray(Fx.QCOW_CLUSTER) }
                System.arraycopy(data0, pos, buf, inner, k)
                off += k
                pos += k
            }
        }
        val perL2 = Fx.QCOW_CLUSTER / 8
        val l1Size = ((Fx.DISK_SIZE / Fx.QCOW_CLUSTER + perL2 - 1) / perL2).toInt()
        val out = Out()
        out.ensure(Fx.QCOW_CLUSTER * 2) // header, L1
        val l2Tables = TreeMap<Int, Pair<Int, LongArray>>()
        for (i1 in guest.keys.map { (it / perL2).toInt() }.toSortedSet()) {
            l2Tables[i1] = out.alloc(ByteArray(Fx.QCOW_CLUSTER)) to LongArray(perL2)
        }
        val x = 62 - (Fx.QCOW_CLUSTER_BITS - 8)
        for ((vc, data) in guest) {
            val i1 = (vc / perL2).toInt()
            val i2 = (vc % perL2).toInt()
            if (vc * Fx.QCOW_CLUSTER >= Fx.DATA) {
                val d = Deflater(9, true)
                d.setInput(data)
                d.finish()
                val tmpBuf = ByteArray(Fx.QCOW_CLUSTER * 2)
                var n = 0
                while (!d.finished()) n += d.deflate(tmpBuf, n, tmpBuf.size - n)
                d.end()
                val packed = tmpBuf.copyOf(n)
                val at = out.size + 3 // not sector-aligned, as qemu packs them
                out.extend(ByteArray(3))
                out.extend(packed)
                val sectors = ((at and 511) + packed.size + 511) / 512
                l2Tables.getValue(i1).second[i2] = (1L shl 62) or ((sectors - 1).toLong() shl x) or at.toLong()
            } else {
                l2Tables.getValue(i1).second[i2] = out.alloc(data).toLong() or (1L shl 63) // COPIED
            }
        }
        out.padTo(512)
        val l1 = LongArray(l1Size)
        for ((i1, t) in l2Tables) {
            for (i in 0 until perL2) out.putLong(t.first + i * 8, t.second[i])
            l1[i1] = t.first.toLong() or (1L shl 63)
        }
        for (i in 0 until l1Size) out.putLong(Fx.QCOW_CLUSTER + i * 8, l1[i])
        val rt = out.alloc(ByteArray(Fx.QCOW_CLUSTER))
        val block = out.alloc(ByteArray(Fx.QCOW_CLUSTER))
        out.putLong(rt, block.toLong())
        val h = ByteBuffer.wrap(out.buf).order(ByteOrder.BIG_ENDIAN)
        h.position(0)
        h.put(byteArrayOf('Q'.code.toByte(), 'F'.code.toByte(), 'I'.code.toByte(), 0xFB.toByte()))
        h.putInt(3).putLong(0).putInt(0).putInt(Fx.QCOW_CLUSTER_BITS).putLong(Fx.DISK_SIZE)
        h.putInt(0).putInt(l1Size).putLong(Fx.QCOW_CLUSTER.toLong()).putLong(rt.toLong())
        h.putInt(1).putInt(0).putLong(0).putLong(0).putLong(0).putLong(0).putInt(4).putInt(104)
        val img = out.bytes()
        for ((hc, n) in references(img)) {
            ByteBuffer.wrap(img).putShort(block + (hc * 2).toInt(), n.toShort())
        }
        path.writeBytes(img)
    }

    /** The same disk as a sparse raw image of the full size. */
    private fun makeRaw(path: File) {
        FileChannel.open(
            path.toPath(), StandardOpenOption.CREATE_NEW, StandardOpenOption.WRITE, StandardOpenOption.SPARSE,
        ).use { ch ->
            for ((off, data) in buildGuest()) ch.write(ByteBuffer.wrap(data), off)
            ch.write(ByteBuffer.wrap(ByteArray(1)), Fx.DISK_SIZE - 1)
        }
    }

    // ------------------------------------------------------------ check-image

    private fun u64(img: ByteArray, at: Long) = ByteBuffer.wrap(img).getLong(at.toInt())
    private fun u32(img: ByteArray, at: Int) = ByteBuffer.wrap(img).getInt(at).toLong() and 0xFFFFFFFFL

    /** host cluster -> how many references the metadata makes to it */
    private fun references(img: ByteArray): Map<Long, Int> {
        val refs = HashMap<Long, Int>()
        fun ref(hc: Long) { refs[hc] = (refs[hc] ?: 0) + 1 }
        val cbits = u32(img, 20).toInt()
        val cs = 1L shl cbits
        val l1Size = u32(img, 36)
        val l1Off = u64(img, 40)
        val rtOff = u64(img, 48)
        val rtClusters = u32(img, 56)
        val mask = 0x00FFFFFFFFFFFE00L
        ref(0)
        for (c in 0 until (l1Size * 8 + cs - 1) / cs) ref(l1Off / cs + c)
        for (c in 0 until rtClusters) ref(rtOff / cs + c)
        for (i in 0 until rtClusters * cs / 8) {
            val b = u64(img, rtOff + i * 8) and mask
            if (b != 0L) ref(b / cs)
        }
        val x = 62 - (cbits - 8)
        for (i1 in 0 until l1Size) {
            val l2 = u64(img, l1Off + i1 * 8) and mask
            if (l2 == 0L) continue
            ref(l2 / cs)
            for (i2 in 0 until cs / 8) {
                val e = u64(img, l2 + i2 * 8)
                if ((e and (1L shl 62)) != 0L) {
                    val off = e and ((1L shl x) - 1)
                    val sectors = ((e ushr x) and ((1L shl (cbits - 8)) - 1)) + 1
                    val start = off and 511L.inv()
                    for (hc in start / cs..(start + sectors * 512 - 1) / cs) ref(hc)
                } else if ((e and mask) != 0L) {
                    ref((e and mask) / cs)
                }
            }
        }
        return refs
    }

    /** What matters of qemu-img check: every cluster's refcount covers its
     *  references (a leftover count is a harmless leak) and nothing points
     *  past the end of the file. */
    private fun checkImage(path: File) {
        val img = path.readBytes()
        val cs = 1L shl u32(img, 20).toInt()
        val rtOff = u64(img, 48)
        val rtClusters = u32(img, 56)
        val perBlock = cs / 2
        val stored = HashMap<Long, Int>()
        for (ri in 0 until rtClusters * cs / 8) {
            val b = u64(img, rtOff + ri * 8) and 0x00FFFFFFFFFFFE00L
            if (b == 0L) continue
            for (k in 0 until perBlock) {
                val n = ByteBuffer.wrap(img).getShort((b + k * 2).toInt()).toInt() and 0xFFFF
                if (n != 0) stored[ri * perBlock + k] = n
            }
        }
        val refs = references(img)
        val bad = refs.filter { (hc, n) -> (stored[hc] ?: 0) < n }
        assertTrue("refcount errors (cluster -> references): $bad", bad.isEmpty())
        assertTrue("a reference past the end of the file", (refs.keys.maxOrNull()!! + 1) * cs <= img.size + cs - 1)
    }

    // ------------------------------------------------------------ tests

    private fun makeFile(size: Int, seed: Int) = ByteArray(size) { (it * 31 + seed).toByte() }

    private val newTitle = listOf(
        "SaveMeta.xbx" to makeFile(40, 1),
        "B00000000001\\save.dat" to makeFile(70000, 2),
        "B00000000001/sub\\deep.bin" to makeFile(9, 3),
        "zero.bin" to makeFile(0, 4),
    )
    private val replacement = listOf("only.sav" to makeFile(20000, 5))

    private fun assertSameTitle(image: File, id: String, want: List<Pair<String, ByteArray>>) {
        val got = XboxHddImage.open(image).use { it.readTitle(id) }
        val sorted = want.map { (p, b) -> p.replace('/', '\\') to b }.sortedBy { it.first }
        assertEquals(sorted.map { it.first }, got.map { it.first })
        for (i in sorted.indices) assertArrayEquals(sorted[i].first, sorted[i].second, got[i].second)
    }

    private fun readsFixture(image: File) {
        XboxHddImage.open(image).use { hdd ->
            assertEquals(listOf("4D53006E"), hdd.titles())
            val tree = hdd.readTitle("4D53006E", 1L shl 20)
            val want = Fx.FILES.toSortedMap()
            assertEquals(want.keys.toList(), tree.map { it.first })
            for ((p, b) in tree) assertArrayEquals(p, want.getValue(p), b)
            // any case
            assertEquals(tree.map { it.first }, hdd.readTitle("4d53006e").map { it.first })
            // too big for the cap: refused, not truncated
            try {
                hdd.readTitle("4D53006E", 100)
                fail("a save over the cap was read")
            } catch (_: IOException) {
            }
            try {
                hdd.readTitle("4D530001")
                fail("a deleted save was read")
            } catch (_: IOException) {
            }
        }
    }

    /** A new title (UDATA grows a slot), then the old one replaced, then again. */
    private fun writes(image: File) {
        XboxHddImage.open(image).use { ro ->
            try {
                ro.writeTitle("4D530099", newTitle)
                fail("wrote through a read-only open")
            } catch (_: IOException) {
            }
        }
        XboxHddImage.openWritable(image).use { hdd ->
            hdd.writeTitle("4D530099", newTitle)
            hdd.writeTitle("4D53006E", replacement)
        }
        // the new one takes the deleted entry's slot, ahead of the old one
        assertEquals(listOf("4D530099", "4D53006E"), XboxHddImage.open(image).use { it.titles() })
        assertSameTitle(image, "4D530099", newTitle)
        assertSameTitle(image, "4D53006E", replacement)
        // again: the second write frees the first's clusters for reuse
        XboxHddImage.openWritable(image).use { it.writeTitle("4D530099", newTitle) }
        assertSameTitle(image, "4D530099", newTitle)
        assertSameTitle(image, "4D53006E", replacement)
    }

    @Test
    fun `reads and writes a qcow2 image, keeping refcounts consistent`() {
        val image = tmp.newFile("xbox_hdd.qcow2")
        makeQcow(image)
        checkImage(image)
        readsFixture(image)
        writes(image)
        checkImage(image)
    }

    @Test
    fun `reads and writes a raw image`() {
        val image = File(tmp.root, "hdd.img")
        makeRaw(image)
        readsFixture(image)
        writes(image)
        assertEquals(Fx.DISK_SIZE, image.length())
    }

    @Test
    fun `refuses to write a dirty or snapshotted qcow2`() {
        val image = tmp.newFile("dirty.qcow2")
        makeQcow(image)
        val clean = image.readBytes()
        val dirty = clean.copyOf().also { it[79] = 1 } // incompatible features: dirty bit
        image.writeBytes(dirty)
        XboxHddImage.open(image).close() // reading is fine
        try {
            XboxHddImage.openWritable(image).close()
            fail("opened a dirty image for writing")
        } catch (_: IOException) {
        }
        val snap = clean.copyOf().also { it[63] = 1 } // nb_snapshots = 1
        image.writeBytes(snap)
        try {
            XboxHddImage.openWritable(image).close()
            fail("opened an image with snapshots for writing")
        } catch (_: IOException) {
        }
    }

    @Test
    fun `refuses what is not an Xbox disk`() {
        val junk = tmp.newFile("junk.img")
        junk.writeBytes(ByteArray(4096))
        try {
            XboxHddImage.open(junk).close()
            fail("opened a tiny file")
        } catch (_: IOException) {
        }
    }

    @Test
    fun `backup copies through a tmp file`() {
        val image = tmp.newFile("xbox_hdd.qcow2")
        makeQcow(image)
        val backup = File(tmp.root, "xbox_hdd.qcow2.bak")
        XboxHddImage.backup(image, backup)
        assertArrayEquals(image.readBytes(), backup.readBytes())
        assertFalse(File(backup.path + ".tmp").exists())
    }

    @Test
    fun `game title ids`() {
        assertTrue(XboxHddImage.isGameTitleId("4D53006E"))
        assertTrue(XboxHddImage.isGameTitleId("4d53006e"))
        assertFalse(XboxHddImage.isGameTitleId("TDSV0000"))
        assertFalse(XboxHddImage.isGameTitleId("FFFE0000")) // dashboard, not a letter pair
        assertFalse(XboxHddImage.isGameTitleId("4D53006"))
        assertFalse(XboxHddImage.isGameTitleId("4D53006E0"))
    }
}
