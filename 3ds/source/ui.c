#include "ui.h"
#include <math.h>
#include "config.h"
#include "sync.h"

// ---------------------------------------------------------------------------
// Init
// ---------------------------------------------------------------------------

static void splash_top(void *ctx);
static void splash_bottom(void *ctx);

static UiBackdrop backdrop = { splash_top, splash_bottom, NULL };

void ui_init(void) {
    gui_init();
}

void ui_exit(void) {
    gui_exit();
}

// ---------------------------------------------------------------------------
// Small helpers
// ---------------------------------------------------------------------------

u32 ui_tone_hex(UiTone tone) {
    switch (tone) {
        case UI_TONE_OK:   return HEX_OK;
        case UI_TONE_WARN: return HEX_WARN;
        case UI_TONE_INFO: return HEX_INFO;
        case UI_TONE_ERR:  return HEX_ERR;
        default:           return HEX_ACCENT;
    }
}

static void format_size(u32 bytes, char *out, int out_size) {
    if (bytes >= 1024 * 1024)
        snprintf(out, out_size, "%.1f MB", bytes / (1024.0 * 1024.0));
    else if (bytes >= 1024)
        snprintf(out, out_size, "%.1f KB", bytes / 1024.0);
    else
        snprintf(out, out_size, "%lu B", (unsigned long)bytes);
}

// ISO 8601 "YYYY-MM-DDTHH:MM:SS..." -> "YYYY-MM-DD HH:MM"
static void format_date(const char *iso, char *out, int out_size) {
    if (strlen(iso) >= 16 && iso[10] == 'T')
        snprintf(out, out_size, "%.10s %.5s", iso, iso + 11);
    else if (iso[0])
        snprintf(out, out_size, "%.19s", iso);
    else
        snprintf(out, out_size, "-");
}

// Wait for the next frame's input; false if the app is closing
static bool next_input(u32 *down) {
    if (!aptMainLoop()) return false;
    hidScanInput();
    *down = hidKeysDown();
    return true;
}

// ---------------------------------------------------------------------------
// Backdrop
// ---------------------------------------------------------------------------

static void splash_top(void *ctx) {
    (void)ctx;
    gui_header(NULL);
    float cx = GUI_TOP_W / 2.0f, cy = 112;
    gui_rrect(cx - 26, cy - 44, 52, 52, 14, gui_rgb(HEX_ACCENT));
    gui_rrect(cx - 12, cy - 30, 24, 24, 6, gui_rgb(HEX_BG2));
    gui_text(cx, cy + 16, 0.9f, gui_rgb(HEX_TEXT), GUI_CENTER, "GameSync");
    gui_text(cx, cy + 46, GUI_S_SMALL, gui_rgb(HEX_DIM), GUI_CENTER, "Save sync for 3DS & DS  \xC2\xB7  v" APP_VERSION);
}

static void splash_bottom(void *ctx) {
    (void)ctx;
    gui_header_bar();
}

UiBackdrop ui_set_backdrop(UiDrawFn top, UiDrawFn bottom, void *ctx) {
    UiBackdrop prev = backdrop;
    backdrop.top = top;
    backdrop.bottom = bottom;
    backdrop.ctx = ctx;
    return prev;
}

void ui_restore_backdrop(UiBackdrop previous) {
    backdrop = previous;
}

void ui_draw_backdrop_top(void) {
    if (backdrop.top) backdrop.top(backdrop.ctx);
}

void ui_draw_backdrop_bottom(void) {
    if (backdrop.bottom) backdrop.bottom(backdrop.ctx);
}

// ---------------------------------------------------------------------------
// Dialogs
// ---------------------------------------------------------------------------

#define DLG_X 10
#define DLG_W (GUI_BOT_W - 2 * DLG_X)
#define DLG_MAX_LINES 10

typedef struct {
    char text[DLG_MAX_LINES][GUI_WRAP_LINE];
    u8 style[DLG_MAX_LINES];  // 0 normal, 1 dim, 2 tone
    int count;
} DialogLines;

static void layout_body(const char *body, float width, DialogLines *out) {
    out->count = 0;
    if (!body) return;
    const char *p = body;
    while (*p && out->count < DLG_MAX_LINES) {
        const char *nl = strchr(p, '\n');
        size_t len = nl ? (size_t)(nl - p) : strlen(p);
        char para[512];
        if (len > sizeof(para) - 1) len = sizeof(para) - 1;
        memcpy(para, p, len);
        para[len] = '\0';
        u8 style = 0;
        char *text = para;
        if (*text == '\x01') { style = 1; text++; }
        else if (*text == '\x02') { style = 2; text++; }
        if (!*text) {
            // Blank line: half a line of space, kept as an empty entry
            out->text[out->count][0] = '\0';
            out->style[out->count++] = 0;
        } else {
            char lines[DLG_MAX_LINES][GUI_WRAP_LINE];
            int n = gui_wrap(text, GUI_S_BODY, width, lines, DLG_MAX_LINES - out->count);
            for (int i = 0; i < n; i++) {
                memcpy(out->text[out->count], lines[i], GUI_WRAP_LINE);
                out->style[out->count++] = style;
            }
        }
        if (!nl) break;
        p = nl + 1;
    }
}

static float buttons_width(const UiButton *b, int count) {
    float w = 0;
    for (int i = 0; i < count; i++)
        w += gui_button_w(b[i].button) + 4 + gui_text_w(GUI_S_SMALL, b[i].label) + (i ? 12 : 0);
    return w;
}

static void draw_dialog(UiTone tone, const char *title, const DialogLines *lines,
                        const UiButton *buttons, int count) {
    float lh = gui_line_h(GUI_S_BODY);
    float body_h = 0;
    for (int i = 0; i < lines->count; i++) body_h += lines->text[i][0] ? lh : lh / 2;
    float h = 24 + 10 + body_h + 10 + 24;
    if (h > GUI_H - 12) h = GUI_H - 12;
    float y = (GUI_H - h) / 2;
    u32 hex = ui_tone_hex(tone);

    gui_dim();
    gui_card(DLG_X, y, DLG_W, h, title, hex);

    float ty = y + 24 + 9;
    for (int i = 0; i < lines->count; i++) {
        if (!lines->text[i][0]) {
            ty += lh / 2;
            continue;
        }
        // Too long for the screen: stop above the button row
        if (ty + lh > y + h - 24 - 4) break;
        u32 color = lines->style[i] == 1 ? HEX_DIM : (lines->style[i] == 2 ? hex : HEX_TEXT);
        gui_text(DLG_X + 12, ty, GUI_S_BODY, gui_rgb(color), GUI_LEFT, lines->text[i]);
        ty += lh;
    }

    // Button row, right-aligned
    float by = y + h - 24;
    gui_rect(DLG_X + 8, by, DLG_W - 16, 1, gui_rgb(HEX_LINE));
    float x = DLG_X + DLG_W - 12 - buttons_width(buttons, count);
    for (int i = 0; i < count; i++) {
        if (i) x += 12;
        x += gui_button(x, by + 12.5f, buttons[i].button) + 4;
        x += gui_text_mid(x, by, 24, GUI_S_SMALL, gui_rgb(HEX_TEXT), GUI_LEFT, 0, buttons[i].label);
    }
}

u32 ui_dialog(UiTone tone, const char *title, const char *body, const UiButton *buttons, int count) {
    static const UiButton any = { 0, "A", "Continue" };
    bool any_key = (count == 0);
    if (any_key) {
        buttons = &any;
        count = 1;
    }
    DialogLines lines;
    layout_body(body, DLG_W - 24, &lines);

    // Ignore keys still held from whatever opened the dialog
    hidScanInput();
    while (aptMainLoop()) {
        gui_begin(true);
        gui_screen(GUI_TOP);
        ui_draw_backdrop_top();
        gui_screen(GUI_BOTTOM);
        ui_draw_backdrop_bottom();
        draw_dialog(tone, title, &lines, buttons, count);
        gui_end();

        u32 down;
        if (!next_input(&down)) break;
        if (any_key) {
            u32 keys = down & (KEY_A | KEY_B | KEY_X | KEY_Y | KEY_START | KEY_SELECT | KEY_L | KEY_R | KEY_TOUCH);
            if (keys) return keys;
            continue;
        }
        for (int i = 0; i < count; i++)
            if (down & buttons[i].keys) return down & buttons[i].keys;
    }
    return 0;
}

void ui_message(UiTone tone, const char *title, const char *body) {
    ui_dialog(tone, title, body, NULL, 0);
}

bool ui_confirm(UiTone tone, const char *title, const char *body, const char *yes_label) {
    UiButton b[2] = {
        { KEY_B, "B", "Cancel" },
        { KEY_A, "A", yes_label ? yes_label : "OK" },
    };
    return (ui_dialog(tone, title, body, b, 2) & KEY_A) != 0;
}

void ui_progress(const UiProgress *p) {
    gui_begin(false);
    gui_screen(GUI_TOP);
    ui_draw_backdrop_top();
    gui_screen(GUI_BOTTOM);
    ui_draw_backdrop_bottom();
    gui_dim();

    u32 hex = ui_tone_hex(p->tone);
    float lh_small = gui_line_h(GUI_S_SMALL);
    float h = 24 + 12;
    if (p->name) h += gui_line_h(GUI_S_BODY) + 2;
    if (p->status) h += lh_small + 4;
    if (p->frac <= 1) h += 14 + lh_small + 6;
    if (p->info1) h += lh_small;
    if (p->info2) h += lh_small;
    if (p->cancel_hint) h += 22;
    h += 6;
    float y = (GUI_H - h) / 2;
    gui_card(DLG_X, y, DLG_W, h, p->title ? p->title : "Working", hex);

    float x = DLG_X + 12, w = DLG_W - 24, ty = y + 24 + 10;
    if (p->name) {
        gui_text_fit(x, ty, GUI_S_BODY, gui_rgb(HEX_TEXT), GUI_LEFT, w, p->name);
        ty += gui_line_h(GUI_S_BODY) + 2;
    }
    if (p->status) {
        if (p->frac < 0) {
            gui_spinner(x + 5, ty + lh_small / 2 + 1, 5, gui_rgb(hex));
            gui_text_fit(x + 16, ty, GUI_S_SMALL, gui_rgb(HEX_DIM), GUI_LEFT, w - 16, p->status);
        } else {
            gui_text_fit(x, ty, GUI_S_SMALL, gui_rgb(HEX_DIM), GUI_LEFT, w, p->status);
        }
        ty += lh_small + 4;
    }
    if (p->frac <= 1) {
        gui_bar(x, ty, w, 10, p->frac, gui_rgb(hex));
        ty += 14;
        if (p->left) gui_text(x, ty, GUI_S_SMALL, gui_rgb(HEX_TEXT), GUI_LEFT, p->left);
        if (p->right) gui_text(x + w, ty, GUI_S_SMALL, gui_rgb(hex), GUI_RIGHT, p->right);
        ty += lh_small + 6;
    }
    if (p->info1) {
        gui_text_fit(x, ty, GUI_S_SMALL, gui_rgb(HEX_DIM), GUI_LEFT, w, p->info1);
        ty += lh_small;
    }
    if (p->info2) {
        gui_text_fit(x, ty, GUI_S_SMALL, gui_rgb(HEX_DIM), GUI_LEFT, w, p->info2);
        ty += lh_small;
    }
    if (p->cancel_hint) {
        float by = y + h - 22;
        gui_rect(DLG_X + 8, by, DLG_W - 16, 1, gui_rgb(HEX_LINE));
        float bx = DLG_X + 12;
        bx += gui_text_mid(bx, by, 22, GUI_S_SMALL, gui_rgb(HEX_DIM), GUI_LEFT, 0, "Hold ") ;
        bx += gui_button(bx, by + 11.5f, "B") + 4;
        gui_text_mid(bx, by, 22, GUI_S_SMALL, gui_rgb(HEX_DIM), GUI_LEFT, 0, "to cancel");
    }
    gui_end();
}

void ui_busy(const char *title, const char *message) {
    UiProgress p = { 0 };
    p.title = title;
    p.status = message;
    p.frac = -1;
    ui_progress(&p);
}

// ---------------------------------------------------------------------------
// Choice list
// ---------------------------------------------------------------------------

#define CHOICE_ROWS 7
#define CHOICE_ROW_H 22

static void draw_choice(UiTone tone, const char *title, const char *const *items, int count,
                        int selected, int scroll, float *list_y) {
    int rows = count < CHOICE_ROWS ? count : CHOICE_ROWS;
    float h = 24 + 6 + rows * CHOICE_ROW_H + 6 + 24;
    float y = (GUI_H - h) / 2;
    u32 hex = ui_tone_hex(tone);
    gui_dim();
    gui_card(DLG_X, y, DLG_W, h, title, hex);
    float ly = y + 24 + 6;
    *list_y = ly;
    for (int i = 0; i < rows; i++) {
        int index = scroll + i;
        float ry = ly + i * CHOICE_ROW_H;
        bool on = (index == selected);
        if (on) gui_rrect(DLG_X + 6, ry + 1, DLG_W - 12, CHOICE_ROW_H - 2, 5, gui_rgb(HEX_ACCENT));
        gui_text_mid(DLG_X + 16, ry, CHOICE_ROW_H, GUI_S_BODY, gui_rgb(on ? HEX_INK : HEX_TEXT), GUI_LEFT,
                     DLG_W - 32, items[index]);
    }
    if (count > rows)
        gui_scrollbar(DLG_X + DLG_W - 5, ly + 2, rows * CHOICE_ROW_H - 4, scroll, rows, count, (float)scroll);
    static const UiButton b[] = { { KEY_B, "B", "Cancel" }, { KEY_A, "A", "Select" } };
    float by = y + h - 24;
    gui_rect(DLG_X + 8, by, DLG_W - 16, 1, gui_rgb(HEX_LINE));
    float x = DLG_X + DLG_W - 12 - buttons_width(b, 2);
    for (int i = 0; i < 2; i++) {
        if (i) x += 12;
        x += gui_button(x, by + 12.5f, b[i].button) + 4;
        x += gui_text_mid(x, by, 24, GUI_S_SMALL, gui_rgb(HEX_TEXT), GUI_LEFT, 0, b[i].label);
    }
}

int ui_choose(UiTone tone, const char *title, const char *const *items, int count) {
    if (count <= 0) return -1;
    int selected = 0, scroll = 0;
    int rows = count < CHOICE_ROWS ? count : CHOICE_ROWS;
    hidScanInput();
    while (aptMainLoop()) {
        float list_y = 0;
        gui_begin(true);
        gui_screen(GUI_TOP);
        ui_draw_backdrop_top();
        gui_screen(GUI_BOTTOM);
        ui_draw_backdrop_bottom();
        draw_choice(tone, title, items, count, selected, scroll, &list_y);
        gui_end();

        hidScanInput();
        u32 down = hidKeysDown(), rep = hidKeysDownRepeat();
        if (rep & KEY_UP) selected = (selected - 1 + count) % count;
        if (rep & KEY_DOWN) selected = (selected + 1) % count;
        if (rep & KEY_LEFT) selected = selected - rows < 0 ? 0 : selected - rows;
        if (rep & KEY_RIGHT) selected = selected + rows >= count ? count - 1 : selected + rows;
        if (selected < scroll) scroll = selected;
        if (selected >= scroll + rows) scroll = selected - rows + 1;
        if (down & KEY_TOUCH) {
            // A tap picks the row right away
            int i = ui_list_touch(list_y, rows, CHOICE_ROW_H, count, scroll);
            if (i >= 0) return i;
        }
        if (down & KEY_B) return -1;
        if (down & KEY_A) return selected;
    }
    return -1;
}

// ---------------------------------------------------------------------------
// Top-level tabs
// ---------------------------------------------------------------------------

static const char *const tab_labels[UI_TAB_COUNT] = { "Saves", "Catalog", "Settings" };

void ui_tab_header(int active) {
    gui_header_tabs(tab_labels, UI_TAB_COUNT, active);
}

UiNav ui_tab_nav(u32 down) {
    if (down & KEY_L) return UI_NAV_PREV;
    if (down & KEY_R) return UI_NAV_NEXT;
    if (down & KEY_START) {
        UiButton b[2] = { { KEY_B, "B", "Cancel" }, { KEY_A | KEY_START, "A", "Exit" } };
        if (ui_dialog(UI_TONE_ACCENT, "Exit GameSync?", "Close the app? Press START again or A to exit.", b, 2) &
            (KEY_A | KEY_START))
            return UI_NAV_EXIT;
    }
    return UI_NAV_STAY;
}

// ---------------------------------------------------------------------------
// List
// ---------------------------------------------------------------------------

void ui_list(UiListAnim *a, float x, float y, float w, int rows, float row_h,
             int count, int selected, int scroll, UiRowFn row, void *ctx) {
    if (!a->init || a->count != count) {
        a->sel = selected;
        a->scroll = scroll;
        a->count = count;
        a->init = true;
    }
    // Long jumps snap, short moves glide
    if (fabsf(a->sel - selected) > rows) a->sel = selected;
    if (fabsf(a->scroll - scroll) > rows) a->scroll = scroll;
    gui_ease(&a->sel, (float)selected, 0.4f);
    gui_ease(&a->scroll, (float)scroll, 0.4f);

    bool bar = count > rows;
    float rw = bar ? w - 7 : w;
    if (count > 0 && selected >= 0) {
        float sy = y + (a->sel - a->scroll) * row_h;
        gui_rrect(x + 3, sy + 1, rw - 6, row_h - 2, 5, gui_rgb(HEX_ACCENT));
    }
    int first = (int)a->scroll;
    for (int i = first; i <= first + rows && i < count; i++) {
        if (i < 0) continue;
        float ry = y + (i - a->scroll) * row_h;
        if (ry < y - row_h || ry > y + rows * row_h) continue;
        row(ctx, i, x, ry, rw, row_h, i == selected);
    }
    // Mask the row sliding out at the bottom
    gui_rect(x, y + rows * row_h, w, row_h, gui_rgb(HEX_BG));
    if (bar) gui_scrollbar(x + w - 6, y + 3, rows * row_h - 6, scroll, rows, count, a->scroll);
}

int ui_list_touch(float y, int rows, float row_h, int count, int scroll) {
    touchPosition t;
    hidTouchRead(&t);
    if (t.py < y || t.py >= y + rows * row_h) return -1;
    int i = scroll + (int)((t.py - y) / row_h);
    return (i >= 0 && i < count) ? i : -1;
}

// ---------------------------------------------------------------------------
// Save list
// ---------------------------------------------------------------------------

static const char *state_label(const TitleInfo *t, u32 *hex) {
    TitleSyncState s = (TitleSyncState)t->sync_state;
    if (t->in_conflict) s = TSTATE_CONFLICT;
    switch (s) {
        case TSTATE_SYNCED:         *hex = HEX_OK;   return "Up to date";
        case TSTATE_UPLOADED:       *hex = HEX_OK;   return "Uploaded";
        case TSTATE_DOWNLOADED:     *hex = HEX_OK;   return "Downloaded";
        case TSTATE_NEEDS_UPLOAD:   *hex = HEX_WARN; return "Needs upload";
        case TSTATE_NEEDS_DOWNLOAD: *hex = HEX_INFO; return "Needs download";
        case TSTATE_CONFLICT:       *hex = HEX_ERR;  return "Conflict";
        case TSTATE_FAILED:         *hex = HEX_ERR;  return "Sync failed";
        case TSTATE_SKIPPED:        *hex = HEX_CART; return "Manual sync";
        default:                    *hex = 0;        return NULL;
    }
}

static bool is_cart(const TitleInfo *t) {
    return t->media_type == MEDIATYPE_GAME_CARD;
}

static const char *system_tag(const TitleInfo *t, u32 *hex) {
    if (is_cart(t)) {
        *hex = HEX_CART;
        return "CART";
    }
    if (t->is_nds) {
        *hex = HEX_NDS;
        return "NDS";
    }
    *hex = HEX_DIM;
    return "3DS";
}

static void kv(float x, float y, float label_w, float w, const char *label, const char *value, u32 color) {
    gui_text(x, y, GUI_S_SMALL, gui_rgb(HEX_MUTED), GUI_LEFT, label);
    gui_text_fit(x + label_w, y, GUI_S_SMALL, gui_rgb(color), GUI_LEFT, w - label_w, value);
}

static void draw_status_toast(const char *status, UiTone tone) {
    if (!status || !status[0]) return;
    float y = 190, h = 22;
    u32 hex = ui_tone_hex(tone);
    gui_rrect(8, y, GUI_TOP_W - 16, h, 6, gui_rgb(HEX_BG2));
    gui_rrect(8, y, 4, h, 2, gui_rgb(hex));
    gui_text_mid(20, y, h, GUI_S_SMALL, gui_rgb(HEX_TEXT), GUI_LEFT, GUI_TOP_W - 40, status);
}

void ui_draw_saves_top(const SavesView *v) {
    ui_tab_header(UI_TAB_SAVES);
    static const GuiHint hints[] = {
        { "SELECT", "All/3DS/NDS" }, { "LR", "Page" }, { "START", "Exit" },
    };

    if (v->count == 0 || v->selected < 0 || v->selected >= v->count) {
        gui_panel(8, 34, GUI_TOP_W - 16, 148);
        gui_text(GUI_TOP_W / 2.0f, 74, GUI_S_TITLE, gui_rgb(HEX_TEXT), GUI_CENTER, "No saves here");
        gui_text(GUI_TOP_W / 2.0f, 102, GUI_S_SMALL, gui_rgb(HEX_DIM), GUI_CENTER,
                 v->view_mode == VIEW_ALL ? "No titles with save data were found."
                                          : "Nothing in this view. Press SELECT to switch.");
        gui_text(GUI_TOP_W / 2.0f, 120, GUI_S_SMALL, gui_rgb(HEX_DIM), GUI_CENTER,
                 "L / R switch to the game catalog and the settings.");
        draw_status_toast(v->status, v->status_tone);
        gui_footer(hints, 3);
        return;
    }

    const TitleInfo *t = &v->titles[v->filtered[v->selected]];
    float px = 8, py = 32, pw = GUI_TOP_W - 16, ph = 152;
    gui_panel(px, py, pw, ph);

    // Name
    float x = px + 12;
    int lines = gui_text_wrap(x, py + 8, GUI_S_TITLE, gui_rgb(HEX_TEXT), pw - 24, 2, t->name);
    float y = py + 10 + lines * gui_line_h(GUI_S_TITLE);

    // Pills: system, marked, sync state
    u32 hex;
    const char *tag = system_tag(t, &hex);
    float pxx = x;
    pxx += gui_pill(pxx, y, 15, GUI_S_TINY, gui_mix(HEX_PANEL, hex, 0.3f), gui_rgb(hex), tag) + 5;
    if (t->marked) pxx += gui_pill(pxx, y, 15, GUI_S_TINY, gui_rgb(HEX_ACCENT), gui_rgb(HEX_INK), "MARKED") + 5;
    const char *state = state_label(t, &hex);
    if (state) {
        float sw = gui_pill_w(15, GUI_S_TINY, state) + 10;
        gui_rrect(pxx, y, sw, 15, 7.5f, gui_mix(HEX_PANEL, hex, 0.25f));
        gui_circle(pxx + 8, y + 7.5f, 3, gui_rgb(hex));
        gui_text_mid(pxx + 14, y, 15, GUI_S_TINY, gui_rgb(hex), GUI_LEFT, 0, state);
        pxx += sw + 5;
    }
    y += 22;

    // Left column: the title. Right column: the last compare.
    float colw = (pw - 36) / 2, lh = gui_line_h(GUI_S_SMALL) + 1;
    float lx = x, rx = x + colw + 12;
    gui_rect(rx - 7, y, 1, ph - (y - py) - 10, gui_rgb(HEX_LINE));

    kv(lx, y, 54, colw, "Title ID", t->title_id_hex, HEX_TEXT);
    kv(lx, y + lh, 54, colw, "Product", t->product_code[0] ? t->product_code : "-", HEX_TEXT);
    const char *media = t->is_nds ? (is_cart(t) ? "DS game card" : "SD card (.sav)")
                                  : (is_cart(t) ? "Game card" : "SD card");
    kv(lx, y + 2 * lh, 54, colw, "Storage", media, HEX_TEXT);
    if (t->is_nds && !is_cart(t) && t->sav_path[0]) {
        const char *base = strrchr(t->sav_path, '/');
        kv(lx, y + 3 * lh, 54, colw, "Save file", base ? base + 1 : t->sav_path, HEX_DIM);
    } else if (is_cart(t)) {
        gui_text_fit(lx, y + 3 * lh, GUI_S_SMALL, gui_rgb(HEX_CART), GUI_LEFT, colw,
                     "Cartridge: not part of Sync All");
    }

    const SaveDetails *d = v->details;
    if (d) {
        char size[24], line[96];
        if (d->local_exists) {
            format_size(d->local_size, size, sizeof(size));
            snprintf(line, sizeof(line), "%s \xC2\xB7 %.8s", size, d->local_hash);
        } else {
            snprintf(line, sizeof(line), "No save");
        }
        kv(rx, y, 50, colw, "Console", line, HEX_TEXT);
        if (d->server_exists) {
            format_size(d->server_size, size, sizeof(size));
            snprintf(line, sizeof(line), "%s \xC2\xB7 %.8s", size, d->server_hash);
        } else {
            snprintf(line, sizeof(line), "Not uploaded");
        }
        kv(rx, y + lh, 50, colw, "Server", line, HEX_TEXT);
        char date[32];
        format_date(d->server_last_sync, date, sizeof(date));
        kv(rx, y + 2 * lh, 50, colw, "Synced", d->server_exists ? date : "-", HEX_DIM);
        kv(rx, y + 3 * lh, 50, colw, "Last ok", d->has_last_synced ? d->last_synced_hash : "never", HEX_DIM);
    } else {
        gui_text(rx, y, GUI_S_SMALL, gui_rgb(HEX_MUTED), GUI_LEFT, "Save");
        float bx = rx;
        bx += gui_text(bx, y + lh, GUI_S_SMALL, gui_rgb(HEX_DIM), GUI_LEFT, "Press ") ;
        bx += gui_button(bx, y + lh + lh / 2 - 0.5f, "A") + 3;
        gui_text(bx, y + lh, GUI_S_SMALL, gui_rgb(HEX_DIM), GUI_LEFT, "to compare");
        gui_text(rx, y + 2 * lh, GUI_S_SMALL, gui_rgb(HEX_DIM), GUI_LEFT, "with the server copy.");
    }

    draw_status_toast(v->status, v->status_tone);
    gui_footer(hints, 3);
}

static void saves_row(void *ctx, int index, float x, float y, float w, float h, bool selected) {
    const SavesView *v = ctx;
    const TitleInfo *t = &v->titles[v->filtered[index]];
    float cy = y + h / 2;

    // Mark box
    if (t->marked) {
        gui_rrect(x + 9, cy - 5.5f, 11, 11, 3, gui_rgb(selected ? HEX_INK : HEX_ACCENT));
        gui_icon_check(x + 14.5f, cy, 8, gui_rgb(selected ? HEX_ACCENT : HEX_INK));
    } else {
        gui_rrect(x + 9, cy - 5.5f, 11, 11, 3, gui_rgb(selected ? 0x1E8F86 : HEX_LINE));
        gui_rrect(x + 10.5f, cy - 4, 8, 8, 2, gui_rgb(selected ? HEX_ACCENT : HEX_BG));
    }

    // System tag, fixed width
    u32 hex;
    const char *tag = system_tag(t, &hex);
    gui_rrect(x + 26, cy - 6.5f, 30, 13, 6.5f, selected ? gui_rgb(HEX_INK) : gui_mix(HEX_BG, hex, 0.25f));
    gui_text_mid(x + 41, cy - 6.5f, 13, GUI_S_TINY, gui_rgb(hex), GUI_CENTER, 0, tag);

    // Name
    bool conflict = t->in_conflict || t->sync_state == TSTATE_CONFLICT;
    u32 color = selected ? HEX_INK : (conflict ? HEX_ERR : HEX_TEXT);
    gui_text_mid(x + 62, y, h, GUI_S_BODY, gui_rgb(color), GUI_LEFT, w - 62 - 18, t->name);

    // State dot
    if (state_label(t, &hex)) {
        if (selected) gui_circle(x + w - 11, cy, 5, gui_rgb(HEX_INK));
        gui_circle(x + w - 11, cy, 3.5f, gui_rgb(hex));
    }
}

void ui_draw_saves_bottom(const SavesView *v) {
    if (v->count > 0) {
        static UiListAnim anim;
        ui_list(&anim, 0, SAVES_LIST_Y, GUI_BOT_W, SAVES_ROWS, SAVES_ROW_H,
                v->count, v->selected, v->scroll, saves_row, (void *)v);
    } else {
        gui_text(GUI_BOT_W / 2.0f, 104, GUI_S_BODY, gui_rgb(HEX_DIM), GUI_CENTER, "No saves to show");
    }

    gui_header_bar();
    static const char *const tabs[] = { "All", "3DS", "NDS" };
    float tx = 6 + gui_button(6, GUI_HEADER_H / 2.0f, "SELECT") + 4;
    gui_tabs(tx, 4, 18, tabs, 3, v->view_mode);
    char count[32];
    snprintf(count, sizeof(count), "%d save%s", v->count, v->count == 1 ? "" : "s");
    float right = GUI_BOT_W - 8;
    right -= gui_text_mid(right, 0, GUI_HEADER_H, GUI_S_SMALL, gui_rgb(HEX_DIM), GUI_RIGHT, 0, count) + 6;
    if (v->marked > 0) {
        char marked[24];
        snprintf(marked, sizeof(marked), "%d marked", v->marked);
        float w = gui_pill_w(15, GUI_S_TINY, marked);
        gui_pill(right - w, 5.5f, 15, GUI_S_TINY, gui_rgb(HEX_ACCENT), gui_rgb(HEX_INK), marked);
    }

    static const GuiHint hints[] = {
        { "A", "Sync" }, { "X", "Sync all" }, { "Y", "Details" }, { "B", "Unmark all" },
    };
    gui_footer(hints, v->marked > 0 ? 4 : 3);
}

// ---------------------------------------------------------------------------
// Save compare (details / smart sync / confirm)
// ---------------------------------------------------------------------------

typedef struct {
    const TitleInfo *title;
    const SaveDetails *details;
    SyncAction verdict;
    const char *section;
} CompareView;

static void verdict_text(const SaveDetails *d, SyncAction a, u32 *hex, const char **head, const char **sub) {
    switch (a) {
        case SYNC_ACTION_UPLOAD:
            *hex = HEX_WARN;
            *head = "Upload";
            *sub = !d->server_exists ? "Not on the server yet"
                 : d->has_last_synced ? "Only this console changed since the last sync"
                 : "This console's copy goes to the server";
            break;
        case SYNC_ACTION_DOWNLOAD:
            *hex = HEX_INFO;
            *head = "Download";
            *sub = !d->local_exists ? "No save on this console yet"
                 : d->has_last_synced ? "Only the server changed since the last sync"
                 : "The server's copy replaces this console's";
            break;
        case SYNC_ACTION_CONFLICT:
            *hex = HEX_ERR;
            *head = "Conflict";
            *sub = d->has_last_synced ? "Both this console and the server changed"
                                      : "No sync history: pick which copy wins";
            break;
        default:
            *hex = HEX_OK;
            *head = "Up to date";
            *sub = (!d->local_exists && !d->server_exists) ? "No save anywhere yet"
                                                           : "This console and the server match";
            break;
    }
}

static void save_column(float x, float y, float w, const char *heading, bool exists,
                        int files, u32 size, const char *hash, const char *extra_label,
                        const char *extra, u32 tone) {
    gui_rrect(x, y, w, 76, 6, gui_rgb(HEX_BG2));
    gui_rrect(x, y, w, 3, 1.5f, gui_rgb(tone));
    gui_text(x + 8, y + 6, GUI_S_SMALL, gui_rgb(HEX_DIM), GUI_LEFT, heading);
    float lh = gui_line_h(GUI_S_SMALL);
    if (!exists) {
        gui_text(x + 8, y + 8 + lh, GUI_S_BODY, gui_rgb(HEX_MUTED), GUI_LEFT, "No save");
        return;
    }
    char size_str[24], line[48];
    format_size(size, size_str, sizeof(size_str));
    snprintf(line, sizeof(line), "%s  \xC2\xB7  %d file%s", size_str, files, files == 1 ? "" : "s");
    gui_text_fit(x + 8, y + 6 + lh, GUI_S_BODY, gui_rgb(HEX_TEXT), GUI_LEFT, w - 16, line);
    snprintf(line, sizeof(line), "%.16s", hash);
    gui_text_fit(x + 8, y + 9 + 2 * lh, GUI_S_SMALL, gui_rgb(HEX_DIM), GUI_LEFT, w - 16, line);
    if (extra && extra[0]) {
        char e[64];
        snprintf(e, sizeof(e), "%s %s", extra_label, extra);
        gui_text_fit(x + 8, y + 9 + 3 * lh, GUI_S_SMALL, gui_rgb(HEX_DIM), GUI_LEFT, w - 16, e);
    }
}

static void compare_top(void *ctx) {
    const CompareView *c = ctx;
    const SaveDetails *d = c->details;
    gui_header(c->section);

    gui_text_fit(12, 32, GUI_S_TITLE, gui_rgb(HEX_TEXT), GUI_LEFT, GUI_TOP_W - 24, c->title->name);
    gui_textf(12, 32 + gui_line_h(GUI_S_TITLE), GUI_S_SMALL, gui_rgb(HEX_MUTED), GUI_LEFT,
              "%s%s", c->title->title_id_hex,
              c->title->media_type == MEDIATYPE_GAME_CARD ? "  \xC2\xB7  game card" : "");

    float y = 70, w = (GUI_TOP_W - 16 - 10) / 2.0f;
    char date[32];
    format_date(d->server_last_sync, date, sizeof(date));
    save_column(8, y, w, "This console", d->local_exists, d->local_file_count, d->local_size,
                d->local_hash, NULL, NULL, HEX_WARN);
    save_column(8 + w + 10, y, w, "Server", d->server_exists, d->server_file_count, d->server_size,
                d->server_hash, "Synced", d->server_exists ? date : NULL, HEX_INFO);

    // Last synced hash
    y += 82;
    gui_text(10, y, GUI_S_SMALL, gui_rgb(HEX_MUTED), GUI_LEFT, "Last synced");
    gui_text_fit(84, y, GUI_S_SMALL, gui_rgb(HEX_DIM), GUI_LEFT, GUI_TOP_W - 94,
                 d->has_last_synced ? d->last_synced_hash : "never synced from this console");

    // Verdict banner
    u32 hex;
    const char *head, *sub;
    verdict_text(d, c->verdict, &hex, &head, &sub);
    float by = 176, bh = 34;
    gui_rrect(8, by, GUI_TOP_W - 16, bh, 7, gui_mix(HEX_BG2, hex, 0.18f));
    gui_rrect(8, by, 4, bh, 2, gui_rgb(hex));
    float icx = 28, icy = by + bh / 2;
    gui_circle(icx, icy, 10, gui_rgb(hex));
    switch (c->verdict) {
        case SYNC_ACTION_UPLOAD:   gui_icon_up(icx, icy, 11, gui_rgb(HEX_INK)); break;
        case SYNC_ACTION_DOWNLOAD: gui_icon_down(icx, icy, 11, gui_rgb(HEX_INK)); break;
        case SYNC_ACTION_CONFLICT: gui_text_mid(icx, by, bh, 0.6f, gui_rgb(HEX_INK), GUI_CENTER, 0, "!"); break;
        default:                   gui_icon_check(icx, icy, 11, gui_rgb(HEX_INK)); break;
    }
    gui_text(46, by + 3, GUI_S_BODY, gui_rgb(hex), GUI_LEFT, head);
    gui_text_fit(46, by + 3 + gui_line_h(GUI_S_BODY) - 1, GUI_S_SMALL, gui_rgb(HEX_DIM), GUI_LEFT,
                 GUI_TOP_W - 60, sub);

    // Thin footer so the top screen looks finished
    gui_rect(0, GUI_FOOTER_Y + 8, GUI_TOP_W, GUI_FOOTER_H - 8, gui_rgb(HEX_BG2));
}

static void compare_bottom(void *ctx) {
    (void)ctx;
    gui_header_bar();
}

static u32 run_compare(const TitleInfo *title, const SaveDetails *details, SyncAction verdict,
                       const char *section, UiTone tone, const char *card_title, const char *body,
                       const UiButton *buttons, int count) {
    CompareView view = { title, details, verdict, section };
    UiBackdrop prev = ui_set_backdrop(compare_top, compare_bottom, &view);
    u32 key = ui_dialog(tone, card_title, body, buttons, count);
    ui_restore_backdrop(prev);
    return key;
}

void ui_show_save_details(const TitleInfo *title, const SaveDetails *details) {
    static const UiButton b[] = { { KEY_B | KEY_A, "B", "Close" } };
    run_compare(title, details, sync_decide(details), "Save details", UI_TONE_ACCENT,
                "Save details", UI_DIM "Local copy on the left, server copy on the right.", b, 1);
}

bool ui_confirm_sync(const TitleInfo *title, const SaveDetails *details, bool is_upload) {
    UiButton b[] = { { KEY_B, "B", "Cancel" }, { KEY_A, "A", is_upload ? "Upload" : "Download" } };
    u32 key = run_compare(title, details, is_upload ? SYNC_ACTION_UPLOAD : SYNC_ACTION_DOWNLOAD,
                          is_upload ? "Upload" : "Download", is_upload ? UI_TONE_WARN : UI_TONE_INFO,
                          is_upload ? "Upload to the server?" : "Download from the server?",
                          is_upload ? "This console's save replaces the server copy."
                                    : "The server's save replaces the one on this console.",
                          b, 2);
    return (key & KEY_A) != 0;
}

SyncAction ui_confirm_smart_sync(const TitleInfo *title, const SaveDetails *details, SyncAction suggested) {
    if (suggested == SYNC_ACTION_CONFLICT) {
        static const UiButton b[] = {
            { KEY_B, "B", "Cancel" }, { KEY_X, "X", "Download" }, { KEY_A, "A", "Upload" },
        };
        u32 key = run_compare(title, details, suggested, "Smart Sync", UI_TONE_ERR,
                              "Both copies changed",
                              "Pick the copy to keep:\n"
                              UI_DIM "A uploads this console's save (the server\n"
                              UI_DIM "keeps its old copy in the history).\n"
                              UI_DIM "X downloads the server's save to this console.",
                              b, 3);
        if (key & KEY_A) return SYNC_ACTION_UPLOAD;
        if (key & KEY_X) return SYNC_ACTION_DOWNLOAD;
        return SYNC_ACTION_UP_TO_DATE;
    }
    if (suggested == SYNC_ACTION_UP_TO_DATE) {
        static const UiButton b[] = { { KEY_A | KEY_B, "A", "OK" } };
        run_compare(title, details, suggested, "Smart Sync", UI_TONE_OK, "Already in sync",
                    "Nothing to do: this console and the server have the same save.", b, 1);
        return SYNC_ACTION_UP_TO_DATE;
    }
    bool up = (suggested == SYNC_ACTION_UPLOAD);
    UiButton b[] = { { KEY_B, "B", "Cancel" }, { KEY_A, "A", up ? "Upload" : "Download" } };
    u32 key = run_compare(title, details, suggested, "Smart Sync", up ? UI_TONE_WARN : UI_TONE_INFO,
                          up ? "Upload this save?" : "Download this save?",
                          up ? "This console has the newer save. Send it to the server?"
                             : "The server has the newer save. Copy it to this console?",
                          b, 2);
    return (key & KEY_A) ? suggested : SYNC_ACTION_UP_TO_DATE;
}

// ---------------------------------------------------------------------------
// History
// ---------------------------------------------------------------------------

#define HIST_ROWS 9
#define HIST_ROW_H 21

typedef struct {
    const TitleInfo *title;
    const HistoryVersion *versions;
    int count, selected, scroll;
} HistoryView;

static void history_row(void *ctx, int index, float x, float y, float w, float h, bool selected) {
    const HistoryView *v = ctx;
    const HistoryVersion *hv = &v->versions[index];
    char date[32], size[24];
    format_date(hv->timestamp, date, sizeof(date));
    format_size(hv->size, size, sizeof(size));
    u32 text = selected ? HEX_INK : HEX_TEXT, dim = selected ? HEX_INK : HEX_DIM;
    gui_text_mid(x + 10, y, h, GUI_S_BODY, gui_rgb(text), GUI_LEFT, 0, date);
    if (index == 0) gui_text_mid(x + 134, y, h, GUI_S_TINY, gui_rgb(selected ? HEX_INK : HEX_ACCENT2), GUI_LEFT, 0, "NEWEST");
    gui_text_mid(x + w - 70, y, h, GUI_S_SMALL, gui_rgb(dim), GUI_RIGHT, 0, size);
    char files[16];
    snprintf(files, sizeof(files), "%d file%s", hv->file_count, hv->file_count == 1 ? "" : "s");
    gui_text_mid(x + w - 10, y, h, GUI_S_SMALL, gui_rgb(dim), GUI_RIGHT, 0, files);
}

static void history_top(void *ctx) {
    const HistoryView *v = ctx;
    gui_header("History");
    gui_panel(8, 32, GUI_TOP_W - 16, 152);
    gui_text_wrap(20, 40, GUI_S_TITLE, gui_rgb(HEX_TEXT), GUI_TOP_W - 40, 2, v->title->name);
    const HistoryVersion *hv = &v->versions[v->selected];
    char date[32], size[24], files[16], pos[24];
    format_date(hv->timestamp, date, sizeof(date));
    format_size(hv->size, size, sizeof(size));
    snprintf(files, sizeof(files), "%d", hv->file_count);
    snprintf(pos, sizeof(pos), "%d of %d", v->selected + 1, v->count);
    float y = 92, lh = gui_line_h(GUI_S_BODY) + 2;
    kv(20, y, 70, 300, "Version", pos, HEX_TEXT);
    kv(20, y + lh, 70, 300, "Saved", date, HEX_TEXT);
    kv(20, y + 2 * lh, 70, 300, "Size", size, HEX_TEXT);
    kv(20, y + 3 * lh, 70, 300, "Files", files, HEX_TEXT);
    gui_text_fit(20, 190, GUI_S_SMALL, gui_rgb(HEX_DIM), GUI_LEFT, GUI_TOP_W - 40,
                 "Restoring writes this version to the console.");
    static const GuiHint hints[] = { { "LR", "Page" } };
    gui_footer(hints, 1);
}

static void history_bottom(void *ctx) {
    HistoryView *v = ctx;
    static UiListAnim anim;
    ui_list(&anim, 0, GUI_HEADER_H, GUI_BOT_W, HIST_ROWS, HIST_ROW_H, v->count, v->selected,
            v->scroll, history_row, v);
    gui_header_bar();
    gui_text_mid(10, 0, GUI_HEADER_H, GUI_S_BODY, gui_rgb(HEX_TEXT), GUI_LEFT, 0, "Saved versions");
    char n[16];
    snprintf(n, sizeof(n), "%d", v->count);
    gui_text_mid(GUI_BOT_W - 10, 0, GUI_HEADER_H, GUI_S_SMALL, gui_rgb(HEX_DIM), GUI_RIGHT, 0, n);
    static const GuiHint hints[] = { { "A", "Restore" }, { "B", "Back" }, { "UD", "Move" } };
    gui_footer(hints, 3);
}

char *ui_show_history(const TitleInfo *title, HistoryVersion *versions, int version_count) {
    if (version_count <= 0) {
        ui_message(UI_TONE_INFO, "History", "No previous versions of this save on the server.");
        return NULL;
    }
    HistoryView v = { title, versions, version_count, 0, 0 };
    UiBackdrop prev = ui_set_backdrop(history_top, history_bottom, &v);
    char *result = NULL;
    hidScanInput();
    while (aptMainLoop()) {
        gui_begin(true);
        gui_screen(GUI_TOP);
        history_top(&v);
        gui_screen(GUI_BOTTOM);
        history_bottom(&v);
        gui_end();

        hidScanInput();
        u32 down = hidKeysDown(), rep = hidKeysDownRepeat();
        if (rep & KEY_UP) v.selected = (v.selected - 1 + v.count) % v.count;
        if (rep & KEY_DOWN) v.selected = (v.selected + 1) % v.count;
        if (rep & KEY_LEFT) v.selected = v.selected - HIST_ROWS < 0 ? 0 : v.selected - HIST_ROWS;
        if (rep & KEY_RIGHT) v.selected = v.selected + HIST_ROWS >= v.count ? v.count - 1 : v.selected + HIST_ROWS;
        if (down & KEY_TOUCH) {
            int i = ui_list_touch(GUI_HEADER_H, HIST_ROWS, HIST_ROW_H, v.count, v.scroll);
            if (i >= 0) v.selected = i;
        }
        if (v.selected < v.scroll) v.scroll = v.selected;
        if (v.selected >= v.scroll + HIST_ROWS) v.scroll = v.selected - HIST_ROWS + 1;
        if (down & KEY_B) break;
        if (down & KEY_A) {
            result = (char *)malloc(32);
            if (result) {
                strncpy(result, versions[v.selected].timestamp, 31);
                result[31] = '\0';
            }
            break;
        }
    }
    ui_restore_backdrop(prev);
    return result;
}

// ---------------------------------------------------------------------------
// Settings
// ---------------------------------------------------------------------------

#define CFG_ITEMS 6
#define CFG_ROW_H 23
#define CFG_ACTIONS 3   // first entry that is an action, not a value

static const char *const cfg_labels[CFG_ITEMS] = {
    "Server URL", "API key", "NDS ROM folder",
    "Rescan titles", "Refresh catalog", "Check for updates",
};

static const char *const cfg_help[CFG_ITEMS] = {
    "Address of your GameSync server, e.g. http://192.168.1.100:8000",
    "The server's SYNC_API_KEY. Sent with every request.",
    "Where your DS ROMs and .sav files live (TWiLight Menu++ / nds-bootstrap).",
    "Look for installed titles and DS saves again.",
    "Ask the server to rescan its ROM folder, then download the whole game catalog again.",
    "Download and install the latest GameSync CIA from your server.",
};

typedef struct {
    const AppConfig *cfg;
    int selected;
} ConfigView;

static void mask_key(const char *key, char *out, size_t size) {
    size_t len = strlen(key);
    if (len == 0) snprintf(out, size, "(not set)");
    else if (len > 4) snprintf(out, size, "%.4s\xE2\x80\xA2\xE2\x80\xA2\xE2\x80\xA2\xE2\x80\xA2", key);
    else snprintf(out, size, "\xE2\x80\xA2\xE2\x80\xA2\xE2\x80\xA2\xE2\x80\xA2");
}

static void config_top(void *ctx) {
    const ConfigView *v = ctx;
    ui_tab_header(UI_TAB_SETTINGS);
    gui_panel(8, 32, GUI_TOP_W - 16, 118);
    float lh = gui_line_h(GUI_S_BODY) + 3, y = 42;
    char key[48];
    mask_key(v->cfg->api_key, key, sizeof(key));
    kv(20, y, 96, GUI_TOP_W - 40, "Server", v->cfg->server_url[0] ? v->cfg->server_url : "(not set)", HEX_TEXT);
    kv(20, y + lh, 96, GUI_TOP_W - 40, "API key", key, HEX_TEXT);
    kv(20, y + 2 * lh, 96, GUI_TOP_W - 40, "NDS ROMs", v->cfg->nds_dir[0] ? v->cfg->nds_dir : "(not set)", HEX_TEXT);
    kv(20, y + 3 * lh, 96, GUI_TOP_W - 40, "Console ID", v->cfg->console_id, HEX_DIM);
    kv(20, y + 4 * lh, 96, GUI_TOP_W - 40, "Config file", CONFIG_PATH, HEX_DIM);

    // Help for the highlighted entry
    gui_rrect(8, 158, GUI_TOP_W - 16, 52, 7, gui_rgb(HEX_BG2));
    gui_rrect(8, 158, 4, 52, 2, gui_rgb(HEX_ACCENT));
    gui_text(20, 163, GUI_S_SMALL, gui_rgb(HEX_ACCENT2), GUI_LEFT, cfg_labels[v->selected]);
    gui_text_wrap(20, 163 + gui_line_h(GUI_S_SMALL), GUI_S_SMALL, gui_rgb(HEX_DIM), GUI_TOP_W - 40, 2,
                  cfg_help[v->selected]);
    static const GuiHint hints[] = { { "UD", "Move" }, { "START", "Exit" } };
    gui_footer(hints, 2);
}

static void config_row(void *ctx, int index, float x, float y, float w, float h, bool selected) {
    const ConfigView *v = ctx;
    u32 text = selected ? HEX_INK : HEX_TEXT;
    if (index == CFG_ACTIONS) gui_rect(x + 10, y, w - 20, 1, gui_rgb(HEX_LINE));
    gui_text_mid(x + 12, y, h, GUI_S_BODY, gui_rgb(text), GUI_LEFT, 0, cfg_labels[index]);
    char value[64] = "";
    if (index == 0) snprintf(value, sizeof(value), "%.63s", v->cfg->server_url);
    else if (index == 1) mask_key(v->cfg->api_key, value, sizeof(value));
    else if (index == 2) snprintf(value, sizeof(value), "%.63s", v->cfg->nds_dir[0] ? v->cfg->nds_dir : "(not set)");
    if (value[0])
        gui_text_mid(x + w - 12, y, h, GUI_S_SMALL, gui_rgb(selected ? HEX_INK : HEX_DIM), GUI_RIGHT, 150, value);
    else
        gui_text_mid(x + w - 14, y, h, GUI_S_BODY, gui_rgb(selected ? HEX_INK : HEX_MUTED), GUI_RIGHT, 0, ">");
}

static void config_bottom(void *ctx) {
    ConfigView *v = ctx;
    static UiListAnim anim;
    ui_list(&anim, 0, GUI_HEADER_H + 4, GUI_BOT_W, CFG_ITEMS, CFG_ROW_H, CFG_ITEMS, v->selected, 0,
            config_row, v);
    gui_header_bar();
    gui_text_mid(10, 0, GUI_HEADER_H, GUI_S_BODY, gui_rgb(HEX_TEXT), GUI_LEFT, 0, "Settings");
    static const GuiHint hints[] = { { "A", "Select" }, { "LR", "First / last" } };
    gui_footer(hints, 2);
}

static void save_settings(const AppConfig *config) {
    if (!config_save(config))
        ui_message(UI_TONE_ERR, "Settings", "Couldn't write the settings to the SD card:\n" UI_DIM CONFIG_PATH);
}

SettingsResult ui_settings_tab(AppConfig *config, SettingsActionFn run_action) {
    static int selected = 0;   // kept while the user visits other tabs
    ConfigView v = { config, selected };
    UiBackdrop prev = ui_set_backdrop(config_top, config_bottom, &v);
    SettingsResult result = SETTINGS_EXIT;  // the app is closing

    hidScanInput();
    while (aptMainLoop()) {
        gui_begin(true);
        gui_screen(GUI_TOP);
        config_top(&v);
        gui_screen(GUI_BOTTOM);
        config_bottom(&v);
        gui_end();

        hidScanInput();
        u32 down = hidKeysDown(), rep = hidKeysDownRepeat();
        if (rep & KEY_UP) v.selected = (v.selected - 1 + CFG_ITEMS) % CFG_ITEMS;
        if (rep & KEY_DOWN) v.selected = (v.selected + 1) % CFG_ITEMS;
        // Page up / down: the whole list fits on one page
        if (rep & KEY_LEFT) v.selected = 0;
        if (rep & KEY_RIGHT) v.selected = CFG_ITEMS - 1;
        if (down & KEY_TOUCH) {
            int i = ui_list_touch(GUI_HEADER_H + 4, CFG_ITEMS, CFG_ROW_H, CFG_ITEMS, 0);
            if (i >= 0) v.selected = i;
        }

        UiNav nav = ui_tab_nav(down);
        if (nav == UI_NAV_PREV) { result = SETTINGS_NAV_PREV; break; }
        if (nav == UI_NAV_NEXT) { result = SETTINGS_NAV_NEXT; break; }
        if (nav == UI_NAV_EXIT) { result = SETTINGS_EXIT; break; }
        if (!(down & KEY_A)) continue;

        SettingsResult action = SETTINGS_EXIT;
        switch (v.selected) {
            case 0:
                if (ui_edit_text("Server URL", "http://192.168.1.100:8000", config->server_url, MAX_URL_LEN))
                    save_settings(config);
                break;
            case 1:
                if (ui_edit_text("API key", "your-api-key", config->api_key, MAX_API_KEY_LEN))
                    save_settings(config);
                break;
            case 2:
                if (ui_edit_text("NDS ROM folder", "sdmc:/roms/nds", config->nds_dir, MAX_PATH_LEN))
                    save_settings(config);
                break;
            case 3: action = SETTINGS_RESCAN; break;
            case 4: action = SETTINGS_REFRESH_CATALOG; break;
            default: action = SETTINGS_UPDATE; break;
        }
        if (action != SETTINGS_EXIT && run_action && run_action(action)) {
            result = SETTINGS_EXIT;
            break;
        }
    }
    selected = v.selected;
    ui_restore_backdrop(prev);
    return result;
}

// ---------------------------------------------------------------------------
// Text entry
// ---------------------------------------------------------------------------

// Characters of the D-pad editor
static const char charset[] = "abcdefghijklmnopqrstuvwxyz0123456789.:/-_ABCDEFGHIJKLMNOPQRSTUVWXYZ@?=&#%+!";
static const int charset_len = sizeof(charset) - 1;

static int charset_index(char c) {
    for (int i = 0; i < charset_len; i++)
        if (charset[i] == c) return i;
    return 0;
}

typedef struct {
    const char *title, *hint;
    const char *text;
    int cursor, len;
} EditView;

static void edit_top(void *ctx) {
    const EditView *e = ctx;
    gui_header(e->title);
    gui_panel(8, 40, GUI_TOP_W - 16, 90);
    gui_text(20, 48, GUI_S_SMALL, gui_rgb(HEX_DIM), GUI_LEFT, e->hint);

    // Text box with a window around the cursor
    float bx = 20, by = 72, bw = GUI_TOP_W - 40, bh = 30;
    gui_rrect(bx, by, bw, bh, 6, gui_rgb(HEX_BG));
    gui_rrect(bx, by + bh - 2, bw, 2, 1, gui_rgb(HEX_ACCENT));
    int start = 0;
    char before[256];
    for (;;) {
        snprintf(before, sizeof(before), "%.*s", e->cursor - start, e->text + start);
        if (gui_text_w(GUI_S_BODY, before) < bw - 30 || start >= e->cursor) break;
        start++;
    }
    float x = bx + 8;
    float ty = by + (bh - gui_line_h(GUI_S_BODY)) / 2 + 1;
    x += gui_text(x, ty, GUI_S_BODY, gui_rgb(HEX_TEXT), GUI_LEFT, before);
    char cur[2] = { e->cursor < e->len ? e->text[e->cursor] : ' ', 0 };
    float cw = gui_text_w(GUI_S_BODY, cur);
    if (cw < 6) cw = 6;
    bool blink = (gui_ticks() / 20) % 2 == 0;
    gui_rrect(x, by + 6, cw + 2, bh - 12, 2, gui_rgb(blink ? HEX_ACCENT : HEX_PANEL_HI));
    gui_text(x + 1, ty, GUI_S_BODY, gui_rgb(HEX_INK), GUI_LEFT, cur);
    if (e->cursor < e->len)
        gui_text_fit(x + cw + 3, ty, GUI_S_BODY, gui_rgb(HEX_TEXT), GUI_LEFT,
                     bx + bw - (x + cw + 3) - 6, e->text + e->cursor + 1);
    gui_text(20, 140, GUI_S_SMALL, gui_rgb(HEX_MUTED), GUI_LEFT,
             "The system keyboard is unavailable: edit with the D-pad.");
    gui_footer(NULL, 0);
}

static void edit_bottom(void *ctx) {
    (void)ctx;
    gui_header_bar();
    gui_text_mid(10, 0, GUI_HEADER_H, GUI_S_BODY, gui_rgb(HEX_TEXT), GUI_LEFT, 0, "D-pad editor");
    static const struct { const char *button, *label; } rows[] = {
        { "LR", "Move the cursor" }, { "UD", "Change the character" }, { "Y", "Insert a character" },
        { "X", "Delete before the cursor" }, { "A", "Confirm" }, { "B", "Cancel" },
    };
    for (int i = 0; i < 6; i++) {
        float y = 40 + i * 26;
        gui_rrect(10, y, GUI_BOT_W - 20, 22, 5, gui_rgb(HEX_PANEL));
        gui_button(20, y + 11, rows[i].button);
        gui_text_mid(44, y, 22, GUI_S_BODY, gui_rgb(HEX_TEXT), GUI_LEFT, 0, rows[i].label);
    }
}

static bool dpad_editor(const char *title, const char *hint, char *buffer, int max_len) {
    char temp[256];
    snprintf(temp, sizeof(temp), "%s", buffer);
    if (max_len > (int)sizeof(temp)) max_len = sizeof(temp);
    EditView e = { title, hint, temp, (int)strlen(temp), (int)strlen(temp) };
    UiBackdrop prev = ui_set_backdrop(edit_top, edit_bottom, &e);
    bool confirmed = false;

    hidScanInput();
    while (aptMainLoop()) {
        gui_begin(true);
        gui_screen(GUI_TOP);
        edit_top(&e);
        gui_screen(GUI_BOTTOM);
        edit_bottom(&e);
        gui_end();

        hidScanInput();
        u32 down = hidKeysDown(), rep = hidKeysDownRepeat();
        if (rep & KEY_LEFT && e.cursor > 0) e.cursor--;
        if (rep & KEY_RIGHT && e.cursor < e.len) e.cursor++;
        if (rep & (KEY_UP | KEY_DOWN)) {
            int dir = (rep & KEY_UP) ? 1 : -1;
            if (e.cursor < e.len) {
                temp[e.cursor] = charset[(charset_index(temp[e.cursor]) + dir + charset_len) % charset_len];
            } else if (e.len < max_len - 1) {
                temp[e.len++] = 'a';
                temp[e.len] = '\0';
            }
        }
        if ((down & KEY_Y) && e.len < max_len - 1) {
            memmove(&temp[e.cursor + 1], &temp[e.cursor], e.len - e.cursor + 1);
            temp[e.cursor++] = 'a';
            e.len++;
        }
        if ((rep & KEY_X) && e.cursor > 0) {
            memmove(&temp[e.cursor - 1], &temp[e.cursor], e.len - e.cursor + 1);
            e.cursor--;
            e.len--;
        }
        if (down & KEY_A) {
            confirmed = true;
            break;
        }
        if (down & KEY_B) break;
    }
    ui_restore_backdrop(prev);
    if (confirmed) snprintf(buffer, max_len, "%s", temp);
    return confirmed;
}

bool ui_edit_text(const char *title, const char *hint, char *buffer, int max_len) {
    char text[MAX_URL_LEN + 1];
    if (max_len > (int)sizeof(text)) max_len = sizeof(text);
    SwkbdState kb;
    swkbdInit(&kb, SWKBD_TYPE_NORMAL, 2, max_len - 1);
    swkbdSetHintText(&kb, hint);
    swkbdSetInitialText(&kb, buffer);
    swkbdSetValidation(&kb, SWKBD_ANYTHING, 0, 0);
    swkbdSetButton(&kb, SWKBD_BUTTON_LEFT, "Cancel", false);
    swkbdSetButton(&kb, SWKBD_BUTTON_RIGHT, "OK", true);
    text[0] = '\0';
    SwkbdButton button = swkbdInputText(&kb, text, sizeof(text));
    if (button == SWKBD_BUTTON_RIGHT) {
        snprintf(buffer, max_len, "%s", text);
        return true;
    }
    if (button == SWKBD_BUTTON_LEFT) return false;
    // No keyboard result: HOME / power pressed, or the applet couldn't run
    SwkbdResult r = swkbdGetResult(&kb);
    if (r == SWKBD_HOMEPRESSED || r == SWKBD_RESETPRESSED || r == SWKBD_POWERPRESSED) return false;
    return dpad_editor(title, hint, buffer, max_len);
}
