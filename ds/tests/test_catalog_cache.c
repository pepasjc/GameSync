// Host test for catalog_cache.c and the cache-related parsers in
// catalog_data.c. Usage: test_catalog_cache <scratch dir> [catalog.json]
// The optional JSON is a full /api/v1/roms?system=NDS response: it is
// written to a cache file page by page and read back.
#include "catalog_cache.h"
#include "test_util.h"
#include <stdlib.h>
#include <sys/stat.h>

static char scratch_dir[256];

static void path_in(char *out, size_t size, const char *name) {
    snprintf(out, size, "%s/%s", scratch_dir, name);
}

static void make_entry(CatEntry *e, int i) {
    memset(e, 0, sizeof(*e));
    snprintf(e->rom_id, sizeof(e->rom_id), "NDS_game_%04d", i);
    snprintf(e->name, sizeof(e->name), "Game %04d %s (USA)", i, i % 3 == 0 ? "Zelda" : "Mario");
    snprintf(e->filename, sizeof(e->filename), "Game %04d (USA).zip", i);
    e->size = 1000u * (uint32_t)i + 7;
    e->ra_game_id = i % 5 == 0 ? 100 + i : 0;
    e->ra_achievements = i % 5 == 0 ? i % 50 + 1 : (i % 7 == 0 ? -1 : 0);
    e->ra_title_only = i % 10 == 0;
    e->can_extract_nds = i % 2 == 0;
}

static bool same_entry(const CatEntry *a, const CatEntry *b) {
    return strcmp(a->rom_id, b->rom_id) == 0 && strcmp(a->name, b->name) == 0 &&
           strcmp(a->filename, b->filename) == 0 && a->size == b->size && a->ra_game_id == b->ra_game_id &&
           a->ra_achievements == b->ra_achievements && a->ra_title_only == b->ra_title_only &&
           a->can_extract_nds == b->can_extract_nds && a->truncated == b->truncated;
}

static void test_round_trip(void) {
    char part[300], final_path[300];
    path_in(part, sizeof(part), "NDS.cat.part");
    path_in(final_path, sizeof(final_path), "NDS.cat");
    remove(final_path);

    const int N = 1000;
    CatCacheWriter w;
    CHECK(ccw_begin(&w, part, "NDS"));
    CatEntry e, back;
    for (int i = 0; i < N; i++) {
        make_entry(&e, i);
        CHECK(ccw_add(&w, &e));
    }
    // A long, unicode, truncated entry
    memset(&e, 0, sizeof(e));
    memset(e.rom_id, 'r', sizeof(e.rom_id) - 1);
    snprintf(e.name, sizeof(e.name), "Pok\xc3\xa9mon Platinum");
    memset(e.filename, 'f', sizeof(e.filename) - 1);
    e.truncated = true;
    e.ra_achievements = 99999;  // clamped to the s16 range
    CHECK(ccw_add(&w, &e));
    CHECK(ccw_finish(&w, "075c55707fbb199cafbffc1e39d9cabf84051c7d", final_path));

    struct stat st;
    CHECK(stat(part, &st) != 0);  // renamed away

    char sys[8], fp[CAT_FP_LEN];
    uint32_t count = 0;
    CHECK(cc_peek(final_path, sys, fp, &count));
    CHECK_STR(sys, "NDS");
    CHECK_STR(fp, "075c55707fbb199cafbffc1e39d9cabf84051c7d");
    CHECK(count == (uint32_t)N + 1);

    CatCache c;
    CHECK(cc_open(&c, final_path));
    CHECK(c.count == (uint32_t)N + 1);
    for (int i = 0; i < N; i++) {
        make_entry(&e, i);
        CHECK(cc_read(&c, c.refs[i], &back));
        if (!same_entry(&e, &back)) {
            CHECK(!"record mismatch");
            break;
        }
        CHECK(((c.refs[i] & CC_RA_BIT) != 0) == cat_entry_has_ra(&e));
    }
    // Random access, backwards
    make_entry(&e, 17);
    CHECK(cc_read(&c, c.refs[17], &back) && same_entry(&e, &back));
    CHECK(cc_read(&c, c.refs[N], &back));
    CHECK(back.truncated);
    CHECK(strlen(back.rom_id) == sizeof(back.rom_id) - 1);
    CHECK(strlen(back.filename) == sizeof(back.filename) - 1);
    CHECK_STR(back.name, "Pok\xc3\xa9mon Platinum");
    CHECK(back.ra_achievements == 32767);

    // Filters
    CatEntry scratch;
    uint32_t *list = NULL;
    int n = cc_filter(&c, false, NULL, &list, &scratch);
    CHECK(n == N + 1);
    free(list);

    int want_ra = 0, want_zelda = 0, want_both = 0;
    for (int i = 0; i < N; i++) {
        make_entry(&e, i);
        bool ra = cat_entry_has_ra(&e), z = (i % 3 == 0);
        want_ra += ra;
        want_zelda += z;
        want_both += ra && z;
    }
    want_ra++;  // the long entry: 99999 achievements
    n = cc_filter(&c, true, NULL, &list, &scratch);
    CHECK(n == want_ra);
    free(list);
    n = cc_filter(&c, false, "zELDa", &list, &scratch);
    CHECK(n == want_zelda);
    if (n > 0) {
        CHECK(cc_read(&c, list[0], &back));
        CHECK_STR(back.rom_id, "NDS_game_0000");
        CHECK(cc_read(&c, list[n - 1], &back));
        CHECK(strstr(back.name, "Zelda") != NULL);
    }
    free(list);
    n = cc_filter(&c, true, "zelda", &list, &scratch);
    CHECK(n == want_both);
    free(list);
    n = cc_filter(&c, false, "(usa).zip", &list, &scratch);  // file name match
    CHECK(n == N);
    free(list);
    n = cc_filter(&c, false, "no such game", &list, &scratch);
    CHECK(n == 0 && list == NULL);
    cc_close(&c);
    CHECK(!cc_is_open(&c));

    // Damaged files are refused
    FILE *f = fopen(final_path, "r+b");
    CHECK(f != NULL);
    if (f) {
        fseek(f, 4, SEEK_SET);
        fputc(CC_VERSION + 1, f);  // newer/older format
        fclose(f);
    }
    CHECK(!cc_open(&c, final_path));
    CHECK(!cc_peek(final_path, NULL, NULL, NULL));
    CHECK(!cc_open(&c, "/nonexistent/NDS.cat"));
}

static void test_truncated_file(void) {
    char part[300], final_path[300];
    path_in(part, sizeof(part), "DSI.cat.part");
    path_in(final_path, sizeof(final_path), "DSI.cat");
    CatCacheWriter w;
    CHECK(ccw_begin(&w, part, "DSI"));
    CatEntry e;
    for (int i = 0; i < 10; i++) {
        make_entry(&e, i);
        ccw_add(&w, &e);
    }
    CHECK(ccw_finish(&w, "abc", final_path));
    CatCache c;
    CHECK(cc_open(&c, final_path));
    cc_close(&c);

    // Cut the last byte off: size no longer matches the header
    FILE *f = fopen(final_path, "rb");
    char buf[8192];
    size_t n = f ? fread(buf, 1, sizeof(buf), f) : 0;
    if (f) fclose(f);
    f = fopen(final_path, "wb");
    if (f) {
        fwrite(buf, 1, n - 1, f);
        fclose(f);
    }
    CHECK(!cc_open(&c, final_path));

    // An aborted write leaves nothing behind and the old file alone
    CHECK(ccw_begin(&w, part, "DSI"));
    make_entry(&e, 1);
    ccw_add(&w, &e);
    ccw_abort(&w);
    struct stat st;
    CHECK(stat(part, &st) != 0);
    CHECK(stat(final_path, &st) == 0);

    // An empty system is a valid cache
    CHECK(ccw_begin(&w, part, "DSI"));
    CHECK(ccw_finish(&w, "empty", final_path));
    CHECK(cc_open(&c, final_path));
    CHECK(c.count == 0);
    uint32_t *list;
    CatEntry scratch;
    CHECK(cc_filter(&c, true, "x", &list, &scratch) == 0);
    cc_close(&c);
}

static void test_fingerprints(void) {
    static const char *const wanted[] = { "NDS", "DSI", NULL };
    char sys[2][8], fps[2][CAT_FP_LEN];
    int counts[2];
    const char *json =
        "{\"systems\":{\"3DO\":{\"fingerprint\":\"aaa\",\"count\":259},"
        "\"DSI\":{\"count\":4,\"fingerprint\":\"d51\",\"extra\":[1,{\"x\":2}]},"
        "\"NDS\":{\"fingerprint\":\"0f3319ff2ac99a766151b4f3821a315d482a437d\",\"count\":3702}}}";
    int n = cat_parse_fingerprints(json, strlen(json), wanted, sys, fps, counts, 2);
    CHECK(n == 2);
    CHECK_STR(sys[0], "NDS");
    CHECK_STR(fps[0], "0f3319ff2ac99a766151b4f3821a315d482a437d");
    CHECK(counts[0] == 3702);
    CHECK_STR(sys[1], "DSI");
    CHECK_STR(fps[1], "d51");
    CHECK(counts[1] == 4);

    const char *only_nds = "{\"systems\":{\"nds\":{\"fingerprint\":null,\"count\":1}}}";
    n = cat_parse_fingerprints(only_nds, strlen(only_nds), wanted, sys, fps, counts, 2);
    CHECK(n == 1);
    CHECK_STR(sys[0], "NDS");
    CHECK_STR(fps[0], "");

    const char *empty = "{\"systems\":{}}";
    CHECK(cat_parse_fingerprints(empty, strlen(empty), wanted, sys, fps, counts, 2) == 0);
    CHECK(cat_parse_fingerprints("{\"detail\":\"Not Found\"}", 22, wanted, sys, fps, counts, 2) == -1);
    CHECK(cat_parse_fingerprints("{\"systems\":{\"NDS\":", 18, wanted, sys, fps, counts, 2) == -1);
    CHECK(cat_parse_fingerprints("[]", 2, wanted, sys, fps, counts, 2) == -1);
}

typedef struct {
    int seen;
    int stop_after;
    char first[CAT_ID_LEN];
} CbState;

static bool count_cb(const CatEntry *e, void *user) {
    CbState *s = user;
    if (s->seen == 0) snprintf(s->first, sizeof(s->first), "%s", e->rom_id);
    s->seen++;
    return s->stop_after == 0 || s->seen < s->stop_after;
}

static void test_parse_cb(void) {
    const char *json =
        "{\"roms\":[{\"rom_id\":\"A\",\"name\":\"Alpha\",\"filename\":\"Alpha.zip\",\"size\":5},"
        "{\"rom_id\":\"B\",\"name\":\"Beta\"},{\"rom_id\":\"C\"}],\"total\":30,\"has_more\":true}";
    CatEntry scratch;
    CatPageInfo info;
    CbState st = { 0 };
    int n = cat_parse_page_cb(json, strlen(json), &scratch, count_cb, &st, &info);
    CHECK(n == 3 && st.seen == 3);
    CHECK_STR(st.first, "A");
    CHECK(info.total == 30 && info.has_more);

    CbState stop = { 0, 2, "" };
    n = cat_parse_page_cb(json, strlen(json), &scratch, count_cb, &stop, &info);
    CHECK(n == 2 && stop.seen == 2);
    CHECK(info.total == 30);  // the rest is still read for total/has_more

    CHECK(cat_parse_page_cb("{\"roms\":[{]}", 12, &scratch, count_cb, &st, &info) == -1);
}

static void test_matches(void) {
    CatEntry e;
    memset(&e, 0, sizeof(e));
    snprintf(e.name, sizeof(e.name), "The Legend of Zelda - Phantom Hourglass (USA)");
    snprintf(e.filename, sizeof(e.filename), "Zelda PH (USA) [b].zip");
    CHECK(cat_entry_matches(&e, NULL));
    CHECK(cat_entry_matches(&e, ""));
    CHECK(cat_entry_matches(&e, "phantom"));
    CHECK(cat_entry_matches(&e, "LEGEND OF"));
    CHECK(cat_entry_matches(&e, "[b]"));
    CHECK(cat_entry_matches(&e, "(usa)"));
    CHECK(!cat_entry_matches(&e, "mario"));
    CHECK(!cat_entry_matches(&e, "hourglass (usa)x"));
}

// A real catalog: page it into a cache file the way the client does
typedef struct {
    CatCacheWriter *w;
    int added;
} FillState;

static bool fill_cb(const CatEntry *e, void *user) {
    FillState *s = user;
    if (ccw_add(s->w, e)) s->added++;
    return true;
}

static void test_real_catalog(const char *path) {
    FILE *f = fopen(path, "rb");
    if (!f) {
        perror(path);
        CHECK(0);
        return;
    }
    fseek(f, 0, SEEK_END);
    long size = ftell(f);
    fseek(f, 0, SEEK_SET);
    char *json = malloc((size_t)size);
    CHECK(json && fread(json, 1, (size_t)size, f) == (size_t)size);
    fclose(f);

    char part[300], final_path[300];
    path_in(part, sizeof(part), "REAL.cat.part");
    path_in(final_path, sizeof(final_path), "REAL.cat");
    CatCacheWriter w;
    CHECK(ccw_begin(&w, part, "NDS"));
    FillState st = { &w, 0 };
    CatEntry scratch;
    CatPageInfo info;
    int n = cat_parse_page_cb(json, (size_t)size, &scratch, fill_cb, &st, &info);
    CHECK(n > 0 && st.added == n);
    CHECK(ccw_finish(&w, "real", final_path));

    struct stat sb;
    stat(final_path, &sb);
    CatCache c;
    CHECK(cc_open(&c, final_path));
    CHECK((int)c.count == n);
    uint32_t *list;
    int ra = cc_filter(&c, true, NULL, &list, &scratch);
    free(list);
    int zelda = cc_filter(&c, false, "zelda", &list, &scratch);
    free(list);
    printf("real catalog: %d entries, %ld KB JSON -> %ld KB cache, %d with RA, %d match \"zelda\"\n", n,
           size / 1024, (long)sb.st_size / 1024, ra, zelda);
    // Spot-check against the JSON parsed into an array
    CatEntry *all = malloc((size_t)n * sizeof(CatEntry));
    if (all && cat_parse_page(json, (size_t)size, all, n, &info) == n) {
        for (int i = 0; i < n; i += 97) {
            CatEntry back;
            CHECK(cc_read(&c, c.refs[i], &back) && same_entry(&all[i], &back));
        }
    }
    free(all);
    cc_close(&c);
    free(json);
}

int main(int argc, char **argv) {
    snprintf(scratch_dir, sizeof(scratch_dir), "%s", argc > 1 ? argv[1] : ".");
    test_round_trip();
    test_truncated_file();
    test_fingerprints();
    test_parse_cb();
    test_matches();
    if (argc > 2) test_real_catalog(argv[2]);
    TEST_DONE();
}
