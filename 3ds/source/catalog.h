#ifndef CATALOG_H
#define CATALOG_H

#include "common.h"

// Game catalog: browse the server's 3DS / DS / DSi games, see which have
// RetroAchievements, and install one: 3DS games as a CIA straight into the
// system (AM), DS games as a .nds file in the TWiLight Menu++ folders.
// Uses both screens; returns when the user presses B. The caller redraws its
// own UI afterwards. Returns true if a DS game was installed into the save
// scan folder (the save list should be rescanned).
bool catalog_screen(const AppConfig *config);

#endif
