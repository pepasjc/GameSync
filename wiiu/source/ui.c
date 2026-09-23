/*
 * ui.c — OSScreen UI, mirrored on TV + GamePad, styled after NUSspli's look
 * (dark background, colored header/footer bars, selection highlight bar,
 * in-row progress bar) without taking NUSspli's SDL2 dependency.
 *
 * OSScreen's font is fixed white, but OSScreenPutFontEx only writes the
 * glyph's foreground pixels, so filled rectangles drawn first show through as
 * row backgrounds.  Rectangles are drawn with OSScreenPutPixelEx, which
 * handles double-buffer parity and clipping itself.
 *
 * Geometry facts (per decaf-emu's coreinit OSScreen implementation,
 * https://github.com/decaf-emu/decaf-emu — Cemu's version has different,
 * wrong numbers): OSScreenPutFontEx draws at a fixed pixel origin of
 * (50, 32) with a 12x24 px character cell (12x19 glyph); the TV buffer is
 * 1280x720, the DRC 854x480.  Every rectangle below is derived from those
 * constants so bars line up with the text on both screens.
 *
 * Framebuffers live in MEM1, which ProcUI reclaims whenever the app drops to
 * the background (HOME menu).  ui_acquire_foreground / ui_release_foreground
 * are registered as ProcUI callbacks so the buffers are torn down and rebuilt
 * around every foreground transition — without this the app hangs on HOME.
 */

#include "ui.h"

#include <stdio.h>
#include <stdarg.h>
#include <string.h>

#include <coreinit/cache.h>
#include <coreinit/memdefaultheap.h>
#include <coreinit/memfrmheap.h>
#include <coreinit/memheap.h>
#include <coreinit/screen.h>
#include <proc_ui/procui.h>

/* Text grid: 1 gutter cell (color chip) + UI_COLS text cells, UI_ROWS rows.
 * 50 + (1 + 64) * 12 = 830 px wide, 32 + 18 * 24 = 464 px tall — inside the
 * DRC's 854x480.  The origin and cell size are OSScreenPutFontEx facts, not
 * choices. */
#define UI_ROWS 18
#define UI_COLS 64
#define GRID_CELLS_W (1 + UI_COLS)
#define CELL_W   12
#define CELL_H   24
#define ORIGIN_X 50
#define ORIGIN_Y 32

#define HEADER_ROWS 2   /* title bar + status line */
#define FOOTER_ROWS 2   /* hint line + status line */

#define FRM_HEAP_TAG 0x33445353   /* "3DSS" */

/* Palette (0xRRGGBBAA, the format OSScreenPutPixelEx/ClearBufferEx take). */
#define COL_BG        0x0B1220FF   /* near-black navy                */
#define COL_BAR       0x14365CFF   /* header / footer bar            */
#define COL_HILITE    0x2E6FB8FF   /* selected row                   */
#define COL_ERRBG     0x6E1B1BFF   /* status row background on error */
#define COL_PROG_BG   0x1D3048FF   /* progress trough                */
#define COL_PROG_FG   0x27AE60FF   /* progress fill                  */

static const uint32_t CHIP_COLORS[] = {
    [UI_WHITE]  = 0,            /* no chip */
    [UI_GREEN]  = 0x2ECC71FF,
    [UI_RED]    = 0xE74C3CFF,
    [UI_YELLOW] = 0xF1C40FFF,
    [UI_CYAN]   = 0x1ABC9CFF,
    [UI_BLUE]   = 0x3498DBFF,
    [UI_GREY]   = 0x66788AFF,
};

static char    g_grid[UI_ROWS][UI_COLS + 1];
static uint8_t g_color[UI_ROWS];       /* chip colour index per row */
static bool    g_sel[UI_ROWS];         /* selection highlight per row */
static char    g_status[200];
static bool    g_status_err = false;

/* One optional in-row progress bar per frame (the active download). */
static int      g_prog_row = -1;
static uint32_t g_prog_permille = 0;

static void    *g_buf_tv = NULL, *g_buf_drc = NULL;
static uint32_t g_size_tv = 0, g_size_drc = 0;
static bool     g_foreground = false;
static volatile bool g_repaint_needed = false;

bool ui_consume_repaint_request(void) {
    if (!g_repaint_needed) return false;
    g_repaint_needed = false;
    return true;
}

int ui_rows(void) { return UI_ROWS; }
int ui_cols(void) { return UI_COLS; }
int ui_list_top(void) { return HEADER_ROWS; }
int ui_list_visible(void) {
    int v = UI_ROWS - HEADER_ROWS - FOOTER_ROWS;
    return v < 1 ? 1 : v;
}

/* ---- ProcUI foreground handling ---- */

uint32_t ui_acquire_foreground(void *ctx) {
    (void)ctx;
    if (g_foreground) return 0;

    MEMHeapHandle heap = MEMGetBaseHeapHandle(MEM_BASE_HEAP_MEM1);
    if (!heap) return 0;
    MEMRecordStateForFrmHeap(heap, FRM_HEAP_TAG);

    OSScreenInit();
    g_size_tv  = OSScreenGetBufferSizeEx(SCREEN_TV);
    g_size_drc = OSScreenGetBufferSizeEx(SCREEN_DRC);

    /* OSScreenSetBufferEx wants 0x100-aligned memory. */
    g_buf_tv  = MEMAllocFromFrmHeapEx(heap, g_size_tv,  0x100);
    g_buf_drc = MEMAllocFromFrmHeapEx(heap, g_size_drc, 0x100);
    if (!g_buf_tv || !g_buf_drc) {
        MEMFreeByStateToFrmHeap(heap, FRM_HEAP_TAG);
        g_buf_tv = g_buf_drc = NULL;
        return 0;
    }

    OSScreenSetBufferEx(SCREEN_TV,  g_buf_tv);
    OSScreenSetBufferEx(SCREEN_DRC, g_buf_drc);
    OSScreenEnableEx(SCREEN_TV,  TRUE);
    OSScreenEnableEx(SCREEN_DRC, TRUE);

    /* Both buffers of each screen are freshly allocated MEM1 holding whatever
     * the previous owner left behind.  Clear and flip twice so neither the
     * visible nor the work buffer can show garbage before the first repaint. */
    for (int i = 0; i < 2; i++) {
        OSScreenClearBufferEx(SCREEN_TV,  COL_BG);
        OSScreenClearBufferEx(SCREEN_DRC, COL_BG);
        DCFlushRange(g_buf_tv,  g_size_tv);
        DCFlushRange(g_buf_drc, g_size_drc);
        OSScreenFlipBuffersEx(SCREEN_TV);
        OSScreenFlipBuffersEx(SCREEN_DRC);
    }

    g_foreground = true;
    g_repaint_needed = true;
    return 0;
}

uint32_t ui_release_foreground(void *ctx) {
    (void)ctx;
    if (!g_foreground) return 0;

    OSScreenShutdown();
    MEMHeapHandle heap = MEMGetBaseHeapHandle(MEM_BASE_HEAP_MEM1);
    if (heap) MEMFreeByStateToFrmHeap(heap, FRM_HEAP_TAG);
    g_buf_tv = g_buf_drc = NULL;
    g_foreground = false;
    return 0;
}

void ui_init(void) {
    memset(g_grid, 0, sizeof(g_grid));
    memset(g_color, UI_WHITE, sizeof(g_color));
    memset(g_sel, 0, sizeof(g_sel));

    ProcUIRegisterCallback(PROCUI_CALLBACK_ACQUIRE, ui_acquire_foreground, NULL, 100);
    ProcUIRegisterCallback(PROCUI_CALLBACK_RELEASE, ui_release_foreground, NULL, 100);
    /* EXIT too: MEM1 must go back before ProcUIShutdown() resets the heap,
     * and the exiting transition does not necessarily fire RELEASE first. */
    ProcUIRegisterCallback(PROCUI_CALLBACK_EXIT, ui_release_foreground, NULL, 100);

    /* ProcUIInit leaves us already in the foreground and does not replay the
     * ACQUIRE callback for that initial state — do it by hand. */
    ui_acquire_foreground(NULL);
    ui_clear();
}

void ui_shutdown(void) {
    ui_release_foreground(NULL);
}

/* ---- drawing ---- */

void ui_clear(void) {
    for (int r = 0; r < UI_ROWS; r++) {
        memset(g_grid[r], ' ', UI_COLS);
        g_grid[r][UI_COLS] = '\0';
        g_color[r] = UI_WHITE;
        g_sel[r]   = false;
    }
    g_prog_row = -1;
}

void ui_progress(int row, uint64_t done, uint64_t total) {
    if (row < 0 || row >= UI_ROWS || total == 0) { g_prog_row = -1; return; }
    if (done > total) done = total;
    g_prog_row      = row;
    g_prog_permille = (uint32_t)(done * 1000 / total);
}

static void fill_rect(OSScreenID screen, int x, int y, int w, int h, uint32_t col) {
    for (int yy = y; yy < y + h; yy++)
        for (int xx = x; xx < x + w; xx++)
            OSScreenPutPixelEx(screen, (uint32_t)xx, (uint32_t)yy, col);
}

static void draw_screen(OSScreenID screen) {
    /* The text origin and cell size are fixed properties of the OSScreen
     * font renderer — identical on both screens, so the same rectangles
     * line up with the same text everywhere. */
    int scr_w  = screen == SCREEN_TV ? 1280 : 854;
    int grid_w = GRID_CELLS_W * CELL_W;

    OSScreenClearBufferEx(screen, COL_BG);

    /* Header and footer bars run the full screen width. */
    fill_rect(screen, 0, ORIGIN_Y, scr_w, HEADER_ROWS * CELL_H, COL_BAR);
    fill_rect(screen, 0, ORIGIN_Y + (UI_ROWS - FOOTER_ROWS) * CELL_H,
              scr_w, FOOTER_ROWS * CELL_H, COL_BAR);
    if (g_status[0] && g_status_err)
        fill_rect(screen, 0, ORIGIN_Y + (UI_ROWS - 1) * CELL_H,
                  scr_w, CELL_H, COL_ERRBG);

    /* Selection highlight bars first, then the progress bar (so an active
     * row that is also selected still shows its bar), then colour chips. */
    for (int r = 0; r < UI_ROWS; r++) {
        if (g_sel[r])
            fill_rect(screen, ORIGIN_X, ORIGIN_Y + r * CELL_H, grid_w, CELL_H,
                      COL_HILITE);
    }

    if (g_prog_row >= 0) {
        int py = ORIGIN_Y + g_prog_row * CELL_H;
        fill_rect(screen, ORIGIN_X, py, grid_w, CELL_H, COL_PROG_BG);
        fill_rect(screen, ORIGIN_X, py,
                  (int)((uint64_t)grid_w * g_prog_permille / 1000), CELL_H,
                  COL_PROG_FG);
    }

    for (int r = 0; r < UI_ROWS; r++) {
        int py = ORIGIN_Y + r * CELL_H;
        uint32_t chip = CHIP_COLORS[g_color[r]];
        if (chip && !(g_status_err && r == UI_ROWS - 1))
            fill_rect(screen, ORIGIN_X + 2, py + 7, CELL_W - 4, CELL_H - 14,
                      chip);
    }

    /* Text on top — the font only writes foreground pixels.  Column 1: the
     * gutter cell holds the chip. */
    char line[UI_COLS + 1];
    for (int r = 0; r < UI_ROWS; r++) {
        int len = UI_COLS;
        while (len > 0 && g_grid[r][len - 1] == ' ') len--;
        if (len == 0) continue;
        memcpy(line, g_grid[r], (size_t)len);
        line[len] = '\0';
        OSScreenPutFontEx(screen, 1, (uint32_t)r, line);
    }
}

void ui_flush(void) {
    if (!g_foreground) return;
    draw_screen(SCREEN_TV);
    draw_screen(SCREEN_DRC);
    DCFlushRange(g_buf_tv,  g_size_tv);
    DCFlushRange(g_buf_drc, g_size_drc);
    OSScreenFlipBuffersEx(SCREEN_TV);
    OSScreenFlipBuffersEx(SCREEN_DRC);
}

/* Copy ``src`` into row ``row`` starting at ``col``, clipped to the grid. */
static void put_at(int row, int col, const char *src) {
    if (row < 0 || row >= UI_ROWS || col >= UI_COLS) return;
    if (col < 0) col = 0;
    for (int i = 0; src[i] && col + i < UI_COLS; i++) {
        char c = src[i];
        if (c == '\t') c = ' ';
        if ((unsigned char)c < 0x20) c = ' ';
        g_grid[row][col + i] = c;
    }
}

static void set_row_color(int row, int color) {
    if (row < 0 || row >= UI_ROWS) return;
    if (color < 0 || color >= (int)(sizeof(CHIP_COLORS) / sizeof(CHIP_COLORS[0])))
        color = UI_WHITE;
    if (g_color[row] == UI_WHITE)
        g_color[row] = (uint8_t)color;
}

void ui_text(int row, int col, int color, const char *fmt, ...) {
    char line[256];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(line, sizeof(line), fmt, ap);
    va_end(ap);

    put_at(row, col, line);
    set_row_color(row, color);
}

void ui_text_hl(int row, bool selected, int color, const char *fmt, ...) {
    char line[256];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(line, sizeof(line), fmt, ap);
    va_end(ap);

    put_at(row, 0, line);
    set_row_color(row, color);
    if (row >= 0 && row < UI_ROWS && selected)
        g_sel[row] = true;
}

void ui_status(const char *fmt, ...) {
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(g_status, sizeof(g_status), fmt, ap);
    va_end(ap);
    g_status_err = false;
}

void ui_error(const char *fmt, ...) {
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(g_status, sizeof(g_status), fmt, ap);
    va_end(ap);
    g_status_err = true;
}

const char *ui_status_text(void) { return g_status; }
bool ui_status_is_error(void) { return g_status_err; }

const char *ui_view_name(AppView view) {
    switch (view) {
        case APP_VIEW_ROMS:      return "GAME CATALOG";
        case APP_VIEW_LOCAL:     return "LOCAL GAMES";
        case APP_VIEW_DOWNLOADS: return "DOWNLOADS";
        case APP_VIEW_GCCARDS:   return "GC MEMCARDS (NINTENDONT)";
        case APP_VIEW_SERVER:    return "SERVER GC SAVES";
        case APP_VIEW_VWII:      return "VWII SAVES";
        case APP_VIEW_WIIU:      return "WII U SAVES";
        case APP_VIEW_CONFIG:    return "CONFIG";
        default:                 return "?";
    }
}

void ui_draw_header(const SyncState *state, AppView view) {
    char bar[UI_COLS + 1];
    char right[24];
    snprintf(right, sizeof(right), "%d/%d  v" APP_VERSION,
             (int)view + 1, (int)APP_VIEW_COUNT);

    memset(bar, ' ', UI_COLS);
    bar[UI_COLS] = '\0';
    const char *title = ui_view_name(view);
    int tl = (int)strlen(title);
    if (tl > UI_COLS - (int)strlen(right) - 2)
        tl = UI_COLS - (int)strlen(right) - 2;
    if (tl > 0) memcpy(bar, title, (size_t)tl);
    int rl = (int)strlen(right);
    memcpy(bar + UI_COLS - rl, right, (size_t)rl);
    put_at(0, 0, bar);

    const char *net = state->net_ready ? state->ip : "no-net";
    ui_text(1, 0, state->net_ready ? UI_GREEN : UI_RED, "net:%s", net);
    ui_text(1, 22, state->sd_ready ? UI_WHITE : UI_RED,
            "sd:%s", state->sd_ready ? "ok" : "none");
    ui_text(1, 32, UI_WHITE, "mocha:%s", state->mocha_ok ? "ok" : "off");
}

void ui_draw_footer(const char *hint) {
    int hint_row   = UI_ROWS - 2;
    int status_row = UI_ROWS - 1;
    if (hint && hint[0]) ui_text(hint_row, 0, UI_WHITE, "%s", hint);
    if (g_status[0])
        ui_text(status_row, 0, g_status_err ? UI_RED : UI_GREEN, "%s", g_status);
}

void ui_draw_message(const char *title, const char *message) {
    ui_clear();
    ui_text(1, 0, UI_YELLOW, "%s", title);

    int row = 3;
    const char *p = message;
    char line[UI_COLS + 1];
    while (p && *p && row < UI_ROWS - 1) {
        const char *nl = strchr(p, '\n');
        size_t len = nl ? (size_t)(nl - p) : strlen(p);
        if (len >= sizeof(line)) len = sizeof(line) - 1;
        memcpy(line, p, len);
        line[len] = '\0';
        ui_text(row++, 0, UI_WHITE, "%s", line);
        if (!nl) break;
        p = nl + 1;
    }
}
