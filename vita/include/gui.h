#ifndef GAMESYNC_GUI_H
#define GAMESYNC_GUI_H

/*
 * Small drawing kit on top of vita2d: frame handling, the GameSync dark
 * palette, rounded panels, system-font text (measure / fit / wrap), pills,
 * PlayStation button glyphs, progress bars and the shared header / footer
 * chrome.
 *
 * Text uses the console's own system font (PGF, falling back to PVF),
 * so nothing is bundled.  The design language is shared with the 3DS and
 * DS clients: deep slate background, lighter cards, teal accent, green /
 * amber / blue / red status colours.
 */

#include <stdbool.h>
#include <stdint.h>

#define GUI_W 960
#define GUI_H 544

#define GUI_HEADER_H 48
#define GUI_FOOTER_H 40
#define GUI_FOOTER_Y (GUI_H - GUI_FOOTER_H)

/* Text scales (1.0 = a 22 px line of system font) */
#define GUI_S_HUGE  1.55f
#define GUI_S_TITLE 1.20f
#define GUI_S_BODY  1.00f
#define GUI_S_SMALL 0.86f
#define GUI_S_TINY  0.74f

/* Palette (0xRRGGBB) */
#define HEX_BG       0x0F1720
#define HEX_BG2      0x1B2633
#define HEX_PANEL    0x243244
#define HEX_PANEL_HI 0x2C3D52
#define HEX_LINE     0x33465C
#define HEX_TEXT     0xE6EDF3
#define HEX_DIM      0x8DA2B5
#define HEX_MUTED    0x5E7286
#define HEX_ACCENT   0x2EC4B6
#define HEX_ACCENT2  0x3DDBD9
#define HEX_OK       0x3FB950
#define HEX_WARN     0xF0B429
#define HEX_INFO     0x58A6FF
#define HEX_ERR      0xF85149
#define HEX_VITA     0x4F8DF5
#define HEX_PSP      0xC678DD
#define HEX_PS1      0x9DA9B8
#define HEX_INK      0x0F1720   /* dark text on accent fills */

/* vita2d colours are 0xAABBGGRR */
static inline unsigned int gui_rgba(uint32_t hex, uint8_t a) {
    return ((unsigned int)a << 24) | ((hex & 0xFF) << 16) |
           (hex & 0xFF00) | ((hex >> 16) & 0xFF);
}
static inline unsigned int gui_rgb(uint32_t hex) { return gui_rgba(hex, 0xFF); }

/* Opaque blend of two 0xRRGGBB colours (t = 0 -> a, 1 -> b), as 0xRRGGBB */
uint32_t gui_mix(uint32_t a, uint32_t b, float t);

typedef enum {
    GUI_LEFT = 0,
    GUI_CENTER,
    GUI_RIGHT,
} GuiAlign;

/* ---------------------------------------------------------------------
 * Lifetime and frames
 * ------------------------------------------------------------------- */

void gui_init(void);
void gui_exit(void);

/* Start a frame and paint the background.  vsync = true paces to the
 * display (interactive loops); false presents right away (progress
 * updates in the middle of a transfer, so the network never waits on
 * the display). */
void gui_begin(bool vsync);
void gui_end(void);

/* Frame counter, for animations */
uint32_t gui_ticks(void);

/* Ease `*value` towards `target` (call once per frame) */
void gui_ease(float *value, float target, float speed);

/* Restrict drawing to a rectangle / lift the restriction */
void gui_clip(float x, float y, float w, float h);
void gui_unclip(void);

/* ---------------------------------------------------------------------
 * Shapes
 * ------------------------------------------------------------------- */

void gui_rect(float x, float y, float w, float h, unsigned int color);
void gui_vgrad(float x, float y, float w, float h, unsigned int top, unsigned int bottom);
/* Rounded rectangle; colour must be opaque (corners overlap) */
void gui_rrect(float x, float y, float w, float h, float r, unsigned int color);
void gui_circle(float cx, float cy, float r, unsigned int color);
void gui_triangle(float x0, float y0, float x1, float y1, float x2, float y2,
                  unsigned int color);
/* Line of a given thickness */
void gui_line(float x0, float y0, float x1, float y1, float thick, unsigned int color);
/* Small marks centred on (cx, cy), `s` = size in px */
void gui_icon_up(float cx, float cy, float s, unsigned int color);
void gui_icon_down(float cx, float cy, float s, unsigned int color);
void gui_icon_check(float cx, float cy, float s, unsigned int color);
void gui_icon_cross(float cx, float cy, float s, unsigned int color);

/* ---------------------------------------------------------------------
 * Text (system font, UTF-8)
 * ------------------------------------------------------------------- */

float gui_line_h(float scale);
float gui_text_w(float scale, const char *s);
/* Draws with (x, y) = top of the line box.  Returns the drawn width. */
float gui_text(float x, float y, float scale, unsigned int color, GuiAlign align, const char *s);
float gui_textf(float x, float y, float scale, unsigned int color, GuiAlign align,
                const char *fmt, ...) __attribute__((format(printf, 6, 7)));
/* Cut to `max_w` with an ellipsis */
float gui_text_fit(float x, float y, float scale, unsigned int color, GuiAlign align,
                   float max_w, const char *s);
/* Vertically centred in a box of height h (max_w <= 0: no cut) */
float gui_text_mid(float x, float y, float h, float scale, unsigned int color,
                   GuiAlign align, float max_w, const char *s);

#define GUI_WRAP_LINE 192
/* Greedy word wrap of `s` ('\n' forces a break) into at most `max_lines`
 * lines of `max_w`; the last line gets an ellipsis if text remains.
 * Long words without spaces (paths) break mid-word.  Returns the count. */
int gui_wrap(const char *s, float scale, float max_w, char lines[][GUI_WRAP_LINE], int max_lines);
/* Wrap and draw; returns the number of lines drawn */
int gui_text_wrap(float x, float y, float scale, unsigned int color, float max_w,
                  int max_lines, const char *s);

/* ---------------------------------------------------------------------
 * Widgets
 * ------------------------------------------------------------------- */

float gui_pill_w(float h, float scale, const char *label);
/* Rounded label of height h; returns its width */
float gui_pill(float x, float y, float h, float scale, unsigned int bg, unsigned int fg,
               const char *label);
/* Outlined variant (for low-emphasis tags) */
float gui_pill_outline(float x, float y, float h, float scale, uint32_t hex,
                       uint32_t fill_hex, const char *label);

/* Button glyphs: "CROSS" "CIRCLE" "SQUARE" "TRIANGLE" (PlayStation shapes
 * in their native colours), "L" "R", "START" "SELECT", "DPAD" "UD" "LR".
 * (x, cy) = left edge and vertical centre.  Returns the width. */
float gui_button(float x, float cy, const char *button);
float gui_button_w(const char *button);

/* Progress bar; frac < 0 draws an animated indeterminate bar */
void gui_bar(float x, float y, float w, float h, float frac, unsigned int color);
/* Same, on a custom track colour (e.g. over the selection bar) */
void gui_bar_track(float x, float y, float w, float h, float frac, unsigned int color,
                   unsigned int track);
void gui_spinner(float cx, float cy, float r, unsigned int color);
void gui_scrollbar(float x, float y, float h, int visible, int total, float smooth_first);

/* Plain panel and a card with a title strip (tone = 0xRRGGBB accent) */
void gui_panel(float x, float y, float w, float h);
void gui_card(float x, float y, float w, float h, const char *title, uint32_t tone);
/* Translucent overlay over the whole screen (behind a modal card) */
void gui_dim(void);
/* Banner with a coloured left edge (info / error notices) */
void gui_banner(float x, float y, float w, float h, uint32_t tone, const char *text);

/* Header bar: logo + "GameSync" + section title, version and server status
 * on the right.  online < 0: unknown, 0: offline, 1: server reachable. */
void gui_header(const char *section, int online);

typedef struct {
    const char *button;   /* glyph name, see gui_button */
    const char *label;
} GuiHint;

/* Footer bar with button hints, laid out left to right */
void gui_footer(const GuiHint *hints, int count);
/* Hints in a row starting at x (or ending at x when right = true) */
float gui_hints(float x, float cy, const GuiHint *hints, int count, bool right);

/* Segmented tabs; returns the right edge */
float gui_tabs(float x, float y, float h, float scale, const char *const *labels,
               int count, int active);

#endif /* GAMESYNC_GUI_H */
