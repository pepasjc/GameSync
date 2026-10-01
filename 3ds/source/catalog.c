// Game catalog screen for the 3DS client. The browsing UX (server-side
// paging window, RA badges and filter, search, progress with speed/ETA,
// hold-B cancel) is ported from the DS client's ds/source/catalog.c.
//
// 3DS games install as a CIA streamed straight into AM (the same path the
// self-updater uses), chunk by chunk from the HTTP body, so a ROM is never
// held in RAM or staged on the SD. The server converts .3ds/.cci carts with
// ?extract=cia (operator-configured, see server/README.md); a .cia on the
// server is installed as-is. DS/DSi games are written to the SD as .nds.
#include "catalog.h"
#include "catalog_data.h"
#include "network.h"
#include "ui.h"
#include <stdarg.h>
#include <strings.h>
#include <sys/stat.h>

#define TOP_W 50
#define BOT_W 40
#define SCREEN_H 30

#define CAT_WINDOW 200       // entries held around the visible rows
#define CAT_ROWS 26          // list rows on the top screen (rows 3..28)
#define CAT_LIST_ROW 3
#define CAT_JUMP 100         // L/R
#define CAT_NAME_W 42        // list name column
#define CAT_SCAN_DEPTH 4     // ROM folder levels searched for installed games

#define CIA_MAP_PATH "sdmc:/3ds/3dssync/catalog_cia.txt"
#define ROMS_3DS_DIR "sdmc:/roms/3ds"
#define ROMS_DSI_DIR "sdmc:/roms/dsi"
#define ROMS_NDS_DIR "sdmc:/roms/nds"

#define WAIT_CONVERT_S (45 * 60)   // a CIA conversion of a big cart is slow
#define WAIT_PLAIN_S 60

static const char *const cat_systems[] = { "3DS", "NDS", "DSI", NULL };
static const char *const nds_exts[] = { ".nds", ".dsi", NULL };
static const char *const cart_exts[] = { ".3ds", ".cci", NULL };

typedef struct {
    const AppConfig *config;
    PrintConsole *top, *bottom;

    char systems[3][8];
    int system_counts[3];
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

    char rom_dir[MAX_PATH_LEN];  // where files of this system go
    CatNameSet files;            // ROM files already in rom_dir
    u64 *titles;                 // installed SD titles (sorted)
    u32 title_count;
    CatCiaMap cia_map;           // rom_id -> title id of catalog installs

    bool nds_installed;          // a DS game landed in the save scan folder
} Catalog;

// ---------------------------------------------------------------------------
// Drawing helpers
// ---------------------------------------------------------------------------

static void present(void) {
    gfxFlushBuffers();
    gfxSwapBuffers();
    gspWaitForVBlank();
}

static void at(int row, int col) {
    printf("\x1b[%d;%dH", row, col);
}

// Print text cut or padded to exactly `width` columns
static void print_fixed(const char *text, int width) {
    char buf[96];
    if (width > (int)sizeof(buf) - 1) width = sizeof(buf) - 1;
    cat_ascii(text, buf, (size_t)width + 1);
    printf("%-*s", width, buf);
}

// One full-width line at `row` (1-based), optionally coloured
static void pline(int row, int width, const char *color, const char *fmt, ...) {
    char buf[256];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(buf, sizeof(buf), fmt, ap);
    va_end(ap);
    at(row, 1);
    if (color) printf("%s", color);
    print_fixed(buf, width);
    if (color) printf("\x1b[0m");
}

// Text over up to `max_rows` rows of `width`, every row padded; returns
// the rows that hold text (at least 1)
static int print_wrapped(int row, int width, const char *color, const char *text, int max_rows) {
    char buf[CAT_NAME_LEN + CAT_FILE_LEN];
    cat_ascii(text, buf, sizeof(buf));
    int len = (int)strlen(buf), used = 0;
    for (int r = 0; r < max_rows; r++) {
        int pos = r * width;
        char part[96];
        if (pos < len) {
            snprintf(part, sizeof(part), "%.*s", width, buf + pos);
            used = r + 1;
        } else {
            part[0] = '\0';
        }
        pline(row + r, width, color, "%s", part);
    }
    return used ? used : 1;
}

static void wait_button(void) {
    printf("\n\x1b[90mPress any button\x1b[0m");
    present();
    while (aptMainLoop()) {
        hidScanInput();
        if (hidKeysDown()) break;
        gspWaitForVBlank();
    }
}

static void format_time(unsigned seconds, char *out, size_t size) {
    if (seconds > 99 * 3600 + 3599) seconds = 99 * 3600 + 3599;
    if (seconds >= 3600)
        snprintf(out, size, "%u:%02u:%02u", seconds / 3600, (seconds / 60) % 60, seconds % 60);
    else
        snprintf(out, size, "%u:%02u", seconds / 60, seconds % 60);
}

static void mkdir_parents(const char *path) {
    char buf[MAX_PATH_LEN];
    snprintf(buf, sizeof(buf), "%s", path);
    for (char *p = strchr(buf, '/'); p; p = strchr(p + 1, '/')) {
        if (p > buf && p[-1] == ':') continue;  // "sdmc:/"
        *p = '\0';
        mkdir(buf, 0777);
        *p = '/';
    }
    mkdir(buf, 0777);
}

static u64 sd_free_bytes(void) {
    FS_ArchiveResource res;
    if (R_FAILED(FSUSER_GetArchiveResource(&res, SYSTEM_MEDIATYPE_SD))) return 0;
    return (u64)res.freeClusters * res.clusterSize;
}

// ---------------------------------------------------------------------------
// Server list
// ---------------------------------------------------------------------------

// GET with one retry when the server didn't answer at all
static u8 *cat_get(const Catalog *cat, const char *path, u32 *size, u32 *status) {
    *status = 0;
    u8 *body = network_get(cat->config, path, size, status);
    if (!body && *status == 0) {
        svcSleepThread(1000000000LL);
        body = network_get(cat->config, path, size, status);
    }
    return body;
}

static void describe_failure(u32 status, char *out, size_t size) {
    if (status == 0)
        snprintf(out, size, "No response from server");
    else if (status == 401 || status == 403)
        snprintf(out, size, "API key rejected (HTTP %lu)", (unsigned long)status);
    else
        snprintf(out, size, "Server error (HTTP %lu)", (unsigned long)status);
}

static bool load_systems(Catalog *cat) {
    u32 size = 0, status = 0;
    u8 *body = cat_get(cat, "/roms/systems", &size, &status);
    bool ok = false;
    if (body && status == 200) {
        int n = cat_parse_systems((const char *)body, size, cat_systems,
                                  cat->systems, cat->system_counts, 3);
        if (n < 0) {
            snprintf(cat->error, sizeof(cat->error), "Bad reply from server");
        } else {
            cat->nsystems = n;
            ok = true;
        }
    } else {
        describe_failure(body ? status : 0, cat->error, sizeof(cat->error));
    }
    free(body);
    return ok;
}

static void show_loading(Catalog *cat) {
    consoleSelect(cat->top);
    pline(SCREEN_H, TOP_W, "\x1b[33m", " Loading...");
    present();
}

// Load the page starting at `offset` into the window
static void fetch_window(Catalog *cat, int offset) {
    char search[160] = "";
    if (cat->search[0]) {
        char enc[144];
        cat_url_encode(cat->search, enc, sizeof(enc));
        snprintf(search, sizeof(search), "&search=%s", enc);
    }
    char path[320];
    snprintf(path, sizeof(path), "/roms?system=%s&limit=%d&offset=%d%s%s",
             cat->systems[cat->sys], CAT_WINDOW, offset,
             cat->ra_only ? "&has_ra=true" : "", search);

    show_loading(cat);
    u32 size = 0, status = 0;
    u8 *body = cat_get(cat, path, &size, &status);
    cat->error[0] = '\0';
    if (body && status == 200) {
        CatPageInfo info;
        int n = cat_parse_page((const char *)body, size, cat->win, CAT_WINDOW, &info);
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
        describe_failure(body ? status : 0, cat->error, sizeof(cat->error));
        cat->win_count = 0;
    }
    free(body);
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

// ---------------------------------------------------------------------------
// What's installed
// ---------------------------------------------------------------------------

static int u64_compare(const void *a, const void *b) {
    u64 x = *(const u64 *)a, y = *(const u64 *)b;
    return (x > y) - (x < y);
}

static void load_installed_titles(Catalog *cat) {
    free(cat->titles);
    cat->titles = NULL;
    cat->title_count = 0;
    u32 count = 0;
    if (R_FAILED(AM_GetTitleCount(MEDIATYPE_SD, &count)) || count == 0) return;
    cat->titles = malloc(count * sizeof(u64));
    if (!cat->titles) return;
    u32 read = 0;
    if (R_FAILED(AM_GetTitleList(&read, MEDIATYPE_SD, count, cat->titles))) read = 0;
    cat->title_count = read;
    qsort(cat->titles, read, sizeof(u64), u64_compare);
}

static bool title_installed(const Catalog *cat, u64 tid) {
    if (!tid || !cat->title_count) return false;
    return bsearch(&tid, cat->titles, cat->title_count, sizeof(u64), u64_compare) != NULL;
}

static bool system_is_3ds(const Catalog *cat) {
    return strcasecmp(cat->systems[cat->sys], "3DS") == 0;
}

static void set_rom_dir(Catalog *cat) {
    const char *sys = cat->systems[cat->sys];
    if (strcasecmp(sys, "3DS") == 0)
        snprintf(cat->rom_dir, sizeof(cat->rom_dir), "%s", ROMS_3DS_DIR);
    else if (strcasecmp(sys, "DSI") == 0)
        snprintf(cat->rom_dir, sizeof(cat->rom_dir), "%s", ROMS_DSI_DIR);
    else if (cat->config->nds_dir[0])
        snprintf(cat->rom_dir, sizeof(cat->rom_dir), "%s", cat->config->nds_dir);
    else
        snprintf(cat->rom_dir, sizeof(cat->rom_dir), "%s", ROMS_NDS_DIR);
    // No trailing slash
    size_t len = strlen(cat->rom_dir);
    while (len > 1 && cat->rom_dir[len - 1] == '/') cat->rom_dir[--len] = '\0';
}

static void scan_installed(Catalog *cat) {
    set_rom_dir(cat);
    cat_names_free(&cat->files);
    cat_names_scan(&cat->files, cat->rom_dir, CAT_SCAN_DEPTH,
                   system_is_3ds(cat) ? cart_exts : nds_exts);
}

// Title id this entry is installed as, 0 if not installed / unknown
static u64 installed_tid(const Catalog *cat, const CatEntry *e) {
    u64 tid = cat_cia_map_get(&cat->cia_map, e->rom_id);
    if (title_installed(cat, tid)) return tid;
    if (cat_parse_tid(e->title_id, &tid) && title_installed(cat, tid)) return tid;
    return 0;
}

static bool file_on_sd(const Catalog *cat, const CatEntry *e) {
    char target[CAT_FILE_LEN];
    cat_target_name(e->filename, system_is_3ds(cat) ? ".3ds" : ".nds", target, sizeof(target));
    return cat_names_contains(&cat->files, target);
}

static bool is_installed(const Catalog *cat, const CatEntry *e) {
    if (system_is_3ds(cat) && installed_tid(cat, e)) return true;
    return file_on_sd(cat, e);
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

static const char *plan_label(const CatPlan *plan) {
    switch (plan->kind) {
        case CAT_PLAN_CIA:         return "Install: CIA to the HOME Menu";
        case CAT_PLAN_CIA_CONVERT: return "Install: CIA (server converts)";
        case CAT_PLAN_3DS_FILE:    return "Save: .3ds file (no CIA convert)";
        case CAT_PLAN_NDS:         return "Install: .nds file";
        case CAT_PLAN_NDS_EXTRACT: return "Install: .nds (server unzips)";
        default:                   return "Can't be installed here";
    }
}

static void draw_list(Catalog *cat) {
    consoleSelect(cat->top);

    // Row 1: system, filter, position
    char head[64], pos[24] = "";
    snprintf(head, sizeof(head), "--- Game Catalog: %s %s---",
             cat->systems[cat->sys], cat->ra_only ? "[RA only] " : "");
    if (cat->total > 0) snprintf(pos, sizeof(pos), "%d/%d", cat->selected + 1, cat->total);
    at(1, 1);
    printf("\x1b[36m");
    print_fixed(head, TOP_W - (int)strlen(pos));
    printf("\x1b[0m%s", pos);

    // Row 2: problem or search
    if (cat->error[0])
        pline(2, TOP_W, "\x1b[31m", "%s", cat->error);
    else if (cat->filter_ignored)
        pline(2, TOP_W, "\x1b[31m", "Server can't filter by RA: update the server");
    else if (cat->search[0])
        pline(2, TOP_W, "\x1b[36m", "Search: %s", cat->search);
    else
        pline(2, TOP_W, NULL, "");

    int row = CAT_LIST_ROW;
    if (cat->error[0] && cat->win_count == 0) {
        pline(row++, TOP_W, NULL, "  A: try again   B: back");
    } else if (cat->total == 0 && !cat->error[0]) {
        if (cat->search[0]) {
            pline(row++, TOP_W, NULL, "  No games match the search.");
            pline(row++, TOP_W, NULL, "  X: new search   START: clear search");
        } else if (cat->ra_only) {
            pline(row++, TOP_W, NULL, "  No games with achievements.");
            pline(row++, TOP_W, NULL, "  Y: show all games");
        } else {
            pline(row++, TOP_W, NULL, "  No games for this system on the server.");
        }
    } else {
        for (int r = 0; r < CAT_ROWS; r++, row++) {
            int index = cat->scroll + r;
            if (cat->total >= 0 && index >= cat->total) break;
            const CatEntry *e = entry_at(cat, index);
            if (!e) {
                pline(row, TOP_W, "\x1b[90m", "  ...");
                continue;
            }
            bool sel = (index == cat->selected);
            bool inst = is_installed(cat, e);
            char tag[8];
            ra_tag(e, tag, sizeof(tag));

            at(row, 1);
            printf("%c", sel ? '>' : ' ');
            fputs(inst ? "\x1b[32m*" : " ", stdout);
            fputs(sel ? "\x1b[33m" : (inst ? "\x1b[32m" : "\x1b[0m"), stdout);
            print_fixed(e->name[0] ? e->name : e->filename, CAT_NAME_W);
            printf("\x1b[0m \x1b[33m%5s\x1b[0m", tag);
        }
    }
    for (; row <= CAT_LIST_ROW + CAT_ROWS; row++) pline(row, TOP_W, NULL, "");

    at(SCREEN_H, 1);
    printf("\x1b[90m \x1b[32m*\x1b[90m installed   \x1b[33mRA\x1b[90m achievements");
    printf("%-*s\x1b[0m", TOP_W - 31, "");
}

static void draw_details(Catalog *cat) {
    consoleSelect(cat->bottom);
    const CatEntry *e = (cat->total > 0) ? entry_at(cat, cat->selected) : NULL;
    int row = 1;

    if (e) {
        print_wrapped(row, BOT_W, "\x1b[36m", e->name[0] ? e->name : e->filename, 3);
        row += 3;
        pline(row++, BOT_W, NULL, "");

        char size[16];
        cat_format_size(e->size, size, sizeof(size));
        const char *ext = strrchr(e->filename, '.');
        pline(row++, BOT_W, NULL, "Size: %s%s", size,
              (ext && strcasecmp(ext, ".zip") == 0) ? " (zipped)" : "");
        pline(row++, BOT_W, "\x1b[90m", "File: %s", e->filename);

        CatPlan plan = cat_plan_install(cat->systems[cat->sys], e);
        pline(row++, BOT_W, plan.kind == CAT_PLAN_NONE ? "\x1b[31m" : NULL, "%s", plan_label(&plan));

        if (cat_entry_has_ra(e) && e->ra_title_only)
            pline(row++, BOT_W, "\x1b[33m", "RA: %d achievements? (name match)", e->ra_achievements);
        else if (cat_entry_has_ra(e))
            pline(row++, BOT_W, "\x1b[33m", "RA: %d achievements", e->ra_achievements);
        else if (e->ra_game_id)
            pline(row++, BOT_W, NULL, "RA: known, no achievements");
        else
            pline(row++, BOT_W, NULL, "RA: none");

        u64 tid = system_is_3ds(cat) ? installed_tid(cat, e) : 0;
        if (tid)
            pline(row++, BOT_W, "\x1b[32m", "Installed: %016llX", (unsigned long long)tid);
        else if (file_on_sd(cat, e))
            pline(row++, BOT_W, "\x1b[32m", "On SD: yes");
        else
            pline(row++, BOT_W, NULL, "%s", system_is_3ds(cat) ? "Installed: no" : "On SD: no");
    }
    while (row <= 12) pline(row++, BOT_W, NULL, "");

    if (system_is_3ds(cat))
        pline(row++, BOT_W, "\x1b[90m", "To: HOME Menu (CIA on SD)");
    else
        pline(row++, BOT_W, "\x1b[90m", "To: %s", cat->rom_dir);
    while (row <= 19) pline(row++, BOT_W, NULL, "");

    pline(row++, BOT_W, NULL, "A: Install            B: Back");
    pline(row++, BOT_W, NULL, "Y: %s", cat->ra_only ? "Show all games" : "Only games with RA");
    pline(row++, BOT_W, NULL, "X: Search             START: Clear");
    pline(row++, BOT_W, NULL, "Up/Dn: Move   Left/Right: Page");
    pline(row++, BOT_W, NULL, "L/R: Jump %d", CAT_JUMP);
    if (cat->nsystems > 1)
        pline(row++, BOT_W, NULL, "SELECT: System (%s)", cat->systems[cat->sys]);
    while (row <= SCREEN_H) pline(row++, BOT_W, NULL, "");
}

static void draw(Catalog *cat) {
    draw_list(cat);
    draw_details(cat);
    present();
}

// ---------------------------------------------------------------------------
// Install
// ---------------------------------------------------------------------------

typedef struct {
    Catalog *cat;
    bool cia;                // stream into AM rather than a file
    FILE *f;
    Handle handle;           // AM CIA install handle
    bool am_started;
    Result am_result;
    u64 offset;              // bytes written into the CIA handle

    u8 *probe;               // start of the CIA, for its title id
    u32 probe_len;
    int probe_state;         // 0 need more, 1 found, -1 not a CIA
    u64 tid;

    u64 replaced;            // size of the file being replaced
    u64 start_ms, last_draw_ms, sample_ms;
    u64 sample_bytes;
    u32 speed;               // bytes/s over the last ~second
    bool checked;
    bool no_space, bad_data;
} Download;

static u32 bytes_per_second(u64 bytes, u64 ms) {
    if (ms == 0) return 0;
    return (u32)(bytes * 1000 / ms);
}

static void draw_progress(Download *dl, u64 done, u64 total) {
    u64 now = osGetTime();
    unsigned elapsed = (unsigned)((now - dl->start_ms) / 1000);
    u32 avg = bytes_per_second(done, now - dl->start_ms);
    char a[16], b[16], t1[16], t2[16];

    consoleSelect(dl->cat->bottom);
    cat_format_size(done, a, sizeof(a));
    if (total) {
        cat_format_size(total, b, sizeof(b));
        pline(4, BOT_W, NULL, "%s / %s  %lu%%", a, b, (unsigned long)(done * 100 / total));
    } else {
        pline(4, BOT_W, NULL, "%s", a);
    }

    char bar[BOT_W + 1];
    int width = BOT_W - 2;
    int filled = total ? (int)(done * (u64)width / total) : 0;
    for (int i = 0; i < width; i++) bar[i] = i < filled ? '#' : '.';
    bar[width] = '\0';
    pline(5, BOT_W, NULL, "[%s]", bar);

    pline(7, BOT_W, NULL, "Speed: %lu KB/s (avg %lu)",
          (unsigned long)((dl->speed ? dl->speed : avg) / 1024), (unsigned long)(avg / 1024));
    format_time(elapsed, t1, sizeof(t1));
    if (total && avg > 0 && done < total) {
        format_time((unsigned)((total - done) / avg), t2, sizeof(t2));
        pline(8, BOT_W, NULL, "Time: %s  Left: %s", t1, t2);
    } else {
        pline(8, BOT_W, NULL, "Time: %s", t1);
    }
    present();
}

// Read the keys; true if the user wants out (B held, or the app closing)
static bool cancel_requested(void) {
    if (!aptMainLoop()) return true;
    hidScanInput();
    return (hidKeysHeld() & KEY_B) != 0;
}

static bool download_wait(u32 seconds, void *user) {
    Download *dl = user;
    char t[16];
    format_time(seconds, t, sizeof(t));
    consoleSelect(dl->cat->bottom);
    if (dl->cia)
        pline(4, BOT_W, "\x1b[33m", "Server is converting to CIA... %s", t);
    else
        pline(4, BOT_W, "\x1b[33m", "Waiting for the server... %s", t);
    if (dl->cia && seconds == 3)
        pline(5, BOT_W, "\x1b[90m", "(big games take a few minutes)");
    present();
    return cancel_requested();
}

static int download_sink(const u8 *data, u32 size, u64 done, u64 total, void *user) {
    Download *dl = user;

    if (!dl->checked) {
        dl->checked = true;
        dl->start_ms = dl->last_draw_ms = dl->sample_ms = osGetTime();
        pline(5, BOT_W, NULL, "");
        // An old server may ignore ?extract=nds and send the zip itself
        if (!dl->cia && size >= 4 && memcmp(data, "PK\x03\x04", 4) == 0) {
            dl->bad_data = true;
            return -1;
        }
        u64 free_bytes = sd_free_bytes();
        // 0 free is more likely a failed query than a full card; a really
        // full card still fails on the first write
        if (total && free_bytes > 0 && free_bytes + dl->replaced < total + 1024 * 1024) {
            dl->no_space = true;
            return -1;
        }
    }

    if (dl->cia) {
        // The title id sits in the TMD, a few KB in
        if (dl->probe_state == 0 && dl->probe) {
            u32 take = CAT_CIA_PROBE_MAX - dl->probe_len;
            if (take > size) take = size;
            memcpy(dl->probe + dl->probe_len, data, take);
            dl->probe_len += take;
            dl->probe_state = cat_cia_title_id(dl->probe, dl->probe_len, &dl->tid);
            if (dl->probe_state == 0 && dl->probe_len >= CAT_CIA_PROBE_MAX) dl->probe_state = -1;
            if (dl->probe_state < 0) {
                dl->bad_data = true;
                return -1;
            }
        }
        if (!dl->am_started) {
            dl->am_result = AM_StartCiaInstall(MEDIATYPE_SD, &dl->handle);
            if (R_FAILED(dl->am_result)) return -1;
            dl->am_started = true;
        }
        u32 written = 0;
        dl->am_result = FSFILE_Write(dl->handle, &written, dl->offset, data, size, 0);
        if (R_FAILED(dl->am_result) || written != size) return -1;
        dl->offset += size;
    } else {
        if (fwrite(data, 1, size, dl->f) != size) return -1;
    }

    u64 now = osGetTime();
    if (now - dl->sample_ms >= 1000) {
        dl->speed = bytes_per_second(done - dl->sample_bytes, now - dl->sample_ms);
        dl->sample_ms = now;
        dl->sample_bytes = done;
    }
    if (now - dl->last_draw_ms >= 250 || (total && done >= total)) {
        dl->last_draw_ms = now;
        draw_progress(dl, done, total);
    }

    return cancel_requested() ? 1 : 0;
}

typedef enum { MODE_CIA, MODE_FILE } InstallMode;

static int confirm_install(Catalog *cat, const CatEntry *e, const CatPlan *plan,
                           const char *target, bool exists) {
    consoleSelect(cat->bottom);
    consoleClear();
    pline(1, BOT_W, "\x1b[36m", "=== Install ===");
    int row = 3;
    row += print_wrapped(row, BOT_W, NULL, e->name[0] ? e->name : e->filename, 3);
    row++;
    char size[16];
    cat_format_size(e->size, size, sizeof(size));
    pline(row++, BOT_W, NULL, "Server file: %s", size);
    pline(row++, BOT_W, NULL, "%s", plan_label(plan));

    bool cia = (plan->kind == CAT_PLAN_CIA || plan->kind == CAT_PLAN_CIA_CONVERT);
    if (cia) {
        pline(row++, BOT_W, NULL, "Goes straight into the system (SD),");
        pline(row++, BOT_W, NULL, "nothing is kept on the SD card.");
        if (plan->kind == CAT_PLAN_CIA_CONVERT) {
            pline(row++, BOT_W, "\x1b[90m", "The server converts it first: big");
            pline(row++, BOT_W, "\x1b[90m", "games can take minutes to start.");
        }
        if (exists) pline(++row, BOT_W, "\x1b[33m", "Already installed: install again?");
    } else {
        pline(row++, BOT_W, NULL, "To %s/", cat->rom_dir);
        row += print_wrapped(row, BOT_W, NULL, target, 2);
        if (plan->kind == CAT_PLAN_3DS_FILE) {
            pline(row++, BOT_W, "\x1b[90m", "The server can't make a CIA. Install");
            pline(row++, BOT_W, "\x1b[90m", "the file with GodMode9 (Build CIA).");
        }
        if (exists) pline(++row, BOT_W, "\x1b[31m", "Already on the SD: replace it?");
    }

    pline(SCREEN_H - 2, BOT_W, NULL, "A: %s   B: Cancel", cia ? "Install" : "Download");
    if (cia && plan->file_fallback)
        pline(SCREEN_H - 1, BOT_W, NULL, "X: Save the .3ds file to the SD");
    present();

    while (aptMainLoop()) {
        hidScanInput();
        u32 k = hidKeysDown();
        if (k & KEY_A) return cia ? MODE_CIA : MODE_FILE;
        if ((k & KEY_X) && cia && plan->file_fallback) return MODE_FILE;
        if (k & KEY_B) return -1;
        gspWaitForVBlank();
    }
    return -1;
}

static void show_error_body(const NetDlInfo *info) {
    char msg[256];
    cat_ascii(info->error, msg, sizeof(msg));
    // First lines of the server's explanation
    char *p = msg;
    for (int lines = 0; *p && lines < 6; lines++) {
        char *nl = strchr(p, '\n');
        if (nl) *nl = '\0';
        printf("%.*s\n", BOT_W - 1, p);
        if (!nl) break;
        p = nl + 1;
    }
}

// Run one install; returns true if the user asked to retry as a file
static bool run_install(Catalog *cat, const CatEntry *e, const CatPlan *plan, InstallMode mode) {
    bool cia = (mode == MODE_CIA);
    char target[CAT_FILE_LEN];
    const char *ext = ".nds";
    if (system_is_3ds(cat)) {
        const char *dot = strrchr(e->filename, '.');
        ext = (dot && strcasecmp(dot, ".cci") == 0) ? ".cci" : ".3ds";
    } else if (strcasecmp(cat->systems[cat->sys], "DSI") == 0) {
        const char *dot = strrchr(e->filename, '.');
        if (dot && strcasecmp(dot, ".dsi") == 0) ext = ".dsi";
    }
    cat_target_name(e->filename, ext, target, sizeof(target));
    char path[MAX_PATH_LEN + CAT_FILE_LEN], part[MAX_PATH_LEN + CAT_FILE_LEN + 8];
    snprintf(path, sizeof(path), "%s/%s", cat->rom_dir, target);
    snprintf(part, sizeof(part), "%s.part", path);

    // A file save of a 3DS entry wants the raw cart, whatever the plan said
    const char *query = cia ? plan->query : (plan->kind == CAT_PLAN_NDS_EXTRACT ? plan->query : "");

    struct stat st;
    bool exists = !cia && stat(path, &st) == 0;

    consoleSelect(cat->bottom);
    consoleClear();
    pline(1, BOT_W, "\x1b[36m", cia ? "=== Installing CIA ===" : "=== Downloading ===");
    pline(2, BOT_W, NULL, "%s", e->name[0] ? e->name : e->filename);
    pline(4, BOT_W, NULL, "Connecting...");
    pline(10, BOT_W, "\x1b[90m", "Hold B to cancel");
    present();

    FILE *f = NULL;
    if (!cia) {
        mkdir_parents(cat->rom_dir);
        remove(part);
        f = fopen(part, "wb");
        if (!f) {
            at(12, 1);
            printf("\x1b[31mCan't create the file on SD\x1b[0m\n%.78s\n", part);
            wait_button();
            return false;
        }
        setvbuf(f, NULL, _IOFBF, 64 * 1024);
    }

    char id[CAT_ID_LEN * 3];
    cat_url_encode(e->rom_id, id, sizeof(id));
    char url_path[CAT_ID_LEN * 3 + 64];
    snprintf(url_path, sizeof(url_path), "/roms/%s%s", id, query);

    Download dl;
    memset(&dl, 0, sizeof(dl));
    dl.cat = cat;
    dl.cia = cia;
    dl.f = f;
    dl.replaced = exists ? (u64)st.st_size : 0;
    if (cia) dl.probe = malloc(CAT_CIA_PROBE_MAX);
    dl.start_ms = osGetTime();
    if (cia && !dl.probe) {
        at(12, 1);
        printf("\x1b[31mOut of memory\x1b[0m\n");
        wait_button();
        return false;
    }

    bool sleep_allowed = aptIsSleepAllowed();
    aptSetSleepAllowed(false);
    NetDlInfo info;
    u32 max_wait = (cia && plan->kind == CAT_PLAN_CIA_CONVERT) ? WAIT_CONVERT_S : WAIT_PLAIN_S;
    NetDlResult rc = network_download(cat->config, url_path, max_wait,
                                      download_wait, download_sink, &dl, &info);
    aptSetSleepAllowed(sleep_allowed);
    u64 ms = osGetTime() - dl.start_ms;

    if (cia && rc == NET_DL_OK) {
        if (!dl.am_started) {
            rc = NET_DL_SHORT;  // empty body
        } else {
            dl.am_result = AM_FinishCiaInstall(dl.handle);
            dl.am_started = false;
            if (R_FAILED(dl.am_result)) rc = NET_DL_SINK;
        }
    }
    if (dl.am_started) {
        AM_CancelCIAInstall(dl.handle);
        dl.am_started = false;
    }
    free(dl.probe);
    if (f) {
        bool closed_ok = (fclose(f) == 0);
        if (rc == NET_DL_OK && !closed_ok) rc = NET_DL_SINK;
    }

    consoleSelect(cat->bottom);
    at(12, 1);
    if (rc != NET_DL_OK) {
        if (!cia) remove(part);
        char a[16], b[16];
        bool offer_file = false;
        switch (rc) {
            case NET_DL_CANCELLED:
                printf("Cancelled\n");
                break;
            case NET_DL_CONNECT:
                printf("\x1b[31mNo response from server\x1b[0m\n");
                break;
            case NET_DL_TIMEOUT:
                printf("\x1b[31mThe server took too long\x1b[0m\n");
                break;
            case NET_DL_STATUS:
                printf("\x1b[31mServer error (HTTP %lu)\x1b[0m\n", (unsigned long)info.status);
                show_error_body(&info);
                offer_file = cia && plan->file_fallback && info.status == 503;
                break;
            case NET_DL_SINK:
                if (dl.bad_data && cia)
                    printf("\x1b[31mThe server didn't send a CIA\x1b[0m\n");
                else if (dl.bad_data)
                    printf("\x1b[31mServer sent a zip, not a ROM:\x1b[0m\nupdate the server\n");
                else if (dl.no_space)
                    printf("\x1b[31mNot enough space on the SD\x1b[0m\n");
                else if (cia)
                    printf("\x1b[31mCIA install failed\x1b[0m\nAM error %08lX\n", (unsigned long)dl.am_result);
                else
                    printf("\x1b[31mSD write failed (card full?)\x1b[0m\n");
                break;
            case NET_DL_SHORT:
                cat_format_size(info.received, a, sizeof(a));
                cat_format_size(info.total, b, sizeof(b));
                printf("\x1b[31mConnection lost\x1b[0m\nat %s of %s\n", a, b);
                break;
            default:
                printf("\x1b[31mDownload failed\x1b[0m\n");
                break;
        }
        if (offer_file) {
            printf("\nX: save the .3ds file to the SD\n   instead (for GodMode9)\nB: back\n");
            present();
            while (aptMainLoop()) {
                hidScanInput();
                u32 k = hidKeysDown();
                if (k & KEY_X) return true;
                if (k & (KEY_B | KEY_A)) break;
                gspWaitForVBlank();
            }
            return false;
        }
        wait_button();
        return false;
    }

    if (!cia) {
        if (exists) remove(path);
        if (rename(part, path) != 0) {
            remove(part);
            printf("\x1b[31mCouldn't rename the download\x1b[0m\n");
            wait_button();
            return false;
        }
        cat_names_add(&cat->files, target);
        cat_names_sort(&cat->files);
        // Its save shows up in the save list after a rescan
        if (!system_is_3ds(cat) && cat->config->nds_dir[0]) cat->nds_installed = true;
    } else if (dl.probe_state == 1) {
        cat_cia_map_set(&cat->cia_map, e->rom_id, dl.tid);
        cat_cia_map_save(&cat->cia_map, CIA_MAP_PATH);
        load_installed_titles(cat);
    }

    char size[16], secs[16];
    cat_format_size(info.received, size, sizeof(size));
    format_time((unsigned)(ms / 1000), secs, sizeof(secs));
    printf("\x1b[32m%s\x1b[0m %s in %s\n", cia ? "Installed" : "Saved", size, secs);
    printf("Average: %lu KB/s\n", (unsigned long)(bytes_per_second(info.received, ms) / 1024));
    if (cia) {
        printf("\nFind it on the HOME Menu.\n");
    } else if (system_is_3ds(cat)) {
        printf("\nIn GodMode9, open the file and pick\n\"Build CIA from file\" to install it.\n");
    } else {
        printf("\nStart it from TWiLight Menu++.\n");
    }
    wait_button();
    return false;
}

static void install(Catalog *cat, const CatEntry *entry) {
    CatEntry e = *entry;  // the window may be refetched meanwhile
    CatPlan plan = cat_plan_install(cat->systems[cat->sys], &e);

    consoleSelect(cat->bottom);
    if (plan.kind == CAT_PLAN_NONE) {
        consoleClear();
        printf("\x1b[31mCan't install this game\x1b[0m\n\n%.78s\n\n%s\n", e.filename, plan.reason);
        wait_button();
        return;
    }

    bool cia = (plan.kind == CAT_PLAN_CIA || plan.kind == CAT_PLAN_CIA_CONVERT);
    char target[CAT_FILE_LEN];
    cat_target_name(e.filename, system_is_3ds(cat) ? ".3ds" : ".nds", target, sizeof(target));
    bool exists = cia ? installed_tid(cat, &e) != 0 : file_on_sd(cat, &e);

    int mode = confirm_install(cat, &e, &plan, target, exists);
    if (mode < 0) return;
    if (run_install(cat, &e, &plan, (InstallMode)mode))
        run_install(cat, &e, &plan, MODE_FILE);  // CIA conversion unavailable
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

static void message(PrintConsole *console, const char *text) {
    consoleSelect(console);
    consoleClear();
    printf("\x1b[36m=== Game Catalog ===\x1b[0m\n\n%s\n", text);
    wait_button();
}

static bool edit_search(char *search, size_t size) {
    SwkbdState kb;
    char text[64];
    if (size > sizeof(text)) size = sizeof(text);
    snprintf(text, sizeof(text), "%s", search);
    swkbdInit(&kb, SWKBD_TYPE_NORMAL, 2, (int)size - 1);
    swkbdSetHintText(&kb, "Search game names (empty = all)");
    swkbdSetInitialText(&kb, text);
    swkbdSetValidation(&kb, SWKBD_ANYTHING, 0, 0);
    swkbdSetButton(&kb, SWKBD_BUTTON_LEFT, "Cancel", false);
    swkbdSetButton(&kb, SWKBD_BUTTON_RIGHT, "Search", true);
    SwkbdButton button = swkbdInputText(&kb, text, sizeof(text));
    if (button != SWKBD_BUTTON_RIGHT) return false;
    text[size - 1] = '\0';  // swkbd was limited to size-1 already
    memcpy(search, text, strlen(text) + 1);
    return true;
}

bool catalog_screen(const AppConfig *config) {
    PrintConsole *top = ui_top_console(), *bottom = ui_bottom_console();
    ui_clear();

    Catalog *cat = calloc(1, sizeof(Catalog));
    CatEntry *win = cat ? malloc(CAT_WINDOW * sizeof(CatEntry)) : NULL;
    if (!win) {
        free(cat);
        message(bottom, "Out of memory");
        ui_clear();
        return false;
    }
    cat->config = config;
    cat->top = top;
    cat->bottom = bottom;
    cat->win = win;
    cat->total = -1;

    consoleSelect(bottom);
    printf("\x1b[36m=== Game Catalog ===\x1b[0m\n\nContacting server...\n");
    present();
    if (!load_systems(cat)) {
        char text[192];
        snprintf(text, sizeof(text), "%s\n\nCheck WiFi and the server URL.", cat->error);
        message(bottom, text);
        goto done;
    }
    if (cat->nsystems == 0) {
        message(bottom, "The server has no 3DS or DS games.");
        goto done;
    }

    printf("Looking for installed games...\n");
    present();
    cat_cia_map_load(&cat->cia_map, CIA_MAP_PATH);
    load_installed_titles(cat);
    scan_installed(cat);
    consoleClear();
    reset_list(cat);
    hidSetRepeatParameters(20, 4);

    bool redraw = true;
    while (aptMainLoop()) {
        if (redraw) {
            draw(cat);
            redraw = false;
        }
        gspWaitForVBlank();
        hidScanInput();
        u32 down = hidKeysDown();
        u32 rep = hidKeysDownRepeat();

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
            if (edit_search(cat->search, sizeof(cat->search))) reset_list(cat);
            ui_clear();
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
                ui_clear();
            }
            redraw = true;
        }
    }

done:;
    bool rescan = cat->nds_installed;
    cat_names_free(&cat->files);
    cat_cia_map_free(&cat->cia_map);
    free(cat->titles);
    free(cat->win);
    free(cat);
    ui_clear();
    return rescan;
}
