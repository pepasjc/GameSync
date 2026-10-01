#ifndef CATALOG_H
#define CATALOG_H

#include "common.h"

// Game catalog tab: browse the server's DS games, see which have
// RetroAchievements, and install one to <SD>/roms/nds. The game list is
// cached on the SD (see catalog.c / catalog_cache.h).

// Switching to the tab: the first visit (or the first one with WiFi after
// an offline start) loads the list, showing progress on the bottom screen.
// Returns false if the tab can't be shown (out of memory).
bool catalog_tab_enter(SyncState *state, bool has_wifi);
// Both screens
void catalog_tab_draw(void);
// Keys of one frame (L/R/START are the caller's). `rep` = keysDownRepeat().
void catalog_tab_input(int down, int rep, bool has_wifi);

// Settings > Refresh Catalog: server rescan, wipe the cache, fetch every
// system again, show the result.
void catalog_refresh(SyncState *state, bool has_wifi);

// For the Settings screen: "3702 games cached", and where
const char *catalog_status(void);
const char *catalog_cache_dir(void);

void catalog_shutdown(void);

#endif
