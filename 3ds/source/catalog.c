// Game catalog screen for the 3DS client. The browsing UX (server-side
// paging window, RA badges and filter, search, progress with speed/ETA,
// hold-B cancel) is ported from the DS client's ds/source/catalog.c.
//
// 3DS games install as a CIA streamed straight into AM (the same path the
// self-updater uses), chunk by chunk from the HTTP body, so a ROM is never
// held in RAM or staged on the SD. The server converts .3ds/.cci carts with
// ?extract=cia (operator-configured, see server/README.md); a .cia on the
// server is installed as-is. DS/DSi games are written to the SD as .nds.
//
// Layout: the list is on the bottom screen, the selected game's details on
// the top screen; dialogs and install progress are cards over the list.
#include "catalog.h"
#include "catalog_data.h"
#include "network.h"
#include "ui.h"
#include <stdarg.h>
#include <strings.h>
#include <sys/stat.h>

#define CAT_WINDOW 200       // entries held around the visible rows
#define CAT_ROWS 9           // list rows on the bottom screen
#define CAT_ROW_H 21
#define CAT_LIST_Y GUI_HEADER_H
#define CAT_JUMP 100         // L/R
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
    bool loading;            // a page request is in flight (drawn as a spinner)
    UiListAnim anim;

    char rom_dir[MAX_PATH_LEN];  // where files of this system go
    CatNameSet files;            // ROM files already in rom_dir
    u64 *titles;                 // installed SD titles (sorted)
    u32 title_count;
    CatCiaMap cia_map;           // rom_id -> title id of catalog installs

    bool nds_installed;          // a DS game landed in the save scan folder
} Catalog;

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

static void format_time(unsigned seconds, char *out, size_t size) {
    if (seconds > 99 * 3600 + 3599) seconds = 99 * 3600 + 3599;
    if (seconds >= 3600)
        snprintf(out, size, "%u:%02u:%02u", seconds / 3600, (seconds / 60) % 60, seconds % 60);
    else
        snprintf(out, size, "%u:%02u", seconds / 60, seconds % 60);
}

static void format_speed(u32 bytes_per_s, char *out, size_t size) {
    if (bytes_per_s >= 1024 * 1024)
        snprintf(out, size, "%.1f MB/s", bytes_per_s / (1024.0 * 1024.0));
    else
        snprintf(out, size, "%lu KB/s", (unsigned long)(bytes_per_s / 1024));
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

static const char *entry_name(const CatEntry *e) {
    return e->name[0] ? e->name : e->filename;
}

static void draw_frame(Catalog *cat);

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
    cat->loading = true;
    draw_frame(cat);
    cat->loading = false;
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
    cat->anim.init = false;
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
    else if (e->ra_achievements > 999) snprintf(out, size, "RA 999+");
    else snprintf(out, size, "RA %d", e->ra_achievements);
}

static const char *plan_label(const CatPlan *plan) {
    switch (plan->kind) {
        case CAT_PLAN_CIA:         return "CIA to the HOME Menu";
        case CAT_PLAN_CIA_CONVERT: return "CIA (the server converts it)";
        case CAT_PLAN_3DS_FILE:    return ".3ds file (no CIA conversion)";
        case CAT_PLAN_NDS:         return ".nds file";
        case CAT_PLAN_NDS_EXTRACT: return ".nds (the server unzips it)";
        default:                   return "Can't be installed here";
    }
}

static void kv(float x, float y, float label_w, float w, const char *label, const char *value, u32 color) {
    gui_text(x, y, GUI_S_SMALL, gui_rgb(HEX_MUTED), GUI_LEFT, label);
    gui_text_fit(x + label_w, y, GUI_S_SMALL, gui_rgb(color), GUI_LEFT, w - label_w, value);
}

// RA badge: gold pill with the count, outlined when matched by name only
static float ra_badge(float x_right, float y, float h, const CatEntry *e, bool on_accent) {
    char tag[16];
    ra_tag(e, tag, sizeof(tag));
    if (!tag[0]) return 0;
    float w = gui_pill_w(h, GUI_S_TINY, tag);
    if (e->ra_title_only)
        gui_pill_outline(x_right - w, y, h, GUI_S_TINY, gui_rgb(on_accent ? HEX_INK : HEX_RA),
                         gui_rgb(on_accent ? HEX_ACCENT : HEX_BG), tag);
    else
        gui_pill(x_right - w, y, h, GUI_S_TINY, gui_rgb(on_accent ? HEX_INK : HEX_RA),
                 gui_rgb(on_accent ? HEX_RA : HEX_INK), tag);
    return w;
}

static void draw_top(void *ctx) {
    Catalog *cat = ctx;
    gui_header("Game Catalog");
    GuiHint hints[5];
    int nhints = 0;
    if (cat->nsystems > 1) hints[nhints++] = (GuiHint){ "SELECT", "System" };
    hints[nhints++] = (GuiHint){ "Y", "RA only" };
    hints[nhints++] = (GuiHint){ "X", "Search" };
    hints[nhints++] = (GuiHint){ "L", "-100" };
    hints[nhints++] = (GuiHint){ "R", "+100" };

    float px = 8, py = 32, pw = GUI_TOP_W - 16;
    float banner_y = 190;
    const char *banner = NULL;
    u32 banner_hex = HEX_ERR;
    if (cat->error[0]) banner = cat->error;
    else if (cat->filter_ignored) banner = "This server can't filter by RetroAchievements: update it";
    else if (cat->search[0]) banner_hex = HEX_ACCENT;

    const CatEntry *e = (cat->total > 0) ? entry_at(cat, cat->selected) : NULL;
    gui_panel(px, py, pw, 152);
    if (!e) {
        const char *msg = cat->loading || cat->total < 0 ? "Loading the catalog..."
                        : cat->total == 0 ? (cat->search[0] ? "No games match the search"
                                             : cat->ra_only ? "No games with achievements"
                                             : "No games for this system")
                        : "Loading...";
        gui_text(GUI_TOP_W / 2.0f, 92, GUI_S_TITLE, gui_rgb(HEX_DIM), GUI_CENTER, msg);
        if (cat->total == 0 && cat->search[0])
            gui_text(GUI_TOP_W / 2.0f, 120, GUI_S_SMALL, gui_rgb(HEX_MUTED), GUI_CENTER,
                     "X: new search   START: clear the search");
        else if (cat->total == 0 && cat->ra_only)
            gui_text(GUI_TOP_W / 2.0f, 120, GUI_S_SMALL, gui_rgb(HEX_MUTED), GUI_CENTER, "Y: show all games");
    } else {
        float x = px + 12, w = pw - 24;
        int lines = gui_text_wrap(x, py + 8, GUI_S_TITLE, gui_rgb(HEX_TEXT), w, 2, entry_name(e));
        float y = py + 10 + lines * gui_line_h(GUI_S_TITLE);

        // Pills: system, size, RA, installed
        char size[16];
        cat_format_size(e->size, size, sizeof(size));
        const char *ext = strrchr(e->filename, '.');
        bool zipped = ext && strcasecmp(ext, ".zip") == 0;
        float pxx = x;
        u32 sys_hex = system_is_3ds(cat) ? HEX_ACCENT2 : HEX_NDS;
        pxx += gui_pill(pxx, y, 15, GUI_S_TINY, gui_mix(HEX_PANEL, sys_hex, 0.3f), gui_rgb(sys_hex),
                        cat->systems[cat->sys]) + 5;
        char size_label[32];
        snprintf(size_label, sizeof(size_label), "%s%s", size, zipped ? " zip" : "");
        pxx += gui_pill(pxx, y, 15, GUI_S_TINY, gui_rgb(HEX_BG2), gui_rgb(HEX_DIM), size_label) + 5;
        if (cat_entry_has_ra(e)) {
            char tag[16];
            ra_tag(e, tag, sizeof(tag));
            pxx += ra_badge(pxx + gui_pill_w(15, GUI_S_TINY, tag), y, 15, e, false) + 5;
        }
        bool inst = is_installed(cat, e);
        if (inst) {
            float iw = gui_pill_w(15, GUI_S_TINY, "INSTALLED") + 12;
            gui_rrect(pxx, y, iw, 15, 7.5f, gui_mix(HEX_PANEL, HEX_OK, 0.25f));
            gui_icon_check(pxx + 8, y + 7.5f, 7, gui_rgb(HEX_OK));
            gui_text_mid(pxx + 15, y, 15, GUI_S_TINY, gui_rgb(HEX_OK), GUI_LEFT, 0, "INSTALLED");
        }
        y += 22;

        float lh = gui_line_h(GUI_S_SMALL) + 1;
        CatPlan plan = cat_plan_install(cat->systems[cat->sys], e);
        kv(x, y, 66, w, "File", e->filename, HEX_DIM);
        kv(x, y + lh, 66, w, "Installs as", plan_label(&plan), plan.kind == CAT_PLAN_NONE ? HEX_ERR : HEX_TEXT);
        if (system_is_3ds(cat)) kv(x, y + 2 * lh, 66, w, "Goes to", "HOME Menu (title on the SD)", HEX_TEXT);
        else kv(x, y + 2 * lh, 66, w, "Goes to", cat->rom_dir, HEX_TEXT);

        char ra[64];
        u32 ra_hex = HEX_DIM;
        if (cat_entry_has_ra(e) && e->ra_title_only) {
            snprintf(ra, sizeof(ra), "%d achievements? (matched by name)", e->ra_achievements);
            ra_hex = HEX_RA;
        } else if (cat_entry_has_ra(e)) {
            snprintf(ra, sizeof(ra), "%d achievements", e->ra_achievements);
            ra_hex = HEX_RA;
        } else if (e->ra_game_id) {
            snprintf(ra, sizeof(ra), "Known, no achievements yet");
        } else {
            snprintf(ra, sizeof(ra), "None");
        }
        kv(x, y + 3 * lh, 66, w, "RetroAch.", ra, ra_hex);

        u64 tid = system_is_3ds(cat) ? installed_tid(cat, e) : 0;
        char inst_line[48];
        if (tid) snprintf(inst_line, sizeof(inst_line), "Yes, %016llX", (unsigned long long)tid);
        else snprintf(inst_line, sizeof(inst_line), "%s", file_on_sd(cat, e) ? "On the SD card" : "No");
        kv(x, y + 4 * lh, 66, w, "Installed", inst_line, (tid || inst) ? HEX_OK : HEX_DIM);
    }

    // Banner: error, or the active search
    if (banner || cat->search[0]) {
        char text[128];
        if (banner) snprintf(text, sizeof(text), "%s", banner);
        else snprintf(text, sizeof(text), "Search: \"%s\"   (START clears)", cat->search);
        gui_rrect(8, banner_y, GUI_TOP_W - 16, 22, 6, gui_rgb(HEX_BG2));
        gui_rrect(8, banner_y, 4, 22, 2, gui_rgb(banner_hex));
        gui_text_mid(20, banner_y, 22, GUI_S_SMALL, gui_rgb(banner ? HEX_ERR : HEX_TEXT), GUI_LEFT,
                     GUI_TOP_W - 40, text);
    }
    gui_footer(hints, nhints);
}

static void cat_row(void *ctx, int index, float x, float y, float w, float h, bool selected) {
    Catalog *cat = ctx;
    const CatEntry *e = entry_at(cat, index);
    float cy = y + h / 2;
    if (!e) {
        gui_text_mid(x + 26, y, h, GUI_S_BODY, gui_rgb(selected ? HEX_INK : HEX_MUTED), GUI_LEFT, 0,
                     "Loading...");
        return;
    }
    bool inst = is_installed(cat, e);
    if (inst) {
        gui_circle(x + 14, cy, 6, gui_rgb(selected ? HEX_INK : HEX_OK));
        gui_icon_check(x + 14, cy, 7, gui_rgb(selected ? HEX_ACCENT : HEX_INK));
    } else {
        gui_circle(x + 14, cy, 2, gui_rgb(selected ? HEX_INK : HEX_LINE));
    }
    float badge = 0;
    if (cat_entry_has_ra(e)) badge = ra_badge(x + w - 8, cy - 6.5f, 13, e, selected) + 6;
    u32 color = selected ? HEX_INK : (inst ? HEX_OK : HEX_TEXT);
    gui_text_mid(x + 26, y, h, GUI_S_BODY, gui_rgb(color), GUI_LEFT, w - 26 - 8 - badge, entry_name(e));
}

static void draw_bottom(void *ctx) {
    Catalog *cat = ctx;
    bool has_rows = cat->total > 0;
    if (has_rows) {
        ui_list(&cat->anim, 0, CAT_LIST_Y, GUI_BOT_W, CAT_ROWS, CAT_ROW_H, cat->total,
                cat->selected, cat->scroll, cat_row, cat);
    } else {
        const char *msg = cat->error[0] && cat->win_count == 0 ? "A: try again   B: back"
                        : cat->total == 0 ? "Nothing to show" : "";
        if (cat->total < 0 || cat->loading) {
            gui_spinner(GUI_BOT_W / 2.0f, 104, 10, gui_rgb(HEX_ACCENT));
        } else {
            gui_text(GUI_BOT_W / 2.0f, 100, GUI_S_BODY, gui_rgb(HEX_DIM), GUI_CENTER, msg);
        }
    }

    gui_header_bar();
    const char *labels[3];
    for (int i = 0; i < cat->nsystems && i < 3; i++) labels[i] = cat->systems[i];
    float x = 6;
    if (cat->nsystems > 0) x = gui_tabs(6, 4, 18, labels, cat->nsystems, cat->sys) + 6;
    if (cat->ra_only) x += gui_pill(x, 5.5f, 15, GUI_S_TINY, gui_rgb(HEX_RA), gui_rgb(HEX_INK), "RA ONLY") + 4;
    if (cat->search[0]) gui_pill(x, 5.5f, 15, GUI_S_TINY, gui_rgb(HEX_ACCENT), gui_rgb(HEX_INK), "SEARCH");
    if (cat->loading) {
        gui_spinner(GUI_BOT_W - 14, GUI_HEADER_H / 2.0f, 6, gui_rgb(HEX_ACCENT));
    } else if (cat->total > 0) {
        char pos[24];
        snprintf(pos, sizeof(pos), "%d/%d", cat->selected + 1, cat->total);
        gui_text_mid(GUI_BOT_W - 8, 0, GUI_HEADER_H, GUI_S_SMALL, gui_rgb(HEX_DIM), GUI_RIGHT, 0, pos);
    }

    static const GuiHint hints[] = {
        { "A", "Install" }, { "B", "Back" }, { "LR", "Page" }, { "START", "Clear" },
    };
    gui_footer(hints, cat->search[0] ? 4 : 3);
}

static void draw_frame(Catalog *cat) {
    gui_begin(!cat->loading);
    gui_screen(GUI_TOP);
    draw_top(cat);
    gui_screen(GUI_BOTTOM);
    draw_bottom(cat);
    gui_end();
}

// ---------------------------------------------------------------------------
// Install
// ---------------------------------------------------------------------------

typedef struct {
    Catalog *cat;
    const char *name;
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
    char a[16], b[16], left[40], right[8] = "", speed[24], avg_s[24], info1[80], info2[64], t1[16], t2[16];

    cat_format_size(done, a, sizeof(a));
    if (total) {
        cat_format_size(total, b, sizeof(b));
        snprintf(left, sizeof(left), "%s / %s", a, b);
        snprintf(right, sizeof(right), "%lu%%", (unsigned long)(done * 100 / total));
    } else {
        snprintf(left, sizeof(left), "%s", a);
    }
    format_speed(dl->speed ? dl->speed : avg, speed, sizeof(speed));
    format_speed(avg, avg_s, sizeof(avg_s));
    snprintf(info1, sizeof(info1), "Speed %s   (average %s)", speed, avg_s);
    format_time(elapsed, t1, sizeof(t1));
    if (total && avg > 0 && done < total) {
        format_time((unsigned)((total - done) / avg), t2, sizeof(t2));
        snprintf(info2, sizeof(info2), "Elapsed %s   Remaining %s", t1, t2);
    } else {
        snprintf(info2, sizeof(info2), "Elapsed %s", t1);
    }

    UiProgress p = { 0 };
    p.title = dl->cia ? "Installing CIA" : "Downloading";
    p.name = dl->name;
    p.status = dl->cia ? "Streaming into the system" : "Writing to the SD card";
    p.frac = total ? (float)((double)done / (double)total) : -1;
    p.left = left;
    p.right = total ? right : NULL;
    p.info1 = info1;
    p.info2 = info2;
    p.cancel_hint = true;
    ui_progress(&p);
}

// Read the keys; true if the user wants out (B held, or the app closing)
static bool cancel_requested(void) {
    if (!aptMainLoop()) return true;
    hidScanInput();
    return (hidKeysHeld() & KEY_B) != 0;
}

static bool download_wait(u32 seconds, void *user) {
    Download *dl = user;
    char t[16], status[64];
    format_time(seconds, t, sizeof(t));
    if (dl->cia)
        snprintf(status, sizeof(status), "The server is converting it to CIA...  %s", t);
    else
        snprintf(status, sizeof(status), "Waiting for the server...  %s", t);
    UiProgress p = { 0 };
    p.title = dl->cia ? "Installing CIA" : "Downloading";
    p.name = dl->name;
    p.status = status;
    p.frac = -1;
    p.info1 = (dl->cia && seconds >= 3) ? "Big games take a few minutes to convert." : NULL;
    p.cancel_hint = true;
    ui_progress(&p);
    return cancel_requested();
}

static int download_sink(const u8 *data, u32 size, u64 done, u64 total, void *user) {
    Download *dl = user;

    if (!dl->checked) {
        dl->checked = true;
        dl->start_ms = dl->last_draw_ms = dl->sample_ms = osGetTime();
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
    // A GUI frame costs a few ms: redraw a few times a second only
    if (now - dl->last_draw_ms >= 250 || (total && done >= total)) {
        dl->last_draw_ms = now;
        draw_progress(dl, done, total);
    }

    return cancel_requested() ? 1 : 0;
}

typedef enum { MODE_CIA, MODE_FILE } InstallMode;

static int confirm_install(Catalog *cat, const CatEntry *e, const CatPlan *plan,
                           const char *target, bool exists) {
    char body[640], size[16];
    cat_format_size(e->size, size, sizeof(size));
    bool cia = (plan->kind == CAT_PLAN_CIA || plan->kind == CAT_PLAN_CIA_CONVERT);
    int pos = snprintf(body, sizeof(body), UI_HI "%s\n" UI_DIM "%s on the server  \xC2\xB7  %s\n",
                       entry_name(e), size, plan_label(plan));
    if (cia) {
        pos += snprintf(body + pos, sizeof(body) - pos,
                        "Goes straight into the system: nothing is kept on the SD card.\n");
        if (plan->kind == CAT_PLAN_CIA_CONVERT)
            pos += snprintf(body + pos, sizeof(body) - pos,
                            UI_DIM "The server converts it first: big games can take minutes to start.\n");
        if (exists)
            pos += snprintf(body + pos, sizeof(body) - pos, "\nAlready installed: install it again?\n");
    } else {
        pos += snprintf(body + pos, sizeof(body) - pos, "To %s/%s\n", cat->rom_dir, target);
        if (plan->kind == CAT_PLAN_3DS_FILE)
            pos += snprintf(body + pos, sizeof(body) - pos,
                            UI_DIM "The server can't make a CIA: install the file with GodMode9 (Build CIA).\n");
        if (exists)
            pos += snprintf(body + pos, sizeof(body) - pos, "\nAlready on the SD: replace it?\n");
    }
    (void)pos;

    UiButton buttons[3];
    int n = 0;
    buttons[n++] = (UiButton){ KEY_B, "B", "Cancel" };
    if (cia && plan->file_fallback) buttons[n++] = (UiButton){ KEY_X, "X", "Save .3ds" };
    buttons[n++] = (UiButton){ KEY_A, "A", cia ? "Install" : "Download" };

    u32 key = ui_dialog(exists ? UI_TONE_WARN : UI_TONE_ACCENT, cia ? "Install game" : "Download game",
                        body, buttons, n);
    if (key & KEY_A) return cia ? MODE_CIA : MODE_FILE;
    if (key & KEY_X) return MODE_FILE;
    return -1;
}

// First lines of the server's error body, appended to `out`
static void append_error_body(const NetDlInfo *info, char *out, size_t size) {
    char msg[256];
    snprintf(msg, sizeof(msg), "%s", info->error);
    char *p = msg;
    for (int lines = 0; *p && lines < 4; lines++) {
        char *nl = strchr(p, '\n');
        if (nl) *nl = '\0';
        size_t len = strlen(out);
        snprintf(out + len, size - len, UI_DIM "%.120s\n", p);
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

    UiProgress connecting = { 0 };
    connecting.title = cia ? "Installing CIA" : "Downloading";
    connecting.name = entry_name(e);
    connecting.status = "Connecting...";
    connecting.frac = -1;
    connecting.cancel_hint = true;
    ui_progress(&connecting);

    FILE *f = NULL;
    if (!cia) {
        mkdir_parents(cat->rom_dir);
        remove(part);
        f = fopen(part, "wb");
        if (!f) {
            char body[400];
            snprintf(body, sizeof(body), "Can't create the file on the SD card:\n" UI_DIM "%.300s", part);
            ui_message(UI_TONE_ERR, "Download failed", body);
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
    dl.name = entry_name(e);
    dl.cia = cia;
    dl.f = f;
    dl.replaced = exists ? (u64)st.st_size : 0;
    if (cia) dl.probe = malloc(CAT_CIA_PROBE_MAX);
    dl.start_ms = osGetTime();
    if (cia && !dl.probe) {
        ui_message(UI_TONE_ERR, "Install failed", "Out of memory.");
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
            UiProgress p = { 0 };
            p.title = "Installing CIA";
            p.name = entry_name(e);
            p.status = "Finishing the install...";
            p.frac = -1;
            ui_progress(&p);
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

    if (rc != NET_DL_OK) {
        if (!cia) remove(part);
        char body[640] = "";
        char a[16], b[16];
        const char *title = cia ? "Install failed" : "Download failed";
        bool offer_file = false;
        UiTone tone = UI_TONE_ERR;
        switch (rc) {
            case NET_DL_CANCELLED:
                title = "Cancelled";
                tone = UI_TONE_ACCENT;
                snprintf(body, sizeof(body), "The %s was cancelled.", cia ? "install" : "download");
                break;
            case NET_DL_CONNECT:
                snprintf(body, sizeof(body), "No response from the server.");
                break;
            case NET_DL_TIMEOUT:
                snprintf(body, sizeof(body), "The server took too long to answer.");
                break;
            case NET_DL_STATUS:
                offer_file = cia && plan->file_fallback && info.status == 503;
                snprintf(body, sizeof(body), "Server error (HTTP %lu)\n%s", (unsigned long)info.status,
                         offer_file ? "Save the .3ds file to the SD instead, to install with GodMode9?\n\n" : "");
                append_error_body(&info, body, sizeof(body));
                break;
            case NET_DL_SINK:
                if (dl.bad_data && cia)
                    snprintf(body, sizeof(body), "The server didn't send a CIA.");
                else if (dl.bad_data)
                    snprintf(body, sizeof(body), "The server sent a zip, not a ROM:\nupdate the server.");
                else if (dl.no_space)
                    snprintf(body, sizeof(body), "Not enough free space on the SD card.");
                else if (cia)
                    snprintf(body, sizeof(body), "The system refused the CIA.\n" UI_DIM "AM error %08lX",
                             (unsigned long)dl.am_result);
                else
                    snprintf(body, sizeof(body), "Writing to the SD card failed (card full?).");
                break;
            case NET_DL_SHORT:
                cat_format_size(info.received, a, sizeof(a));
                cat_format_size(info.total, b, sizeof(b));
                snprintf(body, sizeof(body), "Connection lost at %s of %s.", a, b);
                break;
            default:
                snprintf(body, sizeof(body), "Download failed.");
                break;
        }
        if (offer_file) {
            static const UiButton buttons[] = { { KEY_B | KEY_A, "B", "Back" }, { KEY_X, "X", "Save .3ds" } };
            return (ui_dialog(UI_TONE_WARN, "Can't convert to CIA", body, buttons, 2) & KEY_X) != 0;
        }
        ui_message(tone, title, body);
        return false;
    }

    if (!cia) {
        if (exists) remove(path);
        if (rename(part, path) != 0) {
            remove(part);
            ui_message(UI_TONE_ERR, "Download failed", "Couldn't rename the finished download.");
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

    char size[16], secs[16], avg[24], body[400];
    cat_format_size(info.received, size, sizeof(size));
    format_time((unsigned)(ms / 1000), secs, sizeof(secs));
    format_speed(bytes_per_second(info.received, ms), avg, sizeof(avg));
    const char *next = cia ? "Find it on the HOME Menu."
                     : system_is_3ds(cat) ? "In GodMode9, open the file and pick \"Build CIA from file\" to install it."
                     : "Start it from TWiLight Menu++.";
    snprintf(body, sizeof(body), UI_HI "%s\n" UI_DIM "%s in %s  \xC2\xB7  %s average\n\n%s",
             entry_name(e), size, secs, avg, next);
    ui_message(UI_TONE_OK, cia ? "Installed" : "Saved", body);
    return false;
}

static void install(Catalog *cat, const CatEntry *entry) {
    CatEntry e = *entry;  // the window may be refetched meanwhile
    CatPlan plan = cat_plan_install(cat->systems[cat->sys], &e);

    if (plan.kind == CAT_PLAN_NONE) {
        char body[480];
        snprintf(body, sizeof(body), UI_HI "%s\n\n%s", e.filename, plan.reason);
        ui_message(UI_TONE_ERR, "Can't install this game", body);
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
    Catalog *cat = calloc(1, sizeof(Catalog));
    CatEntry *win = cat ? malloc(CAT_WINDOW * sizeof(CatEntry)) : NULL;
    if (!win) {
        free(cat);
        ui_message(UI_TONE_ERR, "Game Catalog", "Out of memory.");
        return false;
    }
    cat->config = config;
    cat->win = win;
    cat->total = -1;

    UiBackdrop prev = ui_set_backdrop(draw_top, draw_bottom, cat);
    ui_busy("Game Catalog", "Contacting the server...");
    if (!load_systems(cat)) {
        char text[192];
        snprintf(text, sizeof(text), "%s\n\n" UI_DIM "Check WiFi and the server URL.", cat->error);
        cat->error[0] = '\0';
        ui_message(UI_TONE_ERR, "Game Catalog", text);
        goto done;
    }
    if (cat->nsystems == 0) {
        ui_message(UI_TONE_INFO, "Game Catalog", "The server has no 3DS or DS games.");
        goto done;
    }

    ui_busy("Game Catalog", "Looking for installed games...");
    cat_cia_map_load(&cat->cia_map, CIA_MAP_PATH);
    load_installed_titles(cat);
    scan_installed(cat);
    reset_list(cat);
    hidSetRepeatParameters(20, 4);

    while (aptMainLoop()) {
        draw_frame(cat);
        hidScanInput();
        u32 down = hidKeysDown();
        u32 rep = hidKeysDownRepeat();

        if (down & KEY_B) break;

        if (rep & KEY_DOWN) move_to(cat, cat->selected + 1, true);
        if (rep & KEY_UP) move_to(cat, cat->selected - 1, true);
        if (rep & KEY_RIGHT) move_to(cat, cat->selected + CAT_ROWS, false);
        if (rep & KEY_LEFT) move_to(cat, cat->selected - CAT_ROWS, false);
        if (rep & KEY_R) move_to(cat, cat->selected + CAT_JUMP, false);
        if (rep & KEY_L) move_to(cat, cat->selected - CAT_JUMP, false);
        if ((down & KEY_TOUCH) && cat->total > 0) {
            int i = ui_list_touch(CAT_LIST_Y, CAT_ROWS, CAT_ROW_H, cat->total, cat->scroll);
            if (i >= 0) move_to(cat, i, false);
        }

        if (down & KEY_Y) {
            cat->ra_only = !cat->ra_only;
            reset_list(cat);
        }
        if (down & KEY_X) {
            if (edit_search(cat->search, sizeof(cat->search))) reset_list(cat);
        }
        if ((down & KEY_START) && cat->search[0]) {
            cat->search[0] = '\0';
            reset_list(cat);
        }
        if ((down & KEY_SELECT) && cat->nsystems > 1) {
            cat->sys = (cat->sys + 1) % cat->nsystems;
            scan_installed(cat);
            reset_list(cat);
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
        }
    }

done:;
    ui_restore_backdrop(prev);
    bool rescan = cat->nds_installed;
    cat_names_free(&cat->files);
    cat_cia_map_free(&cat->cia_map);
    free(cat->titles);
    free(cat->win);
    free(cat);
    return rescan;
}
