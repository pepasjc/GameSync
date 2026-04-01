#include "debug.h"

#include "common.h"

#include <stdarg.h>
#include <stdio.h>
#include <string.h>

static FILE *g_debug_log = NULL;

bool debug_log_open(void) {
    if (g_debug_log) {
        return true;
    }

    g_debug_log = fopen(DEBUG_LOG_FILE, "wb");
    return g_debug_log != NULL;
}

void debug_log_close(void) {
    if (!g_debug_log) {
        return;
    }
    fclose(g_debug_log);
    g_debug_log = NULL;
}

void debug_log(const char *fmt, ...) {
    va_list args;

    if (!g_debug_log && !debug_log_open()) {
        return;
    }

    va_start(args, fmt);
    vfprintf(g_debug_log, fmt, args);
    va_end(args);

    if (fmt[0] != '\0') {
        size_t len = strlen(fmt);
        if (fmt[len - 1] != '\n') {
            fputc('\n', g_debug_log);
        }
    }

    fflush(g_debug_log);
}
