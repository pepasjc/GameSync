/*
 * catcache.c — ROM catalog cache on SD (see catcache.h).
 *
 * Ported from the MiSTer client's catalog cache (mister/gamesync/
 * catalogcache.py, GameSync) to a compact line format the GameCube can
 * stream with a single line buffer.
 */

#include "catcache.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

#define MAGIC    "GSCATALOG"
#define END_MARK "END"

bool catcache_path(const char *sd_root, const char *system, char *out, size_t out_size) {
    if (!out || out_size == 0 || !system || !system[0]) return false;
    int n = snprintf(out, out_size, "%s%s/cache/catalog_%s.tsv",
                     sd_root ? sd_root : "", APP_DATA_SUBDIR, system);
    return n > 0 && (size_t)n < out_size;
}

static void chomp(char *s) {
    size_t n = strlen(s);
    while (n > 0 && (s[n - 1] == '\n' || s[n - 1] == '\r')) s[--n] = '\0';
}

/* Split `s` in place on tabs; returns the field count (<= max). */
static int split_tabs(char *s, char **fields, int max) {
    int n = 0;
    fields[n++] = s;
    for (char *p = s; *p && n < max; p++) {
        if (*p == '\t') { *p = '\0'; fields[n++] = p + 1; }
    }
    return n;
}

static void copy_field(char *dst, size_t cap, const char *src) {
    snprintf(dst, cap, "%s", src ? src : "");
}

/* Header check; on success `count` holds the promised row count. */
static bool read_header(FILE *fp, const char *system, char *fp_out, size_t fp_size,
                        int *count) {
    char line[256];
    if (!fgets(line, sizeof(line), fp)) return false;
    chomp(line);
    char want[32];
    snprintf(want, sizeof(want), MAGIC " %d", CATCACHE_VERSION);
    if (strcmp(line, want) != 0) return false;

    if (!fgets(line, sizeof(line), fp)) return false;
    chomp(line);
    char *f[3];
    if (split_tabs(line, f, 3) != 3) return false;
    if (strcmp(f[0], system) != 0) return false;
    char *end = NULL;
    long c = strtol(f[2], &end, 10);
    if (end == f[2] || c < 0) return false;
    if (fp_out && fp_size) copy_field(fp_out, fp_size, f[1]);
    if (count) *count = (int)c;
    return true;
}

bool catcache_read_fingerprint(const char *path, const char *system,
                               char *fp_out, size_t fp_size) {
    if (!path || !system) return false;
    FILE *fp = fopen(path, "rb");
    if (!fp) return false;
    bool ok = read_header(fp, system, fp_out, fp_size, NULL);
    fclose(fp);
    return ok;
}

bool catcache_load(const char *path, const char *system,
                   char *fp_out, size_t fp_size, RomCatalog *cat) {
    if (!cat) return false;
    cat->count = 0;
    cat->last_error[0] = '\0';
    if (!path || !system) return false;
    FILE *fp = fopen(path, "rb");
    if (!fp) return false;

    int want = 0;
    if (!read_header(fp, system, fp_out, fp_size, &want)) { fclose(fp); return false; }

    /* rom_id + size + fmt + filename + name, worst case ~520 bytes */
    char line[640];
    while (cat->count < ROM_CATALOG_MAX && cat->count < want && fgets(line, sizeof(line), fp)) {
        chomp(line);
        char *f[5];
        if (split_tabs(line, f, 5) != 5) break;
        RomEntry *e = &cat->items[cat->count];
        memset(e, 0, sizeof(*e));
        copy_field(e->rom_id, sizeof(e->rom_id), f[0]);
        e->size = (uint64_t)strtoull(f[1], NULL, 10);
        copy_field(e->extract_format, sizeof(e->extract_format), f[2]);
        copy_field(e->filename, sizeof(e->filename), f[3]);
        copy_field(e->name, sizeof(e->name), f[4]);
        copy_field(e->system, sizeof(e->system), system);
        if (!e->rom_id[0] || !e->filename[0]) break;
        cat->count++;
    }
    /* Skip rows past ROM_CATALOG_MAX, then require the end marker: a file
     * cut short anywhere (even mid-row) is refetched, never half-shown. */
    bool end_ok = false;
    while (fgets(line, sizeof(line), fp)) {
        chomp(line);
        if (strcmp(line, END_MARK) == 0) { end_ok = true; break; }
    }
    fclose(fp);

    int expect = want < ROM_CATALOG_MAX ? want : ROM_CATALOG_MAX;
    if (!end_ok || cat->count != expect) { cat->count = 0; return false; }
    return true;
}

/* Values never contain the separators: tabs / newlines become spaces. */
static void put_clean(FILE *fp, const char *s) {
    for (; s && *s; s++) {
        char c = *s;
        if (c == '\t' || c == '\n' || c == '\r') c = ' ';
        fputc(c, fp);
    }
}

static void mkdir_parent(const char *path) {
    char buf[256];
    snprintf(buf, sizeof(buf), "%s", path);
    char *p = strchr(buf, ':');
    p = p ? p + 1 : buf;
    if (*p == '/') p++;
    for (; *p; p++) {
        if (*p == '/') {
            *p = '\0';
            mkdir(buf, 0777);
            *p = '/';
        }
    }
}

bool catcache_save(const char *path, const char *system, const char *fpr,
                   const RomCatalog *cat) {
    if (!path || !system || !cat) return false;
    mkdir_parent(path);

    char part[264];
    snprintf(part, sizeof(part), "%s.part", path);
    FILE *fp = fopen(part, "wb");
    if (!fp) return false;
    /* One fat buffer: the SD write path is far faster in big blocks. */
    static char vbuf[32 * 1024];
    setvbuf(fp, vbuf, _IOFBF, sizeof(vbuf));

    fprintf(fp, MAGIC " %d\n", CATCACHE_VERSION);
    put_clean(fp, system);
    fputc('\t', fp);
    put_clean(fp, fpr ? fpr : "");
    fprintf(fp, "\t%d\n", cat->count);
    for (int i = 0; i < cat->count; i++) {
        const RomEntry *e = &cat->items[i];
        put_clean(fp, e->rom_id);
        fprintf(fp, "\t%llu\t", (unsigned long long)e->size);
        put_clean(fp, e->extract_format);
        fputc('\t', fp);
        put_clean(fp, e->filename);
        fputc('\t', fp);
        put_clean(fp, e->name);
        fputc('\n', fp);
    }
    fputs(END_MARK "\n", fp);
    bool ok = !ferror(fp);
    if (fclose(fp) != 0) ok = false;
    if (!ok) { unlink(part); return false; }

    /* FAT rename won't replace an existing file. */
    if (rename(part, path) != 0) {
        unlink(path);
        if (rename(part, path) != 0) { unlink(part); return false; }
    }
    return true;
}

void catcache_wipe(const char *path) {
    if (!path) return;
    unlink(path);
    char part[264];
    snprintf(part, sizeof(part), "%s.part", path);
    unlink(part);
}
