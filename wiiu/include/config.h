#ifndef WIIUSYNC_CONFIG_H
#define WIIUSYNC_CONFIG_H

#include "common.h"

/*
 * Config file at fs:/vol/external01/3dssync/config.txt:
 *
 *   server_url=http://192.168.1.201:8000
 *   api_key=anything
 *   nintendont_saves_dir=/saves
 *   games_dir=/games
 *   wbfs_dir=/wbfs
 *   sync_vwii=true
 *   sync_wiiu=true
 *
 * Unlike the GameCube client there are no network keys — the Wii U uses the
 * system's network settings — and no SD-device selection (there is only one
 * SD slot).  server_url may be a hostname; nsysnet has a resolver.
 *
 * On first launch a default config is written and those defaults are used.
 * All fields are editable in-app (Config view).
 */

bool config_load(SyncState *state, char *err_out, size_t err_size);
bool config_save(const SyncState *state);
void config_load_console_id(SyncState *state);

#endif /* WIIUSYNC_CONFIG_H */
