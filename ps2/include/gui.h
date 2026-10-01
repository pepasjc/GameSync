#ifndef PS2SYNC_GUI_H
#define PS2SYNC_GUI_H

/*
 * Small drawing kit on top of gsKit: frame handling, the GameSync dark
 * palette, rounded panels, anti-aliased text from a baked glyph atlas
 * (measure / fit / wrap), pills, PlayStation button glyphs and progress
 * bars.
 *
 * The design language is shared with the 3DS and DS clients: deep slate
 * background, lighter cards, teal accent, green / amber / blue / red status
 * colours.
 *
 * Everything is laid out on a 640x448 canvas.  On PAL consoles the GS
 * frame buffer is 640x512 and the canvas is centred vertically, so callers
 * never have to care which video mode is active.
 */

#include <stdbool.h>
#include <stdint.h>

#include "font_data.h"

#define GUI_W 640
#define GUI_H 448

/* Palette (0xRRGGBB) */
#define HEX_BG        0x0F1720
#define HEX_BG2       0x1B2633
#define HEX_PANEL     0x243244
#define HEX_PANEL_HI  0x2C3D52
#define HEX_LINE      0x33465C
#define HEX_TEXT      0xE6EDF3
#define HEX_DIM       0x8DA2B5
#define HEX_MUTED     0x5E7286
#define HEX_ACCENT    0x2EC4B6
#define HEX_ACCENT2   0x3DDBD9
#define HEX_OK        0x3FB950
#define HEX_WARN      0xF0B429
#define HEX_INFO      0x58A6FF
#define HEX_ERR       0xF85149
#define HEX_PS1       0xC678DD
#define HEX_PS2       0x4F7DF0
#define HEX_INK       0x0F1720   /* dark text on accent fills */

/* PlayStation face-button colours */
#define HEX_BTN_CROSS    0x7CB2E8
#define HEX_BTN_CIRCLE   0xFF6B6B
#define HEX_BTN_SQUARE   0xE58FCB
#define HEX_BTN_TRIANGLE 0x3FD6A8

typedef enum {
    GUI_LEFT = 0,
    GUI_CENTER,
    GUI_RIGHT,
} GuiAlign;

/* ---- Lifetime and frames ---- */

/* Take over the GS (gsKit), upload the glyph atlas.  Safe to call before
 * the IOP reset: gsKit only touches the EE DMA controller and the GS. */
void gui_init(void);

/* Start a frame: clears to the background colour. */
void gui_begin(void);
/* Submit the frame and flip.  vsync=false skips the vertical-blank wait,
 * used for progress refreshes in the middle of a transfer so drawing
 * never stalls the network loop. */
void gui_end(bool vsync);

/* Milliseconds since boot (EE timer). */
uint32_t gui_ms(void);

/* ---- Shapes ---- */

void gui_rect(float x, float y, float w, float h, uint32_t hex);
void gui_rect_a(float x, float y, float w, float h, uint32_t hex, uint8_t alpha);
void gui_vgrad(float x, float y, float w, float h, uint32_t top, uint32_t bottom);
void gui_hgrad(float x, float y, float w, float h, uint32_t left, uint32_t right);
/* Rounded rectangle.  Translucent fills are fine: the pieces never overlap. */
void gui_rrect(float x, float y, float w, float h, float r, uint32_t hex);
void gui_rrect_a(float x, float y, float w, float h, float r, uint32_t hex, uint8_t alpha);
void gui_circle(float cx, float cy, float r, uint32_t hex);
void gui_ring(float cx, float cy, float r, float thick, uint32_t hex, uint32_t inner);
void gui_tri(float x1, float y1, float x2, float y2, float x3, float y3, uint32_t hex);
/* Small marks centred on (cx, cy), s = size in px. */
void gui_icon_check(float cx, float cy, float s, uint32_t hex);
void gui_icon_arrow(float cx, float cy, float s, bool right, uint32_t hex);

/* ---- Text ---- */

int   gui_line_h(FontId f);
float gui_text_w(FontId f, const char *s);
/* (x, y) = top of the line box.  Returns the drawn width. */
float gui_text(float x, float y, FontId f, uint32_t hex, GuiAlign align, const char *s);
float gui_textf(float x, float y, FontId f, uint32_t hex, GuiAlign align, const char *fmt, ...)
    __attribute__((format(printf, 6, 7)));
/* Cut to max_w with an ellipsis. */
float gui_text_fit(float x, float y, FontId f, uint32_t hex, GuiAlign align,
                   float max_w, const char *s);
/* Vertically centred in a box of height h. */
float gui_text_mid(float x, float y, float h, FontId f, uint32_t hex, GuiAlign align,
                   float max_w, const char *s);

#define GUI_WRAP_LINE 160
/* Greedy word wrap ('\n' forces a break) into at most max_lines lines of
 * max_w; the last line gets an ellipsis if text remains.  Returns lines. */
int gui_wrap(const char *s, FontId f, float max_w,
             char lines[][GUI_WRAP_LINE], int max_lines);
/* Wrap and draw; returns the number of lines drawn. */
int gui_text_wrap(float x, float y, FontId f, uint32_t hex, float max_w,
                  int max_lines, const char *s);

/* ---- Widgets ---- */

float gui_pill_w(FontId f, const char *label);
/* Rounded label of height h; returns its width. */
float gui_pill(float x, float y, float h, FontId f, uint32_t bg, uint32_t fg,
               const char *label);

/* Button glyph: "X" (cross), "O" (circle), "S" (square), "T" (triangle),
 * "L1" "R1" "L2" "R2" "START" "SELECT" "DPAD" "UD" "LR".
 * (x, cy) = left edge and vertical centre.  Returns the width. */
float gui_button(float x, float cy, const char *button);
float gui_button_w(const char *button);

/* Progress bar; frac is clamped to 0..1. */
void gui_bar(float x, float y, float w, float h, float frac, uint32_t hex);
void gui_scrollbar(float x, float y, float h, int first, int visible, int total);

/* Rounded card with a title strip (tone = accent colour of the strip). */
void gui_card(float x, float y, float w, float h, const char *title, uint32_t tone);
void gui_panel(float x, float y, float w, float h);
/* Translucent overlay over the whole screen (behind a modal card). */
void gui_dim(void);

/* ---- Chrome shared by every screen ---- */

#define GUI_HEADER_H 44
#define GUI_FOOTER_H 34
#define GUI_FOOTER_Y (GUI_H - GUI_FOOTER_H)
/* Horizontal safe margin (CRT overscan). */
#define GUI_MARGIN   24

typedef struct {
    const char *button;   /* see gui_button() */
    const char *label;
} GuiHint;

/* Header bar: logo, "GameSync", "• section", status text on the right
 * next to a status dot (dot_hex). */
void gui_header(const char *section, const char *right_text, uint32_t dot_hex);
/* Footer hint bar: button glyph pills + labels, spread across the width. */
void gui_footer(const GuiHint *hints, int count);
/* Segmented tab strip; returns the right edge. */
float gui_tabs(float x, float y, float h, const char *const *labels, int count, int active);

#endif /* PS2SYNC_GUI_H */
