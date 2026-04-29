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
 */

#include <pspkernel.h>
#include <pspdebug.h>
#include <pspctrl.h>
#include <psppower.h>
#include <stdio.h>
#include <string.h>
#include <stdlib.h>

#include <time.h>

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

#define LIST_VISIBLE 20

static SyncState g_state;
static int g_selected = 0;
static int g_scroll = 0;

/* Multi-view state.  START cycles through Saves → ROM Catalog →
 * Downloads.  Each view tracks its own selected/scroll. */
static AppView      g_app_view = APP_VIEW_SAVES;
static RomCatalog   g_rom_catalog;
static DownloadList g_downloads;
static int g_rom_selected = 0;
static int g_rom_scroll   = 0;
static int g_dl_selected  = 0;
static int g_dl_scroll    = 0;

/* Catalog system cycle (PSP/PS1).  L1/R1 in ROMs view rotates. */
static const char *G_ROM_SYSTEMS[] = { "PSP", "PS1" };
#define G_ROM_SYSTEM_COUNT ((int)(sizeof(G_ROM_SYSTEMS) / sizeof(G_ROM_SYSTEMS[0])))
static int g_rom_system_index = 0;

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

/* Edge-detect SQUARE during an active download for pause. */
static uint32_t g_dl_prev_buttons = 0;

static void sync_progress(const char *msg) { ui_status("%s", msg); }

static int title_compare(const void *a, const void *b) {
    const TitleInfo *ta = (const TitleInfo *)a;
    const TitleInfo *tb = (const TitleInfo *)b;
    return strcasecmp(ta->name, tb->name);
}

static void update_scroll(void) {
    if (g_selected < g_scroll)
        g_scroll = g_selected;
    if (g_selected >= g_scroll + LIST_VISIBLE)
        g_scroll = g_selected - LIST_VISIBLE + 1;
}

/* ===== ROM download helpers (mirror of PS3 client) ===== */

static int rom_progress64_cb(uint64_t downloaded, uint64_t total) {
    g_active_downloaded = downloaded;
    if (total > 0) g_active_total = total;

    /* SQUARE press → pause.  Edge-detect via prev_buttons mask. */
    SceCtrlData pad;
    sceCtrlPeekBufferPositive(&pad, 1);
    uint32_t just = pad.Buttons & ~g_dl_prev_buttons;
    g_dl_prev_buttons = pad.Buttons;
    if (just & PSP_CTRL_SQUARE) g_pause_requested = true;
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

    /* Periodic redraw of the downloads view so the user sees
     * progress.  pspDebugScreen has no flicker concern (no double
     * buffer) so we redraw on every callback. */
    if (g_app_view == APP_VIEW_DOWNLOADS) {
        char status[64];
        snprintf(status, sizeof(status),
                 "Downloading...  (Square = pause)");
        ui_draw_downloads(&g_downloads, g_dl_selected, g_dl_scroll,
                          status, true,
                          g_active_downloaded, g_active_total,
                          g_active_bps, g_app_view);
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
                if (g_dl_selected < g_dl_scroll) g_dl_scroll = g_dl_selected;
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

    network_set_progress64_cb(rom_progress64_cb);
    uint64_t total_seen = 0;
    int rc = network_download_rom_resumable(state, e->rom_id,
                                            e->extract_format,
                                            e->target_path,
                                            e->offset, &total_seen);
    network_set_progress64_cb(NULL);

    e->offset = g_active_downloaded;
    if (total_seen > 0) e->total = total_seen;

    if (rc == 0) {
        e->status = DL_STATUS_COMPLETED;
        ui_message("Download complete:\n%s\n\nSaved to:\n%s",
                   e->name[0] ? e->name : e->filename, e->target_path);
    } else if (rc == 1) {
        e->status = DL_STATUS_PAUSED;
        ui_status("Paused %s", e->filename);
    } else {
        e->status = DL_STATUS_ERROR;
        ui_message("Download failed (code %d) for:\n%s",
                   rc, e->filename);
    }
    downloads_save(&g_downloads);

    g_active_in_progress = false;
    g_pause_requested    = false;
    g_active_rom_id[0]   = '\0';
}

static void cycle_view(void) {
    g_app_view = (AppView)(((int)g_app_view + 1) % APP_VIEW_COUNT);
}

int main(int argc, char *argv[]) {
    (void)argc; (void)argv;

    setup_callbacks();
    ui_init();

    /* Power management: prevent sleep during sync */
    // scePowerSetClockFrequency(333, 333, 166); // Deprecated

    memset(&g_state, 0, sizeof(SyncState));
    g_state.wifi_ap_index = 0;

    /* Load config */
    char err_buf[256];
    ui_clear();
    ui_status("Loading config...");

    if (!config_load(&g_state, err_buf, sizeof(err_buf))) {
        ui_clear();
        pspDebugScreenPrintf("Config error:\n%s\n\n", err_buf);
        pspDebugScreenPrintf("Create config.txt in:\n%s\n\n", SYNC_STATE_DIR);
        pspDebugScreenPrintf("Format:\n");
        pspDebugScreenPrintf("server_url=http://192.168.1.100:8000\n");
        pspDebugScreenPrintf("api_key=your-key\n");
        pspDebugScreenPrintf("wifi_ap=0\n\n");
        pspDebugScreenPrintf("Press HOME to exit\n");
        sceKernelSleepThread();
        return 0;
    }

    config_load_console_id(&g_state);

    /* Initialize network */
    ui_clear();
    ui_status("Initializing network...");
    int net_init = network_init();
    bool has_wifi = false;

    if (net_init == 0) {
        ui_status("Connecting to WiFi (AP %d)...", g_state.wifi_ap_index);
        if (network_connect_ap(g_state.wifi_ap_index) == 0) {
            ui_status("WiFi connected. Checking server...");
            has_wifi = network_check_server(&g_state);
            if (!has_wifi) {
                ui_message("Cannot reach server at:\n%s\n\nContinuing offline.",
                           g_state.server_url);
            }
        } else {
            ui_message("WiFi connection failed.\nCheck AP index in config.txt.\n\nContinuing offline.");
        }
    } else {
        /* Pause so pspDebugScreenPrintf output from network_init() stays visible */
        pspDebugScreenPrintf("\n--- network_init returned 0x%08X ---\nPress X to continue\n", net_init);
        {
            SceCtrlData pad;
            uint32_t prev = 0;
            while (1) {
                sceCtrlReadBufferPositive(&pad, 1);
                uint32_t just = pad.Buttons & ~prev;
                prev = pad.Buttons;
                if (just & PSP_CTRL_CROSS) break;
                sceKernelDelayThread(16000);
            }
        }
        ui_message("Network init failed\n0x%08X (%d)\nContinuing offline.", net_init, net_init);
    }

    /* Scan saves */
    ui_clear();
    ui_status("Scanning PSP/SAVEDATA...");
    saves_scan(&g_state);

    if (has_wifi) {
        ui_status("Checking server saves...");
        network_merge_server_titles(&g_state);
    }

    if (has_wifi && g_state.num_titles > 0) {
        ui_status("Fetching game names...");
        network_fetch_names(&g_state);
    }

    if (g_state.num_titles > 1)
        qsort(g_state.titles, g_state.num_titles, sizeof(TitleInfo), title_compare);

    ui_message("Found %d save(s).", g_state.num_titles);

    if (g_state.num_titles == 0) {
        ui_clear();
        pspDebugScreenPrintf("No PSP/PS1 saves found locally or on the server.\n\n");
        pspDebugScreenPrintf("Local path:\n%s\n\n", SAVEDATA_PATH);
        pspDebugScreenPrintf("Press HOME to exit\n");
        sceKernelSleepThread();
        return 0;
    }

    /* Main loop */
    SceCtrlData pad;
    bool redraw = true;
    uint32_t prev_buttons = 0;

    /* Drain any buttons held during startup/scan before entering the loop. */
    do { sceCtrlReadBufferPositive(&pad, 1); sceKernelDelayThread(16000); }
    while (pad.Buttons != 0);

    /* ROM downloads init — directories + persisted queue. */
    roms_ensure_target_dirs();
    memset(&g_rom_catalog, 0, sizeof(g_rom_catalog));
    memset(&g_downloads,   0, sizeof(g_downloads));
    downloads_load(&g_downloads);

    while (1) {
        sceCtrlReadBufferPositive(&pad, 1);
        uint32_t pressed = pad.Buttons;
        uint32_t just_pressed = pressed & ~prev_buttons;
        prev_buttons = pressed;

        /* START: cycle Saves -> ROM Catalog -> Downloads.  Available
         * from any view so the user can always escape. */
        if (just_pressed & PSP_CTRL_START) {
            cycle_view();
            redraw = true;
        }

        /* ─────────────  Saves view (existing behaviour)  ───────────── */
        if (g_app_view == APP_VIEW_SAVES) {

        /* Navigate list */
        if (just_pressed & PSP_CTRL_DOWN) {
            g_selected = (g_selected + 1) % g_state.num_titles;
            update_scroll();
            redraw = true;
        }
        if (just_pressed & PSP_CTRL_UP) {
            g_selected = (g_selected - 1 + g_state.num_titles) % g_state.num_titles;
            update_scroll();
            redraw = true;
        }
        if (just_pressed & PSP_CTRL_RIGHT) {
            g_selected = g_selected + LIST_VISIBLE;
            if (g_selected >= g_state.num_titles)
                g_selected = g_state.num_titles - 1;
            update_scroll();
            redraw = true;
        }
        if (just_pressed & PSP_CTRL_LEFT) {
            g_selected = g_selected - LIST_VISIBLE;
            if (g_selected < 0) g_selected = 0;
            update_scroll();
            redraw = true;
        }

        /* X button: smart sync */
        if (just_pressed & PSP_CTRL_CROSS && has_wifi) {
            TitleInfo *title = &g_state.titles[g_selected];
            ui_clear();
            ui_status("Analyzing %s...", title->game_id);

            SyncAction action = sync_decide(&g_state, g_selected);

            char server_hash[65] = "";
            uint32_t server_size = 0;
            char server_last_sync[32] = "";
            network_get_save_info(&g_state, title->game_id, server_hash, &server_size, server_last_sync);

            if (ui_confirm(title, action, server_hash, server_size, server_last_sync)) {
                ui_clear();
                ui_status("%s %s...",
                    action == SYNC_UPLOAD ? "Uploading" : "Downloading",
                    title->game_id);
                int r = sync_execute(&g_state, g_selected, action);
                if (r == 0)
                    ui_message("Success!");
                else
                    ui_message("Failed! (error %d)", r);
            }
            prev_buttons = 0;
            redraw = true;
        }

        /* Square button: manual upload */
        if (just_pressed & PSP_CTRL_SQUARE && has_wifi) {
            TitleInfo *title = &g_state.titles[g_selected];
            if (title->server_only) {
                ui_message("This save only exists on the server.\n\nDownload it first.");
                prev_buttons = 0;
                redraw = true;
                continue;
            }
            char server_hash[65] = "";
            uint32_t server_size = 0;
            char server_last_sync[32] = "";
            network_get_save_info(&g_state, title->game_id, server_hash, &server_size, server_last_sync);

            if (ui_confirm(title, SYNC_UPLOAD, server_hash, server_size, server_last_sync)) {
                ui_clear();
                ui_status("Uploading %s...", title->game_id);
                int r = sync_execute(&g_state, g_selected, SYNC_UPLOAD);
                if (r == 0)
                    ui_message("Upload OK!");
                else
                    ui_message("Upload failed! (error %d)", r);
            }
            prev_buttons = 0;
            redraw = true;
        }

        /* Triangle button: manual download */
        if (just_pressed & PSP_CTRL_TRIANGLE && has_wifi) {
            TitleInfo *title = &g_state.titles[g_selected];
            char server_hash[65] = "";
            uint32_t server_size = 0;
            char server_last_sync[32] = "";
            network_get_save_info(&g_state, title->game_id, server_hash, &server_size, server_last_sync);

            if (ui_confirm(title, SYNC_DOWNLOAD, server_hash, server_size, server_last_sync)) {
                ui_clear();
                ui_status("Downloading %s...", title->game_id);
                int r = sync_execute(&g_state, g_selected, SYNC_DOWNLOAD);
                if (r == 0)
                    ui_message("Download OK!");
                else
                    ui_message("Download failed! (error %d)", r);
            }
            prev_buttons = 0;
            redraw = true;
        }

        /* Select: auto sync all saves */
        if (just_pressed & PSP_CTRL_SELECT && has_wifi) {
            ui_clear();
            SyncSummary summary;
            sync_auto_all(&g_state, &summary, sync_progress);
            ui_message("Auto sync complete:\n"
                       "Uploaded:   %d\n"
                       "Downloaded: %d\n"
                       "Up to date: %d\n"
                       "Conflicts:  %d\n"
                       "Failed:     %d\n\n"
                       "Press X to continue.",
                       summary.uploaded, summary.downloaded,
                       summary.up_to_date, summary.conflicts, summary.failed);
            prev_buttons = 0;
            redraw = true;
        }

        }  /* end APP_VIEW_SAVES */

        /* ─────────────  ROM Catalog view  ───────────── */
        if (g_app_view == APP_VIEW_ROMS) {
            int total = g_rom_catalog.count;
            const char *current_system = G_ROM_SYSTEMS[g_rom_system_index];

            /* L1/R1: cycle system (PSP <-> PS1).  Reset cache so the
             * new system's catalog auto-loads via the empty check. */
            if ((just_pressed & PSP_CTRL_LTRIGGER) ||
                (just_pressed & PSP_CTRL_RTRIGGER))
            {
                int dir = (just_pressed & PSP_CTRL_RTRIGGER) ? 1 : -1;
                g_rom_system_index =
                    (g_rom_system_index + dir + G_ROM_SYSTEM_COUNT)
                    % G_ROM_SYSTEM_COUNT;
                memset(&g_rom_catalog, 0, sizeof(g_rom_catalog));
                g_rom_selected = 0;
                g_rom_scroll   = 0;
                current_system = G_ROM_SYSTEMS[g_rom_system_index];
                total = 0;
                redraw = true;
            }

            /* Auto-fetch on first entry / after a refresh. */
            if (total == 0 && !g_rom_catalog.last_error[0] && has_wifi) {
                ui_status("Fetching %s catalog...", current_system);
                static char catalog_scratch[1 * 1024 * 1024];
                roms_fetch_catalog(&g_state, current_system,
                                   catalog_scratch,
                                   sizeof(catalog_scratch),
                                   &g_rom_catalog);
                total = g_rom_catalog.count;
                if (g_rom_selected >= total)
                    g_rom_selected = total > 0 ? total - 1 : 0;
                redraw = true;
            }

            if ((just_pressed & PSP_CTRL_DOWN) && total > 0) {
                g_rom_selected = (g_rom_selected + 1) % total;
                if (g_rom_selected >= g_rom_scroll + LIST_VISIBLE)
                    g_rom_scroll = g_rom_selected - LIST_VISIBLE + 1;
                if (g_rom_selected < g_rom_scroll)
                    g_rom_scroll = g_rom_selected;
                redraw = true;
            }
            if ((just_pressed & PSP_CTRL_UP) && total > 0) {
                g_rom_selected = (g_rom_selected - 1 + total) % total;
                if (g_rom_selected < g_rom_scroll)
                    g_rom_scroll = g_rom_selected;
                if (g_rom_selected >= g_rom_scroll + LIST_VISIBLE)
                    g_rom_scroll = g_rom_selected - LIST_VISIBLE + 1;
                redraw = true;
            }
            if ((just_pressed & PSP_CTRL_RIGHT) && total > 0) {
                g_rom_selected += LIST_VISIBLE;
                if (g_rom_selected >= total) g_rom_selected = total - 1;
                if (g_rom_selected >= g_rom_scroll + LIST_VISIBLE)
                    g_rom_scroll = g_rom_selected - LIST_VISIBLE + 1;
                redraw = true;
            }
            if ((just_pressed & PSP_CTRL_LEFT) && total > 0) {
                g_rom_selected -= LIST_VISIBLE;
                if (g_rom_selected < 0) g_rom_selected = 0;
                if (g_rom_selected < g_rom_scroll)
                    g_rom_scroll = g_rom_selected;
                redraw = true;
            }

            /* Circle: rescan server + refetch. */
            if (just_pressed & PSP_CTRL_CIRCLE) {
                if (has_wifi) {
                    ui_status("Server rescan...");
                    int count = -1;
                    int rc = network_trigger_rom_scan(&g_state, &count);
                    if (rc != 0) {
                        ui_message("Server rescan failed (code %d).", rc);
                    }
                }
                memset(&g_rom_catalog, 0, sizeof(g_rom_catalog));
                g_rom_selected = 0;
                g_rom_scroll   = 0;
                redraw = true;
            }

            /* Cross: queue + start the selected ROM. */
            if ((just_pressed & PSP_CTRL_CROSS) && total > 0 && has_wifi) {
                RomEntry *r = &g_rom_catalog.items[g_rom_selected];
                DownloadEntry *e =
                    downloads_upsert_from_catalog(&g_downloads, r);
                if (!e) {
                    ui_message("Download queue full (%d).", DOWNLOAD_MAX);
                } else if (e->status == DL_STATUS_COMPLETED) {
                    ui_message("Already downloaded:\n%s\n\nLocation:\n%s",
                               e->name[0] ? e->name : e->filename,
                               e->target_path);
                } else {
                    e->status = (e->offset > 0) ? DL_STATUS_PAUSED
                                                : DL_STATUS_QUEUED;
                    downloads_save(&g_downloads);
                    run_download(&g_state, e);
                }
                prev_buttons = 0;
                redraw = true;
            }

            /* Triangle on a paused/error entry: resume. */
            if ((just_pressed & PSP_CTRL_TRIANGLE) && total > 0 && has_wifi) {
                RomEntry *r = &g_rom_catalog.items[g_rom_selected];
                DownloadEntry *e = downloads_find(&g_downloads, r->rom_id);
                if (e && (e->status == DL_STATUS_PAUSED ||
                          e->status == DL_STATUS_ERROR ||
                          e->status == DL_STATUS_QUEUED))
                {
                    run_download(&g_state, e);
                }
                prev_buttons = 0;
                redraw = true;
            }

            if (redraw) {
                char roms_status[128];
                snprintf(roms_status, sizeof(roms_status),
                         "%d entries, %d in queue",
                         g_rom_catalog.count, g_downloads.count);
                ui_draw_rom_catalog(&g_rom_catalog, &g_downloads,
                                    current_system,
                                    g_rom_selected, g_rom_scroll,
                                    roms_status, g_app_view);
                redraw = false;
            }
            sceKernelDelayThread(16000);
            continue;
        }

        /* ─────────────  Downloads view  ───────────── */
        if (g_app_view == APP_VIEW_DOWNLOADS) {
            int total = g_downloads.count;

            if ((just_pressed & PSP_CTRL_DOWN) && total > 0) {
                g_dl_selected = (g_dl_selected + 1) % total;
                if (g_dl_selected >= g_dl_scroll + LIST_VISIBLE)
                    g_dl_scroll = g_dl_selected - LIST_VISIBLE + 1;
                if (g_dl_selected < g_dl_scroll) g_dl_scroll = g_dl_selected;
                redraw = true;
            }
            if ((just_pressed & PSP_CTRL_UP) && total > 0) {
                g_dl_selected = (g_dl_selected - 1 + total) % total;
                if (g_dl_selected < g_dl_scroll) g_dl_scroll = g_dl_selected;
                if (g_dl_selected >= g_dl_scroll + LIST_VISIBLE)
                    g_dl_scroll = g_dl_selected - LIST_VISIBLE + 1;
                redraw = true;
            }

            /* Cross: start/resume selected. */
            if ((just_pressed & PSP_CTRL_CROSS) && total > 0 && has_wifi) {
                DownloadEntry *e =
                    (g_dl_selected >= 0 && g_dl_selected < total)
                        ? &g_downloads.items[g_dl_selected]
                        : downloads_next_runnable(&g_downloads);
                if (e && e->status != DL_STATUS_COMPLETED &&
                         e->status != DL_STATUS_ACTIVE)
                {
                    run_download(&g_state, e);
                }
                prev_buttons = 0;
                redraw = true;
            }

            /* Square: pause active. */
            if (just_pressed & PSP_CTRL_SQUARE) {
                if (g_active_in_progress) {
                    g_pause_requested = true;
                    ui_status("Pausing...");
                }
                redraw = true;
            }

            /* Circle: cancel selected (after pause). */
            if ((just_pressed & PSP_CTRL_CIRCLE) && total > 0 &&
                g_dl_selected < total)
            {
                if (g_active_in_progress &&
                    strcmp(g_active_rom_id,
                           g_downloads.items[g_dl_selected].rom_id) == 0)
                {
                    g_pause_requested = true;
                    ui_status("Pause active first, then cancel.");
                } else {
                    char rom_id[ROM_ID_LEN];
                    strncpy(rom_id, g_downloads.items[g_dl_selected].rom_id,
                            sizeof(rom_id) - 1);
                    rom_id[sizeof(rom_id) - 1] = '\0';
                    downloads_remove(&g_downloads, rom_id);
                    downloads_save(&g_downloads);
                    if (g_dl_selected >= g_downloads.count)
                        g_dl_selected = g_downloads.count > 0
                                      ? g_downloads.count - 1 : 0;
                }
                redraw = true;
            }

            /* Triangle: clear completed entries. */
            if (just_pressed & PSP_CTRL_TRIANGLE) {
                int removed = 0;
                int i = 0;
                while (i < g_downloads.count) {
                    if (g_downloads.items[i].status == DL_STATUS_COMPLETED) {
                        char rom_id[ROM_ID_LEN];
                        strncpy(rom_id, g_downloads.items[i].rom_id,
                                sizeof(rom_id) - 1);
                        rom_id[sizeof(rom_id) - 1] = '\0';
                        downloads_remove(&g_downloads, rom_id);
                        removed++;
                    } else {
                        i++;
                    }
                }
                if (removed > 0) {
                    downloads_save(&g_downloads);
                    if (g_dl_selected >= g_downloads.count)
                        g_dl_selected = g_downloads.count > 0
                                      ? g_downloads.count - 1 : 0;
                    ui_status("Cleared %d completed.", removed);
                }
                redraw = true;
            }

            if (redraw) {
                char dl_status[128];
                snprintf(dl_status, sizeof(dl_status),
                         "%d entries  (%s)",
                         g_downloads.count,
                         g_active_in_progress ? "downloading"
                                              : (has_wifi ? "idle" : "offline"));
                ui_draw_downloads(&g_downloads, g_dl_selected, g_dl_scroll,
                                  dl_status,
                                  g_active_in_progress,
                                  g_active_downloaded, g_active_total,
                                  g_active_bps, g_app_view);
                redraw = false;
            }
            sceKernelDelayThread(16000);
            continue;
        }

        if (redraw) {
            ui_clear();
            ui_draw_list(&g_state, g_selected, g_scroll);
            redraw = false;
        }

        sceKernelDelayThread(16000);  /* ~60fps */
    }

    network_disconnect();
    return 0;
}
