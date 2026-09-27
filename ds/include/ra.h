#ifndef RA_H
#define RA_H

#include "common.h"

// RetroAchievements for nds-bootstrap-ra (DSi, TWiLight Menu++ on SD).
// Files, all under the SD root (sd:/ normally):
//   _nds/ra/sets/<ROM file name>.txt   achievement sets read by nds-bootstrap
//   _nds/ra/hashes.txt                 RA ROM hash cache (file name + size)
//   _nds/ra/unlocks.log                unlocks, appended by nds-bootstrap
//   _nds/ra/uploaded.txt               how much of unlocks.log went up
//   _nds/nds-bootstrap/ramDump.bin     unlock ring written during play

// "Achievements" menu: update sets / upload unlocks.
// Draws on the currently selected console; returns when the user presses B.
void ra_menu(SyncState *state, bool has_wifi);

#endif
