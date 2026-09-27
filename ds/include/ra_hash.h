#ifndef RA_HASH_H
#define RA_HASH_H

#include <stdbool.h>

// RetroAchievements hash of a Nintendo DS ROM (rcheevos rc_hash_nintendo_ds):
// MD5 of the first 0x160 header bytes, the ARM9 and ARM7 binaries and the
// 0xA00-byte icon/title block. Plain C + stdio so it also builds on a PC.
// Writes 32 lowercase hex digits + NUL to hash_out; returns false if the
// file can't be read or isn't a DS ROM.
bool ra_hash_nds_file(const char *path, char hash_out[33]);

#endif
