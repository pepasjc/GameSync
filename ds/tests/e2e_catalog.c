// End-to-end check of the DS catalog code paths against a running server:
// the real http.c (BSD sockets on the host) and catalog_data.c.
// Usage: e2e_catalog <server url> <api key> <expected rom file> <out dir>
// Expects the fixture made by run_e2e.sh: NDS games "Alpha Quest (USA)"
// (zip holding the expected ROM) and "Beta Twin (USA)" (zip with two ROMs).
#include "http.h"
#include "catalog_data.h"
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

    // Batched RA sets without an RA login on the server: clean 503
    snprintf(url, sizeof(url), "%s/api/v1/ra/sets", base);
    const char body[] = "{\"md5s\":[\"9dbd0337235cd8acf032c0fbfd649d70\"]}";
    resp = http_request_ex(url, HTTP_POST, key, "application/json", (const uint8_t *)body, strlen(body));
    CHECK(resp.status_code == 503);
    http_response_free(&resp);

    TEST_DONE();
}
