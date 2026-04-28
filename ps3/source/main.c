/*
 * PS3 GameSync - Main
 *
 * Syncs PS3 and PS1 saves with the GameSync server over WiFi, and now
 * also browses + downloads ROMs (.iso → /dev_hdd0/PS3ISO, .pkg →
 * /dev_hdd0/packages).  Three top-level views cycled with SELECT:
 *
 *   1. Saves         — the original save sync flow
 *   2. ROM Catalog   — server-side ROM library (PS3 only)
 *   3. Downloads     — pause/resume queue for ROM downloads
 *
 * Controller layout (Saves view):
 *   Up / Down        Navigate save list
 *   Left / Right     Page up / down
 *   Cross   (X)      Smart sync (auto decide upload/download)
 *   Square  (□)      Force upload to server
 *   Triangle(△)      Force download from server
 *   R1               Compare local files with the server copy
 *   R3               Sync all saves automatically (skip conflicts)
 *   L3               Hash selected save now
 *   Circle  (○)      Rescan + rehash saves
 *   Start            Open config editor
 *   Select           Cycle to next view (ROM Catalog)
 *   PS/Home          Exit
 *
 * Controller layout (ROM Catalog view):
 *   Up / Down        Navigate catalog
 *   Left / Right     Page up / down
 *   Cross            Queue + start download
 *   Triangle         Resume a paused download for the selected ROM
 *   Circle           Refetch catalog from server
 *   Select           Cycle to next view (Downloads)
 *
 * Controller layout (Downloads view):
 *   Up / Down        Navigate queue
 *   Cross            Start / resume selected
 *   Square           Pause active download (saves offset for next session)
 *   Triangle         Clear completed entries
 *   Circle           Cancel + delete .part for selected entry
 *   Select           Cycle to next view (Saves)
 */

#include "apollo.h"
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
    unsigned int prev_buttons = 0;
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

        btns = read_buttons();
        just = btns & ~prev_buttons;
        prev_buttons = btns;
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

static bool run_config_editor(SyncState *state, bool *has_net, char *status_line, size_t status_line_sz) {
    ConfigDraft draft;
    unsigned int prev_buttons = 0;
    int selected = 0;
    bool dirty = false;

    config_draft_from_state(&draft, state);

    while (1) {
        unsigned int btns;
        unsigned int just;

        SDL_PumpEvents();
        sysUtilCheckCallback();
        if (g_exit_requested || ui_exit_requested()) {
            return false;
        }
        if (ui_menu_open()) {
            usleep(50000);
            continue;
        }

        ui_draw_config_editor(
            draft.server_url,
            draft.api_key,
            draft.selected_user,
            draft.scan_ps3,
            draft.scan_ps1,
            selected,
            dirty
        );

        btns = read_buttons();
        just = btns & ~prev_buttons;
        prev_buttons = btns;

        if (just & MASK_UP) {
            selected = (selected + 6) % 7;
        }
        if (just & MASK_DOWN) {
            selected = (selected + 1) % 7;
        }
        if (just & MASK_LEFT) {
            if (selected == 2 && draft.selected_user > 0) {
                draft.selected_user--;
                dirty = true;
            } else if (selected == 3) {
                draft.scan_ps3 = !draft.scan_ps3;
                dirty = true;
            } else if (selected == 4) {
                draft.scan_ps1 = !draft.scan_ps1;
                dirty = true;
            }
        }
        if (just & MASK_RIGHT) {
            if (selected == 2 && draft.selected_user < 16) {
                draft.selected_user++;
                dirty = true;
            } else if (selected == 3) {
                draft.scan_ps3 = !draft.scan_ps3;
                dirty = true;
            } else if (selected == 4) {
                draft.scan_ps1 = !draft.scan_ps1;
                dirty = true;
            }
        }
        if (just & MASK_CIRCLE) {
            return false;
        }
        if (just & MASK_CROSS) {
            if (selected == 0) {
                dirty |= run_text_editor("Server URL", draft.server_url, sizeof(draft.server_url));
            } else if (selected == 1) {
                dirty |= run_text_editor("API Key", draft.api_key, sizeof(draft.api_key));
            } else if (selected == 2) {
                draft.selected_user = (draft.selected_user + 1) % 17;
                dirty = true;
            } else if (selected == 3) {
                draft.scan_ps3 = !draft.scan_ps3;
                dirty = true;
            } else if (selected == 4) {
                draft.scan_ps1 = !draft.scan_ps1;
                dirty = true;
            } else if (selected == 5) {
                ui_status("Applying config...");
                config_draft_apply(state, &draft);
                config_save(state);

                state->network_connected = false;
                *has_net = false;
                saves_scan(state);
                if (draft.selected_user == 0 && state->selected_user > 0) {
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
                snprintf(status_line, status_line_sz,
                         "Config applied. %d save(s). %s",
                         state->num_titles,
                         *has_net ? "Server connected." : "Offline.");
                return true;
            } else if (selected == 6) {
                return false;
            }
        }

        usleep(50000);
    }
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

/* ---- ROM catalog + download globals (accessed by main loop only) ---- */

static AppView      g_app_view = APP_VIEW_SAVES;
static RomCatalog   g_rom_catalog;
static DownloadList g_downloads;

static int  g_rom_selected = 0;
static int  g_rom_scroll   = 0;
static int  g_dl_selected  = 0;
static int  g_dl_scroll    = 0;

/* Live progress for the active download — read by ui_draw_downloads when
 * rendered from the progress callback. */
static volatile bool     g_active_in_progress = false;
static volatile uint64_t g_active_downloaded = 0;
static volatile uint64_t g_active_total      = 0;
static volatile uint64_t g_active_bps        = 0;  /* moving-average B/s */
static char              g_active_rom_id[ROM_ID_LEN] = {0};
/* Set by SQUARE (pause) during an active download.  The progress callback
 * checks it on each chunk so we can pause without race conditions. */
static volatile bool     g_pause_requested = false;

/* Speed sampling: re-anchor every ~2 s so the rate is stable rather than
 * jittering with each 64 KB chunk.  Reset to (0, 0) at run_download
 * start so the first sample begins from the actual download start. */
static uint64_t g_dl_speed_anchor_bytes = 0;
static time_t   g_dl_speed_anchor_time  = 0;

/* Edge-detect SQUARE / CIRCLE while the main loop is blocked inside the
 * download streamer.  read_buttons() returns the live mask; we XOR with
 * the previous reading to find newly-pressed buttons. */
static unsigned int g_dl_prev_buttons = 0;

/* Throttled redraw of the downloads view from inside the progress
 * callback.  Without throttling SDL_Flip @ 60fps eats half the network
 * thread; on the slow PS3 link a redraw every 3 chunks (~192 KB) is
 * frequent enough for the user to see numbers move. */
static int g_progress_redraw_counter = 0;

static int rom_progress64_cb(uint64_t downloaded, uint64_t total) {
    sysUtilCheckCallback();
    SDL_PumpEvents();

    g_active_downloaded = downloaded;
    if (total > 0) g_active_total = total;

    /* Edge-detect SQUARE for pause while the main loop is blocked.  We
     * still let ui_exit_requested() short-circuit ahead of the pause
     * check so a PS-button exit during download cleans up immediately. */
    {
        unsigned int btns = read_buttons();
        unsigned int just = btns & ~g_dl_prev_buttons;
        g_dl_prev_buttons = btns;
        if (just & MASK_SQUARE) g_pause_requested = true;
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
        g_progress_redraw_counter++;
        if (g_progress_redraw_counter >= 3) {
            g_progress_redraw_counter = 0;
            ui_draw_downloads(&g_downloads, g_dl_selected, g_dl_scroll,
                              "Downloading...  (Square = pause)",
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
    g_progress_redraw_counter = 0;

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
            if (!roms_resolve_bundle_file_target(bf->name, target,
                                                 sizeof(target))) {
                debug_log("download: bundle file %s could not resolve target",
                          bf->name);
                rc = -2;
                break;
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

    /* Single-file path (unchanged behaviour). */
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
    int rc = network_download_rom_resumable(state, e->rom_id, e->target_path,
                                            e->offset, &total_seen);
    network_set_progress64_cb(NULL);

    /* Refresh offset from the streamer's view (handles 200-vs-206 fallthroughs
     * where it had to truncate the .part). */
    e->offset = g_active_downloaded;
    if (total_seen > 0) e->total = total_seen;

    if (rc == 0) {
        e->status = DL_STATUS_COMPLETED;
        ui_message("Download complete:\n\n%s\n\nSaved to:\n%s",
                   e->name[0] ? e->name : e->filename, e->target_path);
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

static void cycle_view(AppView *view) {
    *view = (AppView)(((int)*view + 1) % APP_VIEW_COUNT);
}

static const char *view_name(AppView v) {
    switch (v) {
        case APP_VIEW_SAVES:     return "Saves";
        case APP_VIEW_ROMS:      return "ROM Catalog";
        case APP_VIEW_DOWNLOADS: return "Downloads";
        default:                 return "?";
    }
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

        /* SELECT: cycle Saves -> ROM Catalog -> Downloads -> Saves.  This
         * has to come before any view-specific handlers so the user can
         * always escape into the next view regardless of where they are. */
        if (just & MASK_SELECT) {
            cycle_view(&g_app_view);
            ui_status("View: %s", view_name(g_app_view));
            redraw = true;
        }

        /* Saves view (the default) — wraps the original input block so the
         * other views don't accidentally trigger save sync actions. */
        if (g_app_view == APP_VIEW_SAVES) {

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

        /* Start: open config editor */
        if (just & MASK_START) {
            if (run_config_editor(state, &has_net, status_line, sizeof(status_line))) {
                rebuild_visible(state);
                if (selected >= g_visible_count)
                    selected = g_visible_count > 0 ? g_visible_count - 1 : 0;
                update_scroll(selected, &scroll, g_visible_count);
                last_selected_title = -1;
            }
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
            redraw = true;
        }

        /* Square (□): force upload */
        if ((just & MASK_SQUARE) && has_net && g_visible_count > 0) {
            TitleInfo *title = &state->titles[g_visible[selected]];
            if (title->server_only) {
                if (title->kind == SAVE_KIND_PS3) {
                    ui_message("This PS3 save only exists on the server.\n\n"
                               "Use Triangle to stage it to Fake USB first.");
                } else if (title->kind == SAVE_KIND_PS1 || title->kind == SAVE_KIND_PS1_VM1) {
                    ui_message("This save only exists on the server.\nDownload it first (Triangle).");
                } else {
                    ui_message("This save only exists on the server.\nDownload it first (Triangle).");
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
            redraw = true;
        }

        /* Triangle (△): force download */
        if ((just & MASK_TRIANGLE) && has_net && g_visible_count > 0) {
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

        }  /* end if (g_app_view == APP_VIEW_SAVES) */

        /* ============================================================
         * ROM Catalog view — browse + queue/start downloads.
         * ============================================================ */
        if (g_app_view == APP_VIEW_ROMS) {
            int total = g_rom_catalog.count;

            /* Auto-fetch on first entry (or when the catalog is empty
             * because a previous fetch failed and the user pressed
             * Circle to retry). */
            if (total == 0 && !g_rom_catalog.last_error[0] && has_net) {
                ui_status("Fetching PS3 catalog...");
                /* Reuse the request-time scratch buffer.  Catalog payloads
                 * are KB-MB so 1 MB is plenty without touching the bigger
                 * 8 MB save bundle buffer. */
                static char catalog_scratch[1 * 1024 * 1024];
                roms_fetch_ps3_catalog(state, catalog_scratch,
                                       sizeof(catalog_scratch),
                                       &g_rom_catalog);
                total = g_rom_catalog.count;
                if (g_rom_selected >= total) g_rom_selected = total > 0 ? total - 1 : 0;
                redraw = true;
            }

            if ((just & MASK_DOWN) && total > 0) {
                g_rom_selected = (g_rom_selected + 1) % total;
                update_scroll(g_rom_selected, &g_rom_scroll, total);
                redraw = true;
            }
            if ((just & MASK_UP) && total > 0) {
                g_rom_selected = (g_rom_selected - 1 + total) % total;
                update_scroll(g_rom_selected, &g_rom_scroll, total);
                redraw = true;
            }
            if ((just & MASK_RIGHT) && total > 0) {
                g_rom_selected += LIST_VISIBLE;
                if (g_rom_selected >= total) g_rom_selected = total - 1;
                update_scroll(g_rom_selected, &g_rom_scroll, total);
                redraw = true;
            }
            if ((just & MASK_LEFT) && total > 0) {
                g_rom_selected -= LIST_VISIBLE;
                if (g_rom_selected < 0) g_rom_selected = 0;
                update_scroll(g_rom_selected, &g_rom_scroll, total);
                redraw = true;
            }

            /* Circle: refresh catalog.  We tell the server to rescan
             * its rom_dir first so newly-added games show up without an
             * app restart, then clear the in-memory cache so the next
             * loop iteration re-pulls the freshened list. */
            if (just & MASK_CIRCLE) {
                if (has_net) {
                    ui_status("Server rescan...");
                    /* ui_status only paints to the saves view's status
                     * line — flip a one-shot draw so the user sees
                     * what's happening before the blocking call. */
                    ui_draw_rom_catalog(&g_rom_catalog, &g_downloads,
                                        g_rom_selected, g_rom_scroll,
                                        "Server rescan...");
                    int count = -1;
                    int rc = network_trigger_rom_scan(state, &count);
                    if (rc != 0) {
                        ui_message("Server rescan failed (code %d).\n\n"
                                   "Check the server is reachable and "
                                   "see %s for details.",
                                   rc, DEBUG_LOG_FILE);
                    } else {
                        debug_log("rom catalog: server reported %d roms after rescan",
                                  count);
                    }
                } else {
                    ui_status("Offline: refetching cached catalog only");
                }
                memset(&g_rom_catalog, 0, sizeof(g_rom_catalog));
                /* Reset selection so it doesn't dangle past the new list. */
                g_rom_selected = 0;
                g_rom_scroll   = 0;
                redraw = true;
            }

            /* Cross: enqueue + start the selected ROM right away (single
             * active download policy keeps the UI predictable on PS3's
             * single thread). */
            if ((just & MASK_CROSS) && total > 0 && has_net) {
                RomEntry *r = &g_rom_catalog.items[g_rom_selected];
                DownloadEntry *e =
                    downloads_upsert_from_catalog(&g_downloads, r);
                if (!e) {
                    ui_message("Download queue full (%d entries).\n\n"
                               "Clear completed entries from the Downloads "
                               "view and try again.", DOWNLOAD_MAX);
                } else {
                    if (e->status != DL_STATUS_COMPLETED) {
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

            /* Triangle on a paused/error entry from the catalog: resume
             * straight from here without forcing the user to switch view. */
            if ((just & MASK_TRIANGLE) && total > 0 && has_net) {
                RomEntry *r = &g_rom_catalog.items[g_rom_selected];
                DownloadEntry *e = downloads_find(&g_downloads, r->rom_id);
                if (e && (e->status == DL_STATUS_PAUSED ||
                          e->status == DL_STATUS_ERROR ||
                          e->status == DL_STATUS_QUEUED))
                {
                    run_download(state, e);
                }
                redraw = true;
            }

            if (redraw) {
                char roms_status[128];
                snprintf(roms_status, sizeof(roms_status),
                         "%d catalog entries, %d in queue",
                         g_rom_catalog.count, g_downloads.count);
                ui_draw_rom_catalog(&g_rom_catalog, &g_downloads,
                                    g_rom_selected, g_rom_scroll,
                                    roms_status);
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

            if ((just & MASK_DOWN) && total > 0) {
                g_dl_selected = (g_dl_selected + 1) % total;
                update_scroll(g_dl_selected, &g_dl_scroll, total);
                redraw = true;
            }
            if ((just & MASK_UP) && total > 0) {
                g_dl_selected = (g_dl_selected - 1 + total) % total;
                update_scroll(g_dl_selected, &g_dl_scroll, total);
                redraw = true;
            }

            /* Cross: start/resume the selected entry (or auto-pick the
             * first runnable if none selected). */
            if ((just & MASK_CROSS) && total > 0 && has_net) {
                DownloadEntry *e = (g_dl_selected >= 0 && g_dl_selected < total)
                    ? &g_downloads.items[g_dl_selected]
                    : downloads_next_runnable(&g_downloads);
                if (e && e->status != DL_STATUS_COMPLETED &&
                         e->status != DL_STATUS_ACTIVE)
                {
                    run_download(state, e);
                }
                redraw = true;
            }

            /* Square: pause active.  This only takes effect during an
             * active download (single-active policy), but we set the flag
             * anyway so a queued click pre-pauses the next start. */
            if (just & MASK_SQUARE) {
                if (g_active_in_progress) {
                    g_pause_requested = true;
                    ui_status("Pausing...");
                }
                redraw = true;
            }

            /* Circle: cancel the selected entry — drops it from the list
             * and unlinks the .part file.  Does not delete a completed
             * download's final file. */
            if ((just & MASK_CIRCLE) && total > 0 && g_dl_selected < total) {
                if (g_active_in_progress &&
                    strcmp(g_active_rom_id,
                           g_downloads.items[g_dl_selected].rom_id) == 0)
                {
                    /* Active download — pause first, ask user to retry the
                     * cancel after it stops.  Avoids racing the streamer. */
                    g_pause_requested = true;
                    ui_status("Pause active download first, then cancel.");
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

            /* Triangle: clear all completed entries from the list. */
            if (just & MASK_TRIANGLE) {
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
                         "%d entries  (%s)",
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

        /* Saves view render (only reached when g_app_view == APP_VIEW_SAVES). */
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
