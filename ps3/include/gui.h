#ifndef PS3SYNC_GUI_H
#define PS3SYNC_GUI_H

/*
 * Small drawing kit on top of SDL + SDL_gfx: the GameSync dark palette,
 * rounded panels, anti-aliased text, pills, PlayStation button glyphs,
 * progress bars and the shared header / footer chrome.
 *
 * The design language is shared with the 3DS and DS clients: deep slate
 * background, lighter cards, teal accent, green / amber / blue / red
 * status colours.
 *
 * Every coordinate is in a logical 1280x720 space.  gui_init() opens the
 * video mode at the console's own output resolution (720p, 1080p, ...) and
 * everything is scaled to it, so text stays crisp at any resolution.
 *
 * Text uses the PS3's own system font (Rodin, read at runtime from
 * /dev_flash/data/font — nothing is redistributed).  If it cannot be
 * loaded, an enlarged copy of SDL_gfx's built-in 8x8 font is used instead.
 */

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define GUI_W 1280
#define GUI_H 720

/* Title-safe layout */
#define GUI_MARGIN_X   48
#define GUI_HEADER_Y   22
#define GUI_HEADER_H   46
#define GUI_CONTENT_Y  80
#define GUI_FOOTER_H   40
#define GUI_FOOTER_Y   (GUI_H - 22 - GUI_FOOTER_H)

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
#define HEX_PS3       0x9FB4FF
#define HEX_PS1       0xC678DD
#define HEX_INK       0x0F1720   /* dark text on accent fills */

/* Opaque blend of two 0xRRGGBB colours, t = 0 -> a, 1 -> b */
uint32_t gui_mix(uint32_t a, uint32_t b, float t);

typedef enum {
    GUI_F_SMALL = 0,   /* 15 px  secondary text, hints */
    GUI_F_BODY,        /* 18 px  list rows, values */
    GUI_F_BOLD,        /* 18 px  bold labels */
    GUI_F_TITLE,       /* 22 px  bold, card titles */
    GUI_F_HUGE,        /* 28 px  bold, detail headline */
    GUI_F_COUNT
} GuiFont;

typedef enum {
    GUI_LEFT = 0,
    GUI_CENTER,
    GUI_RIGHT,
} GuiAlign;

/* ---- Lifetime and frames ---- */

bool gui_init(char *error_buf, size_t error_buf_size);
void gui_shutdown(void);
/* True when the system font loaded (false = bitmap fallback) */
bool gui_has_system_font(void);

/* Clear to the background gradient */
void gui_clear(void);
/* Push the frame to the screen.  Full screens pass remember = true so the
 * frame becomes the backdrop modal dialogs are drawn over. */
void gui_present(bool remember);
/* Restore the last remembered screen, dimmed, as a modal backdrop */
void gui_backdrop(void);
/* Increments on every gui_present(); lets a screen that is redrawn in a
 * polling loop skip frames when nothing changed and nothing else drew. */
unsigned gui_frame_id(void);

/* ---- Shapes ---- */

void gui_rect(int x, int y, int w, int h, uint32_t color);
void gui_rect_a(int x, int y, int w, int h, uint32_t color, int alpha);
void gui_rrect(int x, int y, int w, int h, int r, uint32_t color);
void gui_rrect_outline(int x, int y, int w, int h, int r, uint32_t color);
void gui_circle(int cx, int cy, int r, uint32_t color);
void gui_line(int x1, int y1, int x2, int y2, int thick, uint32_t color);
void gui_tri(int x1, int y1, int x2, int y2, int x3, int y3, uint32_t color);

/* ---- Text (UTF-8, Latin-1 range) ---- */

int gui_line_h(GuiFont font);
int gui_text_w(GuiFont font, const char *s);
/* (x, y) = top of the line box.  Returns the drawn width. */
int gui_text(int x, int y, GuiFont font, uint32_t color, GuiAlign align, const char *s);
int gui_textf(int x, int y, GuiFont font, uint32_t color, GuiAlign align, const char *fmt, ...)
    __attribute__((format(printf, 6, 7)));
/* Cut to max_w with an ellipsis */
int gui_text_fit(int x, int y, GuiFont font, uint32_t color, GuiAlign align, int max_w, const char *s);
/* Vertically centred in a box of height h */
int gui_text_mid(int x, int y, int h, GuiFont font, uint32_t color, GuiAlign align, int max_w, const char *s);

#define GUI_WRAP_LINE 192
/* Greedy word wrap ('\n' forces a break, long words break anywhere) into
 * at most max_lines lines; the last line gets an ellipsis if text remains.
 * Returns the number of lines. */
int gui_wrap(const char *s, GuiFont font, int max_w, char lines[][GUI_WRAP_LINE], int max_lines);
/* Wrap and draw; returns the number of lines drawn */
int gui_text_wrap(int x, int y, GuiFont font, uint32_t color, int max_w, int max_lines, const char *s);

/* ---- Widgets ---- */

int gui_pill_w(int h, GuiFont font, const char *label);
/* Filled rounded label of height h; returns its width */
int gui_pill(int x, int y, int h, GuiFont font, uint32_t bg, uint32_t fg, const char *label);
/* Tinted tag: dark fill derived from `color`, text in `color` */
int gui_tag(int x, int y, int h, uint32_t color, const char *label);

/* PlayStation button glyph: "CROSS" "CIRCLE" "SQUARE" "TRIANGLE" "L1" "R1"
 * "L2" "R2" "L3" "R3" "START" "SELECT" "DPAD" "UD" "LR".  (x, cy) = left
 * edge and vertical centre.  Returns the width. */
int gui_button(int x, int cy, const char *button);
int gui_button_w(const char *button);

/* Progress bar, frac in 0..1 */
void gui_bar(int x, int y, int w, int h, float frac, uint32_t color);
/* Same, with an explicit track colour (e.g. on top of a selection bar) */
void gui_bar_ex(int x, int y, int w, int h, float frac, uint32_t track, uint32_t color);
void gui_scrollbar(int x, int y, int h, int first, int visible, int total);

/* Rounded card with a title strip; tone = title accent colour */
void gui_card(int x, int y, int w, int h, const char *title, uint32_t tone);
void gui_panel(int x, int y, int w, int h);
/* Card with a coloured bar on its left edge (status / info banners) */
void gui_banner(int x, int y, int w, int h, uint32_t tone, const char *text);

/* Header: logo + "GameSync" + "• section".  Returns the right edge of the
 * title so callers can place tabs after it. */
int  gui_header(const char *section);
/* Version and server-status pill, right-aligned in the header */
void gui_header_status(bool online);
/* Segmented tabs; returns the right edge */
int  gui_tabs(int x, int y, int h, const char *const *labels, int count, int active);

typedef struct {
    const char *button;   /* glyph name, see gui_button */
    const char *label;
} GuiHint;

/* Footer bar with button hints, laid out left to right */
void gui_footer(const GuiHint *hints, int count);
/* Inline hint row (glyph + label pairs) at (x, cy); returns the right edge */
int  gui_hints(int x, int cy, const GuiHint *hints, int count, int gap);

#endif /* PS3SYNC_GUI_H */
