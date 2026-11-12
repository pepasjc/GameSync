/*
 * GameSync PS5 client - RetroArch (PS5_RetroArch, PPSA99169) saves.
 * SPDX-License-Identifier: GPL-3.0-or-later
 */
#ifndef GAMESYNC_RETROARCH_H
#define GAMESYNC_RETROARCH_H

#include "config.h"
#include "saves.h"

/* One entry per ROM RetroArch knows (content/ folders and playlists), with
 * the save file RetroArch reads for it. */
void retroarch_scan(const Config *config, const KvStore *ids, SaveList *list);
/* The save file RetroArch would use for a ROM at rom_path (installed or not:
 * the path the catalog would install it to), with the default core. */
void retroarch_save_path(const Config *config, const char *system, const char *rom_path, char *out,
                         size_t size);
/* PPSSPP's PSP/SAVEDATA folder: the memory stick is the core's save folder. */
void retroarch_psp_savedata(const Config *config, char *out, size_t size);
/* A game file <name>.<any> under content/<system's folder>/ (two levels). */
bool retroarch_has_game(const Config *config, const char *system, const char *name);

#endif
