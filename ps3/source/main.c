/*
 * PS3 GameSync - Main
 *
 * Syncs PS3 and PS1 saves with the GameSync server over WiFi, and also
 * browses + downloads ROMs (.iso -> /dev_hdd0/PS3ISO, .pkg ->
 * /dev_hdd0/packages, PS1 -> /dev_hdd0/PSXISO/<game>/).  Four top-level
 * tabs, cycled (wrapping) with L1 / R1:
 *
 *   1. Saves         the save sync flow
 *   2. ROM Catalog   server-side ROM library (PS3 / PS1)
 *   3. Downloads     pause/resume queue for ROM downloads
 *   4. Settings      config editor + "Refresh catalog"
 *
 * Shared controls (the same scheme as every GameSync console client):
 *   Up / Down        Move one row (hold to repeat)
 *   Left / Right     Page up / down (hold to repeat)
 *   L1 / R1          Previous / next tab
 *   SELECT           Cycle the tab's sub-tabs (Saves: All/PS3/PS1 filter,
 *                    Catalog: PS3/PS1)
 *   Cross            Confirm / primary action of the focused row
 *   Circle           Cancel / back; pauses a running download
 *   START            Exit the app (asks first)
 *
 * Saves:      Cross smart sync, Square sync all, Triangle details/actions
 *             (force upload, force download, compare, rehash, rescan).
 * Catalog:    Cross download / resume, Triangle details.
 * Downloads:  Cross start/resume, Square clear finished, Triangle options
 *             (start, remove), Circle pause while a download runs.
 * Settings:   Up/Down select, Left/Right change value, Cross edit / toggle /
 *             run, Circle discard changes and go back to Saves.
 *
 * Cross / Circle are read as physical buttons, so Cross confirms even on a
 * console set to the Japanese "Circle = enter" convention.
 */

#include "apollo.h"
#include "catalog_cache.h"
#include "common.h"
#include "config.h"
#include "debug.h"
#include "downloads.h"
#include "gamekeys.h"
#include "hash.h"
#include "network.h"
#include "resign.h"
#include "roms.h"
#include "saves.h"
#include "sha256.h"
#include "state.h"
#include "sync.h"
#include "ui.h"
#include "zip_extract.h"

#include <SDL/SDL.h>
#include <io/pad.h>
#include <sysutil/sysutil.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <time.h>
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
#define MASK_L1       (1U << 9)
#define MASK_L2       (1U << 10)
#define MASK_R2       (1U << 11)
#define MASK_R1       (1U << 12)
#define MASK_R3       (1U << 13)
#define MASK_START    (1U << 14)
#define MASK_SELECT   (1U << 15)

/* MAX_PADS is already defined in <io/pad.h> as 127; we use a smaller cap */
#define PAD_COUNT    7
#define LIST_VISIBLE UI_LIST_ROWS

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
        if (paddata.BTN_L1)       btns |= MASK_L1;
        if (paddata.BTN_R1)       btns |= MASK_R1;
        if (paddata.BTN_L2)       btns |= MASK_L2;
        if (paddata.BTN_R2)       btns |= MASK_R2;
        if (paddata.BTN_R3)       btns |= MASK_R3;
        if (paddata.BTN_START)    btns |= MASK_START;
        if (paddata.BTN_SELECT)   btns |= MASK_SELECT;
        break;  /* first connected pad only */
    }
    return btns;
}

#define MASK_DPAD (MASK_UP | MASK_DOWN | MASK_LEFT | MASK_RIGHT)
#define REPEAT_DELAY_MS 350
#define REPEAT_RATE_MS  70

/* Edge detection plus auto-repeat for a held D-pad direction. */
typedef struct {
    unsigned int prev;
    Uint32       held_since;
    Uint32       last_repeat;
    unsigned     dialog_serial;
} InputState;

/* Newly pressed buttons this frame (D-pad repeats while held).  `held_out`
 * receives the live mask.  After a dialog has read the pad the frame is
 * skipped, so the button that closed it does not also act on the view. */
static unsigned int input_poll(InputState *in, unsigned int *held_out) {
    unsigned int btns = read_buttons();
    unsigned int just;
    Uint32 now = SDL_GetTicks();

    if (held_out) *held_out = btns;
    if (in->dialog_serial != ui_dialog_serial()) {
        in->dialog_serial = ui_dialog_serial();
        in->prev = btns;
        return 0;
    }
    just = btns & ~in->prev;
    if (just & MASK_DPAD) {
        in->held_since  = now;
        in->last_repeat = now;
    } else if ((btns & MASK_DPAD) &&
               now - in->held_since >= REPEAT_DELAY_MS &&
               now - in->last_repeat >= REPEAT_RATE_MS) {
        just |= btns & MASK_DPAD;
        in->last_repeat = now;
    }
    in->prev = btns;
    return just;
}

/* Forget held buttons (after a nested loop such as the text editor). */
static void input_resync(InputState *in) {
    in->prev = read_buttons();
    in->dialog_serial = ui_dialog_serial();
}

/* Up/Down one row (wrapping), Left/Right one page (clamped).  Returns true
 * when the selection moved. */
static bool list_nav(unsigned int just, int *selected, int count) {
    int old = *selected;
    if (count <= 0) return false;
    if (just & MASK_DOWN)  *selected = (*selected + 1) % count;
    if (just & MASK_UP)    *selected = (*selected - 1 + count) % count;
    if (just & MASK_RIGHT) {
        *selected += UI_LIST_ROWS;
        if (*selected >= count) *selected = count - 1;
    }
    if (just & MASK_LEFT) {
        *selected -= UI_LIST_ROWS;
        if (*selected < 0) *selected = 0;
    }
    return *selected != old;
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

/* Saves sub-tabs (SELECT): which kinds of save are listed. */
static const char *const G_SAVE_FILTERS[] = { "All", "PS3", "PS1" };
#define G_SAVE_FILTER_COUNT 3
static int g_save_filter = 0;
static void sync_progress_cb(const char *msg);

typedef struct {
    char path[MAX_FILE_LEN];
    uint32_t size;
    char hash_hex[65];
} FileManifestEntry;

typedef struct {
    char server_url[256];
    char api_key[128];
    int selected_user;
    bool scan_ps3;
    bool scan_ps1;
    bool show_server_only;
} ConfigDraft;

static int find_manifest_entry(const FileManifestEntry *entries, int count, const char *path) {
    for (int i = 0; i < count; i++) {
        if (strcmp(entries[i].path, path) == 0) {
            return i;
        }
    }
    return -1;
}

static void show_fake_usb_stage_result(const TitleInfo *title) {
    bool activated;

    if (!title) {
        return;
    }

    activated = network_activate_fake_usb();
    if (activated) {
        ui_message(
            "Staged to Fake USB: %s\n\n"
            "webMAN refreshed dev_usb000.\n"
            "Open XMB Saved Data Utility and copy it from the Fake USB view.",
            title->game_code
        );
    } else {
        ui_message(
            "Staged to Fake USB: %s\n\n"
            "webMAN auto-refresh was not available.\n"
            "If the Fake USB view does not appear, refresh it manually and then continue from XMB.",
            title->game_code
        );
    }
}

static void show_ps1_download_result(const TitleInfo *title) {
    if (!title) {
        return;
    }

    ui_message(
        "PS1 card downloaded: %s\n\n"
        "If the PS3 does not recognize the updated card immediately, run Rebuild Database.",
        title->game_code
    );
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

static void config_draft_from_state(ConfigDraft *draft, const SyncState *state) {
    if (!draft || !state) {
        return;
    }
    memset(draft, 0, sizeof(*draft));
    strncpy(draft->server_url, state->server_url, sizeof(draft->server_url) - 1);
    strncpy(draft->api_key, state->api_key, sizeof(draft->api_key) - 1);
    draft->selected_user = state->selected_user;
    draft->scan_ps3 = state->scan_ps3;
    draft->scan_ps1 = state->scan_ps1;
    draft->show_server_only = state->show_server_only;
}

static void config_draft_apply(SyncState *state, const ConfigDraft *draft) {
    if (!state || !draft) {
        return;
    }

    strncpy(state->server_url, draft->server_url, sizeof(state->server_url) - 1);
    state->server_url[sizeof(state->server_url) - 1] = '\0';
    strncpy(state->api_key, draft->api_key, sizeof(state->api_key) - 1);
    state->api_key[sizeof(state->api_key) - 1] = '\0';
    state->selected_user = draft->selected_user;
    state->scan_ps3 = draft->scan_ps3;
    state->scan_ps1 = draft->scan_ps1;
    state->show_server_only = draft->show_server_only;

    if (state->selected_user > 0) {
        snprintf(state->ps3_user, sizeof(state->ps3_user),
                 "%08d", state->selected_user);
    } else {
        strncpy(state->ps3_user, "00000001", sizeof(state->ps3_user) - 1);
        state->ps3_user[sizeof(state->ps3_user) - 1] = '\0';
    }
    config_load_console_id(state);
}

static const char *text_editor_charset(void) {
    return " abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789:/._-?&=%+[]()@,";
}

static int charset_index_for_char(char c) {
    const char *charset = text_editor_charset();
    const char *p = strchr(charset, c);
    return p ? (int)(p - charset) : 0;
}

static bool run_text_editor(const char *label, char *value, size_t value_size) {
    InputState in;
    int cursor = 0;
    char original[256];
    const char *charset = text_editor_charset();
    int charset_len = (int)strlen(charset);

    if (!value || value_size == 0) {
        return false;
    }

    strncpy(original, value, sizeof(original) - 1);
    original[sizeof(original) - 1] = '\0';
    cursor = (int)strlen(value);
    memset(&in, 0, sizeof(in));
    input_resync(&in);

    while (1) {
        unsigned int btns;
        unsigned int just;
        int len;

        SDL_PumpEvents();
        sysUtilCheckCallback();
        if (g_exit_requested || ui_exit_requested()) {
            return false;
        }
        if (ui_menu_open()) {
            usleep(50000);
            continue;
        }

        ui_draw_text_editor(label, value, cursor);

        just = input_poll(&in, &btns);
        len = (int)strlen(value);

        if (just & MASK_LEFT) {
            if (cursor > 0) cursor--;
        }
        if (just & MASK_RIGHT) {
            if (cursor < len) cursor++;
        }
        if (just & MASK_UP) {
            int idx;
            if (cursor >= len) {
                if ((size_t)len + 1 < value_size) {
                    value[len] = ' ';
                    value[len + 1] = '\0';
                    len++;
                } else {
                    usleep(50000);
                    continue;
                }
            }
            idx = charset_index_for_char(value[cursor]);
            idx = (idx + 1) % charset_len;
            value[cursor] = charset[idx];
        }
        if (just & MASK_DOWN) {
            int idx;
            if (cursor >= len) {
                if ((size_t)len + 1 < value_size) {
                    value[len] = ' ';
                    value[len + 1] = '\0';
                    len++;
                } else {
                    usleep(50000);
                    continue;
                }
            }
            idx = charset_index_for_char(value[cursor]);
            idx = (idx - 1 + charset_len) % charset_len;
            value[cursor] = charset[idx];
        }
        if (just & MASK_SQUARE) {
            if ((size_t)len + 1 < value_size) {
                memmove(value + cursor + 1, value + cursor, (size_t)(len - cursor + 1));
                value[cursor] = ' ';
            }
        }
        if (just & MASK_TRIANGLE) {
            if (len > 0) {
                if (cursor < len) {
                    memmove(value + cursor, value + cursor + 1, (size_t)(len - cursor));
                } else {
                    value[len - 1] = '\0';
                    cursor = len - 1;
                }
            }
        }
        if (just & MASK_CROSS) {
            return true;
        }
        if (just & MASK_CIRCLE) {
            strncpy(value, original, value_size - 1);
            value[value_size - 1] = '\0';
            return false;
        }

        usleep(50000);
    }
}

/* "Save and apply" in the Settings tab: write config.txt, rescan the saves
 * and reconnect.  Leaves the result in status_line. */
static void apply_config_draft(SyncState *state, const ConfigDraft *draft, bool *has_net,
                               char *status_line, size_t status_line_sz) {
    ui_status("Applying config...");
    config_draft_apply(state, draft);
    config_save(state);

    state->network_connected = false;
    *has_net = false;
    saves_scan(state);
    if (draft->selected_user == 0 && state->selected_user > 0) {
        snprintf(state->ps3_user, sizeof(state->ps3_user),
                 "%08d", state->selected_user);
        config_save(state);
    }
    if (network_check_server(state)) {
        state->network_connected = true;
        *has_net = true;
        network_merge_server_titles(state);
        network_fetch_names(state);
        sync_refresh_statuses(state, sync_progress_cb);
    }
    ui_set_online(*has_net);
    snprintf(status_line, status_line_sz,
             "Config applied. %d save(s). %s",
             state->num_titles,
             *has_net ? "Server connected." : "Offline.");
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
    if (title->kind == SAVE_KIND_PS1 || title->kind == SAVE_KIND_PS1_VM1) {
        char local_hash[65];
        char server_hash[65] = "";
        uint32_t server_size = 0;
        int sr;

        if (!title->hash_calculated && saves_compute_hash((TitleInfo *)title) < 0) {
            ui_message("Failed to hash local PS1 card for %s.", title->game_code);
            return;
        }

        hash_to_hex(title->hash, local_hash);
        sr = network_get_save_info(state, title, server_hash, &server_size, NULL);
        if (sr == 1) {
            ui_message("Server compare: %s\n\nLocal card exists, but no server save was found.",
                       title->game_code);
            return;
        }
        if (sr < 0) {
            ui_message("Failed to fetch server PS1 card info for %s.\n(code %d)",
                       title->game_code, sr);
            return;
        }

        ui_message(
            "PS1 card compare: %s\n\nLocal hash:  %.64s\nServer hash: %.64s\n\nLocal size:  %u\nServer size: %u\n\n%s",
            title->game_code,
            local_hash,
            server_hash,
            title->total_size,
            server_size,
            strcmp(local_hash, server_hash) == 0 ? "Cards match." : "Cards differ."
        );
        return;
    }

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
    r = network_get_save_info(state, title, title->server_hash, &server_size, last_sync);
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
        const TitleInfo *t = &state->titles[i];
        if (!state->show_server_only && t->server_only) continue;
        if (g_save_filter == 1 && t->kind != SAVE_KIND_PS3) continue;
        if (g_save_filter == 2 && t->kind == SAVE_KIND_PS3) continue;
        g_visible[g_visible_count++] = i;
    }
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
        case TITLE_STATUS_LOCAL_ONLY:  return "only on this PS3";
        case TITLE_STATUS_SERVER_ONLY: return "only on the server";
        case TITLE_STATUS_SYNCED:      return "up to date";
        case TITLE_STATUS_UPLOAD:      return "changed here - upload";
        case TITLE_STATUS_DOWNLOAD:    return "newer on the server - download";
        case TITLE_STATUS_CONFLICT:    return "conflict - both changed";
        default:                       return "not compared yet";
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

/* ---- ROM catalog + download globals (accessed by main loop only) ---- */

static AppView      g_app_view = APP_VIEW_SAVES;
static RomCatalog   g_rom_catalog;
static DownloadList g_downloads;

static int  g_rom_selected = 0;
static int  g_rom_scroll   = 0;
static int  g_dl_selected  = 0;
static int  g_dl_scroll    = 0;

/* Catalog sub-tabs.  SELECT inside the ROM Catalog view rotates through
 * this list; default is PS3 because that's the system most users will be
 * looking at on a PS3 client. */
static const char *G_ROM_SYSTEMS[] = { "PS3", "PS1" };
#define G_ROM_SYSTEM_COUNT ((int)(sizeof(G_ROM_SYSTEMS) / sizeof(G_ROM_SYSTEMS[0])))
static int g_rom_system_index = 0;

/* Live progress for the active download — read by ui_draw_downloads when
 * rendered from the progress callback. */
static volatile bool     g_active_in_progress = false;
static volatile uint64_t g_active_downloaded = 0;
static volatile uint64_t g_active_total      = 0;
static volatile uint64_t g_active_bps        = 0;  /* moving-average B/s */
static char              g_active_rom_id[ROM_ID_LEN] = {0};
/* Set by CIRCLE (pause) during an active download.  The progress callback
 * checks it on each chunk so we can pause without race conditions. */
static volatile bool     g_pause_requested = false;

/* Speed sampling: re-anchor every ~2 s so the rate is stable rather than
 * jittering with each 64 KB chunk.  Reset to (0, 0) at run_download
 * start so the first sample begins from the actual download start. */
static uint64_t g_dl_speed_anchor_bytes = 0;
static time_t   g_dl_speed_anchor_time  = 0;

/* Edge-detect CIRCLE while the main loop is blocked inside the
 * download streamer.  read_buttons() returns the live mask; we XOR with
 * the previous reading to find newly-pressed buttons. */
static unsigned int g_dl_prev_buttons = 0;

/* Throttled redraw of the downloads view from inside the progress
 * callback.  A full-screen redraw costs a few milliseconds of PPU time, so
 * it is bounded by wall-clock time (~4 per second) rather than by chunk
 * count — fast links would otherwise spend their time repainting. */
#define PROGRESS_REDRAW_MS 250
static Uint32 g_progress_last_redraw = 0;

static int rom_progress64_cb(uint64_t downloaded, uint64_t total) {
    sysUtilCheckCallback();
    SDL_PumpEvents();

    g_active_downloaded = downloaded;
    if (total > 0) g_active_total = total;

    /* Edge-detect CIRCLE for pause while the main loop is blocked.  We
     * still let ui_exit_requested() short-circuit ahead of the pause
     * check so a PS-button exit during download cleans up immediately. */
    {
        unsigned int btns = read_buttons();
        unsigned int just = btns & ~g_dl_prev_buttons;
        g_dl_prev_buttons = btns;
        if (just & MASK_CIRCLE) g_pause_requested = true;
    }

    if (ui_exit_requested()) return 1;
    if (g_pause_requested)   return 1;

    /* Speed sample — recompute every ~2 s for a stable reading. */
    {
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
    }

    /* Redraw the downloads view periodically so the user sees progress.
     * Other views don't update during a download — switching back is fine,
     * the live counters are visible the moment they re-enter Downloads. */
    if (g_app_view == APP_VIEW_DOWNLOADS) {
        Uint32 now_ms = SDL_GetTicks();
        if (now_ms - g_progress_last_redraw >= PROGRESS_REDRAW_MS) {
            g_progress_last_redraw = now_ms;
            ui_draw_downloads(&g_downloads, g_dl_selected, g_dl_scroll,
                              "Downloading... press Circle to pause.",
                              true, g_active_downloaded, g_active_total,
                              g_active_bps);
        }
    }
    return 0;
}

/* Single-file ROM download.  Used both directly (.iso entries) and as
 * the inner loop for bundles (one call per file in the manifest).
 * Returns the network rc (0 ok / 1 paused / <0 error). */
static int download_single_file(const SyncState *state,
                                const char *rom_id,
                                const char *bundle_file_or_null,
                                const char *target_path,
                                uint64_t start_offset,
                                uint64_t *total_out) {
    if (bundle_file_or_null) {
        return network_download_bundle_file_resumable(
            state, rom_id, bundle_file_or_null, target_path,
            start_offset, total_out);
    }
    return network_download_rom_resumable(
        state, rom_id, target_path, start_offset, total_out);
}

/* Stat a target file's .part to derive a resume offset.  Used by the
 * bundle loop so a paused bundle picks up mid-file rather than mid-list
 * but losing the current file's progress. */
static uint64_t stat_part_offset(const char *target_path) {
    if (!target_path || !target_path[0]) return 0;
    char part[PATH_LEN + 8];
    snprintf(part, sizeof(part), "%s.part", target_path);
    struct stat st;
    if (stat(part, &st) != 0) return 0;
    return (uint64_t)st.st_size;
}

/* Run the named entry to completion (or pause / error) — blocking.  Called
 * from the main event loop.  Updates the persisted downloads.dat so a
 * crash mid-flight doesn't lose progress. */
static void run_download(const SyncState *state, DownloadEntry *e) {
    if (!state || !e) return;

    /* Make sure the destination directory exists.  roms_ensure_target_dirs()
     * is called once at startup but if /dev_hdd0 was remounted we want a
     * second-chance mkdir. */
    roms_ensure_target_dirs();

    /* Auto-switch to the Downloads view so the user sees live progress
     * (file name, percent, speed).  Without this, hitting Cross from the
     * ROM Catalog leaves them staring at a frozen-looking catalog while
     * the download runs blocked in this function. */
    if (g_app_view != APP_VIEW_DOWNLOADS) {
        g_app_view = APP_VIEW_DOWNLOADS;
        /* Highlight the entry that's about to start so the user sees
         * which row in the queue is in flight. */
        for (int i = 0; i < g_downloads.count; i++) {
            if (strcmp(g_downloads.items[i].rom_id, e->rom_id) == 0) {
                g_dl_selected = i;
                update_scroll(g_dl_selected, &g_dl_scroll, g_downloads.count);
                break;
            }
        }
    }
    /* Reset the speed sampler so the first reading starts from this
     * download, not from a stale value left behind by a previous run. */
    g_dl_speed_anchor_bytes = 0;
    g_dl_speed_anchor_time  = 0;
    g_active_bps            = 0;
    g_dl_prev_buttons       = read_buttons();
    g_progress_last_redraw = 0;

    /* Free-space precheck — bail before opening a socket so the user sees
     * an actionable error rather than a half-downloaded .part. */
    uint64_t avail = 0;
    if (e->total > 0 && !roms_check_free_space(e->total, &avail)) {
        e->status = DL_STATUS_ERROR;
        downloads_save(&g_downloads);
        ui_message("Not enough free space on /dev_hdd0.\n\n"
                   "Need: %llu MiB\nFree: %llu MiB",
                   (unsigned long long)(e->total / (1024 * 1024)),
                   (unsigned long long)(avail / (1024 * 1024)));
        return;
    }

    /* Bundle path: fetch manifest, iterate per file, route by extension. */
    if (e->is_bundle) {
        static char manifest_scratch[1 * 1024 * 1024];
        RomBundleManifest manifest;
        memset(&manifest, 0, sizeof(manifest));
        ui_status("Fetching bundle manifest for %s...",
                  e->name[0] ? e->name : e->rom_id);
        if (!roms_fetch_bundle_manifest(state, e->rom_id,
                                        manifest_scratch,
                                        sizeof(manifest_scratch),
                                        &manifest)) {
            e->status = DL_STATUS_ERROR;
            downloads_save(&g_downloads);
            ui_message("Bundle manifest failed:\n%s", manifest.last_error);
            return;
        }
        e->bundle_count = manifest.count;
        if (manifest.total_size > 0) e->total = manifest.total_size;

        if (e->bundle_index >= manifest.count) {
            /* All files already on disk from a previous session. */
            e->status = DL_STATUS_COMPLETED;
            downloads_save(&g_downloads);
            ui_message("Bundle already complete:\n%s",
                       e->name[0] ? e->name : e->rom_id);
            return;
        }

        g_active_in_progress = true;
        g_pause_requested    = false;
        strncpy(g_active_rom_id, e->rom_id, sizeof(g_active_rom_id) - 1);
        g_active_rom_id[sizeof(g_active_rom_id) - 1] = '\0';
        e->status = DL_STATUS_ACTIVE;

        network_set_progress64_cb(rom_progress64_cb);

        int rc = 0;
        for (int idx = e->bundle_index; idx < manifest.count; idx++) {
            const RomBundleFile *bf = &manifest.files[idx];

            char target[PATH_LEN];
            if (!roms_resolve_bundle_file_target(e->system, e->name,
                                                 bf->name, target,
                                                 sizeof(target))) {
                debug_log("download: bundle file %s could not resolve target",
                          bf->name);
                rc = -2;
                break;
            }
            /* PS1 (and any future per-game subfolder rule) needs the
             * parent dir created before the streamer fopens .part. */
            {
                char parent[PATH_LEN];
                strncpy(parent, target, sizeof(parent) - 1);
                parent[sizeof(parent) - 1] = '\0';
                char *slash = strrchr(parent, '/');
                if (slash) {
                    *slash = '\0';
                    roms_mkdir_p(parent);
                }
            }
            /* Ask the FS what's already on disk so a kill mid-file picks
             * up where the .part left off. */
            uint64_t start = stat_part_offset(target);

            /* Update target_path so the UI reflects the current file. */
            strncpy(e->target_path, target, sizeof(e->target_path) - 1);
            e->target_path[sizeof(e->target_path) - 1] = '\0';
            e->offset = start;
            g_active_downloaded = start;
            g_active_total      = bf->size;
            ui_status("Bundle %d/%d: %s",
                      idx + 1, manifest.count, bf->name);

            uint64_t per_total = 0;
            rc = download_single_file(state, e->rom_id, bf->name, target,
                                      start, &per_total);
            if (rc == 0) {
                /* File done — advance bundle index, persist so a pause
                 * before the next file picks up at the right place. */
                e->bundle_index = idx + 1;
                e->offset = 0;
                downloads_save(&g_downloads);
                continue;
            }
            /* Pause / error — keep current bundle_index so resume returns
             * to this file.  The .part on disk preserves byte progress. */
            e->offset = g_active_downloaded;
            break;
        }

        network_set_progress64_cb(NULL);

        if (rc == 0) {
            e->status = DL_STATUS_COMPLETED;
            downloads_save(&g_downloads);
            ui_message(
                "Bundle complete:\n\n%s\n\n"
                "%d files placed under /dev_hdd0/packages and /dev_hdd0/exdata.",
                e->name[0] ? e->name : e->rom_id,
                manifest.count);
        } else if (rc == 1) {
            e->status = DL_STATUS_PAUSED;
            downloads_save(&g_downloads);
            ui_status("Paused bundle %s (%d/%d)",
                      e->name[0] ? e->name : e->rom_id,
                      e->bundle_index + 1, manifest.count);
        } else {
            e->status = DL_STATUS_ERROR;
            downloads_save(&g_downloads);
            ui_message("Bundle failed (code %d) for:\n%s\n\nCheck %s.",
                       rc, e->name[0] ? e->name : e->rom_id, DEBUG_LOG_FILE);
        }

        g_active_in_progress = false;
        g_pause_requested    = false;
        g_active_rom_id[0]   = '\0';
        return;
    }

    /* Single-file path.  PS1 single-file ROMs route under
     * /dev_hdd0/PSXISO/<game>/, so create that parent dir first or
     * fopen will fail.
     *
     * For entries the server marks with an extract format (PS1 .chd →
     * "cue"), we hijack the target path to point at a sibling .zip in
     * the same directory and let the streamer write that.  After the
     * download finishes we extract the ZIP into the original target's
     * parent and delete the staging .zip — so the user ends up with
     * cue+bin sitting at /dev_hdd0/PSXISO/<game>/ exactly the way
     * webMAN's PS1 emulator wants them. */
    char extract_target[PATH_LEN] = {0};
    char extract_dir[PATH_LEN]    = {0};
    bool needs_extract = (e->extract_format[0] != '\0');

    if (needs_extract) {
        /* extract_dir = parent of e->target_path */
        strncpy(extract_dir, e->target_path, sizeof(extract_dir) - 1);
        extract_dir[sizeof(extract_dir) - 1] = '\0';
        char *slash = strrchr(extract_dir, '/');
        if (slash) *slash = '\0';
        /* Stage download as <parent>/<rom_id>.zip — rom_id is URL-safe
         * and unique, avoids collisions when two extracted ROMs share
         * the same stem. */
        snprintf(extract_target, sizeof(extract_target),
                 "%s/%s.zip", extract_dir, e->rom_id);
        roms_mkdir_p(extract_dir);
    } else {
        char parent[PATH_LEN];
        strncpy(parent, e->target_path, sizeof(parent) - 1);
        parent[sizeof(parent) - 1] = '\0';
        char *slash = strrchr(parent, '/');
        if (slash) {
            *slash = '\0';
            roms_mkdir_p(parent);
        }
    }
    const char *download_path = needs_extract ? extract_target : e->target_path;
    g_active_in_progress = true;
    g_active_downloaded  = e->offset;
    g_active_total       = e->total;
    g_pause_requested    = false;
    strncpy(g_active_rom_id, e->rom_id, sizeof(g_active_rom_id) - 1);
    g_active_rom_id[sizeof(g_active_rom_id) - 1] = '\0';

    e->status = DL_STATUS_ACTIVE;

    network_set_progress64_cb(rom_progress64_cb);
    debug_log("download: starting %s offset=%llu total=%llu",
              e->rom_id,
              (unsigned long long)e->offset,
              (unsigned long long)e->total);

    uint64_t total_seen = 0;
    int rc = network_download_rom_resumable_ex(state, e->rom_id,
                                               needs_extract ? e->extract_format : NULL,
                                               download_path,
                                               e->offset, &total_seen);
    network_set_progress64_cb(NULL);

    /* Refresh offset from the streamer's view (handles 200-vs-206 fallthroughs
     * where it had to truncate the .part). */
    e->offset = g_active_downloaded;
    if (total_seen > 0) e->total = total_seen;

    if (rc == 0) {
        if (needs_extract) {
            ui_status("Extracting %s...", e->name[0] ? e->name : e->filename);
            char err[128] = {0};
            bool ok = zip_extract_stored(extract_target, extract_dir,
                                         err, sizeof(err));
            /* Drop the staging .zip whether extraction succeeded or
             * not — the user can retry, but no point keeping a 1 GB
             * intermediate. */
            unlink(extract_target);
            if (!ok) {
                e->status = DL_STATUS_ERROR;
                downloads_save(&g_downloads);
                ui_message("Extract failed for %s:\n%s\n\nCheck %s.",
                           e->name[0] ? e->name : e->filename,
                           err[0] ? err : "(unknown)", DEBUG_LOG_FILE);
                g_active_in_progress = false;
                g_pause_requested    = false;
                g_active_rom_id[0]   = '\0';
                return;
            }
            e->status = DL_STATUS_COMPLETED;
            ui_message(
                "Download complete:\n\n%s\n\nExtracted to:\n%s",
                e->name[0] ? e->name : e->filename, extract_dir);
        } else {
            e->status = DL_STATUS_COMPLETED;
            ui_message("Download complete:\n\n%s\n\nSaved to:\n%s",
                       e->name[0] ? e->name : e->filename, e->target_path);
        }
    } else if (rc == 1) {
        e->status = DL_STATUS_PAUSED;
        ui_status("Paused %s", e->filename);
    } else {
        e->status = DL_STATUS_ERROR;
        ui_message("Download failed (code %d) for:\n%s\n\nCheck %s.",
                   rc, e->filename, DEBUG_LOG_FILE);
    }
    downloads_save(&g_downloads);

    g_active_in_progress = false;
    g_pause_requested    = false;
    g_active_rom_id[0]   = '\0';
}

/* ---- ROM catalog, cached on the HDD ----
 *
 * Same strategy as the MiSTer client: GET /api/v1/roms/fingerprints once per
 * session, then per system either reuse the cached rows (fingerprint
 * unchanged), refetch them (changed / missing), or - with the server
 * unreachable - show the cached copy.  A server without the fingerprints
 * route (404/405) gets today's behaviour: a plain fetch, nothing cached.
 * This client has no server-side catalog filters (search / RA), so nothing
 * else needs reproducing locally. */

typedef enum {
    FP_UNKNOWN = 0,   /* not asked yet this session */
    FP_OK,            /* g_fp_json holds the server's fingerprints */
    FP_NO_ROUTE,      /* server predates the route: fetch, don't cache */
    FP_OFFLINE,       /* unreachable: use the cache */
} FingerprintState;

static FingerprintState g_fp_state = FP_UNKNOWN;
static char g_fp_json[32 * 1024];
static int  g_rom_loaded_index = -1;     /* system held in g_rom_catalog */
static char g_rom_notice[160];           /* banner text for the catalog */
static char g_rom_source[40];            /* toolbar chip: Cached / Updated / Offline */
/* Catalog pages are KB-MB of JSON; 1 MB per page is plenty. */
static char g_catalog_scratch[1 * 1024 * 1024];

static void fetch_fingerprints(const SyncState *state, bool has_net) {
    int status = 0;
    int n;

    if (!has_net) {
        g_fp_state = FP_OFFLINE;
        return;
    }
    ui_status("Checking the ROM catalog for changes...");
    n = network_fetch_rom_fingerprints(state, g_fp_json, sizeof(g_fp_json), &status);
    if (n > 0 && status == 200)              g_fp_state = FP_OK;
    else if (status == 404 || status == 405) g_fp_state = FP_NO_ROUTE;
    else                                     g_fp_state = FP_OFFLINE;
    debug_log("catalog: fingerprints status=%d n=%d -> state %d", status, n, (int)g_fp_state);
}

static void load_catalog_offline(const char *sys) {
    char err[sizeof(g_rom_catalog.last_error)];
    snprintf(err, sizeof(err), "%s", g_rom_catalog.last_error);
    if (catcache_load(CATALOG_CACHE_DIR, sys, NULL, 0, &g_rom_catalog)) {
        snprintf(g_rom_notice, sizeof(g_rom_notice),
                 "Server unreachable - showing the cached %s catalog (%d game%s).",
                 sys, g_rom_catalog.count, g_rom_catalog.count == 1 ? "" : "s");
        snprintf(g_rom_source, sizeof(g_rom_source), "Offline - cached");
    } else {
        g_rom_catalog.count = 0;
        if (err[0])
            snprintf(g_rom_catalog.last_error, sizeof(g_rom_catalog.last_error), "%s", err);
        else
            snprintf(g_rom_catalog.last_error, sizeof(g_rom_catalog.last_error),
                     "Offline - no cached %s catalog yet.", sys);
        snprintf(g_rom_notice, sizeof(g_rom_notice), "%s", g_rom_catalog.last_error);
        snprintf(g_rom_source, sizeof(g_rom_source), "Offline");
    }
}

/* Put system `index`'s catalog into g_rom_catalog, from the cache or the
 * server as the fingerprints dictate. */
static void load_catalog_for_system(const SyncState *state, bool has_net, int index) {
    const char *sys = G_ROM_SYSTEMS[index];
    char server_fp[CATALOG_FP_LEN] = "";
    char cached_fp[CATALOG_FP_LEN] = "";
    int listed = -1;

    memset(&g_rom_catalog, 0, sizeof(g_rom_catalog));
    g_rom_selected = 0;
    g_rom_scroll = 0;
    g_rom_loaded_index = index;
    g_rom_notice[0] = '\0';
    g_rom_source[0] = '\0';

    if (g_fp_state == FP_UNKNOWN) fetch_fingerprints(state, has_net);
    if (g_fp_state == FP_OFFLINE) {
        load_catalog_offline(sys);
        return;
    }

    if (g_fp_state == FP_OK)
        listed = catcache_fingerprint_for(g_fp_json, sys, server_fp, sizeof(server_fp), NULL);

    if (listed == 0) {
        /* The server has no games for this system any more. */
        catcache_drop(CATALOG_CACHE_DIR, sys);
        snprintf(g_rom_notice, sizeof(g_rom_notice), "The server has no %s games.", sys);
        return;
    }

    if (listed > 0 &&
        catcache_peek(CATALOG_CACHE_DIR, sys, cached_fp, sizeof(cached_fp)) &&
        strcmp(cached_fp, server_fp) == 0 &&
        catcache_load(CATALOG_CACHE_DIR, sys, NULL, 0, &g_rom_catalog)) {
        snprintf(g_rom_notice, sizeof(g_rom_notice),
                 "%s catalog: %d game%s (unchanged, from the cache).",
                 sys, g_rom_catalog.count, g_rom_catalog.count == 1 ? "" : "s");
        snprintf(g_rom_source, sizeof(g_rom_source), "Cached");
        return;
    }

    ui_status("Fetching %s catalog...", sys);
    if (!roms_fetch_catalog(state, sys, g_catalog_scratch, sizeof(g_catalog_scratch),
                            &g_rom_catalog)) {
        /* Server stopped answering mid-way: the last copy beats nothing. */
        load_catalog_offline(sys);
        return;
    }
    if (listed > 0) {
        if (g_rom_catalog.truncated) {
            debug_log("catalog: %s fetch incomplete - not cached", sys);
        } else if (!catcache_save(CATALOG_CACHE_DIR, sys, server_fp, &g_rom_catalog)) {
            debug_log("catalog: could not write the %s cache", sys);
        }
        snprintf(g_rom_notice, sizeof(g_rom_notice), "%s catalog updated: %d game%s.",
                 sys, g_rom_catalog.count, g_rom_catalog.count == 1 ? "" : "s");
        snprintf(g_rom_source, sizeof(g_rom_source), "Updated");
    } else {
        snprintf(g_rom_notice, sizeof(g_rom_notice), "%s catalog: %d game%s.",
                 sys, g_rom_catalog.count, g_rom_catalog.count == 1 ? "" : "s");
    }
}

/* Settings > Refresh catalog: the MiSTer "force" path.  Ask the server to
 * rescan (failure or refusal is not fatal), wipe the cache, refetch every
 * system and report. */
static void refresh_catalog_all(const SyncState *state, bool has_net,
                                char *out, size_t out_size) {
    char lines[512];
    size_t used = 0;
    int count = -1;
    bool scanned;

    if (!has_net) {
        ui_message("Server is offline.\n\nConnect to the server first to refresh the catalog.");
        snprintf(out, out_size, "Refresh catalog: server offline.");
        return;
    }

    ui_status("Asking the server to rescan its ROM folder...");
    scanned = network_trigger_rom_scan(state, &count) == 0;
    debug_log("catalog: refresh, server rescan %s (count=%d)", scanned ? "ok" : "failed", count);

    for (int i = 0; i < G_ROM_SYSTEM_COUNT; i++)
        catcache_drop(CATALOG_CACHE_DIR, G_ROM_SYSTEMS[i]);
    g_fp_state = FP_UNKNOWN;

    lines[0] = '\0';
    for (int i = 0; i < G_ROM_SYSTEM_COUNT && used < sizeof(lines); i++) {
        load_catalog_for_system(state, has_net, i);
        if (g_rom_catalog.count == 0 && g_rom_catalog.last_error[0])
            used += (size_t)snprintf(lines + used, sizeof(lines) - used, "%s: %s\n",
                                     G_ROM_SYSTEMS[i], g_rom_catalog.last_error);
        else
            used += (size_t)snprintf(lines + used, sizeof(lines) - used, "%s: %d game%s\n",
                                     G_ROM_SYSTEMS[i], g_rom_catalog.count,
                                     g_rom_catalog.count == 1 ? "" : "s");
    }
    /* Reload whichever system the Catalog tab shows the next time it opens. */
    g_rom_loaded_index = -1;

    ui_message("Catalog refreshed\n\n%s\n%s",
               scanned ? "The server rescanned its ROM folder."
                       : "The server did not rescan (failed or not allowed) - refetched anyway.",
               lines);
    snprintf(out, out_size, "Catalog refreshed%s.",
             scanned ? "" : " (server rescan skipped)");
}

static void format_size_short(uint64_t bytes, char *out, size_t out_size) {
    if (bytes >= (1ULL << 30))
        snprintf(out, out_size, "%.2f GiB", (double)bytes / (double)(1ULL << 30));
    else if (bytes >= (1ULL << 20))
        snprintf(out, out_size, "%.1f MiB", (double)bytes / (double)(1ULL << 20));
    else
        snprintf(out, out_size, "%llu KiB", (unsigned long long)(bytes / 1024));
}

/* Catalog Triangle: everything about the selected game. */
static void show_rom_details(const RomEntry *r) {
    char size_buf[32];
    char target[PATH_LEN];
    const DownloadEntry *e;
    const char *dl = "not downloaded";

    format_size_short(r->size, size_buf, sizeof(size_buf));
    if (r->is_bundle) {
        if (strcmp(r->system, "PS1") == 0)
            snprintf(target, sizeof(target), "%s/<game>/", ROM_TARGET_PSXISO_DIR);
        else
            snprintf(target, sizeof(target), "%s + %s", ROM_TARGET_PKG_DIR, ROM_TARGET_EXDATA_DIR);
    } else if (!roms_resolve_target_path(r, target, sizeof(target))) {
        snprintf(target, sizeof(target), "%s", ROM_TARGET_FALLBACK_DIR);
    }
    e = downloads_find(&g_downloads, r->rom_id);
    if (e) dl = downloads_status_to_str(e->status);

    ui_message("%s\n\n"
               "System: %s\n"
               "File: %s\n"
               "Size: %s%s\n"
               "Installs to: %s\n"
               "Download: %s\n"
               "ROM id: %s",
               r->name[0] ? r->name : r->filename,
               r->system[0] ? r->system : "?",
               r->filename,
               size_buf,
               r->is_bundle ? " (bundle)" : (r->extract_format[0] ? " (unpacked on download)" : ""),
               target, dl, r->rom_id);
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
    InputState input;
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
        ui_draw_message("GameSync PS3", error_buf, "Press PS/Home to exit");
        while (1) {
            SDL_PumpEvents();
            sysUtilCheckCallback();
            if (g_exit_requested || ui_exit_requested()) break;
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
            ui_set_online(true);
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

    /* --- ROM downloads init --- */
    ui_status("Loading downloads state...");
    roms_ensure_target_dirs();
    memset(&g_rom_catalog, 0, sizeof(g_rom_catalog));
    memset(&g_downloads,   0, sizeof(g_downloads));
    downloads_load(&g_downloads);
    debug_log("downloads: %d entries on disk", g_downloads.count);

    rebuild_visible(state);
    if (has_net && g_visible_count > 0) {
        fetch_selected_server_meta(state, &state->titles[g_visible[selected]]);
        last_selected_title = g_visible[selected];
    }
    snprintf(status_line, sizeof(status_line),
             "Found %d save(s). %s",
             state->num_titles,
             has_net ? "Server connected." : "Offline.");

    /* Settings tab state: a draft of the config, edited in place and only
     * written by "Save and apply".  It survives tab switches. */
    ConfigDraft cfg_draft;
    bool cfg_valid = false;
    bool cfg_dirty = false;
    int  cfg_selected = 0;
    char settings_status[256] = "";

    memset(&input, 0, sizeof(input));
    input_resync(&input);

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

        ui_set_online(has_net);

        unsigned int held = 0;
        unsigned int just = input_poll(&input, &held);

        /* L1 / R1: previous / next tab, wrapping.  Handled before the view
         * blocks so every tab is reachable from every other. */
        if (just & (MASK_L1 | MASK_R1)) {
            int dir = (just & MASK_R1) ? 1 : -1;
            if ((just & MASK_L1) && (just & MASK_R1)) dir = 0;
            g_app_view = (AppView)(((int)g_app_view + dir + APP_VIEW_COUNT) % APP_VIEW_COUNT);
            redraw = true;
        }

        /* START: leave the app (asks first). */
        if (just & MASK_START) {
            if (ui_ask("Exit GameSync?",
                       "Return to the XMB. Paused downloads resume the next time "
                       "you start GameSync.", "Exit")) {
                debug_log("exit via START");
                break;
            }
            redraw = true;
            just = 0;
        }

        /* ============================================================
         * Saves view
         * ============================================================ */
        if (g_app_view == APP_VIEW_SAVES) {

        /* Navigation: Up/Down one row, Left/Right one page */
        if (list_nav(just, &selected, g_visible_count)) {
            update_scroll(selected, &scroll, g_visible_count);
            redraw = true;
        }

        /* SELECT: cycle the All / PS3 / PS1 sub-tabs */
        if (just & MASK_SELECT) {
            g_save_filter = (g_save_filter + 1) % G_SAVE_FILTER_COUNT;
            rebuild_visible(state);
            selected = 0;
            scroll = 0;
            last_selected_title = -1;
            snprintf(status_line, sizeof(status_line), "Showing %s saves: %d of %d.",
                     g_save_filter == 0 ? "all" : G_SAVE_FILTERS[g_save_filter],
                     g_visible_count, state->num_titles);
            redraw = true;
        }

        /* Square (□): smart sync all saves, skipping conflicts */
        if (just & MASK_SQUARE) {
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
                last_selected_title = -1;
            }
            redraw = true;
        }

        /* Cross (X): smart sync */
        if ((just & MASK_CROSS) && g_visible_count > 0) {
            if (!has_net) {
                ui_message("Server is offline.\n\nConnect to GameSync first to sync this save.");
            } else {
            TitleInfo *title = &state->titles[g_visible[selected]];
            ui_status("Analyzing %s...", title->game_code);

            SyncAction action = sync_decide(state, g_visible[selected]);

            char server_hash[65] = "";
            uint32_t server_size = 0;
            char server_last_sync[32] = "";
            network_get_save_info(state, title,
                                  server_hash, &server_size, server_last_sync);

            if (ui_confirm(title, action, server_hash, server_size, server_last_sync)) {
                ui_status("%s %s...",
                          action == SYNC_UPLOAD ? "Uploading" : "Downloading",
                          title->game_code);
                int r = sync_execute(state, g_visible[selected], action);
                if (r == 0) {
                    title->server_meta_loaded = false;
                    title->server_hash[0] = '\0';
                    if (action == SYNC_DOWNLOAD &&
                        title->kind == SAVE_KIND_PS3 && title->server_only) {
                        show_fake_usb_stage_result(title);
                    } else if (action == SYNC_DOWNLOAD &&
                               (title->kind == SAVE_KIND_PS1 || title->kind == SAVE_KIND_PS1_VM1) &&
                               title->server_only) {
                        show_ps1_download_result(title);
                    } else {
                        ui_message("Done! (%s)", title->game_code);
                    }
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
            }
            redraw = true;
        }

        /* Triangle (△): details + the less common actions, as a menu. */
        enum { ACT_NONE = -1, ACT_UPLOAD, ACT_DOWNLOAD, ACT_COMPARE, ACT_REHASH, ACT_RESCAN };
        int act = ACT_NONE;
        if (just & MASK_TRIANGLE) {
            static const char *const k_save_actions[] = {
                "Upload to server (replace the server copy)",
                "Download from server (replace this PS3's copy)",
                "Compare files with the server",
                "Refresh hash",
                "Rescan all saves",
            };
            if (g_visible_count > 0) {
                const TitleInfo *t = &state->titles[g_visible[selected]];
                char body[320];
                snprintf(body, sizeof(body), "%s  -  %s\n%s",
                         t->game_code, title_status_label(t->status),
                         t->server_only ? "Only on the server" : t->local_path);
                act = ui_choose(t->name[0] ? t->name : t->game_code, body,
                                k_save_actions, 5, 0);
            } else {
                int c = ui_choose("Saves", "No save is selected.", &k_save_actions[ACT_RESCAN], 1, 0);
                act = (c == 0) ? ACT_RESCAN : ACT_NONE;
            }
            if ((act == ACT_UPLOAD || act == ACT_DOWNLOAD || act == ACT_COMPARE) && !has_net) {
                ui_message("Server is offline.\n\nConnect to GameSync first.");
                act = ACT_NONE;
            }
            redraw = true;
        }

        /* Force upload */
        if (act == ACT_UPLOAD && g_visible_count > 0) {
            TitleInfo *title = &state->titles[g_visible[selected]];
            if (title->server_only) {
                if (title->kind == SAVE_KIND_PS3) {
                    ui_message("This PS3 save only exists on the server.\n\n"
                               "Use Download from server to stage it to Fake USB first.");
                } else {
                    ui_message("This save only exists on the server.\n"
                               "Download it first (Cross, or Triangle > Download).");
                }
            } else {
                char server_hash[65] = "";
                uint32_t server_size = 0;
                char server_last_sync[32] = "";
                network_get_save_info(state, title,
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
        }

        /* Force download */
        if (act == ACT_DOWNLOAD && g_visible_count > 0) {
            TitleInfo *title = &state->titles[g_visible[selected]];
            char server_hash[65] = "";
            uint32_t server_size = 0;
            char server_last_sync[32] = "";
            network_get_save_info(state, title,
                                  server_hash, &server_size, server_last_sync);
            if (ui_confirm(title, SYNC_DOWNLOAD, server_hash, server_size, server_last_sync)) {
                ui_status("Downloading %s...", title->game_code);
                int r = sync_execute(state, g_visible[selected], SYNC_DOWNLOAD);
                if (r == 0) {
                    title->server_meta_loaded = false;
                    title->server_hash[0] = '\0';
                    if (title->kind == SAVE_KIND_PS3 && title->server_only) {
                        show_fake_usb_stage_result(title);
                    } else if ((title->kind == SAVE_KIND_PS1 || title->kind == SAVE_KIND_PS1_VM1) &&
                               title->server_only) {
                        show_ps1_download_result(title);
                    } else {
                        ui_message("Download OK!");
                    }
                }
                else        ui_message("Download failed! (code %d)", r);
            }
        }

        /* Compare local files against the server copy */
        if (act == ACT_COMPARE && g_visible_count > 0) {
            show_file_compare(state, &state->titles[g_visible[selected]]);
        }

        /* Hash the selected save now and refresh its status */
        if (act == ACT_REHASH && g_visible_count > 0) {
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
            last_selected_title = -1;
        }

        /* Rescan saves + refresh server status */
        if (act == ACT_RESCAN) {
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
            last_selected_title = -1;
        }

        /* Server metadata for the detail panel: fetched once the cursor
         * rests, so holding the D-pad scrolls without a request per row. */
        if (has_net && g_visible_count > 0 && !(held & MASK_DPAD) &&
            last_selected_title != g_visible[selected]) {
            fetch_selected_server_meta(state, &state->titles[g_visible[selected]]);
            last_selected_title = g_visible[selected];
            redraw = true;
        }

        }  /* end if (g_app_view == APP_VIEW_SAVES) */

        /* ============================================================
         * ROM Catalog view — browse + queue/start downloads.
         * ============================================================ */
        if (g_app_view == APP_VIEW_ROMS) {
            /* SELECT: next catalog sub-tab (PS3 / PS1). */
            if (just & MASK_SELECT) {
                g_rom_system_index = (g_rom_system_index + 1) % G_ROM_SYSTEM_COUNT;
                redraw = true;
            }

            /* Load the shown system from the cache / server on first entry
             * and after a system switch or a refresh. */
            if (g_rom_loaded_index != g_rom_system_index) {
                load_catalog_for_system(state, has_net, g_rom_system_index);
                redraw = true;
            }

            int total = g_rom_catalog.count;
            const char *current_system = G_ROM_SYSTEMS[g_rom_system_index];

            if (list_nav(just, &g_rom_selected, total)) {
                update_scroll(g_rom_selected, &g_rom_scroll, total);
                redraw = true;
            }

            /* Cross: enqueue + start (or resume) the selected ROM right
             * away (single active download policy keeps the UI predictable
             * on PS3's single thread). */
            if ((just & MASK_CROSS) && total > 0) {
                if (!has_net) {
                    ui_message("Server is offline.\n\nThe catalog shown is the cached copy; "
                               "downloads need the server.");
                } else {
                    RomEntry *r = &g_rom_catalog.items[g_rom_selected];
                    DownloadEntry *e =
                        downloads_upsert_from_catalog(&g_downloads, r);
                    if (!e) {
                        ui_message("Download queue full (%d entries).\n\n"
                                   "Clear finished entries from the Downloads "
                                   "tab (Square) and try again.", DOWNLOAD_MAX);
                    } else if (e->status != DL_STATUS_COMPLETED) {
                        e->status = (e->offset > 0) ? DL_STATUS_PAUSED
                                                    : DL_STATUS_QUEUED;
                        downloads_save(&g_downloads);
                        run_download(state, e);
                    } else {
                        ui_message("Already downloaded:\n%s\n\nLocation:\n%s",
                                   e->name[0] ? e->name : e->filename,
                                   e->target_path);
                    }
                }
                redraw = true;
            }

            /* Triangle: details of the selected game. */
            if ((just & MASK_TRIANGLE) && total > 0) {
                show_rom_details(&g_rom_catalog.items[g_rom_selected]);
                redraw = true;
            }

            /* run_download() switches to Downloads; let that view draw. */
            if (redraw && g_app_view == APP_VIEW_ROMS) {
                char roms_status[200];
                if (g_rom_notice[0])
                    snprintf(roms_status, sizeof(roms_status), "%s", g_rom_notice);
                else
                    snprintf(roms_status, sizeof(roms_status), "%s catalog: %d game(s).",
                             current_system, g_rom_catalog.count);
                ui_draw_rom_catalog(&g_rom_catalog, &g_downloads,
                                    G_ROM_SYSTEMS, G_ROM_SYSTEM_COUNT,
                                    g_rom_system_index,
                                    g_rom_selected, g_rom_scroll,
                                    roms_status, g_rom_source);
                redraw = false;
            }

            usleep(16000);
            continue;
        }

        /* ============================================================
         * Downloads view — manage in-flight + completed downloads.
         * ============================================================ */
        if (g_app_view == APP_VIEW_DOWNLOADS) {
            int total = g_downloads.count;

            if (list_nav(just, &g_dl_selected, total)) {
                update_scroll(g_dl_selected, &g_dl_scroll, total);
                redraw = true;
            }

            /* Triangle: options for the selected entry. */
            bool start_selected = (just & MASK_CROSS) != 0;
            bool remove_selected = false;
            if ((just & MASK_TRIANGLE) && total > 0 && g_dl_selected < total) {
                static const char *const k_dl_actions[] = {
                    "Start / resume",
                    "Remove from the list (deletes the partial file)",
                };
                const DownloadEntry *e = &g_downloads.items[g_dl_selected];
                char body[200];
                snprintf(body, sizeof(body), "%s  -  %s",
                         e->system[0] ? e->system : "?", downloads_status_to_str(e->status));
                int c = ui_choose(e->name[0] ? e->name : e->filename, body, k_dl_actions, 2, 0);
                if (c == 0) start_selected = true;
                if (c == 1) remove_selected = true;
                redraw = true;
            }

            /* Cross: start/resume the selected entry (or auto-pick the
             * first runnable if none selected). */
            if (start_selected && total > 0) {
                if (!has_net) {
                    ui_message("Server is offline.\n\nDownloads need the server.");
                } else {
                    DownloadEntry *e = (g_dl_selected >= 0 && g_dl_selected < total)
                        ? &g_downloads.items[g_dl_selected]
                        : downloads_next_runnable(&g_downloads);
                    if (e && e->status != DL_STATUS_COMPLETED &&
                             e->status != DL_STATUS_ACTIVE)
                    {
                        run_download(state, e);
                    }
                }
                redraw = true;
            }

            /* Circle: pause the running download (progress is kept).  The
             * streamer also watches Circle itself while it runs. */
            if (just & MASK_CIRCLE) {
                if (g_active_in_progress) {
                    g_pause_requested = true;
                    ui_status("Pausing...");
                }
                redraw = true;
            }

            /* Remove the selected entry and unlink its .part file.  Does
             * not delete a completed download's final file. */
            if (remove_selected && g_dl_selected < g_downloads.count) {
                if (g_active_in_progress &&
                    strcmp(g_active_rom_id,
                           g_downloads.items[g_dl_selected].rom_id) == 0)
                {
                    /* Active download — pause first, ask user to retry the
                     * removal after it stops.  Avoids racing the streamer. */
                    g_pause_requested = true;
                    ui_status("Pause the active download first, then remove it.");
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
                    update_scroll(g_dl_selected, &g_dl_scroll,
                                  g_downloads.count);
                }
                redraw = true;
            }

            /* Square: clear all completed entries from the list. */
            if (just & MASK_SQUARE) {
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
                    update_scroll(g_dl_selected, &g_dl_scroll,
                                  g_downloads.count);
                    ui_status("Cleared %d completed entries.", removed);
                }
                redraw = true;
            }

            if (redraw) {
                char dl_status[128];
                snprintf(dl_status, sizeof(dl_status),
                         "%d download(s) - %s",
                         g_downloads.count,
                         g_active_in_progress ? "downloading"
                                              : (has_net ? "idle" : "offline"));
                ui_draw_downloads(&g_downloads, g_dl_selected, g_dl_scroll,
                                  dl_status,
                                  g_active_in_progress,
                                  g_active_downloaded, g_active_total,
                                  g_active_bps);
                redraw = false;
            }

            usleep(16000);
            continue;
        }

        /* ============================================================
         * Settings view — config draft + Refresh catalog.
         * ============================================================ */
        if (g_app_view == APP_VIEW_SETTINGS) {
            if (!cfg_valid) {
                config_draft_from_state(&cfg_draft, state);
                cfg_valid = true;
                cfg_dirty = false;
            }

            if (just & MASK_DOWN) { cfg_selected = (cfg_selected + 1) % UI_SETTINGS_FIELDS; redraw = true; }
            if (just & MASK_UP) {
                cfg_selected = (cfg_selected - 1 + UI_SETTINGS_FIELDS) % UI_SETTINGS_FIELDS;
                redraw = true;
            }
            /* Left / Right change the focused value (user, switches). */
            if (just & (MASK_LEFT | MASK_RIGHT)) {
                bool right = (just & MASK_RIGHT) != 0;
                if (cfg_selected == 2) {
                    if (right && cfg_draft.selected_user < 16) { cfg_draft.selected_user++; cfg_dirty = true; }
                    if (!right && cfg_draft.selected_user > 0) { cfg_draft.selected_user--; cfg_dirty = true; }
                } else if (cfg_selected == 3) {
                    cfg_draft.scan_ps3 = !cfg_draft.scan_ps3; cfg_dirty = true;
                } else if (cfg_selected == 4) {
                    cfg_draft.scan_ps1 = !cfg_draft.scan_ps1; cfg_dirty = true;
                } else if (cfg_selected == 5) {
                    cfg_draft.show_server_only = !cfg_draft.show_server_only; cfg_dirty = true;
                }
                redraw = true;
            }

            /* Circle: throw the draft away and go back to Saves. */
            if (just & MASK_CIRCLE) {
                cfg_valid = false;
                settings_status[0] = '\0';
                g_app_view = APP_VIEW_SAVES;
                redraw = true;
            }

            if (just & MASK_CROSS) {
                switch (cfg_selected) {
                    case 0:
                        cfg_dirty |= run_text_editor("Server URL", cfg_draft.server_url,
                                                     sizeof(cfg_draft.server_url));
                        input_resync(&input);
                        break;
                    case 1:
                        cfg_dirty |= run_text_editor("API Key", cfg_draft.api_key,
                                                     sizeof(cfg_draft.api_key));
                        input_resync(&input);
                        break;
                    case 2:
                        cfg_draft.selected_user = (cfg_draft.selected_user + 1) % 17;
                        cfg_dirty = true;
                        break;
                    case 3: cfg_draft.scan_ps3 = !cfg_draft.scan_ps3; cfg_dirty = true; break;
                    case 4: cfg_draft.scan_ps1 = !cfg_draft.scan_ps1; cfg_dirty = true; break;
                    case 5:
                        cfg_draft.show_server_only = !cfg_draft.show_server_only;
                        cfg_dirty = true;
                        break;
                    case UI_SETTINGS_REFRESH:
                        refresh_catalog_all(state, has_net, settings_status,
                                            sizeof(settings_status));
                        break;
                    case UI_SETTINGS_SAVE:
                        apply_config_draft(state, &cfg_draft, &has_net,
                                           status_line, sizeof(status_line));
                        cfg_valid = false;
                        settings_status[0] = '\0';
                        /* The server may have changed: ask again for the
                         * catalog fingerprints next time. */
                        g_fp_state = FP_UNKNOWN;
                        g_rom_loaded_index = -1;
                        rebuild_visible(state);
                        if (selected >= g_visible_count)
                            selected = g_visible_count > 0 ? g_visible_count - 1 : 0;
                        update_scroll(selected, &scroll, g_visible_count);
                        last_selected_title = -1;
                        g_app_view = APP_VIEW_SAVES;
                        break;
                    case UI_SETTINGS_DISCARD:
                        config_draft_from_state(&cfg_draft, state);
                        cfg_dirty = false;
                        snprintf(settings_status, sizeof(settings_status),
                                 "Changes discarded.");
                        break;
                    default:
                        break;
                }
                redraw = true;
            }

            if (g_app_view == APP_VIEW_SETTINGS) {
                if (redraw) {
                    ui_draw_config_editor(cfg_draft.server_url, cfg_draft.api_key,
                                          cfg_draft.selected_user, cfg_draft.scan_ps3,
                                          cfg_draft.scan_ps1, cfg_draft.show_server_only,
                                          cfg_selected, cfg_dirty, settings_status);
                    redraw = false;
                }
                usleep(16000);
                continue;
            }
        }

        /* Saves view render (only reached when g_app_view == APP_VIEW_SAVES). */
        if (redraw) {
            ui_draw_list(state, g_visible, g_visible_count, selected, scroll,
                         status_line, config_created, state->show_server_only,
                         G_SAVE_FILTERS, G_SAVE_FILTER_COUNT, g_save_filter);
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
