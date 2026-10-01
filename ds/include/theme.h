#ifndef THEME_H
#define THEME_H

// GameSync DS look: dark slate background, teal accent, status colours, and
// the widgets every screen is built from (header, footer hints, cards, list
// rows, pills, progress bars). Pure drawing on a Surface, no libnds.

#include "gfx.h"

// Palette (shared with the 3DS client's restyle)
#define C_BG_TOP     0x0F1720
#define C_BG_BOTTOM  0x1B2633
#define C_BG         HEX(0x131C27)
#define C_HEADER     HEX(0x0B121A)
#define C_CARD       HEX(0x243244)
#define C_CARD_HI    HEX(0x2C3D52)
#define C_CARD_LINE  HEX(0x34465C)
#define C_INSET      HEX(0x18222E)
#define C_TEXT       HEX(0xE6EDF3)
#define C_TEXT_DIM   HEX(0x8DA2B5)
#define C_TEXT_FAINT HEX(0x5C6F82)
#define C_ON_ACCENT  HEX(0x0B1A1F)
#define C_ACCENT     HEX(0x2EC4B6)
#define C_ACCENT_HI  HEX(0x3DDBD9)
#define C_OK         HEX(0x3FB950)
#define C_WARN       HEX(0xF0B429)
#define C_INFO       HEX(0x58A6FF)
#define C_ERR        HEX(0xF85149)
#define C_GOLD       HEX(0xE3B341)
#define C_PURPLE     HEX(0xA371F7)
#define C_SHADOW     HEX(0x000000)

// Layout
#define HEADER_H 20
#define FOOTER_H 16
#define CONTENT_Y HEADER_H
#define CONTENT_H (SCREEN_H - HEADER_H - FOOTER_H)
#define ROW_H 14

// Dialog / message kinds (title strip colour)
typedef enum {
    KIND_INFO,
    KIND_OK,
    KIND_WARN,
    KIND_ERROR,
    KIND_DOWNLOAD,
    KIND_RA,
} UiKind;

Color theme_kind_color(UiKind kind);

// Button hint for the footer: button "A", "B", "X", "Y", "L", "R",
// "START", "SELECT", "UD" (up/down), "LR" (left/right), "DPAD"
typedef struct {
    const char *button;
    const char *label;
} Hint;

// Status shown in headers
extern bool theme_wifi;

void theme_background(Surface *s);
// App header: logo, "GameSync", screen title, WiFi state and version
void theme_header(Surface *s, const char *title);
// Bottom-screen toolbar: title on the left, optional text on the right
void theme_toolbar(Surface *s, const char *title, const char *right);
// Footer with button hints, NULL-terminated
void theme_footer(Surface *s, const Hint *hints);
int theme_button(Surface *s, int x, int y, const char *button);  // returns width

// Card: rounded panel with a soft shadow. With a title, a header strip.
void theme_card(Surface *s, int x, int y, int w, int h, const char *title, Color title_color);
// Pill/badge: filled rounded label, returns width. small = mono font
int theme_pill(Surface *s, int x, int y, const char *text, Color bg, Color fg);
int theme_pill_width(const char *text);
int theme_pill_outline(Surface *s, int x, int y, const char *text, Color c);
// Progress bar, done/total (total 0 = empty)
void theme_progress(Surface *s, int x, int y, int w, int h, uint32_t done, uint32_t total, Color c);
// Vertical scrollbar for a list of `total` rows showing `visible` from `first`
void theme_scrollbar(Surface *s, int x, int y, int h, int first, int visible, int total);
// List row background: selected rows get the accent bar (dim when the list
// does not have the focus)
void theme_row(Surface *s, int x, int y, int w, int h, bool selected, bool focused);
// Label left, value right, in a card ("Size        64 KB")
void theme_kv(Surface *s, int x, int y, int w, const char *key, const char *value, Color value_color);
void theme_status_dot(Surface *s, int cx, int cy, Color c);
// Full-screen centred message card with text and footer hints
void theme_message(Surface *s, const char *header_title, const char *title, const char *body,
                   UiKind kind, const Hint *hints);

// Icons (my own 1-bit art, see theme.c)
extern const Icon icon_logo, icon_wifi, icon_check, icon_up, icon_down,
    icon_conflict, icon_cloud, icon_trophy, icon_sd, icon_search, icon_gear, icon_key,
    icon_link, icon_refresh, icon_box, icon_info, icon_dot, icon_right, icon_left;

#endif
