#ifndef SAVES_H
#define SAVES_H

#include "common.h"

/* Scan both native Vita and PSP emu save directories.
 * Respects state->scan_vita_saves and state->scan_psp_emu_saves flags. */
void saves_scan(SyncState *state);

int saves_compute_hash(TitleInfo *title);

/* List files in save directory. Returns file count or negative on error. */
int saves_list_files(const TitleInfo *title,
                     char files[][MAX_FILE_LEN], uint32_t sizes[], int max_files);

int saves_read_file(const TitleInfo *title, const char *rel_path,
                    uint8_t *buf, uint32_t buf_size);
int saves_write_file(const TitleInfo *title, const char *rel_path,
                     const uint8_t *data, uint32_t size);

bool saves_is_vita_game_id(const char *game_id);
bool saves_is_psp_game_id(const char *game_id);

/* Returns true if the first 4 characters of game_id are a known PS1 retail
 * disc prefix (SLUS, SLES, SLPS, SCUS, SCES, SCPS, etc.).
 * Used to classify PSone Classics stored in PSP/SAVEDATA as PSX saves. */
bool saves_is_psx_prefix(const char *game_id);

#endif
