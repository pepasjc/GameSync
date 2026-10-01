#ifndef GCSYNC_GUI_H
#define GCSYNC_GUI_H

/*
 * gui — small 2D drawing kit straight on libogc GX: video/framebuffer setup,
 * frame handling, the GameSync dark palette, rounded panels, text in the
 * console's own IPL ROM font (measure / fit / wrap), pills, GameCube button
 * glyphs, progress bars and the shared header / tab / footer chrome.
 *
 * Design language is shared with the 3DS and DS clients: deep slate
 * background, lighter cards, teal accent, green / amber / blue / red status.
 *
 * Coordinates are a logical 640x480 space (scaled to the EFB on PAL).  Keep
 * text inside GUI_SAFE_* — CRTs overscan 5-8% per edge.
 */

#include "common.h"
#include <gctypes.h>

#define GUI_W 640
#define GUI_H 480

/* Title-safe area (CRT overscan eats the edges). */
#define GUI_SAFE_X 40
#define GUI_SAFE_Y 26
#define GUI_SAFE_R (GUI_W - GUI_SAFE_X)
#define GUI_SAFE_B (GUI_H - GUI_SAFE_Y)

/* Chrome geometry */
#define GUI_HEADER_H  60     /* bar from the top edge; content sits in the safe band */
#define GUI_TABS_Y    66
#define GUI_TABS_H    24
#define GUI_BODY_Y    98
#define GUI_FOOTER_Y  420    /* bar runs to the bottom edge */
#define GUI_BANNER_Y  392
#define GUI_BANNER_H  24

/* Text scales (1.0 = 24 px line) */
#define GUI_S_TITLE 0.92f
#define GUI_S_BODY  0.74f
#define GUI_S_SMALL 0.64f
#define GUI_S_TINY  0.56f

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
#define HEX_GC       0x8E7CC3   /* GameCube indigo, system tag */
#define HEX_INK      0x0F1720   /* dark text on accent fills */

/* Colours passed to drawing calls are RGBA8 packed 0xRRGGBBAA. */
static inline u32 gui_rgb(u32 hex) { return (hex << 8) | 0xFF; }
static inline u32 gui_rgba(u32 hex, u8 a) { return (hex << 8) | a; }
/* Opaque blend of two 0xRRGGBB colours, t = 0 -> a, 1 -> b */
u32 gui_mix(u32 a, u32 b, float t);

typedef enum {
    GUI_LEFT = 0,
    GUI_CENTER,
    GUI_RIGHT,
} GuiAlign;

/* ---- Lifetime and frames ---- */

/* VIDEO + GX + framebuffers + IPL font.  Must run before PAD_Init(): pad
 * sampling is configured from the active video mode. */
void gui_init(void);

/* A frame is fully synchronous (GX_DrawDone before the copy), so there is
 * never a second frame queued behind one still in flight — the condition
 * that wedges the Flipper FIFO on real hardware. */
void gui_begin(void);
/* vsync = false skips the retrace wait — only for progress redraws spaced
 * well apart (>= a few frames), so the back buffer is never on screen. */
void gui_end(bool vsync);

/* ---- Shapes ---- */

void gui_rect(float x, float y, float w, float h, u32 color);
void gui_vgrad(float x, float y, float w, float h, u32 top, u32 bottom);
void gui_rrect(float x, float y, float w, float h, float r, u32 color);
void gui_circle(float cx, float cy, float r, u32 color);
void gui_tri(float x0, float y0, float x1, float y1, float x2, float y2, u32 color);

/* ---- Text (IPL ROM font; UTF-8 in, Latin-1 glyphs out) ---- */

float gui_line_h(float scale);
float gui_text_w(float scale, const char *s);
/* (x, y) = top of the line box.  Returns the drawn width. */
float gui_text(float x, float y, float scale, u32 color, GuiAlign align, const char *s);
float gui_textf(float x, float y, float scale, u32 color, GuiAlign align, const char *fmt, ...)
    __attribute__((format(printf, 6, 7)));
/* Cut to max_w with "..." (max_w <= 0 = no limit) */
float gui_text_fit(float x, float y, float scale, u32 color, GuiAlign align, float max_w, const char *s);
/* Vertically centred in a box of height h */
float gui_text_mid(float x, float y, float h, float scale, u32 color, GuiAlign align, float max_w, const char *s);

#define GUI_WRAP_LINE 160
/* Greedy word wrap ('\n' forces a break) into at most max_lines lines; the
 * last line gets an ellipsis if text remains.  Returns the line count. */
int gui_wrap(const char *s, float scale, float max_w, char lines[][GUI_WRAP_LINE], int max_lines);
int gui_text_wrap(float x, float y, float scale, u32 color, float max_w, int max_lines, const char *s);

/* ---- Widgets ---- */

float gui_pill_w(float h, float scale, const char *label);
float gui_pill(float x, float y, float h, float scale, u32 bg, u32 fg, const char *label);
float gui_pill_outline(float x, float y, float h, float scale, u32 color, u32 fill, const char *label);

/* GameCube button glyph: "A" "B" "X" "Y" "Z" "L" "R" "START" "DPAD" "UD" "LR".
 * (x, cy) = left edge, vertical centre.  Returns the width. */
float gui_button(float x, float cy, const char *button);
float gui_button_w(const char *button);

/* Progress bar; frac < 0 draws a static "busy" striped bar */
void gui_bar(float x, float y, float w, float h, float frac, u32 color);
void gui_scrollbar(float x, float y, float h, int first, int visible, int total);

/* Rounded card with a title strip; tone = title accent (0xRRGGBB) */
void gui_card(float x, float y, float w, float h, const char *title, u32 tone);
void gui_panel(float x, float y, float w, float h);
/* Translucent overlay over everything (behind a modal card) */
void gui_dim(void);

typedef struct {
    const char *button;   /* glyph name, see gui_button */
    const char *label;
} GuiHint;

/* Header bar: logo, "GameSync", "• section", version + status on the right */
void gui_header(const char *section, const SyncState *st);
/* Tab strip across the safe width; returns nothing, highlights `active` */
void gui_tabs(const char *const *labels, int count, int active);
/* Footer bar with button hints laid out left to right */
void gui_footer(const GuiHint *hints, int count);
/* Status banner: accent stripe + text.  tone = stripe colour (0xRRGGBB) */
void gui_banner(float y, const char *text, u32 tone);

#endif /* GCSYNC_GUI_H */
