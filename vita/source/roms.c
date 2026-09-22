/*
 * roms.c — Vita ROM catalog client.
 *
 * Ported from the PSP client's roms.c: the same depth-counted
 * ``find_key`` JSON walker and per-object ``object_bounds``, with the
 * routing rewritten for Adrenaline's PSP tree on the Vita.  File I/O
 * goes through sceIo* like the rest of this client.
 */

#include "roms.h"
#include "network.h"

#include <ctype.h>
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>

#include <psp2/io/stat.h>

/* --- Tiny string helpers --- */

static const char *skip_ws(const char *p) {
    while (*p && (*p == ' ' || *p == '\t' || *p == '\n' || *p == '\r')) p++;
    return p;
}

static const char *find_key(const char *p, const char *end, const char *key) {
    char needle[64];
    int  n = snprintf(needle, sizeof(needle), "\"%s\"", key);
    if (n <= 0) return NULL;

    int depth = 0;
    while (p < end) {
        if (*p == '{' || *p == '[') {
            depth++;
            p++;
            continue;
        }
        if (*p == '}' || *p == ']') {
            if (depth == 0) return NULL;
            depth--;
            p++;
            continue;
        }
        if (*p == '"') {
            /* Match key only at top level. Otherwise skip the string body
             * (including any nested {/}/[/]) so depth tracking stays
             * correct across game names that contain bracket chars. */
            if (depth == 0 && (p + n) <= end &&
                strncmp(p, needle, (size_t)n) == 0)
            {
                const char *q = p + n;
                q = skip_ws(q);
                if (q < end && *q == ':') return skip_ws(q + 1);
            }
            p++;
            while (p < end && *p != '"') {
                if (*p == '\\' && p + 1 < end) p++;
                p++;
            }
            if (p < end) p++;  /* past closing quote */
            continue;
        }
        p++;
    }
    return NULL;
}

static bool extract_str(const char *p, const char *end,
                        char *out, size_t out_size) {
    if (!p || p >= end || *p != '"') return false;
    p++;
    size_t len = 0;
    while (p < end && *p != '"' && len + 1 < out_size) {
        if (*p == '\\' && p + 1 < end) {
            char c = p[1];
            if (c == '"' || c == '\\' || c == '/') {
                out[len++] = c; p += 2; continue;
            }
            if (c == 'n') { out[len++] = '\n'; p += 2; continue; }
            if (c == 'r') { out[len++] = '\r'; p += 2; continue; }
            if (c == 't') { out[len++] = '\t'; p += 2; continue; }
            out[len++] = *p; p++;
            continue;
        }
        out[len++] = *p++;
    }
    out[len] = '\0';
    return true;
}

static bool extract_u64(const char *p, const char *end, uint64_t *out) {
    if (!p || p >= end) return false;
    p = skip_ws(p);
    if (p >= end) return false;
    char *endp = NULL;
    errno = 0;
    unsigned long long v = strtoull(p, &endp, 10);
    if (errno != 0 || endp == p) return false;
    *out = (uint64_t)v;
    return true;
}

static bool object_bounds(const char *p, const char *end, const char **end_out) {
    if (p >= end || *p != '{') return false;
    int depth = 0;
    while (p < end) {
        if (*p == '{') depth++;
        else if (*p == '}') {
            depth--;
            if (depth == 0) { *end_out = p; return true; }
        } else if (*p == '"') {
            p++;
            while (p < end && *p != '"') {
                if (*p == '\\' && p + 1 < end) p++;
                p++;
            }
        }
        p++;
    }
    return false;
}

/* --- Catalog parse + paged fetch --- */

static bool parse_catalog_page(const char *scratch_buf, int n,
                               RomCatalog *catalog,
                               bool *has_more_out) {
    if (has_more_out) *has_more_out = false;

    const char *body_end = scratch_buf + n;
    const char *body = skip_ws(scratch_buf);
    if (body < body_end && *body == '{') body++;

    const char *more_v = find_key(body, body_end, "has_more");
    if (more_v && has_more_out) {
        const char *q = skip_ws(more_v);
        *has_more_out = (q < body_end && *q == 't');
    }

    const char *roms_v = find_key(body, body_end, "roms");
    if (!roms_v || *roms_v != '[') {
        snprintf(catalog->last_error, sizeof(catalog->last_error),
                 "Catalog response missing 'roms' array");
        return false;
    }

    const char *p = roms_v + 1;
    while (p < body_end && catalog->count < ROM_CATALOG_MAX) {
        p = skip_ws(p);
        if (p >= body_end) break;
        if (*p == ']') break;
        if (*p == ',') { p++; continue; }
        if (*p != '{') break;

        const char *obj_end = NULL;
        if (!object_bounds(p, body_end, &obj_end)) break;

        RomEntry *e = &catalog->items[catalog->count];
        memset(e, 0, sizeof(*e));

        const char *v;
        v = find_key(p + 1, obj_end, "rom_id");
        if (v) extract_str(v, obj_end, e->rom_id, sizeof(e->rom_id));
        v = find_key(p + 1, obj_end, "filename");
        if (v) extract_str(v, obj_end, e->filename, sizeof(e->filename));
        v = find_key(p + 1, obj_end, "name");
        if (v) extract_str(v, obj_end, e->name, sizeof(e->name));
        v = find_key(p + 1, obj_end, "system");
        if (v) extract_str(v, obj_end, e->system, sizeof(e->system));
        v = find_key(p + 1, obj_end, "title_id");
        if (v) extract_str(v, obj_end, e->title_id, sizeof(e->title_id));
        v = find_key(p + 1, obj_end, "size");
        if (v) extract_u64(v, obj_end, &e->size);
        v = find_key(p + 1, obj_end, "extract_format");
        if (v) extract_str(v, obj_end, e->extract_format,
                           sizeof(e->extract_format));
        v = find_key(p + 1, obj_end, "is_bundle");
        if (v) {
            const char *q = skip_ws(v);
            e->is_bundle = (q < obj_end && *q == 't');
        }
        v = find_key(p + 1, obj_end, "file_count");
        if (v) {
            uint64_t fc = 0;
            if (extract_u64(v, obj_end, &fc) && fc < 4096)
                e->file_count = (int)fc;
        }
        v = find_key(p + 1, obj_end, "disc_index");
        if (v) {
            uint64_t idx = 0;
            if (extract_u64(v, obj_end, &idx) && idx < 16)
                e->disc_index = (int)idx;
        }
        v = find_key(p + 1, obj_end, "disc_total");
        if (v) {
            uint64_t tot = 0;
            if (extract_u64(v, obj_end, &tot) && tot < 16)
                e->disc_total = (int)tot;
        }

        if (e->rom_id[0] && e->filename[0]) {
            /* Multi-disc PS1 games: keep disc 1 only.  Downloading it
             * yields a single multi-disc EBOOT.PBP; a disc 2 row would
             * just overwrite the same PSP/GAME/<gameid>/ folder. */
            if (e->disc_total > 1 && e->disc_index > 1) {
                p = obj_end + 1;
                continue;
            }
            if (!e->name[0])
                strncpy(e->name, e->filename, sizeof(e->name) - 1);
            catalog->count++;
        }
        p = obj_end + 1;
    }
    return true;
}

bool roms_fetch_catalog(const SyncState *state,
                        const char *system_code,
                        char *scratch_buf, uint32_t scratch_buf_size,
                        RomCatalog *catalog) {
    if (!state || !catalog || !scratch_buf) return false;
    catalog->count = 0;
    catalog->last_error[0] = '\0';

    const int page_size = 500;
    int offset = 0;
    int pages = 0;

    while (catalog->count < ROM_CATALOG_MAX) {
        int status = 0;
        int n = network_fetch_rom_catalog(
            state,
            (system_code && system_code[0]) ? system_code : "PSP",
            offset, page_size,
            scratch_buf, scratch_buf_size, &status);
        if (n <= 0 || status != 200) {
            if (catalog->count == 0) {
                snprintf(catalog->last_error, sizeof(catalog->last_error),
                         "Catalog fetch failed (HTTP %d, n=%d)", status, n);
                return false;
            }
            break;
        }

        bool has_more = false;
        int before = catalog->count;
        if (!parse_catalog_page(scratch_buf, n, catalog, &has_more))
            return false;
        int parsed = catalog->count - before;
        pages++;
        if (!has_more || parsed == 0) break;
        offset += page_size;
        if (pages >= (ROM_CATALOG_MAX / page_size) + 2) break;
    }
    return true;
}

/* --- Routing helpers --- */

static bool is_ps1(const RomEntry *rom) {
    return strcasecmp(rom->system, "PS1") == 0 ||
           strcasecmp(rom->system, "PSX") == 0;
}

static const char *file_extension(const char *filename) {
    const char *dot = strrchr(filename, '.');
    return dot ? dot : "";
}

const char *roms_preferred_extract_format(const RomEntry *rom) {
    if (!rom) return "";
    /* PS1: the server advertises "cue" as its default (that's what the
     * PS3 client wants) but POPS needs an EBOOT.PBP, so always ask for
     * that regardless of the hint. */
    if (is_ps1(rom)) return rom->is_bundle ? "" : "eboot";
    /* PSP: only CHDs need converting; pick CSO over ISO to save space
     * on the memory card.  ISO/CSO/PBP sources download raw. */
    if (strcasecmp(rom->system, "PSP") == 0) {
        if (strcasecmp(file_extension(rom->filename), ".chd") == 0)
            return "cso";
        return "";
    }
    return rom->extract_format;
}

bool roms_entry_unsupported(const RomEntry *rom) {
    if (!rom) return true;
    /* A PS1 folder bundle (loose .cue + .bin) has no EBOOT route on
     * the server — it would come down as a ZIP named EBOOT.PBP. */
    return is_ps1(rom) && rom->is_bundle;
}

static char g_pspemu_root[64] = PSPEMU_ROOT_DEFAULT;

void roms_set_pspemu_root(const char *root) {
    if (!root || !root[0]) {
        snprintf(g_pspemu_root, sizeof(g_pspemu_root), "%s", PSPEMU_ROOT_DEFAULT);
        return;
    }
    snprintf(g_pspemu_root, sizeof(g_pspemu_root), "%s", root);
    size_t len = strlen(g_pspemu_root);
    while (len > 0 && g_pspemu_root[len - 1] == '/')
        g_pspemu_root[--len] = '\0';
}

/* Server's conversion cache names outputs ``<stem>_<16-hex>.<ext>``;
 * if that dir leaks into the catalog the suffix rides along.  Strip it
 * so the file lands under a human name. */
static void strip_cache_hash_suffix(char *stem) {
    if (!stem) return;
    size_t len = strlen(stem);
    if (len < 17 || stem[len - 17] != '_') return;
    for (size_t i = len - 16; i < len; i++) {
        char c = stem[i];
        bool is_hex = (c >= '0' && c <= '9') ||
                      (c >= 'a' && c <= 'f') ||
                      (c >= 'A' && c <= 'F');
        if (!is_hex) return;
    }
    stem[len - 17] = '\0';
}

/* Strip filesystem-unsafe chars + collapse whitespace. */
static void sanitize_name(const char *in, char *out, size_t out_size) {
    if (!out || out_size == 0) return;
    if (!in || !*in) { snprintf(out, out_size, "game"); return; }
    size_t j = 0;
    bool last_space = false;
    for (size_t i = 0; in[i] && j + 1 < out_size; i++) {
        unsigned char c = (unsigned char)in[i];
        if (c < 0x20) continue;
        if (c == '<' || c == '>' || c == ':' || c == '"' ||
            c == '/' || c == '\\' || c == '|' || c == '?' || c == '*')
            continue;
        if (c == ' ' || c == '\t') {
            if (last_space || j == 0) continue;
            out[j++] = ' '; last_space = true; continue;
        }
        out[j++] = (char)c;
        last_space = false;
    }
    while (j > 0 && (out[j - 1] == ' ' || out[j - 1] == '.')) j--;
    out[j] = '\0';
    if (j == 0) snprintf(out, out_size, "game");
}

/* Pull a PS1 disc serial out of a filename (``Crash [SCUS-94900].chd``
 * → SCUS94900).  Emitted without the hyphen to match the save folder
 * the emulator creates and the server's title_id form. */
static bool extract_ps1_serial(const char *filename, char *out, size_t out_size) {
    if (!filename || !out || out_size < 10) return false;
    static const char *prefixes[] = {
        "SLUS", "SCUS", "SLES", "SCES", "SLPS", "SLPM",
        "SCPS", "SCPM", "SLPN", "SCAJ", "SLAJ",
        "PAPX", "PBPX", "SLED", "SCED",
        NULL
    };
    size_t flen = strlen(filename);
    for (size_t i = 0; i + 4 < flen; i++) {
        for (int p = 0; prefixes[p]; p++) {
            if (strncasecmp(filename + i, prefixes[p], 4) != 0) continue;
            const char *q = filename + i + 4;
            if (*q == '-' || *q == '_' || *q == ' ' || *q == '.') q++;
            int digits = 0;
            char digit_buf[8];
            while (*q && digits < 5) {
                if (*q >= '0' && *q <= '9') {
                    digit_buf[digits++] = *q;
                    q++;
                } else if (*q == '.' || *q == '-' || *q == '_' || *q == ' ') {
                    q++;
                } else {
                    break;
                }
            }
            if (digits == 5) {
                snprintf(out, out_size, "%c%c%c%c%c%c%c%c%c",
                         (char)toupper((unsigned char)prefixes[p][0]),
                         (char)toupper((unsigned char)prefixes[p][1]),
                         (char)toupper((unsigned char)prefixes[p][2]),
                         (char)toupper((unsigned char)prefixes[p][3]),
                         digit_buf[0], digit_buf[1], digit_buf[2],
                         digit_buf[3], digit_buf[4]);
                return true;
            }
        }
    }
    return false;
}

/* Copy ``filename`` minus its extension (and any cache-hash suffix). */
static void filename_stem(const char *filename, char *stem, size_t stem_size) {
    const char *dot = strrchr(filename, '.');
    size_t stem_len = dot ? (size_t)(dot - filename) : strlen(filename);
    if (stem_len >= stem_size) stem_len = stem_size - 1;
    memcpy(stem, filename, stem_len);
    stem[stem_len] = '\0';
    strip_cache_hash_suffix(stem);
}

bool roms_resolve_target_path(const RomEntry *rom,
                              char *out_path, size_t out_size) {
    if (!rom || !out_path || out_size < 32) return false;

    /* PS1 → <root>/PSP/GAME/<gameid>/EBOOT.PBP.  The XMB/Adrenaline
     * bubble title comes from the EBOOT's own PARAM.SFO, so the folder
     * name only has to be unique and stable. */
    if (is_ps1(rom)) {
        char gameid[GAME_ID_LEN];
        if (rom->title_id[0] && strlen(rom->title_id) < sizeof(gameid)) {
            snprintf(gameid, sizeof(gameid), "%s", rom->title_id);
        } else if (!extract_ps1_serial(rom->name, gameid, sizeof(gameid)) &&
                   !extract_ps1_serial(rom->filename, gameid, sizeof(gameid)))
        {
            char tmp[160];
            sanitize_name(rom->name[0] ? rom->name : rom->filename,
                          tmp, sizeof(tmp));
            if (strlen(tmp) > 12) tmp[12] = '\0';
            snprintf(gameid, sizeof(gameid), "%s", tmp[0] ? tmp : "PSXGAME");
        }
        int n = snprintf(out_path, out_size, "%s/PSP/GAME/%s/EBOOT.PBP",
                         g_pspemu_root, gameid);
        return n > 0 && (size_t)n < out_size;
    }

    /* PSP → <root>/ISO/<name>.  A converted CHD lands as .cso so
     * Adrenaline's ISO menu recognises it. */
    if (strcasecmp(rom->system, "PSP") == 0) {
        char stem[160], rebuilt[176];
        filename_stem(rom->filename, stem, sizeof(stem));
        const char *ext = file_extension(rom->filename);
        if (strcasecmp(roms_preferred_extract_format(rom), "cso") == 0)
            snprintf(rebuilt, sizeof(rebuilt), "%s.cso", stem);
        else
            snprintf(rebuilt, sizeof(rebuilt), "%s%s", stem, ext);
        int n = snprintf(out_path, out_size, "%s/ISO/%s", g_pspemu_root, rebuilt);
        return n > 0 && (size_t)n < out_size;
    }

    /* Unknown system: keep it out of the emulator's folders. */
    int n = snprintf(out_path, out_size, "%s/%s",
                     ROM_TARGET_FALLBACK_DIR, rom->filename);
    return n > 0 && (size_t)n < out_size;
}

/* --- Filesystem prep --- */

void roms_mkdir_p(const char *path) {
    if (!path || !*path) return;
    char buf[512];
    snprintf(buf, sizeof(buf), "%s", path);

    /* Skip the device prefix (``ux0:``) — sceIoMkdir on the bare
     * device would fail and we only want the folders under it. */
    char *p = strchr(buf, ':');
    p = p ? p + 1 : buf;
    if (*p == '/') p++;

    while (*p) {
        if (*p == '/') {
            *p = '\0';
            sceIoMkdir(buf, 0777);
            *p = '/';
        }
        p++;
    }
    sceIoMkdir(buf, 0777);
}

void roms_ensure_target_dirs(void) {
    char path[128];
    snprintf(path, sizeof(path), "%s/ISO", g_pspemu_root);
    roms_mkdir_p(path);
    snprintf(path, sizeof(path), "%s/PSP/GAME", g_pspemu_root);
    roms_mkdir_p(path);
    roms_mkdir_p(ROM_TARGET_FALLBACK_DIR);
}
