/*
 * catcache.c — on-disk ROM catalog cache keyed by the server's per-system
 * fingerprints.  See catcache.h for the file format.
 *
 * Strategy ported from the MiSTer client's catalogcache.py (same
 * project): fingerprint per system, refetch only what moved, keep the
 * last copy when the server is unreachable.
 */

#include "catcache.h"

#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static const char MAGIC[4] = { 'G', 'S', 'C', 'C' };
static char g_path[256] = CATALOG_CACHE_FILE;

void catcache_set_path(const char *path) {
    snprintf(g_path, sizeof(g_path), "%s", path ? path : CATALOG_CACHE_FILE);
}

/* ------------------------------------------------------------------ */
/* Fingerprints JSON                                                   */
/* ------------------------------------------------------------------ */

static const char *ws(const char *p, const char *end) {
    while (p < end && (*p == ' ' || *p == '\t' || *p == '\n' || *p == '\r')) p++;
    return p;
}

/* Copy a JSON string at p (pointing at the opening quote); returns the
 * position past the closing quote, or NULL. */
static const char *read_jstr(const char *p, const char *end, char *out, size_t out_size) {
    if (p >= end || *p != '"') return NULL;
    p++;
    size_t n = 0;
    while (p < end && *p != '"') {
        char c = *p;
        if (c == '\\' && p + 1 < end) { p++; c = *p; }
        if (out && n + 1 < out_size) out[n++] = c;
        p++;
    }
    if (p >= end) return NULL;
    if (out && out_size) out[n] = '\0';
    return p + 1;
}

/* Skip one JSON value (string, number, literal, object or array). */
static const char *skip_value(const char *p, const char *end) {
    p = ws(p, end);
    if (p >= end) return NULL;
    if (*p == '"') return read_jstr(p, end, NULL, 0);
    if (*p == '{' || *p == '[') {
        int depth = 0;
        while (p < end) {
            if (*p == '"') { p = read_jstr(p, end, NULL, 0); if (!p) return NULL; continue; }
            if (*p == '{' || *p == '[') depth++;
            else if (*p == '}' || *p == ']') { depth--; if (depth == 0) return p + 1; }
            p++;
        }
        return NULL;
    }
    while (p < end && *p != ',' && *p != '}' && *p != ']') p++;
    return p;
}

/* Parse {"fingerprint": "...", "count": N} for one system. */
static const char *parse_fp_object(const char *p, const char *end, CatFingerprint *fp) {
    p = ws(p, end);
    if (p >= end || *p != '{') return skip_value(p, end);
    p++;
    while (p < end) {
        p = ws(p, end);
        if (p < end && *p == ',') { p++; continue; }
        if (p < end && *p == '}') return p + 1;
        char key[32];
        p = read_jstr(p, end, key, sizeof(key));
        if (!p) return NULL;
        p = ws(p, end);
        if (p >= end || *p != ':') return NULL;
        p = ws(p + 1, end);
        if (strcmp(key, "fingerprint") == 0 && p < end && *p == '"') {
            p = read_jstr(p, end, fp->fingerprint, sizeof(fp->fingerprint));
        } else if (strcmp(key, "count") == 0) {
            fp->count = atoi(p);
            p = skip_value(p, end);
        } else {
            p = skip_value(p, end);
        }
        if (!p) return NULL;
    }
    return NULL;
}

bool catcache_parse_fingerprints(const char *json, size_t len, CatFingerprints *out) {
    if (!json || !out) return false;
    memset(out, 0, sizeof(*out));
    const char *end = json + len;
    const char *p = ws(json, end);
    if (p >= end || *p != '{') return false;
    p++;
    /* Top level: find "systems" */
    while (p < end) {
        p = ws(p, end);
        if (p < end && *p == ',') { p++; continue; }
        if (p >= end || *p == '}') return false;
        char key[32];
        p = read_jstr(p, end, key, sizeof(key));
        if (!p) return false;
        p = ws(p, end);
        if (p >= end || *p != ':') return false;
        p = ws(p + 1, end);
        if (strcmp(key, "systems") != 0) {
            p = skip_value(p, end);
            if (!p) return false;
            continue;
        }
        if (p >= end || *p != '{') return false;
        p++;
        while (p < end) {
            p = ws(p, end);
            if (p < end && *p == ',') { p++; continue; }
            if (p < end && *p == '}') return true;
            char sys[64];
            p = read_jstr(p, end, sys, sizeof(sys));
            if (!p) return false;
            p = ws(p, end);
            if (p >= end || *p != ':') return false;
            p = ws(p + 1, end);
            CatFingerprint tmp;
            memset(&tmp, 0, sizeof(tmp));
            p = parse_fp_object(p, end, &tmp);
            if (!p) return false;
            if (out->count < CATCACHE_MAX_SYSTEMS && sys[0] &&
                strlen(sys) < CATCACHE_SYSTEM_LEN) {
                memcpy(tmp.system, sys, strlen(sys) + 1);
                out->items[out->count++] = tmp;
            }
        }
        return false;
    }
    return false;
}

const CatFingerprint *catcache_find_fp(const CatFingerprints *fps, const char *system) {
    if (!fps || !system) return NULL;
    for (int i = 0; i < fps->count; i++)
        if (strcmp(fps->items[i].system, system) == 0) return &fps->items[i];
    return NULL;
}

/* ------------------------------------------------------------------ */
/* Binary helpers                                                      */
/* ------------------------------------------------------------------ */

static bool put_u8(FILE *f, unsigned v) { return fputc((int)(v & 0xFF), f) != EOF; }

static bool put_u16(FILE *f, unsigned v) {
    uint8_t b[2] = { (uint8_t)v, (uint8_t)(v >> 8) };
    return fwrite(b, 1, 2, f) == 2;
}

static bool put_u32(FILE *f, uint32_t v) {
    uint8_t b[4] = { (uint8_t)v, (uint8_t)(v >> 8), (uint8_t)(v >> 16), (uint8_t)(v >> 24) };
    return fwrite(b, 1, 4, f) == 4;
}

static bool put_u64(FILE *f, uint64_t v) {
    return put_u32(f, (uint32_t)v) && put_u32(f, (uint32_t)(v >> 32));
}

static size_t str_len255(const char *s) {
    size_t n = strlen(s);
    return n > 255 ? 255 : n;
}

static bool put_str(FILE *f, const char *s) {
    size_t n = str_len255(s);
    return put_u8(f, (unsigned)n) && fwrite(s, 1, n, f) == n;
}

static bool get_u8(FILE *f, unsigned *v) {
    int c = fgetc(f);
    if (c == EOF) return false;
    *v = (unsigned)c;
    return true;
}

static bool get_u16(FILE *f, unsigned *v) {
    uint8_t b[2];
    if (fread(b, 1, 2, f) != 2) return false;
    *v = (unsigned)b[0] | ((unsigned)b[1] << 8);
    return true;
}

static bool get_u32(FILE *f, uint32_t *v) {
    uint8_t b[4];
    if (fread(b, 1, 4, f) != 4) return false;
    *v = (uint32_t)b[0] | ((uint32_t)b[1] << 8) | ((uint32_t)b[2] << 16) |
         ((uint32_t)b[3] << 24);
    return true;
}

static bool get_u64(FILE *f, uint64_t *v) {
    uint32_t lo, hi;
    if (!get_u32(f, &lo) || !get_u32(f, &hi)) return false;
    *v = (uint64_t)lo | ((uint64_t)hi << 32);
    return true;
}

/* Read a length-prefixed string, truncating to out_size - 1. */
static bool get_str(FILE *f, char *out, size_t out_size) {
    unsigned n;
    if (!get_u8(f, &n)) return false;
    char buf[256];
    if (n && fread(buf, 1, n, f) != n) return false;
    size_t keep = n < out_size ? n : out_size - 1;
    memcpy(out, buf, keep);
    out[keep] = '\0';
    return true;
}

static uint32_t row_bytes(const RomEntry *e) {
    return (uint32_t)(1 + str_len255(e->rom_id) + 1 + str_len255(e->filename) +
                      1 + str_len255(e->name) + 1 + str_len255(e->system) +
                      1 + str_len255(e->title_id) + 8 + 1 + 2 +
                      1 + str_len255(e->extract_format) + 1 + 1);
}

static bool put_row(FILE *f, const RomEntry *e) {
    return put_str(f, e->rom_id) && put_str(f, e->filename) && put_str(f, e->name) &&
           put_str(f, e->system) && put_str(f, e->title_id) && put_u64(f, e->size) &&
           put_u8(f, e->is_bundle ? 1 : 0) &&
           put_u16(f, (unsigned)(e->file_count < 0 ? 0 : e->file_count > 0xFFFF ? 0xFFFF : e->file_count)) &&
           put_str(f, e->extract_format) &&
           put_u8(f, (unsigned)(e->disc_index & 0xFF)) &&
           put_u8(f, (unsigned)(e->disc_total & 0xFF));
}

static bool get_row(FILE *f, RomEntry *e) {
    unsigned b, fc, di, dt;
    memset(e, 0, sizeof(*e));
    if (!get_str(f, e->rom_id, sizeof(e->rom_id)) ||
        !get_str(f, e->filename, sizeof(e->filename)) ||
        !get_str(f, e->name, sizeof(e->name)) ||
        !get_str(f, e->system, sizeof(e->system)) ||
        !get_str(f, e->title_id, sizeof(e->title_id)) ||
        !get_u64(f, &e->size) || !get_u8(f, &b) || !get_u16(f, &fc) ||
        !get_str(f, e->extract_format, sizeof(e->extract_format)) ||
        !get_u8(f, &di) || !get_u8(f, &dt))
        return false;
    e->is_bundle  = b != 0;
    e->file_count = (int)fc;
    e->disc_index = (int)di;
    e->disc_total = (int)dt;
    return true;
}

typedef struct {
    char     system[CATCACHE_SYSTEM_LEN + 1];
    char     fingerprint[CATCACHE_FP_LEN];
    uint32_t count;
    uint32_t bytes;
} SectionHead;

/* Open the cache and check magic + version; NULL if absent or foreign. */
static FILE *open_cache(void) {
    FILE *f = fopen(g_path, "rb");
    if (!f) return NULL;
    char magic[4];
    uint32_t version;
    if (fread(magic, 1, 4, f) != 4 || memcmp(magic, MAGIC, 4) != 0 ||
        !get_u32(f, &version) || version != CATCACHE_VERSION) {
        fclose(f);
        return NULL;
    }
    return f;
}

/* Read the next section header; false at end of file / damage. */
static bool read_head(FILE *f, SectionHead *h) {
    memset(h, 0, sizeof(*h));
    if (fread(h->system, 1, CATCACHE_SYSTEM_LEN, f) != CATCACHE_SYSTEM_LEN) return false;
    h->system[CATCACHE_SYSTEM_LEN] = '\0';
    if (!get_str(f, h->fingerprint, sizeof(h->fingerprint))) return false;
    return get_u32(f, &h->count) && get_u32(f, &h->bytes);
}

static bool write_head(FILE *f, const char *system, const char *fp, uint32_t count,
                       uint32_t bytes) {
    char sys[CATCACHE_SYSTEM_LEN];
    memset(sys, 0, sizeof(sys));
    strncpy(sys, system, sizeof(sys) - 1);
    return fwrite(sys, 1, sizeof(sys), f) == sizeof(sys) && put_str(f, fp) &&
           put_u32(f, count) && put_u32(f, bytes);
}

/* Position `f` at the rows of `system`; fills *h.  False if not found. */
static bool seek_section(FILE *f, const char *system, SectionHead *h) {
    while (read_head(f, h)) {
        if (strcmp(h->system, system) == 0) return true;
        if (fseek(f, (long)h->bytes, SEEK_CUR) != 0) return false;
    }
    return false;
}

/* ------------------------------------------------------------------ */
/* Public API                                                          */
/* ------------------------------------------------------------------ */

bool catcache_info(const char *system, char *fp_out, size_t fp_size, int *count_out) {
    if (count_out) *count_out = 0;
    if (fp_out && fp_size) fp_out[0] = '\0';
    if (!system) return false;
    FILE *f = open_cache();
    if (!f) return false;
    SectionHead h;
    bool found = seek_section(f, system, &h);
    fclose(f);
    if (!found) return false;
    if (fp_out && fp_size) snprintf(fp_out, fp_size, "%s", h.fingerprint);
    if (count_out) *count_out = (int)h.count;
    return true;
}

bool catcache_load(const char *system, RomCatalog *out) {
    if (!system || !out) return false;
    out->count = 0;
    out->partial = false;
    out->last_error[0] = '\0';
    FILE *f = open_cache();
    if (!f) return false;
    SectionHead h;
    bool ok = seek_section(f, system, &h);
    if (ok) {
        for (uint32_t i = 0; i < h.count; i++) {
            RomEntry e;
            if (!get_row(f, &e)) { ok = false; break; }
            if (out->count < ROM_CATALOG_MAX) out->items[out->count++] = e;
        }
    }
    fclose(f);
    if (!ok) out->count = 0;
    return ok;
}

/* Rewrite the file without `system`'s section, then append `rows` (if
 * given) under `fp`.  Other sections are copied through a small buffer,
 * so memory use doesn't depend on the catalog size. */
static bool rewrite(const char *system, const char *fp, const RomCatalog *rows) {
    char tmp[sizeof(g_path) + 8];
    snprintf(tmp, sizeof(tmp), "%s.tmp", g_path);
    FILE *out = fopen(tmp, "wb");
    if (!out) return false;
    bool ok = fwrite(MAGIC, 1, 4, out) == 4 && put_u32(out, CATCACHE_VERSION);

    FILE *in = open_cache();
    if (in) {
        long start = ftell(in);
        fseek(in, 0, SEEK_END);
        long size = ftell(in);
        fseek(in, start, SEEK_SET);
        SectionHead h;
        static char buf[16 * 1024];
        while (ok && read_head(in, &h)) {
            /* A truncated tail section (interrupted write) is dropped. */
            if (ftell(in) + (long)h.bytes > size) break;
            bool keep = strcmp(h.system, system) != 0;
            if (keep) ok = write_head(out, h.system, h.fingerprint, h.count, h.bytes);
            uint32_t left = h.bytes;
            while (ok && left > 0) {
                size_t chunk = left < sizeof(buf) ? left : sizeof(buf);
                if (fread(buf, 1, chunk, in) != chunk) ok = false;
                else if (keep && fwrite(buf, 1, chunk, out) != chunk) ok = false;
                left -= (uint32_t)chunk;
            }
        }
        fclose(in);
    }

    if (ok && rows) {
        uint32_t bytes = 0;
        for (int i = 0; i < rows->count; i++) bytes += row_bytes(&rows->items[i]);
        ok = write_head(out, system, fp ? fp : "", (uint32_t)rows->count, bytes);
        for (int i = 0; ok && i < rows->count; i++) ok = put_row(out, &rows->items[i]);
    }
    if (fclose(out) != 0) ok = false;
    if (!ok) {
        remove(tmp);
        return false;
    }
    remove(g_path);   /* the Vita's rename won't replace an existing file */
    return rename(tmp, g_path) == 0;
}

CatPlan catcache_plan(const char *system, const CatFingerprints *server) {
    const CatFingerprint *s = catcache_find_fp(server, system);
    char fp[CATCACHE_FP_LEN];
    bool cached = catcache_info(system, fp, sizeof(fp), NULL);
    if (!s) {
        if (cached) catcache_drop(system);
        return CATCACHE_GONE;
    }
    return (cached && strcmp(fp, s->fingerprint) == 0) ? CATCACHE_FRESH : CATCACHE_STALE;
}

bool catcache_put(const char *system, const char *fingerprint, const RomCatalog *rows) {
    if (!system || !system[0] || strlen(system) >= CATCACHE_SYSTEM_LEN || !rows) return false;
    return rewrite(system, fingerprint, rows);
}

bool catcache_drop(const char *system) {
    if (!system) return false;
    if (!catcache_info(system, NULL, 0, NULL)) return true;
    return rewrite(system, NULL, NULL);
}

void catcache_clear(void) {
    remove(g_path);
}
