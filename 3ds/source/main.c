#include "common.h"
#include "card_spi.h"
#include "catalog.h"
#include "config.h"
#include "network.h"
#include "sync.h"
#include "title.h"
#include "ui.h"
#include "update.h"
#include <ctype.h>
#include <stdarg.h>

static AppConfig config;
static TitleInfo titles[MAX_TITLES];
static int title_count = 0;
static int selected = 0;
static int scroll_offset = 0;
static char status[MAX_URL_LEN + 64];
static UiTone status_tone = UI_TONE_ACCENT;

// View filtering: filtered[] maps visible indices -> titles[] indices
static int view_mode = VIEW_ALL;
static int filtered[MAX_TITLES];
static int filtered_count = 0;

// Last compare with the server (A), shown on the top screen for that title
static SaveDetails last_details;
static int last_details_idx = -1;

static SavesView view;

#define LIST_VISIBLE SAVES_ROWS

static void set_status(UiTone tone, const char *fmt, ...) __attribute__((format(printf, 2, 3)));
static void set_status(UiTone tone, const char *fmt, ...) {
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(status, sizeof(status), fmt, ap);
    va_end(ap);
    status_tone = tone;
}

static int title_compare(const void *a, const void *b) {
    const TitleInfo *ta = (const TitleInfo *)a;
    const TitleInfo *tb = (const TitleInfo *)b;

    // Sort 3DS games before DS games
    if (ta->is_nds != tb->is_nds) {
        return ta->is_nds ? 1 : -1;  // 3DS (is_nds=false) comes first
    }

    // Within same type, sort alphabetically
    return strcasecmp(ta->name, tb->name);
}

// Rebuild the filtered index list based on current view_mode
static void rebuild_filter(void) {
    filtered_count = 0;
    for (int i = 0; i < title_count; i++) {
        bool include = false;
        switch (view_mode) {
            case VIEW_3DS: include = !titles[i].is_nds; break;
            case VIEW_NDS: include = titles[i].is_nds; break;
            default:       include = true; break;
        }
        if (include)
            filtered[filtered_count++] = i;
    }
    // Reset selection
    selected = 0;
    scroll_offset = 0;
}

// Get the actual title index for the current selection
static int sel_title_idx(void) {
    if (selected >= 0 && selected < filtered_count)
        return filtered[selected];
    return -1;
}

// Count how many titles are marked (across ALL titles, not just filtered)
static int count_marked(void) {
    int count = 0;
    for (int i = 0; i < title_count; i++)
        if (titles[i].marked) count++;
    return count;
}

// Clear all marks
static void clear_marks(void) {
    for (int i = 0; i < title_count; i++)
        titles[i].marked = false;
}

static void scan_titles(void) {
    ui_busy("Loading", "Scanning titles...");
    title_count = titles_scan(titles, MAX_TITLES, config.nds_dir);

    // Fetch game names from server
    if (title_count > 0) {
        ui_busy("Loading", "Fetching game names...");
        titles_fetch_names(&config, titles, title_count);
        qsort(titles, title_count, sizeof(TitleInfo), title_compare);
    }

    last_details_idx = -1;
    rebuild_filter();
}

// Clamp scroll so the selected item is always visible
static void update_scroll(void) {
    if (selected < scroll_offset)
        scroll_offset = selected;
    if (selected >= scroll_offset + LIST_VISIBLE)
        scroll_offset = selected - LIST_VISIBLE + 1;
}

// ---------------------------------------------------------------------------
// Drawing
// ---------------------------------------------------------------------------

static void refresh_view(void) {
    view.titles = titles;
    view.filtered = filtered;
    view.count = filtered_count;
    view.selected = selected;
    view.scroll = scroll_offset;
    view.view_mode = view_mode;
    view.marked = count_marked();
    view.status = status;
    view.status_tone = status_tone;
    int idx = sel_title_idx();
    view.details = (idx >= 0 && idx == last_details_idx) ? &last_details : NULL;
}

static void saves_top(void *ctx) {
    (void)ctx;
    refresh_view();
    ui_draw_saves_top(&view);
}

static void saves_bottom(void *ctx) {
    (void)ctx;
    refresh_view();
    ui_draw_saves_bottom(&view);
}

// ---------------------------------------------------------------------------
// Progress
// ---------------------------------------------------------------------------

static const char *op_title = "Syncing";

// Name for a 16-hex title id inside a progress message, or NULL
static const char *name_for_id(const char *hex) {
    for (int i = 0; i < title_count; i++)
        if (strncmp(titles[i].title_id_hex, hex, 16) == 0) return titles[i].name;
    return NULL;
}

// Progress callback for sync operations. Messages look like
// "Uploading 3/10: 0004000000055D00": show the phase, a bar from "n/m",
// and the game's name instead of its id.
static void sync_progress(const char *message) {
    char phase[96];
    snprintf(phase, sizeof(phase), "%s", message);
    const char *name = NULL;
    char *colon = strstr(phase, ": ");
    if (colon) {
        const char *id = colon + 2;
        if (strlen(id) >= 16 && isxdigit((unsigned char)id[0])) name = name_for_id(id);
        if (name) *colon = '\0';
    }

    float frac = -1;
    for (const char *p = phase; *p; p++) {
        int n, m;
        if (isdigit((unsigned char)*p) && sscanf(p, "%d/%d", &n, &m) == 2 && m > 0) {
            frac = (float)n / (float)m;
            break;
        }
    }

    UiProgress pr = { 0 };
    pr.title = op_title;
    pr.name = name ? name : phase;
    pr.status = name ? phase : NULL;
    pr.frac = frac;
    if (!pr.status) {
        // No name: put the message as the status line under a spinner
        pr.name = NULL;
        pr.status = phase;
        pr.frac = frac >= 0 ? frac : -1;
    }
    ui_progress(&pr);
}

// Update progress callback
static const char *update_phase = "Downloading update";
static void update_progress_cb(int pct) {
    static int last_pct = -1;
    static u64 last_ms = 0;
    u64 now = osGetTime();
    if (pct != last_pct && (pct >= 100 || pct == 0 || now - last_ms >= 50)) {
        char right[8];
        snprintf(right, sizeof(right), "%d%%", pct);
        UiProgress pr = { 0 };
        pr.title = "Update";
        pr.name = update_phase;
        pr.status = "Please wait, don't power off.";
        pr.frac = pct / 100.0f;
        pr.right = right;
        ui_progress(&pr);
        last_pct = pct;
        last_ms = now;
    }
    // Reset for next use when complete
    if (pct >= 100) last_pct = -1;
}

static void wait_for_start(UiTone tone, const char *title, const char *body) {
    static const UiButton b[] = { { KEY_START, "START", "Exit" } };
    ui_dialog(tone, title, body, b, 1);
}

// ---------------------------------------------------------------------------
// Actions
// ---------------------------------------------------------------------------

static void do_history(void) {
    int idx = sel_title_idx();
    if (idx < 0) return;
    ui_busy("History", "Loading saved versions...");

    HistoryVersion versions[MAX_HISTORY_VERSIONS];
    int count = sync_get_history(&config, titles[idx].title_id_hex, versions, MAX_HISTORY_VERSIONS);

    if (count < 0) {
        set_status(UI_TONE_ERR, "Failed to load history");
    } else if (count == 0) {
        set_status(UI_TONE_INFO, "No history available for %.40s", titles[idx].name);
    } else {
        char *selected_ts = ui_show_history(&titles[idx], versions, count);
        if (selected_ts) {
            op_title = "Restoring";
            sync_progress("Downloading history version...");
            SyncResult res = sync_download_history(&config, &titles[idx], selected_ts, sync_progress);
            if (res == SYNC_OK) {
                set_status(UI_TONE_OK, "Restored: %.40s", titles[idx].name);
                titles[idx].in_conflict = false;
                titles[idx].sync_state = TSTATE_DOWNLOADED;
            } else {
                set_status(UI_TONE_ERR, "Restore failed: %s", sync_result_str(res));
            }
            last_details_idx = -1;
            free(selected_ts);
        } else {
            set_status(UI_TONE_ACCENT, "History closed");
        }
    }
}

static void do_batch_upload(int marked) {
    char body[96];
    snprintf(body, sizeof(body), "Upload %d marked save%s to the server?", marked, marked == 1 ? "" : "s");
    if (!ui_confirm(UI_TONE_WARN, "Upload marked saves", body, "Upload")) {
        set_status(UI_TONE_ACCENT, "Batch upload cancelled");
        return;
    }
    int ok_count = 0, fail_count = 0;
    op_title = "Uploading marked saves";
    for (int i = 0; i < title_count; i++) {
        if (!titles[i].marked) continue;
        char msg[160];
        snprintf(msg, sizeof(msg), "Uploading %d/%d: %s", ok_count + fail_count + 1, marked,
                 titles[i].title_id_hex);
        sync_progress(msg);
        SyncResult res = sync_title(&config, &titles[i], NULL);
        if (res == SYNC_OK) {
            ok_count++;
            titles[i].in_conflict = false;
            titles[i].sync_state = TSTATE_UPLOADED;
        } else {
            fail_count++;
            titles[i].sync_state = TSTATE_FAILED;
        }
    }
    clear_marks();
    last_details_idx = -1;
    set_status(fail_count ? UI_TONE_WARN : UI_TONE_OK, "Batch upload: %d OK, %d failed", ok_count, fail_count);
}

static void do_smart_sync(void) {
    int idx = sel_title_idx();
    if (idx < 0) return;
    TitleInfo *t = &titles[idx];
    UiProgress pr = { 0 };
    pr.title = "Smart Sync";
    pr.name = t->name;
    pr.status = "Comparing with the server...";
    pr.frac = -1;
    ui_progress(&pr);

    if (!sync_get_save_details(&config, t, &last_details)) {
        last_details_idx = -1;
        set_status(UI_TONE_ERR, "Failed to load save details");
        return;
    }
    last_details_idx = idx;
    SyncAction suggested = sync_decide(&last_details);
    switch (suggested) {
        case SYNC_ACTION_UPLOAD:   t->sync_state = TSTATE_NEEDS_UPLOAD; break;
        case SYNC_ACTION_DOWNLOAD: t->sync_state = TSTATE_NEEDS_DOWNLOAD; break;
        case SYNC_ACTION_CONFLICT: t->sync_state = TSTATE_CONFLICT; break;
        default:                   t->sync_state = TSTATE_SYNCED; break;
    }

    SyncAction chosen = ui_confirm_smart_sync(t, &last_details, suggested);
    if (chosen == SYNC_ACTION_UPLOAD) {
        op_title = "Uploading";
        SyncResult res = sync_title(&config, t, sync_progress);
        if (res == SYNC_OK) {
            set_status(UI_TONE_OK, "Uploaded: %.40s", t->name);
            t->in_conflict = false;
            t->sync_state = TSTATE_UPLOADED;
        } else {
            set_status(UI_TONE_ERR, "Upload failed: %s", sync_result_str(res));
            t->sync_state = TSTATE_FAILED;
        }
        last_details_idx = -1;
    } else if (chosen == SYNC_ACTION_DOWNLOAD) {
        op_title = "Downloading";
        SyncResult res = sync_download_title(&config, t, sync_progress);
        if (res == SYNC_OK) {
            set_status(UI_TONE_OK, "Downloaded: %.40s", t->name);
            t->in_conflict = false;
            t->sync_state = TSTATE_DOWNLOADED;
        } else {
            set_status(UI_TONE_ERR, "Download failed: %s", sync_result_str(res));
            t->sync_state = TSTATE_FAILED;
        }
        last_details_idx = -1;
    } else if (suggested == SYNC_ACTION_UP_TO_DATE) {
        set_status(UI_TONE_OK, "Up to date: %.40s", t->name);
    } else {
        set_status(UI_TONE_ACCENT, "Sync cancelled");
    }
}

static void do_sync_all(void) {
    // Clear all conflict flags before sync
    for (int i = 0; i < title_count; i++)
        titles[i].in_conflict = false;

    op_title = "Sync all";
    SyncSummary summary;
    bool ok = sync_all(&config, titles, title_count, sync_progress, &summary);
    last_details_idx = -1;
    if (!ok) {
        set_status(UI_TONE_ERR, "Sync failed! Check the server.");
        return;
    }
    for (int i = 0; i < title_count && i < MAX_TITLES; i++)
        titles[i].sync_state = summary.title_state[i];

    // Mark conflicting titles in our list
    for (int i = 0; i < summary.conflicts && i < MAX_CONFLICT_DISPLAY; i++) {
        for (int j = 0; j < title_count; j++) {
            if (strcmp(titles[j].title_id_hex, summary.conflict_titles[i]) == 0) {
                titles[j].in_conflict = true;
                break;
            }
        }
    }

    if (summary.conflicts > 0) {
        // Auto-mark conflicting titles for batch resolve
        for (int i = 0; i < title_count; i++)
            if (titles[i].in_conflict || titles[i].sync_state == TSTATE_CONFLICT)
                titles[i].marked = true;

        char body[640];
        int pos = snprintf(body, sizeof(body),
                           UI_DIM "Up %d \xC2\xB7 Down %d \xC2\xB7 OK %d \xC2\xB7 Failed %d\n\n",
                           summary.uploaded, summary.downloaded, summary.up_to_date, summary.failed);
        int listed = 0;
        for (int i = 0; i < title_count && pos < (int)sizeof(body) - 80 && listed < 5; i++) {
            if (titles[i].in_conflict) {
                pos += snprintf(body + pos, sizeof(body) - pos, UI_HI "%.40s\n", titles[i].name);
                listed++;
            }
        }
        if (summary.conflicts > listed)
            pos += snprintf(body + pos, sizeof(body) - pos, UI_HI "...and %d more\n",
                            summary.conflicts - listed);
        snprintf(body + pos, sizeof(body) - pos,
                 "\nThey are marked: press A on the list to upload them all, or resolve each one.");
        char title[64];
        snprintf(title, sizeof(title), "%d conflict%s", summary.conflicts, summary.conflicts == 1 ? "" : "s");
        ui_message(UI_TONE_WARN, title, body);

        set_status(UI_TONE_WARN, "Up %d  Down %d  OK %d  Conflict %d  Failed %d",
                   summary.uploaded, summary.downloaded, summary.up_to_date,
                   summary.conflicts, summary.failed);
    } else {
        set_status(summary.failed ? UI_TONE_WARN : UI_TONE_OK, "Up %d  Down %d  OK %d  Failed %d",
                   summary.uploaded, summary.downloaded, summary.up_to_date, summary.failed);
    }
}

// Returns true if the app should exit (update installed, not relaunched)
static bool do_update(void) {
    ui_busy("Update", "Checking for updates...");

    UpdateInfo update_info;
    if (!update_check(&config, &update_info)) {
        set_status(UI_TONE_ERR, "Update check failed");
        return false;
    }
    if (!update_info.available) {
        set_status(UI_TONE_OK, "You have the latest version (%s)", APP_VERSION);
        return false;
    }

    char body[256];
    snprintf(body, sizeof(body),
             "Current  %s\n" UI_HI "Latest    %s\n" UI_DIM "Size       %lu KB\n\n"
             "Download and install it now?",
             APP_VERSION, update_info.latest_version, (unsigned long)(update_info.file_size / 1024));
    if (!ui_confirm(UI_TONE_ACCENT, "Update available", body, "Install")) {
        set_status(UI_TONE_ACCENT, "Update cancelled");
        return false;
    }

    update_phase = "Downloading update";
    update_progress_cb(0);
    if (!update_download(&config, update_info.download_url, update_progress_cb)) {
        set_status(UI_TONE_ERR, "Update download failed");
        return false;
    }
    update_phase = "Installing update";
    update_progress_cb(0);
    char install_error[128] = {0};
    if (!update_install(update_progress_cb, install_error, sizeof(install_error))) {
        ui_message(UI_TONE_ERR, "Install failed", install_error);
        set_status(UI_TONE_ERR, "Install failed");
        return false;
    }

    UiProgress pr = { 0 };
    pr.title = "Update installed";
    pr.tone = UI_TONE_OK;
    pr.name = "GameSync " APP_VERSION " -> new version";
    pr.status = "Restarting the application...";
    pr.frac = 1;
    pr.right = "100%";
    ui_progress(&pr);
    svcSleepThread(1500000000LL);
    update_relaunch();

    wait_for_start(UI_TONE_OK, "Update installed",
                   "Please restart the application to use the new version.");
    return true;
}

// ---------------------------------------------------------------------------
// Main
// ---------------------------------------------------------------------------

int main(int argc, char *argv[]) {
    (void)argc; (void)argv;

    // Initialize services
    ui_init();
    amInit();
    fsInit();
    psInit();  // For random number generation (console ID)
    card_spi_init();  // For NDS cartridge SPI save access

    ui_busy("Starting", "Loading config...");

    char config_error[512];
    if (!config_load(&config, config_error, sizeof(config_error))) {
        char msg[768];
        snprintf(msg, sizeof(msg),
            "%s\n\n"
            UI_DIM "Expected file at:\n"
            UI_HI "%s\n\n"
            UI_DIM "With contents:\n"
            UI_HI "server_url=http://<pc-ip>:8000\n"
            UI_HI "api_key=<your-key>",
            config_error, CONFIG_PATH);
        wait_for_start(UI_TONE_ERR, "Config error", msg);
        card_spi_exit();
        psExit();
        fsExit();
        amExit();
        ui_exit();
        return 0;
    }

    // Initialize network
    if (!network_init()) {
        wait_for_start(UI_TONE_ERR, "Network unavailable",
                       "Failed to start the network service.\n\nMake sure WiFi is enabled.");
        card_spi_exit();
        psExit();
        fsExit();
        amExit();
        ui_exit();
        return 0;
    }

    // Initial title scan
    scan_titles();
    set_status(UI_TONE_ACCENT, "Server: %.200s", config.server_url);
    ui_set_backdrop(saves_top, saves_bottom, NULL);
    hidSetRepeatParameters(20, 4);

    // Main loop
    while (aptMainLoop()) {
        gui_begin(true);
        gui_screen(GUI_TOP);
        saves_top(NULL);
        gui_screen(GUI_BOTTOM);
        saves_bottom(NULL);
        gui_end();

        hidScanInput();
        u32 kDown = hidKeysDown();
        u32 kRep = hidKeysDownRepeat();

        if (kDown & KEY_START)
            break;

        if (kRep & KEY_DOWN && filtered_count > 0) {
            selected = (selected + 1) % filtered_count;
            update_scroll();
        }

        if (kRep & KEY_UP && filtered_count > 0) {
            selected = (selected - 1 + filtered_count) % filtered_count;
            update_scroll();
        }

        // Page down
        if (kRep & KEY_RIGHT && filtered_count > 0) {
            selected += LIST_VISIBLE;
            if (selected >= filtered_count) selected = filtered_count - 1;
            update_scroll();
        }

        // Page up
        if (kRep & KEY_LEFT && filtered_count > 0) {
            selected -= LIST_VISIBLE;
            if (selected < 0) selected = 0;
            update_scroll();
        }

        // Tap a row to select it
        if (kDown & KEY_TOUCH && filtered_count > 0) {
            int i = ui_list_touch(SAVES_LIST_Y, SAVES_ROWS, SAVES_ROW_H, filtered_count, scroll_offset);
            if (i >= 0) selected = i;
        }

        // R button - cycle view mode (All -> 3DS -> NDS -> All)
        if (kDown & KEY_R) {
            view_mode = (view_mode + 1) % 3;
            rebuild_filter();
            const char *names[] = {"All", "3DS", "NDS"};
            set_status(UI_TONE_ACCENT, "View: %s (%d title%s)", names[view_mode], filtered_count,
                       filtered_count == 1 ? "" : "s");
        }

        // Y button - show history
        if (kDown & KEY_Y && filtered_count > 0)
            do_history();

        if (kDown & KEY_A && filtered_count > 0) {
            int marked = count_marked();
            if (marked > 0)
                do_batch_upload(marked);
            else
                do_smart_sync();
        }

        if (kDown & KEY_X && title_count > 0) {
            int keep = selected, keep_scroll = scroll_offset;
            do_sync_all();
            // The list itself didn't change: stay where we were
            if (keep < filtered_count) {
                selected = keep;
                scroll_offset = keep_scroll;
            }
        }

        // SELECT button - toggle mark on current item
        if (kDown & KEY_SELECT && filtered_count > 0) {
            int idx = sel_title_idx();
            if (idx >= 0) {
                titles[idx].marked = !titles[idx].marked;
                int mc = count_marked();
                if (mc > 0)
                    set_status(UI_TONE_ACCENT, "%d title%s marked", mc, mc == 1 ? "" : "s");
                else
                    set_status(UI_TONE_ACCENT, "Marks cleared");
            }
        }

        // B button (or the config menu entry) - game catalog
        bool open_catalog = (kDown & KEY_B) != 0;

        // L button - config editor (includes rescan + update options)
        if (kDown & KEY_L) {
            int result = ui_show_config_editor(&config);
            if (result == CONFIG_RESULT_CATALOG) {
                open_catalog = true;
            } else if (result == CONFIG_RESULT_RESCAN) {
                scan_titles();
                set_status(UI_TONE_OK, "Rescanned. %d title%s found.", title_count, title_count == 1 ? "" : "s");
            } else if (result == CONFIG_RESULT_SAVED) {
                set_status(UI_TONE_OK, "Settings saved. Server: %.30s", config.server_url);
            } else if (result == CONFIG_RESULT_UPDATE) {
                if (do_update()) goto cleanup;
            } else {
                set_status(UI_TONE_ACCENT, "Settings unchanged");
            }
        }

        if (open_catalog) {
            if (catalog_screen(&config)) {
                scan_titles();
                set_status(UI_TONE_OK, "Catalog closed. %d title%s found.", title_count, title_count == 1 ? "" : "s");
            } else {
                set_status(UI_TONE_ACCENT, "Catalog closed");
            }
        }
    }

cleanup:
    // Cleanup
    network_exit();
    card_spi_exit();
    psExit();
    fsExit();
    amExit();
    ui_exit();
    return 0;
}
