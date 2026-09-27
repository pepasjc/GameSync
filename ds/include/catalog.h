#ifndef CATALOG_H
#define CATALOG_H

#include "common.h"

// Game catalog: browse the server's DS games, see which have
// RetroAchievements, and install one to <SD>/roms/nds. Uses both screens;
// returns when the user presses B (the caller redraws its own UI).
void catalog_screen(SyncState *state, bool has_wifi, PrintConsole *top, PrintConsole *bottom);

#endif
