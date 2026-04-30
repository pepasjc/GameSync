// SDL2-based rendering for the Xbox client. Uses SDL_ttf for crisp text and
// caches recently-rendered text textures so the per-frame cost stays low.

#include "ui.h"

#include <SDL.h>
#include <SDL_ttf.h>
#include <hal/debug.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <windows.h>

// ---------------------------------------------------------------------------
// Theme
// ---------------------------------------------------------------------------

const UiColor UI_BG               = { 0x0E, 0x2A, 0x1A, 0xFF };  // deep green
const UiColor UI_HEADER_BG        = { 0x14, 0x3C, 0x26, 0xFF };
const UiColor UI_FOOTER_BG        = { 0x10, 0x32, 0x20, 0xFF };
const UiColor UI_ROW_BG_SEL       = { 0x1F, 0x55, 0x36, 0xFF };
const UiColor UI_TEXT             = { 0xF0, 0xF0, 0xE8, 0xFF };
const UiColor UI_TEXT_DIM         = { 0xA8, 0xB4, 0xA8, 0xFF };
const UiColor UI_ACCENT           = { 0x9C, 0xCB, 0x3B, 0xFF };  // xbox lime
const UiColor UI_STATUS_OK        = { 0x7C, 0xFC, 0x7C, 0xFF };
const UiColor UI_STATUS_UPLOAD    = { 0x60, 0xC8, 0xFF, 0xFF };
const UiColor UI_STATUS_DOWNLOAD  = { 0xFF, 0xB4, 0x50, 0xFF };
const UiColor UI_STATUS_CONFLICT  = { 0xFF, 0x60, 0x60, 0xFF };
const UiColor UI_STATUS_NEW       = { 0xFF, 0xDC, 0x50, 0xFF };
const UiColor UI_STATUS_UNKNOWN   = { 0xA0, 0xA0, 0xA0, 0xFF };

// ---------------------------------------------------------------------------
// SDL state
// ---------------------------------------------------------------------------

static SDL_Window   *g_window   = NULL;
static SDL_Renderer *g_renderer = NULL;
static TTF_Font     *g_font_header = NULL;
static TTF_Font     *g_font_body   = NULL;
static TTF_Font     *g_font_small  = NULL;

static SDL_GameController *g_pad = NULL;
static UiKey               g_pending = UI_KEY_NONE;
static int                 g_axis_x_zone = 0;
static int                 g_axis_y_zone = 0;

#define AXIS_DEADZONE 18000

static TTF_Font *font_for(int size)
{
    if (size >= UI_FONT_HEADER) return g_font_header;
    if (size >= UI_FONT_BODY)   return g_font_body;
    return g_font_small;
}

// ---------------------------------------------------------------------------
// Tiny LRU text-texture cache. Saves us from re-rasterising the same string
// every frame; capped at CACHE_MAX entries with ring-buffer eviction.
// ---------------------------------------------------------------------------

#define CACHE_MAX 96
typedef struct {
    char         key[160];      // "<size>|<color_hex>|<text>"
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

static SDL_Texture *cache_get(const char *text, int size, UiColor c,
                              int *out_w, int *out_h)
{
    char key[160];
    snprintf(key, sizeof(key), "%d|%02X%02X%02X|%s",
             size, c.r, c.g, c.b, text);

    g_tick++;

    // Hit?
    for (int i = 0; i < CACHE_MAX; i++) {
        if (g_cache[i].texture && strcmp(g_cache[i].key, key) == 0) {
            g_cache[i].last_used = g_tick;
            *out_w = g_cache[i].w;
            *out_h = g_cache[i].h;
            return g_cache[i].texture;
        }
    }

    // Render fresh.
    TTF_Font *f = font_for(size);
    if (!f) return NULL;
    SDL_Color col = { c.r, c.g, c.b, c.a };
    SDL_Surface *surf = TTF_RenderUTF8_Blended(f, text, col);
    if (!surf) return NULL;
    SDL_Texture *tex = SDL_CreateTextureFromSurface(g_renderer, surf);
    int w = surf->w, h = surf->h;
    SDL_FreeSurface(surf);
    if (!tex) return NULL;

    // Pick a victim slot - first empty, otherwise least-recently-used.
    int victim = -1;
    uint32_t oldest = (uint32_t)-1;
    for (int i = 0; i < CACHE_MAX; i++) {
        if (!g_cache[i].texture) { victim = i; break; }
        if (g_cache[i].last_used < oldest) {
            oldest = g_cache[i].last_used;
            victim = i;
        }
    }
    if (g_cache[victim].texture) {
        SDL_DestroyTexture(g_cache[victim].texture);
    }
    g_cache[victim].texture = tex;
    g_cache[victim].w = w;
    g_cache[victim].h = h;
    g_cache[victim].last_used = g_tick;
    snprintf(g_cache[victim].key, sizeof(g_cache[victim].key), "%s", key);

    *out_w = w;
    *out_h = h;
    return tex;
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

    g_window = SDL_CreateWindow("Save Sync", 0, 0, UI_W, UI_H,
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
    g_font_header = TTF_OpenFont(font_path, UI_FONT_HEADER);
    g_font_body   = TTF_OpenFont(font_path, UI_FONT_BODY);
    g_font_small  = TTF_OpenFont(font_path, UI_FONT_SMALL);
    if (!g_font_header || !g_font_body || !g_font_small) {
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
    if (g_font_header) TTF_CloseFont(g_font_header);
    if (g_font_body)   TTF_CloseFont(g_font_body);
    if (g_font_small)  TTF_CloseFont(g_font_small);
    if (g_renderer) SDL_DestroyRenderer(g_renderer);
    if (g_window)   SDL_DestroyWindow(g_window);
    TTF_Quit();
    SDL_Quit();
}

// ---------------------------------------------------------------------------
// Drawing
// ---------------------------------------------------------------------------

void ui_clear(UiColor c)
{
    SDL_SetRenderDrawColor(g_renderer, c.r, c.g, c.b, c.a);
    SDL_RenderClear(g_renderer);
}

void ui_rect(int x, int y, int w, int h, UiColor c)
{
    SDL_Rect r = { x, y, w, h };
    SDL_SetRenderDrawColor(g_renderer, c.r, c.g, c.b, c.a);
    SDL_RenderFillRect(g_renderer, &r);
}

void ui_text(int x, int y, const char *s, UiColor c, int font_size)
{
    if (!s || !s[0]) return;
    int w = 0, h = 0;
    SDL_Texture *tex = cache_get(s, font_size, c, &w, &h);
    if (!tex) return;
    SDL_Rect dst = { x, y, w, h };
    SDL_RenderCopy(g_renderer, tex, NULL, &dst);
}

int ui_text_width(const char *s, int font_size)
{
    int w = 0, h = 0;
    TTF_Font *f = font_for(font_size);
    if (!f || !s) return 0;
    if (TTF_SizeUTF8(f, s, &w, &h) != 0) return 0;
    return w;
}

int ui_text_height(int font_size)
{
    TTF_Font *f = font_for(font_size);
    return f ? TTF_FontHeight(f) : font_size;
}

void ui_present(void)
{
    SDL_RenderPresent(g_renderer);
}
