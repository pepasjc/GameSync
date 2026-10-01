// Host test for the 3DS client's catalog_data.c (adapted from ds/tests). Usage: test_catalog_data <scratch dir> [catalog.json]
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
    cat_target_name("'Chou' Kowai Hanashi DS (Japan).zip", ".nds", name, sizeof(name));
    CHECK_STR(name, "'Chou' Kowai Hanashi DS (Japan).nds");
    cat_target_name("Game: The \"Best\" <1>?.zip", ".nds", name, sizeof(name));
    CHECK_STR(name, "Game_ The _Best_ _1__.nds");
    cat_target_name("Loose.nds", ".nds", name, sizeof(name));
    CHECK_STR(name, "Loose.nds");
    cat_target_name("Trailing. .zip", ".nds", name, sizeof(name));
    CHECK_STR(name, "Trailing.nds");
    cat_target_name(".zip", ".nds", name, sizeof(name));
    CHECK_STR(name, "game.nds");
    cat_target_name("NoExt", ".nds", name, sizeof(name));
    CHECK_STR(name, "NoExt.nds");
    cat_target_name("a/b.zip", ".nds", name, sizeof(name));
    CHECK_STR(name, "a_b.nds");
    char small[12];
    cat_target_name("Very Long Name.zip", ".nds", small, sizeof(small));
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

static const char *const ds_exts[] = { ".nds", ".dsi", NULL };

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

    int added = cat_names_scan(&set, dir, 1, ds_exts);
    CHECK(added == 2);
    CHECK(cat_names_contains(&set, "A Game (USA).nds"));
    CHECK(cat_names_contains(&set, "b game (japan).nds"));  // .dsi counts, extension ignored
    CHECK(!cat_names_contains(&set, "C Game.nds"));         // saves folder skipped
    CHECK(!cat_names_contains(&set, "Deep.nds"));           // deeper than max_depth
    cat_names_free(&set);
    CHECK(cat_names_scan(&set, dir, 4, ds_exts) == 3);
    cat_names_free(&set);
    CHECK(cat_names_scan(&set, "/nonexistent/dir", 4, ds_exts) == 0);
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
        if (e[i].can_extract_nds || e[i].can_extract_cia) extract++;
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

// ---------------------------------------------------------------------------
// 3DS additions
// ---------------------------------------------------------------------------

static const char page_3ds_json[] =
    "{\"roms\": ["
    "  {\"rom_id\": \"3DS_zelda\", \"title_id\": \"0004000000033500\", \"system\": \"3DS\","
    "   \"name\": \"Zelda\", \"filename\": \"Zelda (USA).3ds\", \"size\": 5368709120,"
    "   \"is_bundle\": false, \"extract_format\": \"3ds\", \"extract_formats\": [\"cia\", \"decrypted_cci\"]},"
    "  {\"rom_id\": \"3DS_x\", \"title_id\": null, \"name\": \"X\", \"filename\": \"X.cia\","
    "   \"size\": 100, \"is_bundle\": true}"
    "], \"total\": 2, \"has_more\": false}";

static void test_parse_3ds(void) {
    CatEntry e[2];
    CatPageInfo info;
    int n = cat_parse_page(page_3ds_json, strlen(page_3ds_json), e, 2, &info);
    CHECK(n == 2);
    CHECK_STR(e[0].title_id, "0004000000033500");
    CHECK(e[0].size == 5368709120ULL);  // > 4 GB survives
    CHECK(e[0].can_extract_cia);
    CHECK(!e[0].can_extract_nds);
    CHECK(!e[0].is_bundle);
    CHECK_STR(e[1].title_id, "");
    CHECK(e[1].is_bundle);
    CHECK(!e[1].can_extract_cia);
}

static void test_target_names_3ds(void) {
    char name[CAT_FILE_LEN];
    cat_target_name("Zelda (USA).3ds.zip", ".3ds", name, sizeof(name));
    CHECK_STR(name, "Zelda (USA).3ds");
    cat_target_name("Zelda (USA).CCI.zip", ".cci", name, sizeof(name));
    CHECK_STR(name, "Zelda (USA).cci");
    cat_target_name("Mario (USA).nds.zip", ".nds", name, sizeof(name));
    CHECK_STR(name, "Mario (USA).nds");
    cat_target_name("Plain (USA).zip", ".nds", name, sizeof(name));
    CHECK_STR(name, "Plain (USA).nds");
    cat_target_name(".3ds.zip", ".3ds", name, sizeof(name));
    CHECK_STR(name, "game.3ds");
}

static CatEntry mk(const char *filename, bool nds, bool cia, bool bundle) {
    CatEntry e;
    memset(&e, 0, sizeof(e));
    snprintf(e.rom_id, sizeof(e.rom_id), "id");
    snprintf(e.filename, sizeof(e.filename), "%s", filename);
    e.can_extract_nds = nds;
    e.can_extract_cia = cia;
    e.is_bundle = bundle;
    return e;
}

static void test_plan(void) {
    CatEntry e;
    CatPlan p;

    e = mk("Game.cia", false, false, false);
    p = cat_plan_install("3DS", &e);
    CHECK(p.kind == CAT_PLAN_CIA);
    CHECK_STR(p.query, "");
    CHECK(!p.file_fallback);

    e = mk("Game.3ds", false, true, false);
    p = cat_plan_install("3DS", &e);
    CHECK(p.kind == CAT_PLAN_CIA_CONVERT);
    CHECK_STR(p.query, "?extract=cia");
    CHECK(p.file_fallback);

    e = mk("Game.CCI", false, false, false);  // old server: no conversion
    p = cat_plan_install("3ds", &e);
    CHECK(p.kind == CAT_PLAN_3DS_FILE);
    CHECK(p.file_fallback);

    e = mk("Game.3ds.zip", false, true, false);
    p = cat_plan_install("3DS", &e);
    CHECK(p.kind == CAT_PLAN_CIA_CONVERT);
    CHECK(!p.file_fallback);  // the zip itself is no use on the SD

    e = mk("Game.3ds.zip", false, false, false);
    CHECK(cat_plan_install("3DS", &e).kind == CAT_PLAN_NONE);
    e = mk("Game.app", false, false, false);
    CHECK(cat_plan_install("3DS", &e).kind == CAT_PLAN_NONE);
    e = mk("Folder", false, false, true);
    CHECK(cat_plan_install("3DS", &e).kind == CAT_PLAN_NONE);

    e = mk("Game.nds", false, false, false);
    CHECK(cat_plan_install("NDS", &e).kind == CAT_PLAN_NDS);
    e = mk("Game.dsi", false, false, false);
    CHECK(cat_plan_install("DSI", &e).kind == CAT_PLAN_NDS);
    e = mk("Game.zip", true, false, false);
    p = cat_plan_install("NDS", &e);
    CHECK(p.kind == CAT_PLAN_NDS_EXTRACT);
    CHECK_STR(p.query, "?extract=nds");
    e = mk("Game.zip", false, false, false);
    CHECK(cat_plan_install("NDS", &e).kind == CAT_PLAN_NONE);
    e = mk("Game.nds", false, false, false);
    e.truncated = true;
    CHECK(cat_plan_install("NDS", &e).kind == CAT_PLAN_NONE);
    e = mk("Game.gba", false, false, false);
    CHECK(cat_plan_install("GBA", &e).kind == CAT_PLAN_NONE);
}

static void put_le32(uint8_t *p, uint32_t v) {
    p[0] = (uint8_t)v; p[1] = (uint8_t)(v >> 8); p[2] = (uint8_t)(v >> 16); p[3] = (uint8_t)(v >> 24);
}

static void put_be32(uint8_t *p, uint32_t v) {
    p[0] = (uint8_t)(v >> 24); p[1] = (uint8_t)(v >> 16); p[2] = (uint8_t)(v >> 8); p[3] = (uint8_t)v;
}

static void test_cia_header(void) {
    // Typical retail-style CIA: header 0x2020, certs 0xA00, ticket 0x350,
    // TMD signed RSA-2048/SHA-256
    static uint8_t cia[0x4000];
    memset(cia, 0, sizeof(cia));
    put_le32(cia + 0x00, 0x2020);
    put_le32(cia + 0x08, 0xA00);
    put_le32(cia + 0x0C, 0x350);
    put_le32(cia + 0x10, 0xB34);
    size_t tmd = 0x2040 + 0xA00 + 0x380;  // each section 64-byte aligned
    put_be32(cia + tmd, 0x010004);
    const uint8_t tid[8] = { 0x00, 0x04, 0x00, 0x00, 0x00, 0x03, 0x35, 0x00 };
    memcpy(cia + tmd + 4 + 0x13C + 0x4C, tid, 8);
    size_t need = tmd + 4 + 0x13C + 0x4C + 8;

    uint64_t out = 0;
    CHECK(cat_cia_title_id(cia, sizeof(cia), &out) == 1);
    CHECK(out == 0x0004000000033500ULL);
    CHECK(cat_cia_title_id(cia, need, &out) == 1);
    CHECK(cat_cia_title_id(cia, need - 1, &out) == 0);  // need more bytes
    CHECK(cat_cia_title_id(cia, 0x10, &out) == 0);
    CHECK(need <= CAT_CIA_PROBE_MAX);

    // RSA-4096 TMD signature moves the id
    put_be32(cia + tmd, 0x010003);
    memset(cia + tmd + 4 + 0x13C + 0x4C, 0, 8);
    memcpy(cia + tmd + 4 + 0x23C + 0x4C, tid, 8);
    out = 0;
    CHECK(cat_cia_title_id(cia, sizeof(cia), &out) == 1);
    CHECK(out == 0x0004000000033500ULL);

    put_be32(cia + tmd, 0x12345678);  // unknown signature type
    CHECK(cat_cia_title_id(cia, sizeof(cia), &out) == -1);

    // Not a CIA at all: a zip, an NCSD image
    const uint8_t zip[0x40] = { 'P', 'K', 3, 4 };
    CHECK(cat_cia_title_id(zip, sizeof(zip), &out) == -1);
    static uint8_t ncsd[0x200];
    memcpy(ncsd + 0x100, "NCSD", 4);
    CHECK(cat_cia_title_id(ncsd, sizeof(ncsd), &out) == -1);
}

static void test_cia_map(const char *scratch) {
    uint64_t tid;
    CHECK(cat_parse_tid("0004000000033500", &tid) && tid == 0x0004000000033500ULL);
    CHECK(cat_parse_tid("0004000000033500 rest", &tid));
    CHECK(!cat_parse_tid("000400000003350", &tid));
    CHECK(!cat_parse_tid("000400000003350G", &tid));
    CHECK(!cat_parse_tid("00040000000335000", &tid));
    CHECK(!cat_parse_tid("3DS_zelda", &tid));

    CatCiaMap map = {0};
    CHECK(cat_cia_map_get(&map, "a") == 0);
    for (int i = 0; i < 40; i++) {
        char id[32];
        snprintf(id, sizeof(id), "3DS_game_%d", i);
        cat_cia_map_set(&map, id, 0x0004000000000000ULL + (uint64_t)i);
    }
    cat_cia_map_set(&map, "3DS_game_3", 0x00040000000ABC00ULL);  // replace
    cat_cia_map_set(&map, "", 1);                                 // ignored
    CHECK(map.count == 40);

    char path[600];
    snprintf(path, sizeof(path), "%s/catalog_cia.txt", scratch);
    CHECK(cat_cia_map_save(&map, path));
    cat_cia_map_free(&map);
    CHECK(map.count == 0);

    // A junk line in the file is skipped
    FILE *f = fopen(path, "a");
    if (f) {
        fputs("garbage line\r\nFFFF short\n", f);
        fclose(f);
    }
    cat_cia_map_load(&map, path);
    CHECK(map.count == 40);
    CHECK(cat_cia_map_get(&map, "3DS_game_3") == 0x00040000000ABC00ULL);
    CHECK(cat_cia_map_get(&map, "3DS_game_39") == 0x0004000000000027ULL);
    CHECK(cat_cia_map_get(&map, "missing") == 0);
    cat_cia_map_free(&map);

    snprintf(path, sizeof(path), "%s/does-not-exist.txt", scratch);
    cat_cia_map_load(&map, path);
    CHECK(map.count == 0);
    cat_cia_map_free(&map);
}

int main(int argc, char **argv) {
    const char *scratch = argc > 1 ? argv[1] : "/tmp";
    test_parse_page();
    test_parse_systems();
    test_strings();
    test_window();
    test_names(scratch);
    test_parse_3ds();
    test_target_names_3ds();
    test_plan();
    test_cia_header();
    test_cia_map(scratch);
    if (argc > 2) test_real_catalog(argv[2]);
    TEST_DONE();
}
