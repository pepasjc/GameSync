/*
 * Host tests for the Vita client's catalog cache (source/catcache.c).
 * Build + run: sh vita/tests/run_host_tests.sh
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "catcache.h"
#include "test_util.h"

static RomCatalog g_a, g_b;

static void make_rows(RomCatalog *c, const char *system, int n, const char *tag) {
    memset(c, 0, sizeof(*c));
    for (int i = 0; i < n; i++) {
        RomEntry *e = &c->items[i];
        snprintf(e->rom_id, sizeof(e->rom_id), "%s_%s_%04d", system, tag, i);
        snprintf(e->filename, sizeof(e->filename), "Game %d (USA) [%s].chd", i, tag);
        snprintf(e->name, sizeof(e->name), "Game \"%d\" %s", i, tag);
        snprintf(e->system, sizeof(e->system), "%s", system);
        snprintf(e->title_id, sizeof(e->title_id), "SLUS%05d", i);
        e->size = 700ULL * 1024 * 1024 * 1024 + (uint64_t)i;   /* > 4 GB */
        e->is_bundle = (i % 3) == 0;
        e->file_count = i % 7;
        snprintf(e->extract_format, sizeof(e->extract_format), "%s", i % 2 ? "cso" : "");
        e->disc_index = 1;
        e->disc_total = 1 + (i % 4);
    }
    c->count = n;
}

static bool same_rows(const RomCatalog *a, const RomCatalog *b) {
    if (a->count != b->count) return false;
    for (int i = 0; i < a->count; i++) {
        const RomEntry *x = &a->items[i], *y = &b->items[i];
        if (strcmp(x->rom_id, y->rom_id) || strcmp(x->filename, y->filename) ||
            strcmp(x->name, y->name) || strcmp(x->system, y->system) ||
            strcmp(x->title_id, y->title_id) || x->size != y->size ||
            x->is_bundle != y->is_bundle || x->file_count != y->file_count ||
            strcmp(x->extract_format, y->extract_format) ||
            x->disc_index != y->disc_index || x->disc_total != y->disc_total)
            return false;
    }
    return true;
}

static void test_fingerprints(void) {
    const char *json =
        "{\"systems\": {\"PSP\": {\"fingerprint\": \"abc123\", \"count\": 12},"
        " \"PS1\": {\"count\": 3, \"fingerprint\": \"d\\\"ef\", \"extra\": [1, {\"x\": \"}\"}]},"
        " \"TOOLONGSYSTEM\": {\"fingerprint\": \"zz\"}},"
        " \"other\": 1}";
    CatFingerprints f;
    CHECK(catcache_parse_fingerprints(json, strlen(json), &f));
    CHECK(f.count == 2);
    const CatFingerprint *psp = catcache_find_fp(&f, "PSP");
    const CatFingerprint *ps1 = catcache_find_fp(&f, "PS1");
    CHECK(psp && ps1);
    if (psp) { CHECK_STR(psp->fingerprint, "abc123"); CHECK(psp->count == 12); }
    if (ps1) { CHECK_STR(ps1->fingerprint, "d\"ef"); CHECK(ps1->count == 3); }
    CHECK(catcache_find_fp(&f, "GBA") == NULL);

    const char *empty = "{\"systems\": {}}";
    CHECK(catcache_parse_fingerprints(empty, strlen(empty), &f));
    CHECK(f.count == 0);

    const char *bad = "{\"detail\": \"Not Found\"}";
    CHECK(!catcache_parse_fingerprints(bad, strlen(bad), &f));
    CHECK(!catcache_parse_fingerprints("<html>", 6, &f));
    /* Truncated body */
    CHECK(!catcache_parse_fingerprints(json, 40, &f));
}

static void test_roundtrip(const char *path) {
    catcache_set_path(path);
    catcache_clear();

    CHECK(!catcache_load("PSP", &g_b));
    CHECK(g_b.count == 0);

    const char *srv = "{\"systems\": {\"PSP\": {\"fingerprint\": \"f1\", \"count\": 2000},"
                      " \"PS1\": {\"fingerprint\": \"g1\", \"count\": 5}}}";
    CatFingerprints f;
    CHECK(catcache_parse_fingerprints(srv, strlen(srv), &f));
    CHECK(catcache_plan("PSP", &f) == CATCACHE_STALE);
    CHECK(catcache_plan("PS1", &f) == CATCACHE_STALE);
    CHECK(catcache_plan("GBA", &f) == CATCACHE_GONE);

    make_rows(&g_a, "PSP", 2000, "a");
    CHECK(catcache_put("PSP", "f1", &g_a));
    CHECK(catcache_load("PSP", &g_b));
    CHECK(same_rows(&g_a, &g_b));
    CHECK(catcache_plan("PSP", &f) == CATCACHE_FRESH);
    CHECK(catcache_plan("PS1", &f) == CATCACHE_STALE);

    /* Second system next to the first */
    make_rows(&g_a, "PS1", 5, "b");
    CHECK(catcache_put("PS1", "g1", &g_a));
    CHECK(catcache_load("PS1", &g_b));
    CHECK(same_rows(&g_a, &g_b));
    char fp[CATCACHE_FP_LEN];
    int count = -1;
    CHECK(catcache_info("PSP", fp, sizeof(fp), &count));
    CHECK_STR(fp, "f1");
    CHECK(count == 2000);

    /* Replace PSP: PS1 must survive, PSP gets the new rows */
    make_rows(&g_a, "PSP", 17, "c");
    CHECK(catcache_put("PSP", "f2", &g_a));
    CHECK(catcache_load("PSP", &g_b));
    CHECK(same_rows(&g_a, &g_b));
    CHECK(catcache_plan("PSP", &f) == CATCACHE_STALE);   /* server still says f1 */
    make_rows(&g_a, "PS1", 5, "b");
    CHECK(catcache_load("PS1", &g_b));
    CHECK(same_rows(&g_a, &g_b));

    /* Empty system is a valid cached state */
    g_a.count = 0;
    CHECK(catcache_put("PSP", "f3", &g_a));
    CHECK(catcache_load("PSP", &g_b));
    CHECK(g_b.count == 0);

    /* Server stops listing PS1: dropped */
    const char *only_psp = "{\"systems\": {\"PSP\": {\"fingerprint\": \"f3\"}}}";
    CHECK(catcache_parse_fingerprints(only_psp, strlen(only_psp), &f));
    CHECK(catcache_plan("PS1", &f) == CATCACHE_GONE);
    CHECK(!catcache_info("PS1", NULL, 0, NULL));
    CHECK(catcache_plan("PSP", &f) == CATCACHE_FRESH);

    /* Long strings are clipped, not overflowed */
    memset(&g_a, 0, sizeof(g_a));
    memset(g_a.items[0].name, 'N', sizeof(g_a.items[0].name) - 1);
    memset(g_a.items[0].filename, 'F', sizeof(g_a.items[0].filename) - 1);
    snprintf(g_a.items[0].rom_id, sizeof(g_a.items[0].rom_id), "X");
    g_a.count = 1;
    CHECK(catcache_put("PS1", "h", &g_a));
    CHECK(catcache_load("PS1", &g_b));
    CHECK(g_b.count == 1 && strlen(g_b.items[0].name) == sizeof(g_b.items[0].name) - 1);

    catcache_clear();
    CHECK(!catcache_info("PSP", NULL, 0, NULL));
}

static void test_damaged(const char *path) {
    catcache_set_path(path);
    catcache_clear();
    make_rows(&g_a, "PSP", 50, "a");
    CHECK(catcache_put("PSP", "f1", &g_a));
    make_rows(&g_a, "PS1", 50, "b");
    CHECK(catcache_put("PS1", "g1", &g_a));

    /* Chop the tail off (interrupted write): PS1 is lost, PSP survives,
     * and a later put still produces a valid file. */
    FILE *f = fopen(path, "rb");
    CHECK(f != NULL);
    if (!f) return;
    fseek(f, 0, SEEK_END);
    long size = ftell(f);
    fseek(f, 0, SEEK_SET);
    char *buf = malloc((size_t)size);
    CHECK(fread(buf, 1, (size_t)size, f) == (size_t)size);
    fclose(f);
    f = fopen(path, "wb");
    fwrite(buf, 1, (size_t)size - 100, f);
    fclose(f);

    CHECK(!catcache_load("PS1", &g_b));
    CHECK(g_b.count == 0);
    make_rows(&g_a, "PSP", 50, "a");
    CHECK(catcache_load("PSP", &g_b));
    CHECK(same_rows(&g_a, &g_b));
    make_rows(&g_a, "PS1", 3, "z");
    CHECK(catcache_put("PS1", "g2", &g_a));
    CHECK(catcache_load("PS1", &g_b));
    CHECK(same_rows(&g_a, &g_b));

    /* Foreign version: treated as empty */
    buf[4] = (char)(CATCACHE_VERSION + 1);
    f = fopen(path, "wb");
    fwrite(buf, 1, (size_t)size, f);
    fclose(f);
    CHECK(!catcache_load("PSP", &g_b));
    free(buf);
    catcache_clear();
}

int main(int argc, char **argv) {
    char path[512];
    snprintf(path, sizeof(path), "%s/catalog_cache.bin", argc > 1 ? argv[1] : ".");
    test_fingerprints();
    test_roundtrip(path);
    test_damaged(path);
    TEST_DONE();
}
