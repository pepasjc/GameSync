/*
 * roms.c — PSP ROM catalog client.
 *
 * Same JSON walker as the PS3 client (depth-counted ``find_key`` plus
 * per-object ``object_bounds``), with PSP-flavoured routing.
 *
 * File I/O is via stdio because the pspsdk runtime wraps libc onto the
 * sceIo* calls under the hood — keeping the source identical to the
 * PS3 module makes maintenance easier and the cost is zero.
 */

#include "roms.h"
#include "network.h"

#include <ctype.h>
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <sys/stat.h>

#include <pspiofilemgr.h>
#include <pspdebug.h>

/* --- Tiny string helpers (mirror PS3 client) --- */

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
        if (depth == 0 && *p == '"' && (p + n) <= end &&
            strncmp(p, needle, (size_t)n) == 0)
        {
            const char *q = p + n;
            q = skip_ws(q);
            if (q < end && *q == ':') return skip_ws(q + 1);
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
            if (extract_u64(v, obj_end, &fc) &&
                fc < (uint64_t)ROM_BUNDLE_FILE_MAX * 16)
            {
                e->file_count = (int)fc;
            }
        }
        v = find_key(p + 1, obj_end, "disc_index");
        if (v) {
            uint64_t idx = 0;
            if (extract_u64(v, obj_end, &idx) && idx < 16) {
                e->disc_index = (int)idx;
            }
        }
        v = find_key(p + 1, obj_end, "disc_total");
        if (v) {
            uint64_t tot = 0;
            if (extract_u64(v, obj_end, &tot) && tot < 16) {
                e->disc_total = (int)tot;
            }
        }

        if (e->rom_id[0] && e->filename[0]) {
            /* Multi-disc PS1 games: hide all but disc 1.  The server
             * generates a single multi-disc EBOOT.PBP when disc 1 is
             * downloaded, and POPS handles in-game disc swapping —
             * showing disc 2+ in the catalog would only invite
             * duplicate downloads that overwrite each other under
             * ms0:/PSP/GAME/<gameid>/. */
            if (e->disc_total > 1 && e->disc_index > 1) {
                p = obj_end + 1;
                continue;
            }
            if (!e->name[0]) {
                strncpy(e->name, e->filename, sizeof(e->name) - 1);
            }
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
        if (!parse_catalog_page(scratch_buf, n, catalog, &has_more)) {
            return false;
        }
        int parsed = catalog->count - before;
        pages++;
        if (!has_more || parsed == 0) break;
        offset += page_size;
        if (pages >= (ROM_CATALOG_MAX / page_size) + 2) break;
    }
    return true;
}

/* --- Routing helpers --- */

const char *roms_preferred_extract_format(const RomEntry *rom) {
    if (!rom) return "";
    /* PSP CHDs come back with extract_format="psp" but extract_formats
     * advertises both "iso" and "cso".  We prefer cso so the resulting
     * file is smaller on the Memory Stick.  The catalog response only
     * exposes ``extract_format`` (not the full list) in the JSON
     * walker so we infer cso from the system + the legacy hint. */
    if (strcasecmp(rom->system, "PSP") == 0 &&
        strcasecmp(rom->extract_format, "psp") == 0)
    {
        return "cso";
    }
    /* PS1 catalog rows advertise "eboot" via the server's
     * _extract_formats_for_entry — pass that straight through. */
    return rom->extract_format;
}

static const char *file_extension(const char *filename) {
    const char *dot = strrchr(filename, '.');
    return dot ? dot : "";
}

/* Strip filesystem-unsafe chars + collapse whitespace.  Used for the
 * per-game subfolder name when routing PS1 EBOOTs. */
static void sanitize_name(const char *in, char *out, size_t out_size) {
    if (!out || out_size == 0) return;
    if (!in || !*in) { snprintf(out, out_size, "game"); return; }
    size_t j = 0;
    bool last_space = false;
    for (size_t i = 0; in[i] && j + 1 < out_size; i++) {
        unsigned char c = (unsigned char)in[i];
        if (c < 0x20) continue;
        if (c == '<' || c == '>' || c == ':' || c == '"' ||
            c == '/' || c == '\\' || c == '|' || c == '?' || c == '*') {
            continue;
        }
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

/* Pull a PS1 disc serial out of a filename (Crash [SCUS-94900].chd
 * → SCUS-94900).  Returns false when no recognisable serial.  Used to
 * derive the EBOOT subfolder name so the PSP XMB shows a stable id. */
static bool extract_ps1_serial(const char *filename, char *out, size_t out_size) {
    if (!filename || !out || out_size < 11) return false;
    static const char *prefixes[] = {
        "SLUS", "SCUS", "SLES", "SCES", "SLPS", "SLPM",
        "SCPS", "SCPM", "SLPN", "SCAJ", "SLAJ",
        "PAPX", "PBPX", "SLED", "SCED",
        NULL
    };
    /* Walk the filename; for each occurrence of a known prefix, peek
     * ahead for 5 digits (with optional ``-``/``_``/``.`` separator). */
    size_t flen = strlen(filename);
    for (size_t i = 0; i + 4 < flen; i++) {
        for (int p = 0; prefixes[p]; p++) {
            if (strncasecmp(filename + i, prefixes[p], 4) != 0) continue;
            const char *q = filename + i + 4;
            /* skip optional separator */
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
                snprintf(out, out_size, "%c%c%c%c-%c%c%c%c%c",
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

bool roms_resolve_target_path(const RomEntry *rom,
                              char *out_path, size_t out_size) {
    if (!rom || !out_path || out_size < 32) return false;

    /* PS1 → ms0:/PSP/GAME/<id>/EBOOT.PBP, where <id> is a stable
     * folder name derived from the disc serial.  Falls back to a
     * sanitized name when no serial is detectable so the user still
     * gets a per-game subdir. */
    if (strcasecmp(rom->system, "PS1") == 0 ||
        strcasecmp(rom->system, "PSX") == 0)
    {
        char gameid[16];
        if (!extract_ps1_serial(rom->name, gameid, sizeof(gameid)) &&
            !extract_ps1_serial(rom->filename, gameid, sizeof(gameid)))
        {
            /* Fall back to a sanitised slug — XMB displays whatever
             * the EBOOT's PARAM.SFO says inside, the folder name is
             * just for filesystem uniqueness. */
            char tmp[160];
            sanitize_name(rom->name[0] ? rom->name : rom->filename,
                          tmp, sizeof(tmp));
            /* Trim to 12 chars max for XMB-friendliness. */
            size_t tl = strlen(tmp);
            if (tl > 12) tmp[12] = '\0';
            snprintf(gameid, sizeof(gameid), "%s",
                     tmp[0] ? tmp : "PSXGAME");
        }
        int n = snprintf(out_path, out_size,
                         "%s/%s/EBOOT.PBP",
                         ROM_TARGET_PSP_GAME_DIR, gameid);
        return n > 0 && (size_t)n < out_size;
    }

    /* PSP — drop ISO/CSO into ms0:/ISO/.  Use the ``.cso`` extension
     * when the server is converting CHD→CSO so the file lands with a
     * name CFW menus recognise. */
    if (strcasecmp(rom->system, "PSP") == 0) {
        const char *ext = file_extension(rom->filename);
        char rebuilt[160];
        if (strcasecmp(roms_preferred_extract_format(rom), "cso") == 0 &&
            strcasecmp(ext, ".chd") == 0)
        {
            /* Replace .chd with .cso for the on-disk filename — the
             * server's converted output is a CSO regardless of input
             * extension. */
            char stem[160];
            const char *dot = strrchr(rom->filename, '.');
            size_t stem_len = dot ? (size_t)(dot - rom->filename)
                                  : strlen(rom->filename);
            if (stem_len >= sizeof(stem)) stem_len = sizeof(stem) - 1;
            memcpy(stem, rom->filename, stem_len);
            stem[stem_len] = '\0';
            snprintf(rebuilt, sizeof(rebuilt), "%s.cso", stem);
        } else {
            snprintf(rebuilt, sizeof(rebuilt), "%s", rom->filename);
        }
        int n = snprintf(out_path, out_size,
                         "%s/%s", ROM_TARGET_ISO_DIR, rebuilt);
        return n > 0 && (size_t)n < out_size;
    }

    /* Fallback for unknown systems — drop into the pspsync downloads
     * folder rather than picking a wrong dir. */
    int n = snprintf(out_path, out_size,
                     "%s/%s", ROM_TARGET_FALLBACK_DIR, rom->filename);
    return n > 0 && (size_t)n < out_size;
}

bool roms_resolve_bundle_file_target(const char *system,
                                     const char *game_name,
                                     const char *bundle_file_name,
                                     char *out_path, size_t out_size) {
    if (!bundle_file_name || !out_path || out_size < 32) return false;

    const char *slash = strrchr(bundle_file_name, '/');
    const char *base = slash ? slash + 1 : bundle_file_name;
    if (!*base) return false;

    /* PSP catalog has no bundles today; PS1 client-side is single-file
     * EBOOT.  Only PS3-style routing would land here, but on PSP we
     * fall back to ms0:/PSP/GAME/<game>/<file>. */
    char safe[160];
    sanitize_name(game_name, safe, sizeof(safe));
    int n = snprintf(out_path, out_size,
                     "%s/%s/%s",
                     ROM_TARGET_PSP_GAME_DIR, safe, base);
    (void)system;
    return n > 0 && (size_t)n < out_size;
}

/* --- Manifest fetch --- */

bool roms_fetch_bundle_manifest(const SyncState *state,
                                const char *rom_id,
                                char *scratch_buf, uint32_t scratch_buf_size,
                                RomBundleManifest *manifest) {
    if (!state || !rom_id || !scratch_buf || !manifest) return false;
    manifest->count = 0;
    manifest->total_size = 0;
    manifest->last_error[0] = '\0';

    int status = 0;
    int n = network_fetch_rom_manifest(state, rom_id,
                                       scratch_buf, scratch_buf_size, &status);
    if (n <= 0 || status != 200) {
        snprintf(manifest->last_error, sizeof(manifest->last_error),
                 "Manifest fetch failed (HTTP %d, n=%d)", status, n);
        return false;
    }

    const char *body_end = scratch_buf + n;
    const char *body = skip_ws(scratch_buf);
    if (body < body_end && *body == '{') body++;

    const char *v = find_key(body, body_end, "total_size");
    if (v) extract_u64(v, body_end, &manifest->total_size);

    const char *files_v = find_key(body, body_end, "files");
    if (!files_v || *files_v != '[') {
        snprintf(manifest->last_error, sizeof(manifest->last_error),
                 "Manifest response missing 'files'");
        return false;
    }

    const char *p = files_v + 1;
    while (p < body_end && manifest->count < ROM_BUNDLE_FILE_MAX) {
        p = skip_ws(p);
        if (p >= body_end || *p == ']') break;
        if (*p == ',') { p++; continue; }
        if (*p != '{') break;

        const char *obj_end = NULL;
        if (!object_bounds(p, body_end, &obj_end)) break;

        RomBundleFile *f = &manifest->files[manifest->count];
        memset(f, 0, sizeof(*f));
        const char *nv;
        nv = find_key(p + 1, obj_end, "name");
        if (nv) extract_str(nv, obj_end, f->name, sizeof(f->name));
        nv = find_key(p + 1, obj_end, "size");
        if (nv) extract_u64(nv, obj_end, &f->size);
        if (f->name[0]) manifest->count++;
        p = obj_end + 1;
    }
    return true;
}

/* --- Filesystem prep --- */

static void mkdir_one(const char *path) {
    /* Try stdio first; on success / EEXIST done.  On other errors,
     * fall back to sceIoMkdir (raw PSP API). */
    if (mkdir(path, 0777) == 0) return;
    if (errno == EEXIST) return;
    sceIoMkdir(path, 0777);
}

void roms_mkdir_p(const char *path) {
    if (!path || !*path) return;
    char buf[512];
    snprintf(buf, sizeof(buf), "%s", path);

    /* Walk separators; for each, terminate temporarily and mkdir the
     * prefix.  Skip the device prefix (``ms0:``) — sceIoMkdir on the
     * bare prefix would error otherwise. */
    char *p = buf;
    if (strncmp(p, "ms0:", 4) == 0) p += 4;
    if (*p == '/') p++;

    while (*p) {
        if (*p == '/') {
            *p = '\0';
            mkdir_one(buf);
            *p = '/';
        }
        p++;
    }
    /* Final component (no trailing slash). */
    mkdir_one(buf);
}

void roms_ensure_target_dirs(void) {
    roms_mkdir_p(ROM_TARGET_ISO_DIR);
    roms_mkdir_p(ROM_TARGET_PSP_GAME_DIR);
    roms_mkdir_p(ROM_TARGET_FALLBACK_DIR);
}
