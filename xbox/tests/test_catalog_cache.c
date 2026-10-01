// Host tests for the Xbox client's catalog cache (source/catalog_cache.c).
// Run with xbox/tests/run_host_tests.sh. Set CATALOG_JSON to a saved
// /api/v1/roms?system=XBOX response to also round-trip a real catalog page.

#include "catalog_cache.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static int g_fail = 0;

#define CHECK(cond) do { \
    if (!(cond)) { \
        fprintf(stderr, "%s:%d: CHECK failed: %s\n", __FILE__, __LINE__, #cond); \
        g_fail++; \
    } \
} while (0)

static const char PAGE[] =
    "{\"roms\":[{\"rom_id\":\"XBOX_BUNDLE_007\",\"system\":\"XBOX\","
    "\"name\":\"007 - Agent \\\"Under\\\" Fire (USA)\","
    "\"filename\":\"007.zip\",\"size\":1431043582,\"is_bundle\":true,"
    "\"files\":[{\"name\":\"nested.cci\",\"size\":1}],"
    "\"extract_formats\":[\"cci\",\"folder\"]},"
    "{\"files\":[{\"name\":\"first-nested\",\"size\":7}],"
    "\"rom_id\":\"XBOX_halo\",\"name\":\"Halo\\tCE\",\"filename\":\"halo.iso\","
    "\"size\":42,\"is_bundle\":false},"
    "{\"rom_id\":\"\",\"name\":\"skipped (no id)\"}"
    "],\"total\":3,\"offset\":0,\"limit\":500,\"has_more\":true,\"has_ra\":null}";

static void test_parse_page(void)
{
    XboxRomList list = { 0, 0, NULL };
    int rows = -1, more = -1;
    CHECK(catcache_parse_roms_page(PAGE, &list, &rows, &more) == 0);
    CHECK(rows == 3);
    CHECK(more == 1);
    CHECK(list.count == 2);
    CHECK(strcmp(list.roms[0].rom_id, "XBOX_BUNDLE_007") == 0);
    CHECK(strcmp(list.roms[0].name, "007 - Agent \"Under\" Fire (USA)") == 0);
    CHECK(list.roms[0].size == 1431043582ULL);
    CHECK(list.roms[0].is_bundle == 1);
    // Top-level keys only: the nested "files" entries must not leak in.
    CHECK(strcmp(list.roms[1].name, "Halo CE") == 0);
    CHECK(list.roms[1].size == 42);
    CHECK(list.roms[1].is_bundle == 0);

    // A second page appends.
    CHECK(catcache_parse_roms_page(
              "{\"roms\":[{\"rom_id\":\"x\",\"name\":\"X\",\"size\":1}],"
              "\"has_more\":false}", &list, &rows, &more) == 0);
    CHECK(list.count == 3 && rows == 1 && more == 0);

    CHECK(catcache_parse_roms_page("not json", &list, &rows, &more) == -1);
    CHECK(catcache_parse_roms_page("{\"detail\":\"x\"}", &list, &rows, &more) == -1);
    rom_list_free(&list);
}

static void test_fingerprints(void)
{
    const char *body =
        "{\"systems\":{\"XBOX360\":{\"fingerprint\":\"nope\",\"count\":1},"
        "\"XBOX\":{\"fingerprint\":\"abc123\",\"count\":925},"
        "\"PS2\":{\"fingerprint\":\"zz\",\"count\":3}}}";
    char fp[CATCACHE_FP_MAX];
    int count = 0;
    CHECK(catcache_find_fingerprint(body, "XBOX", fp, sizeof(fp), &count) == 1);
    CHECK(strcmp(fp, "abc123") == 0);
    CHECK(count == 925);
    CHECK(catcache_find_fingerprint(body, "GC", fp, sizeof(fp), &count) == 0);
    CHECK(catcache_find_fingerprint("{\"systems\":{}}", "XBOX", fp,
                                    sizeof(fp), &count) == 0);
    CHECK(catcache_find_fingerprint("{\"detail\":\"Not Found\"}", "XBOX", fp,
                                    sizeof(fp), &count) == -1);
    CHECK(catcache_find_fingerprint("<html>", "XBOX", fp, sizeof(fp),
                                    &count) == -1);
}

static void fill(XboxRomList *list, int n)
{
    for (int i = 0; i < n; i++) {
        XboxRomEntry *r = rom_list_push(list);
        CHECK(r != NULL);
        if (!r) return;
        snprintf(r->rom_id, sizeof(r->rom_id), "XBOX_%04d", i);
        snprintf(r->name, sizeof(r->name), "Game %d\twith tab", i);
        snprintf(r->filename, sizeof(r->filename), "game%d.zip", i);
        r->size = 4000000000ULL + (uint64_t)i;
        r->is_bundle = i & 1;
    }
}

static void test_roundtrip(void)
{
    XboxRomList list = { 0, 0, NULL };
    fill(&list, 1000);   // past the old 512 cap, and through several grows
    CHECK(list.count == 1000);

    size_t len = 0;
    char *buf = catcache_serialize("XBOX", "fp1", &list, &len);
    CHECK(buf != NULL && len > 0);

    XboxRomList back = { 0, 0, NULL };
    char fp[CATCACHE_FP_MAX];
    CHECK(catcache_parse(buf, len, "XBOX", fp, sizeof(fp), &back) == 0);
    CHECK(strcmp(fp, "fp1") == 0);
    CHECK(back.count == 1000);
    for (int i = 0; i < back.count && i < list.count; i++) {
        CHECK(strcmp(back.roms[i].rom_id, list.roms[i].rom_id) == 0);
        CHECK(strcmp(back.roms[i].filename, list.roms[i].filename) == 0);
        CHECK(back.roms[i].size == list.roms[i].size);
        CHECK(back.roms[i].is_bundle == list.roms[i].is_bundle);
    }
    CHECK(strcmp(back.roms[3].name, "Game 3 with tab") == 0);

    // Wrong system, torn file, other version: rejected and left empty.
    CHECK(catcache_parse(buf, len, "PS2", fp, sizeof(fp), &back) == -1);
    CHECK(back.count == 0);
    CHECK(catcache_parse(buf, len / 2, "XBOX", fp, sizeof(fp), &back) == -1);
    CHECK(back.count == 0);
    CHECK(catcache_parse(buf, len - 1, "XBOX", fp, sizeof(fp), &back) == -1);
    buf[7] = '9';   // "GSXCAT 9"
    CHECK(catcache_parse(buf, len, "XBOX", fp, sizeof(fp), &back) == -1);
    CHECK(catcache_parse("", 0, "XBOX", fp, sizeof(fp), &back) == -1);

    // An empty catalog is a valid cache.
    free(buf);
    rom_list_clear(&list);
    buf = catcache_serialize("XBOX", "fp0", &list, &len);
    CHECK(catcache_parse(buf, len, "XBOX", fp, sizeof(fp), &back) == 0);
    CHECK(back.count == 0 && strcmp(fp, "fp0") == 0);

    free(buf);
    rom_list_free(&list);
    rom_list_free(&back);
}

static void test_take(void)
{
    XboxRomList a = { 0, 0, NULL }, b = { 0, 0, NULL };
    fill(&a, 3);
    fill(&b, 5);
    rom_list_take(&a, &b);
    CHECK(a.count == 5 && b.count == 0 && b.roms == NULL);
    rom_list_free(&a);
}

static void test_real_page(const char *path)
{
    FILE *f = fopen(path, "rb");
    if (!f) {
        fprintf(stderr, "cannot open %s\n", path);
        g_fail++;
        return;
    }
    fseek(f, 0, SEEK_END);
    long n = ftell(f);
    fseek(f, 0, SEEK_SET);
    char *body = (char *)malloc((size_t)n + 1);
    size_t got = fread(body, 1, (size_t)n, f);
    fclose(f);
    body[got] = '\0';

    XboxRomList list = { 0, 0, NULL };
    int rows = 0, more = 0;
    CHECK(catcache_parse_roms_page(body, &list, &rows, &more) == 0);
    CHECK(list.count > 0 && list.count == rows);
    size_t len = 0;
    char *buf = catcache_serialize("XBOX", "real", &list, &len);
    XboxRomList back = { 0, 0, NULL };
    char fp[CATCACHE_FP_MAX];
    CHECK(catcache_parse(buf, len, "XBOX", fp, sizeof(fp), &back) == 0);
    CHECK(back.count == list.count);
    printf("real page: %d rows, has_more=%d, cache %lu bytes\n",
           list.count, more, (unsigned long)len);
    free(buf);
    free(body);
    rom_list_free(&list);
    rom_list_free(&back);
}

int main(int argc, char **argv)
{
    test_parse_page();
    test_fingerprints();
    test_roundtrip();
    test_take();
    if (argc > 1) test_real_page(argv[1]);
    if (g_fail) {
        fprintf(stderr, "%d check(s) failed\n", g_fail);
        return 1;
    }
    printf("catalog cache: all tests passed\n");
    return 0;
}
