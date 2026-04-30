#include "ui.h"
#include "sync.h"
#include "roms.h"
#include "downloads.h"

#include <SDL/SDL.h>
#include <SDL/SDL_gfxPrimitives.h>
#include <io/pad.h>
#include <sysutil/sysutil.h>

#include <stdbool.h>
#include <stdarg.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>

static char g_status_line[256];
static volatile int g_ui_exit = 0;   /* set by sysutil EXIT_GAME */
static volatile int g_xmb_open = 0;  /* set by sysutil MENU_OPEN/CLOSE */

void ui_notify_exit(void)       { g_ui_exit  = 1; }
void ui_notify_menu_open(void)  { g_xmb_open = 1; }
void ui_notify_menu_close(void) { g_xmb_open = 0; }
int  ui_exit_requested(void)    { return g_ui_exit; }
int  ui_menu_open(void)         { return g_xmb_open; }

#define MAX_PADS_UI 7

#define SCREEN_WIDTH  1920
#define SCREEN_HEIGHT 1080
#define LINE_HEIGHT   22
#define LIST_START_Y  95
#define LIST_VISIBLE_ROWS 35

static SDL_Surface *g_screen = NULL;

typedef struct {
    Uint8 r;
    Uint8 g;
    Uint8 b;
} UiColor;

static void draw_text(int x, int y, UiColor color, const char *text) {
    if (!g_screen || !text) {
        return;
    }
    stringRGBA(g_screen, (Sint16)x, (Sint16)y, text, color.r, color.g, color.b, 255);
}

static void draw_textf(int x, int y, UiColor color, const char *fmt, ...) {
    char buffer[512];
    va_list args;

    va_start(args, fmt);
    vsnprintf(buffer, sizeof(buffer), fmt, args);
    va_end(args);
    draw_text(x, y, color, buffer);
}

bool ui_init(char *error_buf, size_t error_buf_size) {
    if (SDL_Init(SDL_INIT_VIDEO | SDL_INIT_JOYSTICK) != 0) {
        snprintf(error_buf, error_buf_size, "SDL_Init failed: %s", SDL_GetError());
        return false;
    }

    g_screen = SDL_SetVideoMode(SCREEN_WIDTH, SCREEN_HEIGHT, 32, SDL_HWSURFACE | SDL_DOUBLEBUF);
    if (!g_screen) {
        /* Fall back to software surface */
        g_screen = SDL_SetVideoMode(SCREEN_WIDTH, SCREEN_HEIGHT, 32, SDL_SWSURFACE);
        if (!g_screen) {
            snprintf(error_buf, error_buf_size, "SDL_SetVideoMode failed: %s", SDL_GetError());
            SDL_Quit();
            return false;
        }
    }

    SDL_ShowCursor(SDL_DISABLE);
    return true;
}

void ui_shutdown(void) {
    g_screen = NULL;
    SDL_Quit();
}

static const char *kind_label(const TitleInfo *title) {
    switch (title->kind) {
        case SAVE_KIND_PS3:     return "PS3";
        case SAVE_KIND_PS1_VM1: return "PS1";
        case SAVE_KIND_PS1:     return "PS1";
        default:                return "???";
    }
}

/* Tab strip — drawn in the right half of every view's header bar so the
 * user always sees which view they're in, what comes next, and how to
 * get there.  Active tab is bright yellow; others sit in muted accent. */
static void draw_tab_strip(AppView current) {
    if (!g_screen) return;
    static const char *names[APP_VIEW_COUNT] = {
        "Saves", "ROM Catalog", "Downloads"
    };
    UiColor active   = {255, 222, 89};   /* hilite yellow */
    UiColor inactive = {120, 145, 180};  /* muted accent */
    UiColor sep      = {88, 100, 132};

    /* Right-anchor the strip so it never overlaps the version string on
     * the left.  We draw a fixed width: "Saves | ROM Catalog | Downloads
     * (SELECT → next)" is ~78 chars at 8 px/char ≈ 620 px.  Plenty of
     * room on a 1920-pixel header. */
    int x = SCREEN_WIDTH - 700;
    int y = 18;

    for (int i = 0; i < APP_VIEW_COUNT; i++) {
        UiColor color = (i == (int)current) ? active : inactive;
        char buf[48];
        snprintf(buf, sizeof(buf), "[%d] %s", i + 1, names[i]);
        draw_text(x, y, color, buf);
        x += 9 * (int)strlen(buf) + 16;
        if (i + 1 < APP_VIEW_COUNT) {
            draw_text(x, y, sep, "|");
            x += 16;
        }
    }
    /* Hint immediately to the right of the strip — same line so the
     * user's eye can sweep header → strip → hint in one read. */
    int next = ((int)current + 1) % APP_VIEW_COUNT;
    char hint[64];
    /* Plain ASCII arrow — SDL_gfx's bitmap font on PS3 doesn't render
     * non-ASCII glyphs reliably. */
    snprintf(hint, sizeof(hint), "(SELECT -> %s)", names[next]);
    draw_text(x, y, active, hint);
}

void ui_clear(void) {
    if (!g_screen) {
        return;
    }
    SDL_FillRect(g_screen, NULL, SDL_MapRGB(g_screen->format, 8, 10, 18));
}

void ui_draw_message(const char *title, const char *message, const char *footer) {
    const char *cursor;
    char line[256];
    int y = 40;

    if (!g_screen) {
        return;
    }
    if (g_xmb_open) {
        return;
    }

    ui_clear();
    boxRGBA(g_screen, 0, 0, SCREEN_WIDTH - 1, SCREEN_HEIGHT - 1, 8, 10, 18, 255);
    draw_text(24, y, (UiColor){88, 208, 255}, title ? title : "GameSync PS3");
    y += 36;

    if (message) {
        cursor = message;
        while (*cursor != '\0') {
            size_t len = strcspn(cursor, "\n");
            if (len >= sizeof(line)) {
                len = sizeof(line) - 1;
            }
            memcpy(line, cursor, len);
            line[len] = '\0';
            draw_text(24, y, (UiColor){240, 240, 240}, line);
            y += LINE_HEIGHT;
            cursor += len;
            if (*cursor == '\n') {
                cursor++;
            }
        }
    }

    if (footer && footer[0] != '\0') {
        draw_text(24, SCREEN_HEIGHT - 28, (UiColor){160, 168, 184}, footer);
    }

    SDL_PumpEvents();
    SDL_Flip(g_screen);
}

void ui_draw_list(
    const SyncState *state,
    const int *visible,
    int visible_count,
    int selected,
    int scroll_offset,
    const char *status_line,
    bool config_created,
    bool show_server_only
) {
    int i;
    int end;
    int y = 18;
    UiColor dim = {160, 168, 184};
    UiColor white = {240, 240, 240};
    UiColor accent = {88, 208, 255};
    UiColor hilite = {255, 222, 89};
    UiColor border = {44, 58, 82};
    if (g_xmb_open) {
        return;
    }

    ui_clear();
    boxRGBA(g_screen, 0, 0, SCREEN_WIDTH - 1, 55, 14, 20, 34, 255);
    rectangleRGBA(g_screen, 12, 64, SCREEN_WIDTH - 12, SCREEN_HEIGHT - 48, border.r, border.g, border.b, 255);
    draw_textf(24, y, accent, "GameSync PS3 v%s", APP_VERSION);
    draw_tab_strip(APP_VIEW_SAVES);
    y += 22;
    draw_textf(24, y, white, "Server: %s", state->server_url);
    y += 18;
    draw_textf(
        24,
        y,
        dim,
        "User: %08d | Showing: %d of %d%s",
        state->selected_user,
        visible_count,
        state->num_titles,
        config_created ? " | debug config auto-created" : ""
    );
    draw_text(24, SCREEN_HEIGHT - 28, dim, status_line ? status_line : "Ready.");
    draw_textf(24, SCREEN_HEIGHT - 46, dim,
        "Up/Dn: nav   X: sync   R3: sync all   Sq: upload   Tri: download   L3: hash   R1: compare   O: rescan   L1: filter[%s]   L2/R2: user   Start: config   Select: views   PS/Home: exit",
        show_server_only ? "ON" : "OFF");

    if (visible_count == 0) {
        draw_text(28, LIST_START_Y, white, "No saves found.");
    } else {
        end = scroll_offset + LIST_VISIBLE_ROWS;
        if (end > visible_count) {
            end = visible_count;
        }

        for (i = scroll_offset; i < end; i++) {
            const TitleInfo *title = &state->titles[visible[i]];
            char line[256];
            bool is_selected = (i == selected);

            /* Status label and base color */
            const char *status_label;
            UiColor status_color;
            switch (title->status) {
                case TITLE_STATUS_LOCAL_ONLY:
                    status_label = "LOC "; status_color = (UiColor){255, 200,  80}; break;
                case TITLE_STATUS_SERVER_ONLY:
                    status_label = "SVR "; status_color = (UiColor){ 80, 180, 255}; break;
                case TITLE_STATUS_SYNCED:
                    status_label = "SYNC"; status_color = (UiColor){ 80, 220, 120}; break;
                case TITLE_STATUS_UPLOAD:
                    status_label = "UP  "; status_color = (UiColor){140, 180, 255}; break;
                case TITLE_STATUS_DOWNLOAD:
                    status_label = "DL  "; status_color = (UiColor){ 80, 220, 220}; break;
                case TITLE_STATUS_CONFLICT:
                    status_label = "CONF"; status_color = (UiColor){255,  80,  80}; break;
                default:
                    status_label = "?   "; status_color = (UiColor){160, 168, 184}; break;
            }

            UiColor color = is_selected ? hilite : status_color;
            char marker = is_selected ? '>' : ' ';
            const char *display_name = title->name[0] ? title->name : "";
            snprintf(
                line,
                sizeof(line),
                "%c [%s] %-3s  %-10.10s  %-40.40s  %u",
                marker,
                status_label,
                kind_label(title),
                title->game_code,
                display_name,
                (unsigned int)title->total_size
            );
            draw_text(28, LIST_START_Y + ((i - scroll_offset) * LINE_HEIGHT), color, line);
        }

        if (selected >= 0 && selected < visible_count) {
            const TitleInfo *title = &state->titles[visible[selected]];
            char hash_hex[65];

            if (title->hash_calculated) {
                static const char hex_chars[] = "0123456789abcdef";
                size_t j;
                for (j = 0; j < 32; j++) {
                    hash_hex[j * 2] = hex_chars[(title->hash[j] >> 4) & 0x0F];
                    hash_hex[j * 2 + 1] = hex_chars[title->hash[j] & 0x0F];
                }
                hash_hex[64] = '\0';
            } else {
                snprintf(hash_hex, sizeof(hash_hex), "not computed");
            }

            boxRGBA(g_screen, 1020, 88, SCREEN_WIDTH - 24, 430, 12, 18, 30, 255);
            rectangleRGBA(g_screen, 1020, 88, SCREEN_WIDTH - 24, 430, border.r, border.g, border.b, 255);
            static const char *status_names[] = {
                "Unknown", "Local only", "Server only",
                "Synced", "Need upload", "Need download", "Conflict"
            };
            int sidx = (int)title->status;
            if (sidx < 0 || sidx > 6) sidx = 0;
            draw_textf(1036, 104, accent, "Selected: %s", title->game_code);
            draw_textf(1036, 128, white, "Status: %s", status_names[sidx]);
            draw_textf(1036, 152, white, "Name: %.46s", title->name[0] ? title->name : "(unknown)");
            draw_textf(1036, 174, white, "Kind: %s", kind_label(title));
            draw_textf(1036, 196, white, "Files: %d", title->file_count);
            draw_textf(1036, 218, white, "Size: %u", (unsigned int)title->total_size);
            draw_text(1036, 244, dim, "Path:");
            draw_text(1036, 266, white, title->server_only ? "(not on device)" : title->local_path);
            draw_text(1036, 296, dim, "Local Hash:");
            draw_text(1036, 318, white, hash_hex);
            draw_text(1036, 348, dim, "Server Hash:");
            if (!title->on_server) {
                draw_text(1036, 370, dim, "(not on server)");
            } else if (!title->server_meta_loaded) {
                draw_text(1036, 370, dim, "(loading...)");
            } else if (title->server_hash[0]) {
                draw_text(1036, 370, white, title->server_hash);
            } else {
                draw_text(1036, 370, dim, "(unavailable)");
            }
        }
    }

    SDL_PumpEvents();
    SDL_Flip(g_screen);
}

void ui_draw_config_editor(
    const char *server_url,
    const char *api_key,
    int selected_user,
    bool scan_ps3,
    bool scan_ps1,
    int selected_field,
    bool dirty
) {
    UiColor dim = {160, 168, 184};
    UiColor white = {240, 240, 240};
    UiColor accent = {88, 208, 255};
    UiColor hilite = {255, 222, 89};
    UiColor border = {44, 58, 82};
    const char *markers[] = {" ", " ", " ", " ", " ", " ", " "};
    char user_buf[32];
    char line[512];

    if (!g_screen || g_xmb_open) {
        return;
    }

    ui_clear();
    boxRGBA(g_screen, 0, 0, SCREEN_WIDTH - 1, 55, 14, 20, 34, 255);
    rectangleRGBA(g_screen, 12, 64, SCREEN_WIDTH - 12, SCREEN_HEIGHT - 48,
                  border.r, border.g, border.b, 255);
    draw_text(24, 18, accent, "GameSync PS3 -- Config");
    draw_text(24, 44, dim, dirty ? "Unsaved changes" : "Saved values");

    if (selected_user <= 0) {
        snprintf(user_buf, sizeof(user_buf), "Auto");
    } else {
        snprintf(user_buf, sizeof(user_buf), "%08d", selected_user);
    }

    if (selected_field >= 0 && selected_field < 7) {
        markers[selected_field] = ">";
    }

    snprintf(line, sizeof(line), "%s Server URL: %s", markers[0], server_url);
    draw_text(28, 104, selected_field == 0 ? hilite : white, line);
    snprintf(line, sizeof(line), "%s API Key:    %s", markers[1], api_key);
    draw_text(28, 132, selected_field == 1 ? hilite : white, line);
    snprintf(line, sizeof(line), "%s User:       %s", markers[2], user_buf);
    draw_text(28, 160, selected_field == 2 ? hilite : white, line);
    snprintf(line, sizeof(line), "%s Scan PS3:   %s", markers[3], scan_ps3 ? "ON" : "OFF");
    draw_text(28, 188, selected_field == 3 ? hilite : white, line);
    snprintf(line, sizeof(line), "%s Scan PS1:   %s", markers[4], scan_ps1 ? "ON" : "OFF");
    draw_text(28, 216, selected_field == 4 ? hilite : white, line);
    snprintf(line, sizeof(line), "%s Save and Apply", markers[5]);
    draw_text(28, 272, selected_field == 5 ? hilite : accent, line);
    snprintf(line, sizeof(line), "%s Cancel", markers[6]);
    draw_text(28, 300, selected_field == 6 ? hilite : accent, line);

    draw_text(28, 372, dim, "Up/Down: select field");
    draw_text(28, 394, dim, "Cross: edit/toggle/confirm");
    draw_text(28, 416, dim, "Left/Right: change user or toggle switch");
    draw_text(28, 438, dim, "Circle: cancel editor");

    draw_text(24, SCREEN_HEIGHT - 28, dim,
              "Start: config   Cross: select   Circle: cancel   PS/Home: exit");

    SDL_PumpEvents();
    SDL_Flip(g_screen);
}

void ui_draw_text_editor(const char *label, const char *value, int cursor_pos) {
    UiColor dim = {160, 168, 184};
    UiColor white = {240, 240, 240};
    UiColor accent = {88, 208, 255};
    UiColor hilite = {255, 222, 89};
    char caret[512];
    int caret_x;
    int value_len;

    if (!g_screen || g_xmb_open) {
        return;
    }

    ui_clear();
    boxRGBA(g_screen, 0, 0, SCREEN_WIDTH - 1, 55, 14, 20, 34, 255);
    draw_text(24, 18, accent, "GameSync PS3 -- Text Editor");
    draw_textf(24, 76, accent, "%s", label ? label : "Value");
    draw_text(24, 120, white, value ? value : "");

    memset(caret, ' ', sizeof(caret));
    caret[sizeof(caret) - 1] = '\0';
    value_len = value ? (int)strlen(value) : 0;
    if (cursor_pos < 0) cursor_pos = 0;
    if (cursor_pos > value_len) cursor_pos = value_len;
    if (cursor_pos >= (int)sizeof(caret) - 2) cursor_pos = (int)sizeof(caret) - 3;
    caret[cursor_pos] = '^';
    caret[cursor_pos + 1] = '\0';
    caret_x = 24;
    draw_text(caret_x, 142, hilite, caret);

    draw_text(24, 220, dim, "Left/Right: move cursor");
    draw_text(24, 242, dim, "Up/Down: change current character");
    draw_text(24, 264, dim, "Square: insert space   Triangle: delete");
    draw_text(24, 286, dim, "Cross: accept   Circle: cancel");

    draw_text(24, SCREEN_HEIGHT - 28, dim,
              "Up/Dn: char   Left/Right: cursor   Sq/Tri: insert/delete   Cross: save   Circle: cancel");

    SDL_PumpEvents();
    SDL_Flip(g_screen);
}

/* ---- Helper: read pad until all buttons released ---- */

static void drain_buttons(void) {
    padInfo padinfo;
    padData paddata;
    int done = 0;
    while (!done) {
        sysUtilCheckCallback();
        if (g_ui_exit || g_xmb_open) return;
        done = 1;
        ioPadGetInfo(&padinfo);
        for (int i = 0; i < MAX_PADS_UI; i++) {
            if (!padinfo.status[i]) continue;
            ioPadGetData(i, &paddata);
            if (paddata.BTN_CROSS || paddata.BTN_CIRCLE || paddata.BTN_SQUARE ||
                paddata.BTN_TRIANGLE || paddata.BTN_START || paddata.BTN_L3 || paddata.BTN_R3 ||
                paddata.BTN_UP || paddata.BTN_DOWN) {
                done = 0;
            }
        }
        SDL_PumpEvents();
        usleep(16000);
    }
}

/* ---- ui_status: show a progress message immediately ---- */

void ui_status(const char *fmt, ...) {
    va_list args;
    va_start(args, fmt);
    vsnprintf(g_status_line, sizeof(g_status_line), fmt, args);
    va_end(args);

    if (!g_screen) return;
    if (g_xmb_open) return;

    ui_clear();
    boxRGBA(g_screen, 0, 0, SCREEN_WIDTH - 1, 55, 14, 20, 34, 255);
    draw_text(24, 18, (UiColor){88, 208, 255}, "GameSync PS3");
    draw_text(24, SCREEN_HEIGHT / 2 - 10, (UiColor){240, 240, 240}, g_status_line);

    SDL_PumpEvents();
    SDL_Flip(g_screen);
}

/* ---- ui_message: blocking full-screen message, Cross to continue ---- */

void ui_message(const char *fmt, ...) {
    char buf[1024];
    va_list args;
    va_start(args, fmt);
    vsnprintf(buf, sizeof(buf), fmt, args);
    va_end(args);

    if (!g_screen) return;

    ui_clear();
    boxRGBA(g_screen, 0, 0, SCREEN_WIDTH - 1, 55, 14, 20, 34, 255);
    draw_text(24, 18, (UiColor){88, 208, 255}, "GameSync PS3");

    int y = 80;
    const char *cursor = buf;
    while (*cursor && y < SCREEN_HEIGHT - 60) {
        char line[128];
        size_t len = strcspn(cursor, "\n");
        if (len >= sizeof(line)) len = sizeof(line) - 1;
        memcpy(line, cursor, len);
        line[len] = '\0';
        draw_text(24, y, (UiColor){240, 240, 240}, line);
        y += LINE_HEIGHT;
        cursor += len;
        if (*cursor == '\n') cursor++;
    }

    draw_text(24, SCREEN_HEIGHT - 28, (UiColor){160, 168, 184}, "Cross: continue   PS/Home: exit");

    SDL_PumpEvents();
    SDL_Flip(g_screen);

    padInfo padinfo;
    padData paddata;
    int prev_cross = 1;
    while (1) {
        sysUtilCheckCallback();
        if (g_ui_exit) return;
        if (g_xmb_open) {
            SDL_PumpEvents();
            usleep(50000);
            continue;
        }
        SDL_PumpEvents();
        ioPadGetInfo(&padinfo);
        for (int i = 0; i < MAX_PADS_UI; i++) {
            if (!padinfo.status[i]) continue;
            ioPadGetData(i, &paddata);
            if (!prev_cross && paddata.BTN_CROSS) return;
            prev_cross = paddata.BTN_CROSS;
        }
        usleep(50000);
    }
}

/* ---- ui_confirm: show sync confirmation dialog ---- */

bool ui_confirm(const TitleInfo *title, SyncAction action,
                const char *server_hash, uint32_t server_size,
                const char *server_last_sync) {
    if (!g_screen) return false;

    drain_buttons();

    const char *action_str =
        action == SYNC_UPLOAD     ? "UPLOAD to server" :
        action == SYNC_DOWNLOAD   ? "DOWNLOAD from server" :
        action == SYNC_CONFLICT   ? "CONFLICT (both changed)" :
        action == SYNC_UP_TO_DATE ? "Already up to date" :
                                    "Failed / unknown";
    const char *kind_str = title->kind == SAVE_KIND_PS3 ? "PS3" : "PS1";

    ui_clear();
    boxRGBA(g_screen, 0, 0, SCREEN_WIDTH - 1, 55, 14, 20, 34, 255);
    draw_text(24, 18, (UiColor){88, 208, 255}, "GameSync PS3 -- Confirm");

    int y = 80;
    UiColor white = {240, 240, 240};
    UiColor accent = {88, 208, 255};
    UiColor yellow = {255, 222, 89};
    UiColor dim    = {160, 168, 184};

    draw_textf(24, y, accent, "Game:    %s",
               title->name[0] ? title->name : title->game_code);
    y += LINE_HEIGHT + 4;
    draw_textf(24, y, white,  "Code:    %s  [%s]", title->game_code, kind_str);
    y += LINE_HEIGHT + 4;
    draw_textf(24, y, yellow, "Action:  %s", action_str);
    y += LINE_HEIGHT + 8;

    if (title->server_only) {
        draw_text(24, y, dim, "Local:   (not on device)");
    } else {
        draw_textf(24, y, white, "Local:   %u bytes  (%d files)",
                   (unsigned)title->total_size, title->file_count);
    }
    y += LINE_HEIGHT + 4;

    if (server_hash && server_hash[0]) {
        draw_textf(24, y, white, "Server:  %u bytes", (unsigned)server_size);
        y += LINE_HEIGHT + 4;
        if (server_last_sync && server_last_sync[0]) {
            char date[20] = "";
            if (strlen(server_last_sync) >= 16 && server_last_sync[10] == 'T')
                snprintf(date, sizeof(date), "%.10s %.5s",
                         server_last_sync, server_last_sync + 11);
            else
                snprintf(date, sizeof(date), "%.16s", server_last_sync);
            draw_textf(24, y, white, "Date:    %s", date);
        }
    } else {
        draw_text(24, y, dim, "Server:  (no save)");
    }

    if (action == SYNC_UP_TO_DATE) {
        draw_text(24, SCREEN_HEIGHT - 28, dim, "Cross: OK   PS/Home: exit");
        SDL_PumpEvents();
        SDL_Flip(g_screen);

        padInfo padinfo2;
        padData paddata2;
        int prev2 = 1;
        while (1) {
            sysUtilCheckCallback();
            if (g_ui_exit) return false;
            if (g_xmb_open) {
                SDL_PumpEvents();
                usleep(50000);
                continue;
            }
            SDL_PumpEvents();
            ioPadGetInfo(&padinfo2);
            for (int i = 0; i < MAX_PADS_UI; i++) {
                if (!padinfo2.status[i]) continue;
                ioPadGetData(i, &paddata2);
                if (!prev2 && (paddata2.BTN_CROSS || paddata2.BTN_CIRCLE)) return false;
                prev2 = paddata2.BTN_CROSS | paddata2.BTN_CIRCLE;
            }
            usleep(50000);
        }
    }

    draw_text(24, SCREEN_HEIGHT - 28, dim, "Cross: Confirm   Circle: Cancel   PS/Home: exit");

    SDL_PumpEvents();
    SDL_Flip(g_screen);

    padInfo padinfo;
    padData paddata;
    int prev_cross = 1, prev_circle = 1;
    while (1) {
        sysUtilCheckCallback();
        if (g_ui_exit) return false;
        if (g_xmb_open) {
            SDL_PumpEvents();
            usleep(50000);
            continue;
        }
        SDL_PumpEvents();
        ioPadGetInfo(&padinfo);
        for (int i = 0; i < MAX_PADS_UI; i++) {
            if (!padinfo.status[i]) continue;
            ioPadGetData(i, &paddata);
            if (!prev_cross  && paddata.BTN_CROSS)  return true;
            if (!prev_circle && paddata.BTN_CIRCLE) return false;
            prev_cross  = paddata.BTN_CROSS;
            prev_circle = paddata.BTN_CIRCLE;
        }
        usleep(50000);
    }
}

/* ============================================================
 * ROM catalog + downloads views
 * ============================================================
 *
 * Both render in the same shape as ui_draw_list — header bar at top,
 * scrollable list of rows below, footer hint at bottom — so muscle memory
 * carries over from the saves view.  No detail panel: the full info we'd
 * normally surface there (target path, server hash) doesn't gain much for
 * ROMs and would require a wider list column to stay readable. */

#define HEADER_BAR_BOTTOM 55

static void format_size(uint64_t bytes, char *out, size_t out_size) {
    if (bytes >= (1ULL << 30)) {
        double gib = (double)bytes / (double)(1ULL << 30);
        snprintf(out, out_size, "%.2f GiB", gib);
    } else if (bytes >= (1ULL << 20)) {
        double mib = (double)bytes / (double)(1ULL << 20);
        snprintf(out, out_size, "%.1f MiB", mib);
    } else if (bytes >= (1ULL << 10)) {
        double kib = (double)bytes / (double)(1ULL << 10);
        snprintf(out, out_size, "%.0f KiB", kib);
    } else {
        snprintf(out, out_size, "%llu B", (unsigned long long)bytes);
    }
}

/* Find a download entry whose rom_id matches *rom_id*.  Defined locally so
 * the UI doesn't need to mutate the list (downloads.c's own
 * downloads_find takes a non-const pointer). */
static const DownloadEntry *find_download_const(
    const DownloadList *list, const char *rom_id
) {
    if (!list || !rom_id) return NULL;
    for (int i = 0; i < list->count; i++) {
        if (strcmp(list->items[i].rom_id, rom_id) == 0)
            return &list->items[i];
    }
    return NULL;
}

void ui_draw_rom_catalog(const RomCatalog *catalog,
                         const DownloadList *downloads,
                         int selected, int scroll_offset,
                         const char *status_line) {
    if (!g_screen) return;
    if (g_xmb_open) return;

    UiColor accent = {88, 208, 255};
    UiColor white  = {240, 240, 240};
    UiColor dim    = {160, 168, 184};
    UiColor border = {44, 58, 82};
    UiColor hilite = {255, 222, 89};
    UiColor done   = {80, 220, 120};
    UiColor pause  = {255, 200, 80};
    UiColor err    = {255, 80, 80};

    ui_clear();

    /* Header bar (mirrors ui_draw_list). */
    boxRGBA(g_screen, 0, 0, SCREEN_WIDTH - 1, HEADER_BAR_BOTTOM, 14, 20, 34, 255);
    draw_text(24, 18, accent, "GameSync PS3 -- ROM Catalog");
    draw_tab_strip(APP_VIEW_ROMS);
    if (status_line && status_line[0])
        draw_text(24, 38, dim, status_line);

    /* List border. */
    rectangleRGBA(g_screen, 12, 64, SCREEN_WIDTH - 12, SCREEN_HEIGHT - 48,
                  border.r, border.g, border.b, 255);

    int total = catalog ? catalog->count : 0;
    if (total == 0) {
        draw_text(28, LIST_START_Y, dim,
                  "No PS3 ROMs in the server catalog yet.");
        if (catalog && catalog->last_error[0]) {
            draw_text(28, LIST_START_Y + 28, err, catalog->last_error);
        }
        draw_text(24, SCREEN_HEIGHT - 28, dim,
                  "Cross: download   Square: pause   Triangle: resume   "
                  "Select: next view   PS/Home: exit");
        SDL_PumpEvents();
        SDL_Flip(g_screen);
        return;
    }

    int end = scroll_offset + LIST_VISIBLE_ROWS;
    if (end > total) end = total;

    for (int i = scroll_offset; i < end; i++) {
        const RomEntry *r = &catalog->items[i];
        bool is_selected = (i == selected);
        UiColor base_color = white;
        const char *status_label = "    ";

        const DownloadEntry *dl = find_download_const(downloads, r->rom_id);
        if (dl) {
            switch (dl->status) {
                case DL_STATUS_QUEUED:
                    status_label = "Q   "; base_color = accent; break;
                case DL_STATUS_ACTIVE:
                    status_label = "ACT "; base_color = accent; break;
                case DL_STATUS_PAUSED:
                    status_label = "PAUS"; base_color = pause;  break;
                case DL_STATUS_COMPLETED:
                    status_label = "DONE"; base_color = done;   break;
                case DL_STATUS_ERROR:
                    status_label = "ERR "; base_color = err;    break;
            }
        }

        UiColor color = is_selected ? hilite : base_color;
        char marker = is_selected ? '>' : ' ';

        char size_buf[24];
        format_size(r->size, size_buf, sizeof(size_buf));

        char line[512];
        snprintf(line, sizeof(line),
                 "%c [%s] %-46.46s  %-10s",
                 marker, status_label,
                 r->name[0] ? r->name : r->filename,
                 size_buf);
        draw_text(28, LIST_START_Y + ((i - scroll_offset) * LINE_HEIGHT),
                  color, line);
    }

    /* Footer with control hints. */
    char footer[256];
    snprintf(footer, sizeof(footer),
             "Selected %d/%d   Cross: queue/start   Triangle: resume   "
             "Circle: refresh (server rescan + reload)   Select: next view",
             selected + 1, total);
    draw_text(24, SCREEN_HEIGHT - 28, dim, footer);

    SDL_PumpEvents();
    SDL_Flip(g_screen);
}

/* ETA: bytes remaining / bytes-per-second.  ``out`` is filled with a
 * human string ("12m34s", "<1s", "??:??") so callers don't have to
 * format inline.  Returns false when ETA is unknown (no speed data
 * yet) so the caller can fall back to a placeholder. */
static bool format_eta(uint64_t remaining, uint64_t bps,
                       char *out, size_t out_size) {
    if (bps == 0 || remaining == 0) {
        snprintf(out, out_size, "--");
        return false;
    }
    uint64_t secs = remaining / bps;
    if (secs >= 3600) {
        unsigned long long h = secs / 3600;
        unsigned long long m = (secs % 3600) / 60;
        snprintf(out, out_size, "%lluh%02llum", h, m);
    } else if (secs >= 60) {
        unsigned long long m = secs / 60;
        unsigned long long s = secs % 60;
        snprintf(out, out_size, "%llum%02llus", m, s);
    } else {
        snprintf(out, out_size, "%llus", (unsigned long long)secs);
    }
    return true;
}

static void format_bps(uint64_t bps, char *out, size_t out_size) {
    if (bps == 0) { snprintf(out, out_size, "--"); return; }
    if (bps >= (1ULL << 20)) {
        snprintf(out, out_size, "%.2f MiB/s", (double)bps / (double)(1ULL << 20));
    } else if (bps >= (1ULL << 10)) {
        snprintf(out, out_size, "%.1f KiB/s", (double)bps / (double)(1ULL << 10));
    } else {
        snprintf(out, out_size, "%llu B/s", (unsigned long long)bps);
    }
}

void ui_draw_downloads(const DownloadList *downloads,
                       int selected, int scroll_offset,
                       const char *status_line,
                       bool active_in_progress,
                       uint64_t active_downloaded,
                       uint64_t active_total,
                       uint64_t active_bps) {
    if (!g_screen) return;
    if (g_xmb_open) return;

    UiColor accent = {88, 208, 255};
    UiColor white  = {240, 240, 240};
    UiColor dim    = {160, 168, 184};
    UiColor border = {44, 58, 82};
    UiColor hilite = {255, 222, 89};
    UiColor done   = {80, 220, 120};
    UiColor pause  = {255, 200, 80};
    UiColor err    = {255, 80, 80};

    ui_clear();

    boxRGBA(g_screen, 0, 0, SCREEN_WIDTH - 1, HEADER_BAR_BOTTOM, 14, 20, 34, 255);
    draw_text(24, 18, accent, "GameSync PS3 -- Downloads");
    draw_tab_strip(APP_VIEW_DOWNLOADS);
    if (status_line && status_line[0])
        draw_text(440, 36, dim, status_line);

    rectangleRGBA(g_screen, 12, 64, SCREEN_WIDTH - 12, SCREEN_HEIGHT - 48,
                  border.r, border.g, border.b, 255);

    int total = downloads ? downloads->count : 0;

    /* When a download is in flight, render a fat info panel at the top
     * of the list area so the user gets a clear at-a-glance status:
     * file name + index, percent, bytes, speed, ETA, and the pause
     * hint.  This is the panel the user said felt missing — without it
     * the bundle download looked frozen because the list only showed a
     * single "Bundle 1/2: foo.pkg" line. */
    int list_start_y = LIST_START_Y;
    if (active_in_progress) {
        const DownloadEntry *active = NULL;
        for (int i = 0; i < total; i++) {
            if (downloads->items[i].status == DL_STATUS_ACTIVE) {
                active = &downloads->items[i];
                break;
            }
        }

        const char *display_name =
            active && active->name[0] ? active->name :
            (active && active->filename[0] ? active->filename : "(unknown)");
        const char *current_file =
            active && active->target_path[0] ? active->target_path : "";
        /* Show only the basename of target_path so the panel doesn't
         * wrap on a 1080p screen. */
        const char *base_slash = current_file ? strrchr(current_file, '/') : NULL;
        const char *current_basename = base_slash ? base_slash + 1 : current_file;

        uint64_t off = active_downloaded;
        uint64_t tot = (active_total > 0) ? active_total
                       : (active ? active->total : 0);
        int pct = 0;
        if (tot > 0) {
            pct = (int)((off * 100ULL) / tot);
            if (pct > 100) pct = 100;
        }

        char off_buf[24], tot_buf[24], bps_buf[24], eta_buf[24];
        format_size(off, off_buf, sizeof(off_buf));
        format_size(tot, tot_buf, sizeof(tot_buf));
        format_bps(active_bps, bps_buf, sizeof(bps_buf));
        uint64_t remaining = (tot > off) ? (tot - off) : 0;
        format_eta(remaining, active_bps, eta_buf, sizeof(eta_buf));

        /* Background for the panel so it visually separates from the
         * queue list below. */
        boxRGBA(g_screen, 18, 70, SCREEN_WIDTH - 18, 70 + 130,
                18, 26, 42, 255);
        rectangleRGBA(g_screen, 18, 70, SCREEN_WIDTH - 18, 70 + 130,
                      border.r, border.g, border.b, 255);

        int y = 78;
        draw_textf(28, y, accent, "Now downloading: %.80s", display_name);
        y += 22;
        if (active && active->is_bundle && active->bundle_count > 0) {
            draw_textf(28, y, white,
                       "Bundle file %d / %d:  %.80s",
                       active->bundle_index + 1,
                       active->bundle_count,
                       current_basename);
        } else {
            draw_textf(28, y, white, "File: %.80s", current_basename);
        }
        y += 22;
        draw_textf(28, y, white,
                   "%3d%%   %s / %s   Speed: %s   ETA: %s",
                   pct, off_buf, tot_buf, bps_buf, eta_buf);
        y += 22;
        draw_text(28, y, dim,
                  "Square: pause (saves progress)   "
                  "Circle: cancel (after pause)");

        list_start_y = 70 + 130 + 12;  /* shift list down past the panel */
    }

    if (total == 0) {
        draw_text(28, list_start_y, dim,
                  "No downloads queued.  Switch to the ROM Catalog view "
                  "and press Cross on a title.");
        draw_text(24, SCREEN_HEIGHT - 28, dim,
                  "Select: next view   PS/Home: exit");
        SDL_PumpEvents();
        SDL_Flip(g_screen);
        return;
    }

    /* Compute how many rows fit when the panel is visible. */
    int rows_avail = (SCREEN_HEIGHT - 48 - list_start_y) / LINE_HEIGHT;
    if (rows_avail > LIST_VISIBLE_ROWS) rows_avail = LIST_VISIBLE_ROWS;
    if (rows_avail < 1) rows_avail = 1;
    int end = scroll_offset + rows_avail;
    if (end > total) end = total;

    for (int i = scroll_offset; i < end; i++) {
        const DownloadEntry *e = &downloads->items[i];
        bool is_selected = (i == selected);

        UiColor base_color = white;
        const char *status_label = "    ";
        switch (e->status) {
            case DL_STATUS_QUEUED:
                status_label = "Q   "; base_color = accent; break;
            case DL_STATUS_ACTIVE:
                status_label = "ACT "; base_color = accent; break;
            case DL_STATUS_PAUSED:
                status_label = "PAUS"; base_color = pause;  break;
            case DL_STATUS_COMPLETED:
                status_label = "DONE"; base_color = done;   break;
            case DL_STATUS_ERROR:
                status_label = "ERR "; base_color = err;    break;
        }

        UiColor color = is_selected ? hilite : base_color;
        char marker = is_selected ? '>' : ' ';

        /* Active row: live offset + bundle index in the row text. */
        uint64_t off  = e->offset;
        uint64_t tot  = e->total;
        if (e->status == DL_STATUS_ACTIVE && active_in_progress) {
            off = active_downloaded;
            if (active_total > 0) tot = active_total;
        }

        char off_buf[24], tot_buf[24];
        format_size(off, off_buf, sizeof(off_buf));
        format_size(tot, tot_buf, sizeof(tot_buf));

        int pct = 0;
        if (tot > 0) {
            pct = (int)((off * 100ULL) / tot);
            if (pct > 100) pct = 100;
        }

        char bundle_tag[16] = {0};
        if (e->is_bundle && e->bundle_count > 0) {
            snprintf(bundle_tag, sizeof(bundle_tag),
                     " [%d/%d]", e->bundle_index + 1, e->bundle_count);
        }

        char line[512];
        snprintf(line, sizeof(line),
                 "%c [%s] %-36.36s%s  %3d%%  %10s / %-10s",
                 marker, status_label,
                 e->name[0] ? e->name : e->filename,
                 bundle_tag,
                 pct, off_buf, tot_buf);
        draw_text(28, list_start_y + ((i - scroll_offset) * LINE_HEIGHT),
                  color, line);
    }

    char footer[320];
    snprintf(footer, sizeof(footer),
             "Selected %d/%d   Cross: start/resume   Square: pause   "
             "Circle: cancel   Triangle: clear completed   Select: next view",
             selected + 1, total);
    draw_text(24, SCREEN_HEIGHT - 28, dim, footer);

    SDL_PumpEvents();
    SDL_Flip(g_screen);
}
