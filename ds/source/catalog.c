#include "catalog.h"
#include "catalog_data.h"
#include "config.h"
#include "http.h"
#include "ra.h"
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
#define CAT_WINDOW 128
#define CAT_ROWS 21          // list rows on the bottom screen (rows 2..22)
#define CAT_JUMP 100         // L/R
#define CAT_COLS 31          // printable width without triggering a wrap
#define CAT_SCAN_DEPTH 4     // ROM folder levels searched for installed games

// Systems the DS can run, in the order SELECT cycles through them, and the
// TWiLight Menu++ folder each installs to
static const char *const cat_systems[] = { "NDS", "DSI", NULL };
static const char *const cat_system_dirs[] = { "roms/nds", "roms/dsi" };

typedef struct {
    SyncState *state;
    PrintConsole *top, *bottom;
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

// ---------------------------------------------------------------------------
// Small helpers
// ---------------------------------------------------------------------------

static void wait_any_button(void) {
    iprintf("\nPress any button\n");
    while (pmMainLoop()) {
        swiWaitForVBlank();
        scanKeys();
        if (keysDown()) break;
    }
}

// Move the cursor (0-based row/column)
static void at(int row, int col) {
    iprintf("\x1b[%d;%dH", row, col);
}

// Print text cut or padded to exactly `width` columns
static void print_fixed(const char *text, int width) {
    char buf[64];
    if (width > (int)sizeof(buf) - 1) width = sizeof(buf) - 1;
    cat_ascii(text, buf, (size_t)width + 1);
    iprintf("%-*s", width, buf);
}

// Text over several rows of `width`; returns the rows used
static int print_wrapped(int row, const char *text, int max_rows) {
    char buf[CAT_NAME_LEN + CAT_FILE_LEN];
    cat_ascii(text, buf, sizeof(buf));
    int len = (int)strlen(buf), used = 0;
    for (int pos = 0; pos < len && used < max_rows; pos += CAT_COLS, used++) {
        at(row + used, 0);
        iprintf("%.*s", CAT_COLS, buf + pos);
    }
    return used ? used : 1;
}

static void format_time(unsigned seconds, char *out, size_t size) {
    if (seconds > 99 * 3600 + 3599) seconds = 99 * 3600 + 3599;
    if (seconds >= 3600)
        snprintf(out, size, "%u:%02u:%02u", seconds / 3600, (seconds / 60) % 60, seconds % 60);
    else
        snprintf(out, size, "%u:%02u", seconds / 60, seconds % 60);
}

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

static void show_loading(Catalog *cat) {
    consoleSelect(cat->bottom);
    at(1, 0);
    iprintf(CON_YELLOW);
    print_fixed("Loading...", CAT_COLS);
    iprintf(CON_RESET);
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

static void ra_tag(const CatEntry *e, char *out, size_t size) {
    if (!cat_entry_has_ra(e)) out[0] = '\0';
    else if (e->ra_title_only) snprintf(out, size, "RA?");
    else if (e->ra_achievements > 999) snprintf(out, size, "RA999");
    else snprintf(out, size, "RA%3d", e->ra_achievements);
}

static void draw_list(Catalog *cat) {
    consoleSelect(cat->bottom);
    consoleClear();

    // Row 0: system, filter, position
    at(0, 0);
    iprintf("%s ", cat->systems[cat->sys]);
    if (cat->ra_only) iprintf(CON_YELLOW "RA only" CON_RESET);
    else iprintf("All games");
    if (cat->total > 0) {
        char pos[24];
        snprintf(pos, sizeof(pos), "%d/%d", cat->selected + 1, cat->total);
        at(0, CAT_COLS - (int)strlen(pos));
        iprintf("%s", pos);
    }

    // Row 1: search or problem
    at(1, 0);
    if (cat->error[0]) {
        iprintf(CON_RED);
        print_fixed(cat->error, CAT_COLS);
        iprintf(CON_RESET);
    } else if (cat->filter_ignored) {
        iprintf(CON_RED);
        print_fixed("Server can't filter RA: update", CAT_COLS);
        iprintf(CON_RESET);
    } else if (cat->search[0]) {
        char line[64];
        snprintf(line, sizeof(line), "Search: %s", cat->search);
        iprintf(CON_CYAN);
        print_fixed(line, CAT_COLS);
        iprintf(CON_RESET);
    }

    if (cat->error[0] && cat->win_count == 0) {
        at(3, 0);
        iprintf("A: try again  B: back");
        return;
    }

    if (cat->total == 0 && !cat->error[0]) {
        at(3, 0);
        if (cat->search[0]) iprintf("No games match the search.\nX: new search  START: clear");
        else if (cat->ra_only) iprintf("No games with achievements.\nY: show all games");
        else iprintf("No games on the server.");
        return;
    }

    for (int r = 0; r < CAT_ROWS; r++) {
        int index = cat->scroll + r;
        if (cat->total >= 0 && index >= cat->total) break;
        const CatEntry *e = entry_at(cat, index);
        at(2 + r, 0);
        if (!e) {
            iprintf("  ...");
            continue;
        }
        bool sel = (index == cat->selected);
        bool inst = is_installed(cat, e);
        char tag[8];
        ra_tag(e, tag, sizeof(tag));

        iprintf("%c", sel ? '>' : ' ');
        iprintf(inst ? CON_GREEN "*" : " ");
        iprintf(sel ? CON_CYAN : (inst ? CON_GREEN : CON_RESET));
        print_fixed(e->name[0] ? e->name : e->filename, 23);
        iprintf(CON_YELLOW " %5s" CON_RESET, tag);
    }
}

static void draw_details(Catalog *cat) {
    consoleSelect(cat->top);
    consoleClear();
    at(0, 0);
    iprintf("======== Game Catalog ========");

    const CatEntry *e = (cat->total > 0) ? entry_at(cat, cat->selected) : NULL;
    int row = 2;
    if (e) {
        iprintf(CON_CYAN);
        row += print_wrapped(row, e->name[0] ? e->name : e->filename, 4);
        iprintf(CON_RESET);
        row++;

        char size[16];
        cat_format_size(e->size, size, sizeof(size));
        const char *ext = strrchr(e->filename, '.');
        at(row++, 0);
        iprintf("Size: %s%s", size, (ext && strcasecmp(ext, ".zip") == 0) ? " (zipped)" : "");

        at(row++, 0);
        if (cat_entry_has_ra(e) && e->ra_title_only)
            iprintf(CON_YELLOW "RA: %d achievements?" CON_RESET, e->ra_achievements);
        else if (cat_entry_has_ra(e))
            iprintf(CON_YELLOW "RA: %d achievements" CON_RESET, e->ra_achievements);
        else if (e->ra_game_id)
            iprintf("RA: known, no achievements");
        else
            iprintf("RA: none");
        if (cat_entry_has_ra(e) && e->ra_title_only) {
            at(row++, 0);
            iprintf(" (matched by name only)");
        }

        at(row++, 0);
        if (is_installed(cat, e)) iprintf(CON_GREEN "On SD: yes" CON_RESET);
        else iprintf("On SD: no");
    }

    at(13, 0);
    iprintf("To: %.27s", cat->rom_dir);
    at(15, 0);
    iprintf("A:Install        B:Back");
    at(16, 0);
    iprintf("Y:%s", cat->ra_only ? "Show all games" : "Only games with RA");
    at(17, 0);
    iprintf("X:Search         START:Clear");
    at(18, 0);
    iprintf("Up/Dn:Move  Lt/Rt:Page");
    at(19, 0);
    iprintf("L/R:Jump %d", CAT_JUMP);
    if (cat->nsystems > 1) {
        at(20, 0);
        iprintf("SELECT:System (%s)", cat->systems[cat->sys]);
    }
    at(22, 0);
    iprintf(CON_GREEN "*" CON_RESET " on SD  " CON_YELLOW "RA" CON_RESET " achievements");
}

static void draw(Catalog *cat) {
    draw_details(cat);
    draw_list(cat);
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
} Download;

static uint32_t bytes_per_second(uint32_t bytes, uint64_t ticks) {
    if (ticks == 0) return 0;
    return (uint32_t)((uint64_t)bytes * TICK_FREQ / ticks);
}

static void draw_progress(Download *dl, uint32_t done, uint32_t total) {
    uint64_t now = tickGetCount();
    unsigned elapsed = (unsigned)((now - dl->start) / TICK_FREQ);
    uint32_t avg = bytes_per_second(done, now - dl->start);
    char buf[48], a[16], b[16], t1[16], t2[16];

    cat_format_size(done, a, sizeof(a));
    at(4, 0);
    if (total) {
        cat_format_size(total, b, sizeof(b));
        snprintf(buf, sizeof(buf), "%s / %s  %lu%%", a, b,
                 (unsigned long)((uint64_t)done * 100 / total));
    } else {
        snprintf(buf, sizeof(buf), "%s", a);
    }
    print_fixed(buf, CAT_COLS);

    at(5, 0);
    char bar[CAT_COLS + 1];
    int width = CAT_COLS - 2;
    int filled = total ? (int)((uint64_t)done * width / total) : 0;
    for (int i = 0; i < width; i++) bar[i] = i < filled ? '#' : '.';
    bar[width] = '\0';
    iprintf("[%s]", bar);

    at(7, 0);
    snprintf(buf, sizeof(buf), "Speed: %lu KB/s (avg %lu)",
             (unsigned long)((dl->speed ? dl->speed : avg) / 1024), (unsigned long)(avg / 1024));
    print_fixed(buf, CAT_COLS);

    at(8, 0);
    format_time(elapsed, t1, sizeof(t1));
    if (total && avg > 0 && done < total) {
        format_time((unsigned)((total - done) / avg), t2, sizeof(t2));
        snprintf(buf, sizeof(buf), "Time: %s  Left: %s", t1, t2);
    } else {
        snprintf(buf, sizeof(buf), "Time: %s", t1);
    }
    print_fixed(buf, CAT_COLS);
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
    consoleSelect(cat->bottom);
    consoleClear();
    at(0, 0);
    iprintf("=== Install ===");
    int row = 2;
    iprintf(CON_CYAN);
    row += print_wrapped(row, e->name[0] ? e->name : e->filename, 4);
    iprintf(CON_RESET);
    row++;
    char size[16];
    cat_format_size(e->size, size, sizeof(size));
    at(row++, 0);
    iprintf("Server file: %s", size);
    at(row++, 0);
    iprintf("To %.28s/", cat->rom_dir);
    row += print_wrapped(row, target, 3);
    if (cat_entry_has_ra(e)) {
        at(++row, 0);
        iprintf(CON_YELLOW "Achievement set is saved too" CON_RESET);
    }
    if (exists) {
        at(++row, 0);
        iprintf(CON_RED "Already on the SD: replace it?" CON_RESET);
    }
    at(21, 0);
    iprintf("A:Install  B:Cancel");

    while (pmMainLoop()) {
        swiWaitForVBlank();
        scanKeys();
        int k = keysDown();
        if (k & KEY_A) return true;
        if (k & KEY_B) return false;
    }
    return false;
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

    consoleSelect(cat->bottom);
    if (e.truncated) {
        consoleClear();
        iprintf(CON_RED "Name too long to download" CON_RESET "\n");
        wait_any_button();
        return;
    }
    if (!raw && !(zipped && e.can_extract_nds)) {
        consoleClear();
        iprintf(CON_RED "Can't install this file" CON_RESET "\n\n%.60s\n\n", e.filename);
        if (zipped) iprintf("The server can't unzip DS ROMs\nyet: update the server.\n");
        else iprintf("Only .nds or single-ROM .zip\nfiles can be installed.\n");
        wait_any_button();
        return;
    }

    struct stat st;
    bool exists = (stat(path, &st) == 0);
    if (!confirm_install(cat, &e, target, exists)) return;

    consoleClear();
    at(0, 0);
    iprintf("=== Installing ===");
    at(1, 0);
    print_fixed(e.name[0] ? e.name : e.filename, CAT_COLS);
    at(10, 0);
    iprintf("Hold B to cancel");
    at(4, 0);
    iprintf("Connecting...");

    mkdir_parents(cat->rom_dir);
    remove(part);
    FILE *f = fopen(part, "wb");
    if (!f) {
        at(12, 0);
        iprintf(CON_RED "Can't create the file on SD" CON_RESET "\n%.60s\n", part);
        wait_any_button();
        return;
    }

    char id[CAT_ID_LEN * 3];
    cat_url_encode(e.rom_id, id, sizeof(id));
    char url[CAT_ID_LEN * 3 + 320];
    snprintf(url, sizeof(url), "%s/api/v1/roms/%s%s", cat->base_url, id, zipped ? "?extract=nds" : "");

    Download dl = { .f = f, .dir = cat->rom_dir, .replaced = exists ? (uint32_t)st.st_size : 0 };
    dl.start = dl.last_draw = dl.sample_tick = tickGetCount();

    // Longer timeout: the server may take a moment to open a big zip
    http_set_timeout(60);
    HttpDownloadInfo info;
    HttpDownloadResult rc = http_download(url, state->api_key, download_sink, &dl, &info);
    http_set_timeout(0);

    bool closed_ok = (fclose(f) == 0);
    uint64_t ticks = tickGetCount() - dl.start;
    if (rc == HTTP_DL_OK && !closed_ok) rc = HTTP_DL_WRITE;

    at(12, 0);
    if (rc != HTTP_DL_OK) {
        remove(part);
        char a[16], b[16];
        switch (rc) {
            case HTTP_DL_CANCELLED:
                iprintf("Cancelled\n");
                break;
            case HTTP_DL_CONNECT:
                iprintf(CON_RED "No response from server" CON_RESET "\n");
                break;
            case HTTP_DL_STATUS: {
                iprintf(CON_RED "Server error (HTTP %d)" CON_RESET "\n", info.status_code);
                char msg[96];
                cat_ascii(info.error, msg, sizeof(msg));
                iprintf("%.90s\n", msg);
                break;
            }
            case HTTP_DL_WRITE:
                if (dl.bad_data) iprintf(CON_RED "Server sent a zip, not a ROM:" CON_RESET "\nupdate the server\n");
                else if (dl.no_space) iprintf(CON_RED "Not enough space on the SD" CON_RESET "\n");
                else iprintf(CON_RED "SD write failed (card full?)" CON_RESET "\n");
                break;
            case HTTP_DL_SHORT:
                cat_format_size(info.received, a, sizeof(a));
                cat_format_size(info.total, b, sizeof(b));
                iprintf(CON_RED "Connection lost" CON_RESET "\nat %s of %s\n", a, b);
                break;
            default:
                iprintf(CON_RED "Download failed" CON_RESET "\n");
                break;
        }
        wait_any_button();
        return;
    }

    if (exists) remove(path);
    if (rename(part, path) != 0) {
        remove(part);
        iprintf(CON_RED "Couldn't rename the download" CON_RESET "\n");
        wait_any_button();
        return;
    }
    cat_names_add(&cat->installed, target);
    cat_names_sort(&cat->installed);

    char size[16], secs[16];
    cat_format_size(info.received, size, sizeof(size));
    format_time((unsigned)(ticks / TICK_FREQ), secs, sizeof(secs));
    iprintf(CON_GREEN "Installed" CON_RESET " %s in %s\n", size, secs);
    iprintf("Average: %lu KB/s\n", (unsigned long)(bytes_per_second(info.received, ticks) / 1024));

    if (cat_entry_has_ra(&e)) {
        iprintf("\nGetting achievement set...\n");
        int count = 0;
        int r = ra_install_set(state, path, &count);
        if (r == RA_SET_OK)
            iprintf(CON_YELLOW "%d achievements ready" CON_RESET "\n", count);
        else if (r == RA_SET_UNKNOWN)
            iprintf("RA doesn't know this dump\n");
        else if (r == RA_SET_NOT_DS_ROM)
            iprintf(CON_RED "Not a DS ROM?" CON_RESET "\n");
        else
            iprintf(CON_RED "Set not saved; use Update\nachievement sets later" CON_RESET "\n");
    }
    wait_any_button();
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

static void message(PrintConsole *console, const char *title, const char *text) {
    consoleSelect(console);
    consoleClear();
    iprintf("=== %s ===\n\n%s\n", title, text);
    wait_any_button();
}

void catalog_screen(SyncState *state, bool has_wifi, PrintConsole *top, PrintConsole *bottom) {
    // Fresh consoles so no colour or cursor state carries over
    consoleInit(top, 3, BgType_Text4bpp, BgSize_T_256x256, 31, 0, true, true);
    consoleInit(bottom, 3, BgType_Text4bpp, BgSize_T_256x256, 31, 0, false, true);
    consoleSelect(top);
    consoleClear();
    iprintf("=== Game Catalog ===\n");

    if (!has_wifi) {
        message(bottom, "Game Catalog", "WiFi required.\nUse Connect WiFi in the\nconfig menu first.");
        return;
    }

    Catalog *cat = calloc(1, sizeof(Catalog));
    CatEntry *win = cat ? malloc(CAT_WINDOW * sizeof(CatEntry)) : NULL;
    if (!win) {
        free(cat);
        message(bottom, "Game Catalog", "Out of memory");
        return;
    }
    cat->state = state;
    cat->top = top;
    cat->bottom = bottom;
    cat->win = win;
    cat->total = -1;
    snprintf(cat->base_url, sizeof(cat->base_url), "%s", state->server_url);
    size_t len = strlen(cat->base_url);
    while (len > 0 && cat->base_url[len - 1] == '/') cat->base_url[--len] = '\0';

    consoleSelect(bottom);
    consoleClear();
    iprintf("Contacting server...\n");
    if (!load_systems(cat)) {
        char text[128];
        snprintf(text, sizeof(text), "%s\n\nCheck WiFi and server_url.", cat->error);
        message(bottom, "Game Catalog", text);
        goto done;
    }
    if (cat->nsystems == 0) {
        message(bottom, "Game Catalog", "The server has no DS games.");
        goto done;
    }

    iprintf("Looking for games on the SD...\n");
    scan_installed(cat);
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
            consoleSelect(bottom);
            char text[sizeof(cat->search)];
            snprintf(text, sizeof(text), "%s", cat->search);
            if (config_edit_field("Search game names (empty = all)", text, sizeof(text))) {
                snprintf(cat->search, sizeof(cat->search), "%s", text);
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
                // Consoles were used for other screens; start clean
                consoleInit(top, 3, BgType_Text4bpp, BgSize_T_256x256, 31, 0, true, true);
                consoleInit(bottom, 3, BgType_Text4bpp, BgSize_T_256x256, 31, 0, false, true);
            }
            redraw = true;
        }
    }

done:
    cat_names_free(&cat->installed);
    free(cat->win);
    free(cat);
}
