// ROM catalog rows, JSON page parsing and the catalog cache file format.
// See catalog_cache.h. Plain C only - built into the XBE and the host tests.

#include "catalog_cache.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

// ---------------------------------------------------------------------------
// Row list
// ---------------------------------------------------------------------------

void rom_list_free(XboxRomList *list)
{
    if (!list) return;
    free(list->roms);
    list->roms = NULL;
    list->count = 0;
    list->cap = 0;
}

void rom_list_clear(XboxRomList *list)
{
    if (list) list->count = 0;
}

XboxRomEntry *rom_list_push(XboxRomList *list)
{
    if (!list || list->count >= XBOX_MAX_ROMS) return NULL;
    if (list->count >= list->cap) {
        int cap = list->cap ? list->cap * 2 : 256;
        if (cap > XBOX_MAX_ROMS) cap = XBOX_MAX_ROMS;
        XboxRomEntry *grown = (XboxRomEntry *)realloc(
            list->roms, (size_t)cap * sizeof(XboxRomEntry));
        if (!grown) return NULL;
        list->roms = grown;
        list->cap = cap;
    }
    XboxRomEntry *e = &list->roms[list->count++];
    memset(e, 0, sizeof(*e));
    return e;
}

void rom_list_take(XboxRomList *dst, XboxRomList *src)
{
    if (!dst || !src || dst == src) return;
    rom_list_free(dst);
    *dst = *src;
    src->roms = NULL;
    src->count = 0;
    src->cap = 0;
}

// ---------------------------------------------------------------------------
// Minimal JSON helpers (the server's output is well-formed; these only need
// to find top-level keys of one object without tripping over nested ones).
// ---------------------------------------------------------------------------

static const char *skip_ws(const char *p, const char *end)
{
    while (p < end && (*p == ' ' || *p == '\t' || *p == '\r' || *p == '\n')) p++;
    return p;
}

// End of the string starting at the opening quote ``p``: the closing quote.
static const char *string_end(const char *p, const char *end)
{
    for (p++; p < end; p++) {
        if (*p == '\\') { p++; continue; }
        if (*p == '"') return p;
    }
    return NULL;
}

// Matching close bracket of the object/array opening at ``open``.
static const char *container_end(const char *open, const char *end)
{
    int depth = 0;
    for (const char *p = open; p < end && *p; p++) {
        char c = *p;
        if (c == '"') {
            p = string_end(p, end);
            if (!p) return NULL;
        } else if (c == '{' || c == '[') {
            depth++;
        } else if (c == '}' || c == ']') {
            depth--;
            if (depth == 0) return p;
            if (depth < 0) return NULL;
        }
    }
    return NULL;
}

// Value of a top-level ``key`` inside the object [open, close], or NULL.
static const char *obj_find_key(const char *open, const char *close,
                                const char *key)
{
    size_t klen = strlen(key);
    int depth = 0;
    for (const char *p = open; p < close; p++) {
        char c = *p;
        if (c == '{' || c == '[') { depth++; continue; }
        if (c == '}' || c == ']') { depth--; continue; }
        if (c != '"') continue;
        const char *q = string_end(p, close);
        if (!q) return NULL;
        if (depth == 1) {
            const char *after = skip_ws(q + 1, close);
            if (after < close && *after == ':') {
                if ((size_t)(q - p - 1) == klen &&
                    memcmp(p + 1, key, klen) == 0) {
                    return skip_ws(after + 1, close);
                }
            }
        }
        p = q;
    }
    return NULL;
}

// Copy a JSON string value, decoding the simple escapes. Control characters
// (and tab, the cache's field separator) become spaces.
static int copy_string(const char *v, const char *close, char *out, int out_len)
{
    if (!out || out_len <= 0) return -1;
    out[0] = '\0';
    if (!v || v >= close || *v != '"') return -1;
    int i = 0;
    for (const char *p = v + 1; p < close && *p != '"'; p++) {
        char c = *p;
        if (c == '\\' && p + 1 < close) {
            p++;
            switch (*p) {
            case 'n': case 'r': case 't': case 'b': case 'f': c = ' '; break;
            case 'u':
                // Non-ASCII escape: keep a placeholder, skip the 4 hex digits.
                c = '?';
                if (p + 4 < close) p += 4;
                break;
            default: c = *p; break;
            }
        }
        if ((unsigned char)c < 32) c = ' ';
        if (i < out_len - 1) out[i++] = c;
    }
    out[i] = '\0';
    return i > 0 ? 0 : -1;
}

static uint64_t read_u64(const char *v, const char *close)
{
    uint64_t n = 0;
    while (v && v < close && *v >= '0' && *v <= '9') {
        n = n * 10u + (uint64_t)(*v - '0');
        v++;
    }
    return n;
}

static int read_bool(const char *v, const char *close)
{
    if (!v || v >= close) return 0;
    if (*v == 't' || *v == '1') return 1;
    return 0;
}

// ---------------------------------------------------------------------------
// Server responses
// ---------------------------------------------------------------------------

int catcache_parse_roms_page(const char *body, XboxRomList *out,
                             int *page_rows, int *has_more)
{
    if (page_rows) *page_rows = 0;
    if (has_more) *has_more = 0;
    if (!body || !out) return -1;
    const char *end = body + strlen(body);
    const char *root = strchr(body, '{');
    if (!root) return -1;
    const char *root_end = container_end(root, end);
    if (!root_end) return -1;

    const char *arr = obj_find_key(root, root_end, "roms");
    if (!arr || *arr != '[') return -1;
    const char *arr_end = container_end(arr, root_end + 1);
    if (!arr_end) return -1;

    int rc = 0;
    const char *p = arr + 1;
    while (p < arr_end) {
        while (p < arr_end && *p != '{') p++;
        if (p >= arr_end) break;
        const char *close = container_end(p, arr_end);
        if (!close) return -1;
        if (page_rows) (*page_rows)++;

        XboxRomEntry tmp;
        memset(&tmp, 0, sizeof(tmp));
        copy_string(obj_find_key(p, close, "rom_id"), close,
                    tmp.rom_id, sizeof(tmp.rom_id));
        copy_string(obj_find_key(p, close, "name"), close,
                    tmp.name, sizeof(tmp.name));
        copy_string(obj_find_key(p, close, "filename"), close,
                    tmp.filename, sizeof(tmp.filename));
        tmp.size = read_u64(obj_find_key(p, close, "size"), close);
        tmp.is_bundle = read_bool(obj_find_key(p, close, "is_bundle"), close);
        if (tmp.rom_id[0] && tmp.name[0] && rc == 0) {
            XboxRomEntry *slot = rom_list_push(out);
            if (slot) *slot = tmp;
            else rc = -2;
        }
        p = close + 1;
    }

    if (has_more) {
        const char *hm = obj_find_key(root, root_end, "has_more");
        *has_more = read_bool(hm, root_end);
    }
    return rc;
}

int catcache_find_fingerprint(const char *json, const char *system,
                              char *fp, int fp_len, int *count)
{
    if (fp && fp_len > 0) fp[0] = '\0';
    if (count) *count = 0;
    if (!json || !system) return -1;
    const char *end = json + strlen(json);
    const char *root = strchr(json, '{');
    if (!root) return -1;
    const char *root_end = container_end(root, end);
    if (!root_end) return -1;
    const char *systems = obj_find_key(root, root_end, "systems");
    if (!systems || *systems != '{') return -1;
    const char *systems_end = container_end(systems, root_end + 1);
    if (!systems_end) return -1;

    const char *entry = obj_find_key(systems, systems_end, system);
    if (!entry || *entry != '{') return 0;
    const char *entry_end = container_end(entry, systems_end + 1);
    if (!entry_end) return -1;
    copy_string(obj_find_key(entry, entry_end, "fingerprint"), entry_end,
                fp, fp_len);
    if (count) {
        *count = (int)read_u64(obj_find_key(entry, entry_end, "count"),
                               entry_end);
    }
    return 1;
}

// ---------------------------------------------------------------------------
// Cache file
// ---------------------------------------------------------------------------

#define CATCACHE_MAGIC "GSXCAT"

// Field text with tabs / newlines flattened so it cannot break a record.
static size_t put_field(char *dst, const char *src)
{
    size_t n = 0;
    for (; src && *src; src++) {
        char c = *src;
        if (c == '\t' || c == '\r' || c == '\n') c = ' ';
        dst[n++] = c;
    }
    return n;
}

char *catcache_serialize(const char *system, const char *fingerprint,
                         const XboxRomList *list, size_t *out_len)
{
    int count = list ? list->count : 0;
    size_t row_max = XBOX_ROM_ID_MAX + XBOX_ROM_NAME_MAX + XBOX_ROM_FILE_MAX + 40;
    size_t cap = 256 + CATCACHE_FP_MAX + (size_t)count * row_max;
    char *buf = (char *)malloc(cap);
    if (!buf) return NULL;

    size_t n = (size_t)snprintf(buf, cap,
                                CATCACHE_MAGIC " %d\nsystem %s\nfingerprint %s\ncount %d\n",
                                CATCACHE_VERSION, system ? system : "",
                                fingerprint ? fingerprint : "", count);
    for (int i = 0; i < count; i++) {
        const XboxRomEntry *r = &list->roms[i];
        n += put_field(buf + n, r->rom_id);
        buf[n++] = '\t';
        n += put_field(buf + n, r->name);
        buf[n++] = '\t';
        n += put_field(buf + n, r->filename);
        n += (size_t)snprintf(buf + n, cap - n, "\t%llu\t%d\n",
                              (unsigned long long)r->size, r->is_bundle ? 1 : 0);
    }
    if (out_len) *out_len = n;
    return buf;
}

// Next line of [*p, end) into (line, len); advances *p. 0 at end of buffer.
static int next_line(const char **p, const char *end,
                     const char **line, size_t *len)
{
    if (*p >= end) return 0;
    const char *s = *p;
    const char *nl = memchr(s, '\n', (size_t)(end - s));
    const char *e = nl ? nl : end;
    *line = s;
    *len = (size_t)(e - s);
    if (*len > 0 && s[*len - 1] == '\r') (*len)--;
    *p = nl ? nl + 1 : end;
    return nl != NULL;   // a record without its newline is a torn write
}

// "<prefix> <value>" -> value copied into out.
static int header_value(const char *line, size_t len, const char *prefix,
                        char *out, size_t out_len)
{
    size_t pl = strlen(prefix);
    if (len < pl + 1 || memcmp(line, prefix, pl) != 0 || line[pl] != ' ') {
        return -1;
    }
    size_t vl = len - pl - 1;
    if (vl >= out_len) return -1;
    memcpy(out, line + pl + 1, vl);
    out[vl] = '\0';
    return 0;
}

static void copy_span(char *dst, size_t dst_len, const char *s, size_t n)
{
    if (n >= dst_len) n = dst_len - 1;
    memcpy(dst, s, n);
    dst[n] = '\0';
}

int catcache_parse(const char *buf, size_t len, const char *system,
                   char *fp, int fp_len, XboxRomList *out)
{
    if (fp && fp_len > 0) fp[0] = '\0';
    if (!out) return -1;
    rom_list_clear(out);
    if (!buf || !system) return -1;

    const char *p = buf, *end = buf + len;
    const char *line;
    size_t ll;
    char val[CATCACHE_FP_MAX + 8];
    char want[16];
    snprintf(want, sizeof(want), "%d", CATCACHE_VERSION);

    if (!next_line(&p, end, &line, &ll) ||
        header_value(line, ll, CATCACHE_MAGIC, val, sizeof(val)) != 0 ||
        strcmp(val, want) != 0) {
        return -1;
    }
    if (!next_line(&p, end, &line, &ll) ||
        header_value(line, ll, "system", val, sizeof(val)) != 0 ||
        strcmp(val, system) != 0) {
        return -1;
    }
    char fingerprint[CATCACHE_FP_MAX];
    if (!next_line(&p, end, &line, &ll) ||
        header_value(line, ll, "fingerprint", fingerprint,
                     sizeof(fingerprint)) != 0 || !fingerprint[0]) {
        return -1;
    }
    if (!next_line(&p, end, &line, &ll) ||
        header_value(line, ll, "count", val, sizeof(val)) != 0) {
        return -1;
    }
    int count = atoi(val);
    if (count < 0 || count > XBOX_MAX_ROMS) return -1;

    for (int i = 0; i < count; i++) {
        if (!next_line(&p, end, &line, &ll)) goto bad;
        const char *f[5];
        size_t fl[5];
        int nf = 0;
        const char *s = line, *le = line + ll;
        while (nf < 5) {
            const char *tab = memchr(s, '\t', (size_t)(le - s));
            f[nf] = s;
            fl[nf] = (size_t)((tab ? tab : le) - s);
            nf++;
            if (!tab) break;
            s = tab + 1;
        }
        if (nf != 5 || fl[0] == 0 || fl[1] == 0) goto bad;

        XboxRomEntry *r = rom_list_push(out);
        if (!r) goto bad;
        copy_span(r->rom_id, sizeof(r->rom_id), f[0], fl[0]);
        copy_span(r->name, sizeof(r->name), f[1], fl[1]);
        copy_span(r->filename, sizeof(r->filename), f[2], fl[2]);
        r->size = read_u64(f[3], f[3] + fl[3]);
        r->is_bundle = (fl[4] > 0 && f[4][0] == '1');
    }
    if (fp && fp_len > 0) snprintf(fp, fp_len, "%s", fingerprint);
    return 0;

bad:
    rom_list_clear(out);
    return -1;
}
