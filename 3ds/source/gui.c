#include "gui.h"
#include "network.h"
#include <3ds/util/utf.h>
#include <math.h>
#include <stdarg.h>

static C3D_RenderTarget *target_top, *target_bottom;
static C2D_TextBuf text_buf;
static float screen_w = GUI_TOP_W;
static int screen_id = GUI_TOP;
static u32 ticks;
static const char *ellipsis = "...";

u32 gui_mix(u32 a, u32 b, float t) {
    if (t < 0) t = 0;
    if (t > 1) t = 1;
    int r = (int)(((a >> 16) & 0xFF) * (1 - t) + ((b >> 16) & 0xFF) * t);
    int g = (int)(((a >> 8) & 0xFF) * (1 - t) + ((b >> 8) & 0xFF) * t);
    int bl = (int)((a & 0xFF) * (1 - t) + (b & 0xFF) * t);
    return gui_rgb(((u32)r << 16) | ((u32)g << 8) | (u32)bl);
}

// ---------------------------------------------------------------------------
// Lifetime and frames
// ---------------------------------------------------------------------------

void gui_init(void) {
    gfxInitDefault();
    C3D_Init(C3D_DEFAULT_CMDBUF_SIZE);
    C2D_Init(C2D_DEFAULT_MAX_OBJECTS);
    C2D_Prepare();
    target_top = C2D_CreateScreenTarget(GFX_TOP, GFX_LEFT);
    target_bottom = C2D_CreateScreenTarget(GFX_BOTTOM, GFX_LEFT);
    text_buf = C2D_TextBufNew(4096);
    fontEnsureMapped();

    // Use a real ellipsis when the system font has one
    CFNT_s *font = fontGetSystemFont();
    if (font && fontGlyphIndexFromCodePoint(font, 0x2026) != fontGetInfo(font)->alterCharIndex)
        ellipsis = "\xE2\x80\xA6";
}

void gui_exit(void) {
    C2D_TextBufDelete(text_buf);
    C2D_Fini();
    C3D_Fini();
    gfxExit();
}

void gui_begin(bool vsync) {
    C3D_FrameBegin(vsync ? C3D_FRAME_SYNCDRAW : 0);
    C2D_TextBufClear(text_buf);
    ticks++;
}

void gui_screen(int screen) {
    C3D_RenderTarget *t = (screen == GUI_TOP) ? target_top : target_bottom;
    screen_id = screen;
    screen_w = (screen == GUI_TOP) ? GUI_TOP_W : GUI_BOT_W;
    C2D_TargetClear(t, gui_rgb(HEX_BG));
    C2D_SceneBegin(t);
    // Soft vertical gradient behind everything
    gui_vgrad(0, 0, screen_w, GUI_H, gui_rgb(0x131D29), gui_rgb(HEX_BG));
}

void gui_end(void) {
    C3D_FrameEnd(0);
}

float gui_w(void) { return screen_w; }
u32 gui_ticks(void) { return ticks; }

void gui_ease(float *value, float target, float speed) {
    float d = target - *value;
    if (fabsf(d) < 0.01f) *value = target;
    else *value += d * speed;
}

// ---------------------------------------------------------------------------
// Shapes
// ---------------------------------------------------------------------------

void gui_rect(float x, float y, float w, float h, u32 color) {
    if (w <= 0 || h <= 0) return;
    C2D_DrawRectSolid(x, y, 0.5f, w, h, color);
}

void gui_vgrad(float x, float y, float w, float h, u32 top, u32 bottom) {
    C2D_DrawRectangle(x, y, 0.5f, w, h, top, top, bottom, bottom);
}

void gui_circle(float cx, float cy, float r, u32 color) {
    C2D_DrawCircleSolid(cx, cy, 0.5f, r, color);
}

void gui_rrect(float x, float y, float w, float h, float r, u32 color) {
    if (w <= 0 || h <= 0) return;
    if (r * 2 > h) r = h / 2;
    if (r * 2 > w) r = w / 2;
    if (r < 1) {
        gui_rect(x, y, w, h, color);
        return;
    }
    gui_rect(x + r, y, w - 2 * r, h, color);
    gui_rect(x, y + r, r, h - 2 * r, color);
    gui_rect(x + w - r, y + r, r, h - 2 * r, color);
    gui_circle(x + r, y + r, r, color);
    gui_circle(x + w - r, y + r, r, color);
    gui_circle(x + r, y + h - r, r, color);
    gui_circle(x + w - r, y + h - r, r, color);
}

void gui_icon_up(float cx, float cy, float s, u32 color) {
    float h = s / 2;
    C2D_DrawTriangle(cx, cy - h, color, cx - h, cy, color, cx + h, cy, color, 0.5f);
    gui_rect(cx - s * 0.17f, cy - 0.5f, s * 0.34f, h + 0.5f, color);
}

void gui_icon_down(float cx, float cy, float s, u32 color) {
    float h = s / 2;
    C2D_DrawTriangle(cx, cy + h, color, cx - h, cy, color, cx + h, cy, color, 0.5f);
    gui_rect(cx - s * 0.17f, cy - h, s * 0.34f, h + 0.5f, color);
}

void gui_icon_check(float cx, float cy, float s, u32 color) {
    float t = s * 0.18f;
    if (t < 1.5f) t = 1.5f;
    C2D_DrawLine(cx - s * 0.42f, cy, color, cx - s * 0.12f, cy + s * 0.3f, color, t, 0.5f);
    C2D_DrawLine(cx - s * 0.12f, cy + s * 0.3f, color, cx + s * 0.45f, cy - s * 0.32f, color, t, 0.5f);
}

// ---------------------------------------------------------------------------
// Text
// ---------------------------------------------------------------------------

float gui_line_h(float scale) {
    return fontGetInfo(NULL)->lineFeed * scale;
}

static float glyph_w(u32 cp, float scale) {
    int index = fontGlyphIndexFromCodePoint(NULL, cp);
    charWidthInfo_s *cw = fontGetCharWidthInfo(NULL, index);
    return cw ? cw->charWidth * scale : 0;
}

// Next code point of `p`; returns bytes consumed (>= 1)
static int next_cp(const char *p, u32 *cp) {
    ssize_t n = decode_utf8(cp, (const uint8_t *)p);
    if (n <= 0) {
        *cp = '?';
        return 1;
    }
    return (int)n;
}

float gui_text_w(float scale, const char *s) {
    float w = 0;
    if (!s) return 0;
    while (*s && *s != '\n') {
        u32 cp;
        s += next_cp(s, &cp);
        w += glyph_w(cp, scale);
    }
    return w;
}

float gui_text(float x, float y, float scale, u32 color, GuiAlign align, const char *s) {
    if (!s || !*s) return 0;
    C2D_Text t;
    C2D_TextParse(&t, text_buf, s);
    C2D_TextOptimize(&t);
    u32 flags = C2D_WithColor;
    if (align == GUI_CENTER) flags |= C2D_AlignCenter;
    else if (align == GUI_RIGHT) flags |= C2D_AlignRight;
    C2D_DrawText(&t, flags, x, y, 0.5f, scale, scale, color);
    return t.width * scale;
}

float gui_textf(float x, float y, float scale, u32 color, GuiAlign align, const char *fmt, ...) {
    char buf[256];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(buf, sizeof(buf), fmt, ap);
    va_end(ap);
    return gui_text(x, y, scale, color, align, buf);
}

// Copy the longest prefix of `s` that fits `max_w` together with an
// ellipsis; returns true if it had to cut
static bool fit(const char *s, float scale, float max_w, char *out, size_t size) {
    if (gui_text_w(scale, s) <= max_w) {
        snprintf(out, size, "%s", s);
        // A newline ends the text here
        char *nl = strchr(out, '\n');
        if (nl) *nl = '\0';
        return false;
    }
    float limit = max_w - gui_text_w(scale, ellipsis);
    float w = 0;
    const char *p = s;
    while (*p && *p != '\n') {
        u32 cp;
        int n = next_cp(p, &cp);
        float gw = glyph_w(cp, scale);
        if (w + gw > limit) break;
        w += gw;
        p += n;
    }
    size_t len = (size_t)(p - s);
    // Don't leave a trailing space before the ellipsis
    while (len > 0 && s[len - 1] == ' ') len--;
    if (len + strlen(ellipsis) + 1 > size) len = size - strlen(ellipsis) - 1;
    memcpy(out, s, len);
    strcpy(out + len, ellipsis);
    return true;
}

float gui_text_fit(float x, float y, float scale, u32 color, GuiAlign align, float max_w, const char *s) {
    if (!s || !*s) return 0;
    char buf[GUI_WRAP_LINE * 2];
    fit(s, scale, max_w, buf, sizeof(buf));
    return gui_text(x, y, scale, color, align, buf);
}

float gui_text_mid(float x, float y, float h, float scale, u32 color, GuiAlign align, float max_w, const char *s) {
    float ty = y + (h - gui_line_h(scale)) / 2 + scale * 1.5f;
    if (max_w > 0) return gui_text_fit(x, ty, scale, color, align, max_w, s);
    return gui_text(x, ty, scale, color, align, s);
}

int gui_wrap(const char *s, float scale, float max_w, char lines[][GUI_WRAP_LINE], int max_lines) {
    int count = 0;
    if (!s || max_lines <= 0) return 0;
    const char *p = s;
    while (*p && count < max_lines) {
        // One line: advance until the width runs out or a newline
        const char *start = p, *cut = NULL, *after_cut = NULL;
        float w = 0;
        while (*p && *p != '\n') {
            u32 cp;
            int n = next_cp(p, &cp);
            float gw = glyph_w(cp, scale);
            if (w + gw > max_w && p > start) break;
            if (cp == ' ') {
                cut = p;
                after_cut = p + n;
            }
            w += gw;
            p += n;
            if ((size_t)(p - start) >= GUI_WRAP_LINE - 4) break;
        }
        const char *end = p;
        if (*p && *p != '\n' && cut && cut > start) {
            end = cut;
            p = after_cut;
        }
        size_t len = (size_t)(end - start);
        if (len > GUI_WRAP_LINE - 1) len = GUI_WRAP_LINE - 1;
        memcpy(lines[count], start, len);
        lines[count][len] = '\0';
        count++;
        if (*p == '\n') p++;
        else while (*p == ' ') p++;
    }
    if (*p && count > 0) {
        // Text left over: ellipsis on the last line
        char tmp[GUI_WRAP_LINE + 8];
        snprintf(tmp, sizeof(tmp), "%s%s", lines[count - 1], ellipsis);
        fit(tmp, scale, max_w, lines[count - 1], GUI_WRAP_LINE);
        if (!strstr(lines[count - 1], ellipsis)) {
            size_t l = strlen(lines[count - 1]);
            if (l + strlen(ellipsis) < GUI_WRAP_LINE) strcat(lines[count - 1], ellipsis);
        }
    }
    return count;
}

int gui_text_wrap(float x, float y, float scale, u32 color, float max_w, int max_lines, const char *s) {
    char lines[8][GUI_WRAP_LINE];
    if (max_lines > 8) max_lines = 8;
    int n = gui_wrap(s, scale, max_w, lines, max_lines);
    float lh = gui_line_h(scale);
    for (int i = 0; i < n; i++) gui_text(x, y + i * lh, scale, color, GUI_LEFT, lines[i]);
    return n;
}

// ---------------------------------------------------------------------------
// Widgets
// ---------------------------------------------------------------------------

float gui_pill_w(float h, float scale, const char *label) {
    return gui_text_w(scale, label) + h * 0.9f;
}

float gui_pill(float x, float y, float h, float scale, u32 bg, u32 fg, const char *label) {
    float pad = h * 0.45f;
    float w = gui_text_w(scale, label) + pad * 2;
    gui_rrect(x, y, w, h, h / 2, bg);
    gui_text_mid(x + pad, y, h, scale, fg, GUI_LEFT, 0, label);
    return w;
}

float gui_pill_outline(float x, float y, float h, float scale, u32 color, u32 fill, const char *label) {
    float pad = h * 0.45f;
    float w = gui_text_w(scale, label) + pad * 2;
    gui_rrect(x, y, w, h, h / 2, color);
    gui_rrect(x + 1, y + 1, w - 2, h - 2, (h - 2) / 2, fill);
    gui_text_mid(x + pad, y, h, scale, color, GUI_LEFT, 0, label);
    return w;
}

static u32 button_hex(const char *b) {
    if (!strcmp(b, "A")) return 0xE5534B;
    if (!strcmp(b, "B")) return 0xE3B341;
    if (!strcmp(b, "X")) return 0x539BF5;
    if (!strcmp(b, "Y")) return 0x57AB5A;
    return 0xAAB8C5;
}

float gui_button_w(const char *b) {
    if (strlen(b) == 1) return (*b == 'L' || *b == 'R') ? 17 : 13;
    if (!strcmp(b, "DPAD") || !strcmp(b, "UD") || !strcmp(b, "LR")) return 13;
    return gui_text_w(0.32f, b) + 8;
}

float gui_button(float x, float cy, const char *b) {
    u32 ink = gui_rgb(HEX_INK);
    if (strlen(b) == 1 && (*b == 'L' || *b == 'R')) {
        // Shoulder button: rounded tab
        gui_rrect(x, cy - 6.5f, 17, 13, 4, gui_rgb(button_hex(b)));
        gui_text_mid(x + 8.5f, cy - 6.5f, 13, 0.38f, ink, GUI_CENTER, 0, b);
        return 17;
    }
    if (strlen(b) == 1) {
        gui_circle(x + 6.5f, cy, 6.5f, gui_rgb(button_hex(b)));
        gui_text_mid(x + 6.6f, cy - 6.5f, 13, 0.38f, ink, GUI_CENTER, 0, b);
        return 13;
    }
    if (!strcmp(b, "DPAD") || !strcmp(b, "UD") || !strcmp(b, "LR")) {
        u32 base = gui_rgb(0x6F8295), hi = gui_rgb(HEX_TEXT);
        bool ud = !strcmp(b, "UD"), lr = !strcmp(b, "LR"), all = !ud && !lr;
        // Cross: vertical arm, horizontal arm, then highlighted halves
        gui_rect(x + 4.5f, cy - 6.5f, 4, 13, (ud || all) ? hi : base);
        gui_rect(x, cy - 2, 13, 4, (lr || all) ? hi : base);
        if (lr) gui_rect(x + 4.5f, cy - 6.5f, 4, 4.5f, base), gui_rect(x + 4.5f, cy + 2, 4, 4.5f, base);
        if (ud) gui_rect(x, cy - 2, 4.5f, 4, base), gui_rect(x + 8.5f, cy - 2, 4.5f, 4, base);
        return 13;
    }
    // START / SELECT
    float w = gui_text_w(0.32f, b) + 8;
    gui_rrect(x, cy - 5.5f, w, 11, 5.5f, gui_rgb(button_hex(b)));
    gui_text_mid(x + w / 2, cy - 5.5f, 11, 0.32f, ink, GUI_CENTER, 0, b);
    return w;
}

void gui_bar(float x, float y, float w, float h, float frac, u32 color) {
    gui_rrect(x, y, w, h, h / 2, gui_rgb(HEX_BG2));
    if (frac < 0) {
        // Indeterminate: a segment sliding back and forth
        float seg = w * 0.3f;
        float t = (sinf(ticks * 0.06f) + 1) / 2;
        gui_rrect(x + (w - seg) * t, y, seg, h, h / 2, color);
        return;
    }
    if (frac > 1) frac = 1;
    float fw = w * frac;
    if (fw <= 0) return;
    if (fw < h) fw = h;
    gui_rrect(x, y, fw, h, h / 2, color);
    // Soft highlight on the top half
    if (fw > h) gui_rect(x + h / 2, y + 1, fw - h, h * 0.35f, gui_rgba(0xFFFFFF, 0x30));
}

void gui_spinner(float cx, float cy, float r, u32 color) {
    const int dots = 8;
    int head = (ticks / 4) % dots;
    for (int i = 0; i < dots; i++) {
        float a = (float)i / dots * 2 * (float)M_PI;
        int age = (head - i + dots) % dots;
        u8 alpha = (u8)(255 - age * 28);
        u32 c = (color & 0x00FFFFFF) | ((u32)alpha << 24);
        gui_circle(cx + cosf(a) * r, cy + sinf(a) * r, r * 0.22f, c);
    }
}

void gui_scrollbar(float x, float y, float h, int first, int visible, int total, float smooth_first) {
    (void)first;
    if (total <= visible || total <= 0) return;
    gui_rrect(x, y, 3, h, 1.5f, gui_rgb(HEX_BG2));
    float th = h * visible / total;
    if (th < 12) th = 12;
    float ty = y + (h - th) * (smooth_first / (float)(total - visible));
    if (ty < y) ty = y;
    if (ty > y + h - th) ty = y + h - th;
    gui_rrect(x, ty, 3, th, 1.5f, gui_rgb(HEX_DIM));
}

void gui_panel(float x, float y, float w, float h) {
    gui_rrect(x - 1, y - 1, w + 2, h + 2, 8, gui_rgb(0x0A1118));
    gui_rrect(x, y, w, h, 7, gui_rgb(HEX_PANEL));
}

void gui_card(float x, float y, float w, float h, const char *title, u32 tone) {
    // Drop shadow
    gui_rrect(x + 2, y + 3, w, h, 8, gui_rgb(0x070B10));
    gui_rrect(x - 1, y - 1, w + 2, h + 2, 8, gui_mix(HEX_LINE, tone, 0.4f));
    gui_rrect(x, y, w, h, 7, gui_rgb(HEX_PANEL));
    if (title) {
        // Title strip: rounded on top, square at the bottom
        gui_rrect(x, y, w, 24, 7, gui_rgb(HEX_PANEL_HI));
        gui_rect(x, y + 14, w, 10, gui_rgb(HEX_PANEL_HI));
        gui_rect(x, y + 24, w, 1, gui_rgb(HEX_LINE));
        gui_rrect(x + 9, y + 8, 4, 8, 2, gui_rgb(tone));
        gui_text_mid(x + 19, y, 24, GUI_S_BODY, gui_rgb(HEX_TEXT), GUI_LEFT, w - 28, title);
    }
}

void gui_dim(void) {
    gui_rect(0, 0, screen_w, GUI_H, gui_rgba(0x05090D, 0xB4));
}

void gui_header_bar(void) {
    gui_rect(0, 0, screen_w, GUI_HEADER_H, gui_rgb(HEX_BG2));
    gui_rect(0, GUI_HEADER_H - 1, screen_w, 1, gui_rgb(HEX_LINE));
}

float gui_status_icons(float x_right, float cy) {
    // Server: green answered, red unreachable, grey not asked yet
    int server = network_server_state();
    u32 dot = server > 0 ? HEX_OK : (server == 0 ? HEX_ERR : HEX_MUTED);
    gui_circle(x_right - 4, cy, 5, gui_rgba(dot, 0x50));
    gui_circle(x_right - 4, cy, 3.2f, gui_rgb(dot));
    // WiFi bars
    u8 strength = osGetWifiStrength();
    float bx = x_right - 26;
    for (int i = 0; i < 3; i++) {
        float bh = 4 + i * 3;
        gui_rect(bx + i * 5, cy + 5 - bh, 3, bh, gui_rgb(i < strength ? HEX_TEXT : HEX_MUTED));
    }
    return bx;
}

void gui_header(const char *section) {
    gui_header_bar();
    // Logo mark: teal rounded square with a sync-ish notch
    gui_rrect(8, 6, 14, 14, 4, gui_rgb(HEX_ACCENT));
    gui_rrect(12, 10, 6, 6, 2, gui_rgb(HEX_BG2));
    float x = 28;
    x += gui_text_mid(x, 0, GUI_HEADER_H, 0.55f, gui_rgb(HEX_TEXT), GUI_LEFT, 0, "GameSync");
    if (section && *section) {
        gui_circle(x + 7, GUI_HEADER_H / 2.0f, 1.6f, gui_rgb(HEX_MUTED));
        gui_text_mid(x + 14, 0, GUI_HEADER_H, 0.5f, gui_rgb(HEX_ACCENT2), GUI_LEFT,
                     screen_w - x - 110, section);
    }
    if (screen_id == GUI_TOP) {
        float left = gui_status_icons(screen_w - 8, GUI_HEADER_H / 2.0f);
        gui_text_mid(left - 8, 0, GUI_HEADER_H, GUI_S_SMALL, gui_rgb(HEX_DIM), GUI_RIGHT, 0, "v" APP_VERSION);
    }
}

void gui_footer(const GuiHint *hints, int count) {
    float y = GUI_FOOTER_Y;
    gui_rect(0, y, screen_w, GUI_FOOTER_H, gui_rgb(HEX_BG2));
    gui_rect(0, y, screen_w, 1, gui_rgb(HEX_LINE));
    if (count <= 0) return;
    // Measure, then spread the leftover space between items
    float widths[12], total = 0;
    if (count > 12) count = 12;
    for (int i = 0; i < count; i++) {
        widths[i] = gui_button_w(hints[i].button) + 4 + gui_text_w(GUI_S_SMALL, hints[i].label);
        total += widths[i];
    }
    float gap = (screen_w - 16 - total) / (count > 1 ? count - 1 : 1);
    if (gap > 16) gap = 16;
    if (gap < 4) gap = 4;
    float x = 8, cy = y + GUI_FOOTER_H / 2.0f + 0.5f;
    for (int i = 0; i < count; i++) {
        float bw = gui_button(x, cy, hints[i].button);
        gui_text_mid(x + bw + 4, y, GUI_FOOTER_H, GUI_S_SMALL, gui_rgb(HEX_DIM), GUI_LEFT, 0, hints[i].label);
        x += widths[i] + gap;
    }
}

float gui_tabs(float x, float y, float h, const char *const *labels, int count, int active) {
    float pad = 9, total = 0;
    for (int i = 0; i < count; i++) total += gui_text_w(GUI_S_SMALL, labels[i]) + pad * 2;
    gui_rrect(x, y, total + 4, h, h / 2, gui_rgb(HEX_BG));
    float cx = x + 2;
    for (int i = 0; i < count; i++) {
        float w = gui_text_w(GUI_S_SMALL, labels[i]) + pad * 2;
        bool on = (i == active);
        if (on) gui_rrect(cx, y + 2, w, h - 4, (h - 4) / 2, gui_rgb(HEX_ACCENT));
        gui_text_mid(cx + w / 2, y, h, GUI_S_SMALL, gui_rgb(on ? HEX_INK : HEX_DIM), GUI_CENTER, 0, labels[i]);
        cx += w;
    }
    return x + total + 4;
}
