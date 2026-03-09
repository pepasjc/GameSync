#ifndef TITLE_H
#define TITLE_H

#include "common.h"

// Scan for installed titles that have save data.
// Fills titles array, returns number found (up to MAX_TITLES).
// nds_dir: path to NDS ROM directory on SD card (NULL or "" to skip NDS scan).
int titles_scan(TitleInfo *titles, int max_titles, const char *nds_dir);

// Format a u64 title ID as a 16-char uppercase hex string.
void title_id_to_hex(u64 title_id, char *out);

// Fetch game names from server for all titles.
// Updates title->name for each title. Returns number of names fetched.
int titles_fetch_names(const AppConfig *config, TitleInfo *titles, int count);

#endif // TITLE_H
