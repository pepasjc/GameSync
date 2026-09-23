/*
 * saves.c — GameCube memory-card save sync.
 *
 * Per-game GCI sync via libogc CARD_* (file-level, no client-side FS parse):
 *   - read:  CARD_GetStatusEx (64-byte card_direntry = GCI header) + CARD_Read
 *   - write: CARD_CreateEntry + CARD_Write + CARD_SetStatusEx (restore metadata)
 *
 * Wire: POST/GET /api/v1/saves/GC_<gamecode>/gc-card?format=gci  (body = GCI).
 */

#include "saves.h"
#include "http.h"

#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#include <ctype.h>
#include <errno.h>
#include <dirent.h>
#include <sys/stat.h>
#include <unistd.h>

#include <gccore.h>
#include <ogc/card.h>
#include <ogc/exi.h>

static u8 g_workarea[CARD_WORKAREA_SIZE] __attribute__((aligned(32)));
static u8 g_gci[SAVES_GCI_MAX]           __attribute__((aligned(32)));

void saves_title_id_from_gamecode(const char *gamecode, char *out, size_t out_size) {
    char code[5] = {0};
    for (int i = 0; i < 4 && gamecode[i] && gamecode[i] != ' '; i++)
        code[i] = (char)toupper((unsigned char)gamecode[i]);
    snprintf(out, out_size, "GC_%s", code);
}

/* Printable copy of a 32-byte save comment (line 1 = game title). */
static void copy_comment(const u8 *src, char *out, size_t out_size) {
    size_t j = 0;
    for (size_t i = 0; i < 32 && j + 1 < out_size; i++) {
        unsigned char ch = src[i];
        if (ch == 0) break;
        if (ch >= 0x20 && ch < 0x7F) out[j++] = (char)ch;
    }
    while (j > 0 && out[j - 1] == ' ') j--;
    out[j] = '\0';
}

static const char *card_err_str(int rc) {
    switch (rc) {
        case CARD_ERROR_NOCARD:      return "no card in slot";
        case CARD_ERROR_WRONGDEVICE: return "wrong device";
        case CARD_ERROR_BROKEN:      return "card directory broken";
        case CARD_ERROR_IOERROR:     return "EXI I/O error";
        case CARD_ERROR_NOFILE:      return "file not found";
        case CARD_ERROR_NOENT:       return "no free block";
        case CARD_ERROR_INSSPACE:    return "not enough space";
        case CARD_ERROR_NOPERM:      return "no permission";
        default:                     return "error";
    }
}

/* Scope libogc's global card identity to one save: CARD_Open matches
 * filename AND gamecode/company when set, so duplicate filenames across
 * games resolve to the right entry.  Reset to wildcard when done. */
static void card_identity(const char *gamecode, const char *company) {
    CARD_SetGamecode(gamecode);
    CARD_SetCompany(company);
}

/* CARD_Read / CARD_Write process at most ONE sector per call (libogc clamps
 * the length to the current sector), so multi-block saves must be looped a
 * sector at a time or only the first block is transferred. */
static u32 card_sector_size(int port) {
    u32 ss = 0;
    if (CARD_GetSectorSize(port, &ss) < 0 || ss == 0) ss = 8192;
    return ss;
}

static s32 card_read_all(card_file *f, u8 *buf, u32 len, u32 ss) {
    for (u32 off = 0; off < len; off += ss) {
        s32 rc = CARD_Read(f, buf + off, ss, off);
        if (rc < 0) return rc;
    }
    return 0;
}

static s32 card_write_all(card_file *f, const u8 *buf, u32 len, u32 ss) {
    for (u32 off = 0; off < len; off += ss) {
        s32 rc = CARD_Write(f, (void *)(buf + off), ss, off);
        if (rc < 0) return rc;
    }
    return 0;
}

/* Mount with retries: transient EXI contention (BBA shares channel 0 with
 * slot A) and slow adapters (MemCard Pro GC) intermittently fail the first
 * attempt with CARD_ERROR_IOERROR.  Genuine no-card errors return at once. */
static int card_mount_retry(int port) {
    int rc = CARD_ERROR_NOCARD;
    for (int i = 0; i < 4; i++) {
        rc = CARD_Mount(port, g_workarea, NULL);
        if (rc >= 0 || rc == CARD_ERROR_NOCARD) break;
        usleep(80 * 1000);
    }
    return rc;
}

/* ---- scan ---- */

void saves_scan_card(int port, GcSaveList *out) {
    if (!out) return;
    out->count = 0;
    out->port = port;
    out->last_error[0] = '\0';

    int rc = card_mount_retry(port);
    if (rc < 0) {
        snprintf(out->last_error, sizeof(out->last_error),
                 "Slot %c: %s (%d)", 'A' + port, card_err_str(rc), rc);
        return;
    }

    card_dir dirs[CARD_MAXFILES];
    s32 count = 0;
    rc = CARD_GetDirectory(port, dirs, &count, true);
    if (rc < 0) {
        CARD_Unmount(port);
        snprintf(out->last_error, sizeof(out->last_error),
                 "Slot %c: dir read failed (%d)", 'A' + port, rc);
        return;
    }

    for (int i = 0; i < count && out->count < SAVES_MAX_CARD; i++) {
        card_dir *d = &dirs[i];
        GcSave *s = &out->items[out->count];
        memset(s, 0, sizeof(*s));

        memcpy(s->gamecode, d->gamecode, 4); s->gamecode[4] = '\0';
        memcpy(s->company,  d->company,  2); s->company[2]  = '\0';
        memcpy(s->filename, d->filename, CARD_FILENAMELEN);
        s->filename[CARD_FILENAMELEN] = '\0';
        s->fileno = (int)d->fileno;
        s->size   = d->filelen;
        s->blocks = (int)(d->filelen / GC_BLOCK_SIZE);
        saves_title_id_from_gamecode(s->gamecode, s->title_id, sizeof(s->title_id));
        out->count++;
    }

    /* Fill display names from each save's comment field (the game title a
     * real memory-card manager shows).  CARD_Read is sector-granular, so
     * read the sector containing comment_addr and extract 32 bytes. */
    u32 ssize = 0;
    if (CARD_GetSectorSize(port, &ssize) < 0 || ssize == 0 || ssize > SAVES_GCI_MAX)
        ssize = 8192;
    for (int i = 0; i < out->count; i++) {
        GcSave *s = &out->items[i];
        card_file f;
        card_identity(s->gamecode, s->company);
        if (CARD_Open(port, s->filename, &f) < 0) { card_identity(NULL, NULL); continue; }
        card_direntry de;
        if (CARD_GetStatusEx(port, f.filenum, &de) >= 0 &&
            de.comment_addr != 0xFFFFFFFF) {
            u32 sec = (de.comment_addr / ssize) * ssize;
            u32 off = de.comment_addr - sec;
            if (off + 32 <= ssize && sec + ssize <= (u32)f.len &&
                CARD_Read(&f, g_gci, ssize, sec) >= 0)
                copy_comment(g_gci + off, s->name, sizeof(s->name));
        }
        CARD_Close(&f);
        card_identity(NULL, NULL);
    }
    CARD_Unmount(port);

    if (out->count == 0)
        snprintf(out->last_error, sizeof(out->last_error),
                 "Slot %c: no saves on card", 'A' + port);
}

/* ---- upload ---- */

static int post_gci(const SyncState *state, const char *title_id,
                    const u8 *body, u32 len, char *msg, size_t msg_size) {
    char path[96];
    snprintf(path, sizeof(path), "/api/v1/saves/%s/gc-card?format=gci", title_id);

    HttpRequest req = {0};
    req.server_url        = state->server_url;
    req.api_key           = state->api_key;
    req.path              = path;
    req.method            = "POST";
    req.body              = body;
    req.body_len          = len;
    req.body_content_type = "application/octet-stream";

    static u8 resp[512];
    int status = 0;
    int n = http_get_buf(&req, resp, sizeof(resp), &status);
    if (n >= 0 && status == 200) {
        snprintf(msg, msg_size, "Uploaded %s (%u KB)", title_id, (unsigned)(len / 1024));
        return 0;
    }
    snprintf(msg, msg_size, "Upload %s failed (HTTP %d, n=%d)", title_id, status, n);
    return -1;
}

int saves_upload_card_game(const SyncState *state, int port, const GcSave *save,
                           char *msg, size_t msg_size) {
    if (!state || !save) return -1;

    int rc = card_mount_retry(port);
    if (rc < 0) { snprintf(msg, msg_size, "Mount slot %c failed (%d)", 'A' + port, rc); return -1; }

    card_file file;
    card_identity(save->gamecode, save->company);
    rc = CARD_Open(port, save->filename, &file);
    card_identity(NULL, NULL);
    if (rc < 0) { CARD_Unmount(port); snprintf(msg, msg_size, "Open %s failed (%d)", save->filename, rc); return -1; }

    /* 64-byte directory entry (GCI header) at the front of the buffer. */
    rc = CARD_GetStatusEx(port, file.filenum, (card_direntry *)g_gci);
    if (rc < 0) { CARD_Close(&file); CARD_Unmount(port); snprintf(msg, msg_size, "Status failed (%d)", rc); return -1; }

    u32 datalen = (u32)file.len;
    if (64 + datalen > SAVES_GCI_MAX) {
        CARD_Close(&file); CARD_Unmount(port);
        snprintf(msg, msg_size, "Save too big (%u KB)", (unsigned)(datalen / 1024));
        return -1;
    }
    rc = card_read_all(&file, g_gci + 64, datalen, card_sector_size(port));
    CARD_Close(&file);
    CARD_Unmount(port);
    if (rc < 0) { snprintf(msg, msg_size, "Read failed (%d)", rc); return -1; }

    return post_gci(state, save->title_id, g_gci, 64 + datalen, msg, msg_size);
}

/* ---- restore (download GCI -> card) ---- */

int saves_restore_card_game(const SyncState *state, int port, const char *title_id,
                            char *msg, size_t msg_size) {
    if (!state || !title_id || !title_id[0]) { snprintf(msg, msg_size, "No title id"); return -1; }

    char path[96];
    snprintf(path, sizeof(path), "/api/v1/saves/%s/gc-card?format=gci", title_id);

    HttpRequest req = {0};
    req.server_url = state->server_url;
    req.api_key    = state->api_key;
    req.path       = path;
    req.method     = "GET";

    int status = 0;
    int n = http_get_buf(&req, g_gci, SAVES_GCI_MAX, &status);
    if (n < 0 || status != 200) {
        snprintf(msg, msg_size, "Download %s failed (HTTP %d, n=%d)", title_id, status, n);
        return -1;
    }
    if (n < 64 + GC_BLOCK_SIZE) { snprintf(msg, msg_size, "GCI too small (%d B)", n); return -1; }

    card_direntry *de = (card_direntry *)g_gci;
    u32 datalen = (u32)n - 64;

    int rc = card_mount_retry(port);
    if (rc < 0) { snprintf(msg, msg_size, "Mount slot %c failed (%d)", 'A' + port, rc); return -1; }

    char fname[CARD_FILENAMELEN + 1];
    memcpy(fname, de->filename, CARD_FILENAMELEN);
    fname[CARD_FILENAMELEN] = '\0';

    /* Scope the card identity to this game so the delete hits the right
     * entry and CARD_Create stamps the correct gamecode. */
    char gcode[5] = {0}, gco[3] = {0};
    memcpy(gcode, de->gamecode, 4);
    memcpy(gco,   de->company,  2);
    card_identity(gcode, gco);

    /* Overwrite: a same-name file would otherwise fail create with EXIST. */
    CARD_Delete(port, fname);

    card_dir dir;
    memset(&dir, 0, sizeof(dir));
    memcpy(dir.gamecode, de->gamecode, 4);
    memcpy(dir.company,  de->company,  2);
    memcpy(dir.filename, de->filename, CARD_FILENAMELEN);
    dir.filelen     = datalen;
    dir.permissions = de->permission;
    dir.showall     = true;

    card_file file;
    rc = CARD_CreateEntry(port, &dir, &file);
    if (rc < 0) {
        card_identity(NULL, NULL);
        CARD_Unmount(port);
        snprintf(msg, msg_size, "Create failed: %s (%d)", card_err_str(rc), rc);
        return -1;
    }

    rc = card_write_all(&file, g_gci + 64, datalen, card_sector_size(port));
    if (rc < 0) {
        card_identity(NULL, NULL);
        CARD_Close(&file); CARD_Unmount(port);
        snprintf(msg, msg_size, "Write failed (%d)", rc);
        return -1;
    }

    /* Restore icon/comment/time metadata (keeps the new block index + length). */
    CARD_SetStatusEx(port, file.filenum, de);
    CARD_Close(&file);
    card_identity(NULL, NULL);
    CARD_Unmount(port);

    snprintf(msg, msg_size, "Restored %s to slot %c (%u KB)", title_id, 'A' + port,
             (unsigned)(datalen / 1024));
    return 0;
}

/* ---- server saves (JSON walk of /titles) ---- */

static const char *skip_ws(const char *p) {
    while (*p && (*p == ' ' || *p == '\t' || *p == '\n' || *p == '\r')) p++;
    return p;
}

static const char *find_key(const char *p, const char *end, const char *key) {
    char needle[64];
    int n = snprintf(needle, sizeof(needle), "\"%s\"", key);
    if (n <= 0) return NULL;
    int depth = 0;
    while (p < end) {
        if (*p == '{' || *p == '[') { depth++; p++; continue; }
        if (*p == '}' || *p == ']') { if (depth == 0) return NULL; depth--; p++; continue; }
        if (*p == '"') {
            if (depth == 0 && (p + n) <= end && strncmp(p, needle, (size_t)n) == 0) {
                const char *q = skip_ws(p + n);
                if (q < end && *q == ':') return skip_ws(q + 1);
            }
            p++;
            while (p < end && *p != '"') { if (*p == '\\' && p + 1 < end) p++; p++; }
            if (p < end) p++;
            continue;
        }
        p++;
    }
    return NULL;
}

static bool extract_str(const char *p, const char *end, char *out, size_t out_size) {
    if (!p || p >= end || *p != '"') return false;
    p++;
    size_t len = 0;
    while (p < end && *p != '"' && len + 1 < out_size) {
        if (*p == '\\' && p + 1 < end) {
            char c = p[1];
            if (c == '"' || c == '\\' || c == '/') { out[len++] = c; p += 2; continue; }
            out[len++] = *p; p++;
            continue;
        }
        out[len++] = *p++;
    }
    out[len] = '\0';
    return true;
}

static bool extract_u32(const char *p, const char *end, uint32_t *out) {
    if (!p || p >= end) return false;
    p = skip_ws(p);
    char *endp = NULL;
    errno = 0;
    unsigned long long v = strtoull(p, &endp, 10);
    if (errno != 0 || endp == p) return false;
    *out = (uint32_t)v;
    return true;
}

static bool object_bounds(const char *p, const char *end, const char **end_out) {
    if (p >= end || *p != '{') return false;
    int depth = 0;
    while (p < end) {
        if (*p == '{') depth++;
        else if (*p == '}') { depth--; if (depth == 0) { *end_out = p; return true; } }
        else if (*p == '"') { p++; while (p < end && *p != '"') { if (*p == '\\' && p + 1 < end) p++; p++; } }
        p++;
    }
    return false;
}

void saves_fetch_server(const SyncState *state,
                        char *scratch, uint32_t scratch_size,
                        ServerSaveList *out) {
    if (!out) return;
    out->count = 0;
    out->last_error[0] = '\0';

    HttpRequest req = {0};
    req.server_url = state->server_url;
    req.api_key    = state->api_key;
    req.path       = "/api/v1/titles?console_type=GC";
    req.method     = "GET";

    int status = 0;
    int n = http_get_buf(&req, (uint8_t *)scratch, scratch_size, &status);
    if (n < 0 || status != 200) {
        snprintf(out->last_error, sizeof(out->last_error),
                 "Server fetch failed (HTTP %d, n=%d)", status, n);
        return;
    }

    const char *body_end = scratch + n;
    /* Skip the root object's opening brace so find_key matches "titles" at
     * depth 0 (otherwise it descends to depth 1 and never matches). */
    const char *body = skip_ws(scratch);
    if (body < body_end && *body == '{') body++;
    const char *titles_v = find_key(body, body_end, "titles");
    if (!titles_v || *titles_v != '[') {
        snprintf(out->last_error, sizeof(out->last_error), "No 'titles' array");
        return;
    }

    const char *p = titles_v + 1;
    while (p < body_end && out->count < SAVES_MAX_SERVER) {
        p = skip_ws(p);
        if (p >= body_end || *p == ']') break;
        if (*p == ',') { p++; continue; }
        if (*p != '{') break;

        const char *obj_end = NULL;
        if (!object_bounds(p, body_end, &obj_end)) break;

        ServerSave *s = &out->items[out->count];
        memset(s, 0, sizeof(*s));

        const char *v;
        v = find_key(p + 1, obj_end, "title_id");
        if (v) extract_str(v, obj_end, s->title_id, sizeof(s->title_id));
        v = find_key(p + 1, obj_end, "game_name");
        if (v) extract_str(v, obj_end, s->name, sizeof(s->name));
        if (!s->name[0]) {
            v = find_key(p + 1, obj_end, "name");
            if (v) extract_str(v, obj_end, s->name, sizeof(s->name));
        }
        v = find_key(p + 1, obj_end, "client_timestamp");
        if (v) extract_u32(v, obj_end, &s->timestamp);

        if (s->title_id[0]) {
            if (!s->name[0]) strncpy(s->name, s->title_id, sizeof(s->name) - 1);
            out->count++;
        }
        p = obj_end + 1;
    }

    if (out->count == 0 && out->last_error[0] == '\0')
        snprintf(out->last_error, sizeof(out->last_error), "No GC saves on server");
}

/* ---- VMC: full card images on SD ---- */

#define VMC_DIR SD_ROOT "/VMC"

static bool vmc_size_ok(uint32_t size) {
    return size >= 0x80000 && size <= 0x1000000 && (size % GC_BLOCK_SIZE) == 0;
}

static bool has_card_ext(const char *fname) {
    size_t n = strlen(fname);
    static const char *exts[] = { ".raw", ".gcp", ".mc", ".bin", ".mcd", NULL };
    for (int i = 0; exts[i]; i++) {
        size_t el = strlen(exts[i]);
        if (n > el && strcasecmp(fname + n - el, exts[i]) == 0) return true;
    }
    return false;
}

static void scan_vmc_dir(const char *dir, SaveVmcList *out) {
    DIR *d = opendir(dir);
    if (!d) return;
    struct dirent *de;
    while ((de = readdir(d)) != NULL && out->count < SAVES_MAX_VMC) {
        if (de->d_name[0] == '.') continue;
        if (!has_card_ext(de->d_name)) continue;
        char path[SAVE_DIR_LEN];
        snprintf(path, sizeof(path), "%s/%s", dir, de->d_name);
        struct stat st;
        if (stat(path, &st) != 0) continue;
        if (!vmc_size_ok((uint32_t)st.st_size)) continue;
        SaveVmc *v = &out->items[out->count];
        memset(v, 0, sizeof(*v));
        strncpy(v->path, path, sizeof(v->path) - 1);
        strncpy(v->filename, de->d_name, sizeof(v->filename) - 1);
        v->size = (uint32_t)st.st_size;
        out->count++;
    }
    closedir(d);
}

void saves_scan_vmc(SaveVmcList *out) {
    if (!out) return;
    out->count = 0;
    out->last_error[0] = '\0';

    /* FlipperMCE / GCMCE virtual cards: one folder per channel,
     * sd:/MemoryCards/GC/<DL-DOL-XXXX-REGION>/<...>-1.raw (8 MB each). */
    DIR *d = opendir(SD_ROOT "/MemoryCards/GC");
    if (d) {
        struct dirent *de;
        while ((de = readdir(d)) != NULL && out->count < SAVES_MAX_VMC) {
            if (de->d_name[0] == '.') continue;
            char sub[SAVE_DIR_LEN];
            snprintf(sub, sizeof(sub), SD_ROOT "/MemoryCards/GC/%s", de->d_name);
            scan_vmc_dir(sub, out);
        }
        closedir(d);
    }

    /* Swiss's memory-card emulation images live in sd:/swiss/saves
     * (MemoryCardA.USA.raw etc., 16 MB). */
    scan_vmc_dir(SD_ROOT "/swiss/saves", out);
    scan_vmc_dir(VMC_DIR, out);
    scan_vmc_dir(SD_ROOT, out);
    if (out->count == 0)
        snprintf(out->last_error, sizeof(out->last_error),
                 "No card images found on SD");
}

int saves_upload_vmc(const SyncState *state, const SaveVmc *vmc, char *msg, size_t msg_size) {
    if (!state || !vmc) return -1;
    FILE *fp = fopen(vmc->path, "rb");
    if (!fp) { snprintf(msg, msg_size, "Open %s failed", vmc->filename); return -1; }

    /* Stream the image from SD — swiss cards are 16 MB, too big to buffer
     * comfortably in the Cube's 24 MB main RAM. */
    HttpRequest req = {0};
    req.server_url        = state->server_url;
    req.api_key           = state->api_key;
    req.path              = "/api/v1/saves/gc-vmc/import";
    req.method            = "POST";
    req.body_fp           = fp;
    req.body_len          = vmc->size;
    req.body_content_type = "application/octet-stream";

    static uint8_t resp[4096];
    int status = 0;
    int n = http_get_buf(&req, resp, sizeof(resp), &status);
    fclose(fp);
    if (n >= 0 && status == 200) {
        int games = 0;
        for (char *p = (char *)resp; (p = strstr(p, "title_id")) != NULL; p++) games++;
        snprintf(msg, msg_size, "Imported %d game(s) from %s", games, vmc->filename);
        return 0;
    }
    snprintf(msg, msg_size, "Import %s failed (HTTP %d, n=%d)", vmc->filename, status, n);
    return -1;
}

int saves_pull_all(const SyncState *state, const ServerSaveList *server,
                   char *msg, size_t msg_size) {
    if (!state || !server) return -1;
    mkdir(VMC_DIR, 0777);

    int ok = 0, fail = 0;
    for (int i = 0; i < server->count; i++) {
        const char *tid = server->items[i].title_id;
        char path[96];
        snprintf(path, sizeof(path), "/api/v1/saves/%s/gc-card?format=gci", tid);

        HttpRequest req = {0};
        req.server_url = state->server_url;
        req.api_key    = state->api_key;
        req.path       = path;
        req.method     = "GET";

        int status = 0;
        int n = http_get_buf(&req, g_gci, SAVES_GCI_MAX, &status);
        if (n < 0 || status != 200) { fail++; continue; }

        char out_path[SAVE_DIR_LEN];
        snprintf(out_path, sizeof(out_path), "%s/%s.gci", VMC_DIR, tid);
        FILE *fp = fopen(out_path, "wb");
        if (!fp) { fail++; continue; }
        size_t wr = fwrite(g_gci, 1, (size_t)n, fp);
        fclose(fp);
        if (wr == (size_t)n) ok++; else fail++;
    }
    snprintf(msg, msg_size, "Pulled %d GCI(s), %d failed", ok, fail);
    return ok;
}

int saves_upload_vmc_save(const SyncState *state, VmcfsCard *card, int idx,
                          char *msg, size_t msg_size) {
    if (!state || !card || idx < 0 || idx >= card->count) return -1;
    int n = vmcfs_read_gci(card, idx, g_gci, SAVES_GCI_MAX);
    if (n < 0) { snprintf(msg, msg_size, "Read save failed (%d)", n); return -1; }
    return post_gci(state, card->saves[idx].title_id, g_gci, (u32)n, msg, msg_size);
}

int saves_restore_vmc_save(const SyncState *state, VmcfsCard *card,
                           const char *title_id, char *msg, size_t msg_size) {
    if (!state || !card || !title_id || !title_id[0]) return -1;

    char path[96];
    snprintf(path, sizeof(path), "/api/v1/saves/%s/gc-card?format=gci", title_id);

    HttpRequest req = {0};
    req.server_url = state->server_url;
    req.api_key    = state->api_key;
    req.path       = path;
    req.method     = "GET";

    int status = 0;
    int n = http_get_buf(&req, g_gci, SAVES_GCI_MAX, &status);
    if (n < 0 || status != 200) {
        snprintf(msg, msg_size, "Download %s failed (HTTP %d, n=%d)", title_id, status, n);
        return -1;
    }
    return vmcfs_write_gci(card, g_gci, (uint32_t)n, msg, msg_size);
}

/* ---- MemCard Pro GC GameID (MMCE over EXI) ----
 *
 * Protocol ported from libogc2 mmce.c (Extrems): each command is a 0x8B-prefixed
 * EXI transaction to the memory-card device.  GameID switch = SetDiskID
 * (0x8B 0x11 + gamecode/company/disknum/gamever) then SetDiskInfo
 * (0x8B 0x13 + 64-byte name).  GC EXI is master-clocked, so sending these to a
 * plain Nintendo card is harmless (it ignores unknown commands) — unlike the
 * PS2's ACK-based SIO2.  Still gated on EXI_Probe to skip empty slots.
 */
int saves_mcp_set_gameid(int port, const char *gamecode, const char *company,
                         const char *name, char *msg, size_t msg_size) {
    static const char digits[16] = "0123456789ABCDEF";
    int chan = (port == 1) ? EXI_CHANNEL_1 : EXI_CHANNEL_0;

    if (EXI_Probe(chan) <= 0) {
        snprintf(msg, msg_size, "No device in slot %c", 'A' + port);
        return -1;
    }

    /* --- Handshake: GetDeviceID (0x8B 0x00, 1 MHz) — mirrors Swiss/libogc2's
     * sequence and doubles as detection: a plain card / floating bus answers
     * all-zeros or all-ones. --- */
    u32 dev_id = 0;
    {
        if (!EXI_Lock(chan, EXI_DEVICE_0, NULL)) {
            snprintf(msg, msg_size, "Slot %c EXI busy", 'A' + port);
            return -2;
        }
        if (!EXI_Select(chan, EXI_DEVICE_0, EXI_SPEED1MHZ)) {
            EXI_Unlock(chan);
            snprintf(msg, msg_size, "Slot %c select failed", 'A' + port);
            return -3;
        }
        u8 hcmd[2] = { 0x8B, 0x00 };
        bool herr = false;
        herr |= !EXI_ImmEx(chan, hcmd, sizeof(hcmd), EXI_WRITE);
        herr |= !EXI_ImmEx(chan, &dev_id, sizeof(dev_id), EXI_READ);
        herr |= !EXI_Deselect(chan);
        EXI_Unlock(chan);
        if (herr) { snprintf(msg, msg_size, "GetDeviceID xfer failed"); return -8; }
        if (dev_id == 0 || dev_id == 0xFFFFFFFF) {
            snprintf(msg, msg_size,
                     "Slot %c: no MMCE reply (id=%08lx) - plain card?",
                     'A' + port, (unsigned long)dev_id);
            return -9;
        }
    }

    /* --- SetDiskID: 0x8B 0x11 + gamename[4] + company[2] + disknum/gamever --- */
    if (!EXI_Lock(chan, EXI_DEVICE_0, NULL)) {
        snprintf(msg, msg_size, "Slot %c EXI busy", 'A' + port);
        return -2;
    }
    if (!EXI_Select(chan, EXI_DEVICE_0, EXI_SPEED16MHZ)) {
        EXI_Unlock(chan);
        snprintf(msg, msg_size, "Slot %c select failed", 'A' + port);
        return -3;
    }
    /* Header and payload as separate ImmEx writes — the same transfer shape
     * as SetDiskInfo/GetGameName, which verifiably parse on FlipperMCE.
     *
     * Always transmit the id uppercase: the device's game-DB lookup is
     * case-sensitive, and a lowercase id (e.g. from a server title stored as
     * "GC_grse") misses -> region "UNK" -> a NEW channel folder
     * (DL-DOL-grse-UNK) instead of the existing DL-DOL-GRSE-USA. */
    u8 cmd[12];
    memset(cmd, 0, sizeof(cmd));
    cmd[0] = 0x8B;
    cmd[1] = 0x11;
    for (int i = 0; i < 4; i++)
        cmd[2 + i] = (u8)toupper((unsigned char)gamecode[i]);
    const char *co = (company && company[0]) ? company : "00";
    cmd[6] = (u8)toupper((unsigned char)co[0]);
    cmd[7] = (u8)toupper((unsigned char)co[1]);
    cmd[8]  = digits[0]; cmd[9]  = digits[0];   /* disknum 00 */
    cmd[10] = digits[0]; cmd[11] = digits[0];   /* gamever 00 */
    bool err = false;
    err |= !EXI_ImmEx(chan, cmd, 2, EXI_WRITE);
    err |= !EXI_ImmEx(chan, &cmd[2], 10, EXI_WRITE);
    err |= !EXI_Deselect(chan);
    EXI_Unlock(chan);
    if (err) { snprintf(msg, msg_size, "SetDiskID failed"); return -4; }

    /* --- SetDiskInfo: 0x8B 0x13 + 64-byte display name --- */
    if (!EXI_Lock(chan, EXI_DEVICE_0, NULL)) { snprintf(msg, msg_size, "Slot %c EXI busy", 'A' + port); return -5; }
    if (!EXI_Select(chan, EXI_DEVICE_0, EXI_SPEED16MHZ)) {
        EXI_Unlock(chan);
        snprintf(msg, msg_size, "Slot %c select2 failed", 'A' + port);
        return -6;
    }
    /* FlipperMCE's SET_GAME_NAME handler consumes bytes only up to the
     * terminating NUL — clocking a full zero-padded 64-byte buffer leaves
     * ~50 stale bytes in its command FIFO and desyncs the device.  Send
     * exactly strlen+1 bytes. */
    u8 cmd2[2] = { 0x8B, 0x13 };
    char info[64];
    memset(info, 0, sizeof(info));
    if (name) strncpy(info, name, sizeof(info) - 1);
    u32 info_len = (u32)strlen(info) + 1;
    err = false;
    err |= !EXI_ImmEx(chan, cmd2, sizeof(cmd2), EXI_WRITE);
    err |= !EXI_ImmEx(chan, info, info_len, EXI_WRITE);
    err |= !EXI_Deselect(chan);
    EXI_Unlock(chan);
    if (err) { snprintf(msg, msg_size, "SetDiskInfo failed"); return -7; }

    /* --- SetGameID (0x8B 0x1D + 10-byte id): libogc2/MemCard Pro opcode.
     * FlipperMCE-family devices (GetDeviceID answers 0x3842...) do NOT
     * implement 0x1D — their channel switch keys on SetDiskID (0x11) — and
     * an unknown command with trailing payload desyncs their FIFO, so skip
     * it for them. --- */
    if ((dev_id >> 16) != 0x3842) {
        if (!EXI_Lock(chan, EXI_DEVICE_0, NULL)) { snprintf(msg, msg_size, "Slot %c EXI busy", 'A' + port); return -10; }
        if (!EXI_Select(chan, EXI_DEVICE_0, EXI_SPEED16MHZ)) {
            EXI_Unlock(chan);
            snprintf(msg, msg_size, "Slot %c select3 failed", 'A' + port);
            return -11;
        }
        u8 cmd3[2] = { 0x8B, 0x1D };
        err = false;
        err |= !EXI_ImmEx(chan, cmd3, sizeof(cmd3), EXI_WRITE);
        err |= !EXI_ImmEx(chan, &cmd[2], 10, EXI_WRITE);   /* id built for SetDiskID */
        err |= !EXI_Deselect(chan);
        EXI_Unlock(chan);
        if (err) { snprintf(msg, msg_size, "SetGameID failed"); return -12; }
    }

    /* --- FlipperMCE-family: read the stored game name back (0x8B 0x12,
     * response = 64 name bytes + 1 terminator — drain all 65 or the device
     * FIFO desyncs).  Confirms on screen whether our writes actually parsed:
     * echo OK but no switch = device-side (settings / game DB). --- */
    if ((dev_id >> 16) == 0x3842) {
        char echo[65];
        memset(echo, 0, sizeof(echo));
        if (EXI_Lock(chan, EXI_DEVICE_0, NULL)) {
            if (EXI_Select(chan, EXI_DEVICE_0, EXI_SPEED16MHZ)) {
                u8 gcmd[3] = { 0x8B, 0x12, 0x00 };
                bool gerr = false;
                gerr |= !EXI_ImmEx(chan, gcmd, sizeof(gcmd), EXI_WRITE);
                gerr |= !EXI_ImmEx(chan, echo, 65, EXI_READ);
                EXI_Deselect(chan);
                EXI_Unlock(chan);
                echo[64] = '\0';
                if (!gerr && strncmp(echo, info, sizeof(echo) - 1) == 0) {
                    snprintf(msg, msg_size, "GameID %.4s OK+verified, slot %c",
                             gamecode, 'A' + port);
                    return 0;
                }
                if (!gerr) {
                    snprintf(msg, msg_size,
                             "GameID sent but echo='%.12s' != '%.12s'",
                             echo, info);
                    return 0;   /* commands may still have landed */
                }
            } else {
                EXI_Unlock(chan);
            }
        }
    }

    snprintf(msg, msg_size, "GameID %.4s%.2s sent, slot %c (dev %08lx)",
             gamecode, (company && company[0]) ? company : "00",
             'A' + port, (unsigned long)dev_id);
    return 0;
}
