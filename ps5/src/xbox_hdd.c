/*
 * GameSync PS5 client - Xbox saves inside XPSemu's virtual hard disk.
 * SPDX-License-Identifier: GPL-3.0-or-later
 *
 * XPSemu (xemu) keeps the Xbox hard disk as a qcow2 image; game saves are in
 * its FATX E: partition under E:\UDATA\<title id>, as on a real console.
 * Writing a save (xhdd_write_title) puts it in free FATX clusters and
 * switches the UDATA entry over last, so a failure leaves the old save; in
 * the qcow2 it overwrites clusters the image owns alone and appends the
 * rest, with refcounts, the way qemu would. XPSemu must not have the image
 * open meanwhile - its own cached tables would undo the change.
 *
 * Based on the qcow2 format description in QEMU (docs/interop/qcow2.txt,
 * GPL-2.0-or-later documentation; no code copied) and the FATX layout as
 * documented by the Xbox-Linux project and xboxdevwiki.net
 * (https://xboxdevwiki.net/FATX).
 *
 * The Android client has a Kotlin port for X1 BOX's disk image
 * (android/.../emulators/XboxHddImage.kt): keep the two in step.
 */
#include "xbox_hdd.h"

#include "util.h"

#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <time.h>
#include <unistd.h>
#include <zlib.h>

#define QCOW_MAGIC 0x514649fbu /* "QFI\xfb" */
#define QCOW_OFLAG_COMPRESSED (1ull << 62)
#define QCOW_OFFSET_MASK 0x00fffffffffffe00ull

/* the retail partition table: E: is fixed in every layout */
#define FATX_E_OFFSET 0xABE80000ull
#define FATX_E_SIZE 0x1312D6000ull
#define FATX_FAT_OFFSET 0x1000u
#define FATX_DIRENT 64
#define FATX_NAME_MAX 42
#define FATX_ATTR_DIR 0x10
#define FATX_DELETED 0xE5
#define MAX_DIR_BYTES (4u << 20)
#define MAX_DEPTH 8

struct XboxHdd
{
    FILE *f;
    /* qcow2 */
    unsigned cluster_bits;
    uint64_t cluster_size;
    uint64_t disk_size;
    uint64_t *l1;
    uint32_t l1_size;
    uint64_t *l2;
    uint64_t l2_at;    /* image offset of the cached L2 table, 0 = none */
    uint8_t *cluster;  /* the last guest cluster read */
    uint64_t cluster_at; /* its guest index + 1, 0 = none */
    uint8_t *packed;   /* a compressed cluster as stored */
    uint64_t l1_off;
    /* writing (xhdd_open_rw) */
    bool rw;
    uint64_t file_end; /* where the next host cluster goes */
    uint64_t rt_off;   /* refcount table */
    uint64_t *rt;
    uint32_t rt_entries;
    uint8_t *scratch;  /* a guest cluster being moved */
    uint32_t fat_lo, fat_hi; /* changed FAT entries, lo > hi = none */
    uint32_t next_free;
    /* FATX E: */
    uint32_t fat_entry; /* 2 or 4 bytes */
    uint32_t clusters;
    uint32_t cluster_bytes;
    uint32_t root;
    uint64_t data;     /* guest offset of cluster 1 */
    uint8_t *fat;
};

static uint64_t be64(const uint8_t *p)
{
    uint64_t v = 0;
    for (int i = 0; i < 8; ++i)
        v = (v << 8) | p[i];
    return v;
}

static uint32_t be32(const uint8_t *p)
{
    return (uint32_t)p[0] << 24 | (uint32_t)p[1] << 16 | (uint32_t)p[2] << 8 | p[3];
}

static uint32_t le32(const uint8_t *p)
{
    return (uint32_t)p[3] << 24 | (uint32_t)p[2] << 16 | (uint32_t)p[1] << 8 | p[0];
}

static bool read_at(FILE *f, uint64_t off, void *buf, size_t n, size_t *got)
{
    if (fseeko(f, (off_t)off, SEEK_SET) != 0)
        return false;
    size_t r = fread(buf, 1, n, f);
    if (got)
        *got = r;
    return got ? true : r == n;
}

/* Makes the L2 table at l2_off the cached one. */
static bool load_l2(XboxHdd *h, uint64_t l2_off)
{
    if (h->l2_at == l2_off)
        return true;
    h->l2_at = 0;
    if (!read_at(h->f, l2_off, h->l2, (size_t)h->cluster_size, NULL))
        return false;
    for (uint64_t i = 0; i < h->cluster_size / 8; ++i)
        h->l2[i] = be64((const uint8_t *)&h->l2[i]);
    h->l2_at = l2_off;
    return true;
}

/* Guest cluster vc, decoded; NULL on a read error. */
static const uint8_t *guest_cluster(XboxHdd *h, uint64_t vc)
{
    if (h->cluster_at == vc + 1)
        return h->cluster;
    h->cluster_at = 0;
    uint64_t per_l2 = h->cluster_size / 8;
    uint64_t i1 = vc / per_l2, i2 = vc % per_l2;
    uint64_t l2_off = i1 < h->l1_size ? h->l1[i1] & QCOW_OFFSET_MASK : 0;
    uint64_t entry = 0;
    if (l2_off)
    {
        if (!load_l2(h, l2_off))
            return NULL;
        entry = h->l2[i2];
    }
    if (entry & QCOW_OFLAG_COMPRESSED)
    {
        unsigned x = 62 - (h->cluster_bits - 8);
        uint64_t off = entry & ((1ull << x) - 1);
        uint64_t sectors = ((entry >> x) & ((1ull << (h->cluster_bits - 8)) - 1)) + 1;
        uint64_t len = sectors * 512 - (off & 511);
        if (len > 2 * h->cluster_size)
            len = 2 * h->cluster_size;
        size_t got = 0;
        if (!read_at(h->f, off, h->packed, (size_t)len, &got) || !got)
            return NULL;
        z_stream zs;
        memset(&zs, 0, sizeof(zs));
        if (inflateInit2(&zs, -12) != Z_OK)
            return NULL;
        zs.next_in = h->packed;
        zs.avail_in = (uInt)got;
        zs.next_out = h->cluster;
        zs.avail_out = (uInt)h->cluster_size;
        int rc = inflate(&zs, Z_FINISH);
        bool full = zs.avail_out == 0;
        inflateEnd(&zs);
        if (rc != Z_STREAM_END && !full)
            return NULL;
    }
    else
    {
        uint64_t off = entry & QCOW_OFFSET_MASK;
        if (!off || (entry & 1)) /* unallocated, or a v3 zero cluster */
            memset(h->cluster, 0, (size_t)h->cluster_size);
        else if (!read_at(h->f, off, h->cluster, (size_t)h->cluster_size, NULL))
            return NULL;
    }
    h->cluster_at = vc + 1;
    return h->cluster;
}

static bool disk_read(XboxHdd *h, uint64_t off, void *buf, size_t n)
{
    uint8_t *out = buf;
    while (n)
    {
        if (off >= h->disk_size)
            return false;
        uint64_t vc = off >> h->cluster_bits, in = off & (h->cluster_size - 1);
        size_t k = (size_t)(h->cluster_size - in);
        if (k > n)
            k = n;
        const uint8_t *c = guest_cluster(h, vc);
        if (!c)
            return false;
        memcpy(out, c + in, k);
        out += k;
        off += k;
        n -= k;
    }
    return true;
}

void xhdd_close(XboxHdd *h)
{
    if (!h)
        return;
    if (h->f)
        fclose(h->f);
    free(h->l1);
    free(h->l2);
    free(h->cluster);
    free(h->packed);
    free(h->fat);
    free(h->rt);
    free(h->scratch);
    free(h);
}

static XboxHdd *fail(XboxHdd *h, char *err, size_t size, const char *why)
{
    util_copy(err, size, why);
    xhdd_close(h);
    return NULL;
}

static XboxHdd *open_image(const char *path, bool rw, char *err, size_t err_size)
{
    XboxHdd *h = calloc(1, sizeof(*h));
    if (!h)
        return fail(h, err, err_size, "out of memory");
    h->rw = rw;
    h->f = fopen(path, rw ? "r+b" : "rb");
    if (!h->f)
        return fail(h, err, err_size, "can't open the disk image");
    uint8_t hdr[112];
    memset(hdr, 0, sizeof(hdr));
    size_t got = 0;
    read_at(h->f, 0, hdr, sizeof(hdr), &got);
    if (got < 72 || be32(hdr) != QCOW_MAGIC)
        return fail(h, err, err_size, "not a qcow2 disk image");
    uint32_t version = be32(hdr + 4);
    if (version != 2 && version != 3)
        return fail(h, err, err_size, "unsupported qcow2 version");
    if (be64(hdr + 8) != 0)
        return fail(h, err, err_size, "qcow2 backing files aren't supported");
    if (be32(hdr + 32) != 0)
        return fail(h, err, err_size, "encrypted qcow2 images aren't supported");
    /* v3 headers past 104 bytes name the compression; only zlib is read */
    if (version == 3 && be32(hdr + 100) > 104 && hdr[104] != 0)
        return fail(h, err, err_size, "zstd-compressed qcow2 images aren't supported");
    h->cluster_bits = be32(hdr + 20);
    if (h->cluster_bits < 9 || h->cluster_bits > 21)
        return fail(h, err, err_size, "bad qcow2 cluster size");
    h->cluster_size = 1ull << h->cluster_bits;
    h->disk_size = be64(hdr + 24);
    h->l1_size = be32(hdr + 36);
    uint64_t l1_off = be64(hdr + 40);
    h->l1_off = l1_off;
    if (rw)
    {
        /* only what qemu-img create makes and xemu leaves: no snapshots
         * (every cluster owned once), 16-bit refcounts, clean shutdown */
        if (be32(hdr + 60) != 0)
            return fail(h, err, err_size, "the disk image has snapshots: not writing it");
        if (version == 3 && (be32(hdr + 96) != 4 || be64(hdr + 72) != 0))
            return fail(h, err, err_size, "the disk image wasn't closed cleanly: start and quit XPSemu once");
        h->rt_off = be64(hdr + 48);
        uint64_t rt_bytes = (uint64_t)be32(hdr + 56) << h->cluster_bits;
        if (!h->rt_off || !rt_bytes || rt_bytes > (64u << 20))
            return fail(h, err, err_size, "bad qcow2 refcount table");
        h->rt_entries = (uint32_t)(rt_bytes / 8);
        h->rt = malloc((size_t)rt_bytes);
        h->scratch = malloc((size_t)h->cluster_size);
        if (!h->rt || !h->scratch)
            return fail(h, err, err_size, "out of memory");
        if (!read_at(h->f, h->rt_off, h->rt, (size_t)rt_bytes, NULL))
            return fail(h, err, err_size, "can't read the qcow2 refcount table");
        for (uint32_t i = 0; i < h->rt_entries; ++i)
            h->rt[i] = be64((const uint8_t *)&h->rt[i]);
        if (fseeko(h->f, 0, SEEK_END) != 0)
            return fail(h, err, err_size, "can't size the disk image");
        uint64_t end = (uint64_t)ftello(h->f);
        h->file_end = (end + h->cluster_size - 1) & ~(h->cluster_size - 1);
        h->fat_lo = UINT32_MAX;
        h->next_free = 1;
    }
    if (h->l1_size == 0 || h->l1_size > (1u << 24))
        return fail(h, err, err_size, "bad qcow2 L1 table");
    h->l1 = malloc((size_t)h->l1_size * 8);
    h->l2 = malloc((size_t)h->cluster_size);
    h->cluster = malloc((size_t)h->cluster_size);
    h->packed = malloc((size_t)h->cluster_size * 2);
    if (!h->l1 || !h->l2 || !h->cluster || !h->packed)
        return fail(h, err, err_size, "out of memory");
    if (!read_at(h->f, l1_off, h->l1, (size_t)h->l1_size * 8, NULL))
        return fail(h, err, err_size, "can't read the qcow2 L1 table");
    for (uint32_t i = 0; i < h->l1_size; ++i)
        h->l1[i] = be64((const uint8_t *)&h->l1[i]);

    uint8_t sb[16];
    if (h->disk_size < FATX_E_OFFSET + FATX_E_SIZE || !disk_read(h, FATX_E_OFFSET, sb, sizeof(sb)) ||
        memcmp(sb, "FATX", 4) != 0)
        return fail(h, err, err_size, "no FATX E: partition on the disk");
    uint32_t spc = le32(sb + 8);
    if (spc == 0 || spc > 1024 || (spc & (spc - 1)))
        return fail(h, err, err_size, "bad FATX cluster size");
    h->cluster_bytes = spc * 512;
    h->root = le32(sb + 12);
    h->clusters = (uint32_t)(FATX_E_SIZE / h->cluster_bytes);
    h->fat_entry = h->clusters < 0xFFF0 ? 2 : 4;
    uint64_t fat_bytes = ((uint64_t)h->clusters * h->fat_entry + 4095) & ~4095ull;
    h->data = FATX_E_OFFSET + FATX_FAT_OFFSET + fat_bytes;
    h->fat = malloc((size_t)fat_bytes);
    if (!h->fat)
        return fail(h, err, err_size, "out of memory");
    if (!disk_read(h, FATX_E_OFFSET + FATX_FAT_OFFSET, h->fat, (size_t)fat_bytes))
        return fail(h, err, err_size, "can't read the FATX table");
    return h;
}

XboxHdd *xhdd_open(const char *path, char *err, size_t err_size)
{
    return open_image(path, false, err, err_size);
}

XboxHdd *xhdd_open_rw(const char *path, char *err, size_t err_size)
{
    return open_image(path, true, err, err_size);
}

/* The cluster after c, 0 at the end of the chain (or a broken one). */
static uint32_t fat_next(const XboxHdd *h, uint32_t c)
{
    if (c == 0 || c >= h->clusters)
        return 0;
    uint32_t n;
    if (h->fat_entry == 2)
    {
        n = (uint32_t)h->fat[c * 2] | (uint32_t)h->fat[c * 2 + 1] << 8;
        if (n >= 0xFFF0)
            return 0;
    }
    else
    {
        n = le32(h->fat + (size_t)c * 4);
        if (n >= 0xFFFFFFF0u)
            return 0;
    }
    return n < h->clusters ? n : 0;
}

/* Reads up to len bytes of the chain starting at first. */
static bool read_chain(XboxHdd *h, uint32_t first, uint8_t *out, uint64_t len)
{
    uint32_t c = first;
    uint64_t done = 0;
    for (uint32_t steps = 0; done < len; ++steps)
    {
        if (c == 0 || c >= h->clusters || steps > h->clusters)
            return false;
        uint64_t k = len - done < h->cluster_bytes ? len - done : h->cluster_bytes;
        if (!disk_read(h, h->data + (uint64_t)(c - 1) * h->cluster_bytes, out + done, (size_t)k))
            return false;
        done += k;
        if (done < len)
            c = fat_next(h, c);
    }
    return true;
}

/* A whole directory (its cluster chain); *len set. */
static uint8_t *read_dir(XboxHdd *h, uint32_t first, size_t *len)
{
    size_t n = 1;
    for (uint32_t c = fat_next(h, first); c && n * h->cluster_bytes < MAX_DIR_BYTES; c = fat_next(h, c))
        ++n;
    *len = n * h->cluster_bytes;
    uint8_t *buf = malloc(*len);
    if (buf && !read_chain(h, first, buf, *len))
    {
        free(buf);
        buf = NULL;
    }
    return buf;
}

typedef struct
{
    char name[FATX_NAME_MAX + 1];
    uint8_t attr;
    uint32_t first;
    uint32_t size;
} Dirent;

/* Entry i of a directory: 1 = an entry, 0 = deleted/skip, -1 = the end. */
static int dirent_at(const uint8_t *d, size_t len, size_t i, Dirent *out)
{
    if ((i + 1) * FATX_DIRENT > len)
        return -1;
    const uint8_t *e = d + i * FATX_DIRENT;
    uint8_t n = e[0];
    if (n == 0x00 || n == 0xFF)
        return -1;
    if (n == FATX_DELETED || n > FATX_NAME_MAX)
        return 0;
    memcpy(out->name, e + 2, n);
    out->name[n] = 0;
    out->attr = e[1];
    out->first = le32(e + 44);
    out->size = le32(e + 48);
    return 1;
}

static bool find_entry(XboxHdd *h, uint32_t dir, const char *name, Dirent *out)
{
    size_t len;
    uint8_t *d = read_dir(h, dir, &len);
    if (!d)
        return false;
    bool found = false;
    int r;
    for (size_t i = 0; !found && (r = dirent_at(d, len, i, out)) >= 0; ++i)
        found = r == 1 && util_ieq(out->name, name);
    free(d);
    return found;
}

static int hex_digit(char c)
{
    if (c >= '0' && c <= '9')
        return c - '0';
    if (c >= 'a' && c <= 'f')
        return c - 'a' + 10;
    if (c >= 'A' && c <= 'F')
        return c - 'A' + 10;
    return -1;
}

bool xhdd_is_game_title_id(const char *name)
{
    if (!name || strlen(name) != 8)
        return false;
    for (int i = 0; i < 8; ++i)
        if (hex_digit(name[i]) < 0)
            return false;
    int a = hex_digit(name[0]) * 16 + hex_digit(name[1]);
    int b = hex_digit(name[2]) * 16 + hex_digit(name[3]);
    return a >= 'A' && a <= 'Z' && b >= 'A' && b <= 'Z';
}

int xhdd_titles(XboxHdd *h, char (*ids)[9], int max)
{
    Dirent udata;
    if (!find_entry(h, h->root, "UDATA", &udata) || !(udata.attr & FATX_ATTR_DIR))
        return 0;
    size_t len;
    uint8_t *d = read_dir(h, udata.first, &len);
    if (!d)
        return 0;
    int count = 0, r;
    Dirent e;
    for (size_t i = 0; count < max && (r = dirent_at(d, len, i, &e)) >= 0; ++i)
    {
        if (r != 1 || !(e.attr & FATX_ATTR_DIR) || !xhdd_is_game_title_id(e.name))
            continue;
        for (int k = 0; k < 8; ++k)
            ids[count][k] = (char)(e.name[k] >= 'a' && e.name[k] <= 'f' ? e.name[k] - 32 : e.name[k]);
        ids[count][8] = 0;
        ++count;
    }
    free(d);
    return count;
}

typedef struct
{
    TreeFile *files;
    int count, cap;
    uint64_t bytes, max_bytes;
    const char *why;
} Walk;

static bool walk(XboxHdd *h, uint32_t dir, const char *rel, int depth, Walk *w)
{
    if (depth > MAX_DEPTH)
    {
        w->why = "folders nested too deep";
        return false;
    }
    size_t len;
    uint8_t *d = read_dir(h, dir, &len);
    if (!d)
    {
        w->why = "can't read a folder";
        return false;
    }
    bool ok = true;
    int r;
    Dirent e;
    for (size_t i = 0; ok && (r = dirent_at(d, len, i, &e)) >= 0; ++i)
    {
        if (r != 1)
            continue;
        char path[256];
        int n = rel[0] ? snprintf(path, sizeof(path), "%s\\%s", rel, e.name)
                       : snprintf(path, sizeof(path), "%s", e.name);
        if (n < 0 || (size_t)n >= sizeof(path))
        {
            w->why = "a path is too long";
            ok = false;
            break;
        }
        if (e.attr & FATX_ATTR_DIR)
        {
            ok = walk(h, e.first, path, depth + 1, w);
            continue;
        }
        w->bytes += e.size;
        if (w->bytes > w->max_bytes)
        {
            w->why = "the save is too big";
            ok = false;
            break;
        }
        if (w->count == w->cap)
        {
            int cap = w->cap ? w->cap * 2 : 32;
            TreeFile *grown = realloc(w->files, (size_t)cap * sizeof(TreeFile));
            if (!grown)
            {
                w->why = "out of memory";
                ok = false;
                break;
            }
            w->files = grown;
            w->cap = cap;
        }
        TreeFile *f = &w->files[w->count];
        memset(f, 0, sizeof(*f));
        util_copy(f->path, sizeof(f->path), path);
        f->size = e.size;
        f->data = malloc((size_t)e.size + 1);
        if (!f->data)
        {
            w->why = "out of memory";
            ok = false;
            break;
        }
        w->count++;
        if (e.size && !read_chain(h, e.first, f->data, e.size))
        {
            w->why = "can't read a file";
            ok = false;
        }
    }
    free(d);
    return ok;
}

bool xhdd_read_title(XboxHdd *h, const char *title_id, uint64_t max_bytes, SaveTree *out,
                     char *err, size_t err_size)
{
    memset(out, 0, sizeof(*out));
    Dirent udata, title;
    if (!find_entry(h, h->root, "UDATA", &udata) || !(udata.attr & FATX_ATTR_DIR) ||
        !find_entry(h, udata.first, title_id, &title) || !(title.attr & FATX_ATTR_DIR))
    {
        util_copy(err, err_size, "no such save on the disk");
        return false;
    }
    Walk w = {0};
    w.max_bytes = max_bytes;
    bool ok = walk(h, title.first, "", 0, &w);
    SaveTree tree = {w.files, w.count};
    if (!ok)
    {
        util_copy(err, err_size, w.why ? w.why : "can't read the save");
        tree_free(&tree);
        return false;
    }
    tree_sort(&tree);
    *out = tree;
    return true;
}

/* ------------------------------------------------------------ writing */

#define QCOW_OFLAG_COPIED (1ull << 63)
#define FATX_ATTR_FILE 0x00

static bool write_at(FILE *f, uint64_t off, const void *buf, size_t n)
{
    return fseeko(f, (off_t)off, SEEK_SET) == 0 && fwrite(buf, 1, n, f) == n;
}

static bool write_be64(FILE *f, uint64_t off, uint64_t v)
{
    uint8_t b[8];
    for (int i = 0; i < 8; ++i)
        b[i] = (uint8_t)(v >> (56 - 8 * i));
    return write_at(f, off, b, 8);
}

/* A new zeroed host cluster at the end of the file, refcount not set. */
static bool raw_alloc(XboxHdd *h, uint64_t *off)
{
    memset(h->scratch, 0, (size_t)h->cluster_size);
    *off = h->file_end;
    if (!write_at(h->f, *off, h->scratch, (size_t)h->cluster_size))
        return false;
    h->file_end += h->cluster_size;
    return true;
}

/* Sets host cluster hc's 16-bit refcount, adding a refcount block when its
 * range has none yet (published only once the block is written). */
static bool set_refcount(XboxHdd *h, uint64_t hc, uint16_t value, int depth)
{
    uint64_t per_block = h->cluster_size / 2;
    uint64_t ri = hc / per_block;
    if (ri >= h->rt_entries || depth > 2)
        return false;
    uint64_t block = h->rt[ri] & QCOW_OFFSET_MASK;
    if (!block)
    {
        if (!raw_alloc(h, &block))
            return false;
        uint64_t bc = block >> h->cluster_bits;
        if (bc / per_block == ri)
        {
            uint8_t one[2] = {0, 1};
            if (!write_at(h->f, block + (bc % per_block) * 2, one, 2))
                return false;
        }
        if (!write_be64(h->f, h->rt_off + ri * 8, block))
            return false;
        h->rt[ri] = block;
        if (bc / per_block != ri && !set_refcount(h, bc, 1, depth + 1))
            return false;
    }
    uint8_t v[2] = {(uint8_t)(value >> 8), (uint8_t)value};
    return write_at(h->f, block + (hc % per_block) * 2, v, 2);
}

/* A new host cluster holding data (cluster_size bytes), refcount 1. */
static bool host_alloc(XboxHdd *h, const uint8_t *data, uint64_t *off)
{
    *off = h->file_end;
    if (!write_at(h->f, *off, data, (size_t)h->cluster_size))
        return false;
    h->file_end += h->cluster_size;
    return set_refcount(h, *off >> h->cluster_bits, 1, 0);
}

/* The host offset guest cluster vc can be written at: in place when this
 * image alone owns it, else a fresh copy of its contents (an unallocated,
 * zero or compressed cluster). The old copy of a compressed one is left as
 * a refcount leak, which qemu-img check -r leaks tidies and nothing minds. */
static bool writable_cluster(XboxHdd *h, uint64_t vc, uint64_t *host)
{
    uint64_t per_l2 = h->cluster_size / 8;
    uint64_t i1 = vc / per_l2, i2 = vc % per_l2;
    if (i1 >= h->l1_size)
        return false;
    uint64_t l2_off = h->l1[i1] & QCOW_OFFSET_MASK;
    if (!l2_off)
    {
        memset(h->scratch, 0, (size_t)h->cluster_size);
        if (!host_alloc(h, h->scratch, &l2_off) ||
            !write_be64(h->f, h->l1_off + i1 * 8, l2_off | QCOW_OFLAG_COPIED))
            return false;
        h->l1[i1] = l2_off | QCOW_OFLAG_COPIED;
    }
    else if (!(h->l1[i1] & QCOW_OFLAG_COPIED))
        return false; /* a shared L2 table */
    if (!load_l2(h, l2_off))
        return false;
    uint64_t e = h->l2[i2];
    if (!(e & QCOW_OFLAG_COMPRESSED) && (e & QCOW_OFFSET_MASK) && !(e & 1))
    {
        if (!(e & QCOW_OFLAG_COPIED))
            return false; /* a shared cluster */
        *host = e & QCOW_OFFSET_MASK;
        return true;
    }
    const uint8_t *now = guest_cluster(h, vc);
    if (!now)
        return false;
    memcpy(h->scratch, now, (size_t)h->cluster_size);
    uint64_t fresh;
    if (!host_alloc(h, h->scratch, &fresh) ||
        !write_be64(h->f, l2_off + i2 * 8, fresh | QCOW_OFLAG_COPIED) || !load_l2(h, l2_off))
        return false;
    h->l2[i2] = fresh | QCOW_OFLAG_COPIED;
    *host = fresh;
    return true;
}

static bool disk_write(XboxHdd *h, uint64_t off, const void *buf, size_t n)
{
    const uint8_t *in = buf;
    while (n)
    {
        if (off >= h->disk_size)
            return false;
        uint64_t vc = off >> h->cluster_bits, at = off & (h->cluster_size - 1);
        size_t k = (size_t)(h->cluster_size - at);
        if (k > n)
            k = n;
        uint64_t host;
        if (!writable_cluster(h, vc, &host) || !write_at(h->f, host + at, in, k))
            return false;
        if (h->cluster_at == vc + 1)
            h->cluster_at = 0;
        in += k;
        off += k;
        n -= k;
    }
    return true;
}

static uint32_t fat_raw(const XboxHdd *h, uint32_t c)
{
    return h->fat_entry == 2 ? (uint32_t)h->fat[c * 2] | (uint32_t)h->fat[c * 2 + 1] << 8
                             : le32(h->fat + (size_t)c * 4);
}

static void fat_set(XboxHdd *h, uint32_t c, uint32_t v)
{
    uint8_t *p = h->fat + (size_t)c * h->fat_entry;
    for (uint32_t i = 0; i < h->fat_entry; ++i)
        p[i] = (uint8_t)(v >> (8 * i));
    if (h->fat_lo > h->fat_hi)
        h->fat_lo = h->fat_hi = c;
    else if (c < h->fat_lo)
        h->fat_lo = c;
    else if (c > h->fat_hi)
        h->fat_hi = c;
}

static bool fat_flush(XboxHdd *h)
{
    if (h->fat_lo > h->fat_hi)
        return true;
    uint64_t from = (uint64_t)h->fat_lo * h->fat_entry;
    uint64_t len = (uint64_t)(h->fat_hi - h->fat_lo + 1) * h->fat_entry;
    if (!disk_write(h, FATX_E_OFFSET + FATX_FAT_OFFSET + from, h->fat + from, (size_t)len))
        return false;
    h->fat_lo = UINT32_MAX;
    h->fat_hi = 0;
    return true;
}

/* count free clusters chained together; *first = 0 for count 0 */
static bool alloc_chain(XboxHdd *h, uint32_t count, uint32_t *first)
{
    *first = 0;
    uint32_t prev = 0, end = h->fat_entry == 2 ? 0xFFFF : 0xFFFFFFFFu;
    for (uint32_t got = 0; got < count; ++got)
    {
        uint32_t c = h->next_free;
        while (c < h->clusters && fat_raw(h, c) != 0)
            ++c;
        if (c >= h->clusters)
            return false;
        h->next_free = c + 1;
        fat_set(h, c, end);
        if (prev)
            fat_set(h, prev, c);
        else
            *first = c;
        prev = c;
    }
    return true;
}

static void free_chain(XboxHdd *h, uint32_t c)
{
    for (uint32_t steps = 0; c && c < h->clusters && steps <= h->clusters; ++steps)
    {
        uint32_t next = fat_next(h, c);
        fat_set(h, c, 0);
        c = next;
    }
}

static void free_tree(XboxHdd *h, uint32_t dir, int depth)
{
    size_t len;
    uint8_t *d = depth <= MAX_DEPTH ? read_dir(h, dir, &len) : NULL;
    Dirent e;
    int r;
    for (size_t i = 0; d && (r = dirent_at(d, len, i, &e)) >= 0; ++i)
    {
        if (r != 1 || !e.first)
            continue;
        if (e.attr & FATX_ATTR_DIR)
            free_tree(h, e.first, depth + 1);
        else
            free_chain(h, e.first);
    }
    free(d);
    free_chain(h, dir);
}

static bool write_chain(XboxHdd *h, uint32_t first, const uint8_t *data, uint64_t len)
{
    uint32_t c = first;
    for (uint64_t done = 0; done < len; c = fat_next(h, c))
    {
        if (!c)
            return false;
        uint64_t k = len - done < h->cluster_bytes ? len - done : h->cluster_bytes;
        if (!disk_write(h, h->data + (uint64_t)(c - 1) * h->cluster_bytes, data + done, (size_t)k))
            return false;
        done += k;
    }
    return true;
}

static void put_le32(uint8_t *p, uint32_t v)
{
    for (int i = 0; i < 4; ++i)
        p[i] = (uint8_t)(v >> (8 * i));
}

/* A FAT-style date and time, years counted from 2000 as FATX does. */
static uint32_t fatx_now(void)
{
    time_t t = time(NULL);
    struct tm tm;
    struct tm *now = localtime(&t); /* the PS5 libc has no localtime_r */
    if (now)
        tm = *now;
    else
        memset(&tm, 0, sizeof(tm));
    int year = tm.tm_year + 1900 - 2000;
    if (year < 0)
        year = 0;
    uint32_t date = (uint32_t)(year << 9 | (tm.tm_mon + 1) << 5 | tm.tm_mday);
    uint32_t tod = (uint32_t)(tm.tm_hour << 11 | tm.tm_min << 5 | tm.tm_sec / 2);
    return date << 16 | tod;
}

static void make_dirent(uint8_t *e, const char *name, uint8_t attr, uint32_t first, uint32_t size)
{
    size_t n = strlen(name);
    memset(e, 0xFF, FATX_DIRENT);
    e[0] = (uint8_t)n;
    e[1] = attr;
    memcpy(e + 2, name, n);
    put_le32(e + 44, first);
    put_le32(e + 48, size);
    uint32_t now = fatx_now();
    put_le32(e + 52, now);
    put_le32(e + 56, now);
    put_le32(e + 60, now);
}

/* Writes the tree's files whose paths start with prefix as a new directory
 * (sub-folders below it recursively); *first is its first cluster. */
static bool write_dir(XboxHdd *h, const SaveTree *tree, const char *prefix, int depth,
                      uint32_t *first, const char **why)
{
    if (depth > MAX_DEPTH)
    {
        *why = "folders nested too deep";
        return false;
    }
    size_t plen = strlen(prefix);
    size_t cap = 16, count = 0;
    uint8_t *ents = malloc(cap * FATX_DIRENT);
    bool ok = ents != NULL;
    char last_dir[FATX_NAME_MAX + 1] = "";
    for (int i = 0; ok && i < tree->count; ++i)
    {
        const char *path = tree->files[i].path;
        if (strncmp(path, prefix, plen) != 0)
            continue;
        const char *rest = path + plen;
        const char *sep = strpbrk(rest, "\\/");
        size_t n = sep ? (size_t)(sep - rest) : strlen(rest);
        if (n == 0 || n > FATX_NAME_MAX)
        {
            *why = n ? "a file name is too long for FATX" : "a bad file name";
            ok = false;
            break;
        }
        char name[FATX_NAME_MAX + 1];
        memcpy(name, rest, n);
        name[n] = 0;
        if (sep && strcmp(name, last_dir) == 0)
            continue; /* written already: the tree is sorted */
        if (count == cap)
        {
            uint8_t *grown = realloc(ents, cap * 2 * FATX_DIRENT);
            if (!grown)
            {
                ok = false;
                break;
            }
            ents = grown;
            cap *= 2;
        }
        uint32_t c = 0;
        if (sep)
        {
            char sub[256];
            snprintf(sub, sizeof(sub), "%.*s", (int)(sep - path + 1), path);
            ok = write_dir(h, tree, sub, depth + 1, &c, why);
            util_copy(last_dir, sizeof(last_dir), name);
            if (ok)
                make_dirent(ents + count++ * FATX_DIRENT, name, FATX_ATTR_DIR, c, 0);
        }
        else
        {
            const TreeFile *f = &tree->files[i];
            uint32_t need = (uint32_t)((f->size + h->cluster_bytes - 1) / h->cluster_bytes);
            ok = alloc_chain(h, need, &c) && write_chain(h, c, f->data, f->size);
            if (!ok)
                *why = "the Xbox disk is full";
            else
                make_dirent(ents + count++ * FATX_DIRENT, name, FATX_ATTR_FILE, c, f->size);
        }
    }
    if (ok)
    {
        /* the entries, then 0xFF: the end marker */
        size_t bytes = (count + 1) * FATX_DIRENT;
        uint32_t need = (uint32_t)((bytes + h->cluster_bytes - 1) / h->cluster_bytes);
        size_t total = (size_t)need * h->cluster_bytes;
        uint8_t *buf = malloc(total);
        ok = buf != NULL;
        if (ok)
        {
            memset(buf, 0xFF, total);
            memcpy(buf, ents, count * FATX_DIRENT);
            ok = alloc_chain(h, need, first) && write_chain(h, *first, buf, total);
            if (!ok)
                *why = "the Xbox disk is full";
            free(buf);
        }
    }
    if (!ok && !*why)
        *why = "out of memory";
    free(ents);
    return ok;
}

static bool sync_file(XboxHdd *h)
{
    return fflush(h->f) == 0 && fsync(fileno(h->f)) == 0;
}

bool xhdd_write_title(XboxHdd *h, const char *title_id, const SaveTree *tree, char *err,
                      size_t err_size)
{
    if (!h->rw)
    {
        util_copy(err, err_size, "the disk image is open read-only");
        return false;
    }
    Dirent udata;
    if (!find_entry(h, h->root, "UDATA", &udata) || !(udata.attr & FATX_ATTR_DIR))
    {
        util_copy(err, err_size, "no E:\\UDATA on the Xbox disk (start a game once)");
        return false;
    }
    /* the new save goes into free clusters first: until the UDATA entry
     * points at it, the old one is intact */
    const char *why = NULL;
    uint32_t first = 0;
    if (!write_dir(h, tree, "", 0, &first, &why) || !fat_flush(h))
    {
        util_copy(err, err_size, why ? why : "can't write the Xbox disk");
        return false;
    }
    size_t len;
    uint8_t *d = read_dir(h, udata.first, &len);
    if (!d)
    {
        util_copy(err, err_size, "can't read E:\\UDATA");
        return false;
    }
    size_t slot = SIZE_MAX, reuse = SIZE_MAX, i = 0;
    uint32_t old = 0;
    char name[FATX_NAME_MAX + 1];
    Dirent e;
    int r;
    for (; (r = dirent_at(d, len, i, &e)) >= 0; ++i)
    {
        if (r == 1 && util_ieq(e.name, title_id))
        {
            slot = i;
            old = e.first;
            util_copy(name, sizeof(name), e.name); /* keep its case */
            break;
        }
        if (r == 0 && d[i * FATX_DIRENT] == FATX_DELETED && reuse == SIZE_MAX)
            reuse = i;
    }
    bool ok = true;
    if (slot == SIZE_MAX)
    {
        /* XAPI names the folder in lowercase hex */
        for (int k = 0; k < 8; ++k)
            name[k] = (char)(title_id[k] >= 'A' && title_id[k] <= 'F' ? title_id[k] + 32 : title_id[k]);
        name[8] = 0;
        slot = reuse != SIZE_MAX ? reuse : i;
        if ((slot + 1) * FATX_DIRENT > len)
        {
            /* E:\UDATA is full: one more cluster on its chain */
            uint32_t last = udata.first, more = 0;
            for (uint32_t n = fat_next(h, last); n; n = fat_next(h, n))
                last = n;
            uint8_t *bigger = realloc(d, len + h->cluster_bytes);
            if (bigger)
                d = bigger;
            ok = bigger && alloc_chain(h, 1, &more);
            if (ok)
            {
                memset(d + len, 0xFF, h->cluster_bytes);
                fat_set(h, last, more);
                len += h->cluster_bytes;
            }
        }
    }
    if (ok)
    {
        uint8_t mark = d[slot * FATX_DIRENT];
        make_dirent(d + slot * FATX_DIRENT, name, FATX_ATTR_DIR, first, 0);
        /* a slot taken from the end marker moves the marker along */
        if ((mark == 0x00 || mark == 0xFF) && (slot + 2) * FATX_DIRENT <= len)
            d[(slot + 1) * FATX_DIRENT] = 0xFF;
        ok = fat_flush(h) && write_chain(h, udata.first, d, len) && sync_file(h);
    }
    free(d);
    if (ok && old)
    {
        free_tree(h, old, 0);
        ok = fat_flush(h) && sync_file(h);
    }
    if (!ok)
        util_copy(err, err_size, "can't write the Xbox disk");
    return ok;
}

bool xhdd_backup(const char *path, const char *backup)
{
    char tmp[1024];
    snprintf(tmp, sizeof(tmp), "%s.tmp", backup);
    FILE *in = fopen(path, "rb");
    FILE *out = in ? fopen(tmp, "wb") : NULL;
    uint8_t *buf = malloc(4u << 20);
    bool ok = in && out && buf;
    size_t n;
    while (ok && (n = fread(buf, 1, 4u << 20, in)) > 0)
        ok = fwrite(buf, 1, n, out) == n;
    if (in && ferror(in))
        ok = false;
    free(buf);
    if (in)
        fclose(in);
    if (out && fclose(out) != 0)
        ok = false;
    if (ok)
    {
        chmod(tmp, 0666);
        ok = rename(tmp, backup) == 0;
    }
    if (!ok)
        remove(tmp);
    return ok;
}

void xhdd_image_path(const char *xemu_dir, char *out, size_t size)
{
    snprintf(out, size, "%s/xbox_hdd.qcow2", xemu_dir);
    char toml[1024];
    snprintf(toml, sizeof(toml), "%s/xemu.toml", xemu_dir);
    size_t len;
    char *text = (char *)util_read_file(toml, &len);
    if (!text)
        return;
    text[len] = 0;
    char *save = NULL;
    for (char *line = strtok_r(text, "\r\n", &save); line; line = strtok_r(NULL, "\r\n", &save))
    {
        while (*line == ' ' || *line == '\t')
            ++line;
        if (strncmp(line, "hdd_path", 8) != 0)
            continue;
        char *q = strpbrk(line + 8, "'\"");
        if (!q || !strchr(line + 8, '='))
            continue;
        char *end = strchr(q + 1, *q);
        if (end && end > q + 1)
        {
            *end = 0;
            util_copy(out, size, q + 1);
        }
        break;
    }
    free(text);
}
