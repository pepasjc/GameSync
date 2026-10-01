#ifndef PSPSYNC_GUI_H
#define PSPSYNC_GUI_H

/*
 * Small drawing kit on top of sceGu: frame handling, the GameSync dark
 * palette, rounded panels, anti-aliased text from a pre-rendered atlas
 * (measure / fit / wrap), pills, PSP button glyphs, progress bars and the
 * shared header / footer chrome.
 *
 * The design language is shared with the 3DS and DS clients: deep slate
 * background, lighter cards, teal accent, green / amber / blue / red
 * status colours.  Coordinates are PSP pixels (480x272); the Vita screen
 * is exactly twice that, so layouts port by scaling.
 */

#include <stdbool.h>
#include <stdint.h>

#include "font.h"

#define GUI_W 480
#define GUI_H 272

#define GUI_HEADER_H 24
#define GUI_FOOTER_H 20
#define GUI_FOOTER_Y (GUI_H - GUI_FOOTER_H)

/* Faces */
#define F_SMALL (&font_small)
#define F_BODY  (&font_body)
#define F_BOLD  (&font_bold)
#define F_TITLE (&font_title)
#define F_HUGE  (&font_huge)

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
#define HEX_PSP      0x4F8FE6   /* PSP system tag */
#define HEX_PS1      0x9A8CD9   /* PS1 system tag */
#define HEX_INK      0x0F1720   /* dark text on accent fills */

/* 0xRRGGBB -> GE colour (0xAABBGGRR) */
static inline uint32_t gui_rgba(uint32_t hex, uint8_t a) {
    return ((uint32_t)a << 24) | ((hex & 0xFF) << 16) | (hex & 0xFF00) |
           ((hex >> 16) & 0xFF);
}
static inline uint32_t gui_rgb(uint32_t hex) { return gui_rgba(hex, 0xFF); }
/* Opaque blend of two 0xRRGGBB colours, t = 0 -> a, 1 -> b */
uint32_t gui_mix(uint32_t a, uint32_t b, float t);

typedef enum {
    GUI_LEFT = 0,
    GUI_CENTER,
    GUI_RIGHT,
} GuiAlign;

/* ------------------------------------------------------------------ */
/* Lifetime and frames                                                 */
/* ------------------------------------------------------------------ */

void gui_init(void);
void gui_term(void);

/* Start a frame (clears to the background colour).  gui_end(true) paces
 * to the next vblank (interactive loops); gui_end(false) shows the frame
 * right away (progress updates in the middle of a transfer). */
void gui_begin(void);
void gui_end(bool vsync);

/* Frame counter (animations) and easing of `*value` towards `target`. */
uint32_t gui_ticks(void);
void gui_ease(float *value, float target, float speed);

/* Copy the frame on screen into a spare VRAM buffer, and draw that copy
 * back as a full-screen background (behind modal cards). */
void gui_snapshot(void);
void gui_draw_snapshot(void);

/* Restrict drawing to a rectangle / back to the whole screen */
void gui_clip(float x, float y, float w, float h);
void gui_unclip(void);

/* ------------------------------------------------------------------ */
/* Shapes (colours are GE colours from gui_rgb / gui_rgba)             */
/* ------------------------------------------------------------------ */

void gui_rect(float x, float y, float w, float h, uint32_t color);
void gui_vgrad(float x, float y, float w, float h, uint32_t top, uint32_t bottom);
void gui_rrect(float x, float y, float w, float h, float r, uint32_t color);
void gui_circle(float cx, float cy, float r, uint32_t color);
void gui_ring(float cx, float cy, float r, float thick, uint32_t color);
void gui_line(float x0, float y0, float x1, float y1, float thick, uint32_t color);
void gui_tri(float x0, float y0, float x1, float y1, float x2, float y2, uint32_t color);
void gui_icon_check(float cx, float cy, float s, uint32_t color);

/* ------------------------------------------------------------------ */
/* Text (UTF-8, Latin-1 coverage)                                      */
/* ------------------------------------------------------------------ */

int   gui_line_h(const FontFace *f);
float gui_text_w(const FontFace *f, const char *s);
/* Draws at (x, y) = top of the line box.  Returns the drawn width. */
float gui_text(float x, float y, const FontFace *f, uint32_t color, GuiAlign align, const char *s);
float gui_textf(float x, float y, const FontFace *f, uint32_t color, GuiAlign align, const char *fmt, ...)
    __attribute__((format(printf, 6, 7)));
/* Cut to `max_w` with an ellipsis (max_w <= 0: no limit) */
float gui_text_fit(float x, float y, const FontFace *f, uint32_t color, GuiAlign align, float max_w, const char *s);
/* Vertically centred in a box of height h */
float gui_text_mid(float x, float y, float h, const FontFace *f, uint32_t color, GuiAlign align, float max_w, const char *s);

#define GUI_WRAP_LINE 160
/* Greedy word wrap ('\n' forces a break) into at most `max_lines` lines
 * of `max_w`; the last line gets an ellipsis if text remains.  Returns
 * the number of lines. */
int gui_wrap(const char *s, const FontFace *f, float max_w, char lines[][GUI_WRAP_LINE], int max_lines);
/* Wrap and draw; returns the number of lines drawn. */
int gui_text_wrap(float x, float y, const FontFace *f, uint32_t color, float max_w, int max_lines, const char *s);

/* ------------------------------------------------------------------ */
/* Widgets                                                             */
/* ------------------------------------------------------------------ */

float gui_pill_w(float h, const FontFace *f, const char *label);
/* Rounded label of height h; returns its width */
float gui_pill(float x, float y, float h, const FontFace *f, uint32_t bg, uint32_t fg, const char *label);
/* Outlined variant (low-emphasis tags); colours are 0xRRGGBB */
float gui_pill_outline(float x, float y, float h, const FontFace *f, uint32_t hex, const char *label);

/* PSP button glyph: "CROSS" "CIRCLE" "SQUARE" "TRIANGLE" "L" "R"
 * "L/R" "START" "SELECT" "DPAD" "UD" "LR".  (x, cy) = left edge, vertical
 * centre.  Returns the width. */
float gui_button(float x, float cy, const char *button);
float gui_button_w(const char *button);

/* Progress bar; frac < 0 draws an animated indeterminate bar.  hex is
 * 0xRRGGBB. */
void gui_bar(float x, float y, float w, float h, float frac, uint32_t hex);
void gui_spinner(float cx, float cy, float r, uint32_t hex);
void gui_scrollbar(float x, float y, float h, int visible, int total, float smooth_first);

/* Rounded card with a title strip; tone = title accent (0xRRGGBB) */
void gui_card(float x, float y, float w, float h, const char *title, uint32_t tone);
/* Plain panel (no title) */
void gui_panel(float x, float y, float w, float h);
/* Translucent overlay over the whole screen (behind a modal card) */
void gui_dim(void);

/* Server reachability shown in the header: <0 unknown, 0 offline,
 * >0 online.  WiFi strength is read from apctl while online. */
void gui_set_server_state(int state);

/* Header bar: logo + "GameSync", the top-level views as a tab strip
 * between L and R glyphs (count <= 0: none), version and status icons. */
void gui_header(const char *const *tabs, int count, int active);

typedef struct {
    const char *button;   /* glyph name, see gui_button */
    const char *label;
} GuiHint;

/* Footer bar with button hints, laid out left to right */
void gui_footer(const GuiHint *hints, int count);

/* Segmented tabs; returns the right edge */
float gui_tabs(float x, float y, float h, const char *const *labels, int count, int active);

#endif /* PSPSYNC_GUI_H */
