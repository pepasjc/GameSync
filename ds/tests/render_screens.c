// Renders every screen of the DS client's UI on a PC, from mock data, so the
// look can be reviewed without a DS. Writes one PPM per scene (top screen
// above bottom screen, like the console) into the directory given on the
// command line. Build and run with tests/render_screens.sh.

#include "views.h"
#include "ui_log.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

// catalog_data.c needs dirent's d_type, which MinGW lacks; the views only
// need this one function from it (same code as cat_format_size there)
void cat_format_size(uint64_t bytes, char *out, size_t size) {
    if (bytes >= 1000ULL * 1024 * 1024) {
        unsigned long hundredths = (unsigned long)(bytes * 100 / (1024ULL * 1024 * 1024));
        snprintf(out, size, "%lu.%02lu GB", hundredths / 100, hundredths % 100);
    } else if (bytes >= 1024ULL * 1024) {
        unsigned long tenths = (unsigned long)(bytes * 10 / (1024ULL * 1024));
        snprintf(out, size, "%lu.%lu MB", tenths / 10, tenths % 10);
    } else {
        snprintf(out, size, "%lu KB", (unsigned long)((bytes + 1023) / 1024));
    }
}

static Color top_px[SCREEN_W * SCREEN_H], bot_px[SCREEN_W * SCREEN_H];
static Surface top, bot;
static const char *out_dir = ".";
static int scenes;

static void write_scene(const char *name) {
    char path[512];
    snprintf(path, sizeof(path), "%s/%s.ppm", out_dir, name);
    FILE *f = fopen(path, "wb");
    if (!f) {
        perror(path);
        exit(1);
    }
    const int gap = 6;
    fprintf(f, "P6\n%d %d\n255\n", SCREEN_W, SCREEN_H * 2 + gap);
    for (int y = 0; y < SCREEN_H * 2 + gap; y++) {
        for (int x = 0; x < SCREEN_W; x++) {
            Color c;
            if (y < SCREEN_H) c = top_px[y * SCREEN_W + x];
            else if (y < SCREEN_H + gap) c = 0;
            else c = bot_px[(y - SCREEN_H - gap) * SCREEN_W + x];
            int r = c & 31, g = (c >> 5) & 31, b = (c >> 10) & 31;
            unsigned char px[3] = { (unsigned char)(r << 3 | r >> 2), (unsigned char)(g << 3 | g >> 2),
                                    (unsigned char)(b << 3 | b >> 2) };
            fwrite(px, 1, 3, f);
        }
    }
    fclose(f);
    scenes++;
}

static SyncState state;

static void add_title(const char *name, uint32_t size, bool scanned, SyncAction result, bool server) {
    Title *t = &state.titles[state.num_titles];
    memset(t, 0, sizeof(*t));
    snprintf(t->game_name, sizeof(t->game_name), "%s", name);
    snprintf(t->save_path, sizeof(t->save_path), "sd:/roms/nds/saves/%s.sav", name);
    t->save_size = size;
    t->scanned = scanned;
    t->scan_result = result;
    t->on_server = server;
    uint8_t id[8] = { 0x00, 0x04, 0x80, 0x00, 'A', 'D', 'A', (uint8_t)('E' + state.num_titles) };
    memcpy(t->title_id, id, 8);
    for (int i = 0; i < 32; i++) t->hash[i] = (uint8_t)(i * 37 + state.num_titles);
    t->hash_calculated = true;
    state.num_titles++;
}

static void mock_state(bool scanned) {
    memset(&state, 0, sizeof(state));
    snprintf(state.server_url, sizeof(state.server_url), "http://192.168.1.201:8000");
    snprintf(state.api_key, sizeof(state.api_key), "s3cr3t-key");
    snprintf(state.wifi_ssid, sizeof(state.wifi_ssid), "RetroLab");
    snprintf(state.wifi_wep_key, sizeof(state.wifi_wep_key), "abcdeabcdeabc");
    add_title("Pok\xc3\xa9mon HeartGold Version", 512 * 1024, scanned, SYNC_UPLOAD, true);
    add_title("Mario Kart DS", 8 * 1024, scanned, SYNC_UP_TO_DATE, true);
    add_title("The Legend of Zelda: Phantom Hourglass", 64 * 1024, scanned, SYNC_UP_TO_DATE, true);
    add_title("New Super Mario Bros.", 8 * 1024, scanned, SYNC_DOWNLOAD, true);
    add_title("Professor Layton and the Curious Village", 8 * 1024, scanned, SYNC_CONFLICT, true);
    add_title("Castlevania: Dawn of Sorrow", 8 * 1024, scanned, SYNC_UP_TO_DATE, true);
    add_title("Chrono Trigger", 8 * 1024, false, SYNC_UP_TO_DATE, false);
    add_title("Advance Wars: Dual Strike", 64 * 1024, scanned, SYNC_UP_TO_DATE, true);
    add_title("Phoenix Wright: Ace Attorney", 8 * 1024, scanned, SYNC_UP_TO_DATE, false);
    add_title("Dragon Quest IX: Sentinels of the Starry Skies", 512 * 1024, scanned, SYNC_UPLOAD, true);
    add_title("The World Ends with You", 64 * 1024, scanned, SYNC_UP_TO_DATE, true);
    add_title("Metroid Prime Hunters", 8 * 1024, scanned, SYNC_UP_TO_DATE, true);
    add_title("Kirby Super Star Ultra", 8 * 1024, scanned, SYNC_UP_TO_DATE, true);
    add_title("Animal Crossing: Wild World", 256 * 1024, scanned, SYNC_UP_TO_DATE, true);
}

static CatEntry cat_entries[CAT_ROWS];

static void mock_catalog(CatalogView *v) {
    static const struct { const char *name; uint32_t size; int ra; bool title_only; } games[CAT_ROWS] = {
        { "Advance Wars - Days of Ruin (USA)", 33 << 20, 52, false },
        { "Animal Crossing - Wild World (USA) (Rev 1)", 30 << 20, 0, false },
        { "Bangai-O Spirits (USA)", 18 << 20, 38, false },
        { "Castlevania - Order of Ecclesia (USA)", 64 << 20, 60, false },
        { "Chrono Trigger (USA) (En,Fr)", 32 << 20, 77, false },
        { "Contact (USA)", 34 << 20, 0, false },
        { "Dragon Quest V - Hand of the Heavenly Bride (USA)", 128 << 20, 41, true },
        { "Elite Beat Agents (USA)", 64 << 20, 30, false },
        { "Final Fantasy III (USA)", 64 << 20, 0, false },
        { "Ghost Trick - Phantom Detective (USA)", 128 << 20, 45, false },
    };
    memset(v, 0, sizeof(*v));
    for (int i = 0; i < CAT_ROWS; i++) {
        CatEntry *e = &cat_entries[i];
        memset(e, 0, sizeof(*e));
        snprintf(e->name, sizeof(e->name), "%s", games[i].name);
        snprintf(e->filename, sizeof(e->filename), "%s.zip", games[i].name);
        e->size = games[i].size;
        e->ra_achievements = games[i].ra;
        e->ra_game_id = games[i].ra ? 1000 + i : (i == 5 ? 77 : 0);
        e->ra_title_only = games[i].title_only;
        e->can_extract_nds = true;
        v->rows[i].entry = e;
        v->rows[i].installed = (i == 1 || i == 4);
    }
    v->system = "NDS";
    v->nsystems = 2;
    v->search = "";
    v->error = "";
    v->total = 1873;
    v->selected = 4;
    v->scroll = 0;
    v->nrows = CAT_ROWS;
    v->current = &cat_entries[4];
    v->current_installed = true;
    v->rom_dir = "sd:/roms/nds";
}

int main(int argc, char **argv) {
    if (argc > 1) out_dir = argv[1];
    gfx_surface_init(&top, top_px, SCREEN_W, SCREEN_H);
    gfx_surface_init(&bot, bot_px, SCREEN_W, SCREEN_H);

    // Boot
    ui_log_clear();
    ui_log_puts("Connecting WiFi...\nAttempt 1/3\nWFC connected!\nIP: 192.168.1.57\n");
    ui_log_puts("Scanning sd:/roms/nds/saves\nFound ROM: Mario Kart DS.nds\nBootstrap: 14 saves\n");
    view_splash(&top, "Scanning saves...");
    TaskView boot = { "Starting up", "Scanning saves", "", 0, 0, KIND_INFO, false, NULL };
    view_task(&bot, &boot);
    write_scene("01_boot");

    // Main screen, list focused
    theme_wifi = true;
    mock_state(true);
    view_main_top(&top, &state, 0, false, 0, true);
    view_save_list(&bot, &state, 0, 0, true, true);
    write_scene("02_main");

    // Not scanned yet, another selection
    mock_state(false);
    view_main_top(&top, &state, 4, false, 0, true);
    view_save_list(&bot, &state, 4, 0, true, true);
    write_scene("03_main_unscanned");

    // Settings menu focused
    mock_state(true);
    view_main_top(&top, &state, 2, true, 5, true);
    view_save_list(&bot, &state, 2, 0, false, true);
    write_scene("04_menu");

    // No saves, offline
    theme_wifi = false;
    SyncState empty;
    memset(&empty, 0, sizeof(empty));
    snprintf(empty.server_url, sizeof(empty.server_url), "http://192.168.1.201:8000");
    view_main_top(&top, &empty, 0, false, 0, false);
    view_save_list(&bot, &empty, 0, 0, true, false);
    write_scene("05_empty_offline");
    theme_wifi = true;

    // Save details (Y)
    view_main_top(&top, &state, 0, false, 0, true);
    view_save_details(&bot, &state.titles[0]);
    write_scene("06_details");

    // Smart sync: upload, download, conflict, up to date
    CompareView cv = { "Smart Sync", state.titles[0].game_name, true, 512 * 1024, "3c9a17f2e05b4d61",
                       true, 512 * 1024, "8e21d0aa94c3b7f0", "8e21d0aa94c3b7f0", SYNC_UPLOAD };
    view_sync_compare(&top, &cv);
    view_sync_action(&bot, cv.game, SYNC_UPLOAD, true);
    write_scene("07_sync_upload");
    cv.action = SYNC_DOWNLOAD;
    snprintf(cv.last_hash, sizeof(cv.last_hash), "3c9a17f2e05b4d61");
    view_sync_compare(&top, &cv);
    view_sync_action(&bot, cv.game, SYNC_DOWNLOAD, true);
    write_scene("08_sync_download");
    cv.action = SYNC_CONFLICT;
    snprintf(cv.last_hash, sizeof(cv.last_hash), "11aa22bb33cc44dd");
    view_sync_compare(&top, &cv);
    view_sync_action(&bot, cv.game, SYNC_CONFLICT, true);
    write_scene("09_sync_conflict");
    cv.action = SYNC_UP_TO_DATE;
    cv.has_server = true;
    snprintf(cv.server_hash, sizeof(cv.server_hash), "%s", cv.local_hash);
    view_sync_compare(&top, &cv);
    view_sync_action(&bot, cv.game, SYNC_UP_TO_DATE, true);
    write_scene("10_sync_ok");

    // Manual upload (R)
    CompareView up = { "Upload", state.titles[1].game_name, true, 8192, "0a1b2c3d4e5f6071",
                       false, 0, "", "", SYNC_UPLOAD };
    view_sync_compare(&top, &up);
    view_transfer_confirm(&bot, up.game, true, false, false);
    write_scene("11_upload_confirm");

    // Task with a result
    ui_log_clear();
    ui_log_puts("=== Upload Debug ===\nServer: http://192.168.1.201:8000\nGame: Mario Kart DS\n");
    ui_log_puts("URL: http://192.168.1.201:8000/api/v1/saves/00048000414D4345/raw\nSize: 8192 bytes\n");
    ui_log_puts("Sending POST...\nHTTP 200\n");
    TaskView done = { "Upload", "Upload successful", "Mario Kart DS", 0, 0, KIND_OK, true, NULL };
    static const Hint back[] = { { "B", "Back" }, { NULL, NULL } };
    done.hints = back;
    view_main_top(&top, &state, 1, false, 0, true);
    view_task(&bot, &done);
    write_scene("12_task_result");

    // Scan all in progress, then the summary
    ui_log_clear();
    ui_log_puts("  [1/14] Pokemon HeartGold Ver\n" "\x1b[33m    -> Needs upload\x1b[39m\n");
    ui_log_puts("  [2/14] Mario Kart DS\n  [3/14] The Legend of Zelda:\n  [4/14] New Super Mario Bros\n");
    ui_log_puts("\x1b[36m    -> Needs download\x1b[39m\n  [5/14] Professor Layton and\n");
    ui_log_puts("\x1b[31m    -> CONFLICT\x1b[39m\n  [6/14] Castlevania: Dawn of\n");
    TaskView scan = { "Scan all", "Checking saves", "6 / 14", 6, 14, KIND_INFO, false, NULL };
    view_task(&bot, &scan);
    write_scene("13_scan_progress");
    SummaryRow rows[] = {
        { "Up to date", "10", C_OK }, { "Need upload", "2", C_WARN }, { "Need download", "1", C_INFO },
        { "Conflicts", "1", C_ERR }, { "Failed", "0", C_TEXT_FAINT },
    };
    static const Hint ok[] = { { "A", "OK" }, { NULL, NULL } };
    view_summary(&bot, "Scan all", "Scan complete", KIND_OK, rows, 5,
                 "Out-of-sync saves are marked in the list: amber upload, blue download, red conflict.", ok);
    write_scene("14_scan_summary");

    // Editor
    view_main_top(&top, &state, 0, true, 0, true);
    const char *charset = "abcdefghijklmnopqrstuvwxyz0123456789.:/-_ABCDEFGHIJKLMNOPQRSTUVWXYZ@?=&#%+! ";
    view_editor(&bot, "Server URL", "http://192.168.1.201:8000", 25, 14, charset, (int)strlen(charset));
    write_scene("15_editor");
    view_editor(&bot, "Search game names (empty = all)", "zelda", 5, 5, charset, (int)strlen(charset));
    write_scene("16_editor_search");

    // RetroAchievements
    view_main_top(&top, &state, 0, true, 7, true);
    view_ra_menu(&bot, "sd:", true);
    write_scene("17_ra_menu");
    ui_log_clear();
    ui_log_puts("sd:/roms/nds: 212 ROMs\nHold B to stop\nHashing 212/212\n");
    ui_log_puts("Contact (USA).nds\n  \x1b[31mbad set file\x1b[39m\n180 games, 6 requests\n");
    ui_log_puts("Getting sets 1-32...\nAdvance Wars - Days of Ruin.nds\n  \x1b[32m52 achievements\x1b[39m\n");
    ui_log_puts("Getting sets 33-64...\n");
    TaskView ra = { "RetroAchievements", "Getting achievement sets", "33-64 of 180", 64, 180, KIND_RA, false, NULL };
    static const Hint stop[] = { { "B", "Hold to stop" }, { NULL, NULL } };
    ra.hints = stop;
    view_task(&bot, &ra);
    write_scene("18_ra_update");

    // Catalog
    CatalogView cat;
    mock_catalog(&cat);
    view_catalog_details(&top, &cat);
    view_catalog_list(&bot, &cat);
    write_scene("19_catalog");
    cat.ra_only = true;
    cat.search = "dragon";
    cat.selected = 6;
    cat.current = &cat_entries[6];
    cat.current_installed = false;
    cat.total = 3;
    cat.nrows = 3;
    cat.rows[0].entry = &cat_entries[6];
    cat.rows[1].entry = &cat_entries[0];
    cat.rows[2].entry = NULL;
    cat.scroll = 6;
    cat.loading = true;
    view_catalog_details(&top, &cat);
    view_catalog_list(&bot, &cat);
    write_scene("20_catalog_filtered");
    mock_catalog(&cat);
    cat.total = -1;
    cat.nrows = 0;
    cat.current = NULL;
    cat.error = "No response from server";
    view_catalog_details(&top, &cat);
    view_catalog_list(&bot, &cat);
    write_scene("21_catalog_error");

    mock_catalog(&cat);
    view_catalog_details(&top, &cat);
    view_install_confirm(&bot, &cat_entries[3], "sd:/roms/nds", "Castlevania - Order of Ecclesia (USA).nds", true);
    write_scene("22_install_confirm");
    InstallView iv = { "Castlevania - Order of Ecclesia (USA)", true, 23 << 20, 64 << 20, 412 * 1024, 398 * 1024,
                       59, 106, false, KIND_OK, NULL, { NULL }, { 0 } };
    view_install(&bot, &iv);
    write_scene("23_install_progress");
    iv.done = iv.total;
    iv.finished = true;
    iv.result = "Installed";
    iv.lines[0] = "64.0 MB in 2:41";
    iv.line_colors[0] = C_TEXT;
    iv.lines[1] = "Average: 398 KB/s";
    iv.line_colors[1] = C_TEXT_DIM;
    iv.lines[2] = "60 achievements ready";
    iv.line_colors[2] = C_GOLD;
    view_install(&bot, &iv);
    write_scene("24_install_done");

    // Plain message
    static const Hint any[] = { { "A", "Continue" }, { NULL, NULL } };
    view_splash(&top, "");
    theme_message(&bot, "Starting up", "WiFi unavailable",
                  "Upload and download are disabled. Configure WiFi in the DS settings or set wifi_ssid "
                  "in config.txt, then use Connect WiFi.", KIND_WARN, any);
    write_scene("25_message");

    printf("rendered %d scenes to %s\n", scenes, out_dir);
    return 0;
}
