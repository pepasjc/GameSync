/*
 * PS3 Save Sync - Game keys database (games.conf parser)
 *
 * Parses the Apollo Save Tool "games.conf" format to look up
 * per-game secure_file_id values needed for save data decryption.
 *
 * Format:
 *   [TITLEID1/TITLEID2]
 *   disc_hash_key=<32 hex chars>
 *   secure_file_id:PATTERN=<32 hex chars>
 *   secure_file_id:*=<32 hex chars>
 *
 * Wildcard matching is supported for filename patterns.
 */

#ifndef PS3SYNC_GAMEKEYS_H
#define PS3SYNC_GAMEKEYS_H

#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>

/*
 * Initialize the gamekeys database from a memory buffer containing
 * the contents of games.conf.
 *
 * The buffer is parsed once; an internal data structure is built.
 * Must be called once at startup before any lookups.
 *
 * Returns true on success, false on parse error or out of memory.
 */
bool gamekeys_init(const char *data, size_t data_len);

/*
 * Free all resources allocated by gamekeys_init().
 */
void gamekeys_shutdown(void);

/*
 * Look up the secure_file_id for a given game code and filename.
 *
 * game_code: the 9-char title ID prefix (e.g. "BCUS98233")
 * filename:  the save data filename (e.g. "USR-DATA")
 * out:       receives the 16-byte secure_file_id if found
 *
 * Returns true if a matching key was found, false otherwise.
 * If multiple secure_file_id entries exist, the most specific
 * filename pattern match wins (exact > prefix* > *).
 */
bool gamekeys_get_secure_file_id(const char *game_code,
                                  const char *filename,
                                  uint8_t out[16]);

/*
 * Look up the disc_hash_key for a given game code.
 *
 * game_code: the 9-char title ID prefix (e.g. "BCUS98233")
 * out:       receives the 16-byte disc_hash_key if found
 *
 * Returns true if found, false otherwise.
 */
bool gamekeys_get_disc_hash_key(const char *game_code, uint8_t out[16]);

/*
 * Check whether the database has been loaded.
 */
bool gamekeys_is_loaded(void);

#endif /* PS3SYNC_GAMEKEYS_H */
