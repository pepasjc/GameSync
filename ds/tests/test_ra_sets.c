// Host test for ra_sets.c (POST /api/v1/ra/sets request/response).
// Build: see run_host_tests.sh
#include "ra_sets.h"
#include "test_util.h"

typedef struct {
    int sets, unknown, errors;
    char md5[4][33];
    char data[4][128];
    size_t len[4];
    int kinds[4];
    int n;
} Seen;

static void collect(RaBatchKind kind, const char *md5, const char *data, size_t len, void *user) {
    Seen *s = user;
    if (kind == RA_BATCH_SET) s->sets++;
    if (kind == RA_BATCH_UNKNOWN) s->unknown++;
    if (kind == RA_BATCH_ERROR) s->errors++;
    if (s->n < 4) {
        strcpy(s->md5[s->n], md5);
        if (data) memcpy(s->data[s->n], data, len);
        s->data[s->n][len] = '\0';
        s->len[s->n] = len;
        s->kinds[s->n] = kind;
        s->n++;
    }
}

#define A "9dbd0337235cd8acf032c0fbfd649d70"
#define B "0123456789abcdef0123456789abcdef"
#define C "ffffffffffffffffffffffffffffffff"

int main(void) {
    // Request body
    char md5s[3][33] = { A, B, C };
    char json[256];
    size_t len = ra_sets_request(json, sizeof(json), (const char (*)[33])md5s, 3);
    CHECK(len == strlen(json));
    CHECK_STR(json, "{\"md5s\":[\"" A "\",\"" B "\",\"" C "\"]}");
    CHECK(ra_sets_request(json, 40, (const char (*)[33])md5s, 3) == 0);  // too small
    len = ra_sets_request(json, sizeof(json), (const char (*)[33])md5s, 0);
    CHECK_STR(json, "{\"md5s\":[]}");

    // A set with an embedded "=== " line inside its text must not confuse
    // the parser: lengths are what counts.
    const char set_a[] = "RASET\t1\ngame\t1\t" A "\tX\n=== fake 3\nach\t1\t5\t0xH1=1\tT\tD\n";
    char body[1024];
    int n = snprintf(body, sizeof(body),
                     "RASETS\t1\n=== " A " %zu\n%s--- " B " unknown\n--- " C " error RA down\nEND\n",
                     strlen(set_a), set_a);
    Seen s = {0};
    CHECK(ra_sets_parse(body, (size_t)n, collect, &s));
    CHECK(s.sets == 1 && s.unknown == 1 && s.errors == 1);
    CHECK_STR(s.md5[0], A);
    CHECK(s.len[0] == strlen(set_a));
    CHECK_STR(s.data[0], set_a);
    CHECK_STR(s.md5[1], B);
    CHECK_STR(s.data[2], "RA down");

    // Cut off: items before the cut are reported, result is false
    Seen cut = {0};
    CHECK(!ra_sets_parse(body, (size_t)n - 4, collect, &cut));
    CHECK(cut.sets == 1 && cut.unknown == 1 && cut.errors == 1);
    Seen cut2 = {0};
    CHECK(!ra_sets_parse(body, 9 + 40, collect, &cut2));  // inside the set
    CHECK(cut2.sets == 0);

    // Length past the end of the body
    const char bad_len[] = "RASETS\t1\n=== " A " 999\nRASET\nEND\n";
    Seen bl = {0};
    CHECK(!ra_sets_parse(bad_len, strlen(bad_len), collect, &bl));
    CHECK(bl.sets == 0);

    // Wrong magic, garbage line, upper-case md5
    Seen g = {0};
    CHECK(!ra_sets_parse("RASET\t1\nEND\n", 12, collect, &g));
    CHECK(!ra_sets_parse("RASETS\t1\nhello\nEND\n", 19, collect, &g));
    const char upper[] = "RASETS\t1\n--- 9DBD0337235CD8ACF032C0FBFD649D70 unknown\nEND\n";
    CHECK(!ra_sets_parse(upper, strlen(upper), collect, &g));
    CHECK(g.n == 0);

    // Empty batch
    Seen e = {0};
    CHECK(ra_sets_parse("RASETS\t1\nEND\n", 13, collect, &e));
    CHECK(e.n == 0);

    // Error without a reason
    const char no_reason[] = "RASETS\t1\n--- " A " error\nEND\n";
    Seen nr = {0};
    CHECK(ra_sets_parse(no_reason, strlen(no_reason), collect, &nr));
    CHECK(nr.errors == 1 && nr.len[0] == 0);

    TEST_DONE();
}
