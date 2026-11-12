/*
 * GameSync PS5 client - screen, controller and console services.
 * SPDX-License-Identifier: GPL-3.0-or-later
 *
 * platform_ps5.c on the console; platform_host.c for the host preview build
 * (scripted buttons, screenshots), which runs the same UI on a PC.
 */
#ifndef GAMESYNC_PLATFORM_H
#define GAMESYNC_PLATFORM_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

enum
{
    BTN_UP = 1u << 0,
    BTN_DOWN = 1u << 1,
    BTN_LEFT = 1u << 2,
    BTN_RIGHT = 1u << 3,
    BTN_CROSS = 1u << 4,
    BTN_CIRCLE = 1u << 5,
    BTN_SQUARE = 1u << 6,
    BTN_TRIANGLE = 1u << 7,
    BTN_L1 = 1u << 8,
    BTN_R1 = 1u << 9,
    BTN_L2 = 1u << 10,
    BTN_R2 = 1u << 11,
    BTN_OPTIONS = 1u << 12,
    BTN_TOUCHPAD = 1u << 13,
    BTN_SELECT = 1u << 14, /* Create */
};

/* Asks the HEN for /data listing; must run first in main(), single-threaded.
 * Fills status with what happened. */
bool plat_unlock_data(char *status, size_t size);
bool plat_init(char *error, size_t size);
void plat_shutdown(void);
/* Ends the process. On the PS5 the shell must close the title: returning
 * from main (exit()) raises SIGSYS there and shows as a crash. */
void plat_exit(void);
/* Buttons held right now; quit set when the app should close. */
uint32_t plat_poll(bool *quit);
/* Shows gfx's canvas and waits for the next vblank. */
void plat_present(void);
/* A system notification (best effort). */
void plat_notify(const char *text);
uint64_t plat_ticks_ms(void);

#endif
