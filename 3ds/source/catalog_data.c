// Game catalog data for the 3DS client. Adapted from the DS client's
// ds/source/catalog_data.c (same project, same license).
#include "catalog_data.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <ctype.h>
#include <dirent.h>
#include <sys/stat.h>

// ---------------------------------------------------------------------------
// Minimal JSON reader: walks the text once, decoding only the fields asked
// for and skipping everything else (nested objects/arrays included).
// ---------------------------------------------------------------------------

#define JSON_MAX_DEPTH 32

typedef struct {
    const char *p;
    const char *end;
} JCur;

static void skip_ws(JCur *c) {
    while (c->p < c->end && (*c->p == ' ' || *c->p == '\t' || *c->p == '\r' || *c->p == '\n'))
        c->p++;
}

static bool expect(JCur *c, char ch) {
    skip_ws(c);
    if (c->p >= c->end || *c->p != ch) return false;
    c->p++;
    return true;
}

static int hex_val(char ch) {
    if (ch >= '0' && ch <= '9') return ch - '0';
    if (ch >= 'a' && ch <= 'f') return ch - 'a' + 10;
    if (ch >= 'A' && ch <= 'F') return ch - 'A' + 10;
    return -1;
}

// Append one byte if there is room (always leaves space for the NUL)
static void put_byte(char *out, size_t size, size_t *n, bool *truncated, char ch) {
    if (!out) return;
    if (*n + 1 < size) out[(*n)++] = ch;
    else if (truncated) *truncated = true;
}

// Parse a string at the cursor into out (NULL to skip). \uXXXX is written
// as UTF-8; lone surrogates become '?'.
static bool parse_string(JCur *c, char *out, size_t size, bool *truncated) {
    size_t n = 0;
    if (truncated) *truncated = false;
    if (!expect(c, '"')) return false;
    while (c->p < c->end) {
        char ch = *c->p++;
        if (ch == '"') {
            if (out && size) out[n] = '\0';
            return true;
        }
        if (ch != '\\') {
            put_byte(out, size, &n, truncated, ch);
            continue;
        }
        if (c->p >= c->end) return false;
        char esc = *c->p++;
        switch (esc) {
            case '"': case '\\': case '/': put_byte(out, size, &n, truncated, esc); break;
            case 'b': put_byte(out, size, &n, truncated, '\b'); break;
            case 'f': put_byte(out, size, &n, truncated, '\f'); break;
            case 'n': put_byte(out, size, &n, truncated, '\n'); break;
            case 'r': put_byte(out, size, &n, truncated, '\r'); break;
            case 't': put_byte(out, size, &n, truncated, '\t'); break;
            case 'u': {
                if (c->end - c->p < 4) return false;
                unsigned cp = 0;
                for (int i = 0; i < 4; i++) {
                    int v = hex_val(c->p[i]);
                    if (v < 0) return false;
                    cp = (cp << 4) | (unsigned)v;
                }
                c->p += 4;
                if (cp >= 0xD800 && cp <= 0xDFFF) {
                    put_byte(out, size, &n, truncated, '?');
                } else if (cp < 0x80) {
                    put_byte(out, size, &n, truncated, (char)cp);
                } else if (cp < 0x800) {
                    put_byte(out, size, &n, truncated, (char)(0xC0 | (cp >> 6)));
                    put_byte(out, size, &n, truncated, (char)(0x80 | (cp & 0x3F)));
                } else {
                    put_byte(out, size, &n, truncated, (char)(0xE0 | (cp >> 12)));
                    put_byte(out, size, &n, truncated, (char)(0x80 | ((cp >> 6) & 0x3F)));
                    put_byte(out, size, &n, truncated, (char)(0x80 | (cp & 0x3F)));
                }
                break;
            }
            default:
                return false;
        }
    }
    return false;
}

static bool parse_number(JCur *c, long long *out) {
    skip_ws(c);
    char buf[32];
    size_t n = 0;
    while (c->p < c->end && n < sizeof(buf) - 1 &&
           (isdigit((unsigned char)*c->p) || *c->p == '-' || *c->p == '+' ||
            *c->p == '.' || *c->p == 'e' || *c->p == 'E')) {
        buf[n++] = *c->p++;
    }
    if (n == 0) return false;
    buf[n] = '\0';
    char *end;
    long long v = strtoll(buf, &end, 10);
    if (end == buf) return false;
    *out = v;
    return true;
}

// true / false / null: returns 1, 0, -1; -2 if it's none of them
static int parse_literal(JCur *c) {
    skip_ws(c);
    size_t left = (size_t)(c->end - c->p);
    if (left >= 4 && strncmp(c->p, "true", 4) == 0) { c->p += 4; return 1; }
    if (left >= 5 && strncmp(c->p, "false", 5) == 0) { c->p += 5; return 0; }
    if (left >= 4 && strncmp(c->p, "null", 4) == 0) { c->p += 4; return -1; }
    return -2;
}

static bool skip_value_depth(JCur *c, int depth) {
    if (depth > JSON_MAX_DEPTH) return false;
    skip_ws(c);
    if (c->p >= c->end) return false;
    char ch = *c->p;
    if (ch == '"') return parse_string(c, NULL, 0, NULL);
    if (ch == '{' || ch == '[') {
        char close = (ch == '{') ? '}' : ']';
        c->p++;
        skip_ws(c);
        if (c->p < c->end && *c->p == close) {
            c->p++;
            return true;
        }
        while (1) {
            if (ch == '{') {
                if (!parse_string(c, NULL, 0, NULL) || !expect(c, ':')) return false;
            }
            if (!skip_value_depth(c, depth + 1)) return false;
            skip_ws(c);
            if (c->p >= c->end) return false;
            if (*c->p == ',') { c->p++; continue; }
            if (*c->p == close) { c->p++; return true; }
            return false;
        }
    }
    if (parse_literal(c) != -2) return true;
    long long dummy;
    return parse_number(c, &dummy);
}

static bool skip_value(JCur *c) {
    return skip_value_depth(c, 0);
}

// Iterate an object's members: returns 1 with the key read and the cursor
// on the value, 0 at the closing brace, -1 on error. *first tracks commas.
static int next_member(JCur *c, bool *first, char *key, size_t key_size) {
    skip_ws(c);
    if (c->p >= c->end) return -1;
    if (*c->p == '}') {
        c->p++;
        return 0;
    }
    if (!*first) {
        if (*c->p != ',') return -1;
        c->p++;
    }
    *first = false;
    bool trunc;
    if (!parse_string(c, key, key_size, &trunc)) return -1;
    if (trunc) key[0] = '\0';  // unknown (long) key: gets skipped
    if (!expect(c, ':')) return -1;
    return 1;
}

// Same for arrays: 1 = cursor on the next element, 0 = end, -1 = error
static int next_element(JCur *c, bool *first) {
    skip_ws(c);
    if (c->p >= c->end) return -1;
    if (*c->p == ']') {
        c->p++;
        return 0;
    }
    if (!*first) {
        if (*c->p != ',') return -1;
        c->p++;
    }
    *first = false;
    return 1;
}

static bool parse_int_field(JCur *c, int *out) {
    skip_ws(c);
    if (parse_literal(c) == -1) {  // null
        *out = 0;
        return true;
    }
    long long v;
    if (!parse_number(c, &v)) return false;
    *out = (int)v;
    return true;
}

static bool parse_entry(JCur *c, CatEntry *e) {
    memset(e, 0, sizeof(*e));
    if (!expect(c, '{')) return false;
    char key[32];
    bool first = true;
    int r;
    while ((r = next_member(c, &first, key, sizeof(key))) == 1) {
        bool ok = true, trunc = false;
        if (strcmp(key, "rom_id") == 0) {
            ok = parse_string(c, e->rom_id, sizeof(e->rom_id), &trunc);
            if (trunc) e->truncated = true;
        } else if (strcmp(key, "title_id") == 0) {
            skip_ws(c);
            if (c->p < c->end && *c->p == '"') ok = parse_string(c, e->title_id, sizeof(e->title_id), &trunc);
            else ok = skip_value(c);
            if (trunc) e->title_id[0] = 0;
        } else if (strcmp(key, "is_bundle") == 0) {
            int v = parse_literal(c);
            if (v == -2) ok = false;
            e->is_bundle = (v == 1);
        } else if (strcmp(key, "name") == 0) {
            ok = parse_string(c, e->name, sizeof(e->name), &trunc);
        } else if (strcmp(key, "filename") == 0) {
            ok = parse_string(c, e->filename, sizeof(e->filename), &trunc);
            if (trunc) e->truncated = true;
        } else if (strcmp(key, "size") == 0) {
            long long v;
            skip_ws(c);
            if (parse_literal(c) == -1) v = 0;
            else ok = parse_number(c, &v);
            e->size = (v > 0) ? (uint64_t)v : 0;
        } else if (strcmp(key, "ra_game_id") == 0) {
            ok = parse_int_field(c, &e->ra_game_id);
        } else if (strcmp(key, "ra_achievements") == 0) {
            ok = parse_int_field(c, &e->ra_achievements);
        } else if (strcmp(key, "ra_match") == 0) {
            char match[16];
            skip_ws(c);
            if (c->p < c->end && *c->p == '"') {
                ok = parse_string(c, match, sizeof(match), &trunc);
                e->ra_title_only = ok && strcmp(match, "title") == 0;
            } else {
                ok = skip_value(c);
            }
        } else if (strcmp(key, "extract_formats") == 0) {
            skip_ws(c);
            if (c->p < c->end && *c->p == '[') {
                c->p++;
                bool efirst = true;
                int er;
                while ((er = next_element(c, &efirst)) == 1) {
                    char fmt[16];
                    skip_ws(c);
                    if (c->p < c->end && *c->p == '"') {
                        if (!parse_string(c, fmt, sizeof(fmt), &trunc)) return false;
                        if (strcmp(fmt, "nds") == 0) e->can_extract_nds = true;
                        if (strcmp(fmt, "cia") == 0) e->can_extract_cia = true;
                    } else if (!skip_value(c)) {
                        return false;
                    }
                }
                ok = (er == 0);
            } else {
                ok = skip_value(c);
            }
        } else {
            ok = skip_value(c);
        }
        if (!ok) return false;
    }
    return r == 0;
}

int cat_parse_page(const char *json, size_t len, CatEntry *out, int max, CatPageInfo *info) {
    JCur c = { json, json + len };
    CatPageInfo tmp = { .total = -1, .has_more = false, .has_ra = -1 };
    int count = 0;
    bool saw_roms = false;

    if (!expect(&c, '{')) return -1;
    char key[32];
    bool first = true;
    int r;
    while ((r = next_member(&c, &first, key, sizeof(key))) == 1) {
        if (strcmp(key, "roms") == 0) {
            if (!expect(&c, '[')) return -1;
            saw_roms = true;
            bool efirst = true;
            int er;
            while ((er = next_element(&c, &efirst)) == 1) {
                if (count < max) {
                    if (!parse_entry(&c, &out[count])) return -1;
                    count++;
                } else if (!skip_value(&c)) {
                    return -1;
                }
            }
            if (er != 0) return -1;
        } else if (strcmp(key, "total") == 0) {
            if (!parse_int_field(&c, &tmp.total)) return -1;
        } else if (strcmp(key, "has_more") == 0) {
            int v = parse_literal(&c);
            if (v == -2) return -1;
            tmp.has_more = (v == 1);
        } else if (strcmp(key, "has_ra") == 0) {
            int v = parse_literal(&c);
            if (v == -2) return -1;
            tmp.has_ra = v;
        } else if (!skip_value(&c)) {
            return -1;
        }
    }
    if (r != 0 || !saw_roms) return -1;
    if (tmp.total < 0) tmp.total = count;
    if (info) *info = tmp;
    return count;
}

int cat_parse_systems(const char *json, size_t len, const char *const *wanted,
                      char out[][8], int *counts, int max) {
    JCur c = { json, json + len };
    int nwanted = 0;
    while (wanted[nwanted]) nwanted++;
    bool present[16] = { false };
    int stat[16] = { 0 };
    if (nwanted > 16) nwanted = 16;

    if (!expect(&c, '{')) return -1;
    char key[32];
    bool first = true;
    int r;
    while ((r = next_member(&c, &first, key, sizeof(key))) == 1) {
        if (strcmp(key, "systems") == 0) {
            if (!expect(&c, '[')) return -1;
            bool efirst = true;
            int er;
            while ((er = next_element(&c, &efirst)) == 1) {
                char sys[16];
                bool trunc;
                if (!parse_string(&c, sys, sizeof(sys), &trunc)) return -1;
                for (int i = 0; i < nwanted; i++) {
                    if (strcasecmp(sys, wanted[i]) == 0) present[i] = true;
                }
            }
            if (er != 0) return -1;
        } else if (strcmp(key, "stats") == 0) {
            if (!expect(&c, '{')) return -1;
            bool sfirst = true;
            int sr;
            char sys[16];
            while ((sr = next_member(&c, &sfirst, sys, sizeof(sys))) == 1) {
                int v = 0;
                skip_ws(&c);
                if (c.p < c.end && (isdigit((unsigned char)*c.p) || *c.p == '-')) {
                    if (!parse_int_field(&c, &v)) return -1;
                    for (int i = 0; i < nwanted; i++) {
                        if (strcasecmp(sys, wanted[i]) == 0) stat[i] = v;
                    }
                } else if (!skip_value(&c)) {
                    return -1;
                }
            }
            if (sr != 0) return -1;
        } else if (!skip_value(&c)) {
            return -1;
        }
    }
    if (r != 0) return -1;

    int n = 0;
    for (int i = 0; i < nwanted && n < max; i++) {
        if (!present[i]) continue;
        snprintf(out[n], 8, "%s", wanted[i]);
        if (counts) counts[n] = stat[i];
        n++;
    }
    return n;
}

// ---------------------------------------------------------------------------
// Strings
// ---------------------------------------------------------------------------

void cat_url_encode(const char *in, char *out, size_t size) {
    static const char hex[] = "0123456789ABCDEF";
    size_t n = 0;
    if (size == 0) return;
    for (const unsigned char *p = (const unsigned char *)in; *p; p++) {
        if (isalnum(*p) || *p == '-' || *p == '_' || *p == '.' || *p == '~') {
            if (n + 1 >= size) break;
            out[n++] = (char)*p;
        } else {
            if (n + 3 >= size) break;
            out[n++] = '%';
            out[n++] = hex[*p >> 4];
            out[n++] = hex[*p & 15];
        }
    }
    out[n] = '\0';
}

void cat_target_name(const char *filename, const char *ext, char *out, size_t size) {
    size_t ext_len = strlen(ext);
    if (size < ext_len + 5) {
        if (size) out[0] = 0;
        return;
    }
    const char *dot = strrchr(filename, '.');
    size_t stem_len = dot ? (size_t)(dot - filename) : strlen(filename);
    // "Game.3ds.zip" -> "Game"
    if (dot && strcasecmp(dot, ".zip") == 0) {
        static const char *const inner[] = { ".3ds", ".cci", ".cia", ".nds", ".dsi", NULL };
        for (int i = 0; inner[i]; i++) {
            size_t il = strlen(inner[i]);
            if (stem_len >= il && strncasecmp(filename + stem_len - il, inner[i], il) == 0) {
                stem_len -= il;
                break;
            }
        }
    }
    if (stem_len > size - ext_len - 1) stem_len = size - ext_len - 1;

    size_t n = 0;
    for (size_t i = 0; i < stem_len; i++) {
        unsigned char ch = (unsigned char)filename[i];
        if (ch < 0x20 || strchr("\\/:*?\"<>|", ch)) ch = '_';
        out[n++] = (char)ch;
    }
    // FAT drops trailing dots and spaces
    while (n > 0 && (out[n - 1] == ' ' || out[n - 1] == '.')) n--;
    if (n == 0) {
        memcpy(out, "game", 4);
        n = 4;
    }
    memcpy(out + n, ext, ext_len + 1);
}

int cat_window_start(int first, int rows, int window, int total, int direction) {
    int spare = window - rows;
    int start;
    if (direction > 0) start = first - spare / 8;                 // most of it ahead
    else if (direction < 0) start = first + rows + spare / 8 - window;
    else start = first - spare / 2;
    if (total >= 0 && start > total - window) start = total - window;
    if (start < 0) start = 0;
    return start;
}

bool cat_window_covers(int win_off, int win_count, int first, int rows, int total) {
    int last = first + rows;  // exclusive
    if (total >= 0 && last > total) last = total;
    if (last <= first) return true;  // nothing to show
    return first >= win_off && last <= win_off + win_count;
}

void cat_format_size(uint64_t bytes, char *out, size_t size) {
    if (bytes >= 1000ULL * 1024 * 1024) {
        unsigned long hundredths = (unsigned long)(bytes * 100 / (1024ULL * 1024 * 1024));
        snprintf(out, size, "%lu.%02lu GB", hundredths / 100, hundredths % 100);
    } else if (bytes >= 1024ULL * 1024) {
        unsigned long tenths = (unsigned long)(bytes * 10 / (1024ULL * 1024));
        snprintf(out, size, "%lu.%lu MB", tenths / 10, tenths % 10);
    } else {
        snprintf(out, size, "%lu KB", (unsigned long)((bytes + 1023) / 1024));
    }
}

void cat_ascii(const char *in, char *out, size_t size) {
    size_t n = 0;
    if (size == 0) return;
    for (const unsigned char *p = (const unsigned char *)in; *p && n + 1 < size; p++) {
        if (*p >= 0x80) {
            if ((*p & 0xC0) == 0x80) continue;  // continuation byte
            out[n++] = '?';
        } else if (*p < 0x20) {
            out[n++] = ' ';
        } else {
            out[n++] = (char)*p;
        }
    }
    out[n] = '\0';
}

// ---------------------------------------------------------------------------
// Name set
// ---------------------------------------------------------------------------

// Lower-case copy of the name without its extension
static char *stem_lower(const char *file_name) {
    const char *slash = strrchr(file_name, '/');
    if (slash) file_name = slash + 1;
    const char *dot = strrchr(file_name, '.');
    size_t len = dot && dot != file_name ? (size_t)(dot - file_name) : strlen(file_name);
    char *s = malloc(len + 1);
    if (!s) return NULL;
    for (size_t i = 0; i < len; i++) s[i] = (char)tolower((unsigned char)file_name[i]);
    s[len] = '\0';
    return s;
}

void cat_names_add(CatNameSet *set, const char *file_name) {
    if (set->count == set->cap) {
        int cap = set->cap ? set->cap * 2 : 64;
        char **grown = realloc(set->names, (size_t)cap * sizeof(char *));
        if (!grown) return;
        set->names = grown;
        set->cap = cap;
    }
    char *s = stem_lower(file_name);
    if (!s) return;
    set->names[set->count++] = s;
    set->sorted = false;
}

static int name_compare(const void *a, const void *b) {
    return strcmp(*(char *const *)a, *(char *const *)b);
}

void cat_names_sort(CatNameSet *set) {
    if (set->count > 1) qsort(set->names, (size_t)set->count, sizeof(char *), name_compare);
    set->sorted = true;
}

bool cat_names_contains(const CatNameSet *set, const char *file_name) {
    if (set->count == 0) return false;
    char *key = stem_lower(file_name);
    if (!key) return false;
    bool found = false;
    if (set->sorted) {
        found = bsearch(&key, set->names, (size_t)set->count, sizeof(char *), name_compare) != NULL;
    } else {
        for (int i = 0; i < set->count && !found; i++) found = strcmp(set->names[i], key) == 0;
    }
    free(key);
    return found;
}

void cat_names_free(CatNameSet *set) {
    for (int i = 0; i < set->count; i++) free(set->names[i]);
    free(set->names);
    memset(set, 0, sizeof(*set));
}

static bool has_ext(const char *name, const char *const *exts) {
    const char *ext = strrchr(name, '.');
    if (!ext) return false;
    for (int i = 0; exts[i]; i++)
        if (strcasecmp(ext, exts[i]) == 0) return true;
    return false;
}

static int scan_dir(CatNameSet *set, const char *dir, int depth, int max_depth,
                    const char *const *exts) {
    DIR *d = opendir(dir);
    if (!d) return 0;
    int added = 0;
    struct dirent *ent;
    char path[512];
    while ((ent = readdir(d)) != NULL) {
        if (ent->d_name[0] == '.') continue;
        snprintf(path, sizeof(path), "%s/%s", dir, ent->d_name);

        bool is_dir = (ent->d_type == DT_DIR);
        if (ent->d_type == DT_UNKNOWN) {
            struct stat st;
            is_dir = (stat(path, &st) == 0 && S_ISDIR(st.st_mode));
        }
        if (is_dir) {
            if (depth < max_depth && strcasecmp(ent->d_name, "saves") != 0)
                added += scan_dir(set, path, depth + 1, max_depth, exts);
            continue;
        }
        if (has_ext(ent->d_name, exts)) {
            cat_names_add(set, ent->d_name);
            added++;
        }
    }
    closedir(d);
    return added;
}

int cat_names_scan(CatNameSet *set, const char *dir, int max_depth, const char *const *exts) {
    int added = scan_dir(set, dir, 0, max_depth, exts);
    cat_names_sort(set);
    return added;
}

// ---------------------------------------------------------------------------
// Install planning
// ---------------------------------------------------------------------------

static bool ext_is(const char *filename, const char *ext) {
    const char *dot = strrchr(filename, '.');
    return dot && strcasecmp(dot, ext) == 0;
}

CatPlan cat_plan_install(const char *system, const CatEntry *e) {
    CatPlan plan = { CAT_PLAN_NONE, "", false, "Can't be installed on a 3DS" };
    if (e->truncated) {
        plan.reason = "Name too long to download";
        return plan;
    }
    if (e->is_bundle) {
        plan.reason = "Folder entries can't be installed";
        return plan;
    }

    if (strcasecmp(system, "3DS") == 0) {
        if (ext_is(e->filename, ".cia")) {
            plan.kind = CAT_PLAN_CIA;
        } else if (ext_is(e->filename, ".3ds") || ext_is(e->filename, ".cci")) {
            if (e->can_extract_cia) {
                plan.kind = CAT_PLAN_CIA_CONVERT;
                plan.query = "?extract=cia";
            } else {
                plan.kind = CAT_PLAN_3DS_FILE;
            }
            plan.file_fallback = true;
        } else if (ext_is(e->filename, ".zip") && e->can_extract_cia) {
            plan.kind = CAT_PLAN_CIA_CONVERT;
            plan.query = "?extract=cia";
        } else if (ext_is(e->filename, ".zip")) {
            plan.reason = "Zipped, and the server can't\nconvert it: update the server";
        } else {
            plan.reason = "Only .cia, .3ds, .cci or a zipped\n.3ds can be installed";
        }
    } else if (strcasecmp(system, "NDS") == 0 || strcasecmp(system, "DSI") == 0) {
        if (ext_is(e->filename, ".nds") || ext_is(e->filename, ".dsi") ||
            ext_is(e->filename, ".srl")) {
            plan.kind = CAT_PLAN_NDS;
        } else if (ext_is(e->filename, ".zip") && e->can_extract_nds) {
            plan.kind = CAT_PLAN_NDS_EXTRACT;
            plan.query = "?extract=nds";
        } else if (ext_is(e->filename, ".zip")) {
            plan.reason = "The server can't unzip DS ROMs\nyet: update the server";
        } else {
            plan.reason = "Only .nds or single-ROM .zip\nfiles can be installed";
        }
    }
    return plan;
}

// ---------------------------------------------------------------------------
// CIA header (3dbrew: CIA, Title metadata)
// ---------------------------------------------------------------------------

#define CIA_HEADER_SIZE 0x2020
#define CIA_MAX_SECTION (16u * 1024 * 1024)

static uint32_t rd_le32(const uint8_t *p) {
    return (uint32_t)p[0] | ((uint32_t)p[1] << 8) | ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24);
}

static uint32_t rd_be32(const uint8_t *p) {
    return ((uint32_t)p[0] << 24) | ((uint32_t)p[1] << 16) | ((uint32_t)p[2] << 8) | (uint32_t)p[3];
}

static size_t align64(size_t v) {
    return (v + 63) & ~(size_t)63;
}

// Signature block size (signature + padding) for a TMD signature type
static size_t tmd_sig_size(uint32_t type) {
    switch (type) {
        case 0x010000: case 0x010003: return 0x200 + 0x3C;  // RSA-4096
        case 0x010001: case 0x010004: return 0x100 + 0x3C;  // RSA-2048
        case 0x010002: case 0x010005: return 0x3C + 0x40;   // ECDSA
        default: return 0;
    }
}

int cat_cia_title_id(const uint8_t *data, size_t len, uint64_t *title_id) {
    if (len < 0x20) return 0;
    uint32_t header_size = rd_le32(data);
    uint32_t cert_size = rd_le32(data + 0x08);
    uint32_t ticket_size = rd_le32(data + 0x0C);
    uint32_t tmd_size = rd_le32(data + 0x10);
    if (header_size != CIA_HEADER_SIZE || cert_size > CIA_MAX_SECTION ||
        ticket_size > CIA_MAX_SECTION || tmd_size < 0x200 || tmd_size > CIA_MAX_SECTION)
        return -1;

    size_t tmd_off = align64(header_size) + align64(cert_size) + align64(ticket_size);
    if (len < tmd_off + 4) return 0;
    size_t sig = tmd_sig_size(rd_be32(data + tmd_off));
    if (sig == 0) return -1;
    size_t tid_off = tmd_off + 4 + sig + 0x4C;
    if (len < tid_off + 8) return 0;

    uint64_t tid = 0;
    for (int i = 0; i < 8; i++) tid = (tid << 8) | data[tid_off + i];
    *title_id = tid;
    return 1;
}

// ---------------------------------------------------------------------------
// rom_id -> title id map
// ---------------------------------------------------------------------------

bool cat_parse_tid(const char *s, uint64_t *out) {
    uint64_t v = 0;
    int i = 0;
    for (; i < 16; i++) {
        int h = hex_val(s[i]);
        if (h < 0) return false;
        v = (v << 4) | (uint64_t)h;
    }
    if (s[i] != '\0' && s[i] != ' ' && s[i] != '\t' && s[i] != '\r' && s[i] != '\n') return false;
    *out = v;
    return true;
}

static int map_find(const CatCiaMap *map, const char *rom_id) {
    for (int i = 0; i < map->count; i++)
        if (strcmp(map->rom_ids[i], rom_id) == 0) return i;
    return -1;
}

void cat_cia_map_set(CatCiaMap *map, const char *rom_id, uint64_t tid) {
    if (!rom_id[0] || strlen(rom_id) >= CAT_ID_LEN) return;
    int i = map_find(map, rom_id);
    if (i >= 0) {
        map->tids[i] = tid;
        return;
    }
    if (map->count == map->cap) {
        int cap = map->cap ? map->cap * 2 : 16;
        char (*ids)[CAT_ID_LEN] = realloc(map->rom_ids, (size_t)cap * CAT_ID_LEN);
        if (!ids) return;
        map->rom_ids = ids;
        uint64_t *tids = realloc(map->tids, (size_t)cap * sizeof(uint64_t));
        if (!tids) return;
        map->tids = tids;
        map->cap = cap;
    }
    snprintf(map->rom_ids[map->count], CAT_ID_LEN, "%s", rom_id);
    map->tids[map->count] = tid;
    map->count++;
}

uint64_t cat_cia_map_get(const CatCiaMap *map, const char *rom_id) {
    int i = map_find(map, rom_id);
    return i >= 0 ? map->tids[i] : 0;
}

void cat_cia_map_load(CatCiaMap *map, const char *path) {
    FILE *f = fopen(path, "r");
    if (!f) return;
    char line[CAT_ID_LEN + 32];
    while (fgets(line, sizeof(line), f)) {
        size_t len = strlen(line);
        while (len > 0 && (line[len - 1] == '\n' || line[len - 1] == '\r')) line[--len] = '\0';
        uint64_t tid;
        if (len < 18 || line[16] != ' ' || !cat_parse_tid(line, &tid)) continue;
        cat_cia_map_set(map, line + 17, tid);
    }
    fclose(f);
}

bool cat_cia_map_save(const CatCiaMap *map, const char *path) {
    FILE *f = fopen(path, "w");
    if (!f) return false;
    for (int i = 0; i < map->count; i++)
        fprintf(f, "%016llX %s\n", (unsigned long long)map->tids[i], map->rom_ids[i]);
    return fclose(f) == 0;
}

void cat_cia_map_free(CatCiaMap *map) {
    free(map->rom_ids);
    free(map->tids);
    memset(map, 0, sizeof(*map));
}
