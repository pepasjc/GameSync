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

    /* Try USB drives first so the log is easy to retrieve */
    for (int i = 0; i < 8; i++) {
        char path[64];
        snprintf(path, sizeof(path), "/dev_usb%03d/ps3sync_debug.log", i);
        g_debug_log = fopen(path, "wb");
        if (g_debug_log) return true;
    }

    /* Fall back to internal HDD */
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
