#ifndef GAMESYNC_GUI_H
#define GAMESYNC_GUI_H

// Small drawing kit on top of citro2d: frame handling, the GameSync dark
// palette, rounded panels, system-font text (measure / fit / wrap), pills,
// button glyphs, progress bars and the shared header / footer chrome.
//
// The design language is shared with the DS client: deep slate background,
// lighter cards, teal accent, green / amber / blue / red status colours.

#include "common.h"
#include <citro2d.h>

#define GUI_TOP 0
#define GUI_BOTTOM 1

#define GUI_TOP_W 400
#define GUI_BOT_W 320
#define GUI_H 240

#define GUI_HEADER_H 26
#define GUI_FOOTER_H 22
#define GUI_FOOTER_Y (GUI_H - GUI_FOOTER_H)

// Text scales
#define GUI_S_TITLE 0.62f
#define GUI_S_BODY 0.50f
#define GUI_S_SMALL 0.43f
#define GUI_S_TINY 0.37f

// Palette (0xRRGGBB)
#define HEX_BG 0x0F1720
#define HEX_BG2 0x1B2633
#define HEX_PANEL 0x243244
#define HEX_PANEL_HI 0x2C3D52
#define HEX_LINE 0x33465C
#define HEX_TEXT 0xE6EDF3
#define HEX_DIM 0x8DA2B5
#define HEX_MUTED 0x5E7286
#define HEX_ACCENT 0x2EC4B6
#define HEX_ACCENT2 0x3DDBD9
#define HEX_OK 0x3FB950
#define HEX_WARN 0xF0B429
#define HEX_INFO 0x58A6FF
#define HEX_ERR 0xF85149
#define HEX_CART 0x39C5F7
#define HEX_NDS 0xC678DD
#define HEX_RA 0xE5B143
#define HEX_INK 0x0F1720   // dark text on accent fills

static inline u32 gui_rgb(u32 hex) {
    return C2D_Color32((hex >> 16) & 0xFF, (hex >> 8) & 0xFF, hex & 0xFF, 0xFF);
}
static inline u32 gui_rgba(u32 hex, u8 a) {
    return C2D_Color32((hex >> 16) & 0xFF, (hex >> 8) & 0xFF, hex & 0xFF, a);
}
// Opaque blend of two 0xRRGGBB colours, t = 0 -> a, 1 -> b
u32 gui_mix(u32 a, u32 b, float t);

typedef enum {
    GUI_LEFT = 0,
    GUI_CENTER,
    GUI_RIGHT,
} GuiAlign;

// ---------------------------------------------------------------------------
// Lifetime and frames
// ---------------------------------------------------------------------------

// gfxInitDefault + citro3d/citro2d + both screen targets + system font
void gui_init(void);
void gui_exit(void);

// Start a frame. vsync = true paces to 60 fps (interactive loops); false
// draws right away (progress updates in the middle of a transfer).
void gui_begin(bool vsync);
// Select and clear a screen (GUI_TOP / GUI_BOTTOM), draws the background
void gui_screen(int screen);
void gui_end(void);

// Width of the selected screen, frame counter (for animations)
float gui_w(void);
u32 gui_ticks(void);

// Ease `*value` towards `target` (call once per frame)
void gui_ease(float *value, float target, float speed);

// ---------------------------------------------------------------------------
// Shapes
// ---------------------------------------------------------------------------

void gui_rect(float x, float y, float w, float h, u32 color);
void gui_vgrad(float x, float y, float w, float h, u32 top, u32 bottom);
// Rounded rectangle; colour must be opaque (corners overlap)
void gui_rrect(float x, float y, float w, float h, float r, u32 color);
void gui_circle(float cx, float cy, float r, u32 color);
// Small filled arrows / marks, centred on (cx, cy), `s` = size in px
void gui_icon_up(float cx, float cy, float s, u32 color);
void gui_icon_down(float cx, float cy, float s, u32 color);
void gui_icon_check(float cx, float cy, float s, u32 color);

// ---------------------------------------------------------------------------
// Text (system font, UTF-8)
// ---------------------------------------------------------------------------

float gui_line_h(float scale);
float gui_text_w(float scale, const char *s);
// Draws at (x, y) = top of the line box. Returns the drawn width.
float gui_text(float x, float y, float scale, u32 color, GuiAlign align, const char *s);
float gui_textf(float x, float y, float scale, u32 color, GuiAlign align, const char *fmt, ...)
    __attribute__((format(printf, 6, 7)));
// Cut to `max_w` with an ellipsis
float gui_text_fit(float x, float y, float scale, u32 color, GuiAlign align, float max_w, const char *s);
// Vertically centred in a box of height h
float gui_text_mid(float x, float y, float h, float scale, u32 color, GuiAlign align, float max_w, const char *s);

#define GUI_WRAP_LINE 160
// Greedy word wrap of `s` ('\n' forces a break) into at most `max_lines`
// lines of `max_w`; the last line gets an ellipsis if text remains.
// Returns the number of lines.
int gui_wrap(const char *s, float scale, float max_w, char lines[][GUI_WRAP_LINE], int max_lines);
// Wrap and draw; returns the number of lines drawn
int gui_text_wrap(float x, float y, float scale, u32 color, float max_w, int max_lines, const char *s);

// ---------------------------------------------------------------------------
// Widgets
// ---------------------------------------------------------------------------

float gui_pill_w(float h, float scale, const char *label);
// Rounded label of height h; returns its width
float gui_pill(float x, float y, float h, float scale, u32 bg, u32 fg, const char *label);
// Outlined variant (for low-emphasis tags)
float gui_pill_outline(float x, float y, float h, float scale, u32 color, u32 fill, const char *label);

// Button glyph: "A" "B" "X" "Y" "L" "R" "START" "SELECT" "DPAD" "UD" "LR".
// (x, y) is the left edge and the vertical centre. Returns the width.
float gui_button(float x, float cy, const char *button);
float gui_button_w(const char *button);

// Progress bar; frac < 0 draws an animated indeterminate bar
void gui_bar(float x, float y, float w, float h, float frac, u32 color);
void gui_spinner(float cx, float cy, float r, u32 color);
void gui_scrollbar(float x, float y, float h, int first, int visible, int total, float smooth_first);

// Rounded card with a title strip. tone = title accent colour (0xRRGGBB)
void gui_card(float x, float y, float w, float h, const char *title, u32 tone);
// Plain panel (no title)
void gui_panel(float x, float y, float w, float h);
// Translucent overlay over the whole screen (behind a modal card)
void gui_dim(void);

// Header bar: "GameSync" + section title on the left. On the top screen it
// also shows the version, WiFi strength and the server status dot.
void gui_header(const char *section);
// Header bar background only (for custom headers, e.g. tabs)
void gui_header_bar(void);
// WiFi bars + server dot ending at x_right; returns the left edge
float gui_status_icons(float x_right, float cy);

typedef struct {
    const char *button;   // glyph name, see gui_button
    const char *label;
} GuiHint;

// Footer bar with button hints, laid out left to right
void gui_footer(const GuiHint *hints, int count);

// Segmented tabs; returns the right edge
float gui_tabs(float x, float y, float h, const char *const *labels, int count, int active);

#endif // GAMESYNC_GUI_H
