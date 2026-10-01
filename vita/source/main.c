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
 * Views (START cycles): Saves -> ROM Catalog -> Downloads, drawn with
 * vita2d (see ui.c / gui.c).
 */

#include <string.h>
#include <stdio.h>
#include <stdlib.h>
#include <psp2/kernel/processmgr.h>
#include <psp2/kernel/threadmgr.h>
#include <psp2/ctrl.h>

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
/* Set once the current system's catalog has been asked for, so an empty
 * catalog isn't refetched on every frame. */
static bool g_rom_fetched = false;

/* Shoulder buttons.  The *2 ctrl readers report the Vita's own L/R as
 * L1/R1; LTRIGGER/RTRIGGER are only a DualShock's L2/R2 on a PSTV. */
#define BTN_L (SCE_CTRL_L1 | SCE_CTRL_LTRIGGER)
#define BTN_R (SCE_CTRL_R1 | SCE_CTRL_RTRIGGER)

/* Catalog systems the PSP emulator can run.  L/R in the ROMs view rotate. */
static const char *G_ROM_SYSTEMS[] = { "PSP", "PS1" };
#define G_ROM_SYSTEM_COUNT ((int)(sizeof(G_ROM_SYSTEMS) / sizeof(G_ROM_SYSTEMS[0])))
static int g_rom_system_index = 0;

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

/* Edge-detect SQUARE during an active download for pause. */
static uint32_t g_dl_prev_buttons = 0;

static void update_scroll(void) {
    if (g_selected < g_scroll)
        g_scroll = g_selected;
    if (g_selected >= g_scroll + LIST_VISIBLE)
        g_scroll = g_selected - LIST_VISIBLE + 1;
}

/* Clamp a list cursor and keep it inside the visible window. */
static void clamp_cursor(int *selected, int *scroll, int total, int visible) {
    if (total <= 0) { *selected = 0; *scroll = 0; return; }
    if (*selected >= total) *selected = total - 1;
    if (*selected < 0) *selected = 0;
    if (*selected < *scroll) *scroll = *selected;
    if (*selected >= *scroll + visible) *scroll = *selected - visible + 1;
    if (*scroll < 0) *scroll = 0;
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

    /* SQUARE press → pause.  Edge-detect via prev_buttons mask. */
    SceCtrlData pad;
    sceCtrlPeekBufferPositive2(0, &pad, 1);
    uint32_t just = pad.buttons & ~g_dl_prev_buttons;
    g_dl_prev_buttons = pad.buttons;
    if (just & SCE_CTRL_SQUARE) g_pause_requested = true;
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

static void cycle_view(void) {
    g_app_view = (AppView)(((int)g_app_view + 1) % APP_VIEW_COUNT);
}

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
    bool has_wifi = false;

    if (network_init() == 0) {
        ui_busy("Checking WiFi connection...");
        if (network_connect() == 0) {
            ui_busy("Checking server...");
            has_wifi = network_check_server(&g_state);
            g_state.network_connected = has_wifi;
            ui_set_online(has_wifi ? 1 : 0);
            if (!has_wifi) {
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

    if (has_wifi) {
        ui_busy("Checking server saves...");
        network_merge_server_titles(&g_state);
    }

    if (has_wifi && g_state.num_titles > 0) {
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
                  "Press START to open the ROM Catalog.");
    } else {
        ui_toast(UI_TONE_OK, "Found %d save%s", g_state.num_titles,
                 g_state.num_titles == 1 ? "" : "s");
    }

    /* ROM downloads init — directories + persisted queue. */
    roms_ensure_target_dirs();
    memset(&g_rom_catalog, 0, sizeof(g_rom_catalog));
    memset(&g_downloads,   0, sizeof(g_downloads));
    downloads_load(&g_downloads);

    /* Main loop.  Every pass draws one full frame of the current view;
     * the frame waits for vblank, which paces the loop at 60 fps. */
    SceCtrlData pad;
    uint32_t prev_buttons = drain_buttons();

    while (1) {
        sceCtrlReadBufferPositive2(0, &pad, 1);
        uint32_t just = pad.buttons & ~prev_buttons;
        prev_buttons = pad.buttons;

        /* START: cycle Saves -> ROM Catalog -> Downloads, from any view. */
        if (just & SCE_CTRL_START) cycle_view();

        /* ─────────────  Saves view  ───────────── */
        if (g_app_view == APP_VIEW_SAVES) {
            int total = g_state.num_titles;

            if ((just & SCE_CTRL_DOWN) && total > 0) {
                g_selected = (g_selected + 1) % total;
                update_scroll();
            }
            if ((just & SCE_CTRL_UP) && total > 0) {
                g_selected = (g_selected - 1 + total) % total;
                update_scroll();
            }
            if ((just & SCE_CTRL_RIGHT) && total > 0) {
                g_selected = g_selected + LIST_VISIBLE;
                if (g_selected >= total) g_selected = total - 1;
                update_scroll();
            }
            if ((just & SCE_CTRL_LEFT) && total > 0) {
                g_selected = g_selected - LIST_VISIBLE;
                if (g_selected < 0) g_selected = 0;
                update_scroll();
            }

            /* Circle: settings card */
            if (just & SCE_CTRL_CIRCLE) {
                ui_show_settings(&g_state);
                prev_buttons = drain_buttons();
            }

            if ((just & (SCE_CTRL_CROSS | SCE_CTRL_SQUARE | SCE_CTRL_TRIANGLE |
                         SCE_CTRL_SELECT)) && !has_wifi && total > 0)
                ui_toast(UI_TONE_ERR, "Offline - syncing needs the server");

            /* X: smart sync */
            if ((just & SCE_CTRL_CROSS) && has_wifi && total > 0) {
                TitleInfo *title = &g_state.titles[g_selected];
                ui_busy("Comparing %s with the server...", title->game_id);
                SyncAction action = sync_decide(&g_state, g_selected);
                ui_invalidate_sync_state();
                confirm_and_sync(g_selected, action);
                prev_buttons = drain_buttons();
            }

            /* Square: manual upload */
            if ((just & SCE_CTRL_SQUARE) && has_wifi && total > 0) {
                TitleInfo *title = &g_state.titles[g_selected];
                if (title->server_only)
                    ui_notice(UI_TONE_INFO, "Nothing to upload",
                              "This save only exists on the server.\n\nDownload it first.");
                else
                    confirm_and_sync(g_selected, SYNC_UPLOAD);
                prev_buttons = drain_buttons();
            }

            /* Triangle: manual download */
            if ((just & SCE_CTRL_TRIANGLE) && has_wifi && total > 0) {
                confirm_and_sync(g_selected, SYNC_DOWNLOAD);
                prev_buttons = drain_buttons();
            }

            /* Select: auto sync all saves */
            if ((just & SCE_CTRL_SELECT) && has_wifi && total > 0) {
                SyncSummary summary;
                sync_auto_all(&g_state, &summary, sync_progress);
                ui_invalidate_sync_state();
                ui_sync_summary(&summary);
                prev_buttons = drain_buttons();
            }

            ui_draw_list(&g_state, g_selected, g_scroll);
            continue;
        }

        /* ─────────────  ROM Catalog view  ───────────── */
        if (g_app_view == APP_VIEW_ROMS) {
            int total = g_rom_catalog.count;
            const char *current_system = G_ROM_SYSTEMS[g_rom_system_index];

            /* L/R: cycle system (PSP <-> PS1).  Reset the cache so the
             * new system's catalog auto-loads. */
            if (just & (BTN_L | BTN_R)) {
                int dir = (just & BTN_R) ? 1 : -1;
                g_rom_system_index =
                    (g_rom_system_index + dir + G_ROM_SYSTEM_COUNT) % G_ROM_SYSTEM_COUNT;
                memset(&g_rom_catalog, 0, sizeof(g_rom_catalog));
                g_rom_fetched  = false;
                g_rom_selected = 0;
                g_rom_scroll   = 0;
                current_system = G_ROM_SYSTEMS[g_rom_system_index];
                total = 0;
            }

            char roms_status[128];
            if (!has_wifi)
                snprintf(roms_status, sizeof(roms_status),
                         "Offline - catalog needs the server");
            else
                snprintf(roms_status, sizeof(roms_status),
                         "%d in queue | %s",
                         g_downloads.count, g_state.pspemu_root);

            /* Auto-fetch on first entry / after a refresh. */
            if (!g_rom_fetched && has_wifi) {
                ui_draw_rom_catalog(&g_rom_catalog, &g_downloads, current_system,
                                    g_rom_selected, g_rom_scroll, roms_status,
                                    g_app_view);
                ui_busy("Fetching %s catalog...", current_system);
                static char catalog_scratch[1024 * 1024];
                roms_fetch_catalog(&g_state, current_system,
                                   catalog_scratch, sizeof(catalog_scratch),
                                   &g_rom_catalog);
                g_rom_fetched = true;
                total = g_rom_catalog.count;
                clamp_cursor(&g_rom_selected, &g_rom_scroll, total, LIST_VISIBLE);
            }

            if ((just & SCE_CTRL_DOWN) && total > 0) {
                g_rom_selected = (g_rom_selected + 1) % total;
                clamp_cursor(&g_rom_selected, &g_rom_scroll, total, LIST_VISIBLE);
            }
            if ((just & SCE_CTRL_UP) && total > 0) {
                g_rom_selected = (g_rom_selected - 1 + total) % total;
                clamp_cursor(&g_rom_selected, &g_rom_scroll, total, LIST_VISIBLE);
            }
            if ((just & SCE_CTRL_RIGHT) && total > 0) {
                g_rom_selected += LIST_VISIBLE;
                clamp_cursor(&g_rom_selected, &g_rom_scroll, total, LIST_VISIBLE);
            }
            if ((just & SCE_CTRL_LEFT) && total > 0) {
                g_rom_selected -= LIST_VISIBLE;
                clamp_cursor(&g_rom_selected, &g_rom_scroll, total, LIST_VISIBLE);
            }

            /* Circle: rescan server + refetch. */
            if (just & SCE_CTRL_CIRCLE) {
                if (has_wifi) {
                    ui_busy("Rescanning the server's ROM folder...");
                    int count = -1;
                    int rc = network_trigger_rom_scan(&g_state, &count);
                    if (rc != 0)
                        ui_notice(UI_TONE_ERR, "Rescan failed",
                                  "The server rescan failed (code %d).", rc);
                }
                memset(&g_rom_catalog, 0, sizeof(g_rom_catalog));
                g_rom_fetched  = false;
                g_rom_selected = 0;
                g_rom_scroll   = 0;
                prev_buttons = drain_buttons();
            }

            /* Cross: queue + start the selected ROM. */
            if ((just & SCE_CTRL_CROSS) && total > 0 && has_wifi) {
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

            /* Triangle on a paused/error entry: resume. */
            if ((just & SCE_CTRL_TRIANGLE) && total > 0 && has_wifi) {
                RomEntry *r = &g_rom_catalog.items[g_rom_selected];
                DownloadEntry *e = downloads_find(&g_downloads, r->rom_id);
                if (e && (e->status == DL_STATUS_PAUSED ||
                          e->status == DL_STATUS_ERROR ||
                          e->status == DL_STATUS_QUEUED))
                {
                    run_download(&g_state, e);
                }
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

            if ((just & SCE_CTRL_DOWN) && total > 0) {
                g_dl_selected = (g_dl_selected + 1) % total;
                clamp_cursor(&g_dl_selected, &g_dl_scroll, total, LIST_VISIBLE);
            }
            if ((just & SCE_CTRL_UP) && total > 0) {
                g_dl_selected = (g_dl_selected - 1 + total) % total;
                clamp_cursor(&g_dl_selected, &g_dl_scroll, total, LIST_VISIBLE);
            }

            /* Cross: start/resume selected. */
            if ((just & SCE_CTRL_CROSS) && total > 0 && has_wifi) {
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

            /* Square: pause active. */
            if (just & SCE_CTRL_SQUARE) {
                if (g_active_in_progress) {
                    g_pause_requested = true;
                    ui_toast(UI_TONE_WARN, "Pausing...");
                }
            }

            /* Circle: cancel selected (after pause). */
            if ((just & SCE_CTRL_CIRCLE) && total > 0 && g_dl_selected < total) {
                if (g_active_in_progress &&
                    strcmp(g_active_rom_id, g_downloads.items[g_dl_selected].rom_id) == 0)
                {
                    g_pause_requested = true;
                    ui_toast(UI_TONE_WARN, "Pause the active download first, then cancel");
                } else {
                    char rom_id[ROM_ID_LEN];
                    strncpy(rom_id, g_downloads.items[g_dl_selected].rom_id,
                            sizeof(rom_id) - 1);
                    rom_id[sizeof(rom_id) - 1] = '\0';
                    downloads_remove(&g_downloads, rom_id);
                    downloads_save(&g_downloads);
                    clamp_cursor(&g_dl_selected, &g_dl_scroll,
                                 g_downloads.count, LIST_VISIBLE);
                    ui_toast(UI_TONE_INFO, "Removed from the queue");
                }
            }

            /* Triangle: clear completed entries. */
            if (just & SCE_CTRL_TRIANGLE) {
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
                }
            }

            int installed = 0;
            for (int i = 0; i < g_downloads.count; i++)
                if (g_downloads.items[i].status == DL_STATUS_COMPLETED) installed++;
            char dl_status[128];
            snprintf(dl_status, sizeof(dl_status), "%d waiting, %d installed%s",
                     g_downloads.count - installed, installed,
                     has_wifi ? "" : " - offline");
            ui_draw_downloads(&g_downloads, g_dl_selected, g_dl_scroll,
                              dl_status,
                              g_active_in_progress,
                              g_active_downloaded, g_active_total,
                              g_active_bps, g_app_view);
            continue;
        }
    }

    network_cleanup();
    ui_term();
    return 0;
}
