/*
 * PS3 GameSync - Main
 *
 * Syncs PS3 and PS1 saves with the GameSync server over WiFi.
 *
 * Controller layout:
 *   Up / Down        Navigate save list
 *   Left / Right     Page up / down
 *   Cross   (X)      Smart sync (auto decide upload/download)
 *   Square  (□)      Force upload to server
 *   Triangle(△)      Force download from server
 *   R1               Compare local files with the server copy
 *   R3               Sync all saves automatically (skip conflicts)
 *   L3               Hash selected save now
 *   Circle  (○)      Rescan + rehash saves
 *   Hold Start       Exit
 */

#include "apollo.h"
#include "common.h"
#include "config.h"
#include "debug.h"
#include "gamekeys.h"
#include "hash.h"
#include "network.h"
#include "resign.h"
#include "saves.h"
#include "sha256.h"
#include "state.h"
#include "sync.h"
#include "ui.h"

#include <SDL/SDL.h>
#include <io/pad.h>
#include <sysutil/sysutil.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

/* ---- Button bitmask IDs (MASK_ prefix avoids collision with padData fields) ---- */
#define MASK_UP       (1U << 0)
#define MASK_DOWN     (1U << 1)
#define MASK_LEFT     (1U << 2)
#define MASK_RIGHT    (1U << 3)
#define MASK_CROSS    (1U << 4)
#define MASK_SQUARE   (1U << 5)
#define MASK_TRIANGLE (1U << 6)
#define MASK_CIRCLE   (1U << 7)
#define MASK_L3       (1U << 8)
#define MASK_START    (1U << 9)
#define MASK_L1       (1U << 10)
#define MASK_L2       (1U << 11)
#define MASK_R2       (1U << 12)
#define MASK_R1       (1U << 13)
#define MASK_R3       (1U << 14)
#define START_EXIT_HOLD_FRAMES 45

static bool start_exit_active(unsigned int btns) {
    return (btns & MASK_START) != 0;
}

/* MAX_PADS is already defined in <io/pad.h> as 127; we use a smaller cap */
#define PAD_COUNT    7
#define LIST_VISIBLE 35

static unsigned int read_buttons(void) {
    unsigned int btns = 0;
    padInfo padinfo;
    padData paddata;

    ioPadGetInfo(&padinfo);
    for (int i = 0; i < PAD_COUNT; i++) {
        if (!padinfo.status[i]) continue;
        ioPadGetData(i, &paddata);
        if (paddata.BTN_UP)       btns |= MASK_UP;
        if (paddata.BTN_DOWN)     btns |= MASK_DOWN;
        if (paddata.BTN_LEFT)     btns |= MASK_LEFT;
        if (paddata.BTN_RIGHT)    btns |= MASK_RIGHT;
        if (paddata.BTN_CROSS)    btns |= MASK_CROSS;
        if (paddata.BTN_SQUARE)   btns |= MASK_SQUARE;
        if (paddata.BTN_TRIANGLE) btns |= MASK_TRIANGLE;
        if (paddata.BTN_CIRCLE)   btns |= MASK_CIRCLE;
        if (paddata.BTN_L3)       btns |= MASK_L3;
        if (paddata.BTN_START)    btns |= MASK_START;
        if (paddata.BTN_L1)       btns |= MASK_L1;
        if (paddata.BTN_R1)       btns |= MASK_R1;
        if (paddata.BTN_L2)       btns |= MASK_L2;
        if (paddata.BTN_R2)       btns |= MASK_R2;
        if (paddata.BTN_R3)       btns |= MASK_R3;
        break;  /* first connected pad only */
    }
    return btns;
}

static void update_scroll(int selected, int *scroll, int count) {
    if (count <= 0) { *scroll = 0; return; }
    if (selected < *scroll) *scroll = selected;
    if (selected >= *scroll + LIST_VISIBLE) *scroll = selected - LIST_VISIBLE + 1;
}

/* Global pump callback — defined in common.h, set below */
PumpCallbackFn g_pump_callback = NULL;

static SyncState g_state;
static volatile int g_exit_requested = 0;

static void sysutil_cb(u64 status, u64 param, void *userdata) {
    (void)param; (void)userdata;
    switch (status) {
        case SYSUTIL_EXIT_GAME:
            g_exit_requested = 1;
            ui_notify_exit();
            break;
        case SYSUTIL_MENU_OPEN:
            /* PSL1GHT apps do not expose a supported way to suppress the
             * PS/Home menu entirely. On this title, opening it has been
             * freezing some consoles, so treat it as an immediate request
             * to leave the app cleanly instead of trying to stay resident
             * beneath the XMB overlay. */
            g_exit_requested = 1;
            ui_notify_menu_open();
            ui_notify_exit();
            break;
        case SYSUTIL_MENU_CLOSE:
            ui_notify_menu_close();
            break;
        default:
            break;
    }
}

static int g_visible[MAX_TITLES];
static int g_visible_count = 0;
static bool g_show_server_only = true;

typedef struct {
    char path[MAX_FILE_LEN];
    uint32_t size;
    char hash_hex[65];
} FileManifestEntry;

static int find_manifest_entry(const FileManifestEntry *entries, int count, const char *path) {
    for (int i = 0; i < count; i++) {
        if (strcmp(entries[i].path, path) == 0) {
            return i;
        }
    }
    return -1;
}

static int parse_manifest_text(char *text, FileManifestEntry *entries, int max_entries) {
    int count = 0;
    char *line = text;

    while (line && *line && count < max_entries) {
        char *next = strchr(line, '\n');
        char *tab1;
        char *tab2;
        size_t path_len;

        if (next) {
            *next = '\0';
        }
        if (!line[0]) {
            line = next ? (next + 1) : NULL;
            continue;
        }

        tab1 = strchr(line, '\t');
        if (!tab1) {
            line = next ? (next + 1) : NULL;
            continue;
        }
        tab2 = strchr(tab1 + 1, '\t');
        if (!tab2) {
            line = next ? (next + 1) : NULL;
            continue;
        }

        path_len = (size_t)(tab1 - line);
        if (path_len >= sizeof(entries[count].path)) {
            path_len = sizeof(entries[count].path) - 1;
        }
        memcpy(entries[count].path, line, path_len);
        entries[count].path[path_len] = '\0';
        entries[count].size = (uint32_t)strtoul(tab1 + 1, NULL, 10);
        snprintf(entries[count].hash_hex, sizeof(entries[count].hash_hex), "%s", tab2 + 1);
        count++;

        line = next ? (next + 1) : NULL;
    }

    return count;
}

static bool compute_local_file_hash(const TitleInfo *title, const char *name, uint32_t size, char hash_hex_out[65]) {
    uint8_t *buf;
    uint8_t hash[32];

    if (!title || !name || !hash_hex_out) {
        return false;
    }

    if (size == 0) {
        sha256(NULL, 0, hash);
        hash_to_hex(hash, hash_hex_out);
        return true;
    }

    buf = (uint8_t *)malloc(size);
    if (!buf) {
        return false;
    }
    if (saves_read_file(title, name, buf, size) < 0) {
        free(buf);
        return false;
    }
    sha256(buf, size, hash);
    free(buf);
    hash_to_hex(hash, hash_hex_out);
    return true;
}

static void show_file_compare(SyncState *state, const TitleInfo *title) {
    char local_names[MAX_FILES][MAX_FILE_LEN];
    uint32_t local_sizes[MAX_FILES];
    FileManifestEntry local_entries[MAX_FILES];
    FileManifestEntry server_entries[MAX_FILES];
    bool server_seen[MAX_FILES];
    char manifest[16384];
    char message[4096];
    int local_count_raw;
    int local_count = 0;
    int server_count = 0;
    int matched = 0;
    int different = 0;
    int local_only = 0;
    int server_only = 0;
    int lines = 0;
    size_t used = 0;
    int mr;

    if (!state || !title) {
        return;
    }

    if (title->server_only) {
        ui_message("This save only exists on the server.\n\nCreate a local save first to compare files.");
        return;
    }

    local_count_raw = saves_list_files(title, local_names, local_sizes, MAX_FILES);
    if (local_count_raw < 0) {
        ui_message("Failed to read local files for %s.", title->game_code);
        return;
    }

    for (int i = 0; i < local_count_raw && local_count < MAX_FILES; i++) {
        if (title->kind == SAVE_KIND_PS3 && hash_should_skip_ps3_file(local_names[i])) {
            continue;
        }
        snprintf(local_entries[local_count].path, sizeof(local_entries[local_count].path), "%s", local_names[i]);
        local_entries[local_count].size = local_sizes[i];
        if (!compute_local_file_hash(title, local_names[i], local_sizes[i], local_entries[local_count].hash_hex)) {
            ui_message("Failed to hash local file:\n%s", local_names[i]);
            return;
        }
        local_count++;
    }

    ui_status("Fetching server manifest: %s", title->game_code);
    mr = network_get_save_manifest(state, title->title_id, manifest, sizeof(manifest));
    if (mr < 0) {
        ui_message("Failed to fetch server manifest for %s.\n(code %d)", title->game_code, mr);
        return;
    }
    if (mr == 0) {
        server_count = parse_manifest_text(manifest, server_entries, MAX_FILES);
    }
    memset(server_seen, 0, sizeof(server_seen));

    used += (size_t)snprintf(
        message + used, sizeof(message) - used,
        "File compare: %s\n\n", title->game_code
    );

    for (int i = 0; i < local_count; i++) {
        int server_idx = find_manifest_entry(server_entries, server_count, local_entries[i].path);
        const char *label;

        if (server_idx < 0) {
            label = "LOCAL";
            local_only++;
        } else if (strcmp(local_entries[i].hash_hex, server_entries[server_idx].hash_hex) == 0) {
            label = "SYNC";
            matched++;
            server_seen[server_idx] = true;
        } else {
            label = "DIFF";
            different++;
            server_seen[server_idx] = true;
        }

        if (lines < 14 && used < sizeof(message)) {
            used += (size_t)snprintf(
                message + used, sizeof(message) - used,
                "[%s] %s\n", label, local_entries[i].path
            );
            lines++;
        }
    }

    for (int i = 0; i < server_count; i++) {
        if (server_seen[i]) {
            continue;
        }
        server_only++;
        if (lines < 14 && used < sizeof(message)) {
            used += (size_t)snprintf(
                message + used, sizeof(message) - used,
                "[SERVER] %s\n", server_entries[i].path
            );
            lines++;
        }
    }

    if ((local_count + server_only) > lines && used < sizeof(message)) {
        used += (size_t)snprintf(
            message + used, sizeof(message) - used,
            "...\n"
        );
    }

    if (used < sizeof(message)) {
        snprintf(
            message + used, sizeof(message) - used,
            "\nSynced: %d  Different: %d  Local only: %d  Server only: %d",
            matched, different, local_only, server_only
        );
    }

    ui_message("%s", message);
}

static void fetch_selected_server_meta(const SyncState *state, TitleInfo *title) {
    char last_sync[32] = "";
    uint32_t server_size = 0;
    int r;

    if (!state || !title || !state->network_connected || !title->on_server || title->server_meta_loaded) {
        return;
    }

    title->server_hash[0] = '\0';
    r = network_get_save_info(state, title->title_id, title->server_hash, &server_size, last_sync);
    title->server_size = (r == 0) ? server_size : 0;
    title->server_meta_loaded = true;
    if (r != 0) {
        title->server_hash[0] = '\0';
    }
}

static void rebuild_visible(const SyncState *state) {
    int i;
    g_visible_count = 0;
    for (i = 0; i < state->num_titles; i++) {
        if (!g_show_server_only && state->titles[i].server_only) continue;
        g_visible[g_visible_count++] = i;
    }
}

/* Find the next (dir=+1) or previous (dir=-1) user that has a savedata dir */
static int find_adjacent_user(int current, int dir) {
    char path[PATH_LEN];
    for (int step = 1; step <= 16; step++) {
        int uid = current + dir * step;
        if (uid < 1)  uid += 16;
        if (uid > 16) uid -= 16;
        snprintf(path, sizeof(path), "/dev_hdd0/home/%08d/savedata", uid);
        struct stat st;
        if (stat(path, &st) == 0 && S_ISDIR(st.st_mode))
            return uid;
    }
    return current;  /* no other user found */
}

static void rescan(SyncState *state, char *status, size_t status_sz) {
    saves_scan(state);
    /* Hashing deferred to sync time */
    snprintf(status, status_sz, "Scanned %d save(s).", state->num_titles);
}

static void sync_progress_cb(const char *msg) {
    ui_status("%s", msg);
}

static const char *title_status_label(TitleStatus status) {
    switch (status) {
        case TITLE_STATUS_LOCAL_ONLY:  return "LOC";
        case TITLE_STATUS_SERVER_ONLY: return "SVR";
        case TITLE_STATUS_SYNCED:      return "SYNC";
        case TITLE_STATUS_UPLOAD:      return "UP";
        case TITLE_STATUS_DOWNLOAD:    return "DL";
        case TITLE_STATUS_CONFLICT:    return "CONF";
        default:                       return "?";
    }
}

/* Pump system callbacks + SDL events — used as g_pump_callback so that
 * long-running operations (zlib, SHA-256, file I/O) in sync/bundle/hash
 * modules keep the PS3 Lv2 kernel happy.  Without this, the kernel
 * considers the app frozen and force-kills it after a few seconds. */
static void pump_all_callbacks(void) {
    sysUtilCheckCallback();
    SDL_PumpEvents();
}

/* Callback for network transfers: pumps both sysutil and SDL events.
 *
 * On real PS3 firmware (unlike RPCS3), failing to call sysUtilCheckCallback()
 * for several seconds during blocking network I/O causes the system to
 * consider the app frozen and force-close it.  SDL_PumpEvents() is also
 * needed to prevent the video subsystem from stalling.
 *
 * The previous version avoided SDL_PumpEvents()/ui_status() here due to
 * stack depth concerns, but the network code now calls this callback
 * *between* send/recv iterations (not nested inside them), so the stack
 * is shallow enough. */
static int net_progress_cb(uint32_t downloaded, int total) {
    sysUtilCheckCallback();
    SDL_PumpEvents();
    return ui_exit_requested();
}

int main(void) {
    SyncState *state = &g_state;
    char error_buf[512];
    char status_line[256];
    char savedata_root[PATH_LEN];
    char vmc_root[PATH_LEN];
    bool config_created = false;
    int configured_user = 0;
    int selected = 0, scroll = 0;
    int last_selected_title = -1;
    int start_hold_frames = 0;
    unsigned int prev_buttons = 0;
    bool redraw = true;

    memset(state, 0, sizeof(*state));
    debug_log_open();
    debug_log("ps3sync starting v%s", APP_VERSION);

    /* --- UI init --- */
    if (!ui_init(error_buf, sizeof(error_buf))) {
        debug_log("ui_init failed: %s", error_buf);
        debug_log_close();
        return 1;
    }

    /* Init pad early so ui_message/drain_buttons work from the start */
    ioPadInit(PAD_COUNT);

    /* Register sysutil callback so the PS button / XMB exit works cleanly */
    sysUtilRegisterCallback(0, sysutil_cb, NULL);

    /* Register network progress callback so downloads pump sysutil */
    network_set_progress_cb(net_progress_cb);

    /* Set global pump callback for sync/bundle/hash modules */
    g_pump_callback = pump_all_callbacks;

    /* --- Config --- */
    ui_status("Loading config...");
    if (!config_load(state, &config_created, error_buf, sizeof(error_buf))) {
        debug_log("config error: %s", error_buf);
        ui_draw_message("GameSync PS3", error_buf, "Press START to exit");
        while (1) {
            SDL_PumpEvents();
            if (read_buttons() & MASK_START) break;
            usleep(100000);
        }
        ioPadEnd();
        ui_shutdown();
        debug_log_close();
        return 1;
    }
    config_load_console_id(state);
    configured_user = state->selected_user;
    debug_log("config ok  server=%s user=%s", state->server_url, state->ps3_user);

    /* --- Resign subsystem init (detects PSID, sets up crypto keys) --- */
    ui_status("Initializing resign engine...");
    if (!resign_init()) {
        debug_log("resign_init failed — downloads will not be resigned");
    }

    /* --- Game keys database (for HDD save decryption) --- */
    ui_status("Loading game keys...");
    {
        debug_log("gamekeys: opening %s", GAMES_CONF_PATH);
        FILE *gkf = fopen(GAMES_CONF_PATH, "rb");
        if (gkf) {
            fseek(gkf, 0, SEEK_END);
            long gk_sz = ftell(gkf);
            fseek(gkf, 0, SEEK_SET);
            debug_log("gamekeys: file size = %ld bytes", gk_sz);
            if (gk_sz > 0 && gk_sz < 4 * 1024 * 1024) {
                char *gk_buf = (char *)malloc((size_t)gk_sz);
                if (gk_buf) {
                    size_t rd = fread(gk_buf, 1, (size_t)gk_sz, gkf);
                    debug_log("gamekeys: read %u of %ld bytes", (unsigned)rd, gk_sz);
                    if (rd == (size_t)gk_sz) {
                        bool ok = gamekeys_init(gk_buf, (size_t)gk_sz);
                        debug_log("gamekeys: init returned %d, is_loaded=%d",
                                  (int)ok, (int)gamekeys_is_loaded());
                    } else {
                        debug_log("gamekeys: short read (%u != %ld)", (unsigned)rd, gk_sz);
                    }
                    free(gk_buf);
                } else {
                    debug_log("gamekeys: malloc(%ld) failed", gk_sz);
                }
            } else {
                debug_log("gamekeys: bad file size %ld", gk_sz);
            }
            fclose(gkf);
        } else {
            debug_log("gamekeys: fopen FAILED for %s — HDD save decryption disabled",
                      GAMES_CONF_PATH);
        }
        debug_log("gamekeys: final is_loaded=%d", (int)gamekeys_is_loaded());
    }

    /* --- Network --- */
    ui_status("Initializing network...");
    bool has_net = false;
    if (network_init() == 0) {
        ui_status("Checking server...");
        if (network_check_server(state)) {
            state->network_connected = true;
            has_net = true;
            debug_log("server reachable");
        } else {
            debug_log("server unreachable");
            ui_message("Cannot reach server at:\n%s\n\n"
                       "Check server_url in config.txt.\n\n"
                       "Continuing offline.",
                       state->server_url);
        }
    } else {
        ui_message("Network init failed.\nContinuing offline.");
    }

    /* --- Scan saves --- */
    ui_status("Scanning saves...");
    apollo_get_ps3_savedata_root(state, savedata_root, sizeof(savedata_root));
    apollo_get_ps1_vmc_root(vmc_root, sizeof(vmc_root));
    saves_scan(state);
    if (configured_user == 0 && state->selected_user > 0) {
        snprintf(state->ps3_user, sizeof(state->ps3_user), "%08d", state->selected_user);
        config_save(state);
        debug_log("persisted auto-detected user %08d", state->selected_user);
    }
    /* Hashing is deferred — sync_decide/sync_execute compute it on demand */

    if (has_net) {
        ui_status("Checking server saves...");
        network_merge_server_titles(state);
        ui_status("Fetching game names...");
        network_fetch_names(state);
        sync_refresh_statuses(state, sync_progress_cb);
    }

    rebuild_visible(state);
    if (has_net && g_visible_count > 0) {
        fetch_selected_server_meta(state, &state->titles[g_visible[selected]]);
        last_selected_title = g_visible[selected];
    }
    snprintf(status_line, sizeof(status_line),
             "Found %d save(s). %s",
             state->num_titles,
             has_net ? "Server connected." : "Offline.");

    while (1) {
        /* Pump SDL events and system callbacks every frame */
        SDL_PumpEvents();
        sysUtilCheckCallback();

        if (g_exit_requested || ui_exit_requested()) {
            debug_log("exit requested");
            break;
        }

        if (ui_menu_open()) {
            usleep(50000);
            continue;
        }

        unsigned int btns = read_buttons();
        unsigned int just = btns & ~prev_buttons;
        prev_buttons = btns;

        /* Exit only after START is held for a short moment.
         * Requiring an exact button state proved too strict on real pads,
         * where extra transient bits can prevent the hold from ever
         * accumulating. */
        if (start_exit_active(btns)) {
            if (start_hold_frames < START_EXIT_HOLD_FRAMES) {
                start_hold_frames++;
                if (just & MASK_START) {
                    snprintf(status_line, sizeof(status_line),
                             "Hold START to exit.");
                    redraw = true;
                }
            }
            if (start_hold_frames >= START_EXIT_HOLD_FRAMES) {
                debug_log("exit via held START");
                break;
            }
        } else {
            start_hold_frames = 0;
        }

        /* Navigation */
        if ((just & MASK_DOWN) && g_visible_count > 0) {
            selected = (selected + 1) % g_visible_count;
            update_scroll(selected, &scroll, g_visible_count);
            if (has_net) last_selected_title = -1;
            redraw = true;
        }
        if ((just & MASK_UP) && g_visible_count > 0) {
            selected = (selected - 1 + g_visible_count) % g_visible_count;
            update_scroll(selected, &scroll, g_visible_count);
            if (has_net) last_selected_title = -1;
            redraw = true;
        }
        if ((just & MASK_RIGHT) && g_visible_count > 0) {
            selected += LIST_VISIBLE;
            if (selected >= g_visible_count) selected = g_visible_count - 1;
            update_scroll(selected, &scroll, g_visible_count);
            if (has_net) last_selected_title = -1;
            redraw = true;
        }
        if ((just & MASK_LEFT) && g_visible_count > 0) {
            selected -= LIST_VISIBLE;
            if (selected < 0) selected = 0;
            update_scroll(selected, &scroll, g_visible_count);
            if (has_net) last_selected_title = -1;
            redraw = true;
        }

        /* L2 / R2: cycle PS3 user profile */
        if ((just & MASK_L2) || (just & MASK_R2)) {
            int dir = (just & MASK_R2) ? 1 : -1;
            int next = find_adjacent_user(state->selected_user, dir);
            if (next != state->selected_user) {
                state->selected_user = next;
                snprintf(state->ps3_user, sizeof(state->ps3_user),
                         "%08d", next);
                config_save(state);
                ui_status("Switching to user %08d...", next);
                saves_scan(state);
                if (has_net) {
                    ui_status("Refreshing server list...");
                    network_merge_server_titles(state);
                    network_fetch_names(state);
                    sync_refresh_statuses(state, sync_progress_cb);
                }
                rebuild_visible(state);
                selected = 0;
                scroll = 0;
                last_selected_title = -1;
                snprintf(status_line, sizeof(status_line),
                         "User %08d — %d save(s).", next, state->num_titles);
            }
            redraw = true;
        }

        /* L1: toggle server-only filter */
        if (just & MASK_L1) {
            g_show_server_only = !g_show_server_only;
            rebuild_visible(state);
            if (selected >= g_visible_count)
                selected = g_visible_count > 0 ? g_visible_count - 1 : 0;
            update_scroll(selected, &scroll, g_visible_count);
            if (has_net) last_selected_title = -1;
            redraw = true;
        }

        /* R1: compare local files against the server copy */
        if ((just & MASK_R1) && has_net && g_visible_count > 0) {
            show_file_compare(state, &state->titles[g_visible[selected]]);
            redraw = true;
        }

        /* R3: smart sync all saves, skipping conflicts */
        if (just & MASK_R3) {
            if (!has_net) {
                ui_message("Server is offline.\n\nConnect to GameSync first to sync all saves.");
            } else if (state->num_titles <= 0) {
                ui_message("No saves found to sync.");
            } else {
                SyncSummary summary;
                ui_status("Syncing all saves...");
                sync_auto_all(state, &summary, sync_progress_cb);
                for (int i = 0; i < state->num_titles; i++) {
                    state->titles[i].server_meta_loaded = false;
                    state->titles[i].server_hash[0] = '\0';
                }
                snprintf(
                    status_line,
                    sizeof(status_line),
                    "All sync: %d up, %d down, %d current, %d conflicts, %d skipped, %d failed.",
                    summary.uploaded,
                    summary.downloaded,
                    summary.up_to_date,
                    summary.conflicts,
                    summary.skipped,
                    summary.failed
                );
                ui_message(
                    "Sync all finished.\n\n"
                    "Uploaded: %d\n"
                    "Downloaded: %d\n"
                    "Up to date: %d\n"
                    "Conflicts skipped: %d\n"
                    "Skipped: %d\n"
                    "Failed: %d",
                    summary.uploaded,
                    summary.downloaded,
                    summary.up_to_date,
                    summary.conflicts,
                    summary.skipped,
                    summary.failed
                );
                if (g_visible_count > 0 && selected < g_visible_count) {
                    last_selected_title = -1;
                }
            }
            redraw = true;
        }

        /* Cross (X): smart sync */
        if ((just & MASK_CROSS) && has_net && g_visible_count > 0) {
            TitleInfo *title = &state->titles[g_visible[selected]];
            if (title->kind == SAVE_KIND_PS3 && title->server_only) {
                ui_message("This PS3 save only exists on the server.\n\n"
                           "Create a save in the game first, then sync again.");
                redraw = true;
                continue;
            }
            ui_status("Analyzing %s...", title->game_code);

            SyncAction action = sync_decide(state, g_visible[selected]);

            char server_hash[65] = "";
            uint32_t server_size = 0;
            char server_last_sync[32] = "";
            network_get_save_info(state, title->title_id,
                                  server_hash, &server_size, server_last_sync);

            if (ui_confirm(title, action, server_hash, server_size, server_last_sync)) {
                ui_status("%s %s...",
                          action == SYNC_UPLOAD ? "Uploading" : "Downloading",
                          title->game_code);
                int r = sync_execute(state, g_visible[selected], action);
                if (r == 0) {
                    title->server_meta_loaded = false;
                    title->server_hash[0] = '\0';
                    ui_message("Done! (%s)", title->game_code);
                } else
                    ui_message("Failed! (code %d)\n\n"
                               "-2=read/hash error\n"
                               "-3=bundle error\n"
                               "-4=network/server error\n"
                               "-5=write error\n"
                               "-6=no upload source (decrypt/export)\n"
                               "-7=create a local save first\n\n"
                               "See %s for details.", r, DEBUG_LOG_FILE);
            }
            redraw = true;
        }

        /* Square (□): force upload */
        if ((just & MASK_SQUARE) && has_net && g_visible_count > 0) {
            TitleInfo *title = &state->titles[g_visible[selected]];
            if (title->server_only) {
                if (title->kind == SAVE_KIND_PS3) {
                    ui_message("This PS3 save only exists on the server.\n\n"
                               "Create a save in the game first, then sync again.");
                } else {
                    ui_message("This save only exists on the server.\nDownload it first (Triangle).");
                }
            } else {
                char server_hash[65] = "";
                uint32_t server_size = 0;
                char server_last_sync[32] = "";
                network_get_save_info(state, title->title_id,
                                      server_hash, &server_size, server_last_sync);
                if (ui_confirm(title, SYNC_UPLOAD, server_hash, server_size, server_last_sync)) {
                    ui_status("Uploading %s...", title->game_code);
                    int r = sync_execute(state, g_visible[selected], SYNC_UPLOAD);
                    if (r == 0) {
                        title->server_meta_loaded = false;
                        title->server_hash[0] = '\0';
                        ui_message("Upload OK!");
                    }
                    else        ui_message("Upload failed! (code %d)", r);
                }
            }
            redraw = true;
        }

        /* Triangle (△): force download */
        if ((just & MASK_TRIANGLE) && has_net && g_visible_count > 0) {
            TitleInfo *title = &state->titles[g_visible[selected]];
            if (title->kind == SAVE_KIND_PS3 && title->server_only) {
                ui_message("This PS3 save only exists on the server.\n\n"
                           "Create a save in the game first, then sync again.");
                redraw = true;
                continue;
            }
            char server_hash[65] = "";
            uint32_t server_size = 0;
            char server_last_sync[32] = "";
            network_get_save_info(state, title->title_id,
                                  server_hash, &server_size, server_last_sync);
            if (ui_confirm(title, SYNC_DOWNLOAD, server_hash, server_size, server_last_sync)) {
                ui_status("Downloading %s...", title->game_code);
                int r = sync_execute(state, g_visible[selected], SYNC_DOWNLOAD);
                if (r == 0) {
                    title->server_meta_loaded = false;
                    title->server_hash[0] = '\0';
                    ui_message("Download OK!");
                }
                else        ui_message("Download failed! (code %d)", r);
            }
            redraw = true;
        }

        /* L3: hash selected save now and refresh its status */
        if ((just & MASK_L3) && g_visible_count > 0) {
            TitleInfo *title = &state->titles[g_visible[selected]];
            title->hash_calculated = false;
            ui_status("Hashing %s...", title->game_code);
            if (saves_compute_hash(title) == 0) {
                if (has_net) {
                    if (title->server_only) {
                        title->status = TITLE_STATUS_SERVER_ONLY;
                    } else {
                        SyncAction action = sync_decide(state, g_visible[selected]);
                        if (action == SYNC_UP_TO_DATE) {
                            title->status = TITLE_STATUS_SYNCED;
                        } else if (action == SYNC_FAILED) {
                            title->status = TITLE_STATUS_UNKNOWN;
                        }
                    }
                    title->server_meta_loaded = false;
                    title->server_hash[0] = '\0';
                } else if (!title->on_server) {
                    title->status = TITLE_STATUS_LOCAL_ONLY;
                }
                ui_message("Hash refreshed for %s.\n\nStatus: %s",
                           title->game_code, title_status_label(title->status));
            } else {
                title->status = TITLE_STATUS_UNKNOWN;
                ui_message("Hash failed for %s.", title->game_code);
            }
            redraw = true;
        }

        /* Circle (○): rescan + rehash */
        if (just & MASK_CIRCLE) {
            ui_status("Rescanning saves...");
            rescan(state, status_line, sizeof(status_line));
            if (has_net) {
                ui_status("Refreshing server list...");
                network_merge_server_titles(state);
                network_fetch_names(state);
                sync_refresh_statuses(state, sync_progress_cb);
            }
            rebuild_visible(state);
            if (selected >= g_visible_count)
                selected = g_visible_count > 0 ? g_visible_count - 1 : 0;
            update_scroll(selected, &scroll, g_visible_count);
            if (has_net) last_selected_title = -1;
            redraw = true;
        }

        if (has_net && g_visible_count > 0 && (last_selected_title != g_visible[selected])) {
            fetch_selected_server_meta(state, &state->titles[g_visible[selected]]);
            last_selected_title = g_visible[selected];
            redraw = true;
        }

        if (redraw) {
            ui_draw_list(state, g_visible, g_visible_count, selected, scroll,
                         status_line, config_created, g_show_server_only);
            redraw = false;
        }

        usleep(16000);  /* ~60 fps */
    }

    ioPadEnd();
    gamekeys_shutdown();
    network_cleanup();
    ui_shutdown();
    debug_log_close();
    return 0;
}
