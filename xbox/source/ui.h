// SDL2 + SDL_ttf drawing kit and SDL_GameController input for the Xbox
// client.
//
// The look follows the design language every GameSync client shares: deep
// slate background, lighter rounded panels, teal accent, green / amber /
// blue / red status colours, pills for tags, a header bar with the logo and
// a footer hint bar with controller glyphs.
//
// Everything is laid out on a 640x480 canvas. Shapes are drawn with the SDL
// software renderer; rounded corners and circles get one anti-aliased edge
// pixel per scanline. Text is rendered white once per (size, string) and
// tinted at blit time, so colour changes never re-rasterise.

#ifndef XBOX_UI_H
#define XBOX_UI_H

#include <stdint.h>

typedef enum {
    UI_KEY_NONE,
    UI_KEY_UP,
    UI_KEY_DOWN,
    UI_KEY_LEFT,
    UI_KEY_RIGHT,
    UI_KEY_A,
    UI_KEY_B,
    UI_KEY_X,
    UI_KEY_Y,
    UI_KEY_LB,      // WHITE button on an original Xbox pad (unbound)
    UI_KEY_RB,      // BLACK button on an original Xbox pad (unbound)
    UI_KEY_LT,      // left trigger: previous tab
    UI_KEY_RT,      // right trigger: next tab
    UI_KEY_START,
    UI_KEY_BACK,
} UiKey;

// ---------------------------------------------------------------------------
// Palette (0xRRGGBB)
// ---------------------------------------------------------------------------

#define UI_HEX_BG        0x0F1720
#define UI_HEX_BG2       0x1B2633
#define UI_HEX_PANEL     0x243244
#define UI_HEX_PANEL_HI  0x2C3D52
#define UI_HEX_LINE      0x33465C
#define UI_HEX_TEXT      0xE6EDF3
#define UI_HEX_DIM       0x8DA2B5
#define UI_HEX_MUTED     0x5E7286
#define UI_HEX_ACCENT    0x2EC4B6
#define UI_HEX_ACCENT2   0x3DDBD9
#define UI_HEX_OK        0x3FB950
#define UI_HEX_WARN      0xF0B429
#define UI_HEX_INFO      0x58A6FF
#define UI_HEX_ERR       0xF85149
#define UI_HEX_XBOX      0x6CC24A   // platform pill
#define UI_HEX_INK       0x0F1720   // dark text on light fills

// Original Xbox face-button colours.
#define UI_HEX_BTN_A     0x5FBF3F
#define UI_HEX_BTN_B     0xE5483F
#define UI_HEX_BTN_X     0x3D8FE0
#define UI_HEX_BTN_Y     0xF2C230

// ---------------------------------------------------------------------------
// Canvas and chrome geometry
// ---------------------------------------------------------------------------

#define UI_W          640
#define UI_H          480

// Title-safe margins for CRT / TV overscan.
#define UI_MARGIN_X   32
#define UI_MARGIN_Y   20

#define UI_HEADER_H   62
#define UI_FOOTER_H   48
#define UI_FOOTER_Y   (UI_H - UI_FOOTER_H)

// Font sizes (pixels). Only these four are opened.
#define UI_FONT_TITLE   24
#define UI_FONT_BODY    18
#define UI_FONT_SMALL   15
#define UI_FONT_TINY    12

typedef enum {
    UI_LEFT = 0,
    UI_CENTER,
    UI_RIGHT,
} UiAlign;

// ---------------------------------------------------------------------------
// Lifetime and input
// ---------------------------------------------------------------------------

// One-time setup. Returns 0 on success. On failure, ``err`` (if non-NULL)
// receives a short message describing which step failed.
int  ui_init(char *err, int err_len);
void ui_shutdown(void);

// Input: pump events + drain edge-triggered button. Holding a D-pad
// direction (or the left stick) repeats it after a short delay; the
// triggers are edge-triggered with hysteresis.
void  ui_pump(void);
UiKey ui_poll_key(void);
void  ui_sleep(int ms);

// Milliseconds since boot.
uint32_t ui_ms(void);

// ---------------------------------------------------------------------------
// Frame
// ---------------------------------------------------------------------------

void ui_clear(uint32_t hex);
void ui_present(void);

// ---------------------------------------------------------------------------
// Shapes
// ---------------------------------------------------------------------------

void ui_rect(int x, int y, int w, int h, uint32_t hex);
void ui_rect_a(int x, int y, int w, int h, uint32_t hex, uint8_t alpha);
void ui_vgrad(int x, int y, int w, int h, uint32_t top, uint32_t bottom);
void ui_rrect(int x, int y, int w, int h, int r, uint32_t hex);
void ui_rrect_a(int x, int y, int w, int h, int r, uint32_t hex, uint8_t alpha);
void ui_circle(float cx, float cy, float r, uint32_t hex);
// Small marks centred on (cx, cy); s = size in px.
void ui_icon_arrow(int cx, int cy, int s, int right, uint32_t hex);

// ---------------------------------------------------------------------------
// Text
// ---------------------------------------------------------------------------

int ui_line_h(int font);
int ui_text_w(int font, const char *s);
// (x, y) = top of the line box. Returns the drawn width.
int ui_text(int x, int y, int font, uint32_t hex, UiAlign align,
            const char *s);
// Cut to max_w with an ellipsis.
int ui_text_fit(int x, int y, int font, uint32_t hex, UiAlign align,
                int max_w, const char *s);
// Vertically centred in a box of height h (max_w <= 0: no fitting).
int ui_text_mid(int x, int y, int h, int font, uint32_t hex, UiAlign align,
                int max_w, const char *s);
// Greedy word wrap ('\n' forces a break) into at most max_lines lines;
// the last line gets an ellipsis if text remains. Returns lines drawn.
int ui_text_wrap(int x, int y, int font, uint32_t hex, int max_w,
                 int max_lines, const char *s);

// ---------------------------------------------------------------------------
// Widgets
// ---------------------------------------------------------------------------

int  ui_pill_w(int font, const char *label);
// Rounded label of height h; returns its width.
int  ui_pill(int x, int y, int h, int font, uint32_t bg, uint32_t fg,
             const char *label);

// Controller glyph: "A" "B" "X" "Y" "WHITE" "BLACK" "L" "R" "L/R" (both
// triggers) "START" "BACK" "DPAD" "UD" "LR". (x, cy) = left edge and vertical centre. Returns width.
int  ui_button(int x, int cy, const char *button);
int  ui_button_w(const char *button);

// Progress bar; frac is clamped to 0..1.
void ui_bar(int x, int y, int w, int h, float frac, uint32_t hex);
void ui_scrollbar(int x, int y, int h, int first, int visible, int total);
void ui_panel(int x, int y, int w, int h);
// Rounded card with a title strip (tone = colour of the strip mark and
// the border tint). Used for dialogs.
#define UI_CARD_TITLE_H 34
void ui_card(int x, int y, int w, int h, const char *title, uint32_t tone);
// Translucent overlay over the whole screen (behind a modal card).
void ui_dim(void);

// Header bar: logo, "GameSync", "* section", then version, right_text and
// a status dot on the right.
void ui_header(const char *section, const char *right_text, uint32_t dot_hex);

typedef struct {
    const char *button;   // see ui_button()
    const char *label;
} UiHint;

// Footer bar with button hints, spread across the safe width.
void ui_footer(const UiHint *hints, int count);

// Segmented tab strip; returns the right edge.
int  ui_tabs(int x, int y, int h, const char *const *labels, int count,
             int active);

// Status banner: rounded strip with a coloured stripe and dot.
void ui_banner(int x, int y, int w, int h, uint32_t tone, const char *text);

#endif // XBOX_UI_H
