/*
 * saves.c — PS2 save sync, VMC / MemCard Pro file path.
 *
 * The PS2 client does not parse the memory-card filesystem itself: it ships a
 * whole 8 MB card image to the server, which splits it into per-game saves
 * (see server/app/services/ps2mc.py).  This keeps the on-console code to file
 * I/O plus the existing socket HTTP client.
 */

#include "saves.h"
#include "http.h"
#include "roms.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <ctype.h>
#include <dirent.h>
#include <unistd.h>
#include <sys/stat.h>
#include <fcntl.h>
#include <strings.h>
#include <libmc.h>

#define NEWLIB_PORT_AWARE
#include <fileXio_rpc.h>

#define MC_PORT 0
#define MC_SLOT 0

/* libmc (mcOpen) takes IOP open flags, NOT newlib POSIX flags.  newlib's
 * O_RDONLY is 0, which mcman reads as "no permissions" -> mcRead returns
 * sceMcResDeniedPermit (-5).  Use the IOP values (ps2sdk issue #168). */
#define IOP_O_RDONLY 0x0001
#define IOP_O_WRONLY 0x0002
#define IOP_O_CREAT  0x0200
#define IOP_O_TRUNC  0x0400

/* MMCE devctl commands (mmceman mmce_cmds.h). GET_CARD is a cheap query used
 * to detect whether an MMCE device (MemCard Pro 2 / SD2PSX) is in the slot. */
#define MMCE_CMD_GET_CARD          0x3
#define MMCE_CMD_SET_GAMEID        0x8
#define MMCE_CMD_SET_GAMEID_LEGACY 0x20   /* gen1 MemCard Pro (0x21 protocol) */

/* Aligned bounce buffer for memory-card DMA reads/writes. */
static unsigned char g_mc_chunk[32768] __attribute__((aligned(64)));

static void put_u32(uint8_t *p, uint32_t v) {
    p[0] = v & 0xFF; p[1] = (v >> 8) & 0xFF;
    p[2] = (v >> 16) & 0xFF; p[3] = (v >> 24) & 0xFF;
}

static uint32_t get_u32(const uint8_t *p) {
    return (uint32_t)p[0] | ((uint32_t)p[1] << 8) |
           ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24);
}

/* ---- local card-image enumeration ---- */

static bool mc_serial_from_dir(const char *name, char *out, size_t out_size);

static bool is_vmc_size(uint64_t size, bool *has_ecc, bool *is_ps1) {
    if (has_ecc) *has_ecc = false;
    if (is_ps1)  *is_ps1 = false;
    if (size == SAVES_VMC_MC2_SIZE) { return true; }
    if (size == SAVES_VMC_PS2_SIZE) { if (has_ecc) *has_ecc = true; return true; }
    if (size == SAVES_VMC_PS1_SIZE || size == SAVES_VMC_VMP_SIZE) {
        if (is_ps1) *is_ps1 = true;
        return true;
    }
    return false;
}

static int scan_dir(const char *dir_path, SaveVmcList *out) {
    DIR *d = opendir(dir_path);
    if (!d) return 0;

    int added = 0;
    struct dirent *de;
    while ((de = readdir(d)) != NULL) {
        if (out->count >= SAVES_MAX_VMC) break;
        if (de->d_name[0] == '.') continue;

        char full[SAVE_DIR_LEN];
        snprintf(full, sizeof(full), "%s/%s", dir_path, de->d_name);

        struct stat st;
        if (stat(full, &st) != 0) continue;
        if ((st.st_mode & S_IFMT) != S_IFREG) continue;

        bool has_ecc = false, is_ps1 = false;
        if (!is_vmc_size((uint64_t)st.st_size, &has_ecc, &is_ps1)) continue;

        SaveVmc *e = &out->items[out->count];
        memset(e, 0, sizeof(*e));
        strncpy(e->path, full, sizeof(e->path) - 1);
        strncpy(e->filename, de->d_name, sizeof(e->filename) - 1);
        if (!mc_serial_from_dir(de->d_name, e->serial, sizeof(e->serial)))
            e->serial[0] = '\0';
        e->size = (uint64_t)st.st_size;
        e->has_ecc = has_ecc;
        e->is_ps1 = is_ps1;
        out->count++;
        added++;
    }
    closedir(d);
    return added;
}

void saves_scan_local(SaveVmcList *out) {
    out->count = 0;
    out->last_error[0] = '\0';

    const char *root = roms_storage_root();
    if (!root || !root[0]) {
        snprintf(out->last_error, sizeof(out->last_error), "Storage not ready");
        return;
    }

    char dir[SAVE_DIR_LEN];
    snprintf(dir, sizeof(dir), "%s/VMC", root);
    scan_dir(dir, out);
    scan_dir(root, out);

    if (out->count == 0) {
        snprintf(out->last_error, sizeof(out->last_error),
                 "No PS1/PS2 card images in %s or %s/VMC", root, root);
    }
}

/* ---- helpers ---- */

static int count_substr(const char *hay, const char *needle) {
    int n = 0;
    size_t nlen = strlen(needle);
    for (const char *p = hay; (p = strstr(p, needle)) != NULL; p += nlen) n++;
    return n;
}

/* Pull the value of the next ``"title_id":"VALUE"`` pair starting at *cursor.
 * Advances *cursor past it.  Returns false when no more remain. */
static bool next_title_id(const char **cursor, char *out, size_t out_size) {
    const char *p = strstr(*cursor, "\"title_id\"");
    if (!p) return false;
    p = strchr(p + 10, ':');
    if (!p) return false;
    p++;
    while (*p == ' ' || *p == '\t') p++;
    if (*p != '"') { *cursor = p; return false; }
    p++;
    size_t i = 0;
    while (*p && *p != '"' && i + 1 < out_size) out[i++] = *p++;
    out[i] = '\0';
    *cursor = (*p == '"') ? p + 1 : p;
    return i > 0;
}

/* ---- physical memory card (mc0:) per-game sync ---- */

static const char *PS2_PFX[] = {
    "SLUS", "SCUS", "SLES", "SCES", "SLPS", "SLPM", "SLKA",
    "SCAJ", "SLAJ", "SCKA", "SLPN", "TCPX", "PBPX", "PCPX", NULL
};

/* Map a save-dir name (BASLUS-20312) to the compact serial (SLUS20312).
 * Mirrors server ps2mc.serial_from_dirname.  Returns false for non-game dirs. */
static bool mc_serial_from_dir(const char *name, char *out, size_t out_size) {
    if (out_size < 10) return false;
    size_t len = strlen(name);
    for (size_t i = 0; i + 4 <= len; i++) {
        for (int p = 0; PS2_PFX[p]; p++) {
            if (strncasecmp(name + i, PS2_PFX[p], 4) != 0) continue;
            const char *q = name + i + 4;
            char digits[8];
            int d = 0;
            while (*q && d < 5) {
                if (*q >= '0' && *q <= '9') digits[d++] = *q++;
                else if (*q == '.' || *q == '-' || *q == '_' || *q == ' ') q++;
                else break;
            }
            if (d == 5) {
                snprintf(out, out_size, "%c%c%c%c%c%c%c%c%c",
                         (char)toupper((unsigned char)PS2_PFX[p][0]),
                         (char)toupper((unsigned char)PS2_PFX[p][1]),
                         (char)toupper((unsigned char)PS2_PFX[p][2]),
                         (char)toupper((unsigned char)PS2_PFX[p][3]),
                         digits[0], digits[1], digits[2], digits[3], digits[4]);
                return true;
            }
        }
    }
    return false;
}

static int mc_wait(void) {
    int cmd = 0, result = 0;
    int rc = mcSync(MC_WAIT, &cmd, &result);
    return rc < 0 ? rc : result;
}

/* Set a file/dir attribute word.  mcman denies reading copy-protected saves;
 * clearing MC_ATTR_PROTECTED before the read and restoring after is the
 * standard save-manager workaround.  Returns mcman result (<0 on failure). */
static int mc_set_attr(int port, const char *path, unsigned short attr) {
    sceMcTblGetDir info;
    memset(&info, 0, sizeof(info));
    info.AttrFile = attr;
    if (mcSetFileInfo(port, MC_SLOT, path, &info, sceMcFileInfoAttr) < 0)
        return -100;
    return mc_wait();
}

/* Find a top-level directory's attribute word (0 if not found). */
static unsigned short mc_dir_attr(int port, const char *dir) {
    static sceMcTblGetDir t[SAVES_MAX_MC_GAMES] __attribute__((aligned(64)));
    if (mcGetDir(port, MC_SLOT, "/*", 0, SAVES_MAX_MC_GAMES, t) < 0) return 0;
    int n = mc_wait();
    if (n < 0) return 0;
    for (int i = 0; i < n; i++) {
        if (!(t[i].AttrFile & MC_ATTR_SUBDIR)) continue;
        if (strcmp((const char *)t[i].EntryName, dir) == 0) return t[i].AttrFile;
    }
    return 0;
}

/* Probe a card slot. Returns the card type (sceMcTypeNoCard==0 when empty),
 * negative on RPC failure; *formatted set when the card is formatted.
 *
 * mcman returns sceMcResChangedCard (-1) on the first probe after a card is
 * (re)inserted, often with type still 0 — so retry once to get a stable type. */
static int mc_probe(int port, bool *formatted) {
    for (int attempt = 0; attempt < 3; attempt++) {
        int type = 0, free_clusters = 0, fmt = 0;
        if (mcGetInfo(port, MC_SLOT, &type, &free_clusters, &fmt) < 0) return -100;
        int res = mc_wait();      /* 0 ok, -1 card changed, -2 unformatted */
        if (formatted) *formatted = (fmt != 0);
        if (res == -2 && formatted) *formatted = false;
        if (res == -1 && type == 0) continue;   /* changed-card; re-probe */
        return type;
    }
    return 0;
}

/* Free space in the card slot, in mcman "clusters" (PS2: 1 KB each).
 * Returns -1 on probe failure. */
static int mc_free_clusters(int port) {
    for (int attempt = 0; attempt < 3; attempt++) {
        int type = 0, free_clusters = 0, fmt = 0;
        if (mcGetInfo(port, MC_SLOT, &type, &free_clusters, &fmt) < 0) return -1;
        int res = mc_wait();
        if (res == -1 && type == 0) continue;   /* changed-card; re-probe */
        return free_clusters;
    }
    return -1;
}

static void mc_dir_stats(int port, const char *dir, int *file_count, uint32_t *total) {
    static sceMcTblGetDir t[64] __attribute__((aligned(64)));
    char pat[64];
    snprintf(pat, sizeof(pat), "/%s/*", dir);
    if (mcGetDir(port, MC_SLOT, pat, 0, 64, t) < 0) return;
    int n = mc_wait();
    if (n < 0) return;
    for (int i = 0; i < n; i++) {
        if (!(t[i].AttrFile & MC_ATTR_FILE)) continue;
        if (t[i].EntryName[0] == '.') continue;
        (*file_count)++;
        *total += t[i].FileSizeByte;
    }
}

void saves_scan_mcard(int port, McGameList *out) {
    out->count = 0;
    out->port = port;
    out->last_error[0] = '\0';

    bool formatted = false;
    int type = mc_probe(port, &formatted);
    if (type == -100) {
        snprintf(out->last_error, sizeof(out->last_error),
                 "mcGetInfo failed (slot %d) - MCMAN/MCSERV?", port + 1);
        return;
    }
    if (type == 0) {        /* sceMcTypeNoCard */
        snprintf(out->last_error, sizeof(out->last_error),
                 "No memory card in slot %d", port + 1);
        return;
    }
    if (!formatted) {
        snprintf(out->last_error, sizeof(out->last_error),
                 "Card in slot %d is not formatted", port + 1);
        return;
    }

    /* PS1 cards present saves as top-level FILES (one per save); PS2 cards
     * present each game as a sub-directory. */
    out->is_ps1 = (type == MC_TYPE_PSX);

    static sceMcTblGetDir table[SAVES_MAX_MC_GAMES] __attribute__((aligned(64)));
    if (mcGetDir(port, MC_SLOT, "/*", 0, SAVES_MAX_MC_GAMES, table) < 0) {
        snprintf(out->last_error, sizeof(out->last_error),
                 "mcGetDir failed to start (slot %d)", port + 1);
        return;
    }
    int n = mc_wait();
    if (n < 0) {
        snprintf(out->last_error, sizeof(out->last_error),
                 "Cannot read memory card slot %d (%d)", port + 1, n);
        return;
    }

    int want = out->is_ps1 ? MC_ATTR_FILE : MC_ATTR_SUBDIR;
    int seen = 0;
    for (int i = 0; i < n && out->count < SAVES_MAX_MC_GAMES; i++) {
        if (!(table[i].AttrFile & want)) continue;
        const char *name = (const char *)table[i].EntryName;
        if (name[0] == '.') continue;
        seen++;

        McGame *g = &out->items[out->count];
        memset(g, 0, sizeof(*g));
        strncpy(g->dir, name, sizeof(g->dir) - 1);
        if (!mc_serial_from_dir(name, g->serial, sizeof(g->serial))) {
            g->serial[0] = '\0';        /* keep listed; upload will skip */
        }
        if (out->is_ps1) {
            g->file_count = 1;
            g->total_size = table[i].FileSizeByte;
        } else {
            mc_dir_stats(port, name, &g->file_count, &g->total_size);
        }
        out->count++;
    }

    if (out->count == 0) {
        snprintf(out->last_error, sizeof(out->last_error),
                 "Slot %d (%s): %d entries, %d %s, no saves",
                 port + 1, out->is_ps1 ? "PS1" : "PS2", n, seen,
                 out->is_ps1 ? "files" : "dirs");
    }
}

int saves_upload_mc_game(const SyncState *state, int port, const McGame *game,
                         char *msg, size_t msg_size) {
    if (game->serial[0] == '\0') {
        snprintf(msg, msg_size, "No disc serial for %s", game->dir);
        return -1;
    }
    static sceMcTblGetDir t[SAVES_MAX_MC_GAMES] __attribute__((aligned(64)));
    char pat[64];
    snprintf(pat, sizeof(pat), "/%s/*", game->dir);
    if (mcGetDir(port, MC_SLOT, pat, 0, SAVES_MAX_MC_GAMES, t) < 0) {
        snprintf(msg, msg_size, "mcGetDir failed for %s", game->dir);
        return -1;
    }
    int n = mc_wait();
    if (n < 0) {
        snprintf(msg, msg_size, "Cannot list %s (%d)", game->dir, n);
        return -1;
    }

    static char names[SAVES_MAX_MC_GAMES][32];
    static uint32_t sizes[SAVES_MAX_MC_GAMES];
    static unsigned short attrs[SAVES_MAX_MC_GAMES];
    int fc = 0;
    uint64_t payload_size = 4 + 4 + 4 + strlen(game->dir) + 4;
    for (int i = 0; i < n && fc < SAVES_MAX_MC_GAMES; i++) {
        if (!(t[i].AttrFile & MC_ATTR_FILE)) continue;
        if (t[i].EntryName[0] == '.') continue;
        strncpy(names[fc], (const char *)t[i].EntryName, 31);
        names[fc][31] = '\0';
        sizes[fc] = t[i].FileSizeByte;
        attrs[fc] = t[i].AttrFile;
        payload_size += 4 + strlen(names[fc]) + 4 + sizes[fc];
        fc++;
    }
    if (fc == 0) {
        snprintf(msg, msg_size, "No files in %s", game->dir);
        return -1;
    }
    if (payload_size > MAX_FILE_SIZE) {
        snprintf(msg, msg_size, "Save too large (%llu)",
                 (unsigned long long)payload_size);
        return -1;
    }

    uint8_t *buf = (uint8_t *)malloc((size_t)payload_size);
    if (!buf) {
        snprintf(msg, msg_size, "Out of memory");
        return -1;
    }

    size_t pos = 0, dirlen = strlen(game->dir);
    memcpy(buf + pos, "P2FD", 4); pos += 4;
    put_u32(buf + pos, 1); pos += 4;
    put_u32(buf + pos, (uint32_t)dirlen); pos += 4;
    memcpy(buf + pos, game->dir, dirlen); pos += dirlen;
    put_u32(buf + pos, (uint32_t)fc); pos += 4;

    /* The whole save directory (and each file) may be copy-protected, which
     * makes mcman deny reads (-5).  Clear DupProhibit + force Readable on the
     * directory for the duration of the read, then restore it. */
    char dpath[40];
    snprintf(dpath, sizeof(dpath), "/%s", game->dir);
    unsigned short dirattr = mc_dir_attr(port, game->dir);
    bool dprot = (dirattr & MC_ATTR_PROTECTED) != 0;
    if (dprot)
        mc_set_attr(port, dpath,
                    (dirattr & ~MC_ATTR_PROTECTED) | MC_ATTR_READABLE | MC_ATTR_WRITEABLE);

    bool ok = true;
    char failmsg[96];
    failmsg[0] = '\0';
    for (int i = 0; i < fc && ok; i++) {
        size_t nl = strlen(names[i]);
        put_u32(buf + pos, (uint32_t)nl); pos += 4;
        memcpy(buf + pos, names[i], nl); pos += nl;
        put_u32(buf + pos, sizes[i]); pos += 4;

        char fp[80];
        snprintf(fp, sizeof(fp), "/%s/%s", game->dir, names[i]);

        /* Clear protection + ensure Readable on the file too. */
        bool protd = (attrs[i] & (MC_ATTR_PROTECTED | MC_ATTR_READABLE)) != MC_ATTR_READABLE;
        if (protd) {
            int sr = mc_set_attr(port, fp,
                                 (attrs[i] & ~MC_ATTR_PROTECTED) | MC_ATTR_READABLE);
            if (sr < 0) {
                snprintf(failmsg, sizeof(failmsg), "setattr %s rc=%d", names[i], sr);
                ok = false; break;
            }
        }

        if (mcOpen(port, MC_SLOT, fp, IOP_O_RDONLY) < 0) {
            if (protd) mc_set_attr(port, fp, attrs[i]);
            snprintf(failmsg, sizeof(failmsg), "open start %s", names[i]);
            ok = false; break;
        }
        int fd = mc_wait();
        if (fd < 0) {
            if (protd) mc_set_attr(port, fp, attrs[i]);
            snprintf(failmsg, sizeof(failmsg), "open %s rc=%d", names[i], fd);
            ok = false; break;
        }

        uint32_t remaining = sizes[i];
        while (remaining > 0) {
            int take = remaining > sizeof(g_mc_chunk)
                     ? (int)sizeof(g_mc_chunk) : (int)remaining;
            if (mcRead(fd, g_mc_chunk, take) < 0) {
                snprintf(failmsg, sizeof(failmsg), "read start %s", names[i]);
                ok = false; break;
            }
            int got = mc_wait();
            if (got <= 0) {
                snprintf(failmsg, sizeof(failmsg), "read %s rc=%d off=%u",
                         names[i], got, (unsigned)(sizes[i] - remaining));
                ok = false; break;
            }
            memcpy(buf + pos, g_mc_chunk, got);
            pos += got;
            remaining -= got;
        }
        mcClose(fd);
        mc_wait();
        if (protd) mc_set_attr(port, fp, attrs[i]);   /* restore file protection */
    }

    if (dprot) mc_set_attr(port, dpath, dirattr);     /* restore dir protection */

    if (!ok) {
        free(buf);
        snprintf(msg, msg_size, "MC read fail: %s", failmsg);
        return -1;
    }

    char rpath[160];
    snprintf(rpath, sizeof(rpath),
             "/api/v1/saves/%s/ps2-files?console_id=%s",
             game->serial, state->console_id);

    HttpRequest req;
    memset(&req, 0, sizeof(req));
    req.server_url        = state->server_url;
    req.api_key           = state->api_key;
    req.path              = rpath;
    req.method            = "POST";
    req.body              = buf;
    req.body_len          = (uint32_t)pos;
    req.body_content_type = "application/octet-stream";

    static uint8_t resp[4096];
    int status = 0;
    int rc = http_get_buf(&req, resp, sizeof(resp), &status);
    free(buf);

    if (rc < 0) {
        snprintf(msg, msg_size, "Upload network error (%d)", rc);
        return -1;
    }
    if (status != 200) {
        snprintf(msg, msg_size, "Server rejected save (HTTP %d)", status);
        return -1;
    }
    snprintf(msg, msg_size, "Uploaded %s (%d file%s)",
             game->serial, fc, fc == 1 ? "" : "s");
    return 0;
}

/* Find the attribute word of a root-level entry by name (0 if not found). */
static unsigned short mc_entry_attr(int port, const char *name) {
    static sceMcTblGetDir t[SAVES_MAX_MC_GAMES] __attribute__((aligned(64)));
    if (mcGetDir(port, MC_SLOT, "/*", 0, SAVES_MAX_MC_GAMES, t) < 0) return 0;
    int n = mc_wait();
    if (n < 0) return 0;
    for (int i = 0; i < n; i++)
        if (strcmp((const char *)t[i].EntryName, name) == 0) return t[i].AttrFile;
    return 0;
}

int saves_upload_ps1_save(const SyncState *state, int port, const McGame *game,
                          char *msg, size_t msg_size) {
    if (game->serial[0] == '\0') {
        snprintf(msg, msg_size, "No serial for %s", game->dir);
        return -1;
    }
    uint32_t size = game->total_size;
    if (size == 0 || size > MAX_FILE_SIZE) {
        snprintf(msg, msg_size, "Bad PS1 save size (%u)", size);
        return -1;
    }

    uint8_t *buf = (uint8_t *)malloc(size);
    if (!buf) { snprintf(msg, msg_size, "Out of memory"); return -1; }

    char fp[64];
    snprintf(fp, sizeof(fp), "/%s", game->dir);

    unsigned short attr = mc_entry_attr(port, game->dir);
    bool protd = (attr & (MC_ATTR_PROTECTED | MC_ATTR_READABLE)) != MC_ATTR_READABLE;
    if (protd) mc_set_attr(port, fp, (attr & ~MC_ATTR_PROTECTED) | MC_ATTR_READABLE);

    int rc = -1;
    if (mcOpen(port, MC_SLOT, fp, IOP_O_RDONLY) >= 0) {
        int fd = mc_wait();
        if (fd >= 0) {
            uint32_t rem = size;
            size_t pos = 0;
            bool ok = true;
            while (rem > 0) {
                int take = rem > sizeof(g_mc_chunk) ? (int)sizeof(g_mc_chunk) : (int)rem;
                if (mcRead(fd, g_mc_chunk, take) < 0) { ok = false; break; }
                int got = mc_wait();
                if (got <= 0) { ok = false; break; }
                memcpy(buf + pos, g_mc_chunk, got);
                pos += got;
                rem -= got;
            }
            mcClose(fd);
            mc_wait();
            if (ok) rc = 0;
        }
    }
    if (protd) mc_set_attr(port, fp, attr);

    if (rc != 0) {
        free(buf);
        snprintf(msg, msg_size, "PS1 read fail %s", game->dir);
        return -1;
    }

    char rpath[200];
    snprintf(rpath, sizeof(rpath),
             "/api/v1/saves/%s/ps1-save?name=%s&console_id=%s",
             game->serial, game->dir, state->console_id);

    HttpRequest req;
    memset(&req, 0, sizeof(req));
    req.server_url        = state->server_url;
    req.api_key           = state->api_key;
    req.path              = rpath;
    req.method            = "POST";
    req.body              = buf;
    req.body_len          = size;
    req.body_content_type = "application/octet-stream";

    static uint8_t resp[2048];
    int status = 0;
    int hr = http_get_buf(&req, resp, sizeof(resp), &status);
    free(buf);

    if (hr < 0)        { snprintf(msg, msg_size, "Upload net err %d", hr); return -1; }
    if (status != 200) { snprintf(msg, msg_size, "Server HTTP %d", status); return -1; }
    snprintf(msg, msg_size, "Uploaded PS1 %s", game->serial);
    return 0;
}

int saves_restore_ps1_save(const SyncState *state, int port, const char *serial,
                           char *msg, size_t msg_size) {
    uint8_t *buf = (uint8_t *)malloc(SAVES_P2FD_MAX);
    if (!buf) { snprintf(msg, msg_size, "Out of memory"); return -1; }

    char rpath[160];
    snprintf(rpath, sizeof(rpath), "/api/v1/saves/%s/ps1-save", serial);
    HttpRequest req;
    memset(&req, 0, sizeof(req));
    req.server_url = state->server_url;
    req.api_key    = state->api_key;
    req.path       = rpath;
    req.method     = "GET";

    int status = 0;
    int n = http_get_buf(&req, buf, SAVES_P2FD_MAX, &status);
    if (n < 0 || status != 200) {
        free(buf);
        snprintf(msg, msg_size, "Fetch failed (rc=%d http=%d)", n, status);
        return -1;
    }
    /* Payload: u32 name_len | name | raw save data */
    if (n < 4) { free(buf); snprintf(msg, msg_size, "Bad PS1 payload"); return -1; }
    uint32_t namelen = get_u32(buf);
    if (namelen == 0 || namelen >= 36 || (uint32_t)n < 4 + namelen) {
        free(buf);
        snprintf(msg, msg_size, "Corrupt PS1 payload");
        return -1;
    }
    char name[36];
    memcpy(name, buf + 4, namelen);
    name[namelen] = '\0';
    uint8_t *data = buf + 4 + namelen;
    uint32_t datalen = (uint32_t)n - 4 - namelen;

    char fp[64];
    snprintf(fp, sizeof(fp), "/%s", name);

    /* Free-space check when the save isn't already present (PS1 block = 8 KB;
     * mcman reports free in 1 KB clusters). */
    if (mc_entry_attr(port, name) == 0) {
        uint32_t need_kb = (datalen + 1023) / 1024;
        int freec = mc_free_clusters(port);
        if (freec >= 0 && (uint32_t)freec < need_kb) {
            free(buf);
            snprintf(msg, msg_size,
                     "Not enough space: need %u KB, free %d KB. Free space first.",
                     need_kb, freec);
            return -1;
        }
    }

    mcDelete(port, MC_SLOT, fp);
    mc_wait();
    if (mcOpen(port, MC_SLOT, fp, IOP_O_WRONLY | IOP_O_CREAT | IOP_O_TRUNC) < 0) {
        free(buf);
        snprintf(msg, msg_size, "PS1 create failed %s", name);
        return -1;
    }
    int fd = mc_wait();
    if (fd < 0) {
        free(buf);
        snprintf(msg, msg_size, "PS1 open failed %s rc=%d", name, fd);
        return -1;
    }

    bool ok = true;
    uint32_t rem = datalen;
    size_t src = 0;
    while (rem > 0) {
        int take = rem > sizeof(g_mc_chunk) ? (int)sizeof(g_mc_chunk) : (int)rem;
        memcpy(g_mc_chunk, data + src, take);
        if (mcWrite(fd, g_mc_chunk, take) < 0) { ok = false; break; }
        int w = mc_wait();
        if (w < 0) { ok = false; break; }
        src += w;
        rem -= w;
    }
    mcFlush(fd);
    mc_wait();
    mcClose(fd);
    mc_wait();
    free(buf);

    if (!ok) {
        snprintf(msg, msg_size, "PS1 write failed %s", name);
        return -1;
    }
    snprintf(msg, msg_size, "Restored PS1 %s", serial);
    return 0;
}

int saves_restore_mc_game(const SyncState *state, int port, const char *serial,
                          char *msg, size_t msg_size) {
    uint8_t *buf = (uint8_t *)malloc(SAVES_P2FD_MAX);
    if (!buf) {
        snprintf(msg, msg_size, "Out of memory");
        return -1;
    }

    char rpath[160];
    snprintf(rpath, sizeof(rpath), "/api/v1/saves/%s/ps2-files", serial);
    HttpRequest req;
    memset(&req, 0, sizeof(req));
    req.server_url = state->server_url;
    req.api_key    = state->api_key;
    req.path       = rpath;
    req.method     = "GET";

    int status = 0;
    int n = http_get_buf(&req, buf, SAVES_P2FD_MAX, &status);
    if (n < 0 || status != 200) {
        free(buf);
        snprintf(msg, msg_size, "Fetch failed (rc=%d http=%d)", n, status);
        return -1;
    }
    if (n < 16 || memcmp(buf, "P2FD", 4) != 0) {
        free(buf);
        snprintf(msg, msg_size, "Bad save payload");
        return -1;
    }

    size_t pos = 4;
    uint32_t ver = get_u32(buf + pos); pos += 4;
    if (ver != 1) {
        free(buf);
        snprintf(msg, msg_size, "Unsupported payload version %u", ver);
        return -1;
    }
    uint32_t dirlen = get_u32(buf + pos); pos += 4;
    char dir[36];
    if (dirlen == 0 || dirlen >= sizeof(dir) || pos + dirlen > (size_t)n) {
        free(buf);
        snprintf(msg, msg_size, "Corrupt save directory");
        return -1;
    }
    memcpy(dir, buf + pos, dirlen); dir[dirlen] = '\0'; pos += dirlen;
    uint32_t fcount = get_u32(buf + pos); pos += 4;

    /* Free-space check: only when the game isn't already on the card (an
     * overwrite reuses its existing clusters).  Pre-scan the file table to sum
     * the clusters this save needs (1 KB clusters + 1 for the directory). */
    bool exists = mc_dir_attr(port, dir) != 0;
    if (!exists) {
        size_t scan = pos;
        uint32_t need = 1;                  /* directory */
        bool scan_ok = true;
        for (uint32_t f = 0; f < fcount; f++) {
            if (scan + 4 > (size_t)n) { scan_ok = false; break; }
            uint32_t snl = get_u32(buf + scan); scan += 4 + snl;
            if (scan + 4 > (size_t)n) { scan_ok = false; break; }
            uint32_t sdl = get_u32(buf + scan); scan += 4 + sdl;
            need += (sdl + 1023) / 1024;
        }
        int freec = mc_free_clusters(port);
        if (scan_ok && freec >= 0 && (uint32_t)freec < need) {
            free(buf);
            snprintf(msg, msg_size,
                     "Not enough space: need %u KB, free %d KB. Free space first.",
                     need, freec);
            return -1;
        }
    }

    char dpath[40];
    snprintf(dpath, sizeof(dpath), "/%s", dir);
    mcMkDir(port, MC_SLOT, dpath);
    mc_wait();

    int written = 0;
    bool ok = true;
    for (uint32_t f = 0; f < fcount && ok; f++) {
        if (pos + 4 > (size_t)n) { ok = false; break; }
        uint32_t nl = get_u32(buf + pos); pos += 4;
        char fname[40];
        if (nl == 0 || nl >= sizeof(fname) || pos + nl > (size_t)n) { ok = false; break; }
        memcpy(fname, buf + pos, nl); fname[nl] = '\0'; pos += nl;
        if (pos + 4 > (size_t)n) { ok = false; break; }
        uint32_t dl = get_u32(buf + pos); pos += 4;
        if (pos + dl > (size_t)n) { ok = false; break; }

        char fp[80];
        snprintf(fp, sizeof(fp), "/%s/%s", dir, fname);
        mcDelete(port, MC_SLOT, fp);
        mc_wait();
        if (mcOpen(port, MC_SLOT, fp, IOP_O_WRONLY | IOP_O_CREAT | IOP_O_TRUNC) < 0) { ok = false; break; }
        int fd = mc_wait();
        if (fd < 0) { ok = false; break; }

        uint32_t remaining = dl;
        size_t src = pos;
        while (remaining > 0) {
            int take = remaining > sizeof(g_mc_chunk)
                     ? (int)sizeof(g_mc_chunk) : (int)remaining;
            memcpy(g_mc_chunk, buf + src, take);
            if (mcWrite(fd, g_mc_chunk, take) < 0) { ok = false; break; }
            int w = mc_wait();
            if (w < 0) { ok = false; break; }
            src += w;
            remaining -= w;
        }
        mcFlush(fd);
        mc_wait();
        mcClose(fd);
        mc_wait();
        pos += dl;
        written++;
    }
    free(buf);

    if (!ok) {
        snprintf(msg, msg_size, "Restore failed in %s", dir);
        return -1;
    }
    snprintf(msg, msg_size, "Restored %s (%d file%s)",
             serial, written, written == 1 ? "" : "s");
    return written;
}

/* ---- upload (push whole card) ---- */

int saves_upload_vmc(const SyncState *state, const SaveVmc *vmc,
                     char *msg, size_t msg_size) {
    const char *path = vmc->path;
    FILE *fp = fopen(path, "rb");
    if (!fp) {
        snprintf(msg, msg_size, "Cannot open %s", path);
        return -1;
    }
    fseek(fp, 0, SEEK_END);
    long sz = ftell(fp);
    fseek(fp, 0, SEEK_SET);
    if (sz <= 0 || (uint64_t)sz > MAX_FILE_SIZE) {
        fclose(fp);
        snprintf(msg, msg_size, "Bad card size (%ld)", sz);
        return -1;
    }

    uint8_t *buf = (uint8_t *)malloc((size_t)sz);
    if (!buf) {
        fclose(fp);
        snprintf(msg, msg_size, "Out of memory for %ld-byte card", sz);
        return -1;
    }
    size_t rd = fread(buf, 1, (size_t)sz, fp);
    fclose(fp);
    if ((long)rd != sz) {
        free(buf);
        snprintf(msg, msg_size, "Read failed (%zu/%ld)", rd, sz);
        return -1;
    }

    char rpath[160];
    snprintf(rpath, sizeof(rpath),
             "/api/v1/saves/%s/import?console_id=%s",
             vmc->is_ps1 ? "ps1-vmc" : "ps2-vmc", state->console_id);

    HttpRequest req;
    memset(&req, 0, sizeof(req));
    req.server_url        = state->server_url;
    req.api_key           = state->api_key;
    req.path              = rpath;
    req.method            = "POST";
    req.body              = buf;
    req.body_len          = (uint32_t)sz;
    req.body_content_type = "application/octet-stream";

    static uint8_t resp[8192];
    int status = 0;
    int n = http_get_buf(&req, resp, sizeof(resp), &status);
    free(buf);

    if (n < 0) {
        snprintf(msg, msg_size, "Upload network error (%d)", n);
        return -1;
    }
    if (status != 200) {
        snprintf(msg, msg_size, "Server rejected card (HTTP %d)", status);
        return -1;
    }

    int imported = count_substr((char *)resp, "\"serial\"");
    snprintf(msg, msg_size, "Imported %d game save(s)", imported);
    return imported;
}

/* ---- pull (download every PS2 save as a per-game card) ---- */

/* Pull every save for one system into mass:/VMC/<serial>.<ext>.
 * card_endpoint is the per-serial card path suffix ("ps2-card"/"ps1-card").
 * Returns count pulled, or negative on titles-fetch error. */
static int pull_system(const SyncState *state, const char *system_code,
                       const char *card_endpoint, const char *ext,
                       const char *vmcdir,
                       char *scratch, uint32_t scratch_size) {
    char tpath[80];
    snprintf(tpath, sizeof(tpath), "/api/v1/titles?console_type=%s", system_code);

    HttpRequest req;
    memset(&req, 0, sizeof(req));
    req.server_url = state->server_url;
    req.api_key    = state->api_key;
    req.path       = tpath;
    req.method     = "GET";

    int status = 0;
    int n = http_get_buf(&req, (uint8_t *)scratch, scratch_size, &status);
    if (n < 0 || status != 200) return -1;
    scratch[(uint32_t)n < scratch_size ? (uint32_t)n : scratch_size - 1] = '\0';

    int pulled = 0;
    const char *cursor = scratch;
    char serial[GAME_ID_LEN];
    while (next_title_id(&cursor, serial, sizeof(serial))) {
        char url[160];
        snprintf(url, sizeof(url), "/api/v1/saves/%s/%s", serial, card_endpoint);
        char out_path[SAVE_DIR_LEN];
        snprintf(out_path, sizeof(out_path), "%s/%s.%s", vmcdir, serial, ext);

        FILE *fp = fopen(out_path, "wb");
        if (!fp) continue;
        static char vbuf[262144];
        setvbuf(fp, vbuf, _IOFBF, sizeof(vbuf));

        HttpRequest dreq;
        memset(&dreq, 0, sizeof(dreq));
        dreq.server_url = state->server_url;
        dreq.api_key    = state->api_key;
        dreq.path       = url;
        dreq.method     = "GET";

        HttpResponseInfo info;
        int rc = http_get_stream(&dreq, fp, NULL, &info);
        fclose(fp);

        if (rc == 0 && info.status >= 200 && info.status < 300) pulled++;
        else remove(out_path);
    }
    return pulled;
}

int saves_pull_all(const SyncState *state,
                   char *scratch, uint32_t scratch_size,
                   char *msg, size_t msg_size) {
    char vmcdir[SAVE_DIR_LEN];
    snprintf(vmcdir, sizeof(vmcdir), "%s/VMC", roms_storage_root());
    roms_mkdir_p(vmcdir);

    int ps2 = pull_system(state, "PS2", "ps2-card", "mc2", vmcdir,
                          scratch, scratch_size);
    int ps1 = pull_system(state, "PS1", "ps1-card", "mcd", vmcdir,
                          scratch, scratch_size);

    if (ps2 < 0 && ps1 < 0) {
        snprintf(msg, msg_size, "Titles fetch failed");
        return -1;
    }
    int total = (ps2 < 0 ? 0 : ps2) + (ps1 < 0 ? 0 : ps1);
    snprintf(msg, msg_size, "Pulled %d save(s) into VMC/ (PS2 %d, PS1 %d)",
             total, ps2 < 0 ? 0 : ps2, ps1 < 0 ? 0 : ps1);
    return total;
}

/* ---- server saves (shared "Server" view) ---- */

static bool json_field_str(const char *start, const char *limit,
                           const char *key, char *out, size_t out_size) {
    const char *p = strstr(start, key);
    if (!p || (limit && p >= limit)) return false;
    p = strchr(p + strlen(key), ':');
    if (!p || (limit && p >= limit)) return false;
    p++;
    while (*p == ' ' || *p == '\t') p++;
    if (*p != '"') return false;
    p++;
    size_t i = 0;
    while (*p && *p != '"' && i + 1 < out_size && (!limit || p < limit))
        out[i++] = *p++;
    out[i] = '\0';
    return i > 0;
}

static uint32_t json_field_uint(const char *start, const char *limit,
                                const char *key) {
    const char *p = strstr(start, key);
    if (!p || (limit && p >= limit)) return 0;
    p = strchr(p + strlen(key), ':');
    if (!p) return 0;
    p++;
    while (*p == ' ' || *p == '\t') p++;
    return (uint32_t)strtoul(p, NULL, 10);
}

static void fetch_server_system(const SyncState *state, const char *system_code,
                                bool is_ps1, char *scratch, uint32_t scratch_size,
                                ServerSaveList *out) {
    char tpath[80];
    snprintf(tpath, sizeof(tpath), "/api/v1/titles?console_type=%s", system_code);

    HttpRequest req;
    memset(&req, 0, sizeof(req));
    req.server_url = state->server_url;
    req.api_key    = state->api_key;
    req.path       = tpath;
    req.method     = "GET";

    int status = 0;
    int n = http_get_buf(&req, (uint8_t *)scratch, scratch_size, &status);
    if (n < 0 || status != 200) {
        snprintf(out->last_error, sizeof(out->last_error),
                 "%s titles fetch failed (rc=%d http=%d)", system_code, n, status);
        return;
    }
    scratch[(uint32_t)n < scratch_size ? (uint32_t)n : scratch_size - 1] = '\0';

    const char *p = scratch;
    while ((p = strstr(p, "\"title_id\"")) != NULL && out->count < SAVES_MAX_SERVER) {
        const char *next = strstr(p + 1, "\"title_id\"");

        ServerSave *s = &out->items[out->count];
        memset(s, 0, sizeof(*s));
        if (!json_field_str(p, next, "\"title_id\"", s->serial, sizeof(s->serial))) {
            if (!next) break;
            p = next;
            continue;
        }
        if (!json_field_str(p, next, "\"game_name\"", s->name, sizeof(s->name)) &&
            !json_field_str(p, next, "\"name\"", s->name, sizeof(s->name))) {
            strncpy(s->name, s->serial, sizeof(s->name) - 1);
        }
        s->timestamp = json_field_uint(p, next, "\"client_timestamp\"");
        s->is_ps1 = is_ps1;
        out->count++;

        if (!next) break;
        p = next;
    }
}

void saves_fetch_server(const SyncState *state,
                        char *scratch, uint32_t scratch_size,
                        ServerSaveList *out) {
    out->count = 0;
    out->last_error[0] = '\0';
    fetch_server_system(state, "PS2", false, scratch, scratch_size, out);
    fetch_server_system(state, "PS1", true, scratch, scratch_size, out);
    if (out->count == 0 && !out->last_error[0]) {
        snprintf(out->last_error, sizeof(out->last_error),
                 "No PS1/PS2 saves on server");
    }
}

int saves_download_server_to_vmc(const SyncState *state, const ServerSave *save,
                                 char *msg, size_t msg_size) {
    char vmcdir[SAVE_DIR_LEN];
    snprintf(vmcdir, sizeof(vmcdir), "%s/VMC", roms_storage_root());
    roms_mkdir_p(vmcdir);

    const char *endpoint = save->is_ps1 ? "ps1-card" : "ps2-card";
    const char *ext = save->is_ps1 ? "mcd" : "mc2";

    char url[160];
    snprintf(url, sizeof(url), "/api/v1/saves/%s/%s", save->serial, endpoint);
    char out_path[SAVE_DIR_LEN];
    snprintf(out_path, sizeof(out_path), "%s/%s.%s", vmcdir, save->serial, ext);

    FILE *fp = fopen(out_path, "wb");
    if (!fp) {
        snprintf(msg, msg_size, "Cannot write %s", out_path);
        return -1;
    }
    static char vbuf[262144];
    setvbuf(fp, vbuf, _IOFBF, sizeof(vbuf));

    HttpRequest req;
    memset(&req, 0, sizeof(req));
    req.server_url = state->server_url;
    req.api_key    = state->api_key;
    req.path       = url;
    req.method     = "GET";

    HttpResponseInfo info;
    int rc = http_get_stream(&req, fp, NULL, &info);
    fclose(fp);

    if (rc == 0 && info.status >= 200 && info.status < 300) {
        snprintf(msg, msg_size, "Downloaded %s to VMC/", save->serial);
        return 0;
    }
    remove(out_path);
    snprintf(msg, msg_size, "Download failed (rc=%d http=%d)", rc, info.status);
    return -1;
}

/* ---- MemCard Pro 2 / SD2PSX GameID channel switching (MMCE protocol) ---- */

/* Convert a compact serial (SLUS20312) to the OPL/disc GameID form
 * (SLUS_203.12) that MMCE devices match VMC folders against.  Falls back to
 * copying the serial verbatim when it isn't the canonical 4-letter + 5-digit
 * shape. */
static void format_gameid(const char *serial, char *out, size_t out_size) {
    size_t len = strlen(serial);
    bool canonical = (len == 9);
    for (int i = 0; canonical && i < 4; i++)
        if (!isalpha((unsigned char)serial[i])) canonical = false;
    for (int i = 4; canonical && i < 9; i++)
        if (!isdigit((unsigned char)serial[i])) canonical = false;

    if (canonical) {
        snprintf(out, out_size, "%c%c%c%c_%c%c%c.%c%c",
                 serial[0], serial[1], serial[2], serial[3],
                 serial[4], serial[5], serial[6], serial[7], serial[8]);
    } else {
        strncpy(out, serial, out_size - 1);
        out[out_size - 1] = '\0';
    }
}

/* Detect an MMCE device (MCP2 / SD2PSX) in a slot without stalling SIO2:
 * verify a card is physically present via libmc first (empty-slot MMCE probes
 * are what hang the bus), then issue a cheap MMCE query.
 *   0 = empty slot, 1 = card present but not MMCE (gen1/plain), 2 = MMCE. */
int saves_mcp_detect(int port) {
    port = port == 1 ? 1 : 0;
    bool fmt = false;
    int type = mc_probe(port, &fmt);
    if (type <= 0) return 0;                 /* no card / probe failed */

    char dev[8];
    snprintf(dev, sizeof(dev), "mmce%d:", port);
    if (fileXioDevctl(dev, MMCE_CMD_GET_CARD, NULL, 0, NULL, 0) >= 0)
        return 2;                            /* MMCE device responded */
    return 1;                                /* present, but not MMCE */
}

/* mode: SAVES_MCP_AUTO routes by card TYPE from mcGetInfo (PS1-type -> gen1
 * 0x21, PS2-type -> MMCE 0x8B) — never tries both, since 0x8B breaks a gen1 and
 * 0x21 breaks an MCP2; SAVES_MCP_GEN1 0x21 only; SAVES_MCP_GEN2 0x8B only.  All
 * paths present-check first so an empty slot is never driven. */
int saves_mcp_set_gameid(const char *serial, int port, int mode,
                         char *msg, size_t msg_size) {
    port = port == 1 ? 1 : 0;

    char gid[32];
    format_gameid(serial, gid, sizeof(gid));
    int len = (int)strlen(gid) + 1;        /* include the NUL terminator */

    char dev[8];
    snprintf(dev, sizeof(dev), "mmce%d:", port);

    if (mode == SAVES_MCP_AUTO) {
        /* Route by the mounted card TYPE (libmc mcGetInfo, no SIO command, so
         * it can't corrupt the device).  0x8B breaks a gen1 and 0x21 breaks an
         * MCP2, so we must NOT try both.  A gen1 runs in PS1 mode (PS1-type
         * card) and an MCP2 in PS2 mode (PS2-type card), so:
         *   PS1-type -> gen1 0x21 only;  PS2-type -> MMCE 0x8B only. */
        bool fmt = false;
        int type = mc_probe(port, &fmt);
        if (type <= 0) {
            snprintf(msg, msg_size, "No card in slot %d", port + 1);
            return -1;
        }
        if (type == MC_TYPE_PSX) {
            if (fileXioDevctl(dev, MMCE_CMD_SET_GAMEID_LEGACY, gid, len, NULL, 0) >= 0) {
                snprintf(msg, msg_size, "gen1 MemCard Pro switched to %s", gid);
                return 0;
            }
            snprintf(msg, msg_size, "No gen1 response in slot %d (PS1 card)", port + 1);
            return -1;
        }
        if (fileXioDevctl(dev, MMCE_CMD_SET_GAMEID, gid, len, NULL, 0) >= 0) {
            snprintf(msg, msg_size, "MCP2/SD2PSX switched to %s", gid);
            return 0;
        }
        snprintf(msg, msg_size, "No MMCE device in slot %d (PS2 card)", port + 1);
        return -1;
    }

    /* Forced modes: present-check (libmc, safe) then the one command. */
    bool fmt = false;
    if (mc_probe(port, &fmt) <= 0) {
        snprintf(msg, msg_size, "No card in slot %d", port + 1);
        return -1;
    }
    if (mode == SAVES_MCP_GEN1) {
        if (fileXioDevctl(dev, MMCE_CMD_SET_GAMEID_LEGACY, gid, len, NULL, 0) >= 0) {
            snprintf(msg, msg_size, "gen1 MemCard Pro switched to %s", gid);
            return 0;
        }
        snprintf(msg, msg_size, "No gen1 response in slot %d", port + 1);
        return -1;
    }
    /* SAVES_MCP_GEN2 */
    if (fileXioDevctl(dev, MMCE_CMD_SET_GAMEID, gid, len, NULL, 0) >= 0) {
        snprintf(msg, msg_size, "MCP2/SD2PSX switched to %s", gid);
        return 0;
    }
    snprintf(msg, msg_size, "No MMCE device in slot %d", port + 1);
    return -1;
}
