/*
 * GameSync PS5 client - host preview platform.
 * SPDX-License-Identifier: GPL-3.0-or-later
 *
 * Runs the real UI on a PC without a window: buttons come from a script,
 * frames can be saved as PPM screenshots. Used by ps5/tests/preview.sh.
 *
 *   GAMESYNC_DATA=<dir>      data folder instead of /data/gamesync
 *   GAMESYNC_SCRIPT=<file>   whitespace-separated steps:
 *       cross circle square triangle up down left right l1 r1 l2 r2 options
 *                            press for one frame
 *       wait:N               N idle frames
 *       shot:<file.ppm>      save the next frame
 *       quit
 */
#if !defined(__PROSPERO__)
#include "platform.h"

#include "config.h"
#include "gfx.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

static char *script;
static char *cursor;
static int wait_frames;
static uint32_t pending;
static char shot_path[512];
static bool release_frame;
static uint64_t frame_count;

bool plat_unlock_data(char *status, size_t size)
{
    const char *dir = getenv("GAMESYNC_DATA");
    if (dir && *dir)
        config_set_data_dir(dir);
    snprintf(status, size, "host build");
    return true;
}

bool plat_init(char *error, size_t size)
{
    const char *path = getenv("GAMESYNC_SCRIPT");
    if (!path)
    {
        snprintf(error, size, "set GAMESYNC_SCRIPT");
        return false;
    }
    FILE *f = fopen(path, "rb");
    if (!f)
    {
        snprintf(error, size, "can't open %s", path);
        return false;
    }
    fseek(f, 0, SEEK_END);
    long len = ftell(f);
    rewind(f);
    script = calloc(1, (size_t)len + 1);
    if (fread(script, 1, (size_t)len, f) != (size_t)len)
        len = 0;
    fclose(f);
    cursor = script;
    return true;
}

void plat_shutdown(void)
{
    free(script);
}

void plat_exit(void)
{
    exit(0);
}

static uint32_t button_named(const char *name)
{
    static const struct
    {
        const char *name;
        uint32_t bit;
    } map[] = {{"cross", BTN_CROSS},   {"circle", BTN_CIRCLE}, {"square", BTN_SQUARE},
               {"triangle", BTN_TRIANGLE}, {"up", BTN_UP},  {"down", BTN_DOWN},
               {"left", BTN_LEFT},     {"right", BTN_RIGHT},   {"l1", BTN_L1},
               {"r1", BTN_R1},         {"l2", BTN_L2},         {"r2", BTN_R2},
               {"options", BTN_OPTIONS}, {"select", BTN_SELECT}};
    for (size_t i = 0; i < sizeof(map) / sizeof(map[0]); ++i)
        if (strcmp(map[i].name, name) == 0)
            return map[i].bit;
    return 0;
}

uint32_t plat_poll(bool *quit)
{
    if (release_frame)
    {
        release_frame = false;
        return 0;
    }
    if (pending)
    {
        uint32_t b = pending;
        pending = 0;
        release_frame = true;
        return b;
    }
    if (wait_frames > 0)
    {
        wait_frames--;
        return 0;
    }
    for (;;)
    {
        while (*cursor == ' ' || *cursor == '\n' || *cursor == '\r' || *cursor == '\t')
            cursor++;
        if (!*cursor)
        {
            *quit = true;
            return 0;
        }
        char token[512];
        size_t n = 0;
        while (cursor[n] && cursor[n] != ' ' && cursor[n] != '\n' && cursor[n] != '\r' &&
               cursor[n] != '\t' && n + 1 < sizeof(token))
        {
            token[n] = cursor[n];
            n++;
        }
        token[n] = 0;
        cursor += n;
        if (strcmp(token, "quit") == 0)
        {
            *quit = true;
            return 0;
        }
        if (strncmp(token, "wait:", 5) == 0)
        {
            wait_frames = atoi(token + 5);
            return 0;
        }
        if (strncmp(token, "shot:", 5) == 0)
        {
            snprintf(shot_path, sizeof(shot_path), "%s", token + 5);
            continue;
        }
        uint32_t b = button_named(token);
        if (b)
        {
            release_frame = true;
            return b;
        }
        fprintf(stderr, "preview: unknown step '%s'\n", token);
    }
}

void plat_present(void)
{
    frame_count++;
    if (!shot_path[0])
        return;
    FILE *f = fopen(shot_path, "wb");
    if (f)
    {
        fprintf(f, "P6\n%d %d\n255\n", GFX_W, GFX_H);
        const uint32_t *px = gfx_pixels();
        static uint8_t row[GFX_W * 3];
        for (int y = 0; y < GFX_H; ++y)
        {
            for (int x = 0; x < GFX_W; ++x)
            {
                uint32_t v = px[(size_t)y * GFX_W + x];
                row[x * 3] = (uint8_t)v;
                row[x * 3 + 1] = (uint8_t)(v >> 8);
                row[x * 3 + 2] = (uint8_t)(v >> 16);
            }
            fwrite(row, 1, sizeof(row), f);
        }
        fclose(f);
        fprintf(stderr, "preview: wrote %s\n", shot_path);
    }
    shot_path[0] = 0;
}

void plat_notify(const char *text)
{
    fprintf(stderr, "notify: %s\n", text);
}

uint64_t plat_ticks_ms(void)
{
    /* frame-driven clock, so repeats and toasts behave the same every run */
    return frame_count * 16;
}
#endif
