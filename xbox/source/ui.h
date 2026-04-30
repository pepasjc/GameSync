// SDL2 + SDL_ttf rendering layer + SDL_GameController input. Designed for a
// 640x480 NTSC display - all colors and sizes assume that resolution.

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
    UI_KEY_LB,
    UI_KEY_RB,
    UI_KEY_START,
    UI_KEY_BACK,
} UiKey;

typedef struct { uint8_t r, g, b, a; } UiColor;

// Theme colors (CRT-friendly, dark green dashboard inspired).
extern const UiColor UI_BG;
extern const UiColor UI_HEADER_BG;
extern const UiColor UI_FOOTER_BG;
extern const UiColor UI_ROW_BG_SEL;
extern const UiColor UI_TEXT;
extern const UiColor UI_TEXT_DIM;
extern const UiColor UI_ACCENT;
extern const UiColor UI_STATUS_OK;
extern const UiColor UI_STATUS_UPLOAD;
extern const UiColor UI_STATUS_DOWNLOAD;
extern const UiColor UI_STATUS_CONFLICT;
extern const UiColor UI_STATUS_NEW;
extern const UiColor UI_STATUS_UNKNOWN;

// 480p NTSC safe area inset.
#define UI_SAFE_X     32
#define UI_SAFE_Y     32
#define UI_W          640
#define UI_H          480

#define UI_FONT_HEADER  22
#define UI_FONT_BODY    18
#define UI_FONT_SMALL   16

// One-time setup. Returns 0 on success. On failure, ``err`` (if non-NULL)
// receives a short message describing which step failed.
int  ui_init(char *err, int err_len);
void ui_shutdown(void);

// Input: pump events + drain edge-triggered button.
void  ui_pump(void);
UiKey ui_poll_key(void);
void  ui_sleep(int ms);

// Drawing primitives.
void ui_clear(UiColor c);
void ui_rect(int x, int y, int w, int h, UiColor c);
void ui_text(int x, int y, const char *s, UiColor c, int font_size);
int  ui_text_width(const char *s, int font_size);
int  ui_text_height(int font_size);
void ui_present(void);

#endif // XBOX_UI_H
