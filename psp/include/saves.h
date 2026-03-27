#ifndef SAVES_H
#define SAVES_H

#include "common.h"

/* Scan SAVEDATA_PATH for save directories matching PSP product code pattern.
 * Populates state->titles and state->num_titles. */
void saves_scan(SyncState *state);

/* Compute SHA-256 hash of all files in a save directory (sorted by name).
 * title->hash is set; title->hash_calculated is set to true.
 * Returns 0 on success, negative on error. */
int saves_compute_hash(TitleInfo *title);

/* Collect all files in title->save_dir into files[] (relative paths) and sizes[].
 * max_files: size of arrays. Returns file count, or negative on error. */
int saves_list_files(const TitleInfo *title,
                     char files[][MAX_FILE_LEN], uint32_t sizes[], int max_files);

/* Read a file from the save directory into buf.
 * rel_path: path relative to save_dir (e.g. "PARAM.SFO").
 * buf_size: size of buf.
 * Returns bytes read, or negative on error. */
int saves_read_file(const TitleInfo *title, const char *rel_path,
                    uint8_t *buf, uint32_t buf_size);

/* Write a file into the save directory, creating parent dirs if needed.
 * Returns 0 on success. */
int saves_write_file(const TitleInfo *title, const char *rel_path,
                     const uint8_t *data, uint32_t size);

/* Returns true if game_id matches PSP product code pattern (XYYY#####). */
bool saves_is_valid_game_id(const char *game_id);

/* Returns true if the first 4 characters of game_id are a known PS1 retail
 * disc prefix (SLUS, SLES, SLPS, SCUS, SCES, SCPS, etc.).
 * Used to classify PSone Classics stored in PSP/SAVEDATA as PSX saves. */
bool saves_is_psx_prefix(const char *game_id);

#endif /* SAVES_H */
