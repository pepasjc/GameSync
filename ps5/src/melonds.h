/*
 * GameSync PS5 client - PS5CEMU-HAR's DS side (melonDS).
 * SPDX-License-Identifier: GPL-3.0-or-later
 */
#ifndef GAMESYNC_MELONDS_H
#define GAMESYNC_MELONDS_H

#include "config.h"

#include <stdbool.h>
#include <stddef.h>

/* Where PS5CEMU-HAR looks for DS games: ps5cemu.json's nds.gamesFolder,
 * else <cemu_dir>/melonds/games. */
void melonds_games_dir(const Config *config, char *out, size_t size);
/* A game's save: <cemu_dir>/melonds/saves/<game file without extension>.sav */
void melonds_save_path(const Config *config, const char *game_file, char *out, size_t size);
/* .nds / .srl / .dsi: what its library lists */
bool melonds_is_game(const char *filename);
/* The NDS save id (00048000 + the hex of the header's 4-char gamecode, as
 * shared/sync_id.py makes it) of a DS ROM; false when it can't be read. */
bool melonds_title_id(const char *rom_path, char *out, size_t size);

#endif
