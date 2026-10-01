/*
 * Vita Save Sync - Main
 *
 * Syncs PS Vita and PSP-emulated saves with the Save Sync server over WiFi,
 * and installs PSP / PS1 games from the server's ROM catalog into
 * Adrenaline's PSP tree.  Uses the 3DSS v3 bundle format (ASCII title_id
 * for product codes).
 *
 * Build requirements:
 *   - VitaSDK toolchain (https://vitasdk.org/), including vita2d
 *   - zlib (bundled under source/zlib)
 *
 * Build:
 *   mkdir build && cd build
 *   cmake .. && make
 *
 * Install:
 *   The vitasync.vpk can be installed via VitaShell.
 *
 * Config file (created by user):
 *   ux0:data/vitasync/config.txt
 *   server_url=http://192.168.1.100:8000
 *   api_key=your-secret-key
 *   scan_vita=1
 *   scan_psp_emu=1
 *   pspemu_root=ux0:pspemu
 *
 * WiFi: the Vita OS manages WiFi. Connect via Settings > Network > WiFi
 * before launching this app.
 *
 * Views (tabs, L / R cycle): Saves -> ROM Catalog -> Downloads -> Settings,
 * drawn with vita2d (see ui.c / gui.c).  Controls are the GameSync scheme
 * shared by every console client:
 *   Up / Down       one row (held: repeats)
 *   Left / Right    page up / down
 *   L / R           previous / next tab
 *   SELECT          sub-tab (PSP / PS1 in the catalog)
 *   Cross           confirm / primary action      Circle  cancel / back / pause
 *   Square          secondary (Sync all, Clear finished)
 *   Triangle        details
 *   START           exit (asks first)
 * Cross / Circle are read as physical buttons, so the Japanese "Circle =
 * enter" system setting never swaps them.
 */

#include <string.h>
#include <stdio.h>
#include <stdlib.h>
#include <psp2/kernel/processmgr.h>
#include <psp2/kernel/threadmgr.h>
#include <psp2/ctrl.h>

#include "catcache.h"
#include "common.h"
#include "config.h"
#include "downloads.h"
#include "roms.h"
#include "saves.h"
#include "network.h"
#include "sync.h"
#include "ui.h"

/* Rows the list views show; paging and scrolling follow it. */
#define LIST_VISIBLE UI_LIST_ROWS

static void sync_progress(const char *msg) { ui_busy("%s", msg); }

static int title_compare(const void *a, const void *b) {
    const TitleInfo *ta = (const TitleInfo *)a;
    const TitleInfo *tb = (const TitleInfo *)b;
    /* Vita native saves before PSP emu saves */
    if (ta->platform != tb->platform)
        return (ta->platform == PLATFORM_VITA) ? -1 : 1;
    return strcasecmp(ta->name, tb->name);
}

/* Wait until all buttons are released, then return 0 for prev_buttons.
 * Call after any blocking UI call (ui_notice / ui_confirm) so the
 * button that dismissed the dialog doesn't fire as a 'just pressed'
 * event on the very next frame. */
static uint32_t drain_buttons(void) {
    SceCtrlData pad;
    do {
        sceCtrlReadBufferPositive2(0, &pad, 1);
        sceKernelDelayThread(16000);
    } while (pad.buttons != 0);
    return 0;
}

static SyncState g_state;
static bool g_has_wifi = false;
static int g_selected = 0;
static int g_scroll   = 0;

/* Multi-view state.  Each view tracks its own selection/scroll. */
static AppView      g_app_view = APP_VIEW_SAVES;
static RomCatalog   g_rom_catalog;
static DownloadList g_downloads;
static int g_rom_selected = 0;
static int g_rom_scroll   = 0;
static int g_dl_selected  = 0;
static int g_dl_scroll    = 0;
static int g_set_selected = 0;
static int g_set_scroll   = 0;

/* Shoulder buttons.  The *2 ctrl readers report the Vita's own L/R as
 * L1/R1, older firmware / emulators as LTRIGGER/RTRIGGER; accept both so
 * the handheld's shoulders always switch tabs (on a PSTV this means a
 * DualShock's L2/R2 do too). */
#define BTN_L (SCE_CTRL_L1 | SCE_CTRL_LTRIGGER)
#define BTN_R (SCE_CTRL_R1 | SCE_CTRL_RTRIGGER)
#define BTN_DIRS (SCE_CTRL_UP | SCE_CTRL_DOWN | SCE_CTRL_LEFT | SCE_CTRL_RIGHT)

/* Catalog systems the PSP emulator can run.  SELECT in the ROMs view
 * toggles between them. */
static const char *G_ROM_SYSTEMS[] = { "PSP", "PS1" };
#define G_ROM_SYSTEM_COUNT ((int)(sizeof(G_ROM_SYSTEMS) / sizeof(G_ROM_SYSTEMS[0])))
static int g_rom_system_index = 0;

/* Where the catalog rows come from this session (see catalog_sync). */
typedef enum {
    CAT_MODE_NONE = 0,  /* not loaded yet */
    CAT_MODE_CACHED,    /* fingerprints checked, rows from the cache file */
    CAT_MODE_OFFLINE,   /* server unreachable: last cached copy */
    CAT_MODE_LIVE,      /* server has no fingerprints route: fetch, no cache */
} CatMode;
static CatMode g_cat_mode = CAT_MODE_NONE;
/* A stale system whose refetch failed this session (shown live / as an
 * error instead of from the cache). */
static bool g_cat_failed[G_ROM_SYSTEM_COUNT];
static char g_cache_info[160];
/* Response buffer for catalog pages and the fingerprints body. */
static char g_catalog_scratch[1024 * 1024];

/* Live progress for the active download — written by the network
 * progress callback, read by ui_draw_downloads. */
static volatile bool     g_active_in_progress = false;
static volatile uint64_t g_active_downloaded  = 0;
static volatile uint64_t g_active_total       = 0;
static volatile uint64_t g_active_bps         = 0;
static volatile bool     g_pause_requested    = false;
static char              g_active_rom_id[ROM_ID_LEN] = {0};

/* Speed sampler — re-anchored every ~2 s for a stable reading. */
static uint64_t g_dl_speed_anchor_bytes = 0;
static uint64_t g_dl_speed_anchor_us    = 0;

/* Edge-detect CIRCLE during an active download for pause. */
static uint32_t g_dl_prev_buttons = 0;

/* Clamp a list cursor and keep it inside the visible window. */
static void clamp_cursor(int *selected, int *scroll, int total, int visible) {
    if (total <= 0) { *selected = 0; *scroll = 0; return; }
    if (*selected >= total) *selected = total - 1;
    if (*selected < 0) *selected = 0;
    if (*selected < *scroll) *scroll = *selected;
    if (*selected >= *scroll + visible) *scroll = *selected - visible + 1;
    if (*scroll < 0) *scroll = 0;
}

/* ===== D-pad with hold-to-repeat ===== */

#define NAV_DELAY_US  350000ULL   /* first repeat */
#define NAV_RATE_US    70000ULL   /* then every */

static bool g_nav_repeat = false;   /* the last nav_buttons() result was a repeat */

/* Directions that fire this frame: a fresh press, or the held direction
 * after the repeat delay. */
static uint32_t nav_buttons(uint32_t held, uint32_t just) {
    static uint32_t dir = 0;
    static uint64_t next_us = 0;
    uint64_t now = sceKernelGetProcessTimeWide();
    g_nav_repeat = false;
    if (just & BTN_DIRS) {
        dir = just & BTN_DIRS;
        next_us = now + NAV_DELAY_US;
        return dir;
    }
    if (!(held & dir)) { dir = 0; return 0; }
    if (now >= next_us) {
        next_us = now + NAV_RATE_US;
        g_nav_repeat = true;
        return dir & held;
    }
    return 0;
}

/* Up / Down one row (wrapping on a fresh press, stopping at the ends
 * while held), Left / Right one page. */
static void list_nav(int *selected, int *scroll, int total, uint32_t nav) {
    if (total <= 0 || !nav) return;
    if (nav & SCE_CTRL_DOWN) {
        if (*selected + 1 < total) (*selected)++;
        else if (!g_nav_repeat) *selected = 0;
    }
    if (nav & SCE_CTRL_UP) {
        if (*selected > 0) (*selected)--;
        else if (!g_nav_repeat) *selected = total - 1;
    }
    if (nav & SCE_CTRL_RIGHT) *selected += LIST_VISIBLE;
    if (nav & SCE_CTRL_LEFT)  *selected -= LIST_VISIBLE;
    clamp_cursor(selected, scroll, total, LIST_VISIBLE);
}

static void format_size(uint64_t bytes, char *out, size_t size) {
    if (bytes >= 1024ULL * 1024 * 1024)
        snprintf(out, size, "%.2f GB", (double)bytes / (1024.0 * 1024 * 1024));
    else if (bytes >= 1024ULL * 1024)
        snprintf(out, size, "%.1f MB", (double)bytes / (1024.0 * 1024));
    else if (bytes >= 1024ULL)
        snprintf(out, size, "%.0f KB", (double)bytes / 1024.0);
    else
        snprintf(out, size, "%llu B", (unsigned long long)bytes);
}

/* ===== Save sync helpers ===== */

static const char SYNC_ERROR_HELP[] =
    "-2 = can't read save files\n"
    "-3 = bundle format error\n"
    "-4 = network / server error\n"
    "-5 = can't write save files\n\n"
    "See ux0:data/vitasync/sync_diag.txt for details.";

/* Show the compare card for `requested` on save `idx` and run whatever
 * the user picks there. */
static void confirm_and_sync(int idx, SyncAction requested) {
    TitleInfo *title = &g_state.titles[idx];

    char server_hash[65] = "";
    uint32_t server_size = 0;
    char server_last_sync[32] = "";
    network_get_save_info(&g_state, title->game_id, server_hash, &server_size,
                          server_last_sync);

    int choice = ui_confirm(title, requested, server_hash, server_size, server_last_sync);
    if (choice < 0) return;

    SyncAction action = (SyncAction)choice;
    bool up = (action == SYNC_UPLOAD);
    ui_busy("%s %s...", up ? "Uploading" : "Downloading", title->game_id);
    int r = sync_execute(&g_state, idx, action);
    ui_invalidate_sync_state();
    if (r == 0)
        ui_notice(UI_TONE_OK, up ? "Upload complete" : "Download complete",
                  up ? "The server now has this Vita's save." :
                       "The server's save is now on this Vita.");
    else
        ui_notice(UI_TONE_ERR, up ? "Upload failed" : "Download failed",
                  "Error code %d.\n\n%s", r, SYNC_ERROR_HELP);
}

/* ===== ROM download helpers (mirror of the PSP client) ===== */

static int rom_progress64_cb(uint64_t downloaded, uint64_t total) {
    g_active_downloaded = downloaded;
    if (total > 0) g_active_total = total;

    /* CIRCLE press -> pause (the .part is kept; Cross resumes).  Edge-
     * detect via prev_buttons mask. */
    SceCtrlData pad;
    sceCtrlPeekBufferPositive2(0, &pad, 1);
    uint32_t just = pad.buttons & ~g_dl_prev_buttons;
    g_dl_prev_buttons = pad.buttons;
    if (just & SCE_CTRL_CIRCLE) g_pause_requested = true;
    if (g_pause_requested) return 1;

    /* Speed sample every ~2 s. */
    uint64_t now_us = sceKernelGetProcessTimeWide();
    if (g_dl_speed_anchor_us == 0) {
        g_dl_speed_anchor_us    = now_us;
        g_dl_speed_anchor_bytes = downloaded;
    } else if (now_us - g_dl_speed_anchor_us >= 2000000ULL) {
        uint64_t db = (downloaded > g_dl_speed_anchor_bytes)
                    ? downloaded - g_dl_speed_anchor_bytes : 0;
        uint64_t ds = now_us - g_dl_speed_anchor_us;
        g_active_bps = (db * 1000000ULL) / ds;
        g_dl_speed_anchor_us    = now_us;
        g_dl_speed_anchor_bytes = downloaded;
    }

    /* Repaint the progress at ~4 Hz without waiting for vblank, so the
     * transfer itself never stalls on the display. */
    if (g_app_view == APP_VIEW_DOWNLOADS) {
        static uint64_t last_draw_us = 0;
        bool first = (last_draw_us == 0);
        bool finished = (total > 0 && downloaded >= total);
        if (first || finished || (now_us - last_draw_us) >= 250000ULL) {
            last_draw_us = now_us;
            ui_draw_progress_partial(&g_downloads,
                                     g_active_downloaded,
                                     g_active_total,
                                     g_active_bps);
        }
    }
    return 0;
}

static void run_download(SyncState *state, DownloadEntry *e) {
    if (!state || !e) return;

    roms_ensure_target_dirs();

    /* Auto-switch to Downloads view so the user sees progress. */
    if (g_app_view != APP_VIEW_DOWNLOADS) {
        g_app_view = APP_VIEW_DOWNLOADS;
        for (int i = 0; i < g_downloads.count; i++) {
            if (strcmp(g_downloads.items[i].rom_id, e->rom_id) == 0) {
                g_dl_selected = i;
                clamp_cursor(&g_dl_selected, &g_dl_scroll, g_downloads.count,
                             LIST_VISIBLE);
                break;
            }
        }
    }

    /* Reset speed sampler + pad mask for this download. */
    g_dl_speed_anchor_bytes = 0;
    g_dl_speed_anchor_us    = 0;
    g_active_bps            = 0;
    {
        SceCtrlData pad;
        sceCtrlPeekBufferPositive2(0, &pad, 1);
        g_dl_prev_buttons = pad.buttons;
    }

    /* Pre-create the per-game folder (PSP/GAME/<id>/) so the .part
     * file can be opened inside it. */
    {
        char parent[DOWNLOAD_PATH_LEN];
        snprintf(parent, sizeof(parent), "%s", e->target_path);
        char *slash = strrchr(parent, '/');
        if (slash) {
            *slash = '\0';
            roms_mkdir_p(parent);
        }
    }

    g_active_in_progress = true;
    g_active_downloaded  = e->offset;
    g_active_total       = e->total;
    g_pause_requested    = false;
    strncpy(g_active_rom_id, e->rom_id, sizeof(g_active_rom_id) - 1);
    g_active_rom_id[sizeof(g_active_rom_id) - 1] = '\0';

    e->status = DL_STATUS_ACTIVE;

    /* One full repaint up front; progress ticks repaint from here on. */
    const char *fmt = e->extract_format[0] ? e->extract_format : "raw";
    char status_line[128];
    if (strcmp(fmt, "raw") == 0)
        snprintf(status_line, sizeof(status_line), "Downloading");
    else
        snprintf(status_line, sizeof(status_line),
                 "Converting to %s on the server", fmt);
    ui_draw_downloads(&g_downloads, g_dl_selected, g_dl_scroll,
                      status_line, true,
                      g_active_downloaded, g_active_total,
                      g_active_bps, g_app_view);

    network_set_progress64_cb(rom_progress64_cb);
    uint64_t total_seen = 0;
    int rc = network_download_rom_resumable(state, e->rom_id,
                                            e->extract_format,
                                            e->target_path,
                                            e->offset, &total_seen);
    network_set_progress64_cb(NULL);

    e->offset = g_active_downloaded;
    if (total_seen > 0) e->total = total_seen;

    g_active_in_progress = false;
    g_pause_requested    = false;
    g_active_rom_id[0]   = '\0';

    /* Repaint once with the final state so the dialog's backdrop matches */
    e->status = (rc == 0) ? DL_STATUS_COMPLETED :
                (rc == 1) ? DL_STATUS_PAUSED : DL_STATUS_ERROR;
    if (rc == 0) e->offset = e->total;
    ui_draw_downloads(&g_downloads, g_dl_selected, g_dl_scroll,
                      rc == 0 ? "Download finished" :
                      rc == 1 ? "Paused - Cross resumes" : "Download failed",
                      false, 0, 0, 0, g_app_view);

    if (rc == 0) {
        ui_notice(UI_TONE_OK, "Download complete", "%s\n\nSaved to:\n%s",
                  e->name[0] ? e->name : e->filename, e->target_path);
    } else if (rc == 1) {
        ui_toast(UI_TONE_WARN, "Paused %s", e->name[0] ? e->name : e->filename);
    } else {
        int http = network_last_download_status();
        if (rc == -3 && http == 503) {
            ui_notice(UI_TONE_ERR, "Server can't convert this ROM",
                      "HTTP 503 for %s.\n\n"
                      "The server needs %s configured (see the server's ROM settings).",
                      e->filename,
                      strcmp(e->extract_format, "eboot") == 0
                          ? "SYNC_ROM_PS1_EBOOT_COMMAND (pop-fe)"
                          : "chdman / maxcso");
        } else if (rc == -3) {
            ui_notice(UI_TONE_ERR, "Download rejected",
                      "The server answered HTTP %d for:\n%s", http, e->filename);
        } else if (rc == -2) {
            ui_notice(UI_TONE_ERR, "Can't write the file",
                      "%s\n\nCheck free space and pspemu_root.", e->target_path);
        } else {
            ui_notice(UI_TONE_ERR, "Download failed",
                      "Error code %d for:\n%s", rc, e->filename);
        }
    }
    downloads_save(&g_downloads);
}


/* ===== ROM catalog: cached by fingerprint (see catcache.h) ===== */

typedef struct {
    int refreshed, unchanged, gone, failed;
    int rescan;          /* -1 not asked, 0 failed / refused, 1 done */
    int rescan_count, rescan_status;
} CatSyncResult;

static void update_cache_info(void) {
    if (g_cat_mode == CAT_MODE_LIVE) {
        snprintf(g_cache_info, sizeof(g_cache_info),
                 "Not used (server has no fingerprints)");
        return;
    }
    int pos = 0;
    g_cache_info[0] = '\0';
    for (int i = 0; i < G_ROM_SYSTEM_COUNT; i++) {
        int count = 0;
        if (!catcache_info(G_ROM_SYSTEMS[i], NULL, 0, &count)) continue;
        pos += snprintf(g_cache_info + pos, sizeof(g_cache_info) - pos, "%s%s %d game%s",
                        pos ? ", " : "", G_ROM_SYSTEMS[i], count, count == 1 ? "" : "s");
        if (pos >= (int)sizeof(g_cache_info)) break;
    }
    if (!g_cache_info[0]) snprintf(g_cache_info, sizeof(g_cache_info), "Empty");
}

/* Bring the cache in line with the server: ask for the per-system
 * fingerprints and refetch only the systems whose fingerprint moved or
 * that aren't cached yet.  `force` (Settings > Refresh catalog) first
 * asks the server to rescan its ROM folder, then refetches everything.
 * Sets g_cat_mode; g_rom_catalog is used as the fetch buffer, so call
 * catalog_show_system() afterwards. */
static void catalog_sync(bool force, CatSyncResult *res) {
    memset(res, 0, sizeof(*res));
    res->rescan = -1;
    for (int i = 0; i < G_ROM_SYSTEM_COUNT; i++) g_cat_failed[i] = false;

    if (!g_has_wifi) {
        g_cat_mode = CAT_MODE_OFFLINE;
        return;
    }
    if (force) {
        ui_busy("Asking the server to rescan its ROM folder...");
        int count = -1, status = 0;
        int rc = network_trigger_rom_scan(&g_state, &count, &status);
        res->rescan = (rc == 0) ? 1 : 0;
        res->rescan_count = count;
        res->rescan_status = status;
    }

    ui_busy("Checking the server's catalog...");
    int status = 0;
    int n = network_fetch_rom_fingerprints(&g_state, g_catalog_scratch,
                                           sizeof(g_catalog_scratch), &status);
    if (status == 404 || status == 405) {
        /* Server predates fingerprints: fetch live, cache nothing. */
        g_cat_mode = CAT_MODE_LIVE;
        if (force) catcache_clear();
        return;
    }
    static CatFingerprints fps;
    if (n <= 0 || !catcache_parse_fingerprints(g_catalog_scratch, (size_t)n, &fps)) {
        /* Unreachable: keep (and show) the last copy, even on a refresh. */
        g_cat_mode = CAT_MODE_OFFLINE;
        return;
    }
    g_cat_mode = CAT_MODE_CACHED;
    if (force) catcache_clear();

    for (int i = 0; i < G_ROM_SYSTEM_COUNT; i++) {
        const char *sys = G_ROM_SYSTEMS[i];
        CatPlan plan = catcache_plan(sys, &fps);
        if (plan == CATCACHE_FRESH) { res->unchanged++; continue; }
        if (plan == CATCACHE_GONE)  { res->gone++; continue; }
        ui_busy("Fetching the %s catalog...", sys);
        memset(&g_rom_catalog, 0, sizeof(g_rom_catalog));
        bool ok = roms_fetch_catalog(&g_state, sys, g_catalog_scratch,
                                     sizeof(g_catalog_scratch), &g_rom_catalog);
        const CatFingerprint *fp = catcache_find_fp(&fps, sys);
        if (ok && !g_rom_catalog.partial && fp &&
            catcache_put(sys, fp->fingerprint, &g_rom_catalog)) {
            res->refreshed++;
        } else {
            /* Keep whatever older copy there is; show this one live. */
            res->failed++;
            g_cat_failed[i] = true;
        }
    }
}

/* Fill g_rom_catalog with the current system's rows. */
static void catalog_show_system(void) {
    const char *sys = G_ROM_SYSTEMS[g_rom_system_index];
    memset(&g_rom_catalog, 0, sizeof(g_rom_catalog));
    g_rom_selected = 0;
    g_rom_scroll   = 0;

    bool live = (g_cat_mode == CAT_MODE_LIVE);
    if (!live) {
        if (catcache_load(sys, &g_rom_catalog)) return;
        /* Not cached: a failed refetch is retried live; otherwise the
         * server simply lists nothing for this system. */
        if (g_cat_mode == CAT_MODE_CACHED && g_cat_failed[g_rom_system_index])
            live = true;
        else if (g_cat_mode == CAT_MODE_OFFLINE)
            snprintf(g_rom_catalog.last_error, sizeof(g_rom_catalog.last_error),
                     g_has_wifi ? "The server can't be reached and there is no cached copy."
                                : "Offline, and there is no cached copy of this catalog.");
    }
    if (live && g_has_wifi) {
        ui_busy("Fetching the %s catalog...", sys);
        roms_fetch_catalog(&g_state, sys, g_catalog_scratch, sizeof(g_catalog_scratch),
                           &g_rom_catalog);
    }
}

/* First visit to the catalog this session. */
static void catalog_open(void) {
    CatSyncResult r;
    catalog_sync(false, &r);
    catalog_show_system();
    update_cache_info();
    if (g_cat_mode == CAT_MODE_OFFLINE && g_has_wifi)
        ui_toast(UI_TONE_WARN, "Server unreachable - showing the cached catalog");
    else if (g_cat_mode == CAT_MODE_OFFLINE)
        ui_toast(UI_TONE_WARN, "Offline - showing the cached catalog");
    else if (r.failed > 0)
        ui_toast(UI_TONE_ERR, "Couldn't refresh %d system%s", r.failed, r.failed == 1 ? "" : "s");
    else if (r.refreshed > 0)
        ui_toast(UI_TONE_OK, "Catalog: refreshed %d system%s, %d unchanged", r.refreshed,
                 r.refreshed == 1 ? "" : "s", r.unchanged);
}

/* Settings > Refresh catalog: the MiSTer "force" path. */
static void catalog_refresh(void) {
    if (!g_has_wifi) {
        ui_notice(UI_TONE_ERR, "Can't refresh the catalog",
                  "The server isn't reachable, so the cached catalog was kept.");
        return;
    }
    CatSyncResult r;
    catalog_sync(true, &r);
    catalog_show_system();
    update_cache_info();

    char rescan[128];
    if (r.rescan == 1 && r.rescan_count >= 0)
        snprintf(rescan, sizeof(rescan), "done (%d ROMs on the server)", r.rescan_count);
    else if (r.rescan == 1)
        snprintf(rescan, sizeof(rescan), "done");
    else if (r.rescan_status == 403)
        snprintf(rescan, sizeof(rescan), "not allowed for this key - refetched anyway");
    else if (r.rescan_status > 0)
        snprintf(rescan, sizeof(rescan), "failed (HTTP %d) - refetched anyway", r.rescan_status);
    else
        snprintf(rescan, sizeof(rescan), "no answer - refetched anyway");

    if (g_cat_mode == CAT_MODE_OFFLINE) {
        ui_notice(UI_TONE_ERR, "Can't refresh the catalog",
                  "Server rescan: %s\n\nThe catalog couldn't be fetched, so the cached "
                  "copy was kept.", rescan);
    } else if (g_cat_mode == CAT_MODE_LIVE) {
        ui_notice(UI_TONE_OK, "Catalog refreshed",
                  "Server rescan: %s\n\n%s: %d game%s.\n\nThis server has no catalog "
                  "fingerprints, so the catalog is fetched fresh every time and not "
                  "kept on the Vita.", rescan, G_ROM_SYSTEMS[g_rom_system_index],
                  g_rom_catalog.count, g_rom_catalog.count == 1 ? "" : "s");
    } else {
        ui_notice(r.failed ? UI_TONE_WARN : UI_TONE_OK,
                  r.failed ? "Catalog partly refreshed" : "Catalog refreshed",
                  "Server rescan: %s\n\nRefetched %d system%s%s.\nCached now: %s",
                  rescan, r.refreshed, r.refreshed == 1 ? "" : "s",
                  r.failed ? " (some failed - try again)" : "", g_cache_info);
    }
}

/* ===== Details cards (Triangle) ===== */

static void show_save_details(int idx) {
    const TitleInfo *t = &g_state.titles[idx];
    char local[160], server[160], last[65];

    if (t->server_only) {
        snprintf(local, sizeof(local), "Not on this Vita yet");
    } else {
        char sz[24];
        format_size(t->total_size, sz, sizeof(sz));
        snprintf(local, sizeof(local), "%s in %d file%s\n%s", sz, t->file_count,
                 t->file_count == 1 ? "" : "s", t->save_dir);
    }

    if (!g_has_wifi) {
        snprintf(server, sizeof(server), "Offline");
    } else {
        ui_busy("Asking the server about %s...", t->game_id);
        char hash[65] = "", date[32] = "";
        uint32_t size = 0;
        int rc = network_get_save_info(&g_state, t->game_id, hash, &size, date);
        if (rc == 0) {
            char sz[24];
            format_size(size, sz, sizeof(sz));
            if (strlen(date) >= 16 && date[10] == 'T') {
                date[10] = ' ';
                date[16] = '\0';
            }
            snprintf(server, sizeof(server), "%s%s%s", sz, date[0] ? ", saved " : "", date);
        } else if (rc == 1) {
            snprintf(server, sizeof(server), "No save on the server");
        } else {
            snprintf(server, sizeof(server), "The server didn't answer");
        }
    }

    bool synced = config_get_last_hash(t->game_id, last);
    const char *platform = t->platform != PLATFORM_PSP_EMU ? "PS Vita" :
                           t->is_psx ? "PS1 (PSone Classic)" : "PSP (emulated)";
    ui_notice(UI_TONE_INFO, t->name[0] ? t->name : t->game_id,
              "Game ID: %s\nPlatform: %s\n\nThis Vita: %s\n\nServer: %s\n\nLast sync: %s",
              t->game_id, platform, local, server,
              synced ? "synced with this Vita before" : "never synced from this Vita");
}

static void show_rom_details(const RomEntry *r) {
    char sz[24], target[DOWNLOAD_PATH_LEN] = "-", status[64];
    format_size(r->size, sz, sizeof(sz));
    const char *fmt = roms_preferred_extract_format(r);
    const char *as = roms_entry_unsupported(r)  ? "Not installable (loose files on the server)" :
                     strcmp(fmt, "eboot") == 0  ? "EBOOT.PBP (server converts)" :
                     strcmp(fmt, "cso") == 0    ? "CSO (server converts the CHD)" :
                                                  "As-is (no conversion)";
    if (!roms_entry_unsupported(r)) roms_resolve_target_path(r, target, sizeof(target));
    const DownloadEntry *e = downloads_find(&g_downloads, r->rom_id);
    if (!e) snprintf(status, sizeof(status), "Not installed");
    else snprintf(status, sizeof(status), "%s", downloads_status_to_str(e->status));
    char discs[24] = "";
    if (r->disc_total > 1) snprintf(discs, sizeof(discs), " (%d discs)", r->disc_total);
    ui_notice(UI_TONE_INFO, r->name[0] ? r->name : r->filename,
              "File: %s\nSize: %s%s\nSystem: %s   Serial: %s\n\nInstalls as: %s\nGoes to: %s\n\n"
              "Status: %s",
              r->filename, sz, discs, r->system, r->title_id[0] ? r->title_id : "-", as,
              target, status);
}

/* ===== Main ===== */

int main(void) {
    ui_init();

    memset(&g_state, 0, sizeof(SyncState));
    g_state.scan_vita_saves    = true;
    g_state.scan_psp_emu_saves = true;

    /* Load config */
    char err_buf[512];
    ui_busy("Loading config...");

    if (!config_load(&g_state, err_buf, sizeof(err_buf))) {
        ui_notice(UI_TONE_ERR, "Config error",
                  "%s\n\nEdit %s with:\nserver_url=http://host:8000\napi_key=key\n\n"
                  "Press Cross to exit.",
                  err_buf, CONFIG_PATH);
        ui_term();
        sceKernelExitProcess(0);
        return 0;
    }

    config_load_console_id(&g_state);
    roms_set_pspemu_root(g_state.pspemu_root);

    /* Initialize network */
    ui_busy("Initializing network...");

    if (network_init() == 0) {
        ui_busy("Checking WiFi connection...");
        if (network_connect() == 0) {
            ui_busy("Checking server...");
            g_has_wifi = network_check_server(&g_state);
            g_state.network_connected = g_has_wifi;
            ui_set_online(g_has_wifi ? 1 : 0);
            if (!g_has_wifi) {
                ui_notice(UI_TONE_WARN, "Server unreachable",
                          "Cannot reach the server at:\n%s\n\n"
                          "Check server_url in config.txt. Continuing offline.",
                          g_state.server_url);
            }
        } else {
            ui_set_online(0);
            ui_notice(UI_TONE_WARN, "WiFi not connected",
                      "Go to Settings > Network > Wi-Fi and connect before launching.\n\n"
                      "Continuing offline.");
        }
    } else {
        ui_set_online(0);
        ui_notice(UI_TONE_ERR, "Network unavailable",
                  "Network init failed. Continuing offline.");
    }

    /* Scan saves */
    ui_busy("Scanning saves...");
    saves_scan(&g_state);

    if (g_has_wifi) {
        ui_busy("Checking server saves...");
        network_merge_server_titles(&g_state);
    }

    if (g_has_wifi && g_state.num_titles > 0) {
        ui_busy("Fetching game names...");
        network_fetch_names(&g_state);
    }

    if (g_state.num_titles > 1)
        qsort(g_state.titles, g_state.num_titles, sizeof(TitleInfo), title_compare);

    if (g_state.num_titles == 0) {
        /* No saves is no longer fatal — the ROM catalog still works. */
        ui_notice(UI_TONE_INFO, "No saves found",
                  "Nothing locally or on the server.\n\n"
                  "Vita saves: ux0:user/00/savedata/\n"
                  "PSP saves: ux0:pspemu/PSP/SAVEDATA/\n\n"
                  "Check scan_vita and scan_psp_emu in config.txt. "
                  "Diagnostic log: ux0:data/vitasync/diag.txt\n\n"
                  "Press R to open the ROM Catalog.");
    } else {
        ui_toast(UI_TONE_OK, "Found %d save%s", g_state.num_titles,
                 g_state.num_titles == 1 ? "" : "s");
    }

    /* ROM downloads init — directories + persisted queue. */
    roms_ensure_target_dirs();
    memset(&g_rom_catalog, 0, sizeof(g_rom_catalog));
    memset(&g_downloads,   0, sizeof(g_downloads));
    downloads_load(&g_downloads);
    update_cache_info();

    /* Main loop.  Every pass draws one full frame of the current view;
     * the frame waits for vblank, which paces the loop at 60 fps. */
    SceCtrlData pad;
    uint32_t prev_buttons = drain_buttons();

    while (1) {
        sceCtrlReadBufferPositive2(0, &pad, 1);
        uint32_t just = pad.buttons & ~prev_buttons;
        prev_buttons = pad.buttons;
        uint32_t nav = nav_buttons(pad.buttons, just);

        /* START: exit, after asking. */
        if (just & SCE_CTRL_START) {
            bool quit = ui_ask(UI_TONE_WARN, "Exit GameSync?", "Exit",
                               "Close GameSync and go back to the LiveArea.");
            prev_buttons = drain_buttons();
            if (quit) break;
            continue;
        }

        /* L / R: previous / next tab, wrapping, from any view. */
        if (just & (BTN_L | BTN_R)) {
            int dir = (just & BTN_R) ? 1 : -1;
            g_app_view = (AppView)(((int)g_app_view + dir + APP_VIEW_COUNT) % APP_VIEW_COUNT);
            just = 0;
            nav = 0;
        }

        /* ─────────────  Saves view  ───────────── */
        if (g_app_view == APP_VIEW_SAVES) {
            int total = g_state.num_titles;
            list_nav(&g_selected, &g_scroll, total, nav);

            if ((just & (SCE_CTRL_CROSS | SCE_CTRL_SQUARE)) && !g_has_wifi && total > 0)
                ui_toast(UI_TONE_ERR, "Offline - syncing needs the server");

            /* Cross: smart sync (the compare card also offers a forced
             * upload / download). */
            if ((just & SCE_CTRL_CROSS) && g_has_wifi && total > 0) {
                TitleInfo *title = &g_state.titles[g_selected];
                ui_busy("Comparing %s with the server...", title->game_id);
                SyncAction action = sync_decide(&g_state, g_selected);
                ui_invalidate_sync_state();
                confirm_and_sync(g_selected, action);
                prev_buttons = drain_buttons();
            }

            /* Square: sync all saves */
            if ((just & SCE_CTRL_SQUARE) && g_has_wifi && total > 0) {
                if (ui_ask(UI_TONE_ACCENT, "Sync all saves?", "Sync all",
                           "Uploads or downloads every save that changed on one side "
                           "(%d save%s). Conflicts are left for you to pick.",
                           total, total == 1 ? "" : "s")) {
                    SyncSummary summary;
                    sync_auto_all(&g_state, &summary, sync_progress);
                    ui_invalidate_sync_state();
                    ui_sync_summary(&summary);
                }
                prev_buttons = drain_buttons();
            }

            /* Triangle: details */
            if ((just & SCE_CTRL_TRIANGLE) && total > 0) {
                show_save_details(g_selected);
                prev_buttons = drain_buttons();
            }

            ui_draw_list(&g_state, g_selected, g_scroll);
            continue;
        }

        /* ─────────────  ROM Catalog view  ───────────── */
        if (g_app_view == APP_VIEW_ROMS) {
            const char *current_system = G_ROM_SYSTEMS[g_rom_system_index];

            char roms_status[128];
            if (g_cat_mode == CAT_MODE_OFFLINE && g_rom_catalog.count > 0)
                snprintf(roms_status, sizeof(roms_status), "Cached copy - server %s",
                         g_has_wifi ? "unreachable" : "offline");
            else if (!g_has_wifi)
                snprintf(roms_status, sizeof(roms_status),
                         "Offline - catalog needs the server");
            else
                snprintf(roms_status, sizeof(roms_status),
                         "%d in queue | %s",
                         g_downloads.count, g_state.pspemu_root);

            /* First visit: sync the cache with the server, then show. */
            if (g_cat_mode == CAT_MODE_NONE) {
                ui_draw_rom_catalog(&g_rom_catalog, &g_downloads, current_system,
                                    g_rom_selected, g_rom_scroll, roms_status,
                                    g_app_view);
                catalog_open();
                prev_buttons = drain_buttons();
                continue;
            }

            /* SELECT: toggle the system (PSP <-> PS1). */
            if (just & SCE_CTRL_SELECT) {
                g_rom_system_index = (g_rom_system_index + 1) % G_ROM_SYSTEM_COUNT;
                catalog_show_system();
                prev_buttons = drain_buttons();
                continue;
            }

            int total = g_rom_catalog.count;
            list_nav(&g_rom_selected, &g_rom_scroll, total, nav);

            /* Cross: queue + start the selected ROM, or resume it. */
            if ((just & SCE_CTRL_CROSS) && total > 0 && !g_has_wifi)
                ui_toast(UI_TONE_ERR, "Offline - installing needs the server");
            if ((just & SCE_CTRL_CROSS) && total > 0 && g_has_wifi) {
                RomEntry *r = &g_rom_catalog.items[g_rom_selected];
                if (roms_entry_unsupported(r)) {
                    ui_notice(UI_TONE_WARN, "Can't install this game",
                              "%s\n\n"
                              "This PS1 game is a folder of loose files on the server; "
                              "only single-file images (CHD / CUE / BIN) can be "
                              "converted to an EBOOT.PBP.",
                              r->name[0] ? r->name : r->filename);
                } else {
                    DownloadEntry *e = downloads_upsert_from_catalog(&g_downloads, r);
                    if (!e) {
                        ui_notice(UI_TONE_ERR, "Queue full",
                                  "The download queue is full (%d). "
                                  "Clear finished items in Downloads.", DOWNLOAD_MAX);
                    } else if (e->status == DL_STATUS_COMPLETED) {
                        ui_notice(UI_TONE_OK, "Already installed",
                                  "%s\n\nLocation:\n%s",
                                  e->name[0] ? e->name : e->filename,
                                  e->target_path);
                    } else {
                        e->status = (e->offset > 0) ? DL_STATUS_PAUSED
                                                    : DL_STATUS_QUEUED;
                        downloads_save(&g_downloads);
                        run_download(&g_state, e);
                    }
                }
                prev_buttons = drain_buttons();
            }

            /* Triangle: details */
            if ((just & SCE_CTRL_TRIANGLE) && total > 0) {
                show_rom_details(&g_rom_catalog.items[g_rom_selected]);
                prev_buttons = drain_buttons();
            }

            /* A download switches to the Downloads view; draw that next pass. */
            if (g_app_view != APP_VIEW_ROMS) continue;
            ui_draw_rom_catalog(&g_rom_catalog, &g_downloads,
                                current_system,
                                g_rom_selected, g_rom_scroll,
                                roms_status, g_app_view);
            continue;
        }

        /* ─────────────  Downloads view  ───────────── */
        if (g_app_view == APP_VIEW_DOWNLOADS) {
            int total = g_downloads.count;
            list_nav(&g_dl_selected, &g_dl_scroll, total, nav);

            /* Cross: start/resume selected. */
            if ((just & SCE_CTRL_CROSS) && total > 0 && !g_has_wifi)
                ui_toast(UI_TONE_ERR, "Offline - downloads need the server");
            if ((just & SCE_CTRL_CROSS) && total > 0 && g_has_wifi) {
                DownloadEntry *e =
                    (g_dl_selected >= 0 && g_dl_selected < total)
                        ? &g_downloads.items[g_dl_selected]
                        : downloads_next_runnable(&g_downloads);
                if (e && e->status != DL_STATUS_COMPLETED &&
                         e->status != DL_STATUS_ACTIVE)
                {
                    run_download(&g_state, e);
                }
                prev_buttons = drain_buttons();
            }

            /* Circle: cancel the selected download (removes it from the
             * queue and deletes its partial file).  A running transfer is
             * paused with Circle from inside the progress callback. */
            if ((just & SCE_CTRL_CIRCLE) && total > 0 && g_dl_selected < total) {
                DownloadEntry *e = &g_downloads.items[g_dl_selected];
                char rom_id[ROM_ID_LEN];
                snprintf(rom_id, sizeof(rom_id), "%s", e->rom_id);
                bool go = true;
                if (e->status != DL_STATUS_COMPLETED && e->offset > 0) {
                    char done[24];
                    format_size(e->offset, done, sizeof(done));
                    go = ui_ask(UI_TONE_WARN, "Cancel this download?", "Delete it",
                                "%s\n\nThe %s downloaded so far is deleted. "
                                "Circle keeps it paused.",
                                e->name[0] ? e->name : e->filename, done);
                    prev_buttons = drain_buttons();
                }
                if (go) {
                    bool finished = (e->status == DL_STATUS_COMPLETED);
                    downloads_remove(&g_downloads, rom_id);
                    downloads_save(&g_downloads);
                    clamp_cursor(&g_dl_selected, &g_dl_scroll,
                                 g_downloads.count, LIST_VISIBLE);
                    ui_toast(UI_TONE_INFO, finished ? "Removed from the list (game kept)"
                                                    : "Download cancelled");
                }
            }

            /* Square: clear completed entries. */
            if (just & SCE_CTRL_SQUARE) {
                int removed = 0;
                int i = 0;
                while (i < g_downloads.count) {
                    if (g_downloads.items[i].status == DL_STATUS_COMPLETED) {
                        char rom_id[ROM_ID_LEN];
                        strncpy(rom_id, g_downloads.items[i].rom_id, sizeof(rom_id) - 1);
                        rom_id[sizeof(rom_id) - 1] = '\0';
                        downloads_remove(&g_downloads, rom_id);
                        removed++;
                    } else {
                        i++;
                    }
                }
                if (removed > 0) {
                    downloads_save(&g_downloads);
                    clamp_cursor(&g_dl_selected, &g_dl_scroll,
                                 g_downloads.count, LIST_VISIBLE);
                    ui_toast(UI_TONE_OK, "Cleared %d finished item%s", removed,
                             removed == 1 ? "" : "s");
                } else {
                    ui_toast(UI_TONE_INFO, "Nothing finished to clear");
                }
            }

            int installed = 0;
            for (int i = 0; i < g_downloads.count; i++)
                if (g_downloads.items[i].status == DL_STATUS_COMPLETED) installed++;
            char dl_status[128];
            snprintf(dl_status, sizeof(dl_status), "%d waiting, %d installed%s",
                     g_downloads.count - installed, installed,
                     g_has_wifi ? "" : " - offline");
            ui_draw_downloads(&g_downloads, g_dl_selected, g_dl_scroll,
                              dl_status,
                              g_active_in_progress,
                              g_active_downloaded, g_active_total,
                              g_active_bps, g_app_view);
            continue;
        }

        /* ─────────────  Settings view  ───────────── */
        if (g_app_view == APP_VIEW_SETTINGS) {
            list_nav(&g_set_selected, &g_set_scroll, UI_SETTINGS_ROWS, nav);

            if ((just & SCE_CTRL_CROSS) && g_set_selected == UI_SETTINGS_REFRESH) {
                catalog_refresh();
                prev_buttons = drain_buttons();
            }

            ui_draw_settings(&g_state, g_set_selected, g_set_scroll, g_cache_info);
            continue;
        }
    }

    downloads_save(&g_downloads);
    network_cleanup();
    ui_term();
    sceKernelExitProcess(0);
    return 0;
}
