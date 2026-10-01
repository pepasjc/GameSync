// SDL2-based drawing kit for the Xbox client. See ui.h for the overview.
//
// nxdk's SDL2 only has the software renderer, so everything here is chosen
// to stay cheap on a 733 MHz CPU: opaque fills skip blending, rounded
// shapes are a handful of scanline spans, and text textures are cached.

#include "ui.h"

#include <SDL.h>
#include <SDL_ttf.h>
#include <hal/debug.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <windows.h>

#ifndef APP_VERSION
#define APP_VERSION "dev"
#endif

static const char ELLIPSIS[] = "...";

// ---------------------------------------------------------------------------
// SDL state
// ---------------------------------------------------------------------------

static SDL_Window   *g_window   = NULL;
static SDL_Renderer *g_renderer = NULL;
static TTF_Font     *g_font_title = NULL;
static TTF_Font     *g_font_body  = NULL;
static TTF_Font     *g_font_small = NULL;
static TTF_Font     *g_font_tiny  = NULL;

static SDL_GameController *g_pad = NULL;
static UiKey               g_pending = UI_KEY_NONE;
static int                 g_axis_x_zone = 0;
static int                 g_axis_y_zone = 0;
static int                 g_trig_l = 0;
static int                 g_trig_r = 0;

// Held-direction auto-repeat.
static UiKey               g_hold_key = UI_KEY_NONE;
static uint32_t            g_hold_since = 0;
static uint32_t            g_hold_last = 0;

#define AXIS_DEADZONE 18000
// Triggers report 0..32767; press above ON, release below OFF.
#define TRIGGER_ON    16000
#define TRIGGER_OFF   8000
#define REPEAT_DELAY_MS  380
#define REPEAT_RATE_MS   75

static TTF_Font *font_for(int size)
{
    if (size >= UI_FONT_TITLE) return g_font_title;
    if (size >= UI_FONT_BODY)  return g_font_body;
    if (size >= UI_FONT_SMALL) return g_font_small;
    return g_font_tiny;
}

// ---------------------------------------------------------------------------
// Colour helpers
// ---------------------------------------------------------------------------

static uint32_t mix(uint32_t a, uint32_t b, float t)
{
    int r  = (int)(((a >> 16) & 0xFF) * (1 - t) + ((b >> 16) & 0xFF) * t);
    int g  = (int)(((a >> 8) & 0xFF) * (1 - t) + ((b >> 8) & 0xFF) * t);
    int bl = (int)((a & 0xFF) * (1 - t) + (b & 0xFF) * t);
    return ((uint32_t)r << 16) | ((uint32_t)g << 8) | (uint32_t)bl;
}

// Opaque fills bypass the per-pixel blend loop of the software renderer.
static void set_color(uint32_t hex, uint8_t alpha)
{
    SDL_SetRenderDrawBlendMode(g_renderer, alpha == 0xFF ? SDL_BLENDMODE_NONE
                                                         : SDL_BLENDMODE_BLEND);
    SDL_SetRenderDrawColor(g_renderer, (hex >> 16) & 0xFF, (hex >> 8) & 0xFF,
                           hex & 0xFF, alpha);
}

static void fill(int x, int y, int w, int h)
{
    if (w <= 0 || h <= 0) return;
    SDL_Rect r = { x, y, w, h };
    SDL_RenderFillRect(g_renderer, &r);
}

// ---------------------------------------------------------------------------
// Text-texture cache. Strings are rasterised once in white and tinted with
// SDL_SetTextureColorMod at draw time; least-recently-used eviction.
// ---------------------------------------------------------------------------

#define CACHE_MAX 128
#define CACHE_KEY 200

typedef struct {
    char         key[CACHE_KEY];    // "<size>|<text>"
    SDL_Texture *texture;
    int          w, h;
    uint32_t     last_used;
} TextEntry;

static TextEntry g_cache[CACHE_MAX];
static uint32_t  g_tick = 0;

static void cache_clear(void)
{
    for (int i = 0; i < CACHE_MAX; i++) {
        if (g_cache[i].texture) {
            SDL_DestroyTexture(g_cache[i].texture);
            g_cache[i].texture = NULL;
        }
        g_cache[i].key[0] = '\0';
    }
}

static SDL_Texture *cache_get(const char *text, int size, int *out_w, int *out_h)
{
    char key[CACHE_KEY];
    int n = snprintf(key, sizeof(key), "%d|%s", size, text);
    // Over-long strings would collide on a truncated key; draw them
    // uncached (the caller fits text to a width, so this is rare).
    int cacheable = n > 0 && n < (int)sizeof(key);

    g_tick++;
    if (cacheable) {
        for (int i = 0; i < CACHE_MAX; i++) {
            if (g_cache[i].texture && strcmp(g_cache[i].key, key) == 0) {
                g_cache[i].last_used = g_tick;
                *out_w = g_cache[i].w;
                *out_h = g_cache[i].h;
                return g_cache[i].texture;
            }
        }
    }

    TTF_Font *f = font_for(size);
    if (!f) return NULL;
    SDL_Color white = { 0xFF, 0xFF, 0xFF, 0xFF };
    SDL_Surface *surf = TTF_RenderUTF8_Blended(f, text, white);
    if (!surf) return NULL;
    SDL_Texture *tex = SDL_CreateTextureFromSurface(g_renderer, surf);
    int w = surf->w, h = surf->h;
    SDL_FreeSurface(surf);
    if (!tex) return NULL;
    SDL_SetTextureBlendMode(tex, SDL_BLENDMODE_BLEND);

    *out_w = w;
    *out_h = h;
    if (!cacheable) return tex;   // caller frees (see draw_run)

    int victim = 0;
    uint32_t oldest = (uint32_t)-1;
    for (int i = 0; i < CACHE_MAX; i++) {
        if (!g_cache[i].texture) { victim = i; break; }
        if (g_cache[i].last_used < oldest) {
            oldest = g_cache[i].last_used;
            victim = i;
        }
    }
    if (g_cache[victim].texture) SDL_DestroyTexture(g_cache[victim].texture);
    g_cache[victim].texture = tex;
    g_cache[victim].w = w;
    g_cache[victim].h = h;
    g_cache[victim].last_used = g_tick;
    snprintf(g_cache[victim].key, sizeof(g_cache[victim].key), "%s", key);
    return tex;
}

static int is_cached(SDL_Texture *tex)
{
    for (int i = 0; i < CACHE_MAX; i++) {
        if (g_cache[i].texture == tex) return 1;
    }
    return 0;
}

// ---------------------------------------------------------------------------
// Input
// ---------------------------------------------------------------------------

static UiKey map_button(int b)
{
    switch (b) {
    case SDL_CONTROLLER_BUTTON_DPAD_UP:    return UI_KEY_UP;
    case SDL_CONTROLLER_BUTTON_DPAD_DOWN:  return UI_KEY_DOWN;
    case SDL_CONTROLLER_BUTTON_DPAD_LEFT:  return UI_KEY_LEFT;
    case SDL_CONTROLLER_BUTTON_DPAD_RIGHT: return UI_KEY_RIGHT;
    case SDL_CONTROLLER_BUTTON_A:          return UI_KEY_A;
    case SDL_CONTROLLER_BUTTON_B:          return UI_KEY_B;
    case SDL_CONTROLLER_BUTTON_X:          return UI_KEY_X;
    case SDL_CONTROLLER_BUTTON_Y:          return UI_KEY_Y;
    // nxdk maps the Duke / Controller S WHITE button to the left
    // shoulder and BLACK to the right one.
    case SDL_CONTROLLER_BUTTON_LEFTSHOULDER:  return UI_KEY_LB;
    case SDL_CONTROLLER_BUTTON_RIGHTSHOULDER: return UI_KEY_RB;
    case SDL_CONTROLLER_BUTTON_START:      return UI_KEY_START;
    case SDL_CONTROLLER_BUTTON_BACK:       return UI_KEY_BACK;
    default:                               return UI_KEY_NONE;
    }
}

static void queue_key(UiKey k)
{
    if (k != UI_KEY_NONE && g_pending == UI_KEY_NONE) {
        g_pending = k;
    }
}

static int axis_zone(int value)
{
    if (value > AXIS_DEADZONE) return 1;
    if (value < -AXIS_DEADZONE) return -1;
    return 0;
}

static void handle_axis(int axis, int value)
{
    int z = axis_zone(value);
    int *state = NULL;
    UiKey neg = UI_KEY_NONE;
    UiKey pos = UI_KEY_NONE;

    switch (axis) {
    case SDL_CONTROLLER_AXIS_LEFTX:
        state = &g_axis_x_zone;
        neg = UI_KEY_LEFT;
        pos = UI_KEY_RIGHT;
        break;
    case SDL_CONTROLLER_AXIS_LEFTY:
        state = &g_axis_y_zone;
        neg = UI_KEY_UP;
        pos = UI_KEY_DOWN;
        break;
    case SDL_CONTROLLER_AXIS_TRIGGERLEFT:
    case SDL_CONTROLLER_AXIS_TRIGGERRIGHT: {
        int *held = axis == SDL_CONTROLLER_AXIS_TRIGGERLEFT ? &g_trig_l : &g_trig_r;
        if (!*held && value > TRIGGER_ON) {
            *held = 1;
            queue_key(axis == SDL_CONTROLLER_AXIS_TRIGGERLEFT ? UI_KEY_LT
                                                              : UI_KEY_RT);
        } else if (*held && value < TRIGGER_OFF) {
            *held = 0;
        }
        return;
    }
    default:
        return;
    }

    if (z == 0) {
        *state = 0;
        return;
    }
    if (*state != z) {
        queue_key(z < 0 ? neg : pos);
    }
    *state = z;
}

// Direction currently held on the D-pad or left stick, if any.
static UiKey held_direction(void)
{
    if (g_pad) {
        if (SDL_GameControllerGetButton(g_pad, SDL_CONTROLLER_BUTTON_DPAD_UP))
            return UI_KEY_UP;
        if (SDL_GameControllerGetButton(g_pad, SDL_CONTROLLER_BUTTON_DPAD_DOWN))
            return UI_KEY_DOWN;
        if (SDL_GameControllerGetButton(g_pad, SDL_CONTROLLER_BUTTON_DPAD_LEFT))
            return UI_KEY_LEFT;
        if (SDL_GameControllerGetButton(g_pad, SDL_CONTROLLER_BUTTON_DPAD_RIGHT))
            return UI_KEY_RIGHT;
    }
    if (g_axis_y_zone) return g_axis_y_zone < 0 ? UI_KEY_UP : UI_KEY_DOWN;
    if (g_axis_x_zone) return g_axis_x_zone < 0 ? UI_KEY_LEFT : UI_KEY_RIGHT;
    return UI_KEY_NONE;
}

// The press itself arrives as an event; this only adds the repeats.
static void hold_repeat(void)
{
    UiKey k = held_direction();
    uint32_t now = (uint32_t)GetTickCount();
    if (k != g_hold_key) {
        g_hold_key = k;
        g_hold_since = now;
        g_hold_last = now;
        return;
    }
    if (k == UI_KEY_NONE) return;
    if ((uint32_t)(now - g_hold_since) < REPEAT_DELAY_MS) return;
    if ((uint32_t)(now - g_hold_last) < REPEAT_RATE_MS) return;
    g_hold_last = now;
    queue_key(k);
}

void ui_pump(void)
{
    SDL_Event e;
    while (SDL_PollEvent(&e)) {
        switch (e.type) {
        case SDL_CONTROLLERDEVICEADDED: {
            SDL_GameController *pad = SDL_GameControllerOpen(e.cdevice.which);
            if (g_pad == NULL) g_pad = pad;
            break;
        }
        case SDL_CONTROLLERDEVICEREMOVED: {
            SDL_GameController *gone =
                SDL_GameControllerFromInstanceID(e.cdevice.which);
            if (g_pad == gone) g_pad = NULL;
            if (gone) SDL_GameControllerClose(gone);
            g_axis_x_zone = 0;
            g_axis_y_zone = 0;
            g_trig_l = 0;
            g_trig_r = 0;
            g_hold_key = UI_KEY_NONE;
            break;
        }
        case SDL_CONTROLLERBUTTONDOWN: {
            UiKey k = map_button(e.cbutton.button);
            queue_key(k);
            break;
        }
        case SDL_CONTROLLERAXISMOTION: {
            handle_axis(e.caxis.axis, e.caxis.value);
            break;
        }
        default:
            break;
        }
    }
    SDL_GameControllerUpdate();
    hold_repeat();
}

UiKey ui_poll_key(void)
{
    UiKey k = g_pending;
    g_pending = UI_KEY_NONE;
    return k;
}

void ui_sleep(int ms)
{
    int waited = 0;
    while (waited < ms) {
        ui_pump();
        int slice = ms - waited;
        if (slice > 16) slice = 16;
        Sleep(slice);
        waited += slice;
    }
}

uint32_t ui_ms(void)
{
    return (uint32_t)GetTickCount();
}

// ---------------------------------------------------------------------------
// Init / shutdown
// ---------------------------------------------------------------------------

int ui_init(char *err, int err_len)
{
    #define UI_ERR(fmt, ...) do { \
        if (err && err_len > 0) snprintf(err, err_len, fmt, ##__VA_ARGS__); \
    } while (0)

    // nxdk's SDL2 doesn't ship every backend. Bring up video + game-
    // controller separately, matching the nxdk sample pattern.
    if (SDL_VideoInit(NULL) != 0) {
        UI_ERR("SDL_VideoInit: %s", SDL_GetError());
        return -1;
    }
    if (SDL_InitSubSystem(SDL_INIT_GAMECONTROLLER) != 0) {
        // Non-fatal: keep going without controller; render still works.
        UI_ERR("(warn) SDL gamepad: %s", SDL_GetError());
    }
    if (TTF_Init() != 0) {
        UI_ERR("TTF_Init: %s", TTF_GetError());
        return -1;
    }

    g_window = SDL_CreateWindow("GameSync", 0, 0, UI_W, UI_H,
                                SDL_WINDOW_SHOWN);
    if (!g_window) {
        UI_ERR("CreateWindow: %s", SDL_GetError());
        return -1;
    }
    // Don't request SDL_RENDERER_ACCELERATED - nxdk's SDL2 build returns
    // "Couldn't find matching render driver" with that flag set. Pass 0
    // and let SDL pick whatever it has (software path is fine at 640x480).
    g_renderer = SDL_CreateRenderer(g_window, -1, 0);
    if (!g_renderer) {
        UI_ERR("CreateRenderer: %s", SDL_GetError());
        return -1;
    }

    const char *font_path = "D:\\font.ttf";
    g_font_title = TTF_OpenFont(font_path, UI_FONT_TITLE);
    g_font_body  = TTF_OpenFont(font_path, UI_FONT_BODY);
    g_font_small = TTF_OpenFont(font_path, UI_FONT_SMALL);
    g_font_tiny  = TTF_OpenFont(font_path, UI_FONT_TINY);
    if (!g_font_title || !g_font_body || !g_font_small || !g_font_tiny) {
        UI_ERR("TTF_OpenFont(%s): %s", font_path, TTF_GetError());
        return -1;
    }
    if (err && err_len > 0) err[0] = '\0';
    return 0;
    #undef UI_ERR
}

void ui_shutdown(void)
{
    cache_clear();
    if (g_font_title) TTF_CloseFont(g_font_title);
    if (g_font_body)  TTF_CloseFont(g_font_body);
    if (g_font_small) TTF_CloseFont(g_font_small);
    if (g_font_tiny)  TTF_CloseFont(g_font_tiny);
    if (g_renderer) SDL_DestroyRenderer(g_renderer);
    if (g_window)   SDL_DestroyWindow(g_window);
    TTF_Quit();
    SDL_Quit();
}

// ---------------------------------------------------------------------------
// Frame + shapes
// ---------------------------------------------------------------------------

void ui_clear(uint32_t hex)
{
    set_color(hex, 0xFF);
    SDL_RenderClear(g_renderer);
}

void ui_present(void)
{
    SDL_RenderPresent(g_renderer);
}

void ui_rect_a(int x, int y, int w, int h, uint32_t hex, uint8_t alpha)
{
    set_color(hex, alpha);
    fill(x, y, w, h);
}

void ui_rect(int x, int y, int w, int h, uint32_t hex)
{
    ui_rect_a(x, y, w, h, hex, 0xFF);
}

void ui_vgrad(int x, int y, int w, int h, uint32_t top, uint32_t bottom)
{
    for (int i = 0; i < h; i++) {
        set_color(mix(top, bottom, h > 1 ? (float)i / (float)(h - 1) : 0), 0xFF);
        fill(x, y + i, w, 1);
    }
}

// One scanline of a shape whose left/right edges sit at fractional x
// positions: solid core plus one partially covered pixel on each side.
static void span(float x0, float x1, int y, uint32_t hex, uint8_t alpha)
{
    if (x1 <= x0) return;
    int ix0 = (int)ceilf(x0);
    int ix1 = (int)floorf(x1);
    if (ix1 < ix0) {
        // Narrower than one pixel.
        set_color(hex, (uint8_t)(alpha * (x1 - x0)));
        fill((int)floorf(x0), y, 1, 1);
        return;
    }
    set_color(hex, alpha);
    fill(ix0, y, ix1 - ix0, 1);
    float cl = (float)ix0 - x0;
    float cr = x1 - (float)ix1;
    if (cl > 0.04f) {
        set_color(hex, (uint8_t)(alpha * cl));
        fill(ix0 - 1, y, 1, 1);
    }
    if (cr > 0.04f) {
        set_color(hex, (uint8_t)(alpha * cr));
        fill(ix1, y, 1, 1);
    }
}

void ui_rrect_a(int x, int y, int w, int h, int r, uint32_t hex, uint8_t alpha)
{
    if (w <= 0 || h <= 0) return;
    if (r > w / 2) r = w / 2;
    if (r > h / 2) r = h / 2;
    if (r < 1) {
        ui_rect_a(x, y, w, h, hex, alpha);
        return;
    }
    set_color(hex, alpha);
    fill(x, y + r, w, h - 2 * r);
    for (int i = 0; i < r; i++) {
        float dy = (float)r - (float)i - 0.5f;
        float inset = (float)r - sqrtf((float)(r * r) - dy * dy);
        span(x + inset, x + w - inset, y + i, hex, alpha);
        span(x + inset, x + w - inset, y + h - 1 - i, hex, alpha);
    }
}

void ui_rrect(int x, int y, int w, int h, int r, uint32_t hex)
{
    ui_rrect_a(x, y, w, h, r, hex, 0xFF);
}

void ui_circle(float cx, float cy, float r, uint32_t hex)
{
    int y0 = (int)floorf(cy - r);
    int y1 = (int)ceilf(cy + r);
    for (int y = y0; y < y1; y++) {
        float dy = (float)y + 0.5f - cy;
        float d2 = r * r - dy * dy;
        if (d2 <= 0) continue;
        float half = sqrtf(d2);
        span(cx - half, cx + half, y, hex, 0xFF);
    }
}

void ui_icon_arrow(int cx, int cy, int s, int right, uint32_t hex)
{
    // Filled triangle as horizontal spans.
    int half = s / 2;
    set_color(hex, 0xFF);
    for (int i = -half; i <= half; i++) {
        int len = half - (i < 0 ? -i : i) + 1;
        if (right) fill(cx - half / 2, cy + i, len, 1);
        else       fill(cx + half / 2 - len + 1, cy + i, len, 1);
    }
}

// ---------------------------------------------------------------------------
// Text
// ---------------------------------------------------------------------------

int ui_line_h(int font)
{
    TTF_Font *f = font_for(font);
    return f ? TTF_FontHeight(f) : font;
}

int ui_text_w(int font, const char *s)
{
    int w = 0, h = 0;
    TTF_Font *f = font_for(font);
    if (!f || !s || !s[0]) return 0;
    if (TTF_SizeUTF8(f, s, &w, &h) != 0) return 0;
    return w;
}

static void draw_run(int x, int y, int font, uint32_t hex, const char *s,
                     int *out_w)
{
    int w = 0, h = 0;
    SDL_Texture *tex = cache_get(s, font, &w, &h);
    *out_w = w;
    if (!tex) return;
    SDL_SetTextureColorMod(tex, (hex >> 16) & 0xFF, (hex >> 8) & 0xFF,
                           hex & 0xFF);
    SDL_Rect dst = { x, y, w, h };
    SDL_RenderCopy(g_renderer, tex, NULL, &dst);
    if (!is_cached(tex)) SDL_DestroyTexture(tex);
}

int ui_text(int x, int y, int font, uint32_t hex, UiAlign align,
            const char *s)
{
    if (!s || !s[0]) return 0;
    int w = ui_text_w(font, s);
    if (align == UI_CENTER) x -= w / 2;
    else if (align == UI_RIGHT) x -= w;
    int drawn = 0;
    draw_run(x, y, font, hex, s, &drawn);
    return w;
}

// Step back to the start of the UTF-8 sequence containing s[i].
static int utf8_floor(const char *s, int i)
{
    while (i > 0 && ((unsigned char)s[i] & 0xC0) == 0x80) i--;
    return i;
}

// Copy the longest prefix of s (up to the first newline) that fits max_w,
// adding an ellipsis when it had to cut. Returns 1 if it cut.
static int fit(const char *s, int font, int max_w, char *out, int size)
{
    int len = 0;
    while (s[len] && s[len] != '\n') len++;
    if (len >= size) len = size - 1;
    memcpy(out, s, len);
    out[len] = '\0';
    if (max_w <= 0 || ui_text_w(font, out) <= max_w) return 0;

    int limit = max_w - ui_text_w(font, ELLIPSIS);
    int lo = 0, hi = len;   // binary search the byte length that fits
    char tmp[256];
    while (lo < hi) {
        int mid = utf8_floor(s, (lo + hi + 1) / 2);
        if (mid <= lo) mid = lo + 1;
        if (mid >= (int)sizeof(tmp)) mid = (int)sizeof(tmp) - 1;
        memcpy(tmp, s, mid);
        tmp[mid] = '\0';
        if (ui_text_w(font, tmp) <= limit) lo = mid;
        else hi = mid - 1;
    }
    lo = utf8_floor(s, lo);
    while (lo > 0 && s[lo - 1] == ' ') lo--;
    if (lo + (int)sizeof(ELLIPSIS) > size) lo = size - (int)sizeof(ELLIPSIS);
    memcpy(out, s, lo);
    memcpy(out + lo, ELLIPSIS, sizeof(ELLIPSIS));
    return 1;
}

int ui_text_fit(int x, int y, int font, uint32_t hex, UiAlign align,
                int max_w, const char *s)
{
    if (!s || !s[0]) return 0;
    char buf[256];
    fit(s, font, max_w, buf, sizeof(buf));
    return ui_text(x, y, font, hex, align, buf);
}

int ui_text_mid(int x, int y, int h, int font, uint32_t hex, UiAlign align,
                int max_w, const char *s)
{
    // Centre the cap height rather than the full line box: looks right for
    // the mostly-uppercase-and-digits labels in pills and buttons.
    TTF_Font *f = font_for(font);
    int ascent = f ? TTF_FontAscent(f) : font;
    int cap = (int)(ascent * 0.70f + 0.5f);
    int ty = y + (h - cap) / 2 - (ascent - cap);
    if (max_w > 0) return ui_text_fit(x, ty, font, hex, align, max_w, s);
    return ui_text(x, ty, font, hex, align, s);
}

#define WRAP_LINE 160

static int wrap(const char *s, int font, int max_w, char lines[][WRAP_LINE],
                int max_lines)
{
    int count = 0;
    const char *p = s;
    while (*p && count < max_lines) {
        // Grow word by word while the line still fits.
        const char *start = p;
        const char *end = p;
        char tmp[WRAP_LINE];
        for (;;) {
            const char *q = end;
            while (*q == ' ') q++;
            while (*q && *q != ' ' && *q != '\n') q++;
            int len = (int)(q - start);
            if (len >= WRAP_LINE) break;
            memcpy(tmp, start, len);
            tmp[len] = '\0';
            if (end != start && ui_text_w(font, tmp) > max_w) break;
            end = q;
            if (!*q || *q == '\n') break;
        }
        if (end == start) {
            // A single word longer than the line: hard cut.
            int len = 0;
            while (start[len] && start[len] != '\n' && len < WRAP_LINE - 1) len++;
            end = start + len;
        }
        int len = (int)(end - start);
        memcpy(lines[count], start, len);
        lines[count][len] = '\0';
        count++;
        p = end;
        if (*p == '\n') p++;
        else while (*p == ' ') p++;
    }
    if (*p && count > 0) {
        char tmp[WRAP_LINE + 8];
        snprintf(tmp, sizeof(tmp), "%s%s", lines[count - 1], ELLIPSIS);
        fit(tmp, font, max_w, lines[count - 1], WRAP_LINE);
    }
    return count;
}

int ui_text_wrap(int x, int y, int font, uint32_t hex, int max_w,
                 int max_lines, const char *s)
{
    char lines[6][WRAP_LINE];
    if (!s || !s[0] || max_lines <= 0) return 0;
    if (max_lines > 6) max_lines = 6;
    int n = wrap(s, font, max_w, lines, max_lines);
    int lh = ui_line_h(font);
    for (int i = 0; i < n; i++) {
        ui_text(x, y + i * lh, font, hex, UI_LEFT, lines[i]);
    }
    return n;
}

// ---------------------------------------------------------------------------
// Widgets
// ---------------------------------------------------------------------------

#define PILL_PAD 7

int ui_pill_w(int font, const char *label)
{
    return ui_text_w(font, label) + PILL_PAD * 2;
}

int ui_pill(int x, int y, int h, int font, uint32_t bg, uint32_t fg,
            const char *label)
{
    int w = ui_pill_w(font, label);
    ui_rrect(x, y, w, h, h / 2, bg);
    ui_text_mid(x + PILL_PAD, y, h, font, fg, UI_LEFT, 0, label);
    return w;
}

#define BTN_R 9.0f   // face-button radius

static int is_face(const char *b)
{
    return b[1] == '\0' && strchr("ABXY", b[0]) != NULL;
}

static int is_dpad(const char *b)
{
    return !strcmp(b, "DPAD") || !strcmp(b, "UD") || !strcmp(b, "LR");
}

int ui_button_w(const char *b)
{
    if (is_face(b)) return (int)(BTN_R * 2);
    if (!strcmp(b, "WHITE") || !strcmp(b, "BLACK")) return (int)(BTN_R * 2);
    if (is_dpad(b)) return 18;
    if (!strcmp(b, "L/R")) return ui_button_w("L") + 3 + ui_button_w("R");
    return ui_text_w(UI_FONT_TINY, b) + 12;
}

int ui_button(int x, int cy, const char *b)
{
    if (is_face(b)) {
        float cx = x + BTN_R;
        uint32_t c = b[0] == 'A' ? UI_HEX_BTN_A :
                     b[0] == 'B' ? UI_HEX_BTN_B :
                     b[0] == 'X' ? UI_HEX_BTN_X : UI_HEX_BTN_Y;
        ui_circle(cx, cy, BTN_R, mix(c, 0x000000, 0.35f));
        ui_circle(cx, cy, BTN_R - 1.5f, c);
        ui_text_mid((int)cx + 1, cy - 9, 18, UI_FONT_TINY, UI_HEX_INK,
                    UI_CENTER, 0, b);
        return (int)(BTN_R * 2);
    }
    if (!strcmp(b, "WHITE") || !strcmp(b, "BLACK")) {
        float cx = x + BTN_R;
        int white = (b[0] == 'W');
        ui_circle(cx, cy, BTN_R, white ? 0x8DA2B5 : 0x5E7286);
        ui_circle(cx, cy, BTN_R - 1.5f, white ? 0xF4F7FA : 0x070A0E);
        return (int)(BTN_R * 2);
    }
    if (is_dpad(b)) {
        uint32_t base = 0x4A5D70, hi = UI_HEX_TEXT;
        int ud = !strcmp(b, "UD"), lr = !strcmp(b, "LR"), all = !ud && !lr;
        ui_rrect(x + 6, cy - 9, 6, 18, 1, (ud || all) ? hi : base);
        ui_rrect(x, cy - 3, 18, 6, 1, (lr || all) ? hi : base);
        if (lr) {
            ui_rect(x + 6, cy - 9, 6, 6, base);
            ui_rect(x + 6, cy + 3, 6, 6, base);
        }
        if (ud) {
            ui_rect(x, cy - 3, 6, 6, base);
            ui_rect(x + 12, cy - 3, 6, 6, base);
        }
        return 18;
    }
    if (!strcmp(b, "L/R")) {
        int lw = ui_button(x, cy, "L");
        return lw + 3 + ui_button(x + lw + 3, cy, "R");
    }
    // Triggers get a tab shape, START / BACK a pill.
    int w = ui_button_w(b);
    int trigger = (b[0] == 'L' || b[0] == 'R') && b[1] == '\0';
    ui_rrect(x, cy - 9, w, 18, trigger ? 4 : 9, 0xAAB8C5);
    ui_text_mid(x + w / 2, cy - 9, 18, UI_FONT_TINY, UI_HEX_INK, UI_CENTER, 0, b);
    return w;
}

void ui_bar(int x, int y, int w, int h, float frac, uint32_t hex)
{
    ui_rrect(x, y, w, h, h / 2, UI_HEX_BG2);
    if (frac < 0) frac = 0;
    if (frac > 1) frac = 1;
    int fw = (int)(w * frac + 0.5f);
    if (fw <= 0) return;
    if (fw < h) fw = h;
    ui_rrect(x, y, fw, h, h / 2, hex);
    if (fw > h) ui_rect_a(x + h / 2, y + 2, fw - h, h / 3, 0xFFFFFF, 0x30);
}

void ui_scrollbar(int x, int y, int h, int first, int visible, int total)
{
    if (total <= visible || total <= 0) return;
    ui_rrect(x, y, 4, h, 2, UI_HEX_BG2);
    int th = h * visible / total;
    if (th < 16) th = 16;
    int ty = y + (int)((float)(h - th) * first / (float)(total - visible));
    if (ty < y) ty = y;
    if (ty > y + h - th) ty = y + h - th;
    ui_rrect(x, ty, 4, th, 2, UI_HEX_DIM);
}

void ui_panel(int x, int y, int w, int h)
{
    ui_rrect(x - 1, y - 1, w + 2, h + 2, 9, 0x0A1118);
    ui_rrect(x, y, w, h, 8, UI_HEX_PANEL);
}

void ui_card(int x, int y, int w, int h, const char *title, uint32_t tone)
{
    ui_rrect_a(x + 4, y + 5, w, h, 9, 0x000000, 0x70);   // drop shadow
    ui_rrect(x - 1, y - 1, w + 2, h + 2, 9, mix(UI_HEX_LINE, tone, 0.45f));
    ui_rrect(x, y, w, h, 8, UI_HEX_PANEL);
    if (title) {
        ui_rrect(x, y, w, UI_CARD_TITLE_H, 8, UI_HEX_PANEL_HI);
        ui_rect(x, y + UI_CARD_TITLE_H - 10, w, 10, UI_HEX_PANEL_HI);
        ui_rect(x, y + UI_CARD_TITLE_H, w, 1, UI_HEX_LINE);
        ui_rrect(x + 12, y + 10, 4, 14, 2, tone);
        ui_text_mid(x + 24, y, UI_CARD_TITLE_H, UI_FONT_BODY, UI_HEX_TEXT,
                    UI_LEFT, w - 36, title);
    }
}

void ui_dim(void)
{
    ui_rect_a(0, 0, UI_W, UI_H, 0x05090D, 0xB4);
}

// ---------------------------------------------------------------------------
// Chrome
// ---------------------------------------------------------------------------

void ui_header(const char *section, const char *right_text, uint32_t dot_hex)
{
    ui_vgrad(0, 0, UI_W, UI_HEADER_H, 0x16202B, UI_HEX_BG2);
    ui_rect(0, UI_HEADER_H - 1, UI_W, 1, UI_HEX_LINE);

    // Logo mark: teal rounded square with a notch.
    int lx = UI_MARGIN_X;
    int cy = UI_MARGIN_Y + (UI_HEADER_H - UI_MARGIN_Y) / 2;
    ui_rrect(lx, cy - 10, 20, 20, 5, UI_HEX_ACCENT);
    ui_rrect(lx + 6, cy - 4, 8, 8, 2, UI_HEX_BG2);
    int x = lx + 29;
    x += ui_text_mid(x, cy - 13, 26, UI_FONT_TITLE, UI_HEX_TEXT, UI_LEFT, 0,
                     "GameSync");
    if (section && section[0]) {
        ui_circle(x + 10.5f, cy + 0.5f, 2.4f, UI_HEX_MUTED);
        ui_text_mid(x + 21, cy - 13, 26, UI_FONT_TITLE, UI_HEX_ACCENT2, UI_LEFT,
                    230, section);
    }

    int rx = UI_W - UI_MARGIN_X;
    ui_circle(rx - 6, cy + 0.5f, 6.5f, mix(UI_HEX_BG2, dot_hex, 0.35f));
    ui_circle(rx - 6, cy + 0.5f, 4.0f, dot_hex);
    rx -= 20;
    if (right_text && right_text[0]) {
        rx -= ui_text_mid(rx, cy - 13, 26, UI_FONT_SMALL, UI_HEX_DIM, UI_RIGHT,
                          0, right_text) + 12;
    }
    ui_text_mid(rx, cy - 13, 26, UI_FONT_SMALL, UI_HEX_MUTED, UI_RIGHT, 0,
                "v" APP_VERSION);
}

void ui_footer(const UiHint *hints, int count)
{
    int y = UI_FOOTER_Y;
    ui_rect(0, y, UI_W, UI_FOOTER_H, UI_HEX_BG2);
    ui_rect(0, y, UI_W, 1, UI_HEX_LINE);
    if (count <= 0) return;
    if (count > 12) count = 12;

    int widths[12], total = 0;
    int avail = UI_W - 2 * UI_MARGIN_X;
    int font = UI_FONT_SMALL;
    for (int pass = 0; pass < 2; pass++) {
        total = 0;
        for (int i = 0; i < count; i++) {
            widths[i] = ui_button_w(hints[i].button) + 5 +
                        ui_text_w(font, hints[i].label);
            total += widths[i];
        }
        if (total + 10 * (count - 1) <= avail) break;
        font = UI_FONT_TINY;   // crowded view: smaller labels
    }
    int gap = count > 1 ? (avail - total) / (count - 1) : 0;
    if (gap > 22) gap = 22;
    if (gap < 6) gap = 6;

    // Footer content sits above the bottom title-safe line.
    int x = UI_MARGIN_X, cy = y + 19;
    for (int i = 0; i < count; i++) {
        int bw = ui_button(x, cy, hints[i].button);
        ui_text_mid(x + bw + 5, cy - 12, 24, font, UI_HEX_DIM, UI_LEFT, 0,
                    hints[i].label);
        x += widths[i] + gap;
    }
}

int ui_tabs(int x, int y, int h, const char *const *labels, int count,
            int active)
{
    int pad = 12, total = 0;
    for (int i = 0; i < count; i++) total += ui_text_w(UI_FONT_SMALL, labels[i]) + pad * 2;
    ui_rrect(x, y, total + 4, h, h / 2, UI_HEX_BG2);
    int cx = x + 2;
    for (int i = 0; i < count; i++) {
        int w = ui_text_w(UI_FONT_SMALL, labels[i]) + pad * 2;
        int on = (i == active);
        if (on) ui_rrect(cx, y + 2, w, h - 4, (h - 4) / 2, UI_HEX_ACCENT);
        ui_text_mid(cx + w / 2, y, h, UI_FONT_SMALL,
                    on ? UI_HEX_INK : UI_HEX_DIM, UI_CENTER, 0, labels[i]);
        cx += w;
    }
    return x + total + 4;
}

void ui_banner(int x, int y, int w, int h, uint32_t tone, const char *text)
{
    ui_rrect(x, y, w, h, 6, UI_HEX_BG2);
    ui_rrect(x, y, 4, h, 2, tone);
    ui_circle(x + 17.5f, y + h / 2.0f, 3.5f, tone);
    ui_text_mid(x + 30, y, h, UI_FONT_SMALL, UI_HEX_TEXT, UI_LEFT, w - 42, text);
}
