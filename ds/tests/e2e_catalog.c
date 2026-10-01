// End-to-end check of the DS catalog code paths against a running server:
// the real http.c (BSD sockets on the host) and catalog_data.c.
// Usage: e2e_catalog <server url> <api key> <expected rom file> <out dir>
// Also fills the SD catalog cache the way catalog.c does (fingerprints,
// paged list streamed into catalog_cache.c) and asks for a rescan.
// Expects the fixture made by run_e2e.sh: NDS games "Alpha Quest (USA)"
// (zip holding the expected ROM) and "Beta Twin (USA)" (zip with two ROMs).
#include "http.h"
#include "catalog_data.h"
#include "catalog_cache.h"
#include "test_util.h"
#include <stdlib.h>
#include <stdint.h>
#include <time.h>

typedef struct {
    FILE *f;
    int calls;
    int cancel_after;
} Sink;

static int sink(const uint8_t *data, size_t size, uint32_t done, uint32_t total, void *user) {
    Sink *s = user;
    s->calls++;
    if (s->f && fwrite(data, 1, size, s->f) != size) return -1;
    if (s->cancel_after && s->calls >= s->cancel_after) return 1;
    return 0;
}

static bool same_file(const char *a, const char *b) {
    FILE *fa = fopen(a, "rb"), *fb = fopen(b, "rb");
    bool same = fa && fb;
    while (same) {
        int ca = fgetc(fa), cb = fgetc(fb);
        if (ca != cb) same = false;
        if (ca == EOF || cb == EOF) break;
    }
    if (fa) fclose(fa);
    if (fb) fclose(fb);
    return same;
}

static const CatEntry *find(const CatEntry *e, int n, const char *prefix) {
    for (int i = 0; i < n; i++) {
        if (strncmp(e[i].name, prefix, strlen(prefix)) == 0) return &e[i];
    }
    return NULL;
}

static bool fill_add(const CatEntry *e, void *user) {
    return ccw_add((CatCacheWriter *)user, e);
}

// catalog.c's fill_system without the UI: page `system` into a cache file
static int fill_cache(const char *base, const char *key, const char *system, const char *fp, const char *path,
                      int page) {
    char part[640], url[1024];
    snprintf(part, sizeof(part), "%s.part", path);
    CatCacheWriter w;
    if (!ccw_begin(&w, part, system)) return -1;
    CatEntry scratch;
    int offset = 0, requests = 0;
    while (1) {
        snprintf(url, sizeof(url), "%s/api/v1/roms?system=%s&limit=%d&offset=%d", base, system, page, offset);
        HttpResponse resp = http_request(url, HTTP_GET, key, NULL, 0);
        requests++;
        CatPageInfo info;
        int n = resp.status_code == 200 && resp.body
                    ? cat_parse_page_cb((const char *)resp.body, resp.body_size, &scratch, fill_add, &w, &info)
                    : -1;
        http_response_free(&resp);
        if (n < 0) {
            ccw_abort(&w);
            return -1;
        }
        offset += n;
        if (!info.has_more || n == 0) break;
    }
    return ccw_finish(&w, fp, path) ? requests : -1;
}

static void test_cache(const char *base, const char *key, const char *dir) {
    static const char *const wanted[] = { "NDS", "DSI", NULL };
    char url[1024], sys[2][8], fps[2][CAT_FP_LEN];
    int counts[2];
    snprintf(url, sizeof(url), "%s/api/v1/roms/fingerprints", base);
    HttpResponse resp = http_request(url, HTTP_GET, key, NULL, 0);
    CHECK(resp.status_code == 200);
    int n = resp.body ? cat_parse_fingerprints((const char *)resp.body, resp.body_size, wanted, sys, fps, counts, 2)
                      : -1;
    http_response_free(&resp);
    CHECK(n == 1);  // the fixture has NDS only
    if (n != 1) return;
    CHECK_STR(sys[0], "NDS");
    CHECK(fps[0][0] != '\0');
    CHECK(counts[0] == 3);
    printf("fingerprint NDS: %s (%d)\n", fps[0], counts[0]);

    // Page size 2: three games take two requests, like a big list in 500s
    char path[600];
    snprintf(path, sizeof(path), "%s/NDS.cat", dir);
    CHECK(fill_cache(base, key, "NDS", fps[0], path, 2) == 2);
    char have_fp[CAT_FP_LEN];
    uint32_t count = 0;
    CHECK(cc_peek(path, NULL, have_fp, &count));
    CHECK_STR(have_fp, fps[0]);  // unchanged next time: no refetch
    CHECK(count == 3);

    CatCache c;
    CHECK(cc_open(&c, path));
    CatEntry e, scratch;
    bool alpha = false;
    for (uint32_t i = 0; i < c.count; i++) {
        CHECK(cc_read(&c, c.refs[i], &e));
        if (strncmp(e.name, "Alpha", 5) == 0) alpha = e.can_extract_nds && e.size > 0 && e.rom_id[0];
    }
    CHECK(alpha);
    uint32_t *list;
    CHECK(cc_filter(&c, false, "alpha quest", &list, &scratch) == 1);  // same answer as ?search=
    free(list);
    CHECK(cc_filter(&c, true, NULL, &list, &scratch) == 0);  // same answer as ?has_ra=true
    free(list);
    cc_close(&c);

    // Settings > Refresh Catalog: rescan (open server: allowed), then the
    // fingerprint is unchanged as nothing moved on disk
    snprintf(url, sizeof(url), "%s/api/v1/roms/scan", base);
    resp = http_request(url, HTTP_GET, key, NULL, 0);
    CHECK(resp.status_code == 200);
    CHECK(resp.body && strstr((const char *)resp.body, "\"count\"") != NULL);
    http_response_free(&resp);
    snprintf(url, sizeof(url), "%s/api/v1/roms/fingerprints", base);
    resp = http_request(url, HTTP_GET, key, NULL, 0);
    char fps2[2][CAT_FP_LEN];
    n = resp.body ? cat_parse_fingerprints((const char *)resp.body, resp.body_size, wanted, sys, fps2, counts, 2) : -1;
    http_response_free(&resp);
    CHECK(n == 1 && strcmp(fps2[0], fps[0]) == 0);
}

int main(int argc, char **argv) {
    if (argc < 5) {
        fprintf(stderr, "usage: %s <url> <key> <expected rom> <out dir>\n", argv[0]);
        return 2;
    }
    const char *base = argv[1], *key = argv[2], *expected = argv[3], *dir = argv[4];
    http_set_verbose(0);
    char url[1024];

    // Paged list, as the catalog screen asks for it
    snprintf(url, sizeof(url), "%s/api/v1/roms?system=NDS&limit=128&offset=0", base);
    HttpResponse resp = http_request(url, HTTP_GET, key, NULL, 0);
    CHECK(resp.status_code == 200);
    CatEntry e[8];
    CatPageInfo info;
    int n = resp.body ? cat_parse_page((const char *)resp.body, resp.body_size, e, 8, &info) : -1;
    http_response_free(&resp);
    CHECK(n == 3 && info.total == 3);
    CHECK(info.has_ra == -1);
    const CatEntry *alpha = find(e, n, "Alpha");
    const CatEntry *beta = find(e, n, "Beta");
    CHECK(alpha && alpha->can_extract_nds);
    CHECK(beta && beta->can_extract_nds);
    if (!alpha || !beta) TEST_DONE();

    // RA-only filter (the fixture has no RA index): applied, echoed, empty
    snprintf(url, sizeof(url), "%s/api/v1/roms?system=NDS&limit=128&offset=0&has_ra=true", base);
    resp = http_request(url, HTTP_GET, key, NULL, 0);
    n = resp.body ? cat_parse_page((const char *)resp.body, resp.body_size, e + 4, 4, &info) : -1;
    http_response_free(&resp);
    CHECK(n == 0 && info.has_ra == 1 && info.total == 0);

    // Search, URL-encoded
    char enc[64];
    cat_url_encode("alpha quest", enc, sizeof(enc));
    snprintf(url, sizeof(url), "%s/api/v1/roms?system=NDS&limit=128&offset=0&search=%s", base, enc);
    resp = http_request(url, HTTP_GET, key, NULL, 0);
    n = resp.body ? cat_parse_page((const char *)resp.body, resp.body_size, e + 4, 4, &info) : -1;
    http_response_free(&resp);
    CHECK(n == 1 && info.total == 1);

    // Streaming install of the zipped ROM
    char id[CAT_ID_LEN * 3], path[600];
    cat_url_encode(alpha->rom_id, id, sizeof(id));
    snprintf(url, sizeof(url), "%s/api/v1/roms/%s?extract=nds", base, id);
    char target[CAT_FILE_LEN];
    cat_target_name(alpha->filename, target, sizeof(target));
    CHECK_STR(target, "Alpha Quest (USA).nds");
    snprintf(path, sizeof(path), "%s/%s", dir, target);
    Sink s = { .f = fopen(path, "wb") };
    struct timespec t0, t1;
    clock_gettime(CLOCK_MONOTONIC, &t0);
    HttpDownloadInfo dl;
    HttpDownloadResult rc = http_download(url, key, sink, &s, &dl);
    clock_gettime(CLOCK_MONOTONIC, &t1);
    fclose(s.f);
    double secs = (t1.tv_sec - t0.tv_sec) + (t1.tv_nsec - t0.tv_nsec) / 1e9;
    printf("download: rc=%d status=%d %lu/%lu bytes in %d chunks, %.2fs (%.0f KB/s on localhost)\n",
           rc, dl.status_code, (unsigned long)dl.received, (unsigned long)dl.total, s.calls, secs,
           dl.received / 1024.0 / (secs > 0 ? secs : 1));
    CHECK(rc == HTTP_DL_OK);
    CHECK(dl.status_code == 200);
    CHECK(dl.total > 0 && dl.received == dl.total);
    CHECK(same_file(path, expected));

    // Cancel part way
    Sink c = { .f = NULL, .cancel_after = 2 };
    rc = http_download(url, key, sink, &c, &dl);
    CHECK(rc == HTTP_DL_CANCELLED);
    CHECK(c.calls == 2);

    // Write error
    Sink w = { .f = NULL, .cancel_after = 0 };
    w.f = fopen("/dev/full", "wb");
    if (w.f) {
        setvbuf(w.f, NULL, _IONBF, 0);
        rc = http_download(url, key, sink, &w, &dl);
        CHECK(rc == HTTP_DL_WRITE);
        fclose(w.f);
    }

    // Zip with two ROMs: server refuses, the reason reaches the DS
    cat_url_encode(beta->rom_id, id, sizeof(id));
    snprintf(url, sizeof(url), "%s/api/v1/roms/%s?extract=nds", base, id);
    Sink b = {0};
    rc = http_download(url, key, sink, &b, &dl);
    CHECK(rc == HTTP_DL_STATUS);
    CHECK(dl.status_code == 422);
    CHECK(strstr(dl.error, "exactly one .nds") != NULL);
    CHECK(b.calls == 0);
    printf("422 text: %s\n", dl.error);

    // Unknown ROM, wrong key, nothing listening
    snprintf(url, sizeof(url), "%s/api/v1/roms/NDS_nope?extract=nds", base);
    rc = http_download(url, key, sink, &b, &dl);
    CHECK(rc == HTTP_DL_STATUS && dl.status_code == 404);
    snprintf(url, sizeof(url), "%s/api/v1/roms/%s?extract=nds", base, id);
    rc = http_download(url, "wrong-key", sink, &b, &dl);
    CHECK(rc == HTTP_DL_STATUS && (dl.status_code == 401 || dl.status_code == 403));
    rc = http_download("http://127.0.0.1:1/x", key, sink, &b, &dl);
    CHECK(rc == HTTP_DL_CONNECT);

    test_cache(base, key, dir);

    // Batched RA sets without an RA login on the server: clean 503
    snprintf(url, sizeof(url), "%s/api/v1/ra/sets", base);
    const char body[] = "{\"md5s\":[\"9dbd0337235cd8acf032c0fbfd649d70\"]}";
    resp = http_request_ex(url, HTTP_POST, key, "application/json", (const uint8_t *)body, strlen(body));
    CHECK(resp.status_code == 503);
    http_response_free(&resp);

    TEST_DONE();
}
