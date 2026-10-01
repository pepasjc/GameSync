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
#include <pspctrl.h>
#include <psppower.h>
#include <pspthreadman.h>
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
static const char *const G_ROM_SYSTEMS[] = { "PSP", "PS1" };
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

static void cycle_view(void) {
    g_app_view = (AppView)(((int)g_app_view + 1) % APP_VIEW_COUNT);
}

/* Fetch the save's server-side info and run the confirm dialog for
 * `action`; on confirmation execute it and report the result. */
static void confirm_and_sync(int idx, SyncAction action, const char *verb) {
    TitleInfo *title = &g_state.titles[idx];
    char server_hash[65] = "";
    uint32_t server_size = 0;
    char server_last_sync[32] = "";
    network_get_save_info(&g_state, title->game_id, server_hash, &server_size, server_last_sync);

    if (!ui_confirm(title, action, server_hash, server_size, server_last_sync))
        return;

    ui_status("%s %s...", verb, title->name[0] ? title->name : title->game_id);
    int r = sync_execute(&g_state, idx, action);
    if (r == 0)
        ui_toast(UI_TONE_OK, "%s finished", action == SYNC_UPLOAD ? "Upload" : "Download");
    else
        ui_message(UI_TONE_ERR, "Sync failed", "%s failed (error %d) for:\n%s",
                   action == SYNC_UPLOAD ? "Upload" : "Download", r, title->game_id);
}

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
    bool has_wifi = false;

    if (net_init == 0) {
        ui_status("Connecting to WiFi (access point %d)...", g_state.wifi_ap_index + 1);
        if (network_connect_ap(g_state.wifi_ap_index) == 0) {
            ui_status("WiFi connected. Checking server...");
            has_wifi = network_check_server(&g_state);
            if (!has_wifi) {
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
    ui_set_online(has_wifi);

    /* Scan saves */
    ui_status("Scanning PSP/SAVEDATA...");
    saves_scan(&g_state);

    if (has_wifi) {
        ui_status("Checking server saves...");
        int srv_added = 0, srv_seen = 0;
        if (network_merge_server_titles(&g_state, &srv_added, &srv_seen) < 0)
            ui_toast(UI_TONE_WARN, "Couldn't fetch the server's save list");
    }

    if (has_wifi && g_state.num_titles > 0) {
        ui_status("Fetching game names...");
        network_fetch_names(&g_state);
    }

    if (g_state.num_titles > 1)
        qsort(g_state.titles, g_state.num_titles, sizeof(TitleInfo), title_compare);

    if (g_state.num_titles == 0) {
        ui_fatal("No saves",
                 "No PSP/PS1 saves were found locally or on the server.\n\n"
                 "Local path: %s", SAVEDATA_PATH);
        sceKernelSleepThread();
        return 0;
    }
    ui_toast(UI_TONE_OK, "Found %d save%s", g_state.num_titles,
             g_state.num_titles == 1 ? "" : "s");

    /* Main loop */
    SceCtrlData pad;
    uint32_t prev_buttons = 0;

    /* Drain any buttons held during startup/scan before entering the loop. */
    do { sceCtrlReadBufferPositive(&pad, 1); sceKernelDelayThread(16000); }
    while (pad.Buttons != 0);

    /* ROM downloads init — directories + persisted queue. */
    roms_ensure_target_dirs();
    memset(&g_rom_catalog, 0, sizeof(g_rom_catalog));
    memset(&g_downloads,   0, sizeof(g_downloads));
    downloads_load(&g_downloads);

    /* Each iteration handles input, then draws the current view; the
     * draw waits for vblank, which paces the loop at 60 fps. */
    while (1) {
        sceCtrlReadBufferPositive(&pad, 1);
        uint32_t pressed = pad.Buttons;
        uint32_t just_pressed = pressed & ~prev_buttons;
        prev_buttons = pressed;

        /* START: cycle Saves -> ROM Catalog -> Downloads.  Available
         * from any view so the user can always escape. */
        if (just_pressed & PSP_CTRL_START)
            cycle_view();

        /* ─────────────  Saves view  ───────────── */
        if (g_app_view == APP_VIEW_SAVES) {
            int total = g_state.num_titles;

            if (just_pressed & PSP_CTRL_DOWN)
                g_selected = (g_selected + 1) % total;
            if (just_pressed & PSP_CTRL_UP)
                g_selected = (g_selected - 1 + total) % total;
            if (just_pressed & PSP_CTRL_RIGHT) {
                g_selected += UI_LIST_ROWS;
                if (g_selected >= total) g_selected = total - 1;
            }
            if (just_pressed & PSP_CTRL_LEFT) {
                g_selected -= UI_LIST_ROWS;
                if (g_selected < 0) g_selected = 0;
            }
            clamp_scroll(g_selected, &g_scroll);

            /* X button: smart sync */
            if ((just_pressed & PSP_CTRL_CROSS) && has_wifi) {
                TitleInfo *title = &g_state.titles[g_selected];
                ui_status("Comparing %s with the server...",
                          title->name[0] ? title->name : title->game_id);
                SyncAction action = sync_decide(&g_state, g_selected);
                confirm_and_sync(g_selected, action,
                                 action == SYNC_UPLOAD ? "Uploading" : "Downloading");
                prev_buttons = 0;
            }

            /* Square button: manual upload */
            if ((just_pressed & PSP_CTRL_SQUARE) && has_wifi) {
                if (g_state.titles[g_selected].server_only)
                    ui_message(UI_TONE_WARN, "Nothing to upload",
                               "This save only exists on the server.\n\nDownload it first.");
                else
                    confirm_and_sync(g_selected, SYNC_UPLOAD, "Uploading");
                prev_buttons = 0;
            }

            /* Triangle button: manual download */
            if ((just_pressed & PSP_CTRL_TRIANGLE) && has_wifi) {
                confirm_and_sync(g_selected, SYNC_DOWNLOAD, "Downloading");
                prev_buttons = 0;
            }

            /* Select: auto sync all saves */
            if ((just_pressed & PSP_CTRL_SELECT) && has_wifi) {
                SyncSummary summary;
                ui_status("Starting sync...");
                sync_auto_all(&g_state, &summary, sync_progress);
                ui_sync_summary(&summary);
                prev_buttons = 0;
            }

            ui_draw_saves(&g_state, g_selected, g_scroll);
            continue;
        }

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
            }

            if ((just_pressed & PSP_CTRL_DOWN) && total > 0)
                g_rom_selected = (g_rom_selected + 1) % total;
            if ((just_pressed & PSP_CTRL_UP) && total > 0)
                g_rom_selected = (g_rom_selected - 1 + total) % total;
            if ((just_pressed & PSP_CTRL_RIGHT) && total > 0) {
                g_rom_selected += UI_LIST_ROWS;
                if (g_rom_selected >= total) g_rom_selected = total - 1;
            }
            if ((just_pressed & PSP_CTRL_LEFT) && total > 0) {
                g_rom_selected -= UI_LIST_ROWS;
                if (g_rom_selected < 0) g_rom_selected = 0;
            }
            clamp_scroll(g_rom_selected, &g_rom_scroll);

            /* Circle: rescan server + refetch. */
            if (just_pressed & PSP_CTRL_CIRCLE) {
                if (has_wifi) {
                    ui_status("Asking the server to rescan its ROMs...");
                    int count = -1;
                    int rc = network_trigger_rom_scan(&g_state, &count);
                    if (rc != 0)
                        ui_message(UI_TONE_ERR, "Rescan failed",
                                   "Server rescan failed (code %d).", rc);
                }
                memset(&g_rom_catalog, 0, sizeof(g_rom_catalog));
                g_rom_selected = 0;
                g_rom_scroll   = 0;
            }

            /* Cross: queue + start the selected ROM. */
            if ((just_pressed & PSP_CTRL_CROSS) && total > 0 && has_wifi) {
                RomEntry *r = &g_rom_catalog.items[g_rom_selected];
                DownloadEntry *e =
                    downloads_upsert_from_catalog(&g_downloads, r);
                if (!e) {
                    ui_message(UI_TONE_WARN, "Queue full",
                               "The download queue is full (%d).", DOWNLOAD_MAX);
                } else if (e->status == DL_STATUS_COMPLETED) {
                    ui_message(UI_TONE_OK, "Already downloaded", "%s\n\nLocation:\n%s",
                               e->name[0] ? e->name : e->filename,
                               e->target_path);
                } else {
                    e->status = (e->offset > 0) ? DL_STATUS_PAUSED
                                                : DL_STATUS_QUEUED;
                    downloads_save(&g_downloads);
                    run_download(&g_state, e);
                }
                prev_buttons = 0;
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
            }

            /* A download switches to the Downloads view; that one is
             * drawn from the next iteration, with fresh input. */
            if (g_app_view == APP_VIEW_ROMS)
                ui_draw_rom_catalog(&g_rom_catalog, &g_downloads,
                                    G_ROM_SYSTEMS, G_ROM_SYSTEM_COUNT,
                                    g_rom_system_index,
                                    g_rom_selected, g_rom_scroll);
            continue;
        }

        /* ─────────────  Downloads view  ───────────── */
        if (g_app_view == APP_VIEW_DOWNLOADS) {
            int total = g_downloads.count;

            if ((just_pressed & PSP_CTRL_DOWN) && total > 0)
                g_dl_selected = (g_dl_selected + 1) % total;
            if ((just_pressed & PSP_CTRL_UP) && total > 0)
                g_dl_selected = (g_dl_selected - 1 + total) % total;
            clamp_scroll(g_dl_selected, &g_dl_scroll);

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
            }

            /* Square: pause active. */
            if ((just_pressed & PSP_CTRL_SQUARE) && g_active_in_progress) {
                g_pause_requested = true;
                ui_toast(UI_TONE_WARN, "Pausing...");
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
                    ui_toast(UI_TONE_WARN, "Pause the active download first, then remove it.");
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
                    clamp_scroll(g_dl_selected, &g_dl_scroll);
                }
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
                    clamp_scroll(g_dl_selected, &g_dl_scroll);
                    ui_toast(UI_TONE_OK, "Cleared %d finished download%s.",
                             removed, removed == 1 ? "" : "s");
                }
            }

            draw_downloads_view(true);
        }
    }

    network_disconnect();
    return 0;
}
