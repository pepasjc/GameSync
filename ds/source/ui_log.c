#include "ui_log.h"
#include "theme.h"
#include <string.h>

#define LOG_LINES 48
#define LOG_COLS 64

enum { COL_DEFAULT, COL_RED, COL_GREEN, COL_YELLOW, COL_CYAN };

typedef struct {
    char text[LOG_COLS];
    uint8_t color[LOG_COLS];
    uint8_t len;
} LogLine;

static LogLine lines[LOG_LINES];
static int head;        // index of the current (last) line
static int count = 1;   // lines in use, including the current one
static int columns = LOG_COLS;
static uint8_t cur_color = COL_DEFAULT;

// Escape parser state, kept across writes
static int esc_state;   // 0 text, 1 after ESC, 2 in CSI
static char esc_buf[16];
static int esc_len;
// Partial UTF-8 character
static char utf8_buf[4];
static int utf8_len, utf8_need;

void ui_log_clear(void) {
    memset(lines, 0, sizeof(lines));
    head = 0;
    count = 1;
    cur_color = COL_DEFAULT;
    esc_state = 0;
    utf8_len = utf8_need = 0;
}

void ui_log_set_columns(int c) {
    columns = c < 8 ? 8 : (c > LOG_COLS ? LOG_COLS : c);
}

int ui_log_count(void) {
    return count;
}

static void new_line(void) {
    head = (head + 1) % LOG_LINES;
    lines[head].len = 0;
    if (count < LOG_LINES) count++;
}

static void put_char(char c) {
    LogLine *l = &lines[head];
    if (l->len >= columns) {
        new_line();
        l = &lines[head];
    }
    l->text[l->len] = c;
    l->color[l->len] = cur_color;
    l->len++;
}

static void apply_sgr(void) {
    esc_buf[esc_len] = '\0';
    const char *p = esc_buf;
    if (!*p) {
        cur_color = COL_DEFAULT;
        return;
    }
    while (*p) {
        int v = 0;
        while (*p >= '0' && *p <= '9') v = v * 10 + (*p++ - '0');
        if (*p == ';') p++;
        else if (*p) p++;
        switch (v) {
            case 0: case 39: case 37: cur_color = COL_DEFAULT; break;
            case 31: case 35: cur_color = COL_RED; break;
            case 32: cur_color = COL_GREEN; break;
            case 33: cur_color = COL_YELLOW; break;
            case 34: case 36: cur_color = COL_CYAN; break;
            default: break;
        }
    }
}

static void feed(char c) {
    if (esc_state == 1) {
        if (c == '[') {
            esc_state = 2;
            esc_len = 0;
        } else {
            esc_state = 0;
        }
        return;
    }
    if (esc_state == 2) {
        if ((c >= '0' && c <= '9') || c == ';') {
            if (esc_len < (int)sizeof(esc_buf) - 1) esc_buf[esc_len++] = c;
            return;
        }
        esc_state = 0;
        if (c == 'm') apply_sgr();
        else if (c == 'J') ui_log_clear();
        return;  // cursor movement etc. is ignored
    }

    unsigned char u = (unsigned char)c;
    if (utf8_need) {
        utf8_buf[utf8_len++] = c;
        if (utf8_len < utf8_need) return;
        char folded[5];
        memcpy(folded, utf8_buf, utf8_len);
        folded[utf8_len] = '\0';
        const char *p = folded;
        put_char((char)gfx_next_char(&p));
        utf8_need = utf8_len = 0;
        return;
    }
    if (u >= 0xC0) {
        utf8_buf[0] = c;
        utf8_len = 1;
        utf8_need = (u >= 0xF0) ? 4 : (u >= 0xE0) ? 3 : 2;
        return;
    }

    switch (c) {
        case '\x1b': esc_state = 1; break;
        case '\n': new_line(); break;
        case '\r': lines[head].len = 0; break;
        case '\t':
            do put_char(' '); while (lines[head].len % 4);
            break;
        default:
            if (u >= 0x20 && u < 0x80) put_char(c);
            else if (u >= 0x80) put_char('?');
            break;
    }
}

void ui_log_write(const char *buf, size_t len) {
    for (size_t i = 0; i < len; i++) feed(buf[i]);
}

void ui_log_puts(const char *text) {
    ui_log_write(text, strlen(text));
}

static Color line_color(uint8_t c) {
    switch (c) {
        case COL_RED: return C_ERR;
        case COL_GREEN: return C_OK;
        case COL_YELLOW: return C_WARN;
        case COL_CYAN: return C_ACCENT_HI;
        default: return C_TEXT_DIM;
    }
}

void ui_log_draw(Surface *s, int x, int y, int w, int h) {
    int fit = h / LOG_LINE_H;
    // The current line counts only once something is on it
    int used = count - (lines[head].len == 0 ? 1 : 0);
    int n = used < fit ? used : fit;
    int last = lines[head].len == 0 ? head - 1 : head;
    Surface clip = *s;
    gfx_clip(&clip, x, y, w, h);
    for (int i = 0; i < n; i++) {
        const LogLine *l = &lines[((last - (n - 1 - i)) % LOG_LINES + LOG_LINES) % LOG_LINES];
        int ly = y + i * LOG_LINE_H, lx = x;
        for (int start = 0; start < l->len;) {
            int end = start;
            while (end < l->len && l->color[end] == l->color[start]) end++;
            lx = gfx_text_n(&clip, &font_mono, lx, ly, line_color(l->color[start]), l->text + start, end - start);
            start = end;
        }
    }
}
