#ifndef PS3SYNC_APOLLO_H
#define PS3SYNC_APOLLO_H

#include "common.h"

bool apollo_is_ps3_save_dir(const char *name);
SaveKind apollo_detect_save_kind(const char *game_code);
bool apollo_is_ps1_vm1_file(const char *name);
bool apollo_is_ps1_card_file(const char *name);
bool apollo_extract_game_code(const char *save_dir_name, char *game_code_out, size_t out_size);
bool apollo_extract_ps1_title_id(const char *vm1_name, char *title_id_out, size_t out_size);

void apollo_get_ps3_savedata_root(const SyncState *state, char *out, size_t out_size);
void apollo_get_ps3_export_root(int usb_index, char *out, size_t out_size);
void apollo_get_ps3_usb_savedata_root(int usb_index, char *out, size_t out_size);
void apollo_get_ps1_vmc_root(char *out, size_t out_size);
void apollo_get_ps1_usb_vmc_root(int usb_index, char *out, size_t out_size);

#endif /* PS3SYNC_APOLLO_H */
