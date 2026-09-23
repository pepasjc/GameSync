#ifndef PS2SYNC_SAVES_H
#define PS2SYNC_SAVES_H

#include "common.h"

#include <stddef.h>
#include <stdint.h>

/*
 * PS2 save sync — VMC / MemCard Pro file path (phase 1).
 *
 * A "VMC" here is any full 8 MB PS2 memory-card image sitting on the mass:
 * storage root: an OPL virtual memory card (mass:/VMC/<name>.bin), a
 * MemCard Pro per-channel image copied over USB, or a raw physical-card dump.
 * Both the canonical .mc2 form (8388608 bytes) and the ECC .ps2 form
 * (8650752 bytes) are accepted; the server strips ECC on import.
 *
 * Upload pushes a whole card to POST /api/v1/saves/ps2-vmc/import, where the
 * server splits it into per-game saves keyed by disc serial.  Pull downloads
 * every PS2 save the server knows about back out as per-game .mc2 cards into
 * mass:/VMC/<SERIAL>.mc2, ready to mount in OPL.
 */

#define SAVES_MAX_VMC      128
#define SAVES_VMC_MC2_SIZE 8388608ULL   /* PS2 .mc2: 512 * 16384 */
#define SAVES_VMC_PS2_SIZE 8650752ULL   /* PS2 .ps2: 528 * 16384 (ECC spare) */
#define SAVES_VMC_PS1_SIZE 131072ULL    /* PS1 raw .mcd/.mcr: 16 * 8192 */
#define SAVES_VMC_VMP_SIZE 131200ULL    /* PS1 .vmp: 0x80 header + raw card */

typedef struct {
    char     path[SAVE_DIR_LEN];
    char     filename[128];
    char     serial[GAME_ID_LEN];   /* derived from filename ("" if none) */
    char     name[64];              /* game title (filled from server list) */
    uint64_t size;
    bool     has_ecc;          /* PS2 .ps2 (528-byte page) image */
    bool     is_ps1;           /* PS1 card image (128 KB / .vmp) */
} SaveVmc;

typedef struct {
    SaveVmc items[SAVES_MAX_VMC];
    int     count;
    char    last_error[128];
} SaveVmcList;

/* ---- physical memory card (mc0:) per-game sync ---- */

#define SAVES_MAX_MC_GAMES 128
#define SAVES_P2FD_MAX     (4 * 1024 * 1024)   /* restore payload cap */

typedef struct {
    char     dir[36];               /* save directory name on the card */
    char     serial[GAME_ID_LEN];   /* compact serial, e.g. SLUS20312 ("" if unknown) */
    char     name[64];              /* game name (filled from server list; "" if unknown) */
    int      file_count;
    uint32_t total_size;
} McGame;

typedef struct {
    McGame items[SAVES_MAX_MC_GAMES];
    int    count;
    int    port;                    /* memory-card port: 0=slot1, 1=slot2 */
    bool   is_ps1;                  /* card is a PS1 memory card */
    char   last_error[128];
} McGameList;

/* Enumerate save folders on the physical memory card in the given port
 * (0 = slot 1, 1 = slot 2).  Lists every directory; serial is "" when the
 * folder name carries no recognizable disc serial. */
void saves_scan_mcard(int port, McGameList *out);

/* Push one card game's files to the server (built into a card image there).
 * Returns 0 on success, negative on error; ``msg`` gets a result line. */
int  saves_upload_mc_game(const SyncState *state, int port, const McGame *game,
                          char *msg, size_t msg_size);

/* Push one PS1 save (a single file on a PS1 card) to the server. */
int  saves_upload_ps1_save(const SyncState *state, int port, const McGame *game,
                           char *msg, size_t msg_size);

/* Restore one PS1 save from the server back onto a physical PS1 card. */
int  saves_restore_ps1_save(const SyncState *state, int port, const char *serial,
                            char *msg, size_t msg_size);

/* Restore one game (by serial) from the server back onto the memory card.
 * Returns number of files written, or negative on error. */
int  saves_restore_mc_game(const SyncState *state, int port, const char *serial,
                           char *msg, size_t msg_size);

/* ---- server saves (shared "Server" view) ---- */

#define SAVES_MAX_SERVER 512

typedef struct {
    char     serial[GAME_ID_LEN];   /* server title id, e.g. SLUS20312 */
    char     name[64];              /* game name (or serial if unknown) */
    uint32_t timestamp;             /* server client_timestamp (last saved) */
    bool     is_ps1;
    bool     local;                 /* present on a scanned memory card */
} ServerSave;

typedef struct {
    ServerSave items[SAVES_MAX_SERVER];
    int        count;
    char       last_error[128];
} ServerSaveList;

/* Fetch all PS1+PS2 saves the server holds (GET /titles?console_type=...).
 * ``scratch`` is borrowed for the JSON response. */
void saves_fetch_server(const SyncState *state,
                        char *scratch, uint32_t scratch_size,
                        ServerSaveList *out);

/* Download a server save as a per-game card into mass:/VMC/<serial>.<ext>.
 * Returns 0 on success, negative on error. */
int  saves_download_server_to_vmc(const SyncState *state, const ServerSave *save,
                                  char *msg, size_t msg_size);

/* Broadcast a GameID over the MMCE protocol so a MemCard Pro 2 / SD2PSX
 * switches to that game's VMC channel. Returns 0 if a device responded. */
/* Detect what is in a memory-card slot: 0=empty, 1=card (gen1/plain), 2=MMCE
 * (MemCard Pro 2 / SD2PSX).  Safe: only MMCE-probes when a card is present. */
int  saves_mcp_detect(int port);

/* MCP GameID mode for saves_mcp_set_gameid. */
#define SAVES_MCP_AUTO 1
#define SAVES_MCP_GEN1 2
#define SAVES_MCP_GEN2 3

int  saves_mcp_set_gameid(const char *serial, int port, int mode,
                          char *msg, size_t msg_size);

/* Enumerate candidate 8 MB card images under the storage root (VMC/ + root). */
void saves_scan_local(SaveVmcList *out);

/* Upload one card image; server splits it into per-game saves.
 * Returns number of games imported, or negative on error.  ``msg`` receives a
 * human-readable result/error line. */
int  saves_upload_vmc(const SyncState *state, const SaveVmc *vmc,
                      char *msg, size_t msg_size);

/* Download every PS2 save on the server as a per-game card into mass:/VMC/.
 * ``scratch``/``scratch_size`` is borrowed for the titles JSON response.
 * Returns number of saves pulled, or negative on error. */
int  saves_pull_all(const SyncState *state,
                    char *scratch, uint32_t scratch_size,
                    char *msg, size_t msg_size);

#endif /* PS2SYNC_SAVES_H */
