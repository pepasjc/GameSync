/*
 * catalog_cache.c — per-system ROM catalog cache on the HDD.
 * See catalog_cache.h for the file format and the refresh strategy.
 */

#include "catalog_cache.h"

#include <ctype.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define CACHE_MAGIC "GSCATALOG"
#define LINE_MAX_LEN 1024

void catcache_path(const char *dir, const char *system, char *out, size_t out_size) {
    snprintf(out, out_size, "%s/catalog_%s.dat",
             (dir && dir[0]) ? dir : ".", (system && system[0]) ? system : "ALL");
}

static void chomp(char *s) {
    size_t n = strlen(s);
    while (n > 0 && (s[n - 1] == '\n' || s[n - 1] == '\r')) s[--n] = '\0';
}

static void copy_field(char *dst, size_t dst_size, const char *src) {
    if (!dst || dst_size == 0) return;
    snprintf(dst, dst_size, "%s", src ? src : "");
}

/* Write a string with the separators this format uses replaced by spaces. */
static void put_clean(FILE *fp, const char *s) {
    for (; s && *s; s++) {
        char c = *s;
        if (c == '\t' || c == '\n' || c == '\r') c = ' ';
        fputc(c, fp);
    }
}

/* Header: magic+version, system, fingerprint, count.  Returns the declared
 * row count, or -1 when the header does not match. */
static int read_header(FILE *fp, const char *system, char *fp_out, size_t fp_size) {
    char line[LINE_MAX_LEN];
    char want[64];
    int count = -1;

    snprintf(want, sizeof(want), "%s %d", CACHE_MAGIC, CATALOG_CACHE_VERSION);
    if (!fgets(line, sizeof(line), fp)) return -1;
    chomp(line);
    if (strcmp(line, want) != 0) return -1;

    if (!fgets(line, sizeof(line), fp)) return -1;
    chomp(line);
    if (strncmp(line, "system=", 7) != 0 || strcmp(line + 7, system) != 0) return -1;

    if (!fgets(line, sizeof(line), fp)) return -1;
    chomp(line);
    if (strncmp(line, "fingerprint=", 12) != 0) return -1;
    if (fp_out && fp_size) copy_field(fp_out, fp_size, line + 12);

    if (!fgets(line, sizeof(line), fp)) return -1;
    chomp(line);
    if (strncmp(line, "count=", 6) != 0) return -1;
    count = atoi(line + 6);
    return count < 0 ? -1 : count;
}

bool catcache_peek(const char *dir, const char *system, char *fp_out, size_t fp_size) {
    char path[512];
    FILE *fp;
    int count;

    if (fp_out && fp_size) fp_out[0] = '\0';
    if (!system || !system[0]) return false;
    catcache_path(dir, system, path, sizeof(path));
    fp = fopen(path, "rb");
    if (!fp) return false;
    count = read_header(fp, system, fp_out, fp_size);
    fclose(fp);
    return count >= 0;
}

/* Split a row line on tabs in place.  Returns the number of fields. */
static int split_tabs(char *line, char **fields, int max_fields) {
    int n = 0;
    char *p = line;
    while (n < max_fields) {
        fields[n++] = p;
        char *tab = strchr(p, '\t');
        if (!tab) break;
        *tab = '\0';
        p = tab + 1;
    }
    return n;
}

bool catcache_load(const char *dir, const char *system,
                   char *fp_out, size_t fp_size, RomCatalog *catalog) {
    char path[512];
    char line[LINE_MAX_LEN];
    FILE *fp;
    int declared;

    if (fp_out && fp_size) fp_out[0] = '\0';
    if (!catalog || !system || !system[0]) return false;
    catalog->count = 0;
    catalog->last_error[0] = '\0';

    catcache_path(dir, system, path, sizeof(path));
    fp = fopen(path, "rb");
    if (!fp) return false;

    declared = read_header(fp, system, fp_out, fp_size);
    if (declared < 0) {
        fclose(fp);
        if (fp_out && fp_size) fp_out[0] = '\0';
        return false;
    }

    while (catalog->count < ROM_CATALOG_MAX && fgets(line, sizeof(line), fp)) {
        char *f[8];
        RomEntry *e;

        chomp(line);
        if (!line[0]) continue;
        if (split_tabs(line, f, 8) != 8) continue;

        e = &catalog->items[catalog->count];
        memset(e, 0, sizeof(*e));
        copy_field(e->rom_id, sizeof(e->rom_id), f[0]);
        copy_field(e->filename, sizeof(e->filename), f[1]);
        copy_field(e->name, sizeof(e->name), f[2]);
        copy_field(e->system, sizeof(e->system), f[3]);
        e->size = (uint64_t)strtoull(f[4], NULL, 10);
        e->is_bundle = atoi(f[5]) != 0;
        e->file_count = atoi(f[6]);
        copy_field(e->extract_format, sizeof(e->extract_format), f[7]);
        if (!e->rom_id[0] || !e->filename[0]) continue;
        if (!e->name[0]) copy_field(e->name, sizeof(e->name), e->filename);
        catalog->count++;
    }
    fclose(fp);

    /* A short file means the write was interrupted: treat it as no cache so
     * the caller refetches rather than showing half a library. */
    if (catalog->count != declared &&
        !(declared > ROM_CATALOG_MAX && catalog->count == ROM_CATALOG_MAX)) {
        catalog->count = 0;
        if (fp_out && fp_size) fp_out[0] = '\0';
        return false;
    }
    return true;
}

static bool same_system(const char *a, const char *b) {
    if (!a || !b) return false;
    while (*a && *b) {
        if (toupper((unsigned char)*a) != toupper((unsigned char)*b)) return false;
        a++; b++;
    }
    return *a == *b;
}

bool catcache_save(const char *dir, const char *system, const char *fingerprint,
                   const RomCatalog *catalog) {
    char path[512];
    char tmp[520];
    FILE *fp;
    int rows = 0;
    bool ok;

    if (!catalog || !system || !system[0]) return false;
    catcache_path(dir, system, path, sizeof(path));
    snprintf(tmp, sizeof(tmp), "%s.part", path);

    for (int i = 0; i < catalog->count; i++) {
        const RomEntry *e = &catalog->items[i];
        if (e->system[0] && !same_system(e->system, system)) continue;
        rows++;
    }

    fp = fopen(tmp, "wb");
    if (!fp) return false;
    fprintf(fp, "%s %d\n", CACHE_MAGIC, CATALOG_CACHE_VERSION);
    fprintf(fp, "system=%s\n", system);
    fputs("fingerprint=", fp);
    put_clean(fp, fingerprint ? fingerprint : "");
    fputc('\n', fp);
    fprintf(fp, "count=%d\n", rows);
    for (int i = 0; i < catalog->count; i++) {
        const RomEntry *e = &catalog->items[i];
        if (e->system[0] && !same_system(e->system, system)) continue;
        put_clean(fp, e->rom_id);     fputc('\t', fp);
        put_clean(fp, e->filename);   fputc('\t', fp);
        put_clean(fp, e->name);       fputc('\t', fp);
        put_clean(fp, e->system[0] ? e->system : system); fputc('\t', fp);
        fprintf(fp, "%llu\t%d\t%d\t", (unsigned long long)e->size,
                e->is_bundle ? 1 : 0, e->file_count);
        put_clean(fp, e->extract_format);
        fputc('\n', fp);
    }
    ok = (ferror(fp) == 0);
    if (fclose(fp) != 0) ok = false;
    if (!ok) {
        remove(tmp);
        return false;
    }
    /* The PS3 filesystem does not replace on rename: drop the old file first. */
    remove(path);
    if (rename(tmp, path) != 0) {
        remove(tmp);
        return false;
    }
    return true;
}

void catcache_drop(const char *dir, const char *system) {
    char path[512];
    if (!system || !system[0]) return;
    catcache_path(dir, system, path, sizeof(path));
    remove(path);
}

/* --- /roms/fingerprints parsing --- */

static const char *skip_ws(const char *p) {
    while (*p == ' ' || *p == '\t' || *p == '\n' || *p == '\r') p++;
    return p;
}

/* End of the object starting at '{' (pointer to its '}'), skipping strings. */
static const char *object_end(const char *p) {
    int depth = 0;
    for (; *p; p++) {
        if (*p == '"') {
            for (p++; *p && *p != '"'; p++)
                if (*p == '\\' && p[1]) p++;
            if (!*p) return NULL;
        } else if (*p == '{') {
            depth++;
        } else if (*p == '}') {
            if (--depth == 0) return p;
        }
    }
    return NULL;
}

/* Value position of "key": inside [p, end), at the object's top level. */
static const char *find_value(const char *p, const char *end, const char *key) {
    char needle[64];
    int n = snprintf(needle, sizeof(needle), "\"%s\"", key);
    int depth = 0;
    while (p < end) {
        if (*p == '{' || *p == '[') { depth++; p++; continue; }
        if (*p == '}' || *p == ']') { depth--; p++; continue; }
        if (*p == '"') {
            if (depth == 0 && end - p >= n && strncmp(p, needle, (size_t)n) == 0) {
                const char *q = skip_ws(p + n);
                if (*q == ':') return skip_ws(q + 1);
            }
            for (p++; p < end && *p != '"'; p++)
                if (*p == '\\' && p + 1 < end) p++;
        }
        p++;
    }
    return NULL;
}

int catcache_fingerprint_for(const char *json, const char *system,
                             char *fp_out, size_t fp_size, int *count_out) {
    const char *sys_v, *sys_end, *obj, *obj_end, *v;

    if (fp_out && fp_size) fp_out[0] = '\0';
    if (count_out) *count_out = 0;
    if (!json || !system || !system[0]) return -1;

    obj = skip_ws(json);
    if (*obj != '{') return -1;
    obj_end = object_end(obj);
    if (!obj_end) return -1;

    sys_v = find_value(obj + 1, obj_end, "systems");
    if (!sys_v || *sys_v != '{') return -1;
    sys_end = object_end(sys_v);
    if (!sys_end) return -1;

    v = find_value(sys_v + 1, sys_end, system);
    if (!v || *v != '{') return 0;
    obj_end = object_end(v);
    if (!obj_end) return -1;

    const char *fv = find_value(v + 1, obj_end, "fingerprint");
    if (fv && fp_out && fp_size) {
        size_t len = 0;
        if (*fv == '"') {
            for (fv++; fv < obj_end && *fv != '"' && len + 1 < fp_size; fv++)
                fp_out[len++] = *fv;
        } else {
            /* Tolerate a bare (numeric) fingerprint. */
            for (; fv < obj_end && *fv != ',' && *fv != '}' &&
                   !isspace((unsigned char)*fv) && len + 1 < fp_size; fv++)
                fp_out[len++] = *fv;
        }
        fp_out[len] = '\0';
    }
    const char *cv = find_value(v + 1, obj_end, "count");
    if (cv && count_out) *count_out = atoi(cv);
    return 1;
}
