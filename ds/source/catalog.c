#include "catalog.h"
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

// The server has thousands of DS games, so the list is paged from the
// server (filtered there too) and only a window of it is held here. Each
// page is one connection, and the DSi stack only manages a few dozen of
// those before it needs a rest, so the window is large.
// CAT_ROWS (list rows on the bottom screen) is in views.h.
#define CAT_WINDOW 128
#define CAT_JUMP 100         // L/R
#define CAT_SCAN_DEPTH 4     // ROM folder levels searched for installed games

// Systems the DS can run, in the order SELECT cycles through them, and the
// TWiLight Menu++ folder each installs to
static const char *const cat_systems[] = { "NDS", "DSI", NULL };
static const char *const cat_system_dirs[] = { "roms/nds", "roms/dsi" };

typedef struct {
    SyncState *state;
    char base_url[256];

    char systems[2][8];
    int system_counts[2];
    int nsystems, sys;

    bool ra_only;
    char search[48];

    CatEntry *win;           // loaded window of the filtered list
    int win_off, win_count;
    int total;               // filtered count, -1 before the first page
    bool filter_ignored;     // server didn't apply has_ra (too old)
    char error[96];

    int selected, scroll;
    int direction;           // of the last move: where to load ahead

    char rom_dir[64];
    CatNameSet installed;
} Catalog;

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

// ---------------------------------------------------------------------------
// Server list
// ---------------------------------------------------------------------------

static bool load_systems(Catalog *cat) {
    char url[320];
    snprintf(url, sizeof(url), "%s/api/v1/roms/systems", cat->base_url);
    HttpResponse resp = cat_get(url, cat->state->api_key);
    bool ok = false;
    if (resp.status_code == 200 && resp.body) {
        int n = cat_parse_systems((const char *)resp.body, resp.body_size, cat_systems,
                                  cat->systems, cat->system_counts, 2);
        if (n < 0) {
            snprintf(cat->error, sizeof(cat->error), "Bad reply from server");
        } else {
            cat->nsystems = n;
            ok = true;
        }
    } else {
        describe_failure(&resp, cat->error, sizeof(cat->error));
    }
    http_response_free(&resp);
    return ok;
}

static const CatEntry *entry_at(const Catalog *cat, int index);
static bool is_installed(const Catalog *cat, const CatEntry *e);

static void build_view(const Catalog *cat, CatalogView *v) {
    memset(v, 0, sizeof(*v));
    v->system = cat->nsystems > 0 ? cat->systems[cat->sys] : "NDS";
    v->nsystems = cat->nsystems;
    v->ra_only = cat->ra_only;
    v->search = cat->search;
    v->error = cat->error;
    v->filter_ignored = cat->filter_ignored;
    v->total = cat->total;
    v->selected = cat->selected;
    v->scroll = cat->scroll;
    v->rom_dir = cat->rom_dir;
    for (int r = 0; r < CAT_ROWS; r++) {
        int index = cat->scroll + r;
        if (cat->total < 0 || index >= cat->total) break;
        const CatEntry *e = entry_at(cat, index);
        v->rows[r].entry = e;
        v->rows[r].installed = e && is_installed(cat, e);
        v->nrows = r + 1;
    }
    v->current = cat->total > 0 ? entry_at(cat, cat->selected) : NULL;
    v->current_installed = v->current && is_installed(cat, v->current);
}

static void show_loading(Catalog *cat) {
    CatalogView v;
    build_view(cat, &v);
    v.loading = true;
    view_catalog_strip(&ui_bottom, &v);
    ui_present_rows(&ui_bottom, CONTENT_Y + CAT_ROWS * ROW_H + 1, 15);
}

// Load the page starting at `offset` into the window
static void fetch_window(Catalog *cat, int offset) {
    char search[160] = "";
    if (cat->search[0]) {
        char enc[144];
        cat_url_encode(cat->search, enc, sizeof(enc));
        snprintf(search, sizeof(search), "&search=%s", enc);
    }
    char url[512];
    snprintf(url, sizeof(url), "%s/api/v1/roms?system=%s&limit=%d&offset=%d%s%s",
             cat->base_url, cat->systems[cat->sys], CAT_WINDOW, offset,
             cat->ra_only ? "&has_ra=true" : "", search);

    show_loading(cat);
    HttpResponse resp = cat_get(url, cat->state->api_key);
    cat->error[0] = '\0';
    if (resp.status_code == 200 && resp.body) {
        CatPageInfo info;
        int n = cat_parse_page((const char *)resp.body, resp.body_size, cat->win, CAT_WINDOW, &info);
        if (n < 0) {
            snprintf(cat->error, sizeof(cat->error), "Bad catalog reply");
            cat->win_count = 0;
        } else {
            cat->win_off = offset;
            cat->win_count = n;
            cat->total = info.total;
            cat->filter_ignored = cat->ra_only && info.has_ra != 1;
        }
    } else {
        describe_failure(&resp, cat->error, sizeof(cat->error));
        cat->win_count = 0;
    }
    http_response_free(&resp);
}

static const CatEntry *entry_at(const Catalog *cat, int index) {
    if (index < cat->win_off || index >= cat->win_off + cat->win_count) return NULL;
    return &cat->win[index - cat->win_off];
}

// Make sure the visible rows are loaded
static void ensure_loaded(Catalog *cat) {
    if (cat->total < 0) {
        fetch_window(cat, 0);
        if (cat->total < 0) return;
    }
    for (int tries = 0; tries < 2; tries++) {
        if (cat->selected >= cat->total) cat->selected = cat->total > 0 ? cat->total - 1 : 0;
        if (cat->scroll > cat->selected) cat->scroll = cat->selected;
        if (cat->selected >= cat->scroll + CAT_ROWS) cat->scroll = cat->selected - CAT_ROWS + 1;
        if (cat->scroll < 0) cat->scroll = 0;
        if (cat->error[0] || cat_window_covers(cat->win_off, cat->win_count, cat->scroll, CAT_ROWS, cat->total))
            return;
        // The total may shrink on the refetch (server rescan): clamp again
        fetch_window(cat, cat_window_start(cat->scroll, CAT_ROWS, CAT_WINDOW, cat->total, cat->direction));
    }
}

// New filter/system: start over from the top
static void reset_list(Catalog *cat) {
    cat->total = -1;
    cat->win_off = cat->win_count = 0;
    cat->selected = cat->scroll = 0;
    cat->error[0] = '\0';
    ensure_loaded(cat);
}

static void scan_installed(Catalog *cat) {
    cat_names_free(&cat->installed);
    snprintf(cat->rom_dir, sizeof(cat->rom_dir), "%s/%s", ra_sd_root(), cat_system_dirs[cat->sys]);
    cat_names_scan(&cat->installed, cat->rom_dir, CAT_SCAN_DEPTH);
}

static bool is_installed(const Catalog *cat, const CatEntry *e) {
    char target[CAT_FILE_LEN];
    cat_target_name(e->filename, target, sizeof(target));
    return cat_names_contains(&cat->installed, target);
}

// ---------------------------------------------------------------------------
// Drawing
// ---------------------------------------------------------------------------

static void draw(Catalog *cat) {
    CatalogView v;
    build_view(cat, &v);
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
            snprintf(ra_line, sizeof(ra_line), "Set not saved: use Update achievement sets");
        }
        dl.view.line_colors[2] = c;
    }
    finish(&dl, KIND_OK, "Installed");
}

// ---------------------------------------------------------------------------
// Screen
// ---------------------------------------------------------------------------

static void move_to(Catalog *cat, int index, bool wrap) {
    if (cat->total <= 0) return;
    if (wrap) {
        index = (index % cat->total + cat->total) % cat->total;
    } else {
        if (index < 0) index = 0;
        if (index >= cat->total) index = cat->total - 1;
    }
    cat->direction = (index > cat->selected) - (index < cat->selected);
    cat->selected = index;
    ensure_loaded(cat);
}

void catalog_screen(SyncState *state, bool has_wifi) {
    if (!has_wifi) {
        ui_message(TOOLBAR, "WiFi required", "Use Connect WiFi in the menu first.", KIND_ERROR, HINTS_ANY, 0);
        return;
    }

    Catalog *cat = calloc(1, sizeof(Catalog));
    CatEntry *win = cat ? malloc(CAT_WINDOW * sizeof(CatEntry)) : NULL;
    if (!win) {
        free(cat);
        ui_message(TOOLBAR, "Out of memory", "", KIND_ERROR, HINTS_ANY, 0);
        return;
    }
    cat->state = state;
    cat->win = win;
    cat->total = -1;
    snprintf(cat->base_url, sizeof(cat->base_url), "%s", state->server_url);
    size_t len = strlen(cat->base_url);
    while (len > 0 && cat->base_url[len - 1] == '/') cat->base_url[--len] = '\0';

    ui_task_begin(TOOLBAR, "Contacting server");
    if (!load_systems(cat)) {
        char text[128];
        snprintf(text, sizeof(text), "%s\n\nCheck WiFi and server_url.", cat->error);
        ui_message(TOOLBAR, "Can't open the catalog", text, KIND_ERROR, HINTS_ANY, 0);
        goto done;
    }
    if (cat->nsystems == 0) {
        ui_message(TOOLBAR, "No games", "The server has no DS games.", KIND_WARN, HINTS_ANY, 0);
        goto done;
    }

    ui_task_status("Looking for games on the SD", "");
    scan_installed(cat);
    // First page: draw the (empty) list so "Loading..." has a screen to sit on
    draw(cat);
    reset_list(cat);
    keysSetRepeat(20, 4);

    bool redraw = true;
    while (pmMainLoop()) {
        if (redraw) {
            draw(cat);
            redraw = false;
        }
        swiWaitForVBlank();
        scanKeys();
        int down = keysDown();
        int rep = keysDownRepeat();

        if (down & KEY_B) break;

        int before = cat->selected;
        if (rep & KEY_DOWN) move_to(cat, cat->selected + 1, true);
        if (rep & KEY_UP) move_to(cat, cat->selected - 1, true);
        if (rep & KEY_RIGHT) move_to(cat, cat->selected + CAT_ROWS, false);
        if (rep & KEY_LEFT) move_to(cat, cat->selected - CAT_ROWS, false);
        if (rep & KEY_R) move_to(cat, cat->selected + CAT_JUMP, false);
        if (rep & KEY_L) move_to(cat, cat->selected - CAT_JUMP, false);
        if (cat->selected != before) redraw = true;

        if (down & KEY_Y) {
            cat->ra_only = !cat->ra_only;
            reset_list(cat);
            redraw = true;
        }
        if (down & KEY_X) {
            char text[sizeof(cat->search)];
            snprintf(text, sizeof(text), "%s", cat->search);
            if (config_edit_field("Search game names (empty = all)", text, sizeof(text))) {
                snprintf(cat->search, sizeof(cat->search), "%s", text);
                draw(cat);
                reset_list(cat);
            }
            redraw = true;
        }
        if ((down & KEY_START) && cat->search[0]) {
            cat->search[0] = '\0';
            reset_list(cat);
            redraw = true;
        }
        if ((down & KEY_SELECT) && cat->nsystems > 1) {
            cat->sys = (cat->sys + 1) % cat->nsystems;
            scan_installed(cat);
            reset_list(cat);
            redraw = true;
        }
        if (down & KEY_A) {
            if (cat->error[0]) {
                // Retry after a failed page
                cat->error[0] = '\0';
                if (cat->total < 0) reset_list(cat);
                else fetch_window(cat, cat_window_start(cat->scroll, CAT_ROWS, CAT_WINDOW, cat->total, cat->direction));
            } else {
                const CatEntry *e = cat->total > 0 ? entry_at(cat, cat->selected) : NULL;
                if (e) install(cat, e);
            }
            redraw = true;
        }
    }

done:
    cat_names_free(&cat->installed);
    free(cat->win);
    free(cat);
}
