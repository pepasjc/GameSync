#ifndef GCSYNC_CONFIG_H
#define GCSYNC_CONFIG_H

#include "common.h"

/*
 * Config file at sd:/3dssync/config.txt:
 *
 *   server_url=http://192.168.1.201:8000
 *   api_key=anything
 *   use_static_ip=true
 *   static_ip=192.168.1.95
 *   static_netmask=255.255.255.0
 *   static_gateway=192.168.1.1
 *   sd_device=sp2          # sp2, geckoa, geckob
 *   games_folder=/games
 *   gameid_slota=on        # on / off
 *   gameid_slotb=on
 *
 * On first launch a default config is written and those defaults are used.
 * All fields are editable in-app via the controller (Config view).
 */

bool config_load(SyncState *state, char *err_out, size_t err_size);
bool config_save(const SyncState *state);
void config_load_console_id(SyncState *state);

SdDevice config_sd_device_from_str(const char *value);

#endif /* GCSYNC_CONFIG_H */
