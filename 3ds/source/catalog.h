#ifndef CATALOG_H
#define CATALOG_H

#include "common.h"
#include "ui.h"

// Game catalog: browse the server's 3DS / DS / DSi games, see which have
// RetroAchievements, and install one: 3DS games as a CIA straight into the
// system (AM), DS games as a .nds file in the TWiLight Menu++ folders.
//
// The catalog is a top-level tab: this runs it on both screens until the
// user switches tab (L / R) or exits (START, confirmed), and returns which.
// What was loaded stays in memory for the next visit. *saves_changed is set
// when a DS game was installed into the save scan folder (the save list
// should be rescanned).
UiNav catalog_tab(const AppConfig *config, bool *saves_changed);

// Settings > Refresh catalog: ask the server to rescan its ROM folder (going
// on if it can't or won't), throw the cached catalog away and fetch every
// system again. Writes a summary for a message box into `report`.
void catalog_refresh(const AppConfig *config, char *report, size_t size);

// Forget everything loaded (the server settings changed): the next visit
// loads the catalog again. The cache files stay; their fingerprints decide.
void catalog_reset(void);

// Free everything (app exit)
void catalog_exit(void);

#endif
