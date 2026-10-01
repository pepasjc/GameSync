#ifndef UI_LOG_H
#define UI_LOG_H

// Activity log: everything the client prints with iprintf/printf lands here
// (video.c points stdout at ui_log_write) and is drawn in a panel on the
// task screens, in the small mono font. Understands \n, \r (start the line
// over), \t and the SGR colour escapes CON_RED/GREEN/YELLOW/CYAN/RESET.

#include "gfx.h"

#define LOG_LINE_H 9

void ui_log_clear(void);
void ui_log_write(const char *buf, size_t len);
void ui_log_puts(const char *text);
// Characters per line before wrapping (set from the panel width)
void ui_log_set_columns(int columns);
// Draw the newest lines that fit, oldest at the top
void ui_log_draw(Surface *s, int x, int y, int w, int h);
// Lines written since the last clear (capped at the buffer size)
int ui_log_count(void);

#endif
