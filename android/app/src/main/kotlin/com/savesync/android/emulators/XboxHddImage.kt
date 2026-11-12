/*
 * GameSync Android client - Xbox saves inside an emulator's virtual hard disk.
 * SPDX-License-Identifier: GPL-3.0-or-later
 *
 * Kotlin port of the PS5 client's ps5/src/xbox_hdd.c (keep the two in step).
 *
 * xemu-based emulators (XPSemu, X1 BOX) keep the Xbox hard disk as a qcow2
 * image, or as a raw image of the retail disk; game saves are in its FATX E:
 * partition under E:\UDATA\<title id>, as on a real console. Writing a save
 * (writeTitle) puts it in free FATX clusters and switches the UDATA entry
 * over last, so a failure leaves the old save; in a qcow2 image it overwrites
 * clusters the image owns alone and appends the rest, with refcounts, the
 * way qemu would. The emulator must not have the image open meanwhile - its
 * own cached tables would undo the change.
 *
 * Based on the qcow2 format description in QEMU (docs/interop/qcow2.txt,
 * GPL-2.0-or-later documentation; no code copied) and the FATX layout as
 * documented by the Xbox-Linux project and xboxdevwiki.net
 * (https://xboxdevwiki.net/FATX).
 *
 * Pure JVM (no Android APIs) so it unit-tests on the host.
 */
package com.savesync.android.emulators

import java.io.Closeable
import java.io.File
import java.io.FileInputStream
import java.io.FileOutputStream
import java.io.IOException
import java.io.RandomAccessFile
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.nio.file.Files
import java.nio.file.StandardCopyOption
import java.time.LocalDateTime
import java.util.zip.DataFormatException
import java.util.zip.Inflater

class XboxHddImage private constructor(
    private val raf: RandomAccessFile,
    private val writable: Boolean,
) : Closeable {

    // ------------------------------------------------------------ the disk
    private var qcow = false
    private var diskSize = 0L

    // qcow2
    private var clusterBits = 0
    private var clusterSize = 0
    private var l1 = LongArray(0)
    private var l1Off = 0L
    private var l2 = LongArray(0)
    private var l2At = 0L // image offset of the cached L2 table, 0 = none
    private var cluster = ByteArray(0) // the last guest cluster read
    private var clusterAt = 0L // its guest index + 1, 0 = none
    private var packed = ByteArray(0) // a compressed cluster as stored

    // writing a qcow2
    private var fileEnd = 0L // where the next host cluster goes
    private var rtOff = 0L // refcount table
    private var rt = LongArray(0)
    private var scratch = ByteArray(0)

    // FAT changes
    private var fatLo = Long.MAX_VALUE
    private var fatHi = 0L
    private var nextFree = 1L

    // FATX E:
    private var fatEntry = 4
    private var clusters = 0L
    private var clusterBytes = 0
    private var root = 0L
    private var data = 0L // guest offset of cluster 1
    private var fat = ByteArray(0)

    private class Dirent(val name: String, val attr: Int, val first: Long, val size: Long)

    private fun mount() {
        val hdr = ByteArray(112)
        val got = readPartial(0, hdr, hdr.size)
        if (got >= 4 && be32(hdr, 0) == QCOW_MAGIC) {
            openQcow(hdr, got)
        } else {
            // a raw image of the retail disk
            qcow = false
            diskSize = raf.length()
            if (diskSize < FATX_E_OFFSET + FATX_E_SIZE) {
                throw IOException("not a qcow2 or raw Xbox disk image")
            }
        }

        val sb = ByteArray(16)
        if (diskSize < FATX_E_OFFSET + FATX_E_SIZE ||
            !runCatching { diskRead(FATX_E_OFFSET, sb, 0, sb.size) }.isSuccess ||
            sb[0] != 'F'.code.toByte() || sb[1] != 'A'.code.toByte() ||
            sb[2] != 'T'.code.toByte() || sb[3] != 'X'.code.toByte()
        ) {
            throw IOException("no FATX E: partition on the disk")
        }
        val spc = le32(sb, 8)
        if (spc == 0L || spc > 1024 || (spc and (spc - 1)) != 0L) {
            throw IOException("bad FATX cluster size")
        }
        clusterBytes = (spc * 512).toInt()
        root = le32(sb, 12)
        clusters = FATX_E_SIZE / clusterBytes
        fatEntry = if (clusters < 0xFFF0) 2 else 4
        val fatBytes = (clusters * fatEntry + 4095) and 4095L.inv()
        data = FATX_E_OFFSET + FATX_FAT_OFFSET + fatBytes
        fat = ByteArray(fatBytes.toInt())
        try {
            diskRead(FATX_E_OFFSET + FATX_FAT_OFFSET, fat, 0, fat.size)
        } catch (e: IOException) {
            throw IOException("can't read the FATX table", e)
        }
    }

    private fun openQcow(hdr: ByteArray, got: Int) {
        if (got < 72) throw IOException("not a qcow2 disk image")
        qcow = true
        val version = be32(hdr, 4)
        if (version != 2L && version != 3L) throw IOException("unsupported qcow2 version")
        if (be64(hdr, 8) != 0L) throw IOException("qcow2 backing files aren't supported")
        if (be32(hdr, 32) != 0L) throw IOException("encrypted qcow2 images aren't supported")
        // v3 headers past 104 bytes name the compression; only zlib is read
        if (version == 3L && be32(hdr, 100) > 104 && hdr[104].toInt() != 0) {
            throw IOException("zstd-compressed qcow2 images aren't supported")
        }
        val bits = be32(hdr, 20)
        if (bits < 9 || bits > 21) throw IOException("bad qcow2 cluster size")
        clusterBits = bits.toInt()
        clusterSize = 1 shl clusterBits
        diskSize = be64(hdr, 24)
        val l1Size = be32(hdr, 36)
        l1Off = be64(hdr, 40)
        if (writable) {
            // only what qemu-img create makes and xemu leaves: no snapshots
            // (every cluster owned once), 16-bit refcounts, clean shutdown
            if (be32(hdr, 60) != 0L) throw IOException("the disk image has snapshots: not writing it")
            if (version == 3L && (be32(hdr, 96) != 4L || be64(hdr, 72) != 0L)) {
                throw IOException("the disk image wasn't closed cleanly: start and quit the emulator once")
            }
            rtOff = be64(hdr, 48)
            val rtBytes = be32(hdr, 56) shl clusterBits
            if (rtOff == 0L || rtBytes == 0L || rtBytes > (64L shl 20)) {
                throw IOException("bad qcow2 refcount table")
            }
            rt = LongArray((rtBytes / 8).toInt())
            scratch = ByteArray(clusterSize)
            try {
                readLongs(rtOff, rt)
            } catch (e: IOException) {
                throw IOException("can't read the qcow2 refcount table", e)
            }
            val end = raf.length()
            fileEnd = (end + clusterSize - 1) and (clusterSize - 1).toLong().inv()
        }
        if (l1Size == 0L || l1Size > (1L shl 24)) throw IOException("bad qcow2 L1 table")
        l1 = LongArray(l1Size.toInt())
        l2 = LongArray(clusterSize / 8)
        cluster = ByteArray(clusterSize)
        packed = ByteArray(clusterSize * 2)
        try {
            readLongs(l1Off, l1)
        } catch (e: IOException) {
            throw IOException("can't read the qcow2 L1 table", e)
        }
    }

    // ------------------------------------------------------------ file I/O

    private fun readFully(off: Long, buf: ByteArray, pos: Int, n: Int) {
        raf.seek(off)
        raf.readFully(buf, pos, n) // EOFException is an IOException
    }

    /** As many of n bytes as the file has at off. */
    private fun readPartial(off: Long, buf: ByteArray, n: Int): Int {
        raf.seek(off)
        var done = 0
        while (done < n) {
            val r = raf.read(buf, done, n - done)
            if (r < 0) break
            done += r
        }
        return done
    }

    private fun readLongs(off: Long, out: LongArray) {
        val b = ByteArray(out.size * 8)
        readFully(off, b, 0, b.size)
        ByteBuffer.wrap(b).order(ByteOrder.BIG_ENDIAN).asLongBuffer().get(out)
    }

    private fun writeAt(off: Long, buf: ByteArray, pos: Int, n: Int) {
        raf.seek(off)
        raf.write(buf, pos, n)
    }

    private fun writeBe64(off: Long, v: Long) {
        val b = ByteArray(8)
        for (i in 0 until 8) b[i] = (v ushr (56 - 8 * i)).toByte()
        writeAt(off, b, 0, 8)
    }

    // ------------------------------------------------------------ qcow2 reading

    /** Makes the L2 table at l2Off the cached one. */
    private fun loadL2(l2Off: Long) {
        if (l2At == l2Off) return
        l2At = 0
        readLongs(l2Off, l2)
        l2At = l2Off
    }

    /** Guest cluster vc, decoded. */
    private fun guestCluster(vc: Long): ByteArray {
        if (clusterAt == vc + 1) return cluster
        clusterAt = 0
        val perL2 = (clusterSize / 8).toLong()
        val i1 = vc / perL2
        val i2 = (vc % perL2).toInt()
        val l2Off = if (i1 < l1.size) l1[i1.toInt()] and QCOW_OFFSET_MASK else 0L
        var entry = 0L
        if (l2Off != 0L) {
            loadL2(l2Off)
            entry = l2[i2]
        }
        if ((entry and QCOW_OFLAG_COMPRESSED) != 0L) {
            val x = 62 - (clusterBits - 8)
            val off = entry and ((1L shl x) - 1)
            val sectors = ((entry ushr x) and ((1L shl (clusterBits - 8)) - 1)) + 1
            var len = sectors * 512 - (off and 511)
            if (len > 2L * clusterSize) len = 2L * clusterSize
            val got = readPartial(off, packed, len.toInt())
            if (got == 0) throw IOException("can't read a compressed qcow2 cluster")
            val inf = Inflater(true)
            try {
                inf.setInput(packed, 0, got)
                var total = 0
                while (total < clusterSize) {
                    val n = inf.inflate(cluster, total, clusterSize - total)
                    total += n
                    if (n == 0 && (inf.finished() || inf.needsInput() || inf.needsDictionary())) break
                }
                if (total < clusterSize && !inf.finished()) {
                    throw IOException("a broken compressed qcow2 cluster")
                }
                cluster.fill(0, total, clusterSize)
            } catch (e: DataFormatException) {
                throw IOException("a broken compressed qcow2 cluster", e)
            } finally {
                inf.end()
            }
        } else {
            val off = entry and QCOW_OFFSET_MASK
            if (off == 0L || (entry and 1L) != 0L) { // unallocated, or a v3 zero cluster
                cluster.fill(0)
            } else {
                readFully(off, cluster, 0, clusterSize)
            }
        }
        clusterAt = vc + 1
        return cluster
    }

    private fun diskRead(offIn: Long, out: ByteArray, posIn: Int, nIn: Int) {
        var off = offIn
        var pos = posIn
        var n = nIn
        if (!qcow) {
            if (off + n > diskSize) throw IOException("a read past the end of the disk")
            readFully(off, out, pos, n)
            return
        }
        while (n > 0) {
            if (off >= diskSize) throw IOException("a read past the end of the disk")
            val vc = off ushr clusterBits
            val inn = (off and (clusterSize - 1).toLong()).toInt()
            val k = minOf(clusterSize - inn, n)
            val c = guestCluster(vc)
            System.arraycopy(c, inn, out, pos, k)
            pos += k
            off += k
            n -= k
        }
    }

    // ------------------------------------------------------------ FATX reading

    private fun fatRaw(c: Long): Long {
        val i = (c * fatEntry).toInt()
        return if (fatEntry == 2) {
            (fat[i].toLong() and 0xFF) or ((fat[i + 1].toLong() and 0xFF) shl 8)
        } else {
            le32(fat, i)
        }
    }

    /** The cluster after c, 0 at the end of the chain (or a broken one). */
    private fun fatNext(c: Long): Long {
        if (c <= 0L || c >= clusters) return 0
        val n = fatRaw(c)
        if (fatEntry == 2 && n >= 0xFFF0) return 0
        if (fatEntry == 4 && n >= 0xFFFFFFF0L) return 0
        return if (n < clusters) n else 0
    }

    /** Reads len bytes of the chain starting at first. */
    private fun readChain(first: Long, out: ByteArray, len: Int) {
        var c = first
        var done = 0
        var steps = 0L
        while (done < len) {
            if (c <= 0L || c >= clusters || steps > clusters) throw IOException("a broken FATX cluster chain")
            val k = minOf(len - done, clusterBytes)
            diskRead(data + (c - 1) * clusterBytes, out, done, k)
            done += k
            if (done < len) c = fatNext(c)
            steps++
        }
    }

    /** A whole directory (its cluster chain). */
    private fun readDir(first: Long): ByteArray {
        var n = 1L
        var c = fatNext(first)
        while (c != 0L && n * clusterBytes < MAX_DIR_BYTES) {
            n++
            c = fatNext(c)
        }
        val buf = ByteArray((n * clusterBytes).toInt())
        readChain(first, buf, buf.size)
        return buf
    }

    /** Entry i of a directory: an entry, DELETED (skip it) or null at the end. */
    private fun direntAt(d: ByteArray, i: Int): Any? {
        if ((i + 1) * FATX_DIRENT > d.size) return null
        val e = i * FATX_DIRENT
        val n = d[e].toInt() and 0xFF
        if (n == 0x00 || n == 0xFF) return null
        if (n == FATX_DELETED || n > FATX_NAME_MAX) return SKIP
        return Dirent(
            String(d, e + 2, n, Charsets.ISO_8859_1),
            d[e + 1].toInt() and 0xFF,
            le32(d, e + 44),
            le32(d, e + 48),
        )
    }

    private inline fun forEachEntry(d: ByteArray, block: (Int, Dirent) -> Boolean) {
        var i = 0
        while (true) {
            val r = direntAt(d, i) ?: return
            if (r is Dirent && !block(i, r)) return
            i++
        }
    }

    private fun findEntry(dir: Long, name: String): Dirent? {
        val d = runCatching { readDir(dir) }.getOrNull() ?: return null
        var found: Dirent? = null
        forEachEntry(d) { _, e ->
            if (asciiEq(e.name, name)) found = e
            found == null
        }
        return found
    }

    private fun udata(): Dirent? =
        findEntry(root, "UDATA")?.takeIf { (it.attr and FATX_ATTR_DIR) != 0 }

    /** Uppercase 8-hex ids of game save folders in E:\UDATA. */
    fun titles(): List<String> {
        val udata = udata() ?: return emptyList()
        val d = runCatching { readDir(udata.first) }.getOrNull() ?: return emptyList()
        val ids = ArrayList<String>()
        forEachEntry(d) { _, e ->
            if ((e.attr and FATX_ATTR_DIR) != 0 && isGameTitleId(e.name)) {
                ids.add(asciiUpper(e.name))
            }
            true
        }
        return ids
    }

    private class Walk(val maxBytes: Long) {
        val files = ArrayList<Pair<String, ByteArray>>()
        var bytes = 0L
    }

    private fun walk(dir: Long, rel: String, depth: Int, w: Walk) {
        if (depth > MAX_DEPTH) throw IOException("folders nested too deep")
        val d = try {
            readDir(dir)
        } catch (e: IOException) {
            throw IOException("can't read a folder", e)
        }
        forEachEntry(d) { _, e ->
            val path = if (rel.isEmpty()) e.name else "$rel\\${e.name}"
            if (path.length >= 256) throw IOException("a path is too long")
            if ((e.attr and FATX_ATTR_DIR) != 0) {
                walk(e.first, path, depth + 1, w)
                return@forEachEntry true
            }
            w.bytes += e.size
            if (w.bytes > w.maxBytes) throw IOException("the save is too big")
            val buf = ByteArray(e.size.toInt())
            if (e.size > 0) {
                try {
                    readChain(e.first, buf, buf.size)
                } catch (ex: IOException) {
                    throw IOException("can't read a file", ex)
                }
            }
            w.files.add(path to buf)
            true
        }
    }

    /**
     * Files under E:\UDATA\<id> (any case): relative paths with '\'
     * separators, sorted by plain String compare (== strcmp for ASCII), as
     * xhdd_read_title. Throws if more than maxBytes.
     */
    fun readTitle(titleId: String, maxBytes: Long = 64L shl 20): List<Pair<String, ByteArray>> {
        val udata = udata()
        val title = udata?.let { findEntry(it.first, titleId) }
        if (title == null || (title.attr and FATX_ATTR_DIR) == 0) {
            throw IOException("no such save on the disk")
        }
        val w = Walk(maxBytes)
        walk(title.first, "", 0, w)
        return w.files.sortedWith { a, b -> a.first.compareTo(b.first) }
    }

    // ------------------------------------------------------------ writing

    /** A new zeroed host cluster at the end of the file, refcount not set. */
    private fun rawAlloc(): Long {
        scratch.fill(0)
        val off = fileEnd
        writeAt(off, scratch, 0, clusterSize)
        fileEnd += clusterSize
        return off
    }

    /** Sets host cluster hc's 16-bit refcount, adding a refcount block when its
     *  range has none yet (published only once the block is written). */
    private fun setRefcount(hc: Long, value: Int, depth: Int) {
        val perBlock = (clusterSize / 2).toLong()
        val ri = hc / perBlock
        if (ri >= rt.size || depth > 2) throw IOException("the qcow2 refcount table is full")
        var block = rt[ri.toInt()] and QCOW_OFFSET_MASK
        if (block == 0L) {
            block = rawAlloc()
            val bc = block ushr clusterBits
            if (bc / perBlock == ri) {
                writeAt(block + (bc % perBlock) * 2, byteArrayOf(0, 1), 0, 2)
            }
            writeBe64(rtOff + ri * 8, block)
            rt[ri.toInt()] = block
            if (bc / perBlock != ri) setRefcount(bc, 1, depth + 1)
        }
        writeAt(block + (hc % perBlock) * 2, byteArrayOf((value ushr 8).toByte(), value.toByte()), 0, 2)
    }

    /** A new host cluster holding buf (clusterSize bytes), refcount 1. */
    private fun hostAlloc(buf: ByteArray): Long {
        val off = fileEnd
        writeAt(off, buf, 0, clusterSize)
        fileEnd += clusterSize
        setRefcount(off ushr clusterBits, 1, 0)
        return off
    }

    /** The host offset guest cluster vc can be written at: in place when this
     *  image alone owns it, else a fresh copy of its contents (an unallocated,
     *  zero or compressed cluster). The old copy of a compressed one is left as
     *  a refcount leak, which qemu-img check -r leaks tidies and nothing minds. */
    private fun writableCluster(vc: Long): Long {
        val perL2 = (clusterSize / 8).toLong()
        val i1 = vc / perL2
        val i2 = (vc % perL2).toInt()
        if (i1 >= l1.size) throw IOException("a write past the qcow2 L1 table")
        var l2Off = l1[i1.toInt()] and QCOW_OFFSET_MASK
        if (l2Off == 0L) {
            scratch.fill(0)
            l2Off = hostAlloc(scratch)
            writeBe64(l1Off + i1 * 8, l2Off or QCOW_OFLAG_COPIED)
            l1[i1.toInt()] = l2Off or QCOW_OFLAG_COPIED
        } else if ((l1[i1.toInt()] and QCOW_OFLAG_COPIED) == 0L) {
            throw IOException("a shared qcow2 L2 table")
        }
        loadL2(l2Off)
        val e = l2[i2]
        if ((e and QCOW_OFLAG_COMPRESSED) == 0L && (e and QCOW_OFFSET_MASK) != 0L && (e and 1L) == 0L) {
            if ((e and QCOW_OFLAG_COPIED) == 0L) throw IOException("a shared qcow2 cluster")
            return e and QCOW_OFFSET_MASK
        }
        val now = guestCluster(vc)
        System.arraycopy(now, 0, scratch, 0, clusterSize)
        val fresh = hostAlloc(scratch)
        writeBe64(l2Off + i2 * 8L, fresh or QCOW_OFLAG_COPIED)
        loadL2(l2Off)
        l2[i2] = fresh or QCOW_OFLAG_COPIED
        return fresh
    }

    private fun diskWrite(offIn: Long, buf: ByteArray, posIn: Int, nIn: Int) {
        var off = offIn
        var pos = posIn
        var n = nIn
        if (!qcow) {
            if (off + n > diskSize) throw IOException("a write past the end of the disk")
            writeAt(off, buf, pos, n)
            return
        }
        while (n > 0) {
            if (off >= diskSize) throw IOException("a write past the end of the disk")
            val vc = off ushr clusterBits
            val at = (off and (clusterSize - 1).toLong()).toInt()
            val k = minOf(clusterSize - at, n)
            val host = writableCluster(vc)
            writeAt(host + at, buf, pos, k)
            if (clusterAt == vc + 1) clusterAt = 0
            pos += k
            off += k
            n -= k
        }
    }

    private fun fatSet(c: Long, v: Long) {
        val p = (c * fatEntry).toInt()
        for (i in 0 until fatEntry) fat[p + i] = (v ushr (8 * i)).toByte()
        if (fatLo > fatHi) {
            fatLo = c
            fatHi = c
        } else if (c < fatLo) {
            fatLo = c
        } else if (c > fatHi) {
            fatHi = c
        }
    }

    private fun fatFlush() {
        if (fatLo > fatHi) return
        val from = (fatLo * fatEntry).toInt()
        val len = ((fatHi - fatLo + 1) * fatEntry).toInt()
        diskWrite(FATX_E_OFFSET + FATX_FAT_OFFSET + from, fat, from, len)
        fatLo = Long.MAX_VALUE
        fatHi = 0
    }

    /** count free clusters chained together; 0 for count 0. */
    private fun allocChain(count: Long): Long {
        var first = 0L
        var prev = 0L
        val end = if (fatEntry == 2) 0xFFFFL else 0xFFFFFFFFL
        for (got in 0 until count) {
            var c = nextFree
            while (c < clusters && fatRaw(c) != 0L) c++
            if (c >= clusters) throw IOException("the Xbox disk is full")
            nextFree = c + 1
            fatSet(c, end)
            if (prev != 0L) fatSet(prev, c) else first = c
            prev = c
        }
        return first
    }

    private fun freeChain(start: Long) {
        var c = start
        var steps = 0L
        while (c != 0L && c < clusters && steps <= clusters) {
            val next = fatNext(c)
            fatSet(c, 0)
            c = next
            steps++
        }
    }

    private fun freeTree(dir: Long, depth: Int) {
        val d = if (depth <= MAX_DEPTH) runCatching { readDir(dir) }.getOrNull() else null
        if (d != null) {
            forEachEntry(d) { _, e ->
                if (e.first != 0L) {
                    if ((e.attr and FATX_ATTR_DIR) != 0) freeTree(e.first, depth + 1) else freeChain(e.first)
                }
                true
            }
        }
        freeChain(dir)
    }

    private fun writeChain(first: Long, buf: ByteArray, len: Int) {
        var c = first
        var done = 0
        while (done < len) {
            if (c == 0L) throw IOException("a broken FATX cluster chain")
            val k = minOf(len - done, clusterBytes)
            diskWrite(data + (c - 1) * clusterBytes, buf, done, k)
            done += k
            c = fatNext(c)
        }
    }

    private fun makeDirent(d: ByteArray, at: Int, name: String, attr: Int, first: Long, size: Long) {
        val raw = name.toByteArray(Charsets.ISO_8859_1)
        d.fill(0xFF.toByte(), at, at + FATX_DIRENT)
        d[at] = raw.size.toByte()
        d[at + 1] = attr.toByte()
        System.arraycopy(raw, 0, d, at + 2, raw.size)
        putLe32(d, at + 44, first)
        putLe32(d, at + 48, size)
        val now = fatxNow()
        putLe32(d, at + 52, now)
        putLe32(d, at + 56, now)
        putLe32(d, at + 60, now)
    }

    /** Writes the files whose paths start with prefix as a new directory
     *  (sub-folders below it recursively); returns its first cluster.
     *  files must be sorted, with '\' separators. */
    private fun writeDir(files: List<Pair<String, ByteArray>>, prefix: String, depth: Int): Long {
        if (depth > MAX_DEPTH) throw IOException("folders nested too deep")
        var ents = ByteArray(16 * FATX_DIRENT)
        var count = 0
        var lastDir = ""
        for ((path, bytes) in files) {
            if (!path.startsWith(prefix)) continue
            val rest = path.substring(prefix.length)
            val sep = rest.indexOf('\\')
            val name = if (sep >= 0) rest.substring(0, sep) else rest
            if (name.isEmpty()) throw IOException("a bad file name")
            if (name.toByteArray(Charsets.ISO_8859_1).size > FATX_NAME_MAX) {
                throw IOException("a file name is too long for FATX")
            }
            if (sep >= 0 && name == lastDir) continue // written already: the files are sorted
            if ((count + 1) * FATX_DIRENT > ents.size) ents = ents.copyOf(ents.size * 2)
            if (sep >= 0) {
                val c = writeDir(files, prefix + name + "\\", depth + 1)
                lastDir = name
                makeDirent(ents, count++ * FATX_DIRENT, name, FATX_ATTR_DIR, c, 0)
            } else {
                val need = (bytes.size.toLong() + clusterBytes - 1) / clusterBytes
                val c = allocChain(need)
                writeChain(c, bytes, bytes.size)
                makeDirent(ents, count++ * FATX_DIRENT, name, FATX_ATTR_FILE, c, bytes.size.toLong())
            }
        }
        // the entries, then 0xFF: the end marker
        val bytesNeeded = (count + 1) * FATX_DIRENT
        val need = (bytesNeeded + clusterBytes - 1) / clusterBytes
        val buf = ByteArray(need * clusterBytes)
        buf.fill(0xFF.toByte())
        System.arraycopy(ents, 0, buf, 0, count * FATX_DIRENT)
        val first = allocChain(need.toLong())
        writeChain(first, buf, buf.size)
        return first
    }

    private fun syncFile() {
        raf.fd.sync()
    }

    /** Replace E:\UDATA\<id> with these files ('\' or '/' paths), as xhdd_write_title. */
    fun writeTitle(titleId: String, files: List<Pair<String, ByteArray>>) {
        if (!writable) throw IOException("the disk image is open read-only")
        require(titleId.length == 8 && titleId.all { hexDigit(it) >= 0 }) { "not an Xbox title id: $titleId" }
        val udata = udata() ?: throw IOException("no E:\\UDATA on the Xbox disk (start a game once)")
        // '/' and '\' alike, sorted, so a folder's files sit together
        val tree = files.map { (p, b) -> p.replace('/', '\\') to b }
            .sortedWith { a, b -> a.first.compareTo(b.first) }

        // the new save goes into free clusters first: until the UDATA entry
        // points at it, the old one is intact
        val first = writeDir(tree, "", 0)
        fatFlush()

        var d = try {
            readDir(udata.first)
        } catch (e: IOException) {
            throw IOException("can't read E:\\UDATA", e)
        }
        var slot = -1
        var reuse = -1
        var i = 0
        var old = 0L
        var name = ""
        while (true) {
            val r = direntAt(d, i) ?: break
            if (r is Dirent && asciiEq(r.name, titleId)) {
                slot = i
                old = r.first
                name = r.name // keep its case
                break
            }
            if (r === SKIP && (d[i * FATX_DIRENT].toInt() and 0xFF) == FATX_DELETED && reuse < 0) reuse = i
            i++
        }
        if (slot < 0) {
            // XAPI names the folder in lowercase hex
            name = titleId.map { if (it in 'A'..'F') it + 32 else it }.joinToString("")
            slot = if (reuse >= 0) reuse else i
            if ((slot + 1) * FATX_DIRENT > d.size) {
                // E:\UDATA is full: one more cluster on its chain
                var last = udata.first
                var n = fatNext(last)
                while (n != 0L) {
                    last = n
                    n = fatNext(n)
                }
                val more = allocChain(1)
                val bigger = d.copyOf(d.size + clusterBytes)
                bigger.fill(0xFF.toByte(), d.size, bigger.size)
                fatSet(last, more)
                d = bigger
            }
        }
        val mark = d[slot * FATX_DIRENT].toInt() and 0xFF
        makeDirent(d, slot * FATX_DIRENT, name, FATX_ATTR_DIR, first, 0)
        // a slot taken from the end marker moves the marker along
        if ((mark == 0x00 || mark == 0xFF) && (slot + 2) * FATX_DIRENT <= d.size) {
            d[(slot + 1) * FATX_DIRENT] = 0xFF.toByte()
        }
        fatFlush()
        writeChain(udata.first, d, d.size)
        syncFile()
        if (old != 0L) {
            freeTree(old, 0)
            fatFlush()
            syncFile()
        }
    }

    override fun close() {
        raf.close()
    }

    companion object {
        private const val QCOW_MAGIC = 0x514649FBL // "QFI\xfb"
        private const val QCOW_OFLAG_COMPRESSED = 1L shl 62
        private const val QCOW_OFLAG_COPIED = 1L shl 63
        private const val QCOW_OFFSET_MASK = 0x00FFFFFFFFFFFE00L

        // the retail partition table: E: is fixed in every layout
        private const val FATX_E_OFFSET = 0xABE80000L
        private const val FATX_E_SIZE = 0x1312D6000L
        private const val FATX_FAT_OFFSET = 0x1000L
        private const val FATX_DIRENT = 64
        private const val FATX_NAME_MAX = 42
        private const val FATX_ATTR_DIR = 0x10
        private const val FATX_ATTR_FILE = 0x00
        private const val FATX_DELETED = 0xE5
        private const val MAX_DIR_BYTES = 4L shl 20
        private const val MAX_DEPTH = 8

        private val SKIP = Any()

        /** Read-only open; throws IOException with a readable reason. */
        fun open(file: File): XboxHddImage = openImage(file, false)

        /** Read-write open; refuses qcow2 images with snapshots or the dirty bit, like xhdd_open_rw. */
        fun openWritable(file: File): XboxHddImage = openImage(file, true)

        private fun openImage(file: File, rw: Boolean): XboxHddImage {
            val raf = try {
                RandomAccessFile(file, if (rw) "rw" else "r")
            } catch (e: IOException) {
                throw IOException("can't open the disk image", e)
            }
            try {
                return XboxHddImage(raf, rw).also { it.mount() }
            } catch (e: Throwable) {
                raf.close()
                throw e
            }
        }

        /** 8 hex digits, first two bytes uppercase letters (xhdd_is_game_title_id). */
        fun isGameTitleId(name: String): Boolean {
            if (name.length != 8 || name.any { hexDigit(it) < 0 }) return false
            val a = hexDigit(name[0]) * 16 + hexDigit(name[1])
            val b = hexDigit(name[2]) * 16 + hexDigit(name[3])
            return a in 'A'.code..'Z'.code && b in 'A'.code..'Z'.code
        }

        /** Copy image to backup through backup.tmp (xhdd_backup). */
        fun backup(image: File, backup: File) {
            val tmp = File(backup.path + ".tmp")
            try {
                FileInputStream(image).use { input ->
                    FileOutputStream(tmp).use { out ->
                        input.copyTo(out, 4 shl 20)
                        out.fd.sync()
                    }
                }
                tmp.setReadable(true, false)
                tmp.setWritable(true, false)
                Files.move(tmp.toPath(), backup.toPath(), StandardCopyOption.REPLACE_EXISTING)
            } catch (e: IOException) {
                tmp.delete()
                throw e
            }
        }

        private fun hexDigit(c: Char): Int = when (c) {
            in '0'..'9' -> c - '0'
            in 'a'..'f' -> c - 'a' + 10
            in 'A'..'F' -> c - 'A' + 10
            else -> -1
        }

        private fun asciiUpper(s: String): String =
            s.map { if (it in 'a'..'z') it - 32 else it }.joinToString("")

        private fun asciiEq(a: String, b: String): Boolean =
            a.length == b.length && asciiUpper(a) == asciiUpper(b)

        private fun be32(b: ByteArray, i: Int): Long =
            ((b[i].toLong() and 0xFF) shl 24) or ((b[i + 1].toLong() and 0xFF) shl 16) or
                ((b[i + 2].toLong() and 0xFF) shl 8) or (b[i + 3].toLong() and 0xFF)

        private fun be64(b: ByteArray, i: Int): Long = (be32(b, i) shl 32) or be32(b, i + 4)

        private fun le32(b: ByteArray, i: Int): Long =
            (b[i].toLong() and 0xFF) or ((b[i + 1].toLong() and 0xFF) shl 8) or
                ((b[i + 2].toLong() and 0xFF) shl 16) or ((b[i + 3].toLong() and 0xFF) shl 24)

        private fun putLe32(b: ByteArray, i: Int, v: Long) {
            for (k in 0 until 4) b[i + k] = (v ushr (8 * k)).toByte()
        }

        /** A FAT-style date and time, years counted from 2000 as FATX does. */
        private fun fatxNow(): Long {
            val t = LocalDateTime.now()
            val year = maxOf(t.year - 2000, 0).toLong()
            val date = (year shl 9) or (t.monthValue.toLong() shl 5) or t.dayOfMonth.toLong()
            val tod = (t.hour.toLong() shl 11) or (t.minute.toLong() shl 5) or (t.second / 2).toLong()
            return (date shl 16) or tod
        }
    }
}
