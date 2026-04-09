#ifndef PS3SYNC_PS1CARD_SCAN_H
#define PS3SYNC_PS1CARD_SCAN_H

#include "common.h"

typedef struct {
    char title_id[GAME_ID_LEN];
    char save_name[32];
    int slot_index;
} Ps1CardEntry;

int ps1card_scan_file(const char *path, Ps1CardEntry *entries, int max_entries);

#endif /* PS3SYNC_PS1CARD_SCAN_H */
