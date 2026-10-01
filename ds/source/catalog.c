#include "catalog.h"
#include "catalog_cache.h"
#include "catalog_data.h"
#include "config.h"
#include "http.h"
#include "ra.h"
#include "ui.h"
#include "views.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <errno.h>
#include <sys/stat.h>
#include <sys/statvfs.h>

// The Catalog tab. The server has thousands of DS games and a DS in DS mode
// has 4 MB of RAM, so the list never lives in memory whole:
//
// - Normally it is cached on the SD (catalog_cache.h), one file per system,
//   stamped with the fingerprint the server publishes for it. On the first
//   visit of the tab the fingerprints are fetched and only a system whose
//   fingerprint moved is downloaded again (in pages, streamed into the
//   file). The list is then paged from the file, a window of rows at a time,
//   and the RA filter and the search run over the file on the DS. Without a
//   server the cached copy is still browsable ("OFFLINE").
// - A server too old to publish fingerprints (404) gets the old behaviour:
//   the list is paged from the server, which also does the filtering.
//
// CAT_ROWS (list rows on the bottom screen) is in views.h.
#define CAT_WINDOW 128       // server mode: rows per request (each one a connection)
#define CAT_CACHE_WINDOW 40  // cache mode: rows decoded from the file around the view
#define CAT_PAGE 500         // rows per request when filling the cache (~250 KB of JSON)
#define CAT_SCAN_DEPTH 4     // ROM folder levels searched for installed games

// Systems the DS can run, in the order SELECT cycles through them, and the
// TWiLight Menu++ folder each installs to
#define CAT_NSYS 2
static const char *const cat_systems[] = { "NDS", "DSI", NULL };
static const char *const cat_system_dirs[] = { "roms/nds", "roms/dsi" };

typedef enum { SRC_NONE, SRC_CACHE, SRC_SERVER } CatSource;

typedef struct {
    SyncState *state;
    char base_url[256];
    bool tried;              // loaded once this session (first visit of the tab)
    bool loaded_with_wifi;   // false: loaded offline, try again once WiFi is up
    CatSource src;
    bool offline;            // cache shown because the server couldn't be reached
    char notice[100];        // strip message, "" = legend

    char systems[CAT_NSYS][8];
    const char *system_names[CAT_NSYS];
    int system_counts[CAT_NSYS];
    int nsystems, sys;

    CatCache cache;          // SRC_CACHE: file of the current system
    uint32_t *filtered;      // SRC_CACHE with a filter on: refs of the matches
    int nfiltered;

    bool ra_only;
    char search[48];

    CatEntry *win;           // loaded window of the (filtered) list
    int win_size;
    int win_off, win_count;
    int total;               // filtered count, -1 before the first page
    bool filter_ignored;     // server didn't apply has_ra (too old)
    char error[96];

    int selected, scroll;
    int direction;           // of the last move: where to load ahead

    char rom_dir[64];
    CatNameSet installed;
    CatEntry scratch;
} Catalog;

static Catalog *the_cat;
static char status_text[48];  // Settings > Refresh Catalog value

static const char *const TOOLBAR = "Game Catalog";

// ---------------------------------------------------------------------------
// Small helpers
// ---------------------------------------------------------------------------

static void mkdir_parents(const char *path) {
    char buf[128];
    snprintf(buf, sizeof(buf), "%s", path);
    for (char *p = strchr(buf, '/'); p; p = strchr(p + 1, '/')) {
        if (p > buf && p[-1] == ':') continue;  // "sd:/"
        *p = '\0';
        mkdir(buf, 0777);
        *p = '/';
    }
    mkdir(buf, 0777);
}

// GET with the same "no response" retry as the RA requests
static HttpResponse cat_get(const char *url, const char *api_key) {
    http_set_verbose(0);
    HttpResponse resp = http_request(url, HTTP_GET, api_key, NULL, 0);
    for (int attempt = 1; resp.status_code == 0 && attempt <= 3; attempt++) {
        http_response_free(&resp);
        for (int frame = 0; frame < 60 * 2 * attempt; frame++) swiWaitForVBlank();
        resp = http_request(url, HTTP_GET, api_key, NULL, 0);
    }
    http_set_verbose(1);
    return resp;
}

static void describe_failure(const HttpResponse *resp, char *out, size_t size) {
    if (resp->status_code == 0)
        snprintf(out, size, "No response from server");
    else if (resp->status_code == 401 || resp->status_code == 403)
        snprintf(out, size, "API key rejected (HTTP %d)", resp->status_code);
    else
        snprintf(out, size, "Server error (HTTP %d)", resp->status_code);
}

const char *catalog_cache_dir(void) {
    static char dir[80];
    snprintf(dir, sizeof(dir), "%s/cache", config_dir());
    return dir;
}

static void cache_path(const char *system, bool part, char *out, size_t size) {
    snprintf(out, size, "%s/%s.cat%s", catalog_cache_dir(), system, part ? ".part" : "");
}

static int wanted_index(const char *system) {
    for (int i = 0; cat_systems[i]; i++) {
        if (strcasecmp(cat_systems[i], system) == 0) return i;
    }
    return -1;
}

// Settings value: what the SD holds
static void update_status(void) {
    uint32_t total = 0;
    int files = 0;
    for (int i = 0; cat_systems[i]; i++) {
        char path[128];
        uint32_t n;
        cache_path(cat_systems[i], false, path, sizeof(path));
        if (cc_peek(path, NULL, NULL, &n)) {
            total += n;
            files++;
        }
    }
    if (the_cat && the_cat->tried && the_cat->src == SRC_SERVER) snprintf(status_text, sizeof(status_text), "Not cached (old server)");
    else if (files) snprintf(status_text, sizeof(status_text), "%lu games cached", (unsigned long)total);
    else snprintf(status_text, sizeof(status_text), "Not downloaded yet");
}

const char *catalog_status(void) {
    if (!status_text[0]) update_status();
    return status_text;
}

// ---------------------------------------------------------------------------
// The list: window of rows from the cache file or the server
// ---------------------------------------------------------------------------

static const CatEntry *entry_at(const Catalog *c, int index);
static bool is_installed(const Catalog *c, const CatEntry *e);

static void build_view(const Catalog *c, CatalogView *v) {
    memset(v, 0, sizeof(*v));
    v->system = c->nsystems > 0 ? c->systems[c->sys] : "NDS";
    v->systems = c->system_names;
    v->nsystems = c->nsystems;
    v->sys = c->sys;
    v->notice = c->notice;
    v->offline = c->offline;
    v->ra_only = c->ra_only;
    v->search = c->search;
    v->error = c->error;
    v->filter_ignored = c->filter_ignored;
    v->total = c->src == SRC_NONE ? (c->error[0] ? -1 : 0) : c->total;
    v->selected = c->selected;
    v->scroll = c->scroll;
    v->rom_dir = c->rom_dir;
    for (int r = 0; r < CAT_ROWS; r++) {
        int index = c->scroll + r;
        if (c->total < 0 || index >= c->total) break;
        const CatEntry *e = entry_at(c, index);
        v->rows[r].entry = e;
        v->rows[r].installed = e && is_installed(c, e);
        v->nrows = r + 1;
    }
    v->current = c->total > 0 ? entry_at(c, c->selected) : NULL;
    v->current_installed = v->current && is_installed(c, v->current);
}

static void show_loading(Catalog *c, bool searching) {
    CatalogView v;
    build_view(c, &v);
    v.loading = true;
    v.searching = searching;
    view_catalog_strip(&ui_bottom, &v);
    ui_present_rows(&ui_bottom, CONTENT_Y + CAT_ROWS * ROW_H + 1, 15);
}

static uint32_t list_ref(const Catalog *c, int index) {
    return c->filtered ? c->filtered[index] : c->cache.refs[index];
}

// Load the rows starting at `offset` into the window
static void fetch_window(Catalog *c, int offset) {
    if (c->src == SRC_CACHE) {
        int n = c->total - offset;
        if (n > c->win_size) n = c->win_size;
        if (n < 0) n = 0;
        c->win_off = offset;
        c->win_count = 0;
        for (int i = 0; i < n; i++) {
            if (!cc_read(&c->cache, list_ref(c, offset + i), &c->win[i])) {
                snprintf(c->error, sizeof(c->error), "Can't read the catalog cache: use Refresh Catalog");
                return;
            }
            c->win_count = i + 1;
        }
        return;
    }
    if (c->src != SRC_SERVER) return;

    char search[160] = "";
    if (c->search[0]) {
        char enc[144];
        cat_url_encode(c->search, enc, sizeof(enc));
        snprintf(search, sizeof(search), "&search=%s", enc);
    }
    char url[512];
    snprintf(url, sizeof(url), "%s/api/v1/roms?system=%s&limit=%d&offset=%d%s%s",
             c->base_url, c->systems[c->sys], CAT_WINDOW, offset,
             c->ra_only ? "&has_ra=true" : "", search);

    show_loading(c, false);
    HttpResponse resp = cat_get(url, c->state->api_key);
    c->error[0] = '\0';
    if (resp.status_code == 200 && resp.body) {
        CatPageInfo info;
        int n = cat_parse_page((const char *)resp.body, resp.body_size, c->win, CAT_WINDOW, &info);
        if (n < 0) {
            snprintf(c->error, sizeof(c->error), "Bad catalog reply");
            c->win_count = 0;
        } else {
            c->win_off = offset;
            c->win_count = n;
            c->total = info.total;
            c->filter_ignored = c->ra_only && info.has_ra != 1;
        }
    } else {
        describe_failure(&resp, c->error, sizeof(c->error));
        c->win_count = 0;
    }
    http_response_free(&resp);
}

static const CatEntry *entry_at(const Catalog *c, int index) {
    if (index < c->win_off || index >= c->win_off + c->win_count) return NULL;
    return &c->win[index - c->win_off];
}

// Make sure the visible rows are loaded
static void ensure_loaded(Catalog *c) {
    if (c->src == SRC_NONE) return;
    if (c->total < 0) {
        fetch_window(c, 0);
        if (c->total < 0) return;
    }
    for (int tries = 0; tries < 2; tries++) {
        if (c->selected >= c->total) c->selected = c->total > 0 ? c->total - 1 : 0;
        if (c->scroll > c->selected) c->scroll = c->selected;
        if (c->selected >= c->scroll + CAT_ROWS) c->scroll = c->selected - CAT_ROWS + 1;
        if (c->scroll < 0) c->scroll = 0;
        if (c->error[0] || cat_window_covers(c->win_off, c->win_count, c->scroll, CAT_ROWS, c->total))
            return;
        // The total may shrink on the refetch (server rescan): clamp again
        fetch_window(c, cat_window_start(c->scroll, CAT_ROWS, c->win_size, c->total, c->direction));
    }
}

// New filter/system: start over from the top
static void reset_list(Catalog *c) {
    c->win_off = c->win_count = 0;
    c->selected = c->scroll = 0;
    c->error[0] = '\0';
    c->filter_ignored = false;
    free(c->filtered);
    c->filtered = NULL;
    c->nfiltered = 0;
    if (c->src == SRC_CACHE) {
        c->total = (int)c->cache.count;
        if (c->ra_only || c->search[0]) {
            if (c->search[0]) show_loading(c, true);
            uint32_t *list = NULL;
            int n = cc_filter(&c->cache, c->ra_only, c->search, &list, &c->scratch);
            if (n < 0) {
                snprintf(c->error, sizeof(c->error), "Can't read the catalog cache: use Refresh Catalog");
                c->total = 0;
            } else {
                c->filtered = list;
                c->nfiltered = n;
                c->total = n;
            }
        }
    } else {
        c->total = -1;
    }
    ensure_loaded(c);
}

static void scan_installed(Catalog *c) {
    cat_names_free(&c->installed);
    int idx = c->nsystems > 0 ? wanted_index(c->systems[c->sys]) : 0;
    if (idx < 0) idx = 0;
    snprintf(c->rom_dir, sizeof(c->rom_dir), "%s/%s", ra_sd_root(), cat_system_dirs[idx]);
    cat_names_scan(&c->installed, c->rom_dir, CAT_SCAN_DEPTH);
}

static bool is_installed(const Catalog *c, const CatEntry *e) {
    char target[CAT_FILE_LEN];
    cat_target_name(e->filename, target, sizeof(target));
    return cat_names_contains(&c->installed, target);
}

// Show the current system: open its cache file, find what's on the SD
static void open_system(Catalog *c) {
    cc_close(&c->cache);
    c->error[0] = '\0';
    if (c->nsystems == 0) {
        c->total = 0;
        return;
    }
    if (c->src == SRC_CACHE) {
        char path[128];
        cache_path(c->systems[c->sys], false, path, sizeof(path));
        if (!cc_open(&c->cache, path)) {
            snprintf(c->error, sizeof(c->error), "Can't read the catalog cache: use Refresh Catalog");
            c->total = 0;
        }
    }
    scan_installed(c);
    if (!c->error[0]) reset_list(c);
}

// ---------------------------------------------------------------------------
// Loading: fingerprints, then only the systems that changed
// ---------------------------------------------------------------------------

typedef struct {
    int refreshed, unchanged, failed;
    bool cached;             // fingerprints route present: cache in use
    bool offline;
} LoadReport;

// Server list of the old (uncached) mode
static bool load_systems(Catalog *c) {
    char url[320];
    snprintf(url, sizeof(url), "%s/api/v1/roms/systems", c->base_url);
    HttpResponse resp = cat_get(url, c->state->api_key);
    bool ok = false;
    if (resp.status_code == 200 && resp.body) {
        int n = cat_parse_systems((const char *)resp.body, resp.body_size, cat_systems,
                                  c->systems, c->system_counts, CAT_NSYS);
        if (n < 0) {
            snprintf(c->error, sizeof(c->error), "Bad reply from server");
        } else {
            c->nsystems = n;
            ok = true;
        }
    } else {
        describe_failure(&resp, c->error, sizeof(c->error));
    }
    http_response_free(&resp);
    return ok;
}

typedef struct {
    CatCacheWriter *w;
} FillState;

static bool fill_add(const CatEntry *e, void *user) {
    FillState *fs = user;
    return ccw_add(fs->w, e);
}

// Download one system's whole list into its cache file. On failure the old
// file (if any) stays as it was and *why says what went wrong.
static bool fill_system(Catalog *c, const char *system, const char *fingerprint, int expected,
                        char *why, size_t why_size) {
    char part[128], final_path[128];
    cache_path(system, true, part, sizeof(part));
    cache_path(system, false, final_path, sizeof(final_path));
    CatCacheWriter w;
    if (!ccw_begin(&w, part, system)) {
        snprintf(why, why_size, "Can't write %.60s", part);
        return false;
    }
    FillState fs = { &w };
    int offset = 0, total = expected > 0 ? expected : 0;
    char detail[48];
    static const Hint stop[] = { { "B", "Hold to cancel" }, { NULL, NULL } };
    ui_task_hints(stop);
    while (1) {
        snprintf(detail, sizeof(detail), "%s %d/%d", system, offset, total);
        ui_task_progress("Game list", detail, (uint32_t)offset, (uint32_t)(total > 0 ? total : 1));
        char url[384];
        snprintf(url, sizeof(url), "%s/api/v1/roms?system=%s&limit=%d&offset=%d", c->base_url, system, CAT_PAGE,
                 offset);
        HttpResponse resp = cat_get(url, c->state->api_key);
        if (resp.status_code != 200 || !resp.body) {
            describe_failure(&resp, why, why_size);
            http_response_free(&resp);
            ccw_abort(&w);
            return false;
        }
        CatPageInfo info;
        int n = cat_parse_page_cb((const char *)resp.body, resp.body_size, &c->scratch, fill_add, &fs, &info);
        http_response_free(&resp);
        if (n < 0 || w.failed) {
            snprintf(why, why_size, n < 0 ? "Bad catalog reply" : "Can't write the cache (SD full?)");
            ccw_abort(&w);
            return false;
        }
        offset += n;
        total = info.total;
        if (!info.has_more || n == 0) break;
        scanKeys();
        if (keysHeld() & KEY_B) {
            snprintf(why, why_size, "Cancelled");
            ccw_abort(&w);
            return false;
        }
    }
    snprintf(detail, sizeof(detail), "%s %d/%d", system, offset, offset);
    ui_task_progress("Game list", detail, 1, 1);
    if (!ccw_finish(&w, fingerprint, final_path)) {
        snprintf(why, why_size, "Can't write the cache (SD full?)");
        return false;
    }
    return true;
}

static void set_systems_from_cache(Catalog *c) {
    c->nsystems = 0;
    for (int i = 0; cat_systems[i]; i++) {
        char path[128];
        uint32_t n;
        cache_path(cat_systems[i], false, path, sizeof(path));
        if (cc_peek(path, NULL, NULL, &n)) {
            snprintf(c->systems[c->nsystems], 8, "%s", cat_systems[i]);
            c->system_counts[c->nsystems] = (int)n;
            c->nsystems++;
        }
    }
}

static bool cache_exists(void) {
    for (int i = 0; cat_systems[i]; i++) {
        char path[128];
        cache_path(cat_systems[i], false, path, sizeof(path));
        if (cc_peek(path, NULL, NULL, NULL)) return true;
    }
    return false;
}

static void catalog_load(Catalog *c, bool has_wifi, LoadReport *rep) {
    memset(rep, 0, sizeof(*rep));
    char prev_sys[8];
    snprintf(prev_sys, sizeof(prev_sys), "%s", c->nsystems > 0 ? c->systems[c->sys] : "");
    cc_close(&c->cache);
    free(c->filtered);
    c->filtered = NULL;
    c->src = SRC_NONE;
    c->offline = false;
    c->notice[0] = c->error[0] = '\0';
    c->nsystems = c->sys = 0;
    c->total = -1;
    c->win_off = c->win_count = 0;
    c->tried = true;

    snprintf(c->base_url, sizeof(c->base_url), "%s", c->state->server_url);
    size_t len = strlen(c->base_url);
    while (len > 0 && c->base_url[len - 1] == '/') c->base_url[--len] = '\0';

    char reason[96] = "WiFi is off";
    bool legacy = false, have_fps = false;
    char fp_sys[CAT_NSYS][8], fps[CAT_NSYS][CAT_FP_LEN];
    int counts[CAT_NSYS], nfp = 0;
    if (has_wifi) {
        ui_task_status("Checking the game list", "");
        char url[320];
        snprintf(url, sizeof(url), "%s/api/v1/roms/fingerprints", c->base_url);
        HttpResponse resp;
        if (cache_exists()) {
            // A copy to fall back on: one short try, so a server that's down
            // costs seconds rather than minutes of retries
            http_set_timeout(10);
            http_set_verbose(0);
            resp = http_request(url, HTTP_GET, c->state->api_key, NULL, 0);
            http_set_verbose(1);
            http_set_timeout(0);
        } else {
            resp = cat_get(url, c->state->api_key);
        }
        if (resp.status_code == 200 && resp.body) {
            nfp = cat_parse_fingerprints((const char *)resp.body, resp.body_size, cat_systems, fp_sys, fps,
                                         counts, CAT_NSYS);
            if (nfp >= 0) have_fps = true;
            else legacy = true;  // odd reply: behave like an old server
        } else if (resp.status_code == 404 || resp.status_code == 405) {
            legacy = true;
        } else {
            describe_failure(&resp, reason, sizeof(reason));
        }
        http_response_free(&resp);
    }

    if (have_fps) {
        rep->cached = true;
        mkdir_parents(catalog_cache_dir());
        // Systems the server no longer has: drop their files
        for (int i = 0; cat_systems[i]; i++) {
            bool listed = false;
            for (int k = 0; k < nfp; k++) listed |= strcasecmp(fp_sys[k], cat_systems[i]) == 0;
            if (!listed) {
                char path[128];
                cache_path(cat_systems[i], false, path, sizeof(path));
                remove(path);
            }
        }
        for (int k = 0; k < nfp; k++) {
            char path[128], have_fp[CAT_FP_LEN];
            cache_path(fp_sys[k], false, path, sizeof(path));
            bool have = cc_peek(path, NULL, have_fp, NULL);
            if (have && fps[k][0] && strcmp(have_fp, fps[k]) == 0) {
                rep->unchanged++;
                continue;
            }
            char why[96];
            bool filled = fill_system(c, fp_sys[k], fps[k], counts[k], why, sizeof(why));
            ui_task_hints(NULL);
            if (filled) {
                rep->refreshed++;
            } else {
                rep->failed++;
                if (have) snprintf(c->notice, sizeof(c->notice), "%s not updated (%.40s): cached copy", fp_sys[k], why);
                else snprintf(c->notice, sizeof(c->notice), "%s not loaded: %.60s", fp_sys[k], why);
                snprintf(reason, sizeof(reason), "%s", why);
            }
        }
        set_systems_from_cache(c);
        c->src = SRC_CACHE;
        if (c->nsystems == 0 && rep->failed) {
            snprintf(c->error, sizeof(c->error), "%s", reason);
            c->src = SRC_NONE;
        } else if (rep->refreshed && !rep->failed) {
            snprintf(c->notice, sizeof(c->notice), "Game list updated: %d refreshed, %d unchanged", rep->refreshed,
                     rep->unchanged);
        }
    } else if (legacy) {
        if (load_systems(c)) c->src = SRC_SERVER;
    } else {
        // No server: the last copy beats an empty tab
        set_systems_from_cache(c);
        if (c->nsystems > 0) {
            c->src = SRC_CACHE;
            c->offline = rep->offline = true;
            snprintf(c->notice, sizeof(c->notice), "Offline: showing the cached list");
        } else {
            snprintf(c->error, sizeof(c->error), "%s", reason);
        }
    }

    for (int i = 0; i < c->nsystems; i++) {
        c->system_names[i] = c->systems[i];
        if (prev_sys[0] && strcmp(prev_sys, c->systems[i]) == 0) c->sys = i;
    }
    c->win_size = c->src == SRC_CACHE ? CAT_CACHE_WINDOW : CAT_WINDOW;
    update_status();
    if (c->src != SRC_NONE) {
        ui_task_status("Looking for games on the SD", "");
        open_system(c);
    }
}

// ---------------------------------------------------------------------------
// Drawing
// ---------------------------------------------------------------------------

static void draw(Catalog *c) {
    CatalogView v;
    build_view(c, &v);
    view_catalog_details(&ui_top, &v);
    view_catalog_list(&ui_bottom, &v);
    ui_present(&ui_top);
    ui_present(&ui_bottom);
}

// ---------------------------------------------------------------------------
// Install
// ---------------------------------------------------------------------------

typedef struct {
    FILE *f;
    const char *dir;
    uint32_t replaced;       // size of the file being replaced (freed at the end)
    uint64_t start, last_draw, sample_tick;
    uint32_t sample_bytes;
    uint32_t speed;          // bytes/s over the last ~second
    bool checked;            // first-chunk checks done
    bool no_space, bad_data;
    InstallView view;
} Download;

static uint32_t bytes_per_second(uint32_t bytes, uint64_t ticks) {
    if (ticks == 0) return 0;
    return (uint32_t)((uint64_t)bytes * TICK_FREQ / ticks);
}

static void draw_progress(Download *dl, uint32_t done, uint32_t total) {
    uint64_t now = tickGetCount();
    uint32_t avg = bytes_per_second(done, now - dl->start);
    InstallView *v = &dl->view;
    v->started = true;
    v->done = done;
    v->total = total;
    v->speed = dl->speed;
    v->avg = avg;
    v->elapsed = (unsigned)((now - dl->start) / TICK_FREQ);
    v->left = (total && avg > 0 && done < total) ? (unsigned)((total - done) / avg) : ~0u;
    view_install(&ui_bottom, v);
    ui_present(&ui_bottom);
}

static int download_sink(const uint8_t *data, size_t size, uint32_t done, uint32_t total, void *user) {
    Download *dl = user;

    if (!dl->checked) {
        dl->checked = true;
        // An old server may ignore ?extract=nds and send the zip itself
        if (size >= 4 && memcmp(data, "PK\x03\x04", 4) == 0) {
            dl->bad_data = true;
            return -1;
        }
        struct statvfs vfs;
        if (total && statvfs(dl->dir, &vfs) == 0 && vfs.f_frsize) {
            // 0 free is more likely an unsupported statvfs than a full card;
            // a really full card still fails on the first write
            uint64_t free_bytes = (uint64_t)vfs.f_bavail * vfs.f_frsize;
            if (free_bytes > 0 && free_bytes + dl->replaced < (uint64_t)total + 64 * 1024) {
                dl->no_space = true;
                return -1;
            }
        }
    }

    if (fwrite(data, 1, size, dl->f) != size) return -1;

    uint64_t now = tickGetCount();
    if (now - dl->sample_tick >= TICK_FREQ) {
        dl->speed = bytes_per_second(done - dl->sample_bytes, now - dl->sample_tick);
        dl->sample_tick = now;
        dl->sample_bytes = done;
    }
    if (now - dl->last_draw >= TICK_FREQ / 4 || (total && done >= total)) {
        dl->last_draw = now;
        draw_progress(dl, done, total);
    }

    if (!pmMainLoop()) return 1;
    scanKeys();
    if (keysHeld() & KEY_B) return 1;
    return 0;
}

static bool confirm_install(const Catalog *cat, const CatEntry *e, const char *target, bool exists) {
    view_install_confirm(&ui_bottom, e, cat->rom_dir, target, exists);
    ui_present(&ui_bottom);
    return (ui_wait(KEY_A | KEY_B) & KEY_A) != 0;
}

// Show the result under the progress card and wait for a button
static void finish(Download *dl, UiKind kind, const char *result) {
    dl->view.finished = true;
    dl->view.kind = kind;
    dl->view.result = result;
    view_install(&ui_bottom, &dl->view);
    ui_present(&ui_bottom);
    ui_wait(0);
}

static void add_line(InstallView *v, Color c, const char *text) {
    for (int i = 0; i < 3; i++) {
        if (!v->lines[i]) {
            v->lines[i] = text;
            v->line_colors[i] = c;
            return;
        }
    }
}

static void install(Catalog *cat, const CatEntry *entry) {
    CatEntry e = *entry;  // the window may be refetched meanwhile
    SyncState *state = cat->state;

    char target[CAT_FILE_LEN];
    cat_target_name(e.filename, target, sizeof(target));
    char path[320], part[330];
    snprintf(path, sizeof(path), "%s/%s", cat->rom_dir, target);
    snprintf(part, sizeof(part), "%s.part", path);

    const char *ext = strrchr(e.filename, '.');
    bool zipped = ext && strcasecmp(ext, ".zip") == 0;
    bool raw = ext && (strcasecmp(ext, ".nds") == 0 || strcasecmp(ext, ".dsi") == 0);
    const char *name = e.name[0] ? e.name : e.filename;

    if (e.truncated) {
        ui_message("Install", "Can't install", "Name too long to download.", KIND_ERROR, HINTS_ANY, 0);
        return;
    }
    if (!raw && !(zipped && e.can_extract_nds)) {
        char text[200];
        snprintf(text, sizeof(text), "%.90s\n\n%s", e.filename,
                 zipped ? "The server can't unzip DS ROMs yet: update the server."
                        : "Only .nds or single-ROM .zip files can be installed.");
        ui_message("Install", "Can't install this file", text, KIND_ERROR, HINTS_ANY, 0);
        return;
    }

    struct stat st;
    bool exists = (stat(path, &st) == 0);
    if (!confirm_install(cat, &e, target, exists)) return;

    Download dl;
    memset(&dl, 0, sizeof(dl));
    dl.view.name = name;
    dl.view.left = ~0u;
    view_install(&ui_bottom, &dl.view);
    ui_present(&ui_bottom);

    mkdir_parents(cat->rom_dir);
    remove(part);
    FILE *f = fopen(part, "wb");
    if (!f) {
        add_line(&dl.view, C_TEXT_DIM, part);
        finish(&dl, KIND_ERROR, "Can't create the file on SD");
        return;
    }

    char id[CAT_ID_LEN * 3];
    cat_url_encode(e.rom_id, id, sizeof(id));
    char url[CAT_ID_LEN * 3 + 320];
    snprintf(url, sizeof(url), "%s/api/v1/roms/%s%s", cat->base_url, id, zipped ? "?extract=nds" : "");

    dl.f = f;
    dl.dir = cat->rom_dir;
    dl.replaced = exists ? (uint32_t)st.st_size : 0;
    dl.start = dl.last_draw = dl.sample_tick = tickGetCount();

    // Longer timeout: the server may take a moment to open a big zip
    http_set_timeout(60);
    HttpDownloadInfo info;
    HttpDownloadResult rc = http_download(url, state->api_key, download_sink, &dl, &info);
    http_set_timeout(0);

    bool closed_ok = (fclose(f) == 0);
    uint64_t ticks = tickGetCount() - dl.start;
    if (rc == HTTP_DL_OK && !closed_ok) rc = HTTP_DL_WRITE;

    char line1[96], line2[96];
    if (rc != HTTP_DL_OK) {
        remove(part);
        char a[16], b[16];
        switch (rc) {
            case HTTP_DL_CANCELLED:
                finish(&dl, KIND_WARN, "Cancelled");
                break;
            case HTTP_DL_CONNECT:
                finish(&dl, KIND_ERROR, "No response from server");
                break;
            case HTTP_DL_STATUS: {
                snprintf(line1, sizeof(line1), "Server error (HTTP %d)", info.status_code);
                cat_ascii(info.error, line2, sizeof(line2));
                if (line2[0]) add_line(&dl.view, C_TEXT_DIM, line2);
                finish(&dl, KIND_ERROR, line1);
                break;
            }
            case HTTP_DL_WRITE:
                if (dl.bad_data) {
                    add_line(&dl.view, C_TEXT_DIM, "Update the server");
                    finish(&dl, KIND_ERROR, "Server sent a zip, not a ROM");
                } else if (dl.no_space) {
                    finish(&dl, KIND_ERROR, "Not enough space on the SD");
                } else {
                    finish(&dl, KIND_ERROR, "SD write failed (card full?)");
                }
                break;
            case HTTP_DL_SHORT:
                cat_format_size(info.received, a, sizeof(a));
                cat_format_size(info.total, b, sizeof(b));
                snprintf(line1, sizeof(line1), "at %s of %s", a, b);
                add_line(&dl.view, C_TEXT_DIM, line1);
                finish(&dl, KIND_ERROR, "Connection lost");
                break;
            default:
                finish(&dl, KIND_ERROR, "Download failed");
                break;
        }
        return;
    }

    if (exists) remove(path);
    if (rename(part, path) != 0) {
        remove(part);
        finish(&dl, KIND_ERROR, "Couldn't rename the download");
        return;
    }
    cat_names_add(&cat->installed, target);
    cat_names_sort(&cat->installed);

    // Final numbers on the progress card
    dl.speed = 0;
    draw_progress(&dl, info.received, info.received);
    dl.view.elapsed = (unsigned)(ticks / TICK_FREQ);

    char size[16], secs[16], ra_line[64];
    cat_format_size(info.received, size, sizeof(size));
    view_format_time((unsigned)(ticks / TICK_FREQ), secs, sizeof(secs));
    snprintf(line1, sizeof(line1), "%s in %s", size, secs);
    snprintf(line2, sizeof(line2), "Average: %lu KB/s", (unsigned long)(bytes_per_second(info.received, ticks) / 1024));
    add_line(&dl.view, C_TEXT, line1);
    add_line(&dl.view, C_TEXT_DIM, line2);

    if (cat_entry_has_ra(&e)) {
        dl.view.finished = true;
        dl.view.kind = KIND_OK;
        dl.view.result = "Installed";
        snprintf(ra_line, sizeof(ra_line), "Getting achievement set...");
        add_line(&dl.view, C_TEXT_DIM, ra_line);
        view_install(&ui_bottom, &dl.view);
        ui_present(&ui_bottom);

        int count = 0;
        int r = ra_install_set(state, path, &count);
        Color c = C_ERR;
        if (r == RA_SET_OK) {
            snprintf(ra_line, sizeof(ra_line), "%d achievements ready", count);
            c = C_GOLD;
        } else if (r == RA_SET_UNKNOWN) {
            snprintf(ra_line, sizeof(ra_line), "RA doesn't know this dump");
            c = C_TEXT_DIM;
        } else if (r == RA_SET_NOT_DS_ROM) {
            snprintf(ra_line, sizeof(ra_line), "Not a DS ROM?");
        } else {
            snprintf(ra_line, sizeof(ra_line), "Set not saved: use Settings > Achievement Sets");
        }
        dl.view.line_colors[2] = c;
    }
    finish(&dl, KIND_OK, "Installed");
}

// ---------------------------------------------------------------------------
// Tab
// ---------------------------------------------------------------------------

static void move_to(Catalog *c, int index, bool wrap) {
    if (c->total <= 0) return;
    if (wrap) {
        index = (index % c->total + c->total) % c->total;
    } else {
        if (index < 0) index = 0;
        if (index >= c->total) index = c->total - 1;
    }
    c->direction = (index > c->selected) - (index < c->selected);
    c->selected = index;
    ensure_loaded(c);
}

static Catalog *get_catalog(SyncState *state) {
    if (the_cat) return the_cat;
    Catalog *c = calloc(1, sizeof(Catalog));
    CatEntry *win = c ? malloc(CAT_WINDOW * sizeof(CatEntry)) : NULL;
    if (!win) {
        free(c);
        ui_message(TOOLBAR, "Out of memory", "", KIND_ERROR, HINTS_ANY, 0);
        return NULL;
    }
    c->state = state;
    c->win = win;
    c->win_size = CAT_WINDOW;
    c->total = -1;
    the_cat = c;
    return c;
}

static void load(Catalog *c, bool has_wifi, const char *status) {
    LoadReport rep;
    ui_task_begin(TOOLBAR, status);
    catalog_load(c, has_wifi, &rep);
    c->loaded_with_wifi = has_wifi;
}

bool catalog_tab_enter(SyncState *state, bool has_wifi) {
    Catalog *c = get_catalog(state);
    if (!c) return false;
    // First visit, or WiFi came up since an offline load
    if (!c->tried || (has_wifi && !c->loaded_with_wifi)) load(c, has_wifi, "Loading the game list");
    return true;
}

void catalog_tab_draw(void) {
    if (the_cat) draw(the_cat);
}

void catalog_tab_input(int down, int rep, bool has_wifi) {
    Catalog *c = the_cat;
    if (!c) return;
    // A one-off notice ("Game list updated") goes at the first key; the
    // offline one stays
    if (down && !c->offline) c->notice[0] = '\0';

    if (rep & KEY_DOWN) move_to(c, c->selected + 1, true);
    if (rep & KEY_UP) move_to(c, c->selected - 1, true);
    if (rep & KEY_RIGHT) move_to(c, c->selected + CAT_ROWS, false);
    if (rep & KEY_LEFT) move_to(c, c->selected - CAT_ROWS, false);

    if (c->src == SRC_NONE) {
        if (down & KEY_A) load(c, has_wifi, "Loading the game list");
        return;
    }
    if (down & KEY_X) {
        c->ra_only = !c->ra_only;
        reset_list(c);
    }
    if (down & KEY_Y) {
        char text[sizeof(c->search)];
        snprintf(text, sizeof(text), "%s", c->search);
        if (config_edit_field("Search game names (empty = all)", text, sizeof(text))) {
            snprintf(c->search, sizeof(c->search), "%s", text);
            draw(c);
            reset_list(c);
        }
    }
    if ((down & KEY_B) && c->search[0]) {
        c->search[0] = '\0';
        reset_list(c);
    }
    if ((down & KEY_SELECT) && c->nsystems > 1) {
        c->sys = (c->sys + 1) % c->nsystems;
        open_system(c);
    }
    if (down & KEY_A) {
        if (c->error[0]) {
            // Try again after a failed page / unreadable cache
            c->error[0] = '\0';
            if (c->src == SRC_CACHE) load(c, has_wifi, "Loading the game list");
            else if (c->total < 0) reset_list(c);
            else fetch_window(c, cat_window_start(c->scroll, CAT_ROWS, c->win_size, c->total, c->direction));
        } else {
            const CatEntry *e = c->total > 0 ? entry_at(c, c->selected) : NULL;
            if (e && !has_wifi)
                ui_message("Install", "WiFi required", "Use Connect WiFi in Settings first.", KIND_ERROR, HINTS_ANY, 0);
            else if (e)
                install(c, e);
        }
    }
}

// ---------------------------------------------------------------------------
// Settings > Refresh Catalog
// ---------------------------------------------------------------------------

// Ask the server to walk its ROM folder again (its list lives in memory and
// otherwise waits for the periodic scan). Never fatal: the refetch goes on.
static void rescan_server(Catalog *c, char *out, size_t size, Color *color) {
    char url[320];
    snprintf(url, sizeof(url), "%s/api/v1/roms/scan", c->base_url);
    // A big library takes a while to walk
    http_set_timeout(300);
    http_set_verbose(0);
    HttpResponse resp = http_request(url, HTTP_GET, c->state->api_key, NULL, 0);
    http_set_verbose(1);
    http_set_timeout(0);
    *color = C_TEXT_DIM;
    if (resp.status_code == 200) {
        const char *count = resp.body ? strstr((const char *)resp.body, "\"count\"") : NULL;
        long n = -1;
        if (count) {
            const char *colon = strchr(count, ':');
            if (colon) n = strtol(colon + 1, NULL, 10);
        }
        if (n >= 0) snprintf(out, size, "%ld ROMs", n);
        else snprintf(out, size, "Done");
        *color = C_OK;
    } else if (resp.status_code == 403) {
        snprintf(out, size, "Not allowed");
        *color = C_WARN;
    } else if (resp.status_code == 404 || resp.status_code == 405) {
        snprintf(out, size, "Not supported");
        *color = C_WARN;
    } else if (resp.status_code == 0) {
        snprintf(out, size, "No response");
        *color = C_ERR;
    } else {
        snprintf(out, size, "HTTP %d", resp.status_code);
        *color = C_ERR;
    }
    http_response_free(&resp);
}

void catalog_refresh(SyncState *state, bool has_wifi) {
    if (!has_wifi) {
        ui_message("Refresh catalog", "WiFi required", "Use Connect WiFi first.", KIND_ERROR, HINTS_ANY, 0);
        return;
    }
    Catalog *c = get_catalog(state);
    if (!c) return;

    ui_task_begin("Refresh catalog", "Asking the server to rescan its ROMs");
    snprintf(c->base_url, sizeof(c->base_url), "%s", state->server_url);
    size_t len = strlen(c->base_url);
    while (len > 0 && c->base_url[len - 1] == '/') c->base_url[--len] = '\0';
    char rescan[32];
    Color rescan_color;
    rescan_server(c, rescan, sizeof(rescan), &rescan_color);

    // Throw the cached copy away, then fetch every system again
    cc_close(&c->cache);
    for (int i = 0; cat_systems[i]; i++) {
        char path[128];
        cache_path(cat_systems[i], false, path, sizeof(path));
        remove(path);
        cache_path(cat_systems[i], true, path, sizeof(path));
        remove(path);
    }
    ui_task_status("Downloading the game list", "");
    LoadReport rep;
    catalog_load(c, has_wifi, &rep);
    c->loaded_with_wifi = true;
    c->notice[0] = '\0';

    SummaryRow rows[2 + CAT_NSYS];
    char counts[CAT_NSYS][24];
    int n = 0;
    rows[n++] = (SummaryRow){ "Server rescan", rescan, rescan_color };
    for (int i = 0; i < c->nsystems && i < CAT_NSYS; i++) {
        snprintf(counts[i], sizeof(counts[i]), "%d games", c->system_counts[i]);
        rows[n++] = (SummaryRow){ c->systems[i], counts[i], C_TEXT };
    }
    rows[n++] = (SummaryRow){ "Cache", rep.cached ? "Saved on the SD" : "Not used (old server)",
                              rep.cached ? C_OK : C_TEXT_DIM };

    bool failed = c->src == SRC_NONE || rep.failed;
    const char *note = c->error[0] ? c->error : (rep.failed ? "Some systems could not be downloaded." : NULL);
    if (!failed && c->nsystems == 0) note = "The server has no DS games.";
    view_summary(&ui_bottom, "Refresh catalog", failed ? "Refresh failed" : "Catalog refreshed",
                 failed ? KIND_ERROR : KIND_OK, rows, n, note, HINTS_ANY);
    ui_present(&ui_bottom);
    ui_wait(0);
}

void catalog_shutdown(void) {
    Catalog *c = the_cat;
    if (!c) return;
    cc_close(&c->cache);
    free(c->filtered);
    cat_names_free(&c->installed);
    free(c->win);
    free(c);
    the_cat = NULL;
}
