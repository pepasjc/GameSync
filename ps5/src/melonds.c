/*
 * GameSync PS5 client - PS5CEMU-HAR's DS side (melonDS).
 * SPDX-License-Identifier: GPL-3.0-or-later
 *
 * Folder layout from PS5CEMU-HAR by premohq,
 * https://github.com/premohq/PS5CEMU-HAR (port/melonds/melonds.h,
 * library.cpp, core.cpp: games under nds.gamesFolder, saves as
 * saves/<game file>.sav); no code copied.
 */
#include "melonds.h"

#include "json.h"
#include "util.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

void melonds_games_dir(const Config *config, char *out, size_t size)
{
    snprintf(out, size, "%s/melonds/games", config->cemu_dir);
    char path[PATH_LEN];
    snprintf(path, sizeof(path), "%s/ps5cemu.json", config->cemu_dir);
    char *text = (char *)util_read_file(path, NULL);
    if (!text)
        return;
    JsonValue *root = json_parse(text);
    const char *dir = json_get_str(json_get(root, "nds"), "gamesFolder", "");
    if (dir[0] == '/')
        util_copy(out, size, dir);
    json_free(root);
    free(text);
}

void melonds_save_path(const Config *config, const char *game_file, char *out, size_t size)
{
    char stem[256];
    util_copy(stem, sizeof(stem), util_basename(game_file));
    util_strip_ext(stem);
    snprintf(out, size, "%s/melonds/saves/%s.sav", config->cemu_dir, stem);
}

bool melonds_is_game(const char *filename)
{
    return util_has_suffix_i(filename, ".nds") || util_has_suffix_i(filename, ".srl") ||
           util_has_suffix_i(filename, ".dsi");
}

bool melonds_title_id(const char *rom_path, char *out, size_t size)
{
    FILE *f = fopen(rom_path, "rb");
    if (!f)
        return false;
    unsigned char header[16];
    bool ok = fread(header, 1, sizeof(header), f) == sizeof(header);
    fclose(f);
    if (!ok)
        return false;
    /* the gamecode at 0x0C: four printable characters (AMCE, IRBO...),
     * taken as they are, like nds_gamecode_to_sync_id */
    for (int i = 12; i < 16; ++i)
        if (header[i] < 0x20 || header[i] >= 0x7F)
            return false;
    snprintf(out, size, "00048000%02X%02X%02X%02X", header[12], header[13], header[14],
             header[15]);
    return true;
}
