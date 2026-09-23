/*
 * vmcfs.c — GameCube memory-card image filesystem (see vmcfs.h).
 *
 * Format follows Dolphin/mymc and the server's gc_cards.py: big-endian u16
 * fields, additive word checksums with 0xFFFF folded to 0.
 */

#include "vmcfs.h"
#include "gcsaves.h"   /* saves_title_id_from_gamecode */

#include <stdio.h>
#include <string.h>
#include <sys/stat.h>

#define DIR1_OFF  0x2000
#define DIR2_OFF  0x4000
#define BAM1_OFF  0x6000
#define BAM2_OFF  0x8000
#define DENTRY    64
#define FST_BLOCKS 5

static uint16_t rd16(const uint8_t *p) { return (uint16_t)((p[0] << 8) | p[1]); }
static void wr16(uint8_t *p, uint16_t v) { p[0] = (uint8_t)(v >> 8); p[1] = (uint8_t)v; }
static uint32_t rd32(const uint8_t *p) {
    return ((uint32_t)p[0] << 24) | ((uint32_t)p[1] << 16) |
           ((uint32_t)p[2] << 8)  | p[3];
}

/* Printable copy of a 32-byte save comment (line 1 = game title). */
static void copy_comment(const uint8_t *src, char *out, size_t out_size) {
    size_t j = 0;
    for (size_t i = 0; i < 32 && j + 1 < out_size; i++) {
        unsigned char ch = src[i];
        if (ch == 0) break;
        if (ch >= 0x20 && ch < 0x7F) out[j++] = (char)ch;
    }
    while (j > 0 && out[j - 1] == ' ') j--;
    out[j] = '\0';
}

static void checksums_be(const uint8_t *data, uint32_t len,
                         uint16_t *csum, uint16_t *inv) {
    uint32_t c = 0, i = 0;
    for (uint32_t off = 0; off + 1 < len; off += 2) {
        uint16_t w = rd16(data + off);
        c = (c + w) & 0xFFFF;
        i = (i + (w ^ 0xFFFF)) & 0xFFFF;
    }
    if (c == 0xFFFF) c = 0;
    if (i == 0xFFFF) i = 0;
    *csum = (uint16_t)c;
    *inv  = (uint16_t)i;
}

static bool entry_free(const uint8_t *e) {
    return memcmp(e, "\xff\xff\xff\xff", 4) == 0 ||
           memcmp(e, "\x00\x00\x00\x00", 4) == 0;
}

/* BAM chain entry for absolute block B. */
static uint16_t bam_get(const VmcfsCard *c, uint16_t block) {
    uint32_t off = 0x0A + (uint32_t)(block - FST_BLOCKS) * 2;
    if (block < FST_BLOCKS || off + 1 >= VMCFS_BLOCK) return 0xFFFF;
    return rd16(c->bam + off);
}

static void bam_set(VmcfsCard *c, uint16_t block, uint16_t v) {
    uint32_t off = 0x0A + (uint32_t)(block - FST_BLOCKS) * 2;
    if (block < FST_BLOCKS || off + 1 >= VMCFS_BLOCK) return;
    wr16(c->bam + off, v);
}

static bool read_block_at(const char *path, uint32_t off, uint8_t *out, FILE *fp) {
    if (fseek(fp, (long)off, SEEK_SET) != 0) return false;
    return fread(out, 1, VMCFS_BLOCK, fp) == VMCFS_BLOCK;
}

static void parse_dir(VmcfsCard *c) {
    c->count = 0;
    for (int i = 0; i < VMCFS_MAX_SAVES; i++) {
        const uint8_t *e = c->dir + i * DENTRY;
        if (entry_free(e)) continue;
        uint16_t first = rd16(e + 0x36);
        uint16_t count = rd16(e + 0x38);
        if (count == 0 || first < FST_BLOCKS || first + count > c->total_blocks)
            continue;
        VmcfsSave *s = &c->saves[c->count];
        memset(s, 0, sizeof(*s));
        memcpy(s->gamecode, e, 4);      s->gamecode[4] = '\0';
        memcpy(s->company,  e + 4, 2);  s->company[2]  = '\0';
        memcpy(s->filename, e + 8, 32); s->filename[32] = '\0';
        s->dir_index   = i;
        s->first_block = first;
        s->blocks      = count;
        saves_title_id_from_gamecode(s->gamecode, s->title_id, sizeof(s->title_id));
        c->count++;
    }
}

bool vmcfs_open(VmcfsCard *c, const char *path) {
    if (!c || !path) return false;
    memset(c->last_error, 0, sizeof(c->last_error));
    c->count = 0;

    strncpy(c->path, path, sizeof(c->path) - 1);
    c->path[sizeof(c->path) - 1] = '\0';
    const char *slash = strrchr(path, '/');
    strncpy(c->filename, slash ? slash + 1 : path, sizeof(c->filename) - 1);
    c->filename[sizeof(c->filename) - 1] = '\0';

    struct stat st;
    if (stat(path, &st) != 0) {
        snprintf(c->last_error, sizeof(c->last_error), "stat failed");
        return false;
    }
    c->card_size = (uint32_t)st.st_size;
    if (c->card_size < BAM2_OFF + VMCFS_BLOCK || (c->card_size % VMCFS_BLOCK) != 0) {
        snprintf(c->last_error, sizeof(c->last_error), "not a card image");
        return false;
    }
    c->total_blocks = (uint16_t)(c->card_size / VMCFS_BLOCK);

    FILE *fp = fopen(path, "rb");
    if (!fp) {
        snprintf(c->last_error, sizeof(c->last_error), "open failed");
        return false;
    }

    /* Pick the directory / BAM copy with the higher update counter. */
    static uint8_t alt[VMCFS_BLOCK];
    bool ok = read_block_at(path, DIR1_OFF, c->dir, fp) &&
              read_block_at(path, DIR2_OFF, alt,    fp);
    if (ok && rd16(alt + 0x1FFA) > rd16(c->dir + 0x1FFA))
        memcpy(c->dir, alt, VMCFS_BLOCK);

    ok = ok && read_block_at(path, BAM1_OFF, c->bam, fp) &&
               read_block_at(path, BAM2_OFF, alt,    fp);
    if (ok && rd16(alt + 4) > rd16(c->bam + 4))
        memcpy(c->bam, alt, VMCFS_BLOCK);

    if (!ok) {
        fclose(fp);
        snprintf(c->last_error, sizeof(c->last_error), "read failed");
        return false;
    }

    parse_dir(c);

    /* Fill display names from each save's comment field (32-byte game title
     * at comment_addr inside the save data) — what a real card shows. */
    for (int i = 0; i < c->count; i++) {
        VmcfsSave *s = &c->saves[i];
        uint32_t cmt = rd32(c->dir + s->dir_index * DENTRY + 0x3C);
        if (cmt == 0xFFFFFFFF) continue;
        uint32_t hop = cmt / VMCFS_BLOCK;
        uint32_t off = cmt % VMCFS_BLOCK;
        if (hop >= s->blocks || off + 32 > VMCFS_BLOCK) continue;
        uint16_t block = s->first_block;
        for (uint32_t h = 0; h < hop; h++) block = bam_get(c, block);
        if (block < FST_BLOCKS || block >= c->total_blocks) continue;
        uint8_t cbuf[32];
        if (fseek(fp, (long)block * VMCFS_BLOCK + (long)off, SEEK_SET) == 0 &&
            fread(cbuf, 1, 32, fp) == 32)
            copy_comment(cbuf, s->name, sizeof(s->name));
    }

    fclose(fp);
    return true;
}

int vmcfs_read_gci(VmcfsCard *c, int idx, uint8_t *out, uint32_t out_max) {
    if (!c || idx < 0 || idx >= c->count) return -1;
    const VmcfsSave *s = &c->saves[idx];
    uint32_t need = (uint32_t)DENTRY + (uint32_t)s->blocks * VMCFS_BLOCK;
    if (need > out_max) return -2;

    memcpy(out, c->dir + s->dir_index * DENTRY, DENTRY);

    FILE *fp = fopen(c->path, "rb");
    if (!fp) return -3;

    uint16_t block = s->first_block;
    for (int i = 0; i < s->blocks; i++) {
        if (block < FST_BLOCKS || block >= c->total_blocks) { fclose(fp); return -4; }
        if (!read_block_at(c->path, (uint32_t)block * VMCFS_BLOCK,
                           out + DENTRY + (uint32_t)i * VMCFS_BLOCK, fp)) {
            fclose(fp);
            return -5;
        }
        block = bam_get(c, block);   /* 0xFFFF terminates the last block */
    }
    fclose(fp);
    return (int)need;
}

/* Persist the in-RAM dir + BAM to both copies with bumped counters. */
static bool flush_meta(VmcfsCard *c, FILE *fp) {
    uint16_t csum, inv;

    wr16(c->dir + 0x1FFA, (uint16_t)(rd16(c->dir + 0x1FFA) + 1));
    checksums_be(c->dir, 0x1FFC, &csum, &inv);
    wr16(c->dir + 0x1FFC, csum);
    wr16(c->dir + 0x1FFE, inv);

    wr16(c->bam + 4, (uint16_t)(rd16(c->bam + 4) + 1));
    checksums_be(c->bam + 4, VMCFS_BLOCK - 4, &csum, &inv);
    wr16(c->bam + 0, csum);
    wr16(c->bam + 2, inv);

    return fseek(fp, DIR1_OFF, SEEK_SET) == 0 &&
           fwrite(c->dir, 1, VMCFS_BLOCK, fp) == VMCFS_BLOCK &&
           fseek(fp, DIR2_OFF, SEEK_SET) == 0 &&
           fwrite(c->dir, 1, VMCFS_BLOCK, fp) == VMCFS_BLOCK &&
           fseek(fp, BAM1_OFF, SEEK_SET) == 0 &&
           fwrite(c->bam, 1, VMCFS_BLOCK, fp) == VMCFS_BLOCK &&
           fseek(fp, BAM2_OFF, SEEK_SET) == 0 &&
           fwrite(c->bam, 1, VMCFS_BLOCK, fp) == VMCFS_BLOCK;
}

static bool write_chain_block(FILE *fp, uint16_t block, const uint8_t *data) {
    return fseek(fp, (long)block * VMCFS_BLOCK, SEEK_SET) == 0 &&
           fwrite(data, 1, VMCFS_BLOCK, fp) == VMCFS_BLOCK;
}

int vmcfs_write_gci(VmcfsCard *c, const uint8_t *gci, uint32_t len,
                    char *msg, size_t msg_size) {
    if (!c || !gci || len < DENTRY + VMCFS_BLOCK) {
        snprintf(msg, msg_size, "bad GCI");
        return -1;
    }
    uint16_t want = rd16(gci + 0x38);
    if ((uint32_t)DENTRY + (uint32_t)want * VMCFS_BLOCK != len || want == 0) {
        snprintf(msg, msg_size, "GCI size mismatch");
        return -1;
    }

    /* Existing entry for this game (gamecode + company)? */
    int slot = -1;
    for (int i = 0; i < VMCFS_MAX_SAVES; i++) {
        const uint8_t *e = c->dir + i * DENTRY;
        if (entry_free(e)) continue;
        if (memcmp(e, gci, 6) == 0) { slot = i; break; }
    }

    FILE *fp = fopen(c->path, "r+b");
    if (!fp) { snprintf(msg, msg_size, "open for write failed"); return -2; }

    if (slot >= 0) {
        uint8_t *e = c->dir + slot * DENTRY;
        uint16_t first = rd16(e + 0x36);
        uint16_t count = rd16(e + 0x38);

        if (count == want) {
            /* In-place overwrite following the existing chain. */
            uint16_t block = first;
            for (int i = 0; i < count; i++) {
                if (block < FST_BLOCKS || block >= c->total_blocks ||
                    !write_chain_block(fp, block, gci + DENTRY + (uint32_t)i * VMCFS_BLOCK)) {
                    fclose(fp);
                    snprintf(msg, msg_size, "data write failed");
                    return -3;
                }
                block = bam_get(c, block);
            }
            /* Refresh the entry's metadata, keep this card's allocation. */
            memcpy(e, gci, DENTRY);
            wr16(e + 0x36, first);
            wr16(e + 0x38, count);
            bool ok = flush_meta(c, fp);
            fclose(fp);
            if (!ok) { snprintf(msg, msg_size, "meta write failed"); return -4; }
            parse_dir(c);
            snprintf(msg, msg_size, "Updated %.4s in %s", gci, c->filename);
            return 0;
        }

        /* Size changed: free the old chain, then fall through to allocate. */
        uint16_t block = first;
        uint16_t freed = 0;
        for (int i = 0; i < count && block >= FST_BLOCKS && block < c->total_blocks; i++) {
            uint16_t next = bam_get(c, block);
            bam_set(c, block, 0);
            freed++;
            block = next;
        }
        wr16(c->bam + 6, (uint16_t)(rd16(c->bam + 6) + freed));
        memset(c->dir + slot * DENTRY, 0xFF, DENTRY);
    }

    /* Allocate ``want`` free blocks. */
    uint16_t chain[256];
    if (want > 255) { fclose(fp); snprintf(msg, msg_size, "save too large"); return -5; }
    int got = 0;
    for (uint16_t b = FST_BLOCKS; b < c->total_blocks && got < want; b++)
        if (bam_get(c, b) == 0) chain[got++] = b;
    if (got < want) {
        fclose(fp);
        snprintf(msg, msg_size, "card full (need %u, free %d)", want, got);
        return -6;
    }
    for (int i = 0; i < want; i++) {
        bam_set(c, chain[i], (i == want - 1) ? 0xFFFF : chain[i + 1]);
        if (!write_chain_block(fp, chain[i], gci + DENTRY + (uint32_t)i * VMCFS_BLOCK)) {
            fclose(fp);
            snprintf(msg, msg_size, "data write failed");
            return -3;
        }
    }
    wr16(c->bam + 6, (uint16_t)(rd16(c->bam + 6) - want));
    wr16(c->bam + 8, chain[want - 1]);

    /* Directory slot: reuse the freed one, else first free. */
    if (slot < 0) {
        for (int i = 0; i < VMCFS_MAX_SAVES; i++)
            if (entry_free(c->dir + i * DENTRY)) { slot = i; break; }
    }
    if (slot < 0) {
        fclose(fp);
        snprintf(msg, msg_size, "directory full");
        return -7;
    }
    uint8_t *e = c->dir + slot * DENTRY;
    memcpy(e, gci, DENTRY);
    wr16(e + 0x36, chain[0]);
    wr16(e + 0x38, want);

    bool ok = flush_meta(c, fp);
    fclose(fp);
    if (!ok) { snprintf(msg, msg_size, "meta write failed"); return -4; }
    parse_dir(c);
    snprintf(msg, msg_size, "Wrote %.4s to %s (%u blk)", gci, c->filename, want);
    return 0;
}
