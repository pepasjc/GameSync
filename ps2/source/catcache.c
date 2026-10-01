/*
 * catcache.c — fingerprinted on-disk catalog cache (see catcache.h).
 */

#include "catcache.h"

#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

static const char MAGIC[4] = { 'G', 'S', 'C', 'C' };

#define FLAG_BUNDLE 0x01
#define FLAG_CD     0x02

/* stdio buffer: one fileXio round trip per 32 KB instead of per field. */
static char g_io_buf[32 * 1024];

/* ---- Encoding helpers ----
 *
 * Writer / reader over either a stdio FILE (mass storage) or a memory
 * buffer (the memory card copy goes through libmc, which stdio cannot
 * reach). */

typedef struct {
    FILE    *fp;
    uint8_t *buf;
    size_t   cap, len;
} Out;

typedef struct {
    FILE          *fp;
    const uint8_t *buf;
    size_t         len, pos;
} In;

static size_t str_len(const char *s) {
    size_t n = s ? strlen(s) : 0;
    return n > 255 ? 255 : n;
}

static bool put_bytes(Out *o, const void *p, size_t n) {
    if (n == 0) return true;
    if (o->fp) return fwrite(p, 1, n, o->fp) == n;
    if (o->len + n > o->cap) return false;
    memcpy(o->buf + o->len, p, n);
    o->len += n;
    return true;
}

static bool put_u8(Out *o, unsigned v) {
    uint8_t b = (uint8_t)v;
    return put_bytes(o, &b, 1);
}

static bool put_u32(Out *o, uint32_t v) {
    uint8_t b[4] = { (uint8_t)v, (uint8_t)(v >> 8), (uint8_t)(v >> 16), (uint8_t)(v >> 24) };
    return put_bytes(o, b, 4);
}

static bool put_u64(Out *o, uint64_t v) {
    return put_u32(o, (uint32_t)v) && put_u32(o, (uint32_t)(v >> 32));
}

static bool put_str(Out *o, const char *s) {
    size_t n = str_len(s);
    return put_u8(o, (unsigned)n) && put_bytes(o, s, n);
}

static bool get_bytes(In *in, void *p, size_t n) {
    if (n == 0) return true;
    if (in->fp) return fread(p, 1, n, in->fp) == n;
    if (in->pos + n > in->len) return false;
    memcpy(p, in->buf + in->pos, n);
    in->pos += n;
    return true;
}

static bool get_u8(In *in, unsigned *v) {
    uint8_t b;
    if (!get_bytes(in, &b, 1)) return false;
    *v = b;
    return true;
}

static bool get_u32(In *in, uint32_t *v) {
    uint8_t b[4];
    if (!get_bytes(in, b, 4)) return false;
    *v = (uint32_t)b[0] | ((uint32_t)b[1] << 8) | ((uint32_t)b[2] << 16) | ((uint32_t)b[3] << 24);
    return true;
}

static bool get_u64(In *in, uint64_t *v) {
    uint32_t lo, hi;
    if (!get_u32(in, &lo) || !get_u32(in, &hi)) return false;
    *v = (uint64_t)lo | ((uint64_t)hi << 32);
    return true;
}

/* Read a length-prefixed string into out (always terminated).  A stored
 * string longer than the field means a damaged or foreign file. */
static bool get_str(In *in, char *out, size_t out_size) {
    unsigned n;
    if (!get_u8(in, &n)) return false;
    if (n >= out_size) return false;
    if (!get_bytes(in, out, n)) return false;
    out[n] = '\0';
    return true;
}

static bool encode(Out *o, const char *system, const char *fingerprint,
                   const RomCatalog *cat) {
    bool ok = put_bytes(o, MAGIC, sizeof(MAGIC)) &&
              put_u32(o, CATCACHE_VERSION) &&
              put_str(o, system) &&
              put_str(o, fingerprint) &&
              put_u32(o, (uint32_t)cat->count);
    for (int i = 0; ok && i < cat->count; i++) {
        const RomEntry *e = &cat->items[i];
        unsigned flags = (e->is_bundle ? FLAG_BUNDLE : 0) | (e->is_cd ? FLAG_CD : 0);
        int fc = e->file_count < 0 ? 0 : (e->file_count > 255 ? 255 : e->file_count);
        ok = put_str(o, e->rom_id) && put_str(o, e->filename) &&
             put_str(o, e->name) && put_str(o, e->serial) &&
             put_str(o, e->extract_format) && put_u64(o, e->size) &&
             put_u8(o, flags) && put_u8(o, (unsigned)fc);
    }
    return ok;
}

static bool decode(In *in, const char *system, char *fp_out, size_t fp_size,
                   RomCatalog *cat) {
    cat->count = 0;
    if (fp_out && fp_size) fp_out[0] = '\0';

    char magic[4];
    uint32_t version = 0, count = 0;
    char stored_system[sizeof(cat->items[0].system)];
    char fingerprint[128];
    bool ok = get_bytes(in, magic, sizeof(magic)) &&
              memcmp(magic, MAGIC, sizeof(MAGIC)) == 0 &&
              get_u32(in, &version) && version == CATCACHE_VERSION &&
              get_str(in, stored_system, sizeof(stored_system)) &&
              strcmp(stored_system, system ? system : "") == 0 &&
              get_str(in, fingerprint, sizeof(fingerprint)) &&
              get_u32(in, &count) && count <= ROM_CATALOG_MAX;

    for (uint32_t i = 0; ok && i < count; i++) {
        RomEntry *e = &cat->items[i];
        memset(e, 0, sizeof(*e));
        unsigned flags = 0, fc = 0;
        ok = get_str(in, e->rom_id, sizeof(e->rom_id)) &&
             get_str(in, e->filename, sizeof(e->filename)) &&
             get_str(in, e->name, sizeof(e->name)) &&
             get_str(in, e->serial, sizeof(e->serial)) &&
             get_str(in, e->extract_format, sizeof(e->extract_format)) &&
             get_u64(in, &e->size) && get_u8(in, &flags) && get_u8(in, &fc);
        if (!ok) break;
        e->is_bundle  = (flags & FLAG_BUNDLE) != 0;
        e->is_cd      = (flags & FLAG_CD) != 0;
        e->file_count = (int)fc;
        snprintf(e->system, sizeof(e->system), "%s", stored_system);
    }
    if (!ok) {
        cat->count = 0;
        return false;
    }
    cat->count = (int)count;
    cat->last_error[0] = '\0';
    if (fp_out && fp_size) snprintf(fp_out, fp_size, "%s", fingerprint);
    return true;
}

/* ---- Public API ---- */

size_t catcache_encoded_size(const char *system, const char *fingerprint,
                             const RomCatalog *cat) {
    size_t total = sizeof(MAGIC) + 4 + 1 + str_len(system) + 1 + str_len(fingerprint) + 4;
    if (!cat) return total;
    for (int i = 0; i < cat->count; i++) {
        const RomEntry *e = &cat->items[i];
        total += 5 + str_len(e->rom_id) + str_len(e->filename) + str_len(e->name) +
                 str_len(e->serial) + str_len(e->extract_format);
        total += 8 + 1 + 1;
    }
    return total;
}

size_t catcache_encode(void *buf, size_t cap, const char *system,
                       const char *fingerprint, const RomCatalog *cat) {
    if (!buf || !cat) return 0;
    Out o = { NULL, (uint8_t *)buf, cap, 0 };
    return encode(&o, system, fingerprint, cat) ? o.len : 0;
}

bool catcache_decode(const void *buf, size_t len, const char *system,
                     char *fp_out, size_t fp_size, RomCatalog *cat) {
    if (!cat) return false;
    In in = { NULL, (const uint8_t *)buf, buf ? len : 0, 0 };
    return decode(&in, system, fp_out, fp_size, cat);
}

bool catcache_save(const char *path, const char *system,
                   const char *fingerprint, const RomCatalog *cat) {
    if (!path || !path[0] || !cat) return false;

    char tmp[280];
    snprintf(tmp, sizeof(tmp), "%s.tmp", path);
    FILE *fp = fopen(tmp, "wb");
    if (!fp) return false;
    setvbuf(fp, g_io_buf, _IOFBF, sizeof(g_io_buf));

    Out o = { fp, NULL, 0, 0 };
    bool ok = encode(&o, system, fingerprint, cat);
    if (fclose(fp) != 0) ok = false;
    if (!ok) {
        unlink(tmp);
        return false;
    }

    if (rename(tmp, path) != 0) {
        /* FAT refuses to rename over an existing file. */
        unlink(path);
        if (rename(tmp, path) != 0) {
            unlink(tmp);
            return false;
        }
    }
    return true;
}

bool catcache_load(const char *path, const char *system,
                   char *fp_out, size_t fp_size, RomCatalog *cat) {
    if (!cat) return false;
    cat->count = 0;
    if (fp_out && fp_size) fp_out[0] = '\0';
    if (!path || !path[0]) return false;

    FILE *fp = fopen(path, "rb");
    if (!fp) return false;
    setvbuf(fp, g_io_buf, _IOFBF, sizeof(g_io_buf));
    In in = { fp, NULL, 0, 0 };
    bool ok = decode(&in, system, fp_out, fp_size, cat);
    fclose(fp);
    return ok;
}

void catcache_remove(const char *path) {
    if (!path || !path[0]) return;
    unlink(path);
    char tmp[280];
    snprintf(tmp, sizeof(tmp), "%s.tmp", path);
    unlink(tmp);
}

/* ---- Fingerprint response ---- */

static const char *skip_ws(const char *p, const char *end) {
    while (p < end && (*p == ' ' || *p == '\t' || *p == '\n' || *p == '\r')) p++;
    return p;
}

/* Position just past the closing quote of the JSON string starting at p. */
static const char *skip_string(const char *p, const char *end) {
    p++;
    while (p < end && *p != '"') {
        if (*p == '\\' && p + 1 < end) p++;
        p++;
    }
    return p < end ? p + 1 : end;
}

/* Find "key": inside the object [p, end) at depth 0; returns the value
 * start or NULL.  Nested objects/arrays are skipped. */
static const char *object_value(const char *p, const char *end, const char *key) {
    size_t klen = strlen(key);
    int depth = 0;
    while (p < end) {
        char c = *p;
        if (c == '"') {
            const char *after = skip_string(p, end);
            if (depth == 0 && (size_t)(after - p) == klen + 2 &&
                strncmp(p + 1, key, klen) == 0) {
                const char *q = skip_ws(after, end);
                if (q < end && *q == ':') return skip_ws(q + 1, end);
            }
            p = after;
            continue;
        }
        if (c == '{' || c == '[') depth++;
        else if (c == '}' || c == ']') {
            if (depth == 0) return NULL;
            depth--;
        }
        p++;
    }
    return NULL;
}

/* Position of the closing brace of the object starting at p. */
static const char *object_end(const char *p, const char *end) {
    int depth = 0;
    while (p < end) {
        if (*p == '"') { p = skip_string(p, end); continue; }
        if (*p == '{' || *p == '[') depth++;
        else if (*p == '}' || *p == ']') {
            if (--depth == 0) return p;
        }
        p++;
    }
    return NULL;
}

int catcache_parse_fingerprint(const char *json, size_t len, const char *system,
                               char *fp_out, size_t fp_size, int *count_out) {
    if (fp_out && fp_size) fp_out[0] = '\0';
    if (count_out) *count_out = 0;
    if (!json || !system) return -1;

    const char *end = json + len;
    const char *p = skip_ws(json, end);
    if (p >= end || *p != '{') return -1;

    const char *systems = object_value(p + 1, end, "systems");
    if (!systems || *systems != '{') return -1;
    const char *systems_end = object_end(systems, end);
    if (!systems_end) return -1;

    const char *entry = object_value(systems + 1, systems_end, system);
    if (!entry) return 0;
    if (*entry != '{') return -1;
    const char *entry_end = object_end(entry, systems_end + 1);
    if (!entry_end) return -1;

    const char *v = object_value(entry + 1, entry_end, "fingerprint");
    if (v && *v == '"' && fp_out && fp_size) {
        const char *s = v + 1;
        size_t n = 0;
        while (s < entry_end && *s != '"' && n + 1 < fp_size) fp_out[n++] = *s++;
        fp_out[n] = '\0';
    }
    v = object_value(entry + 1, entry_end, "count");
    if (v && count_out) *count_out = atoi(v);
    return 1;
}
