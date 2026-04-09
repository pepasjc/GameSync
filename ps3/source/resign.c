/*
 * PS3 Save Sync - Save resign orchestration
 *
 * Ties together PARAM.SFO patching and PARAM.PFD resign to make
 * downloaded saves work on the target PS3 console/user.
 *
 * Console identity (PSID) is obtained via lv2 syscall 872
 * (sys_ss_get_open_psid), which works on CFW/HEN.
 */

#include "resign.h"
#include "pfd.h"
#include "sfo.h"
#include "decrypt.h"
#include "gamekeys.h"
#include "debug.h"
#include "ui.h"

#include <ppu-lv2.h>
#include <lv2/syscalls.h>

#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#include <sys/stat.h>

/* ---- Console identity ---- */

static pfd_keys_t g_keys;
static uint8_t    g_psid[16];
static bool       g_initialized = false;

/*
 * Get the console's Open PSID via syscall 872.
 * The PSID is a 16-byte value unique to each PS3 console.
 * Works on CFW (DEX/CEX) and HEN.
 *
 * Returns 0 on success, -1 on failure.
 */
static int get_open_psid(uint8_t psid_out[16]) {
    /* sys_ss_get_open_psid returns two 64-bit values (high/low) */
    uint64_t psid_hi = 0, psid_lo = 0;

    /* lv2syscall1 takes the syscall number and one argument (pointer to output).
     * But sys_ss_get_open_psid(872) takes a pointer to a 16-byte buffer.
     * We use a struct on the stack. */
    struct { uint64_t hi; uint64_t lo; } psid_buf;
    memset(&psid_buf, 0, sizeof(psid_buf));

    {
        lv2syscall1(SYSCALL_SS_GET_OPEN_PSID, (uint64_t)(unsigned long)&psid_buf);
    }

    psid_hi = psid_buf.hi;
    psid_lo = psid_buf.lo;

    if (psid_hi == 0 && psid_lo == 0) {
        debug_log("resign: get_open_psid returned all zeros (RPCS3 or unsupported)");
        /* On RPCS3, PSID might be all zeros. Still proceed — resign
         * will produce a valid PFD structure, just with zeros as console_id.
         * This actually works on RPCS3 since it doesn't validate signatures. */
    }

    /* Write as big-endian bytes */
    for (int i = 0; i < 8; i++) {
        psid_out[i]     = (uint8_t)(psid_hi >> (56 - i * 8));
        psid_out[i + 8] = (uint8_t)(psid_lo >> (56 - i * 8));
    }

    debug_log("resign: PSID = %02x%02x%02x%02x%02x%02x%02x%02x%02x%02x%02x%02x%02x%02x%02x%02x",
              psid_out[0],  psid_out[1],  psid_out[2],  psid_out[3],
              psid_out[4],  psid_out[5],  psid_out[6],  psid_out[7],
              psid_out[8],  psid_out[9],  psid_out[10], psid_out[11],
              psid_out[12], psid_out[13], psid_out[14], psid_out[15]);

    return 0;
}

/* ---- Public API ---- */

bool resign_init(void) {
    debug_log("resign_init: initializing resign subsystem");

    /* Set up static crypto keys */
    pfd_setup_keys(&g_keys);

    /* Get console PSID */
    if (get_open_psid(g_psid) != 0) {
        debug_log("resign_init: failed to get PSID");
        return false;
    }

    /* Set console_id in keys */
    memcpy(g_keys.console_id, g_psid, 16);

    g_initialized = true;
    debug_log("resign_init: initialized successfully");
    return true;
}

int resign_save(const TitleInfo *title, const SyncState *state) {
    /* Only resign PS3 saves */
    if (title->kind != SAVE_KIND_PS3) {
        debug_log("resign_save: skipping non-PS3 save %s (kind=%d)",
                  title->title_id, (int)title->kind);
        return 0;
    }

    if (!g_initialized) {
        debug_log("resign_save: not initialized, skipping");
        return -1;
    }

    /* Check whether a *valid* native PS3 PARAM.PFD exists in the save
     * directory.  A valid PFD starts with u64-BE magic 0x0000000050464442
     * ("PFDB").  RPCS3 emulator writes a different format (u32 layout, bytes
     * "PFDB\x00\x00\x00\x04" at offset 0) which our resign engine can't
     * parse.  If we find an invalid/RPCS3 PFD, delete it and fall through
     * to the create path which builds a proper one from scratch. */
    char pfd_path[600];
    snprintf(pfd_path, sizeof(pfd_path), "%s/PARAM.PFD", title->local_path);
    {
        bool need_create = false;
        struct stat st;
        if (stat(pfd_path, &st) != 0) {
            /* No PARAM.PFD at all */
            need_create = true;
            debug_log("resign_save: no PARAM.PFD in %s", title->local_path);
        } else {
            /* PFD exists — validate magic bytes */
            FILE *pfp = fopen(pfd_path, "rb");
            if (pfp) {
                uint8_t hdr[16];
                size_t nr = fread(hdr, 1, 16, pfp);
                fclose(pfp);
                if (nr >= 16) {
                    /* Valid PS3 PFD: bytes 0-7 = 00 00 00 00 50 46 44 42 */
                    bool valid = (hdr[0] == 0x00 && hdr[1] == 0x00 &&
                                  hdr[2] == 0x00 && hdr[3] == 0x00 &&
                                  hdr[4] == 0x50 && hdr[5] == 0x46 &&
                                  hdr[6] == 0x44 && hdr[7] == 0x42);
                    if (!valid) {
                        debug_log("resign_save: PARAM.PFD has invalid/RPCS3 magic "
                                  "(%02x%02x%02x%02x %02x%02x%02x%02x), deleting",
                                  hdr[0], hdr[1], hdr[2], hdr[3],
                                  hdr[4], hdr[5], hdr[6], hdr[7]);
                        remove(pfd_path);
                        need_create = true;
                    }
                } else {
                    debug_log("resign_save: PARAM.PFD too small (%u bytes), deleting",
                              (unsigned)nr);
                    remove(pfd_path);
                    need_create = true;
                }
            } else {
                debug_log("resign_save: failed to open PARAM.PFD for validation");
                need_create = true;
            }
        }

        if (need_create) {
            /* No valid PARAM.PFD — common for saves synced from RPCS3 / other
             * emulators.  Encrypt save data files and create PFD with the
             * encrypted entry keys so the real PS3 will accept the save. */
            debug_log("resign_save: encrypting + creating PARAM.PFD for %s",
                      title->local_path);

            /* Prepare keys with current console identity */
            pfd_keys_t create_keys;
            memcpy(&create_keys, &g_keys, sizeof(create_keys));
            memset(create_keys.user_id, 0, 8);
            {
                const char *user_str = state->ps3_user;
                size_t ulen = strlen(user_str);
                if (ulen > 8) ulen = 8;
                memcpy(create_keys.user_id, user_str, ulen);
            }
            memcpy(create_keys.disc_hash_key,
                   create_keys.fallback_disc_hash_key, PFD_KEY_SIZE);

            /* Look up per-game secure_file_id for correct file hash HMAC keys */
            create_keys.has_secure_file_id = false;
            if (gamekeys_is_loaded()) {
                if (gamekeys_get_secure_file_id(title->game_code, "*",
                                                 create_keys.secure_file_id)) {
                    create_keys.has_secure_file_id = true;
                    debug_log("resign_save: found secure_file_id for %s (create path)",
                              title->game_code);
                }
            }

            /* Step 0: Patch PARAM.SFO ownership before anything else.
             * The SFO from the server bundle has the original uploader's
             * user_id / PSID / account_id.  We must rewrite these to the
             * target console's identity so the XMB shows the save under
             * the correct user.  This must happen before PFD creation
             * because the PFD hashes cover the SFO contents. */
            {
                char sfo_path[600];
                snprintf(sfo_path, sizeof(sfo_path), "%s/PARAM.SFO",
                         title->local_path);
                struct stat sfo_st;
                if (stat(sfo_path, &sfo_st) == 0) {
                    sfo_patch_t sfo_p;
                    memset(&sfo_p, 0, sizeof(sfo_p));
                    sfo_p.flags = SFO_PATCH_FLAG_REMOVE_COPY_PROTECTION;
                    sfo_p.user_id = (uint32_t)atoi(state->ps3_user);
                    memcpy(sfo_p.psid, g_psid, 16);
                    memset(sfo_p.account_id, '0', SFO_ACCOUNT_ID_SIZE);
                    sfo_p.account_id[SFO_ACCOUNT_ID_SIZE] = '\0';

                    ui_status("Patching PARAM.SFO: %s", title->game_code);
                    pump_callbacks();

                    if (sfo_patch(sfo_path, &sfo_p) != 0) {
                        debug_log("resign_save: SFO patch failed for %s "
                                  "(create path, non-fatal)", title->title_id);
                    } else {
                        debug_log("resign_save: SFO patched for %s (create path)",
                                  title->title_id);
                    }
                } else {
                    debug_log("resign_save: no PARAM.SFO in %s (create path)",
                              title->local_path);
                }
            }
            pump_callbacks();

            /* Step 1: Encrypt save data files in-place */
            encrypt_keys_t *enc_keys = NULL;
            int has_enc_keys = 0;

            if (gamekeys_is_loaded()) {
                enc_keys = (encrypt_keys_t *)malloc(sizeof(encrypt_keys_t));
                if (enc_keys) {
                    ui_status("Encrypting save files: %s", title->game_code);
                    pump_callbacks();

                    int er = encrypt_save(title, enc_keys);
                    if (er == 0 && enc_keys->count > 0) {
                        has_enc_keys = 1;
                        debug_log("resign_save: encrypted %d files for %s",
                                  enc_keys->count, title->title_id);
                    } else {
                        debug_log("resign_save: encrypt_save returned %d (count=%d), "
                                  "creating PFD without encryption",
                                  er, enc_keys->count);
                    }
                }
            }
            pump_callbacks();

            /* Step 2: Create PARAM.PFD (with or without entry keys) */
            int cr;
            if (has_enc_keys) {
                ui_status("Creating PARAM.PFD (encrypted): %s", title->game_code);
                pump_callbacks();
                /* Cast encrypt_key_entry_t[] to pfd_entry_key_t[] — they have
                 * identical layout: char[65] + uint8_t[64] + uint64_t */
                cr = pfd_create_encrypted(title->local_path, &create_keys,
                                           (const pfd_entry_key_t *)enc_keys->entries,
                                           enc_keys->count);
            } else {
                ui_status("Creating PARAM.PFD: %s", title->game_code);
                pump_callbacks();
                cr = pfd_create(title->local_path, &create_keys);
            }

            if (enc_keys) free(enc_keys);

            if (cr != 0) {
                debug_log("resign_save: pfd_create failed for %s (save may not load)",
                          title->title_id);
                ui_status("PFD create failed: %s", title->game_code);
            } else {
                debug_log("resign_save: pfd_create success for %s", title->title_id);
                ui_status("PARAM.PFD created: %s", title->game_code);
            }
            return cr;
        }
    }

    debug_log("resign_save: resigning %s at %s", title->title_id, title->local_path);
    ui_status("Resigning save: %s", title->game_code);
    pump_callbacks();

    /* Prepare a local copy of keys with runtime identity */
    pfd_keys_t keys;
    memcpy(&keys, &g_keys, sizeof(keys));

    /* Set user_id (8-byte string, zero-padded, from the selected PS3 user).
     * Format: decimal string like "00000001" as raw bytes. */
    memset(keys.user_id, 0, 8);
    {
        const char *user_str = state->ps3_user;
        size_t ulen = strlen(user_str);
        if (ulen > 8) ulen = 8;
        memcpy(keys.user_id, user_str, ulen);
    }

    /* disc_hash_key: we use the fallback key since we don't have
     * a per-game key database. This works for most games. */
    memcpy(keys.disc_hash_key, keys.fallback_disc_hash_key, PFD_KEY_SIZE);

    /* Look up per-game secure_file_id for correct file hash HMAC keys */
    keys.has_secure_file_id = false;
    if (gamekeys_is_loaded()) {
        if (gamekeys_get_secure_file_id(title->game_code, "*",
                                         keys.secure_file_id)) {
            keys.has_secure_file_id = true;
            debug_log("resign_save: found secure_file_id for %s (resign path)",
                      title->game_code);
        } else {
            debug_log("resign_save: no secure_file_id for %s, using real_hash_key",
                      title->game_code);
        }
    }

    /* Step 1: Patch PARAM.SFO */
    char sfo_path[600];
    snprintf(sfo_path, sizeof(sfo_path), "%s/PARAM.SFO", title->local_path);
    {
        struct stat st;
        if (stat(sfo_path, &st) == 0) {
            sfo_patch_t sfo_p;
            memset(&sfo_p, 0, sizeof(sfo_p));
            sfo_p.flags = SFO_PATCH_FLAG_REMOVE_COPY_PROTECTION;
            sfo_p.user_id = (uint32_t)atoi(state->ps3_user);
            memcpy(sfo_p.psid, g_psid, 16);
            /* account_id: leave empty for now (we don't have it).
             * Apollo gets it from the console's login state.
             * The PSID and user_id are the critical fields. */
            memset(sfo_p.account_id, '0', SFO_ACCOUNT_ID_SIZE);
            sfo_p.account_id[SFO_ACCOUNT_ID_SIZE] = '\0';

            ui_status("Patching PARAM.SFO: %s", title->game_code);
            pump_callbacks();

            if (sfo_patch(sfo_path, &sfo_p) != 0) {
                debug_log("resign_save: SFO patch failed for %s", title->title_id);
                /* Non-fatal: continue to PFD resign anyway */
            }
        } else {
            debug_log("resign_save: no PARAM.SFO in %s", title->local_path);
        }
    }

    pump_callbacks();

    /* Step 2: Resign PARAM.PFD (recompute all HMAC hashes) */
    ui_status("Rebuilding PARAM.PFD: %s", title->game_code);
    pump_callbacks();

    int r = pfd_resign(title->local_path, &keys);
    if (r != 0) {
        debug_log("resign_save: PFD resign failed for %s", title->title_id);
        ui_status("Resign failed: %s (save may not load)", title->game_code);
        return -1;
    }

    ui_status("Resign complete: %s", title->game_code);
    pump_callbacks();

    debug_log("resign_save: success for %s", title->title_id);
    return 0;
}

int resign_pfd_only(const TitleInfo *title, const SyncState *state) {
    if (title->kind != SAVE_KIND_PS3) return 0;

    if (!g_initialized) {
        debug_log("resign_pfd_only: not initialized");
        return -1;
    }

    debug_log("resign_pfd_only: updating PFD hashes for %s at %s",
              title->title_id, title->local_path);
    ui_status("Rebuilding PARAM.PFD: %s", title->game_code);
    pump_callbacks();

    /* Same key setup as resign_save */
    pfd_keys_t keys;
    memcpy(&keys, &g_keys, sizeof(keys));
    memset(keys.user_id, 0, 8);
    {
        const char *user_str = state->ps3_user;
        size_t ulen = strlen(user_str);
        if (ulen > 8) ulen = 8;
        memcpy(keys.user_id, user_str, ulen);
    }
    memcpy(keys.disc_hash_key, keys.fallback_disc_hash_key, PFD_KEY_SIZE);

    /* Look up per-game secure_file_id for correct file hash HMAC keys */
    keys.has_secure_file_id = false;
    if (gamekeys_is_loaded()) {
        if (gamekeys_get_secure_file_id(title->game_code, "*",
                                         keys.secure_file_id)) {
            keys.has_secure_file_id = true;
            debug_log("resign_pfd_only: found secure_file_id for %s",
                      title->game_code);
        } else {
            debug_log("resign_pfd_only: no secure_file_id for %s, using real_hash_key",
                      title->game_code);
        }
    }

    int r = pfd_resign(title->local_path, &keys);
    if (r != 0) {
        debug_log("resign_pfd_only: pfd_resign failed for %s", title->title_id);
        return -1;
    }

    ui_status("PARAM.PFD updated: %s", title->game_code);
    debug_log("resign_pfd_only: success for %s", title->title_id);
    return 0;
}
