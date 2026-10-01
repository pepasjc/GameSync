#ifndef RA_H
#define RA_H

#include "common.h"

// RetroAchievements for nds-bootstrap-ra (DSi, TWiLight Menu++ on SD).
// Files, all under the SD root (sd:/ normally):
//   _nds/ra/sets/<ROM file name>.txt   achievement sets read by nds-bootstrap
//   _nds/ra/hashes.txt                 RA ROM hash cache (file name + size)

// "Achievements" menu: update sets.
// Draws on the currently selected console; returns when the user presses B.
void ra_menu(SyncState *state, bool has_wifi);

// SD root holding _nds ("sd:" normally, "fat:" if only a flashcard has it)
const char *ra_sd_root(void);

// Results of ra_install_set
#define RA_SET_OK 0
#define RA_SET_UNKNOWN -1      // RetroAchievements doesn't know this ROM
#define RA_SET_NOT_DS_ROM -2   // couldn't hash the file
#define RA_SET_ERROR -3        // server/SD problem for this set
#define RA_SET_NO_SERVER -4    // request failed (no response, bad key, no RA login)

// Hash one ROM (remembered in hashes.txt) and save its achievement set, as
// "Update achievement sets" does for the whole folder. Prints nothing
// except retry notices.
int ra_install_set(SyncState *state, const char *rom_path, int *achievements);

#endif
