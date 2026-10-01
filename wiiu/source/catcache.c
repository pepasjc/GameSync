/*
 * catcache.c — per-system ROM catalog cache on SD (see catcache.h).
 *
 * Pure stdio: no wut calls, so the format can be exercised on a host too.
 */

#include "catcache.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>

static const char MAGIC[4] = { 'G', 'S', 'W', 'C' };

void catcache_dir(const char *sd_root, char *out, size_t out_size) {
    snprintf(out, out_size, "%s%s/catalog", sd_root ? sd_root : "", APP_DATA_SUBDIR);
}

static void file_path(const char *dir, const char *system, const char *ext,
                      char *out, size_t out_size) {
    /* System codes are short uppercase ids from our own list ("GC", "WII",
     * "WIIU"); keep only filename-safe characters regardless. */
    char sys[16];
    size_t j = 0;
    for (size_t i = 0; system && system[i] && j + 1 < sizeof(sys); i++) {
        char c = system[i];
        if ((c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') ||
            (c >= '0' && c <= '9') || c == '_' || c == '-')
            sys[j++] = c;
    }
    sys[j] = '\0';
    snprintf(out, out_size, "%s/%s%s", dir, sys, ext);
}

/* ---- writer ---- */

typedef struct {
    FILE *fp;
    bool  ok;
} W;

static void w_bytes(W *w, const void *p, size_t n) {
    if (w->ok && n && fwrite(p, 1, n, w->fp) != n) w->ok = false;
}

static void w_u8(W *w, unsigned v) {
    unsigned char b = (unsigned char)v;
    w_bytes(w, &b, 1);
}

static void w_u16(W *w, unsigned v) {
    unsigned char b[2] = { (unsigned char)(v >> 8), (unsigned char)v };
    w_bytes(w, b, 2);
}

static void w_u32(W *w, uint32_t v) {
    unsigned char b[4] = { (unsigned char)(v >> 24), (unsigned char)(v >> 16),
                           (unsigned char)(v >> 8), (unsigned char)v };
    w_bytes(w, b, 4);
}

static void w_u64(W *w, uint64_t v) {
    w_u32(w, (uint32_t)(v >> 32));
    w_u32(w, (uint32_t)v);
}

static void w_str(W *w, const char *s) {
    size_t n = s ? strlen(s) : 0;
    if (n > 0xFFFF) n = 0xFFFF;
    w_u16(w, (unsigned)n);
    w_bytes(w, s, n);
}

bool catcache_save(const char *dir, const char *system,
                   const char *fingerprint, const RomCatalog *cat) {
    if (!dir || !system || !cat) return false;
    mkdir(dir, 0777);   /* parent (<sd>/3dssync) is created at boot */

    char path[SAVE_DIR_LEN], part[SAVE_DIR_LEN];
    file_path(dir, system, ".bin", path, sizeof(path));
    file_path(dir, system, ".part", part, sizeof(part));

    W w = { fopen(part, "wb"), true };
    if (!w.fp) return false;
    static char vbuf[64 * 1024];
    setvbuf(w.fp, vbuf, _IOFBF, sizeof(vbuf));

    w_bytes(&w, MAGIC, sizeof(MAGIC));
    w_u32(&w, CATCACHE_VERSION);
    w_u32(&w, (uint32_t)cat->count);
    w_str(&w, fingerprint);
    w_str(&w, system);
    for (int i = 0; i < cat->count && w.ok; i++) {
        const RomEntry *e = &cat->items[i];
        w_str(&w, e->rom_id);
        w_str(&w, e->filename);
        w_str(&w, e->name);
        w_str(&w, e->system);
        w_u64(&w, e->size);
        w_str(&w, e->extract_format);
        w_u8(&w, e->is_bundle ? 1 : 0);
        w_str(&w, e->content_type);
        int rc = e->related_count;
        if (rc < 0) rc = 0;
        if (rc > WIIU_RELATED_MAX) rc = WIIU_RELATED_MAX;
        w_u8(&w, (unsigned)rc);
        for (int k = 0; k < rc; k++) w_str(&w, e->related[k]);
    }
    if (fclose(w.fp) != 0) w.ok = false;
    if (!w.ok) { remove(part); return false; }

    /* FAT's rename does not replace an existing file. */
    remove(path);
    if (rename(part, path) != 0) { remove(part); return false; }
    return true;
}

/* ---- reader ---- */

typedef struct {
    FILE *fp;
    bool  ok;
} R;

static void r_bytes(R *r, void *p, size_t n) {
    if (r->ok && n && fread(p, 1, n, r->fp) != n) r->ok = false;
}

static unsigned r_u8(R *r) {
    unsigned char b = 0;
    r_bytes(r, &b, 1);
    return r->ok ? b : 0;
}

static unsigned r_u16(R *r) {
    unsigned char b[2] = { 0, 0 };
    r_bytes(r, b, 2);
    return r->ok ? ((unsigned)b[0] << 8 | b[1]) : 0;
}

static uint32_t r_u32(R *r) {
    unsigned char b[4] = { 0, 0, 0, 0 };
    r_bytes(r, b, 4);
    if (!r->ok) return 0;
    return (uint32_t)b[0] << 24 | (uint32_t)b[1] << 16 | (uint32_t)b[2] << 8 | b[3];
}

static uint64_t r_u64(R *r) {
    uint64_t hi = r_u32(r);
    return hi << 32 | r_u32(r);
}

/* Read a string into ``out`` (truncating to fit); the excess is skipped so
 * the stream stays aligned. */
static void r_str(R *r, char *out, size_t cap) {
    unsigned n = r_u16(r);
    if (!r->ok) { if (cap) out[0] = '\0'; return; }
    size_t keep = n < cap ? n : (cap ? cap - 1 : 0);
    r_bytes(r, out, keep);
    if (cap) out[r->ok ? keep : 0] = '\0';
    for (size_t i = keep; i < n && r->ok; i++) {
        char sink;
        r_bytes(r, &sink, 1);
    }
}

bool catcache_load(const char *dir, const char *system,
                   char *fp_out, size_t fp_size, RomCatalog *cat) {
    if (fp_out && fp_size) fp_out[0] = '\0';
    if (!cat) return false;
    cat->count = 0;
    if (!dir || !system) return false;

    char path[SAVE_DIR_LEN];
    file_path(dir, system, ".bin", path, sizeof(path));
    R r = { fopen(path, "rb"), true };
    if (!r.fp) return false;
    static char vbuf[64 * 1024];
    setvbuf(r.fp, vbuf, _IOFBF, sizeof(vbuf));

    char magic[4];
    r_bytes(&r, magic, sizeof(magic));
    uint32_t version = r_u32(&r);
    uint32_t count   = r_u32(&r);
    char fp[CATCACHE_FP_LEN], sys[16];
    r_str(&r, fp, sizeof(fp));
    r_str(&r, sys, sizeof(sys));
    if (!r.ok || memcmp(magic, MAGIC, sizeof(MAGIC)) != 0 ||
        version != CATCACHE_VERSION || count > ROM_CATALOG_MAX ||
        strcmp(sys, system) != 0) {
        fclose(r.fp);
        return false;
    }

    snprintf(cat->system, sizeof(cat->system), "%s", system);
    for (uint32_t i = 0; i < count && r.ok; i++) {
        RomEntry *e = &cat->items[i];
        memset(e, 0, sizeof(*e));
        r_str(&r, e->rom_id, sizeof(e->rom_id));
        r_str(&r, e->filename, sizeof(e->filename));
        r_str(&r, e->name, sizeof(e->name));
        r_str(&r, e->system, sizeof(e->system));
        e->size = r_u64(&r);
        r_str(&r, e->extract_format, sizeof(e->extract_format));
        e->is_bundle = r_u8(&r) != 0;
        r_str(&r, e->content_type, sizeof(e->content_type));
        unsigned rc = r_u8(&r);
        if (rc > WIIU_RELATED_MAX) r.ok = false;
        for (unsigned k = 0; k < rc && r.ok; k++)
            r_str(&r, e->related[k], sizeof(e->related[k]));
        e->related_count = (int)rc;
    }
    fclose(r.fp);
    if (!r.ok) { cat->count = 0; return false; }

    cat->count = (int)count;
    cat->last_error[0] = '\0';
    if (fp_out && fp_size) snprintf(fp_out, fp_size, "%s", fp);
    return true;
}

void catcache_drop(const char *dir, const char *system) {
    char path[SAVE_DIR_LEN];
    file_path(dir, system, ".bin", path, sizeof(path));
    remove(path);
}

void catcache_wipe(const char *dir) {
    static const char *const SYSTEMS[] = { "GC", "WII", "WIIU" };
    for (size_t i = 0; i < sizeof(SYSTEMS) / sizeof(SYSTEMS[0]); i++)
        catcache_drop(dir, SYSTEMS[i]);
}
