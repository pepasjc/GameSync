/*
 * PSP Save Sync - Main
 *
 * Syncs PSP SAVEDATA saves with the Save Sync server over WiFi.
 * Uses the 3DSS v3 bundle format (string title_id for PSP product codes).
 *
 * Build requirements:
 *   - pspdev toolchain (https://github.com/pspdev/pspdev)
 *   - zlib port for PSP (psp-zlib)
 *
 * Install pspdev:
 *   https://github.com/pspdev/pspdev#installation
 *
 * Build:
 *   export PSPDEV=/usr/local/pspdev
 *   export PSPSDK=$PSPDEV/psp/sdk
 *   export PATH=$PATH:$PSPDEV/bin
 *   make
 *
 * The EBOOT.PBP goes into ms0:/PSP/GAME/pspsync/EBOOT.PBP
 * Config file:   ms0:/PSP/GAME/pspsync/config.txt
 * Sync state:    ms0:/PSP/GAME/pspsync/state.dat
 * Catalog cache: ms0:/PSP/GAME/pspsync/catalog_cache.bin (catcache.h)
 *
 * Controls: L / R switch views (Saves, Catalog, Downloads, Settings),
 * Up / Down rows, Left / Right pages, Cross confirms, Circle cancels,
 * SELECT switches the catalog sub-tab (PSP / PS1), START exits.
 */

#include <pspkernel.h>
#include <pspctrl.h>
#include <psppower.h>
#include <pspthreadman.h>
#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#include <strings.h>

#include <time.h>

#include "catcache.h"
#include "common.h"
#include "config.h"
#include "downloads.h"
#include "roms.h"
#include "saves.h"
#include "network.h"
#include "sync.h"
#include "ui.h"
#include "zip_extract.h"

PSP_MODULE_INFO("PSP Save Sync", 0, 1, 0);
PSP_MAIN_THREAD_ATTR(THREAD_ATTR_USER | THREAD_ATTR_VFPU);
/* Bumped from 4 MB so the ~1.7 MB ROM catalog cache + 1 MB scratch JSON
 * fit comfortably alongside the existing save/bundle buffers. */
PSP_HEAP_SIZE_KB(16384);

/* Kernel callback for EXIT button */
static int exit_callback(int arg1, int arg2, void *common) {
    (void)arg1; (void)arg2; (void)common;
    sceKernelExitGame();
    return 0;
}

static int callback_thread(SceSize args, void *argp) {
    (void)args; (void)argp;
    int cbid = sceKernelCreateCallback("Exit Callback", exit_callback, NULL);
    sceKernelRegisterExitCallback(cbid);
    sceKernelSleepThreadCB();
    return 0;
}

static void setup_callbacks(void) {
    int thid = sceKernelCreateThread("update_thread", callback_thread,
                                     0x11, 0xFA0, 0, NULL);
    if (thid >= 0)
        sceKernelStartThread(thid, 0, NULL);
}

static SyncState g_state;
static int g_selected = 0;
static int g_scroll = 0;
static bool g_online = false;   /* WiFi up and the server answered */

/* Top-level views; L / R cycle Saves -> Catalog -> Downloads ->
 * Settings (wrapping).  Each view tracks its own selected/scroll. */
static AppView      g_app_view = APP_VIEW_SAVES;
static RomCatalog   g_rom_catalog;
static DownloadList g_downloads;
static int g_rom_selected = 0;
static int g_rom_scroll   = 0;
static int g_dl_selected  = 0;
static int g_dl_scroll    = 0;
static int g_set_selected = 0;
static int g_set_scroll   = 0;

/* Catalog sub-tabs (PSP / PS1); SELECT switches. */
static const char *const G_ROM_SYSTEMS[] = { "PSP", "PS1" };
#define G_ROM_SYSTEM_COUNT ((int)(sizeof(G_ROM_SYSTEMS) / sizeof(G_ROM_SYSTEMS[0])))
static int g_rom_system_index = 0;

/* Where the catalog rows come from this session (see catalog_sync).
 * Same structure as the Vita client's main.c. */
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
static char g_catalog_scratch[1 * 1024 * 1024];

/* Live progress for the active download.  Updated from the network
 * progress callback; read by ui_draw_downloads. */
static volatile bool     g_active_in_progress = false;
static volatile uint64_t g_active_downloaded  = 0;
static volatile uint64_t g_active_total       = 0;
static volatile uint64_t g_active_bps         = 0;
static volatile bool     g_pause_requested    = false;
static char              g_active_rom_id[ROM_ID_LEN] = {0};

/* Speed sampler — re-anchored every ~2 s for a stable reading. */
static uint64_t g_dl_speed_anchor_bytes = 0;
static time_t   g_dl_speed_anchor_time  = 0;

/* Edge-detect CIRCLE during an active download for pause. */
static uint32_t g_dl_prev_buttons = 0;


static void sync_progress(const char *msg) { ui_status("%s", msg); }

static int title_compare(const void *a, const void *b) {
    const TitleInfo *ta = (const TitleInfo *)a;
    const TitleInfo *tb = (const TitleInfo *)b;
    return strcasecmp(ta->name, tb->name);
}

/* Keep `*selected` inside the visible window starting at `*scroll`. */
static void clamp_scroll(int selected, int *scroll) {
    if (selected < *scroll)
        *scroll = selected;
    if (selected >= *scroll + UI_LIST_ROWS)
        *scroll = selected - UI_LIST_ROWS + 1;
}

static void draw_downloads_view(bool vsync) {
    ui_draw_downloads(&g_downloads, g_dl_selected, g_dl_scroll,
                      g_active_in_progress,
                      g_active_downloaded, g_active_total,
                      g_active_bps, vsync);
}


/* ===== ROM download helpers (mirror of PS3 client) ===== */

static int rom_progress64_cb(uint64_t downloaded, uint64_t total) {
    g_active_downloaded = downloaded;
    if (total > 0) g_active_total = total;

    /* CIRCLE press -> pause (resume later).  Edge-detect via prev_buttons. */
    SceCtrlData pad;
    sceCtrlPeekBufferPositive(&pad, 1);
    uint32_t just = pad.Buttons & ~g_dl_prev_buttons;
    g_dl_prev_buttons = pad.Buttons;
    if (just & PSP_CTRL_CIRCLE) g_pause_requested = true;
    if (g_pause_requested) return 1;

    /* Speed sample every ~2 s. */
    time_t now = time(NULL);
    if (g_dl_speed_anchor_time == 0) {
        g_dl_speed_anchor_time  = now;
        g_dl_speed_anchor_bytes = downloaded;
    } else if (now - g_dl_speed_anchor_time >= 2) {
        uint64_t db = (downloaded > g_dl_speed_anchor_bytes)
                    ? downloaded - g_dl_speed_anchor_bytes : 0;
        time_t   ds = now - g_dl_speed_anchor_time;
        if (ds > 0) g_active_bps = db / (uint64_t)ds;
        g_dl_speed_anchor_time  = now;
        g_dl_speed_anchor_bytes = downloaded;
    }

    /* Redraw the Downloads view at most ~5 times a second, without
     * waiting for vblank, so the transfer loop is never held up. */
    static uint32_t last_draw_us = 0;
    uint32_t now_us = sceKernelGetSystemTimeLow();
    bool finished = (total > 0 && downloaded >= total);
    if (last_draw_us == 0 || finished || (now_us - last_draw_us) >= 200000U) {
        last_draw_us = now_us ? now_us : 1;
        draw_downloads_view(false);
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
                clamp_scroll(g_dl_selected, &g_dl_scroll);
                break;
            }
        }
    }

    /* Reset speed sampler + pad mask for this download. */
    g_dl_speed_anchor_bytes = 0;
    g_dl_speed_anchor_time  = 0;
    g_active_bps            = 0;
    {
        SceCtrlData pad;
        sceCtrlPeekBufferPositive(&pad, 1);
        g_dl_prev_buttons = pad.Buttons;
    }

    /* Single-file path.  PSP catalog has no bundles today, so we
     * always go through the per-rom-id endpoint with optional
     * ?extract=<fmt>.  Pre-create the per-game subdir for PS1
     * EBOOTs so fopen of .part doesn't fail. */
    {
        char parent[512];
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
    draw_downloads_view(false);

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

    if (rc == 0) {
        e->status = DL_STATUS_COMPLETED;
        ui_message(UI_TONE_OK, "Download complete", "%s\n\nSaved to:\n%s",
                   e->name[0] ? e->name : e->filename, e->target_path);
    } else if (rc == 1) {
        e->status = DL_STATUS_PAUSED;
        ui_toast(UI_TONE_WARN, "Paused %s", e->name[0] ? e->name : e->filename);
    } else {
        e->status = DL_STATUS_ERROR;
        ui_message(UI_TONE_ERR, "Download failed", "Error code %d while downloading:\n%s",
                   rc, e->filename);
    }
    downloads_save(&g_downloads);
}

/* ===== Input ===== */

#define NAV_MASK (PSP_CTRL_UP | PSP_CTRL_DOWN | PSP_CTRL_LEFT | PSP_CTRL_RIGHT)
#define NAV_REPEAT_DELAY 18   /* frames held before the D-pad repeats */
#define NAV_REPEAT_RATE  4    /* frames between repeats */

static uint32_t g_prev_buttons = 0;
static int      g_nav_hold = 0;

/* Forget what is held now, so a button that closed a dialog does not
 * fire again on the view behind it. */
static void input_resync(void) {
    SceCtrlData pad;
    sceCtrlPeekBufferPositive(&pad, 1);
    g_prev_buttons = pad.Buttons;
    g_nav_hold = 0;
}

/* Newly pressed buttons in *just; D-pad presses plus auto-repeat while
 * held in *nav. */
static void input_poll(uint32_t *just, uint32_t *nav) {
    SceCtrlData pad;
    sceCtrlReadBufferPositive(&pad, 1);
    uint32_t pressed = pad.Buttons;
    *just = pressed & ~g_prev_buttons;
    uint32_t dirs = pressed & NAV_MASK;
    if (dirs && dirs == (g_prev_buttons & NAV_MASK)) g_nav_hold++;
    else g_nav_hold = 0;
    *nav = *just & NAV_MASK;
    if (g_nav_hold >= NAV_REPEAT_DELAY &&
        (g_nav_hold - NAV_REPEAT_DELAY) % NAV_REPEAT_RATE == 0)
        *nav |= dirs;
    g_prev_buttons = pressed;
}

/* Up / Down one row (a fresh press wraps around the ends, a held one
 * stops there), Left / Right one page. */
static void list_nav(uint32_t nav, uint32_t just, int total, int *sel, int *scroll) {
    if (total <= 0) { *sel = 0; *scroll = 0; return; }
    if (nav & PSP_CTRL_DOWN) {
        if (*sel + 1 < total) (*sel)++;
        else if (just & PSP_CTRL_DOWN) *sel = 0;
    }
    if (nav & PSP_CTRL_UP) {
        if (*sel > 0) (*sel)--;
        else if (just & PSP_CTRL_UP) *sel = total - 1;
    }
    if (nav & PSP_CTRL_RIGHT) *sel += UI_LIST_ROWS;
    if (nav & PSP_CTRL_LEFT)  *sel -= UI_LIST_ROWS;
    if (*sel >= total) *sel = total - 1;
    if (*sel < 0) *sel = 0;
    clamp_scroll(*sel, scroll);
}

/* START: leave the app after a confirmation. */
static void confirm_exit(void) {
    static const char *const buttons[] = { "CROSS" };
    static const char *const labels[]  = { "Exit" };
    uint32_t b = ui_ask(UI_TONE_WARN, "Exit GameSync", buttons, labels, 1,
                        "Leave GameSync and return to the XMB?%s",
                        downloads_next_runnable(&g_downloads)
                            ? "\n\nUnfinished downloads stay in the queue and "
                              "resume next time." : "");
    if (b & PSP_CTRL_CROSS) {
        downloads_save(&g_downloads);
        network_disconnect();
        sceKernelExitGame();
    }
    input_resync();
}

/* ===== Saves ===== */

/* Fetch the save's server-side info and run the compare dialog with
 * `suggested` on Cross; Square / Triangle in the dialog force a
 * direction.  Executes the choice and reports the result. */
static void compare_and_sync(int idx, SyncAction suggested) {
    TitleInfo *title = &g_state.titles[idx];
    char server_hash[65] = "";
    uint32_t server_size = 0;
    char server_last_sync[32] = "";
    network_get_save_info(&g_state, title->game_id, server_hash, &server_size, server_last_sync);

    int choice = ui_confirm(title, suggested, !title->server_only,
                            server_hash, server_size, server_last_sync);
    if (choice != SYNC_UPLOAD && choice != SYNC_DOWNLOAD)
        return;
    SyncAction action = (SyncAction)choice;

    ui_status("%s %s...", action == SYNC_UPLOAD ? "Uploading" : "Downloading",
              title->name[0] ? title->name : title->game_id);
    int r = sync_execute(&g_state, idx, action);
    if (r == 0)
        ui_toast(UI_TONE_OK, "%s finished", action == SYNC_UPLOAD ? "Upload" : "Download");
    else
        ui_message(UI_TONE_ERR, "Sync failed", "%s failed (error %d) for:\n%s",
                   action == SYNC_UPLOAD ? "Upload" : "Download", r, title->game_id);
}

static void format_bytes(uint64_t bytes, char *out, size_t n) {
    if (bytes >= 1024ULL * 1024 * 1024)
        snprintf(out, n, "%.2f GB", bytes / (1024.0 * 1024 * 1024));
    else if (bytes >= 1024ULL * 1024)
        snprintf(out, n, "%.1f MB", bytes / (1024.0 * 1024));
    else if (bytes >= 1024)
        snprintf(out, n, "%.0f KB", bytes / 1024.0);
    else
        snprintf(out, n, "%u B", (unsigned)bytes);
}

static void save_details(const TitleInfo *t) {
    char size[32], files[16], hash[40];
    const char *labels[8], *values[8];
    int n = 0;
    labels[n] = "Name";    values[n++] = t->name[0] ? t->name : t->game_id;
    labels[n] = "Game ID"; values[n++] = t->game_id;
    labels[n] = "System";  values[n++] = t->is_psx ? "PS1 (PSone Classic)" : "PSP";
    if (t->server_only) {
        labels[n] = "Location"; values[n++] = "Only on the server";
    } else {
        format_bytes(t->total_size, size, sizeof(size));
        snprintf(files, sizeof(files), "%d", t->file_count);
        labels[n] = "Location"; values[n++] = "On this PSP";
        labels[n] = "Size";     values[n++] = size;
        labels[n] = "Files";    values[n++] = files;
        labels[n] = "Folder";   values[n++] = t->save_dir;
        if (t->hash_calculated) {
            snprintf(hash, sizeof(hash), "%02x%02x%02x%02x%02x%02x%02x%02x...",
                     t->hash[0], t->hash[1], t->hash[2], t->hash[3],
                     t->hash[4], t->hash[5], t->hash[6], t->hash[7]);
            labels[n] = "SHA-256"; values[n++] = hash;
        }
    }
    ui_details("Save details", labels, values, n);
}

/* ===== Catalog ===== */

static int dl_percent(const DownloadEntry *e) {
    if (!e || e->total == 0) return 0;
    uint64_t p = e->offset * 100ULL / e->total;
    return p > 100 ? 100 : (int)p;
}

static const char *dl_state_text(const DownloadEntry *e, char *buf, size_t n) {
    if (!e) return "Not downloaded";
    switch (e->status) {
        case DL_STATUS_COMPLETED: return "Downloaded";
        case DL_STATUS_ACTIVE:    return "Downloading";
        case DL_STATUS_QUEUED:    return "Queued";
        case DL_STATUS_ERROR:
            snprintf(buf, n, "Failed at %d%%", dl_percent(e));
            return buf;
        case DL_STATUS_PAUSED:
            snprintf(buf, n, "Paused at %d%%", dl_percent(e));
            return buf;
    }
    return "";
}

static void rom_details(const RomEntry *r) {
    char size[32], discs[16], path[DOWNLOAD_PATH_LEN], state[32];
    const char *labels[9], *values[9];
    int n = 0;
    const char *fmt = roms_preferred_extract_format(r);
    format_bytes(r->size, size, sizeof(size));
    labels[n] = "Name";   values[n++] = r->name[0] ? r->name : r->filename;
    labels[n] = "System"; values[n++] = r->system;
    labels[n] = "File";   values[n++] = r->filename;
    labels[n] = "Size";   values[n++] = size;
    if (r->disc_total > 1) {
        snprintf(discs, sizeof(discs), "%d", r->disc_total);
        labels[n] = "Discs"; values[n++] = discs;
    }
    labels[n] = "Installs";
    values[n++] = !strcmp(fmt, "cso")   ? "CSO (server converts)" :
                  !strcmp(fmt, "eboot") ? "EBOOT.PBP (server converts)" :
                  fmt[0] ? fmt : "As-is";
    if (roms_resolve_target_path(r, path, sizeof(path))) {
        labels[n] = "Goes to"; values[n++] = path;
    }
    labels[n] = "Status";
    values[n++] = dl_state_text(downloads_find(&g_downloads, r->rom_id), state, sizeof(state));
    labels[n] = "ROM ID"; values[n++] = r->rom_id;
    ui_details("Game details", labels, values, n);
}

/* ===== ROM catalog: cached by fingerprint (see catcache.h) ===== */

typedef struct {
    int refreshed, unchanged, gone, failed;
    int rescan;          /* -1 not asked, 0 failed / refused, 1 done */
    int rescan_count, rescan_status;
} CatSyncResult;

static void update_cache_info(void) {
    if (g_cat_mode == CAT_MODE_LIVE) {
        snprintf(g_cache_info, sizeof(g_cache_info), "Not used (no fingerprints)");
        return;
    }
    int pos = 0;
    g_cache_info[0] = '\0';
    for (int i = 0; i < G_ROM_SYSTEM_COUNT; i++) {
        int count = 0;
        if (!catcache_info(G_ROM_SYSTEMS[i], NULL, 0, &count)) continue;
        pos += snprintf(g_cache_info + pos, sizeof(g_cache_info) - pos, "%s%s %d",
                        pos ? ", " : "", G_ROM_SYSTEMS[i], count);
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

    if (!g_online) {
        g_cat_mode = CAT_MODE_OFFLINE;
        return;
    }
    if (force) {
        ui_status("Asking the server to rescan its ROM folder...");
        int count = -1, status = 0;
        int rc = network_trigger_rom_scan(&g_state, &count, &status);
        res->rescan = (rc == 0) ? 1 : 0;
        res->rescan_count = count;
        res->rescan_status = status;
    }

    ui_status("Checking the server's catalog...");
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
        ui_status("Fetching the %s catalog...", sys);
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
                     g_online ? "The server can't be reached and there is no cached copy."
                              : "Offline, and there is no cached copy of this catalog.");
    }
    if (live && g_online) {
        ui_status("Fetching the %s catalog...", sys);
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
    if (g_cat_mode == CAT_MODE_OFFLINE && g_online)
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
    if (!g_online) {
        ui_message(UI_TONE_ERR, "Can't refresh the catalog",
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
        ui_message(UI_TONE_ERR, "Can't refresh the catalog",
                   "Server rescan: %s\n\nThe catalog couldn't be fetched, so the cached "
                   "copy was kept.", rescan);
    } else if (g_cat_mode == CAT_MODE_LIVE) {
        ui_message(UI_TONE_OK, "Catalog refreshed",
                   "Server rescan: %s\n\n%s: %d games.\n\nThis server has no catalog "
                   "fingerprints, so the catalog is fetched fresh every time and not "
                   "kept on the PSP.", rescan, G_ROM_SYSTEMS[g_rom_system_index],
                   g_rom_catalog.count);
    } else {
        ui_message(r.failed ? UI_TONE_WARN : UI_TONE_OK,
                   r.failed ? "Catalog partly refreshed" : "Catalog refreshed",
                   "Server rescan: %s\n\nRefetched %d system%s%s.\nCached now: %s",
                   rescan, r.refreshed, r.refreshed == 1 ? "" : "s",
                   r.failed ? " (some failed - try again)" : "", g_cache_info);
    }
}

static void catalog_download(void) {
    RomEntry *r = &g_rom_catalog.items[g_rom_selected];
    DownloadEntry *e = downloads_upsert_from_catalog(&g_downloads, r);
    if (!e) {
        ui_message(UI_TONE_WARN, "Queue full",
                   "The download queue is full (%d).", DOWNLOAD_MAX);
    } else if (e->status == DL_STATUS_COMPLETED) {
        ui_message(UI_TONE_OK, "Already downloaded", "%s\n\nLocation:\n%s",
                   e->name[0] ? e->name : e->filename, e->target_path);
    } else {
        /* A paused or failed entry resumes from where it stopped. */
        e->status = (e->offset > 0) ? DL_STATUS_PAUSED : DL_STATUS_QUEUED;
        downloads_save(&g_downloads);
        run_download(&g_state, e);
    }
}

/* ===== Downloads ===== */

static void download_details(const DownloadEntry *e) {
    char done[32], total[32], prog[80], state[32];
    const char *labels[7], *values[7];
    int n = 0;
    labels[n] = "Name";    values[n++] = e->name[0] ? e->name : e->filename;
    labels[n] = "System";  values[n++] = e->system;
    labels[n] = "Status";  values[n++] = dl_state_text(e, state, sizeof(state));
    format_bytes(e->offset, done, sizeof(done));
    format_bytes(e->total, total, sizeof(total));
    snprintf(prog, sizeof(prog), "%s / %s", done, total);
    labels[n] = "Progress"; values[n++] = prog;
    labels[n] = "File";     values[n++] = e->filename;
    labels[n] = "Goes to";  values[n++] = e->target_path;
    labels[n] = "ROM ID";   values[n++] = e->rom_id;
    ui_details("Download details", labels, values, n);
}

static void clamp_dl_selection(void) {
    if (g_dl_selected >= g_downloads.count)
        g_dl_selected = g_downloads.count > 0 ? g_downloads.count - 1 : 0;
    clamp_scroll(g_dl_selected, &g_dl_scroll);
}

/* Square: remove the selected entry, or clear every finished one. */
static void downloads_remove_menu(void) {
    int finished = 0;
    for (int i = 0; i < g_downloads.count; i++)
        if (g_downloads.items[i].status == DL_STATUS_COMPLETED) finished++;
    const DownloadEntry *e = (g_dl_selected < g_downloads.count)
                           ? &g_downloads.items[g_dl_selected] : NULL;
    if (!e && finished == 0) return;

    const char *buttons[2], *labels[2];
    char clear_label[32];
    int n = 0;
    if (e) { buttons[n] = "CROSS"; labels[n++] = "Remove"; }
    if (finished > 0) {
        snprintf(clear_label, sizeof(clear_label), "Clear %d finished", finished);
        buttons[n] = "SQUARE"; labels[n++] = clear_label;
    }
    bool partial = e && e->status != DL_STATUS_COMPLETED && e->offset > 0;
    uint32_t b = ui_ask(UI_TONE_WARN, "Remove from the queue", buttons, labels, n,
                        "%s%s%s",
                        e ? (e->name[0] ? e->name : e->filename) : "",
                        partial ? "\n\nThe partly downloaded file is deleted." :
                        (e && e->status == DL_STATUS_COMPLETED)
                            ? "\n\nThe game stays on the Memory Stick." : "",
                        finished > 0 ? "\n\nSquare clears every finished download "
                                       "from the list." : "");
    if ((b & PSP_CTRL_CROSS) && e) {
        char rom_id[ROM_ID_LEN];
        snprintf(rom_id, sizeof(rom_id), "%s", e->rom_id);
        downloads_remove(&g_downloads, rom_id);
        downloads_save(&g_downloads);
        ui_toast(UI_TONE_OK, "Removed from the queue");
    } else if ((b & PSP_CTRL_SQUARE) && finished > 0) {
        int removed = 0, i = 0;
        while (i < g_downloads.count) {
            if (g_downloads.items[i].status == DL_STATUS_COMPLETED) {
                char rom_id[ROM_ID_LEN];
                snprintf(rom_id, sizeof(rom_id), "%s", g_downloads.items[i].rom_id);
                downloads_remove(&g_downloads, rom_id);
                removed++;
            } else {
                i++;
            }
        }
        downloads_save(&g_downloads);
        ui_toast(UI_TONE_OK, "Cleared %d finished download%s.",
                 removed, removed == 1 ? "" : "s");
    }
    clamp_dl_selection();
    input_resync();
}

/* ===== Settings ===== */

enum {
    SET_SERVER = 0,
    SET_CONNECTION,
    SET_CONSOLE,
    SET_WIFI,
    SET_CACHE,
    SET_REFRESH,
    SET_VERSION,
    SET_COUNT
};

static void build_settings_rows(UiSettingsRow *rows) {
    memset(rows, 0, sizeof(UiSettingsRow) * SET_COUNT);
    if (!g_cache_info[0]) update_cache_info();

    rows[SET_SERVER].label = "Server";
    snprintf(rows[SET_SERVER].value, sizeof(rows[0].value), "%s", g_state.server_url);
    rows[SET_SERVER].help = "server_url in " CONFIG_PATH
                            ". Restart GameSync after editing it.";

    rows[SET_CONNECTION].label = "Connection";
    snprintf(rows[SET_CONNECTION].value, sizeof(rows[0].value), "%s",
             g_online ? "Online" : "Offline");
    rows[SET_CONNECTION].help = g_online
        ? "WiFi is connected and the server answered at startup."
        : "The server could not be reached at startup. Saves can't sync; the "
          "catalog shows its cached copy.";

    rows[SET_CONSOLE].label = "Console ID";
    snprintf(rows[SET_CONSOLE].value, sizeof(rows[0].value), "%s", g_state.console_id);
    rows[SET_CONSOLE].help = "Identifies this PSP to the server (" CONSOLE_ID_FILE ").";

    rows[SET_WIFI].label = "Access point";
    snprintf(rows[SET_WIFI].value, sizeof(rows[0].value), "Connection %d",
             g_state.wifi_ap_index + 1);
    rows[SET_WIFI].help = "wifi_ap in config.txt picks which of the PSP's saved "
                          "network connections GameSync uses (0 = the first).";

    rows[SET_CACHE].label = "Cached catalog";
    snprintf(rows[SET_CACHE].value, sizeof(rows[0].value), "%s", g_cache_info);
    rows[SET_CACHE].help = "Games kept on the Memory Stick per system, in "
                           CATALOG_CACHE_FILE ". Only systems that changed on "
                           "the server are downloaded again.";

    rows[SET_REFRESH].label = "Refresh catalog";
    rows[SET_REFRESH].action = true;
    rows[SET_REFRESH].help = "Asks the server to rescan its ROM folder, throws "
                             "away the cached catalog and downloads every "
                             "system again.";

    rows[SET_VERSION].label = "Version";
    snprintf(rows[SET_VERSION].value, sizeof(rows[0].value), "%s", APP_VERSION);
    rows[SET_VERSION].help = "GameSync PSP client.";
}

/* ===== Main ===== */

int main(int argc, char *argv[]) {
    (void)argc; (void)argv;

    setup_callbacks();
    ui_init();

    /* Power management: bump CPU/bus to max so WiFi recv + MS write
     * keep up.  Default boot clock is 222/111 MHz which caps PSP TCP
     * throughput around 80-100 KB/s; 333/166 MHz can reach 1+ MB/s
     * over a clean 802.11g link. */
    scePowerSetClockFrequency(333, 333, 166);

    memset(&g_state, 0, sizeof(SyncState));
    g_state.wifi_ap_index = 0;

    /* Load config */
    char err_buf[256];
    ui_status("Loading config...");

    if (!config_load(&g_state, err_buf, sizeof(err_buf))) {
        ui_fatal("Config error",
                 "%s\n\nCreate config.txt in %s with:\n"
                 "server_url=http://192.168.1.100:8000\n"
                 "api_key=your-key\n"
                 "wifi_ap=0",
                 err_buf, SYNC_STATE_DIR);
        sceKernelSleepThread();
        return 0;
    }

    config_load_console_id(&g_state);

    /* Initialize network */
    ui_status("Initializing network...");
    int net_init = network_init();

    if (net_init == 0) {
        ui_status("Connecting to WiFi (access point %d)...", g_state.wifi_ap_index + 1);
        if (network_connect_ap(g_state.wifi_ap_index) == 0) {
            ui_status("WiFi connected. Checking server...");
            g_online = network_check_server(&g_state);
            if (!g_online) {
                ui_message(UI_TONE_WARN, "Server unreachable",
                           "Cannot reach the server at:\n%s\n\nContinuing offline.",
                           g_state.server_url);
            }
        } else {
            ui_message(UI_TONE_WARN, "WiFi connection failed",
                       "Check wifi_ap in config.txt.\n\nContinuing offline.");
        }
    } else {
        ui_message(UI_TONE_ERR, "Network unavailable",
                   "Network init failed\n%s (%d)\n\nContinuing offline.",
                   network_init_error(), net_init);
    }
    ui_set_online(g_online);

    /* Scan saves */
    ui_status("Scanning PSP/SAVEDATA...");
    saves_scan(&g_state);

    if (g_online) {
        ui_status("Checking server saves...");
        int srv_added = 0, srv_seen = 0;
        if (network_merge_server_titles(&g_state, &srv_added, &srv_seen) < 0)
            ui_toast(UI_TONE_WARN, "Couldn't fetch the server's save list");
    }

    if (g_online && g_state.num_titles > 0) {
        ui_status("Fetching game names...");
        network_fetch_names(&g_state);
    }

    if (g_state.num_titles > 1)
        qsort(g_state.titles, g_state.num_titles, sizeof(TitleInfo), title_compare);

    /* No saves is not fatal: the catalog and downloads still work. */
    if (g_state.num_titles == 0)
        ui_toast(UI_TONE_WARN, "No PSP/PS1 saves found");
    else
        ui_toast(UI_TONE_OK, "Found %d save%s", g_state.num_titles,
                 g_state.num_titles == 1 ? "" : "s");

    /* Drain any buttons held during startup/scan before entering the loop. */
    {
        SceCtrlData pad;
        do { sceCtrlReadBufferPositive(&pad, 1); sceKernelDelayThread(16000); }
        while (pad.Buttons != 0);
    }

    /* ROM downloads init — directories + persisted queue. */
    roms_ensure_target_dirs();
    memset(&g_rom_catalog, 0, sizeof(g_rom_catalog));
    memset(&g_downloads,   0, sizeof(g_downloads));
    downloads_load(&g_downloads);
    input_resync();

    /* Each iteration handles input, then draws the current view; the
     * draw waits for vblank, which paces the loop at 60 fps. */
    while (1) {
        uint32_t just, nav;
        input_poll(&just, &nav);

        /* L / R: previous / next top-level view, wrapping. */
        if (just & PSP_CTRL_LTRIGGER)
            g_app_view = (AppView)(((int)g_app_view + APP_VIEW_COUNT - 1) % APP_VIEW_COUNT);
        if (just & PSP_CTRL_RTRIGGER)
            g_app_view = (AppView)(((int)g_app_view + 1) % APP_VIEW_COUNT);

        /* START: exit (with confirmation) from any view. */
        if (just & PSP_CTRL_START) {
            confirm_exit();
            just = nav = 0;
        }

        /* ─────────────  Saves view  ───────────── */
        if (g_app_view == APP_VIEW_SAVES) {
            int total = g_state.num_titles;
            list_nav(nav, just, total, &g_selected, &g_scroll);

            /* Cross: compare with the server, confirm the suggestion
             * (or force a direction in the dialog). */
            if ((just & PSP_CTRL_CROSS) && g_online && total > 0) {
                TitleInfo *title = &g_state.titles[g_selected];
                ui_status("Comparing %s with the server...",
                          title->name[0] ? title->name : title->game_id);
                compare_and_sync(g_selected, sync_decide(&g_state, g_selected));
                input_resync();
            }

            /* Square: sync all saves. */
            if ((just & PSP_CTRL_SQUARE) && g_online && total > 0) {
                SyncSummary summary;
                ui_status("Starting sync...");
                sync_auto_all(&g_state, &summary, sync_progress);
                ui_sync_summary(&summary);
                input_resync();
            }

            /* Triangle: details of the selected save. */
            if ((just & PSP_CTRL_TRIANGLE) && total > 0) {
                save_details(&g_state.titles[g_selected]);
                input_resync();
            }

            ui_draw_saves(&g_state, g_selected, g_scroll);
            continue;
        }

        /* ─────────────  ROM Catalog view  ───────────── */
        if (g_app_view == APP_VIEW_ROMS) {
            if (g_cat_mode == CAT_MODE_NONE) {
                catalog_open();
                input_resync();
                just = nav = 0;
            }

            /* SELECT: switch the PSP / PS1 sub-tab (from the cache). */
            if (just & PSP_CTRL_SELECT) {
                g_rom_system_index = (g_rom_system_index + 1) % G_ROM_SYSTEM_COUNT;
                catalog_show_system();
                input_resync();
                just = nav = 0;
            }

            int total = g_rom_catalog.count;
            list_nav(nav, just, total, &g_rom_selected, &g_rom_scroll);

            /* Cross: download (or resume) the selected game. */
            if ((just & PSP_CTRL_CROSS) && total > 0) {
                if (g_online)
                    catalog_download();
                else
                    ui_message(UI_TONE_WARN, "Offline",
                               "Connect to the server to download games.");
                input_resync();
            }

            /* Triangle: details of the selected game. */
            if ((just & PSP_CTRL_TRIANGLE) && total > 0) {
                rom_details(&g_rom_catalog.items[g_rom_selected]);
                input_resync();
            }

            /* A download switches to the Downloads view; that one is
             * drawn from the next iteration, with fresh input. */
            if (g_app_view == APP_VIEW_ROMS)
                ui_draw_rom_catalog(&g_rom_catalog, &g_downloads,
                                    G_ROM_SYSTEMS, G_ROM_SYSTEM_COUNT,
                                    g_rom_system_index,
                                    g_rom_selected, g_rom_scroll,
                                    g_cat_mode == CAT_MODE_OFFLINE
                                        ? "Offline \xC2\xB7 cached" : NULL);
            continue;
        }

        /* ─────────────  Downloads view  ───────────── */
        if (g_app_view == APP_VIEW_DOWNLOADS) {
            int total = g_downloads.count;
            list_nav(nav, just, total, &g_dl_selected, &g_dl_scroll);

            /* Cross: start / resume the selected entry. */
            if ((just & PSP_CTRL_CROSS) && total > 0) {
                DownloadEntry *e = &g_downloads.items[g_dl_selected];
                if (e->status == DL_STATUS_COMPLETED) {
                    ui_message(UI_TONE_OK, "Already downloaded", "%s\n\nLocation:\n%s",
                               e->name[0] ? e->name : e->filename, e->target_path);
                } else if (!g_online) {
                    ui_message(UI_TONE_WARN, "Offline",
                               "Connect to the server to resume downloads.");
                } else if (e->status != DL_STATUS_ACTIVE) {
                    run_download(&g_state, e);
                }
                input_resync();
            }

            /* Square: remove the entry / clear finished ones. */
            if (just & PSP_CTRL_SQUARE)
                downloads_remove_menu();

            /* Triangle: details of the selected entry. */
            if ((just & PSP_CTRL_TRIANGLE) && total > 0) {
                download_details(&g_downloads.items[g_dl_selected]);
                input_resync();
            }

            /* Circle pauses a running transfer (rom_progress64_cb); with
             * nothing running there is nothing to cancel here. */
            draw_downloads_view(true);
            continue;
        }

        /* ─────────────  Settings view  ───────────── */
        if (g_app_view == APP_VIEW_SETTINGS) {
            static UiSettingsRow rows[SET_COUNT];
            list_nav(nav, just, SET_COUNT, &g_set_selected, &g_set_scroll);

            if ((just & PSP_CTRL_CROSS) && g_set_selected == SET_REFRESH) {
                catalog_refresh();
                input_resync();
            }

            build_settings_rows(rows);
            ui_draw_settings(rows, SET_COUNT, g_set_selected, g_set_scroll);
            continue;
        }
    }

    network_disconnect();
    return 0;
}
