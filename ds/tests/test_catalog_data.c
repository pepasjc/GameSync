// Host test for catalog_data.c. Usage: test_catalog_data <scratch dir> [catalog.json]
// The optional JSON is a full /api/v1/roms?system=NDS response to parse as a
// stress test (its entry count and RA count are printed).
#include "catalog_data.h"
#include "test_util.h"
#include <stdlib.h>
#include <sys/stat.h>

static const char page_json[] =
    "{\"roms\": [\n"
    "  {\"rom_id\": \"NDS_chou_kowai_hanashi_ds_ao_no_shou_japan\", \"title_id\": \"x\","
    "   \"system\": \"NDS\", \"name\": \"'Chou' Kowai Hanashi DS - Ao no Shou (Japan)\","
    "   \"filename\": \"'Chou' Kowai Hanashi DS - Ao no Shou (Japan).zip\","
    "   \"path\": \"nds/'Chou' Kowai Hanashi DS - Ao no Shou (Japan).zip\", \"size\": 76514534,"
    "   \"crc32\": \"\", \"source\": \"dat_filename\", \"is_bundle\": false,"
    "   \"extract_formats\": [\"nds\"],"
    "   \"ra_game_id\": 28100, \"ra_achievements\": 0, \"ra_title\": \"Chou\", \"ra_match\": \"hash\","
    "   \"ra_hash\": \"94985f9c547118060a0ef396dec72a8c\"},\n"
    "  {\"rom_id\": \"NDS_pokemon\", \"name\": \"Pok\\u00e9mon \\\"Quoted\\\" \\\\ Game\","
    "   \"filename\": \"Pokemon.nds\", \"size\": 1234, \"is_bundle\": true, \"file_count\": 2,"
    "   \"files\": [{\"name\": \"a]b}c\", \"size\": 1}, {\"name\": \"d\", \"size\": 2}],"
    "   \"ra_game_id\": 1, \"ra_achievements\": 45, \"ra_match\": \"title\", \"crc32\": null},\n"
    "  {\"rom_id\": \"NDS_plain\", \"name\": \"Plain\", \"filename\": \"Plain (USA).zip\","
    "   \"size\": null, \"ra_achievements\": -1, \"extract_formats\": [\"cia\", 3, null]}\n"
    "], \"total\": 3655, \"offset\": 0, \"limit\": 3, \"has_more\": true, \"has_ra\": null}";

static void test_parse_page(void) {
    CatEntry e[4];
    CatPageInfo info;
    int n = cat_parse_page(page_json, strlen(page_json), e, 4, &info);
    CHECK(n == 3);
    CHECK(info.total == 3655);
    CHECK(info.has_more);
    CHECK(info.has_ra == -1);

    CHECK_STR(e[0].rom_id, "NDS_chou_kowai_hanashi_ds_ao_no_shou_japan");
    CHECK_STR(e[0].name, "'Chou' Kowai Hanashi DS - Ao no Shou (Japan)");
    CHECK_STR(e[0].filename, "'Chou' Kowai Hanashi DS - Ao no Shou (Japan).zip");
    CHECK(e[0].size == 76514534u);
    CHECK(e[0].ra_game_id == 28100);
    CHECK(e[0].ra_achievements == 0);
    CHECK(!cat_entry_has_ra(&e[0]));
    CHECK(e[0].can_extract_nds);
    CHECK(!e[0].ra_title_only);
    CHECK(!e[0].truncated);

    CHECK_STR(e[1].name, "Pok\xc3\xa9mon \"Quoted\" \\ Game");
    CHECK(e[1].ra_achievements == 45);
    CHECK(cat_entry_has_ra(&e[1]));
    CHECK(e[1].ra_title_only);
    CHECK(!e[1].can_extract_nds);

    CHECK(e[2].size == 0);
    CHECK(e[2].ra_achievements == -1);
    CHECK(!cat_entry_has_ra(&e[2]));
    CHECK(!e[2].can_extract_nds);

    // Fewer slots than entries: the rest is skipped, not an error
    n = cat_parse_page(page_json, strlen(page_json), e, 1, &info);
    CHECK(n == 1);
    CHECK(info.total == 3655);

    // has_ra echo
    const char ra_on[] = "{\"roms\":[],\"total\":0,\"has_ra\":true}";
    n = cat_parse_page(ra_on, strlen(ra_on), e, 4, &info);
    CHECK(n == 0 && info.has_ra == 1 && info.total == 0 && !info.has_more);

    // Old server: no total -> count
    const char no_total[] = "{\"roms\":[{\"rom_id\":\"a\"}]}";
    n = cat_parse_page(no_total, strlen(no_total), e, 4, &info);
    CHECK(n == 1 && info.total == 1);

    // Long rom_id is flagged, not silently cut
    char long_json[600];
    char long_id[400];
    memset(long_id, 'x', sizeof(long_id) - 1);
    long_id[sizeof(long_id) - 1] = '\0';
    snprintf(long_json, sizeof(long_json), "{\"roms\":[{\"rom_id\":\"%s\",\"name\":\"n\"}]}", long_id);
    n = cat_parse_page(long_json, strlen(long_json), e, 4, &info);
    CHECK(n == 1 && e[0].truncated);
    CHECK(strlen(e[0].rom_id) == CAT_ID_LEN - 1);

    // Malformed: truncated anywhere must fail cleanly, never crash
    for (size_t cut = 0; cut < strlen(page_json) - 1; cut++) {
        int r = cat_parse_page(page_json, cut, e, 4, &info);
        if (r != -1) {
            fprintf(stderr, "cut at %zu parsed as %d\n", cut, r);
            CHECK(r == -1);
            break;
        }
    }
    CHECK(cat_parse_page("[]", 2, e, 4, &info) == -1);
    CHECK(cat_parse_page("{\"total\":3}", 11, e, 4, &info) == -1);  // no roms
    CHECK(cat_parse_page("{\"roms\":[1,]}", 13, e, 4, &info) == -1);
    const char bad_escape[] = "{\"roms\":[{\"name\":\"a\\qb\"}]}";
    CHECK(cat_parse_page(bad_escape, strlen(bad_escape), e, 4, &info) == -1);

    // Deep nesting is refused, not recursed into forever
    char deep[200];
    int p = snprintf(deep, sizeof(deep), "{\"roms\":[],\"x\":");
    for (int i = 0; i < 40; i++) deep[p++] = '[';
    for (int i = 0; i < 40; i++) deep[p++] = ']';
    deep[p++] = '}';
    deep[p] = '\0';
    CHECK(cat_parse_page(deep, (size_t)p, e, 4, &info) == -1);
}

static void test_parse_systems(void) {
    const char json[] = "{\"systems\": [\"GBA\", \"NDS\", \"PSP\"], \"stats\": {\"GBA\": 10, \"NDS\": 3655,"
                        " \"total\": 99, \"extra\": {\"a\": [1]}}}";
    const char *const wanted[] = { "NDS", "DSI", NULL };
    char out[2][8];
    int counts[2];
    int n = cat_parse_systems(json, strlen(json), wanted, out, counts, 2);
    CHECK(n == 1);
    CHECK_STR(out[0], "NDS");
    CHECK(counts[0] == 3655);

    const char both[] = "{\"systems\":[\"DSI\",\"NDS\"],\"stats\":{}}";
    n = cat_parse_systems(both, strlen(both), wanted, out, counts, 2);
    CHECK(n == 2);
    CHECK_STR(out[0], "NDS");  // wanted order, not server order
    CHECK_STR(out[1], "DSI");

    CHECK(cat_parse_systems("{\"systems\":[]}", 14, wanted, out, counts, 2) == 0);
    CHECK(cat_parse_systems("{\"systems\":[", 12, wanted, out, counts, 2) == -1);
}

static void test_strings(void) {
    char out[64];
    cat_url_encode("mario kart&x=1/é", out, sizeof(out));
    CHECK_STR(out, "mario%20kart%26x%3D1%2F%C3%A9");
    cat_url_encode("NDS_abc-1.2~", out, sizeof(out));
    CHECK_STR(out, "NDS_abc-1.2~");
    cat_url_encode("   ", out, 7);  // no half escapes
    CHECK_STR(out, "%20%20");

    char name[CAT_FILE_LEN];
    cat_target_name("'Chou' Kowai Hanashi DS (Japan).zip", name, sizeof(name));
    CHECK_STR(name, "'Chou' Kowai Hanashi DS (Japan).nds");
    cat_target_name("Game: The \"Best\" <1>?.zip", name, sizeof(name));
    CHECK_STR(name, "Game_ The _Best_ _1__.nds");
    cat_target_name("Loose.nds", name, sizeof(name));
    CHECK_STR(name, "Loose.nds");
    cat_target_name("Trailing. .zip", name, sizeof(name));
    CHECK_STR(name, "Trailing.nds");
    cat_target_name(".zip", name, sizeof(name));
    CHECK_STR(name, "game.nds");
    cat_target_name("NoExt", name, sizeof(name));
    CHECK_STR(name, "NoExt.nds");
    cat_target_name("a/b.zip", name, sizeof(name));
    CHECK_STR(name, "a_b.nds");
    char small[12];
    cat_target_name("Very Long Name.zip", small, sizeof(small));
    CHECK_STR(small, "Very Lo.nds");

    cat_format_size(1, out, sizeof(out));
    CHECK_STR(out, "1 KB");
    cat_format_size(76514534, out, sizeof(out));
    CHECK_STR(out, "72.9 MB");
    cat_format_size(512ULL * 1024 * 1024, out, sizeof(out));
    CHECK_STR(out, "512.0 MB");
    cat_format_size(2ULL * 1024 * 1024 * 1024 + 1024ULL * 1024 * 1024 / 4, out, sizeof(out));
    CHECK_STR(out, "2.25 GB");

    cat_ascii("Pok\xc3\xa9mon\tX", out, sizeof(out));
    CHECK_STR(out, "Pok?mon X");
    cat_ascii("abcdef", out, 4);
    CHECK_STR(out, "abc");
}

static void test_window(void) {
    // Centred on the rows, clamped to the list
    CHECK(cat_window_start(0, 21, 128, 3655, 0) == 0);
    CHECK(cat_window_start(500, 21, 128, 3655, 0) == 500 - 53);
    CHECK(cat_window_start(500, 21, 128, 3655, 1) == 500 - 13);
    CHECK(cat_window_start(500, 21, 128, 3655, -1) == 500 + 21 + 13 - 128);
    CHECK(cat_window_start(3640, 21, 128, 3655, 0) == 3655 - 128);
    CHECK(cat_window_start(3640, 21, 128, 3655, 1) == 3655 - 128);
    CHECK(cat_window_start(10, 21, 128, 3655, -1) == 0);
    CHECK(cat_window_start(5, 21, 128, 40, 0) == 0);  // list smaller than a window

    CHECK(cat_window_covers(0, 128, 0, 21, 3655));
    CHECK(cat_window_covers(0, 128, 107, 21, 3655));
    CHECK(!cat_window_covers(0, 128, 108, 21, 3655));
    CHECK(!cat_window_covers(100, 128, 99, 21, 3655));
    CHECK(cat_window_covers(0, 40, 30, 21, 40));  // rows past the end don't count
    CHECK(cat_window_covers(0, 0, 0, 21, 0));     // empty list
    CHECK(!cat_window_covers(0, 0, 0, 21, 5));

    // Walking the whole list one row at a time, down then back up: every
    // position is covered right after fetching the window picked for it
    int fetches = 0, off = 0, count = 0, total = 3655;
    for (int scroll = 0; scroll + 21 <= total; scroll++) {
        if (!cat_window_covers(off, count, scroll, 21, total)) {
            off = cat_window_start(scroll, 21, 128, total, 1);
            count = total - off < 128 ? total - off : 128;
            fetches++;
            CHECK(cat_window_covers(off, count, scroll, 21, total));
        }
    }
    int down = fetches;
    for (int scroll = total - 21; scroll >= 0; scroll--) {
        if (!cat_window_covers(off, count, scroll, 21, total)) {
            off = cat_window_start(scroll, 21, 128, total, -1);
            count = total - off < 128 ? total - off : 128;
            fetches++;
            CHECK(cat_window_covers(off, count, scroll, 21, total));
        }
    }
    printf("line-by-line through %d entries: %d fetches down, %d up\n", total, down, fetches - down);
    CHECK(down <= total / 90 && fetches - down <= total / 90);
}

static void touch(const char *path) {
    FILE *f = fopen(path, "w");
    if (f) fclose(f);
}

static void test_names(const char *scratch) {
    CatNameSet set = {0};
    cat_names_add(&set, "Zelda (USA).nds");
    cat_names_add(&set, "sd:/roms/nds/Mario Kart DS (USA).NDS");
    cat_names_sort(&set);
    CHECK(cat_names_contains(&set, "mario kart ds (usa).nds"));
    CHECK(cat_names_contains(&set, "ZELDA (USA).nds"));
    CHECK(!cat_names_contains(&set, "Zelda (Europe).nds"));
    cat_names_free(&set);
    CHECK(!cat_names_contains(&set, "Zelda (USA).nds"));

    char dir[512], path[600];
    snprintf(dir, sizeof(dir), "%s/roms", scratch);
    mkdir(dir, 0777);
    snprintf(path, sizeof(path), "%s/A Game (USA).nds", dir); touch(path);
    snprintf(path, sizeof(path), "%s/readme.txt", dir); touch(path);
    snprintf(path, sizeof(path), "%s/._A Game (USA).nds", dir); touch(path);
    snprintf(path, sizeof(path), "%s/sub", dir); mkdir(path, 0777);
    snprintf(path, sizeof(path), "%s/sub/B Game (Japan).dsi", dir); touch(path);
    snprintf(path, sizeof(path), "%s/saves", dir); mkdir(path, 0777);
    snprintf(path, sizeof(path), "%s/saves/C Game.nds", dir); touch(path);
    snprintf(path, sizeof(path), "%s/sub/d1", dir); mkdir(path, 0777);
    snprintf(path, sizeof(path), "%s/sub/d1/d2", dir); mkdir(path, 0777);
    snprintf(path, sizeof(path), "%s/sub/d1/d2/Deep.nds", dir); touch(path);

    int added = cat_names_scan(&set, dir, 1);
    CHECK(added == 2);
    CHECK(cat_names_contains(&set, "A Game (USA).nds"));
    CHECK(cat_names_contains(&set, "b game (japan).nds"));  // .dsi counts, extension ignored
    CHECK(!cat_names_contains(&set, "C Game.nds"));         // saves folder skipped
    CHECK(!cat_names_contains(&set, "Deep.nds"));           // deeper than max_depth
    cat_names_free(&set);
    CHECK(cat_names_scan(&set, dir, 4) == 3);
    cat_names_free(&set);
    CHECK(cat_names_scan(&set, "/nonexistent/dir", 4) == 0);
    cat_names_free(&set);
}

static void test_real_catalog(const char *path) {
    FILE *f = fopen(path, "rb");
    if (!f) {
        printf("(no %s, skipping full-catalog parse)\n", path);
        return;
    }
    fseek(f, 0, SEEK_END);
    long size = ftell(f);
    fseek(f, 0, SEEK_SET);
    char *json = malloc((size_t)size);
    CHECK(fread(json, 1, (size_t)size, f) == (size_t)size);
    fclose(f);

    int max = 5000;
    CatEntry *e = malloc(sizeof(CatEntry) * (size_t)max);
    CatPageInfo info;
    int n = cat_parse_page(json, (size_t)size, e, max, &info);
    int ra = 0, extract = 0, truncated = 0;
    for (int i = 0; i < n; i++) {
        if (cat_entry_has_ra(&e[i])) ra++;
        if (e[i].can_extract_nds) extract++;
        if (e[i].truncated) truncated++;
        CHECK(e[i].rom_id[0] && e[i].filename[0]);
    }
    printf("full catalog: %ld bytes, %d entries (total %d), %d with RA, %d extractable, %d truncated\n",
           size, n, info.total, ra, extract, truncated);
    CHECK(n == info.total);
    CHECK(truncated == 0);
    free(e);
    free(json);
}

int main(int argc, char **argv) {
    test_parse_page();
    test_parse_systems();
    test_strings();
    test_window();
    test_names(argc > 1 ? argv[1] : "/tmp");
    if (argc > 2) test_real_catalog(argv[2]);
    TEST_DONE();
}
