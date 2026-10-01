#include "catalog_cache.h"
#include <stdlib.h>
#include <string.h>

static const char MAGIC[4] = { 'G', 'S', 'D', 'C' };

#define FLAG_TITLE_ONLY 1
#define FLAG_EXTRACT_NDS 2
#define FLAG_TRUNCATED 4

#define IOBUF_SIZE 4096

// ---------------------------------------------------------------------------
// Little-endian helpers
// ---------------------------------------------------------------------------

static void put16(uint8_t *p, uint16_t v) {
    p[0] = (uint8_t)v;
    p[1] = (uint8_t)(v >> 8);
}

static void put32(uint8_t *p, uint32_t v) {
    p[0] = (uint8_t)v;
    p[1] = (uint8_t)(v >> 8);
    p[2] = (uint8_t)(v >> 16);
    p[3] = (uint8_t)(v >> 24);
}

static uint16_t get16(const uint8_t *p) {
    return (uint16_t)(p[0] | (p[1] << 8));
}

static uint32_t get32(const uint8_t *p) {
    return (uint32_t)p[0] | ((uint32_t)p[1] << 8) | ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24);
}

// ---------------------------------------------------------------------------
// Reading
// ---------------------------------------------------------------------------

typedef struct {
    char system[8];
    char fingerprint[CAT_FP_LEN];
    uint32_t count, index_offset;
} Header;

static bool read_header(FILE *f, Header *h) {
    uint8_t buf[CC_HEADER_SIZE];
    if (fseek(f, 0, SEEK_SET) != 0 || fread(buf, 1, sizeof(buf), f) != sizeof(buf)) return false;
    if (memcmp(buf, MAGIC, 4) != 0) return false;
    if (get16(buf + 4) != CC_VERSION || get16(buf + 6) != CC_HEADER_SIZE) return false;
    h->count = get32(buf + 8);
    h->index_offset = get32(buf + 12);
    memcpy(h->system, buf + 16, 8);
    h->system[7] = '\0';
    memcpy(h->fingerprint, buf + 24, CAT_FP_LEN);
    h->fingerprint[CAT_FP_LEN - 1] = '\0';
    if (h->count > CC_MAX_COUNT || h->index_offset < CC_HEADER_SIZE) return false;
    // The offset table must end the file exactly
    if (fseek(f, 0, SEEK_END) != 0) return false;
    long size = ftell(f);
    if (size < 0 || (uint32_t)size != h->index_offset + h->count * 4u) return false;
    return true;
}

bool cc_peek(const char *path, char system[8], char fingerprint[CAT_FP_LEN], uint32_t *count) {
    FILE *f = fopen(path, "rb");
    if (!f) return false;
    Header h;
    bool ok = read_header(f, &h);
    fclose(f);
    if (!ok) return false;
    if (system) memcpy(system, h.system, 8);
    if (fingerprint) memcpy(fingerprint, h.fingerprint, CAT_FP_LEN);
    if (count) *count = h.count;
    return true;
}

void cc_close(CatCache *c) {
    if (c->f) fclose(c->f);
    free(c->refs);
    free(c->iobuf);
    memset(c, 0, sizeof(*c));
}

bool cc_open(CatCache *c, const char *path) {
    memset(c, 0, sizeof(*c));
    c->f = fopen(path, "rb");
    if (!c->f) return false;
    c->iobuf = malloc(IOBUF_SIZE);
    if (c->iobuf) setvbuf(c->f, c->iobuf, _IOFBF, IOBUF_SIZE);

    Header h;
    if (!read_header(c->f, &h)) goto fail;
    memcpy(c->system, h.system, sizeof(c->system));
    memcpy(c->fingerprint, h.fingerprint, sizeof(c->fingerprint));
    c->count = h.count;
    c->index_offset = h.index_offset;
    if (c->count) {
        c->refs = malloc(c->count * sizeof(uint32_t));
        if (!c->refs) goto fail;
        if (fseek(c->f, (long)c->index_offset, SEEK_SET) != 0) goto fail;
        // Read in small chunks through a byte buffer: the file is LE whatever the CPU
        uint8_t buf[256];
        uint32_t done = 0;
        while (done < c->count) {
            uint32_t n = c->count - done;
            if (n > sizeof(buf) / 4) n = sizeof(buf) / 4;
            if (fread(buf, 4, n, c->f) != n) goto fail;
            for (uint32_t i = 0; i < n; i++) {
                uint32_t ref = get32(buf + i * 4);
                if (CC_OFFSET(ref) < CC_HEADER_SIZE || CC_OFFSET(ref) + CC_RECORD_HEAD > c->index_offset)
                    goto fail;
                c->refs[done + i] = ref;
            }
            done += n;
        }
    }
    return true;

fail:
    cc_close(c);
    return false;
}

// Read the record at the current file position
static bool read_record(FILE *f, uint32_t limit, CatEntry *e) {
    uint8_t head[CC_RECORD_HEAD];
    if (fread(head, 1, sizeof(head), f) != sizeof(head)) return false;
    memset(e, 0, sizeof(*e));
    e->size = get32(head);
    e->ra_game_id = (int32_t)get32(head + 4);
    e->ra_achievements = (int16_t)get16(head + 8);
    uint8_t flags = head[10];
    e->ra_title_only = (flags & FLAG_TITLE_ONLY) != 0;
    e->can_extract_nds = (flags & FLAG_EXTRACT_NDS) != 0;
    e->truncated = (flags & FLAG_TRUNCATED) != 0;
    size_t lid = head[11], lname = head[12], lfile = head[13];
    if (lid >= sizeof(e->rom_id) || lname >= sizeof(e->name) || lfile >= sizeof(e->filename)) return false;
    long here = ftell(f);
    if (here < 0 || (uint32_t)here + lid + lname + lfile > limit) return false;
    if (fread(e->rom_id, 1, lid, f) != lid) return false;
    if (fread(e->name, 1, lname, f) != lname) return false;
    if (fread(e->filename, 1, lfile, f) != lfile) return false;
    e->rom_id[lid] = e->name[lname] = e->filename[lfile] = '\0';
    return true;
}

bool cc_read(const CatCache *c, uint32_t ref, CatEntry *e) {
    if (!c->f) return false;
    if (fseek(c->f, (long)CC_OFFSET(ref), SEEK_SET) != 0) return false;
    return read_record(c->f, c->index_offset, e);
}

int cc_filter(const CatCache *c, bool ra_only, const char *search, uint32_t **out, CatEntry *scratch) {
    *out = NULL;
    if (!c->f) return -1;
    bool searching = search && search[0];
    uint32_t *list = NULL;
    int n = 0, cap = 0;
    // Records are stored in table order, so a search reads the file once,
    // front to back (no seeks)
    if (searching && c->count && fseek(c->f, (long)CC_OFFSET(c->refs[0]), SEEK_SET) != 0) return -1;
    for (uint32_t i = 0; i < c->count; i++) {
        uint32_t ref = c->refs[i];
        bool keep = !ra_only || (ref & CC_RA_BIT);
        if (searching) {
            long here = ftell(c->f);
            if (here < 0 || (uint32_t)here != CC_OFFSET(ref)) {
                if (fseek(c->f, (long)CC_OFFSET(ref), SEEK_SET) != 0) goto fail;
            }
            if (!read_record(c->f, c->index_offset, scratch)) goto fail;
            keep = keep && cat_entry_matches(scratch, search);
        }
        if (!keep) continue;
        if (n == cap) {
            int grown_cap = cap ? cap * 2 : 256;
            uint32_t *grown = realloc(list, (size_t)grown_cap * sizeof(uint32_t));
            if (!grown) goto fail;
            list = grown;
            cap = grown_cap;
        }
        list[n++] = ref;
    }
    *out = list;
    return n;

fail:
    free(list);
    return -1;
}

// ---------------------------------------------------------------------------
// Writing
// ---------------------------------------------------------------------------

bool ccw_begin(CatCacheWriter *w, const char *part_path, const char *system) {
    memset(w, 0, sizeof(*w));
    snprintf(w->part, sizeof(w->part), "%s", part_path);
    snprintf(w->system, sizeof(w->system), "%s", system);
    remove(part_path);
    w->f = fopen(part_path, "wb");
    if (!w->f) return false;
    // Placeholder header (zero magic): only ccw_finish makes the file valid
    uint8_t zero[CC_HEADER_SIZE] = { 0 };
    if (fwrite(zero, 1, sizeof(zero), w->f) != sizeof(zero)) {
        ccw_abort(w);
        return false;
    }
    w->pos = CC_HEADER_SIZE;
    return true;
}

bool ccw_add(CatCacheWriter *w, const CatEntry *e) {
    if (!w->f || w->failed) return false;
    if (w->count >= CC_MAX_COUNT) {
        w->failed = true;
        return false;
    }
    if (w->count == w->cap) {
        uint32_t cap = w->cap ? w->cap * 2 : 512;
        uint32_t *grown = realloc(w->refs, cap * sizeof(uint32_t));
        if (!grown) {
            w->failed = true;
            return false;
        }
        w->refs = grown;
        w->cap = cap;
    }
    size_t lid = strlen(e->rom_id), lname = strlen(e->name), lfile = strlen(e->filename);
    if (lid > 255) lid = 255;
    if (lname > 255) lname = 255;
    if (lfile > 255) lfile = 255;

    uint8_t head[CC_RECORD_HEAD] = { 0 };
    put32(head, e->size);
    put32(head + 4, (uint32_t)e->ra_game_id);
    int ach = e->ra_achievements;
    if (ach > 32767) ach = 32767;
    if (ach < -32768) ach = -32768;
    put16(head + 8, (uint16_t)(int16_t)ach);
    head[10] = (uint8_t)((e->ra_title_only ? FLAG_TITLE_ONLY : 0) | (e->can_extract_nds ? FLAG_EXTRACT_NDS : 0) |
                         (e->truncated ? FLAG_TRUNCATED : 0));
    head[11] = (uint8_t)lid;
    head[12] = (uint8_t)lname;
    head[13] = (uint8_t)lfile;
    if (fwrite(head, 1, sizeof(head), w->f) != sizeof(head) || fwrite(e->rom_id, 1, lid, w->f) != lid ||
        fwrite(e->name, 1, lname, w->f) != lname || fwrite(e->filename, 1, lfile, w->f) != lfile) {
        w->failed = true;
        return false;
    }
    w->refs[w->count++] = w->pos | (cat_entry_has_ra(e) ? CC_RA_BIT : 0);
    w->pos += (uint32_t)(CC_RECORD_HEAD + lid + lname + lfile);
    if (w->pos & CC_RA_BIT) w->failed = true;  // 2 GB: not a DS catalog
    return !w->failed;
}

void ccw_abort(CatCacheWriter *w) {
    if (w->f) fclose(w->f);
    if (w->part[0]) remove(w->part);
    free(w->refs);
    memset(w, 0, sizeof(*w));
}

bool ccw_finish(CatCacheWriter *w, const char *fingerprint, const char *final_path) {
    if (!w->f || w->failed) {
        ccw_abort(w);
        return false;
    }
    bool ok = true;
    uint8_t buf[256];
    uint32_t done = 0;
    while (ok && done < w->count) {
        uint32_t n = w->count - done;
        if (n > sizeof(buf) / 4) n = sizeof(buf) / 4;
        for (uint32_t i = 0; i < n; i++) put32(buf + i * 4, w->refs[done + i]);
        ok = fwrite(buf, 4, n, w->f) == n;
        done += n;
    }

    uint8_t head[CC_HEADER_SIZE] = { 0 };
    memcpy(head, MAGIC, 4);
    put16(head + 4, CC_VERSION);
    put16(head + 6, CC_HEADER_SIZE);
    put32(head + 8, w->count);
    put32(head + 12, w->pos);
    snprintf((char *)head + 16, 8, "%s", w->system);
    snprintf((char *)head + 24, CAT_FP_LEN, "%s", fingerprint ? fingerprint : "");
    ok = ok && fseek(w->f, 0, SEEK_SET) == 0 && fwrite(head, 1, sizeof(head), w->f) == sizeof(head);
    ok = (fclose(w->f) == 0) && ok;
    w->f = NULL;
    if (ok) {
        // FAT can't rename over an existing file
        remove(final_path);
        ok = rename(w->part, final_path) == 0;
    }
    if (!ok) remove(w->part);
    free(w->refs);
    memset(w, 0, sizeof(*w));
    return ok;
}
