/*
 * GameSync PS5 client - entry point.
 * SPDX-License-Identifier: GPL-3.0-or-later
 *
 * Syncs emulator saves (RetroArch, SwanStationPS5, PS5SX2) with a GameSync
 * server and installs games from its ROM catalog. PS5 game saves are not
 * handled by this version.
 */
#include "config.h"
#include "gfx.h"
#include "platform.h"
#include "ui.h"

#include <stdio.h>

int main(void)
{
    /* before anything starts a thread: the HEN grants /data to a
     * single-threaded process (see ps5_unlock.c) */
    char status[160] = "";
    bool listable = plat_unlock_data(status, sizeof(status));

    char error[160] = "";
    if (!gfx_init())
    {
        app_log("out of memory for the screen");
        return 1;
    }
    if (!plat_init(error, sizeof(error)))
    {
        app_log("start-up failed: %s", error);
        plat_notify(error);
        return 1;
    }
#if defined(__PROSPERO__)
    {
        extern size_t ps5_heap_size(void);
        size_t heap = ps5_heap_size();
        if (heap)
            app_log("heap: %zu MB of direct memory", heap >> 20);
        else
            app_log("heap: no direct memory, the system heap only (large saves will fail)");
    }
#endif
    ui_run(listable, status);
    app_log("exit");
    plat_shutdown();
    plat_exit();
    return 0;
}
