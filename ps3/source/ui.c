#include "ui.h"
#include "sync.h"

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
    draw_text(24, y, (UiColor){88, 208, 255}, title ? title : "Save Sync PS3");
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
    draw_textf(24, y, accent, "Save Sync PS3 v%s", APP_VERSION);
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
        "Up/Dn: nav   X: sync   Sq: upload   Tri: download   R1: compare   O: rescan   L1: filter[%s]   L2/R2: user   Start: exit",
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

            boxRGBA(g_screen, 1020, 88, SCREEN_WIDTH - 24, 380, 12, 18, 30, 255);
            rectangleRGBA(g_screen, 1020, 88, SCREEN_WIDTH - 24, 380, border.r, border.g, border.b, 255);
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
            draw_text(1036, 296, dim, "Hash:");
            draw_text(1036, 318, white, hash_hex);
        }
    }

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
                paddata.BTN_TRIANGLE || paddata.BTN_START || paddata.BTN_SELECT ||
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
    draw_text(24, 18, (UiColor){88, 208, 255}, "Save Sync PS3");
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

    drain_buttons();

    ui_clear();
    boxRGBA(g_screen, 0, 0, SCREEN_WIDTH - 1, 55, 14, 20, 34, 255);
    draw_text(24, 18, (UiColor){88, 208, 255}, "Save Sync PS3");

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

    draw_text(24, SCREEN_HEIGHT - 28, (UiColor){160, 168, 184}, "Cross: continue");

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
            if (paddata.BTN_START) { ui_notify_exit(); return; }
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
    draw_text(24, 18, (UiColor){88, 208, 255}, "Save Sync PS3 -- Confirm");

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
        draw_text(24, SCREEN_HEIGHT - 28, dim, "Cross: OK");
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
                if (paddata2.BTN_START) { ui_notify_exit(); return false; }
                if (!prev2 && (paddata2.BTN_CROSS || paddata2.BTN_CIRCLE)) return false;
                prev2 = paddata2.BTN_CROSS | paddata2.BTN_CIRCLE;
            }
            usleep(50000);
        }
    }

    draw_text(24, SCREEN_HEIGHT - 28, dim, "Cross: Confirm   Circle: Cancel");

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
            if (paddata.BTN_START) { ui_notify_exit(); return false; }
            if (!prev_cross  && paddata.BTN_CROSS)  return true;
            if (!prev_circle && paddata.BTN_CIRCLE) return false;
            prev_cross  = paddata.BTN_CROSS;
            prev_circle = paddata.BTN_CIRCLE;
        }
        usleep(50000);
    }
}
