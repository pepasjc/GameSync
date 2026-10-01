/*
 * Vita Save Sync - UI
 *
 * The four views (Saves, ROM Catalog, Downloads, Settings) and the modal
 * cards,
 * drawn with the vita2d kit in gui.c.  Layout on the 960x544 screen:
 *
 *   header   GameSync . <view>                 vX.Y.Z  (o) Online
 *   tabs     L [Saves | ROM Catalog | Downloads | Settings] R
 *   content  list panel (left)                 detail panel (right)
 *   footer   button hints
 *
 * Each view call draws one full frame.  Dialogs remember which view was
 * drawn last and repaint it, dimmed, as their backdrop.
 */

#include <stdarg.h>
#include <stdio.h>
#include <string.h>

#include <psp2/ctrl.h>
#include <psp2/kernel/threadmgr.h>

#include "config.h"
#include "gui.h"
#include "ui.h"

/* ------------------------------------------------------------------ */
/* Layout                                                              */
/* ------------------------------------------------------------------ */

#define TAB_Y      58
#define TAB_H      36
#define LIST_X     16
#define LIST_Y     104
#define LIST_W     580
#define LIST_H     392
#define LIST_HEAD  44                       /* panel header row */
#define ROW_H      31
#define ROWS_Y     (LIST_Y + LIST_HEAD)
#define ROWS_H     (UI_LIST_ROWS * ROW_H)
#define DET_X      608
#define DET_Y      LIST_Y
#define DET_W      336
#define DET_H      LIST_H
#define DET_PAD    18
#define KV_KEY_W   104

#define KIB (1024ULL)
#define MIB (1024ULL * 1024ULL)
#define GIB (1024ULL * 1024ULL * 1024ULL)

/* ------------------------------------------------------------------ */
/* State                                                               */
/* ------------------------------------------------------------------ */

typedef enum {
    BACK_SPLASH = 0,
    BACK_SAVES,
    BACK_ROMS,
    BACK_DOWNLOADS,
    BACK_SETTINGS,
} Backdrop;

/* Smoothed list cursor per view */
typedef struct {
    float sel;
    float scroll;
    bool  primed;
} ListAnim;

static int      g_online = -1;
static Backdrop g_back   = BACK_SPLASH;

/* Arguments of the last view drawn, so dialogs can repaint it */
static struct {
    const SyncState *state;
    int selected, scroll;
} g_saves_args;

static struct {
    const RomCatalog   *catalog;
    const DownloadList *downloads;
    const char *system;
    int selected, scroll;
    char status[160];
} g_roms_args;

static struct {
    const DownloadList *downloads;
    int selected, scroll;
    char status[160];
    bool active;
    uint64_t done, total, bps;
} g_dl_args;

static struct {
    const SyncState *state;
    int selected, scroll;
    char cache[160];
} g_set_args;

static ListAnim g_anim[APP_VIEW_COUNT];

static char     g_toast[160];
static UiTone   g_toast_tone;
static uint32_t g_toast_until;

/* Per-save sync marker, filled lazily from the state file */
typedef enum {
    MARK_UNKNOWN = 0,
    MARK_NEVER,
    MARK_TRACKED,    /* synced before; local hash not computed yet */
    MARK_SYNCED,     /* local hash matches the last synced hash */
    MARK_CHANGED,    /* local save changed since the last sync */
} SyncMark;
static uint8_t g_marks[MAX_TITLES];

/* ------------------------------------------------------------------ */
/* Helpers                                                             */
/* ------------------------------------------------------------------ */

static uint32_t tone_hex(UiTone tone) {
    switch (tone) {
        case UI_TONE_OK:   return HEX_OK;
        case UI_TONE_WARN: return HEX_WARN;
        case UI_TONE_INFO: return HEX_INFO;
        case UI_TONE_ERR:  return HEX_ERR;
        default:           return HEX_ACCENT;
    }
}

static void format_size(uint64_t bytes, char *out, size_t out_size) {
    if (bytes >= GIB)
        snprintf(out, out_size, "%.2f GB", (double)bytes / (double)GIB);
    else if (bytes >= MIB)
        snprintf(out, out_size, "%.1f MB", (double)bytes / (double)MIB);
    else if (bytes >= KIB)
        snprintf(out, out_size, "%.0f KB", (double)bytes / (double)KIB);
    else
        snprintf(out, out_size, "%llu B", (unsigned long long)bytes);
}

static void format_bps(uint64_t bps, char *out, size_t out_size) {
    if (bps == 0) { snprintf(out, out_size, "--"); return; }
    if (bps >= MIB)
        snprintf(out, out_size, "%.1f MB/s", (double)bps / (double)MIB);
    else if (bps >= KIB)
        snprintf(out, out_size, "%.0f KB/s", (double)bps / (double)KIB);
    else
        snprintf(out, out_size, "%llu B/s", (unsigned long long)bps);
}

static void format_eta(uint64_t remaining, uint64_t bps, char *out, size_t out_size) {
    if (bps == 0 || remaining == 0) { snprintf(out, out_size, "--"); return; }
    uint64_t s = remaining / bps;
    if (s >= 3600)
        snprintf(out, out_size, "%lluh %02llum",
                 (unsigned long long)(s / 3600), (unsigned long long)((s % 3600) / 60));
    else if (s >= 60)
        snprintf(out, out_size, "%llum %02llus",
                 (unsigned long long)(s / 60), (unsigned long long)(s % 60));
    else
        snprintf(out, out_size, "%llus", (unsigned long long)s);
}

/* ISO 8601 "YYYY-MM-DDTHH:MM:SS..." -> "YYYY-MM-DD HH:MM" */
static void format_date(const char *iso, char *out, size_t out_size) {
    if (!iso || !iso[0])
        snprintf(out, out_size, "-");
    else if (strlen(iso) >= 16 && iso[10] == 'T')
        snprintf(out, out_size, "%.10s %.5s", iso, iso + 11);
    else
        snprintf(out, out_size, "%.19s", iso);
}

static int percent(uint64_t done, uint64_t total) {
    if (total == 0) return 0;
    uint64_t p = (done * 100ULL) / total;
    return p > 100 ? 100 : (int)p;
}

static const char *title_display(const TitleInfo *t) {
    return (t->name[0] && strcmp(t->name, t->game_id) != 0) ? t->name : t->game_id;
}

static const char *platform_label(const TitleInfo *t) {
    if (t->platform == PLATFORM_VITA) return "VITA";
    return t->is_psx ? "PS1" : "PSP";
}

static uint32_t platform_hex(const TitleInfo *t) {
    if (t->platform == PLATFORM_VITA) return HEX_VITA;
    return t->is_psx ? HEX_PS1 : HEX_PSP;
}

static uint32_t system_hex(const char *system) {
    if (system && strcmp(system, "PS1") == 0) return HEX_PS1;
    return HEX_PSP;
}

static const char *dl_status_label(DownloadStatus s) {
    switch (s) {
        case DL_STATUS_QUEUED:    return "Queued";
        case DL_STATUS_ACTIVE:    return "Downloading";
        case DL_STATUS_PAUSED:    return "Paused";
        case DL_STATUS_COMPLETED: return "Installed";
        case DL_STATUS_ERROR:     return "Failed";
    }
    return "";
}

static uint32_t dl_status_hex(DownloadStatus s) {
    switch (s) {
        case DL_STATUS_QUEUED:    return HEX_ACCENT;
        case DL_STATUS_ACTIVE:    return HEX_INFO;
        case DL_STATUS_PAUSED:    return HEX_WARN;
        case DL_STATUS_COMPLETED: return HEX_OK;
        case DL_STATUS_ERROR:     return HEX_ERR;
    }
    return HEX_MUTED;
}

static const DownloadEntry *find_dl(const DownloadList *list, const char *rom_id) {
    if (!list || !rom_id) return NULL;
    for (int i = 0; i < list->count; i++)
        if (strcmp(list->items[i].rom_id, rom_id) == 0) return &list->items[i];
    return NULL;
}

static const DownloadEntry *active_dl(const DownloadList *list) {
    if (!list) return NULL;
    for (int i = 0; i < list->count; i++)
        if (list->items[i].status == DL_STATUS_ACTIVE) return &list->items[i];
    return NULL;
}

static const char *basename_of(const char *path) {
    const char *s = strrchr(path, '/');
    return s ? s + 1 : path;
}

/* Wait for every button to be released */
static void drain_buttons(void) {
    SceCtrlData pad;
    do {
        sceCtrlReadBufferPositive2(0, &pad, 1);
        sceKernelDelayThread(16000);
    } while (pad.buttons != 0);
}

/* Newly pressed buttons since the last call (dialog loops) */
static uint32_t pressed(uint32_t *prev) {
    SceCtrlData pad;
    sceCtrlReadBufferPositive2(0, &pad, 1);
    uint32_t just = pad.buttons & ~*prev;
    *prev = pad.buttons;
    return just;
}

static SyncMark sync_mark(const TitleInfo *t, int index) {
    if (t->server_only) return MARK_NEVER;
    if (index < 0 || index >= MAX_TITLES) return MARK_UNKNOWN;
    if (g_marks[index] == MARK_UNKNOWN) {
        /* Compare the last synced hash with the local one: computed this
         * session, or from the hash cache (keyed on file count + size, the
         * same shortcut sync itself takes) - never hash files just to draw. */
        char last[65], hex[65];
        bool have_local = false;
        if (t->hash_calculated) {
            for (int i = 0; i < 32; i++)
                snprintf(&hex[i * 2], 3, "%02x", t->hash[i]);
            have_local = true;
        } else {
            have_local = config_get_cached_hash(t->game_id, t->file_count, t->total_size, hex);
        }
        if (!config_get_last_hash(t->game_id, last))
            g_marks[index] = MARK_NEVER;
        else if (!have_local)
            g_marks[index] = MARK_TRACKED;
        else
            g_marks[index] = (strcasecmp(hex, last) == 0) ? MARK_SYNCED : MARK_CHANGED;
    }
    return (SyncMark)g_marks[index];
}

void ui_invalidate_sync_state(void) {
    memset(g_marks, 0, sizeof(g_marks));
}

/* ------------------------------------------------------------------ */
/* Shared chrome                                                       */
/* ------------------------------------------------------------------ */

static const char *const VIEW_NAMES[APP_VIEW_COUNT] = {
    "Saves", "ROM Catalog", "Downloads", "Settings",
};

static void draw_view_tabs(AppView view, const char *info) {
    /* L [tab | tab | ...] R: the shoulder buttons step through the tabs */
    float cy = TAB_Y + TAB_H / 2.0f;
    float x = LIST_X;
    x += gui_button(x, cy, "L") + 8;
    float right = gui_tabs(x, TAB_Y, TAB_H, GUI_S_SMALL, VIEW_NAMES, APP_VIEW_COUNT,
                           (int)view);
    right += 8;
    right += gui_button(right, cy, "R");
    if (info && info[0])
        gui_text_mid(GUI_W - 16, TAB_Y, TAB_H, GUI_S_SMALL, gui_rgb(HEX_DIM), GUI_RIGHT,
                     GUI_W - 16 - right - 24, info);
}

/* Ease the cursor animation of a view and return it */
static ListAnim *list_anim(AppView view, int selected, int scroll) {
    ListAnim *a = &g_anim[view];
    if (!a->primed) {
        a->sel = (float)selected;
        a->scroll = (float)scroll;
        a->primed = true;
    }
    gui_ease(&a->sel, (float)selected, 0.35f);
    gui_ease(&a->scroll, (float)scroll, 0.35f);
    return a;
}

typedef void (*RowFn)(int index, float y, bool selected, void *ctx);

/* Rows of a list panel with the smooth selection bar and scrollbar */
static void draw_rows(AppView view, int total, int selected, int scroll, RowFn row, void *ctx) {
    ListAnim *a = list_anim(view, selected, scroll);
    gui_clip(LIST_X, ROWS_Y, LIST_W, ROWS_H);
    if (total > 0) {
        float bar_y = ROWS_Y + (a->sel - a->scroll) * ROW_H;
        gui_rrect(LIST_X + 8, bar_y + 1, LIST_W - 26, ROW_H - 2, 7, gui_rgb(HEX_ACCENT));
    }
    int first = (int)a->scroll;
    if (first < 0) first = 0;
    for (int i = first; i < total && i <= first + UI_LIST_ROWS; i++) {
        float y = ROWS_Y + (i - a->scroll) * ROW_H;
        row(i, y, i == selected, ctx);
    }
    gui_unclip();
    gui_scrollbar(LIST_X + LIST_W - 12, ROWS_Y + 2, ROWS_H - 4, UI_LIST_ROWS, total, a->scroll);
}

/* Empty-list placeholder centred in the rows area */
static void draw_empty(const char *title, const char *hint, uint32_t tone) {
    float cy = ROWS_Y + ROWS_H / 2.0f - 30;
    gui_circle(LIST_X + LIST_W / 2.0f, cy - 22, 20, gui_rgb(gui_mix(HEX_PANEL, tone, 0.25f)));
    gui_circle(LIST_X + LIST_W / 2.0f, cy - 22, 6, gui_rgb(tone));
    gui_text(LIST_X + LIST_W / 2.0f, cy + 10, GUI_S_BODY, gui_rgb(HEX_TEXT), GUI_CENTER, title);
    if (hint && hint[0]) {
        char lines[3][GUI_WRAP_LINE];
        int n = gui_wrap(hint, GUI_S_SMALL, LIST_W - 80, lines, 3);
        for (int i = 0; i < n; i++)
            gui_text(LIST_X + LIST_W / 2.0f, cy + 40 + i * gui_line_h(GUI_S_SMALL) * 1.15f,
                     GUI_S_SMALL, gui_rgb(HEX_DIM), GUI_CENTER, lines[i]);
    }
}

/* Key / value line in a detail panel; returns the next y */
static float kv(float y, const char *key, const char *value) {
    gui_text(DET_X + DET_PAD, y, GUI_S_SMALL, gui_rgb(HEX_DIM), GUI_LEFT, key);
    gui_text_fit(DET_X + DET_PAD + KV_KEY_W, y, GUI_S_SMALL, gui_rgb(HEX_TEXT), GUI_LEFT,
                 DET_W - DET_PAD * 2 - KV_KEY_W, value);
    return y + gui_line_h(GUI_S_SMALL) * 1.3f;
}

/* Key / value with the value wrapped over up to `lines` lines */
static float kv_wrap(float y, const char *key, const char *value, int lines) {
    gui_text(DET_X + DET_PAD, y, GUI_S_SMALL, gui_rgb(HEX_DIM), GUI_LEFT, key);
    int n = gui_text_wrap(DET_X + DET_PAD + KV_KEY_W, y, GUI_S_SMALL, gui_rgb(HEX_TEXT),
                          DET_W - DET_PAD * 2 - KV_KEY_W, lines, value);
    if (n < 1) n = 1;
    return y + gui_line_h(GUI_S_SMALL) * (1.12f * (n - 1) + 1.3f);
}

/* Title of the detail panel (wrapped) and returns the next y */
static float detail_title(const char *name) {
    float y = DET_Y + 16;
    int n = gui_text_wrap(DET_X + DET_PAD, y, GUI_S_TITLE, gui_rgb(HEX_TEXT),
                          DET_W - DET_PAD * 2, 3, name);
    if (n < 1) n = 1;
    return y + gui_line_h(GUI_S_TITLE) * 1.12f * n + 6;
}

/* "Press <button> to <action>" line */
static void press_hint(float y, const char *button, const char *action) {
    float x = DET_X + DET_PAD;
    float lh = gui_line_h(GUI_S_SMALL);
    x += gui_text(x, y, GUI_S_SMALL, gui_rgb(HEX_DIM), GUI_LEFT, "Press ") + 2;
    x += gui_button(x, y + lh / 2, button) + 6;
    gui_text_fit(x, y, GUI_S_SMALL, gui_rgb(HEX_DIM), GUI_LEFT, DET_X + DET_W - DET_PAD - x,
                 action);
}

static void draw_toast(void) {
    if (!g_toast[0] || gui_ticks() > g_toast_until) return;
    float w = gui_text_w(GUI_S_SMALL, g_toast) + 60;
    if (w > LIST_W - 40) w = LIST_W - 40;
    float x = LIST_X + (LIST_W - w) / 2, y = LIST_Y + LIST_H - 54;
    uint32_t hex = tone_hex(g_toast_tone);
    gui_rrect(x + 2, y + 4, w, 40, 12, gui_rgba(0x000000, 0x80));
    gui_rrect(x - 1.5f, y - 1.5f, w + 3, 43, 12, gui_rgb(gui_mix(HEX_LINE, hex, 0.6f)));
    gui_rrect(x, y, w, 40, 11, gui_rgb(HEX_PANEL_HI));
    gui_circle(x + 22, y + 20, 5, gui_rgb(hex));
    gui_text_mid(x + 38, y, 40, GUI_S_SMALL, gui_rgb(HEX_TEXT), GUI_LEFT, w - 50, g_toast);
}

/* ------------------------------------------------------------------ */
/* Saves view                                                          */
/* ------------------------------------------------------------------ */

static void saves_row(int i, float y, bool sel, void *ctx) {
    const SyncState *state = (const SyncState *)ctx;
    const TitleInfo *t = &state->titles[i];
    float x = LIST_X + 18;
    float ph = 20, py = y + (ROW_H - ph) / 2;

    /* Platform pill in a fixed slot so names line up */
    uint32_t phex = platform_hex(t);
    float pw = gui_pill_w(ph, GUI_S_TINY, platform_label(t));
    float slot = 56;
    gui_pill(x + (slot - pw) / 2, py, ph, GUI_S_TINY,
             gui_rgb(sel ? gui_mix(phex, HEX_INK, 0.55f) : gui_mix(HEX_PANEL, phex, 0.30f)),
             gui_rgb(sel ? 0xFFFFFF : gui_mix(phex, 0xFFFFFF, 0.35f)), platform_label(t));
    x += slot + 12;

    /* Sync marker on the right */
    const char *label = NULL;
    uint32_t hex = HEX_MUTED;
    switch (sync_mark(t, i)) {
        case MARK_SYNCED:  label = "Synced";       hex = HEX_OK;   break;
        case MARK_CHANGED: label = "Changed";      hex = HEX_WARN; break;
        case MARK_TRACKED: label = "Synced before"; hex = HEX_DIM; break;
        default:           label = t->server_only ? "On server" : "Local";
                           hex = t->server_only ? HEX_INFO : HEX_MUTED;
                           break;
    }
    float right = LIST_X + LIST_W - 30;
    float lw = gui_text_w(GUI_S_TINY, label);
    unsigned int fg = gui_rgb(sel ? HEX_INK : hex);
    gui_text_mid(right, y, ROW_H, GUI_S_TINY, fg, GUI_RIGHT, 0, label);
    gui_circle(right - lw - 10, y + ROW_H / 2.0f, 4, fg);

    gui_text_mid(x, y, ROW_H, GUI_S_BODY, gui_rgb(sel ? HEX_INK : HEX_TEXT), GUI_LEFT,
                 right - lw - 28 - x, title_display(t));
}

static void saves_detail(const SyncState *state, int selected) {
    gui_panel(DET_X, DET_Y, DET_W, DET_H);
    if (state->num_titles <= 0 || selected < 0 || selected >= state->num_titles) {
        gui_text(DET_X + DET_PAD, DET_Y + 20, GUI_S_BODY, gui_rgb(HEX_DIM), GUI_LEFT,
                 "No save selected");
        return;
    }
    const TitleInfo *t = &state->titles[selected];
    float y = detail_title(title_display(t));

    float x = DET_X + DET_PAD;
    uint32_t phex = platform_hex(t);
    x += gui_pill(x, y, 24, GUI_S_TINY, gui_rgb(gui_mix(HEX_PANEL, phex, 0.35f)),
                  gui_rgb(gui_mix(phex, 0xFFFFFF, 0.4f)), platform_label(t)) + 8;
    switch (sync_mark(t, selected)) {
        case MARK_SYNCED:  gui_pill_outline(x, y, 24, GUI_S_TINY, HEX_OK, HEX_PANEL, "In sync"); break;
        case MARK_CHANGED: gui_pill_outline(x, y, 24, GUI_S_TINY, HEX_WARN, HEX_PANEL, "Changed locally"); break;
        case MARK_TRACKED: gui_pill_outline(x, y, 24, GUI_S_TINY, HEX_DIM, HEX_PANEL, "Synced before"); break;
        default:
            if (t->server_only) gui_pill_outline(x, y, 24, GUI_S_TINY, HEX_INFO, HEX_PANEL, "Server only");
            else gui_pill_outline(x, y, 24, GUI_S_TINY, HEX_MUTED, HEX_PANEL, "Never synced");
            break;
    }
    y += 38;

    char buf[64];
    y = kv(y, "Game ID", t->game_id);
    y = kv(y, "Platform", t->platform != PLATFORM_PSP_EMU ? "PS Vita" :
                          t->is_psx ? "PS1 (PSone Classic)" : "PSP (emulated)");
    if (t->server_only) {
        y = kv(y, "Local", "Not on this Vita yet");
    } else {
        format_size(t->total_size, buf, sizeof(buf));
        char files[96];
        snprintf(files, sizeof(files), "%s in %d file%s", buf, t->file_count,
                 t->file_count == 1 ? "" : "s");
        y = kv(y, "Size", files);
        y = kv_wrap(y, "Location", t->save_dir, 2);
    }

    /* Bottom: what Cross does, and the server */
    float by = DET_Y + DET_H - 90;
    gui_rect(DET_X + DET_PAD, by - 10, DET_W - DET_PAD * 2, 1, gui_rgb(HEX_LINE));
    press_hint(by, "CROSS", t->server_only ? "to download it" : "to sync with the server");
    char srv[300];
    snprintf(srv, sizeof(srv), "Server: %s", state->server_url);
    gui_banner(DET_X + 12, DET_Y + DET_H - 52, DET_W - 24, 40,
               g_online > 0 ? HEX_ACCENT : HEX_ERR, srv);
}

static void draw_saves_body(const SyncState *state, int selected, int scroll) {
    gui_header("Saves", g_online);

    const char *scope =
        (state->scan_vita_saves && state->scan_psp_emu_saves) ? "Vita + PSP saves" :
        state->scan_vita_saves ? "Vita saves" : "PSP saves";
    draw_view_tabs(APP_VIEW_SAVES, scope);

    gui_panel(LIST_X, LIST_Y, LIST_W, LIST_H);
    gui_text_mid(LIST_X + 18, LIST_Y, LIST_HEAD, GUI_S_SMALL, gui_rgb(HEX_DIM), GUI_LEFT, 0,
                 "Game");
    char count[48];
    if (state->num_titles > 0)
        snprintf(count, sizeof(count), "%d / %d", selected + 1, state->num_titles);
    else
        snprintf(count, sizeof(count), "0 saves");
    gui_text_mid(LIST_X + LIST_W - 18, LIST_Y, LIST_HEAD, GUI_S_SMALL, gui_rgb(HEX_DIM),
                 GUI_RIGHT, 0, count);
    gui_rect(LIST_X + 12, LIST_Y + LIST_HEAD - 3, LIST_W - 24, 1, gui_rgb(HEX_LINE));

    if (state->num_titles == 0)
        draw_empty("No saves found",
                   "Nothing in ux0:user/00/savedata or the PSP save folder, and nothing "
                   "on the server. Press R for the ROM Catalog.", HEX_MUTED);
    else
        draw_rows(APP_VIEW_SAVES, state->num_titles, selected, scroll, saves_row,
                  (void *)state);

    saves_detail(state, selected);
    draw_toast();
}

static void saves_footer(void) {
    static const GuiHint hints[] = {
        { "CROSS", "Sync" }, { "SQUARE", "Sync all" }, { "TRIANGLE", "Details" },
        { "L", "" }, { "R", "Tabs" }, { "LR", "Page" }, { "START", "Exit" },
    };
    gui_footer(hints, (int)(sizeof(hints) / sizeof(hints[0])));
}

void ui_draw_list(const SyncState *state, int selected, int scroll) {
    g_back = BACK_SAVES;
    g_saves_args.state = state;
    g_saves_args.selected = selected;
    g_saves_args.scroll = scroll;

    gui_begin(true);
    draw_saves_body(state, selected, scroll);
    saves_footer();
    gui_end();
}

/* ------------------------------------------------------------------ */
/* ROM Catalog view                                                    */
/* ------------------------------------------------------------------ */

typedef struct {
    const RomCatalog *catalog;
    const DownloadList *downloads;
} RomsCtx;

static void roms_row(int i, float y, bool sel, void *vctx) {
    const RomsCtx *ctx = (const RomsCtx *)vctx;
    const RomEntry *r = &ctx->catalog->items[i];
    float x = LIST_X + 22;
    float right = LIST_X + LIST_W - 30;

    char size[24];
    format_size(r->size, size, sizeof(size));
    float sw = gui_text_w(GUI_S_SMALL, size);
    gui_text_mid(right, y, ROW_H, GUI_S_SMALL, gui_rgb(sel ? HEX_INK : HEX_DIM), GUI_RIGHT, 0,
                 size);
    right -= sw + 12;

    /* Download state / unsupported tag */
    const DownloadEntry *dl = find_dl(ctx->downloads, r->rom_id);
    const char *tag = NULL;
    uint32_t hex = HEX_MUTED;
    if (dl) { tag = dl_status_label(dl->status); hex = dl_status_hex(dl->status); }
    else if (roms_entry_unsupported(r)) { tag = "N/A"; hex = HEX_MUTED; }
    if (tag) {
        float ph = 20, pw = gui_pill_w(ph, GUI_S_TINY, tag);
        right -= pw;
        gui_pill(right, y + (ROW_H - ph) / 2, ph, GUI_S_TINY,
                 gui_rgb(sel ? HEX_INK : gui_mix(HEX_PANEL, hex, 0.30f)),
                 gui_rgb(sel ? gui_mix(hex, 0xFFFFFF, 0.25f) : hex), tag);
        right -= 10;
    }

    /* Leading dot, then the name (plus disc count for multi-disc sets) */
    gui_circle(x - 6, y + ROW_H / 2.0f, 3, gui_rgb(sel ? HEX_INK : HEX_MUTED));
    const char *name = r->name[0] ? r->name : r->filename;
    char label[MAX_TITLE_LEN + 16];
    if (r->disc_total > 1) {
        snprintf(label, sizeof(label), "%s (%d discs)", name, r->disc_total);
        name = label;
    }
    gui_text_mid(x + 6, y, ROW_H, GUI_S_BODY,
                 gui_rgb(sel ? HEX_INK : (roms_entry_unsupported(r) ? HEX_DIM : HEX_TEXT)),
                 GUI_LEFT, right - x - 10, name);
}

static void roms_detail(const RomCatalog *catalog, const DownloadList *downloads,
                        int selected, const char *status) {
    gui_panel(DET_X, DET_Y, DET_W, DET_H);
    if (!catalog || catalog->count <= 0 || selected < 0 || selected >= catalog->count) {
        gui_text(DET_X + DET_PAD, DET_Y + 20, GUI_S_BODY, gui_rgb(HEX_DIM), GUI_LEFT,
                 "No game selected");
    } else {
        const RomEntry *r = &catalog->items[selected];
        float y = detail_title(r->name[0] ? r->name : r->filename);

        float x = DET_X + DET_PAD;
        uint32_t shex = system_hex(r->system);
        char size[24];
        format_size(r->size, size, sizeof(size));
        x += gui_pill(x, y, 24, GUI_S_TINY, gui_rgb(gui_mix(HEX_PANEL, shex, 0.35f)),
                      gui_rgb(gui_mix(shex, 0xFFFFFF, 0.4f)), r->system) + 8;
        x += gui_pill(x, y, 24, GUI_S_TINY, gui_rgb(HEX_BG2), gui_rgb(HEX_DIM), size) + 8;
        if (r->disc_total > 1) {
            char discs[24];
            snprintf(discs, sizeof(discs), "%d discs", r->disc_total);
            gui_pill(x, y, 24, GUI_S_TINY, gui_rgb(HEX_BG2), gui_rgb(HEX_DIM), discs);
        }
        y += 38;

        y = kv_wrap(y, "File", r->filename, 2);
        const char *fmt = roms_preferred_extract_format(r);
        const char *as =
            roms_entry_unsupported(r)  ? "Not installable (loose files)" :
            strcmp(fmt, "eboot") == 0  ? "EBOOT.PBP (server converts)" :
            strcmp(fmt, "cso") == 0    ? "CSO (server converts the CHD)" :
                                         "As-is (no conversion)";
        y = kv_wrap(y, "Installs as", as, 2);
        if (!roms_entry_unsupported(r)) {
            char target[DOWNLOAD_PATH_LEN];
            if (roms_resolve_target_path(r, target, sizeof(target)))
                y = kv_wrap(y, "Goes to", target, 3);
        }
        const DownloadEntry *dl = find_dl(downloads, r->rom_id);
        if (dl) {
            char st[64];
            if (dl->status == DL_STATUS_PAUSED || dl->status == DL_STATUS_ERROR)
                snprintf(st, sizeof(st), "%s at %d%%", dl_status_label(dl->status),
                         percent(dl->offset, dl->total));
            else
                snprintf(st, sizeof(st), "%s", dl_status_label(dl->status));
            y = kv(y, "Status", st);
        } else {
            y = kv(y, "Status", "Not installed");
        }

        float by = DET_Y + DET_H - 90;
        gui_rect(DET_X + DET_PAD, by - 10, DET_W - DET_PAD * 2, 1, gui_rgb(HEX_LINE));
        if (roms_entry_unsupported(r))
            press_hint(by, "CROSS", "for why it can't install");
        else if (dl && (dl->status == DL_STATUS_PAUSED || dl->status == DL_STATUS_ERROR))
            press_hint(by, "CROSS", "to resume the download");
        else if (dl && dl->status == DL_STATUS_COMPLETED)
            press_hint(by, "CROSS", "to see where it went");
        else
            press_hint(by, "CROSS", "to install");
    }
    if (status && status[0])
        gui_banner(DET_X + 12, DET_Y + DET_H - 52, DET_W - 24, 40,
                   g_online > 0 ? HEX_ACCENT : HEX_ERR, status);
}

static void draw_roms_body(const RomCatalog *catalog, const DownloadList *downloads,
                           const char *system, int selected, int scroll, const char *status) {
    gui_header("ROM Catalog", g_online);
    draw_view_tabs(APP_VIEW_ROMS, "Installs into Adrenaline");

    gui_panel(LIST_X, LIST_Y, LIST_W, LIST_H);
    /* System switcher: SELECT [PSP | PS1] */
    static const char *const systems[] = { "PSP", "PS1" };
    int active = (system && strcmp(system, "PS1") == 0) ? 1 : 0;
    float cy = LIST_Y + LIST_HEAD / 2.0f - 1;
    float x = LIST_X + 14;
    x += gui_button(x, cy, "SELECT") + 8;
    gui_tabs(x, cy - 15, 30, GUI_S_SMALL, systems, 2, active);

    int total = catalog ? catalog->count : 0;
    char count[48];
    snprintf(count, sizeof(count), "%d / %d", total > 0 ? selected + 1 : 0, total);
    gui_text_mid(LIST_X + LIST_W - 18, LIST_Y, LIST_HEAD, GUI_S_SMALL, gui_rgb(HEX_DIM),
                 GUI_RIGHT, 0, count);
    gui_rect(LIST_X + 12, LIST_Y + LIST_HEAD - 3, LIST_W - 24, 1, gui_rgb(HEX_LINE));

    if (total == 0) {
        if (catalog && catalog->last_error[0])
            draw_empty("Couldn't load the catalog", catalog->last_error, HEX_ERR);
        else if (g_online <= 0)
            draw_empty("Offline", "The ROM catalog needs the server.", HEX_ERR);
        else
            draw_empty("No games here yet",
                       "Add games to the server's ROM folder, then use Settings > Refresh catalog.",
                       HEX_MUTED);
    } else {
        RomsCtx ctx = { catalog, downloads };
        draw_rows(APP_VIEW_ROMS, total, selected, scroll, roms_row, &ctx);
    }

    roms_detail(catalog, downloads, selected, status);
    draw_toast();
}

static void roms_footer(void) {
    static const GuiHint hints[] = {
        { "CROSS", "Install" }, { "TRIANGLE", "Details" }, { "SELECT", "PSP / PS1" },
        { "L", "" }, { "R", "Tabs" }, { "LR", "Page" }, { "START", "Exit" },
    };
    gui_footer(hints, (int)(sizeof(hints) / sizeof(hints[0])));
}

void ui_draw_rom_catalog(const RomCatalog *catalog,
                         const DownloadList *downloads,
                         const char *current_system,
                         int selected, int scroll_offset,
                         const char *status_line,
                         AppView current_view) {
    (void)current_view;
    g_back = BACK_ROMS;
    g_roms_args.catalog = catalog;
    g_roms_args.downloads = downloads;
    g_roms_args.system = current_system;
    g_roms_args.selected = selected;
    g_roms_args.scroll = scroll_offset;
    snprintf(g_roms_args.status, sizeof(g_roms_args.status), "%s",
             status_line ? status_line : "");

    gui_begin(true);
    draw_roms_body(catalog, downloads, current_system, selected, scroll_offset,
                   g_roms_args.status);
    roms_footer();
    gui_end();
}

/* ------------------------------------------------------------------ */
/* Downloads view                                                      */
/* ------------------------------------------------------------------ */

typedef struct {
    const DownloadList *downloads;
    bool active;
    uint64_t done, total;
} DlCtx;

static void entry_progress(const DownloadEntry *e, const DlCtx *ctx,
                           uint64_t *done, uint64_t *total) {
    *done = e->offset;
    *total = e->total;
    if (e->status == DL_STATUS_ACTIVE && ctx->active) {
        *done = ctx->done;
        if (ctx->total > 0) *total = ctx->total;
    }
    if (e->status == DL_STATUS_COMPLETED && *total > 0) *done = *total;
}

static void dl_row(int i, float y, bool sel, void *vctx) {
    const DlCtx *ctx = (const DlCtx *)vctx;
    const DownloadEntry *e = &ctx->downloads->items[i];
    float x = LIST_X + 18;
    uint32_t hex = dl_status_hex(e->status);

    /* Status pill in a fixed slot */
    const char *tag = dl_status_label(e->status);
    float ph = 20, slot = gui_pill_w(ph, GUI_S_TINY, "Downloading") + 14;
    gui_pill(x, y + (ROW_H - ph) / 2, ph, GUI_S_TINY,
             gui_rgb(sel ? HEX_INK : gui_mix(HEX_PANEL, hex, 0.30f)),
             gui_rgb(sel ? gui_mix(hex, 0xFFFFFF, 0.25f) : hex), tag);
    x += slot;

    uint64_t done, total;
    entry_progress(e, ctx, &done, &total);
    int pct = percent(done, total);
    float right = LIST_X + LIST_W - 30;
    char pbuf[8];
    snprintf(pbuf, sizeof(pbuf), "%d%%", pct);
    gui_text_mid(right, y, ROW_H, GUI_S_SMALL, gui_rgb(sel ? HEX_INK : HEX_DIM), GUI_RIGHT, 0,
                 pbuf);
    right -= 48;
    float bw = 80;
    gui_bar_track(right - bw, y + ROW_H / 2.0f - 4, bw, 8, total > 0 ? pct / 100.0f : 0,
                  gui_rgb(sel ? HEX_INK : hex),
                  gui_rgb(sel ? gui_mix(HEX_ACCENT, HEX_INK, 0.30f) : HEX_BG2));
    right -= bw + 14;

    gui_text_mid(x, y, ROW_H, GUI_S_BODY, gui_rgb(sel ? HEX_INK : HEX_TEXT), GUI_LEFT,
                 right - x, e->name[0] ? e->name : e->filename);
}

/* Big progress block for the transfer in flight */
static void active_card(const DownloadEntry *e, uint64_t done, uint64_t total, uint64_t bps) {
    float y = DET_Y + 16;
    gui_rrect(DET_X + DET_PAD, y + 3, 6, 16, 3, gui_rgb(HEX_INFO));
    gui_text(DET_X + DET_PAD + 14, y, GUI_S_BODY, gui_rgb(HEX_INFO), GUI_LEFT,
             (e->extract_format[0] && done == 0) ? "Converting on server" : "Downloading");
    y += 34;
    int n = gui_text_wrap(DET_X + DET_PAD, y, GUI_S_TITLE, gui_rgb(HEX_TEXT),
                          DET_W - DET_PAD * 2, 2, e->name[0] ? e->name : e->filename);
    y += gui_line_h(GUI_S_TITLE) * 1.12f * (n < 1 ? 1 : n) + 4;
    gui_text_fit(DET_X + DET_PAD, y, GUI_S_SMALL, gui_rgb(HEX_DIM), GUI_LEFT,
                 DET_W - DET_PAD * 2, basename_of(e->target_path));
    y += 36;

    int pct = percent(done, total);
    gui_bar(DET_X + DET_PAD, y, DET_W - DET_PAD * 2, 16,
            total > 0 ? pct / 100.0f : -1.0f, gui_rgb(HEX_ACCENT));
    y += 26;
    char a[24], b[24], line[64];
    format_size(done, a, sizeof(a));
    format_size(total, b, sizeof(b));
    snprintf(line, sizeof(line), "%s / %s", a, total > 0 ? b : "?");
    gui_text(DET_X + DET_PAD, y, GUI_S_SMALL, gui_rgb(HEX_TEXT), GUI_LEFT, line);
    snprintf(line, sizeof(line), "%d%%", pct);
    gui_text(DET_X + DET_W - DET_PAD, y, GUI_S_BODY, gui_rgb(HEX_ACCENT2), GUI_RIGHT, line);
    y += 38;

    format_bps(bps, a, sizeof(a));
    format_eta(total > done ? total - done : 0, bps, b, sizeof(b));
    y = kv(y, "Speed", a);
    y = kv(y, "Remaining", b);
}

static void dl_detail(const DownloadList *downloads, int selected, const char *status,
                      const DlCtx *ctx, uint64_t bps) {
    gui_panel(DET_X, DET_Y, DET_W, DET_H);
    const DownloadEntry *active = ctx->active ? active_dl(downloads) : NULL;
    int count = downloads ? downloads->count : 0;

    if (active) {
        active_card(active, ctx->done, ctx->total > 0 ? ctx->total : active->total, bps);
        float by = DET_Y + DET_H - 90;
        gui_rect(DET_X + DET_PAD, by - 10, DET_W - DET_PAD * 2, 1, gui_rgb(HEX_LINE));
        press_hint(by, "CIRCLE", "to pause (resume later)");
    } else if (count > 0 && selected >= 0 && selected < count) {
        const DownloadEntry *e = &downloads->items[selected];
        float y = detail_title(e->name[0] ? e->name : e->filename);
        uint32_t hex = dl_status_hex(e->status);
        float x = DET_X + DET_PAD;
        x += gui_pill(x, y, 24, GUI_S_TINY, gui_rgb(gui_mix(HEX_PANEL, hex, 0.35f)),
                      gui_rgb(hex), dl_status_label(e->status)) + 8;
        if (e->system[0])
            gui_pill(x, y, 24, GUI_S_TINY, gui_rgb(HEX_BG2), gui_rgb(HEX_DIM), e->system);
        y += 38;

        uint64_t done, total;
        entry_progress(e, ctx, &done, &total);
        gui_bar(DET_X + DET_PAD, y, DET_W - DET_PAD * 2, 12, total > 0 ? percent(done, total) / 100.0f : 0,
                gui_rgb(hex));
        y += 20;
        char a[24], b[24], line[64];
        format_size(done, a, sizeof(a));
        format_size(total, b, sizeof(b));
        snprintf(line, sizeof(line), "%s / %s  (%d%%)", a, total > 0 ? b : "?",
                 percent(done, total));
        gui_text(DET_X + DET_PAD, y, GUI_S_SMALL, gui_rgb(HEX_TEXT), GUI_LEFT, line);
        y += 32;

        y = kv(y, "Format", e->extract_format[0] ? e->extract_format : "raw");
        y = kv_wrap(y, "Saved to", e->target_path, 3);

        float by = DET_Y + DET_H - 90;
        gui_rect(DET_X + DET_PAD, by - 10, DET_W - DET_PAD * 2, 1, gui_rgb(HEX_LINE));
        if (e->status == DL_STATUS_COMPLETED)
            press_hint(by, "SQUARE", "to clear finished items");
        else
            press_hint(by, "CROSS", e->status == DL_STATUS_ERROR ? "to retry" :
                                    e->offset > 0 ? "to resume" : "to start");
    } else {
        gui_text(DET_X + DET_PAD, DET_Y + 20, GUI_S_BODY, gui_rgb(HEX_DIM), GUI_LEFT,
                 "Nothing queued");
    }
    if (status && status[0])
        gui_banner(DET_X + 12, DET_Y + DET_H - 52, DET_W - 24, 40,
                   ctx->active ? HEX_INFO : (g_online > 0 ? HEX_ACCENT : HEX_ERR), status);
}

static void draw_downloads_body(void) {
    const DownloadList *downloads = g_dl_args.downloads;
    gui_header("Downloads", g_online);
    draw_view_tabs(APP_VIEW_DOWNLOADS, NULL);

    gui_panel(LIST_X, LIST_Y, LIST_W, LIST_H);
    int total = downloads ? downloads->count : 0;
    gui_text_mid(LIST_X + 18, LIST_Y, LIST_HEAD, GUI_S_SMALL, gui_rgb(HEX_DIM), GUI_LEFT, 0,
                 "Queue");
    char count[48];
    snprintf(count, sizeof(count), "%d / %d", total > 0 ? g_dl_args.selected + 1 : 0, total);
    gui_text_mid(LIST_X + LIST_W - 18, LIST_Y, LIST_HEAD, GUI_S_SMALL, gui_rgb(HEX_DIM),
                 GUI_RIGHT, 0, count);
    gui_rect(LIST_X + 12, LIST_Y + LIST_HEAD - 3, LIST_W - 24, 1, gui_rgb(HEX_LINE));

    DlCtx ctx = { downloads, g_dl_args.active, g_dl_args.done, g_dl_args.total };
    if (total == 0)
        draw_empty("No downloads queued",
                   "Pick a game in the ROM Catalog and press Cross to install it.",
                   HEX_MUTED);
    else
        draw_rows(APP_VIEW_DOWNLOADS, total, g_dl_args.selected, g_dl_args.scroll, dl_row,
                  &ctx);

    dl_detail(downloads, g_dl_args.selected, g_dl_args.status, &ctx, g_dl_args.bps);
    draw_toast();
}

static void downloads_footer(void) {
    static const GuiHint idle[] = {
        { "CROSS", "Start / resume" }, { "CIRCLE", "Cancel" }, { "SQUARE", "Clear finished" },
        { "L", "" }, { "R", "Tabs" }, { "LR", "Page" }, { "START", "Exit" },
    };
    static const GuiHint busy[] = {
        { "CIRCLE", "Pause" },
    };
    if (g_dl_args.active) gui_footer(busy, 1);
    else gui_footer(idle, (int)(sizeof(idle) / sizeof(idle[0])));
}

void ui_draw_downloads(const DownloadList *downloads,
                       int selected, int scroll_offset,
                       const char *status_line,
                       bool active_in_progress,
                       uint64_t active_downloaded,
                       uint64_t active_total,
                       uint64_t active_bps,
                       AppView current_view) {
    (void)current_view;
    g_back = BACK_DOWNLOADS;
    g_dl_args.downloads = downloads;
    g_dl_args.selected = selected;
    g_dl_args.scroll = scroll_offset;
    snprintf(g_dl_args.status, sizeof(g_dl_args.status), "%s", status_line ? status_line : "");
    g_dl_args.active = active_in_progress;
    g_dl_args.done = active_downloaded;
    g_dl_args.total = active_total;
    g_dl_args.bps = active_bps;

    gui_begin(true);
    draw_downloads_body();
    downloads_footer();
    gui_end();
}

void ui_draw_progress_partial(const DownloadList *downloads,
                              uint64_t active_downloaded,
                              uint64_t active_total,
                              uint64_t active_bps) {
    g_back = BACK_DOWNLOADS;
    g_dl_args.downloads = downloads;
    g_dl_args.active = true;
    g_dl_args.done = active_downloaded;
    g_dl_args.total = active_total;
    g_dl_args.bps = active_bps;

    gui_begin(false);
    draw_downloads_body();
    downloads_footer();
    gui_end();
}

/* ------------------------------------------------------------------ */
/* Settings view                                                       */
/* ------------------------------------------------------------------ */

static const char *const SETTING_KEYS[UI_SETTINGS_ROWS] = {
    "Refresh catalog", "Server", "API key", "Console ID", "Connection", "Vita saves",
    "PSP saves", "Adrenaline", "Config file", "Catalog cache", "Version",
};

/* Value shown for a settings row (the refresh row has none) */
static const char *setting_value(int i, char *buf, size_t size) {
    const SyncState *s = g_set_args.state;
    if (!s) return "";
    switch (i) {
        case 1: return s->server_url;
        case 2: {
            size_t klen = strlen(s->api_key);
            if (klen == 0) snprintf(buf, size, "(not set)");
            else snprintf(buf, size, "%.4s%s", s->api_key, klen > 4 ? "********" : "");
            return buf;
        }
        case 3: return s->console_id;
        case 4: return g_online > 0 ? "Server reachable" : "Offline";
        case 5: return s->scan_vita_saves ? "Scanned" : "Skipped";
        case 6: return s->scan_psp_emu_saves ? "Scanned" : "Skipped";
        case 7: return s->pspemu_root;
        case 8: return CONFIG_PATH;
        case 9: return g_set_args.cache;
        case 10: return "v" APP_VERSION;
    }
    return "";
}

static void settings_row(int i, float y, bool sel, void *ctx) {
    (void)ctx;
    float x = LIST_X + 22;
    float right = LIST_X + LIST_W - 30;
    if (i == UI_SETTINGS_REFRESH) {
        gui_circle(x - 6, y + ROW_H / 2.0f, 3, gui_rgb(sel ? HEX_INK : HEX_ACCENT));
        gui_text_mid(x + 6, y, ROW_H, GUI_S_BODY, gui_rgb(sel ? HEX_INK : HEX_ACCENT2),
                     GUI_LEFT, 0, SETTING_KEYS[i]);
        gui_text_mid(right, y, ROW_H, GUI_S_SMALL, gui_rgb(sel ? HEX_INK : HEX_DIM),
                     GUI_RIGHT, 0, "Rescan + refetch");
        return;
    }
    char buf[64];
    const char *v = setting_value(i, buf, sizeof(buf));
    uint32_t vhex = HEX_TEXT;
    if (i == 4) vhex = g_online > 0 ? HEX_OK : HEX_ERR;
    gui_text_mid(x + 6, y, ROW_H, GUI_S_SMALL, gui_rgb(sel ? HEX_INK : HEX_DIM), GUI_LEFT, 0,
                 SETTING_KEYS[i]);
    gui_text_mid(LIST_X + 200, y, ROW_H, GUI_S_SMALL, gui_rgb(sel ? HEX_INK : vhex), GUI_LEFT,
                 right - LIST_X - 200, v);
}

static void settings_detail(int selected) {
    gui_panel(DET_X, DET_Y, DET_W, DET_H);
    if (selected < 0 || selected >= UI_SETTINGS_ROWS) return;
    float y = detail_title(SETTING_KEYS[selected]);
    float by = DET_Y + DET_H - 90;
    if (selected == UI_SETTINGS_REFRESH) {
        gui_text_wrap(DET_X + DET_PAD, y, GUI_S_SMALL, gui_rgb(HEX_TEXT), DET_W - DET_PAD * 2, 8,
                      "Rescans the server's ROM folder, drops the catalog kept on this "
                      "Vita and fetches the PSP and PS1 lists again.\n\n"
                      "Changed systems are refetched on their own; use this after "
                      "adding games to the server.");
        gui_rect(DET_X + DET_PAD, by - 10, DET_W - DET_PAD * 2, 1, gui_rgb(HEX_LINE));
        press_hint(by, "CROSS", "to refresh the catalog");
    } else {
        char buf[64];
        gui_text_wrap(DET_X + DET_PAD, y, GUI_S_BODY, gui_rgb(HEX_TEXT), DET_W - DET_PAD * 2, 5,
                      setting_value(selected, buf, sizeof(buf)));
        gui_rect(DET_X + DET_PAD, by - 10, DET_W - DET_PAD * 2, 1, gui_rgb(HEX_LINE));
        gui_text_wrap(DET_X + DET_PAD, by, GUI_S_SMALL, gui_rgb(HEX_DIM), DET_W - DET_PAD * 2, 2,
                      "Read-only. Edit config.txt and restart to change.");
    }
    char srv[300];
    snprintf(srv, sizeof(srv), "Server: %s", g_set_args.state ? g_set_args.state->server_url : "");
    gui_banner(DET_X + 12, DET_Y + DET_H - 52, DET_W - 24, 40,
               g_online > 0 ? HEX_ACCENT : HEX_ERR, srv);
}

static void draw_settings_body(void) {
    gui_header("Settings", g_online);
    draw_view_tabs(APP_VIEW_SETTINGS, CONFIG_PATH);

    gui_panel(LIST_X, LIST_Y, LIST_W, LIST_H);
    gui_text_mid(LIST_X + 18, LIST_Y, LIST_HEAD, GUI_S_SMALL, gui_rgb(HEX_DIM), GUI_LEFT, 0,
                 "Setting");
    gui_rect(LIST_X + 12, LIST_Y + LIST_HEAD - 3, LIST_W - 24, 1, gui_rgb(HEX_LINE));
    draw_rows(APP_VIEW_SETTINGS, UI_SETTINGS_ROWS, g_set_args.selected, g_set_args.scroll,
              settings_row, NULL);
    settings_detail(g_set_args.selected);
    draw_toast();
}

static void settings_footer(void) {
    static const GuiHint hints[] = {
        { "CROSS", "Select" }, { "UD", "Move" }, { "L", "" }, { "R", "Tabs" },
        { "START", "Exit" },
    };
    gui_footer(hints, (int)(sizeof(hints) / sizeof(hints[0])));
}

void ui_draw_settings(const SyncState *state, int selected, int scroll,
                      const char *cache_info) {
    g_back = BACK_SETTINGS;
    g_set_args.state = state;
    g_set_args.selected = selected;
    g_set_args.scroll = scroll;
    snprintf(g_set_args.cache, sizeof(g_set_args.cache), "%s", cache_info ? cache_info : "");

    gui_begin(true);
    draw_settings_body();
    settings_footer();
    gui_end();
}

/* ------------------------------------------------------------------ */
/* Backdrop, busy and toasts                                           */
/* ------------------------------------------------------------------ */

static void draw_splash(void) {
    gui_header(NULL, g_online);
    float cx = GUI_W / 2.0f, cy = 200;
    gui_rrect(cx - 44, cy - 74, 88, 88, 24, gui_rgb(HEX_ACCENT));
    gui_rrect(cx - 20, cy - 50, 40, 40, 10, gui_rgb(HEX_BG2));
    gui_text(cx, cy + 34, GUI_S_HUGE, gui_rgb(HEX_TEXT), GUI_CENTER, "GameSync");
    gui_text(cx, cy + 34 + gui_line_h(GUI_S_HUGE) * 1.2f, GUI_S_SMALL, gui_rgb(HEX_DIM),
             GUI_CENTER, "Save sync and game installs for PS Vita  -  v" APP_VERSION);
}

/* Repaint the last view without its footer */
static void draw_backdrop(void) {
    switch (g_back) {
        case BACK_SAVES:
            draw_saves_body(g_saves_args.state, g_saves_args.selected, g_saves_args.scroll);
            break;
        case BACK_ROMS:
            draw_roms_body(g_roms_args.catalog, g_roms_args.downloads, g_roms_args.system,
                           g_roms_args.selected, g_roms_args.scroll, g_roms_args.status);
            break;
        case BACK_DOWNLOADS:
            draw_downloads_body();
            break;
        case BACK_SETTINGS:
            draw_settings_body();
            break;
        default:
            draw_splash();
            break;
    }
}

void ui_init(void) {
    sceCtrlSetSamplingMode(SCE_CTRL_MODE_DIGITAL);
    gui_init();
}

void ui_term(void) {
    gui_exit();
}

void ui_set_online(int online) {
    g_online = online;
}

void ui_busy(const char *fmt, ...) {
    char msg[192];
    va_list args;
    va_start(args, fmt);
    vsnprintf(msg, sizeof(msg), fmt, args);
    va_end(args);

    gui_begin(false);
    draw_backdrop();
    gui_footer(NULL, 0);
    if (g_back == BACK_SPLASH) {
        /* Startup: a status line under the logo, no card */
        float y = 330;
        gui_spinner(GUI_W / 2.0f - gui_text_w(GUI_S_BODY, msg) / 2 - 24, y + 12, 9,
                    gui_rgb(HEX_ACCENT));
        gui_text(GUI_W / 2.0f + 6, y, GUI_S_BODY, gui_rgb(HEX_DIM), GUI_CENTER, msg);
    } else {
        gui_dim();
        float w = 560, h = 104, x = (GUI_W - w) / 2, y = (GUI_H - h) / 2 - 10;
        gui_card(x, y, w, h, "Working", HEX_ACCENT);
        gui_spinner(x + 40, y + 72, 11, gui_rgb(HEX_ACCENT));
        gui_text_mid(x + 70, y + 46, 52, GUI_S_BODY, gui_rgb(HEX_TEXT), GUI_LEFT, w - 90, msg);
    }
    gui_end();
}

void ui_toast(UiTone tone, const char *fmt, ...) {
    va_list args;
    va_start(args, fmt);
    vsnprintf(g_toast, sizeof(g_toast), fmt, args);
    va_end(args);
    g_toast_tone = tone;
    g_toast_until = gui_ticks() + 150;   /* ~2.5 s */
}

/* ------------------------------------------------------------------ */
/* Dialogs                                                             */
/* ------------------------------------------------------------------ */

#define DLG_W 640
#define DLG_MAX_LINES 14

/* Tone badge: a filled circle with a check / cross / "!" / "i" */
static void tone_badge(float cx, float cy, UiTone tone) {
    uint32_t hex = tone_hex(tone);
    gui_circle(cx, cy, 20, gui_rgb(gui_mix(HEX_PANEL, hex, 0.25f)));
    gui_circle(cx, cy, 15, gui_rgb(hex));
    unsigned int ink = gui_rgb(HEX_INK);
    if (tone == UI_TONE_OK) {
        gui_icon_check(cx, cy, 16, ink);
    } else if (tone == UI_TONE_ERR) {
        gui_icon_cross(cx, cy, 16, ink);
    } else if (tone == UI_TONE_WARN) {
        gui_rrect(cx - 2, cy - 9, 4, 11, 2, ink);
        gui_circle(cx, cy + 6, 2.2f, ink);
    } else {
        gui_circle(cx, cy - 7, 2.2f, ink);
        gui_rrect(cx - 2, cy - 3, 4, 12, 2, ink);
    }
}

/* Hints right-aligned along the bottom strip of a card */
static void card_buttons(float x, float y, float w, float h, const GuiHint *hints, int count) {
    float by = y + h - 46;
    gui_rect(x + 14, by, w - 28, 1, gui_rgb(HEX_LINE));
    gui_hints(x + w - 20, by + 23, hints, count, true);
}

static void draw_notice(UiTone tone, const char *title, char lines[][GUI_WRAP_LINE], int n,
                        const GuiHint *hints, int hint_count) {
    float lh = gui_line_h(GUI_S_BODY) * 1.18f;
    float body_h = n * lh;
    if (body_h < 44) body_h = 44;
    float h = 40 + 22 + body_h + 22 + 46;
    float x = (GUI_W - DLG_W) / 2, y = (GUI_H - h) / 2;

    gui_dim();
    gui_card(x, y, DLG_W, h, title, tone_hex(tone));
    tone_badge(x + 42, y + 40 + 22 + 20, tone);
    for (int i = 0; i < n; i++)
        gui_text(x + 80, y + 40 + 22 + i * lh, GUI_S_BODY, gui_rgb(HEX_TEXT), GUI_LEFT, lines[i]);
    card_buttons(x, y, DLG_W, h, hints, hint_count);
}

/* Wrap a multi-paragraph body into card lines */
static int layout_body(const char *body, float width, char lines[][GUI_WRAP_LINE], int max) {
    return gui_wrap(body, GUI_S_BODY, width, lines, max);
}

void ui_notice(UiTone tone, const char *title, const char *fmt, ...) {
    char body[768];
    va_list args;
    va_start(args, fmt);
    vsnprintf(body, sizeof(body), fmt, args);
    va_end(args);

    static char lines[DLG_MAX_LINES][GUI_WRAP_LINE];
    int n = layout_body(body, DLG_W - 110, lines, DLG_MAX_LINES);

    drain_buttons();
    uint32_t prev = 0;
    while (1) {
        uint32_t just = pressed(&prev);
        if (just & (SCE_CTRL_CROSS | SCE_CTRL_CIRCLE)) break;
        gui_begin(true);
        draw_backdrop();
        gui_footer(NULL, 0);
        static const GuiHint ok[] = { { "CROSS", "OK" } };
        draw_notice(tone, title, lines, n, ok, 1);
        gui_end();
    }
    drain_buttons();
}

bool ui_ask(UiTone tone, const char *title, const char *confirm_label, const char *fmt, ...) {
    char body[768];
    va_list args;
    va_start(args, fmt);
    vsnprintf(body, sizeof(body), fmt, args);
    va_end(args);

    static char lines[DLG_MAX_LINES][GUI_WRAP_LINE];
    int n = layout_body(body, DLG_W - 110, lines, DLG_MAX_LINES);
    GuiHint hints[2] = { { "CROSS", confirm_label }, { "CIRCLE", "Cancel" } };

    drain_buttons();
    uint32_t prev = 0;
    bool yes = false;
    while (1) {
        uint32_t just = pressed(&prev);
        if (just & SCE_CTRL_CROSS) { yes = true; break; }
        if (just & SCE_CTRL_CIRCLE) break;
        gui_begin(true);
        draw_backdrop();
        gui_footer(NULL, 0);
        draw_notice(tone, title, lines, n, hints, 2);
        gui_end();
    }
    drain_buttons();
    return yes;
}

/* One side of the compare card */
static void side_box(float x, float y, float w, float h, const char *label, uint32_t hex,
                     const char *line1, const char *line2, bool empty) {
    gui_rrect(x, y, w, h, 9, gui_rgb(HEX_BG2));
    gui_rrect(x, y, w, 5, 2.5f, gui_rgb(hex));
    gui_text(x + 16, y + 14, GUI_S_SMALL, gui_rgb(HEX_DIM), GUI_LEFT, label);
    if (empty) {
        gui_text(x + 16, y + 44, GUI_S_BODY, gui_rgb(HEX_MUTED), GUI_LEFT, line1);
        return;
    }
    gui_text_fit(x + 16, y + 44, GUI_S_BODY, gui_rgb(HEX_TEXT), GUI_LEFT, w - 32, line1);
    if (line2 && line2[0])
        gui_text_fit(x + 16, y + 74, GUI_S_SMALL, gui_rgb(HEX_DIM), GUI_LEFT, w - 32, line2);
}

typedef struct {
    const TitleInfo *title;
    SyncAction action;
    const char *server_hash;
    uint32_t server_size;
    char date[32];
} ConfirmCtx;

static void draw_confirm(const ConfirmCtx *c) {
    const TitleInfo *t = c->title;
    float w = 760, h = 362, x = (GUI_W - w) / 2, y = (GUI_H - h) / 2;

    uint32_t hex;
    const char *head, *banner, *detail;
    switch (c->action) {
        case SYNC_UPLOAD:
            hex = HEX_WARN; head = "Upload save";
            banner = "Upload to server";
            detail = "The copy on this Vita replaces the server copy.";
            break;
        case SYNC_DOWNLOAD:
            hex = HEX_INFO; head = "Download save";
            banner = "Download from server";
            detail = "The server copy replaces the save on this Vita.";
            break;
        case SYNC_CONFLICT:
            hex = HEX_ERR; head = "Sync conflict";
            banner = "Both copies changed";
            detail = "Pick which copy to keep - the other one is overwritten.";
            break;
        case SYNC_UP_TO_DATE:
            hex = HEX_OK; head = "Already in sync";
            banner = "Up to date";
            detail = "This Vita and the server have the same save.";
            break;
        default:
            hex = HEX_ERR; head = "Sync";
            banner = "Can't compare this save";
            detail = "Reading the local save failed. See sync_diag.txt.";
            break;
    }

    gui_dim();
    gui_card(x, y, w, h, head, hex);
    float cy = y + 54;
    gui_text_fit(x + 24, cy, GUI_S_TITLE, gui_rgb(HEX_TEXT), GUI_LEFT, w - 48, title_display(t));
    cy += gui_line_h(GUI_S_TITLE) * 1.2f;
    float px = x + 24;
    uint32_t phex = platform_hex(t);
    px += gui_pill(px, cy, 22, GUI_S_TINY, gui_rgb(gui_mix(HEX_PANEL, phex, 0.35f)),
                   gui_rgb(gui_mix(phex, 0xFFFFFF, 0.4f)), platform_label(t)) + 10;
    gui_text_mid(px, cy, 22, GUI_S_SMALL, gui_rgb(HEX_DIM), GUI_LEFT, 0, t->game_id);
    cy += 38;

    /* This Vita | Server */
    float bw = (w - 48 - 16) / 2, bh = 104;
    char l1[48], l2[64], sz[24];
    if (t->server_only) {
        side_box(x + 24, cy, bw, bh, "This Vita", HEX_WARN, "No save", NULL, true);
    } else {
        format_size(t->total_size, sz, sizeof(sz));
        snprintf(l1, sizeof(l1), "%s", sz);
        snprintf(l2, sizeof(l2), "%d file%s", t->file_count, t->file_count == 1 ? "" : "s");
        side_box(x + 24, cy, bw, bh, "This Vita", HEX_WARN, l1, l2, false);
    }
    if (c->server_hash && c->server_hash[0]) {
        format_size(c->server_size, sz, sizeof(sz));
        snprintf(l1, sizeof(l1), "%s", sz);
        snprintf(l2, sizeof(l2), "Saved %s", c->date);
        side_box(x + 24 + bw + 16, cy, bw, bh, "Server", HEX_INFO, l1,
                 c->date[0] != '-' ? l2 : NULL, false);
    } else {
        side_box(x + 24 + bw + 16, cy, bw, bh, "Server", HEX_INFO, "No save", NULL, true);
    }
    cy += bh + 16;

    /* Verdict banner */
    float bh2 = 62;
    gui_rrect(x + 24, cy, w - 48, bh2, 9, gui_rgb(gui_mix(HEX_BG2, hex, 0.14f)));
    gui_rrect(x + 24, cy, 5, bh2, 2.5f, gui_rgb(hex));
    UiTone tone = c->action == SYNC_UPLOAD ? UI_TONE_WARN :
                  c->action == SYNC_DOWNLOAD ? UI_TONE_INFO :
                  c->action == SYNC_UP_TO_DATE ? UI_TONE_OK : UI_TONE_ERR;
    tone_badge(x + 62, cy + bh2 / 2, tone);
    gui_text(x + 96, cy + 8, GUI_S_BODY, gui_rgb(hex), GUI_LEFT, banner);
    gui_text_fit(x + 96, cy + 34, GUI_S_SMALL, gui_rgb(HEX_DIM), GUI_LEFT, w - 140, detail);

    /* Cross = the recommended action; Square / Triangle force a side */
    GuiHint hints[4];
    int n = 0;
    bool can_up = !t->server_only;
    bool can_down = c->server_hash && c->server_hash[0];
    if (c->action == SYNC_UPLOAD) {
        hints[n++] = (GuiHint){ "CROSS", "Upload" };
        if (can_down) hints[n++] = (GuiHint){ "TRIANGLE", "Download instead" };
    } else if (c->action == SYNC_DOWNLOAD) {
        hints[n++] = (GuiHint){ "CROSS", "Download" };
        if (can_up) hints[n++] = (GuiHint){ "SQUARE", "Upload instead" };
    } else if (c->action == SYNC_CONFLICT) {
        if (can_up) hints[n++] = (GuiHint){ "SQUARE", "Keep this Vita's" };
        if (can_down) hints[n++] = (GuiHint){ "TRIANGLE", "Keep server's" };
    } else {
        hints[n++] = (GuiHint){ "CROSS", "OK" };
        if (can_up) hints[n++] = (GuiHint){ "SQUARE", "Upload" };
        if (can_down) hints[n++] = (GuiHint){ "TRIANGLE", "Download" };
    }
    hints[n++] = (GuiHint){ "CIRCLE", "Cancel" };
    card_buttons(x, y, w, h, hints, n);
}

int ui_confirm(const TitleInfo *title, SyncAction action,
               const char *server_hash, uint32_t server_size,
               const char *server_last_sync) {
    ConfirmCtx c;
    c.title = title;
    c.action = action;
    c.server_hash = server_hash;
    c.server_size = server_size;
    format_date(server_last_sync, c.date, sizeof(c.date));
    bool can_up = !title->server_only;
    bool can_down = server_hash && server_hash[0];

    drain_buttons();
    uint32_t prev = 0;
    int result = -1;
    while (1) {
        uint32_t just = pressed(&prev);
        if (just & SCE_CTRL_CIRCLE) break;
        if (just & SCE_CTRL_CROSS) {
            if (action == SYNC_UPLOAD || action == SYNC_DOWNLOAD) result = (int)action;
            if (action != SYNC_CONFLICT) break;   /* a conflict needs a side */
        }
        if ((just & SCE_CTRL_SQUARE) && can_up)     { result = SYNC_UPLOAD; break; }
        if ((just & SCE_CTRL_TRIANGLE) && can_down) { result = SYNC_DOWNLOAD; break; }
        gui_begin(true);
        draw_backdrop();
        gui_footer(NULL, 0);
        draw_confirm(&c);
        gui_end();
    }
    drain_buttons();
    return result;
}

static void stat_tile(float x, float y, float w, float h, const char *label, int value,
                      uint32_t hex) {
    gui_rrect(x, y, w, h, 9, gui_rgb(HEX_BG2));
    gui_rrect(x, y, w, 5, 2.5f, gui_rgb(value > 0 ? hex : HEX_LINE));
    char num[16];
    snprintf(num, sizeof(num), "%d", value);
    gui_text(x + w / 2, y + 18, GUI_S_HUGE, gui_rgb(value > 0 ? hex : HEX_MUTED), GUI_CENTER,
             num);
    gui_text(x + w / 2, y + h - 30, GUI_S_SMALL, gui_rgb(HEX_DIM), GUI_CENTER, label);
}

void ui_sync_summary(const SyncSummary *s) {
    bool bad = s->failed > 0 || s->conflicts > 0;
    UiTone tone = s->failed > 0 ? UI_TONE_ERR : s->conflicts > 0 ? UI_TONE_WARN : UI_TONE_OK;
    const char *verdict =
        s->failed > 0    ? "Some saves failed - see sync_diag.txt" :
        s->conflicts > 0 ? "Conflicts need a manual choice: select them and press Cross" :
                           "Everything is in sync";

    drain_buttons();
    uint32_t prev = 0;
    while (1) {
        uint32_t just = pressed(&prev);
        if (just & (SCE_CTRL_CROSS | SCE_CTRL_CIRCLE)) break;
        gui_begin(true);
        draw_backdrop();
        gui_footer(NULL, 0);

        float w = 760, h = 292, x = (GUI_W - w) / 2, y = (GUI_H - h) / 2;
        gui_dim();
        gui_card(x, y, w, h, "Sync all - finished", tone_hex(tone));
        float tw = (w - 48 - 4 * 12) / 5, ty = y + 62, th = 112;
        stat_tile(x + 24 + 0 * (tw + 12), ty, tw, th, "Uploaded",   s->uploaded,   HEX_WARN);
        stat_tile(x + 24 + 1 * (tw + 12), ty, tw, th, "Downloaded", s->downloaded, HEX_INFO);
        stat_tile(x + 24 + 2 * (tw + 12), ty, tw, th, "Up to date", s->up_to_date, HEX_OK);
        stat_tile(x + 24 + 3 * (tw + 12), ty, tw, th, "Conflicts",  s->conflicts,  HEX_ERR);
        stat_tile(x + 24 + 4 * (tw + 12), ty, tw, th, "Failed",     s->failed,     HEX_ERR);
        float by = ty + th + 18;
        tone_badge(x + 44, by + 20, bad ? tone : UI_TONE_OK);
        gui_text_mid(x + 76, by, 40, GUI_S_BODY, gui_rgb(HEX_TEXT), GUI_LEFT, w - 100, verdict);
        static const GuiHint ok[] = { { "CROSS", "OK" } };
        card_buttons(x, y, w, h, ok, 1);
        gui_end();
    }
    drain_buttons();
}
