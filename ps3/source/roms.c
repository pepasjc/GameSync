/*
 * roms.c — ROM catalog client for the GameSync PS3 build.
 *
 * Lightweight JSON walker over the same catalog endpoint the desktop /
 * steamdeck clients consume.  We parse a curly-brace-balanced subset so we
 * don't pull in a full JSON library (PSL1GHT has none).  The server-side
 * shape is stable and is checked via tests in server/tests/test_roms.py,
 * so the parser matches that exact layout: a top-level object with a
 * "roms" array of flat objects.
 */

#include "roms.h"
#include "debug.h"
#include "network.h"

#include <ctype.h>
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>

#include <sys/file.h>      /* sysFsMkdir, when available via PSL1GHT */
#include <lv2/sysfs.h>

/* --- Tiny string helpers --- */

static const char *skip_ws(const char *p) {
    while (*p && (*p == ' ' || *p == '\t' || *p == '\n' || *p == '\r')) p++;
    return p;
}

/* Find the next "key": from p, but stay inside the current object.  Returns
 * the position right after the colon, or NULL if not found before the
 * matching closing brace. */
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
        /* Handle a couple of common escapes — server side mostly emits
         * raw ASCII names, but a backslash-quote shouldn't truncate us. */
        if (*p == '\\' && p + 1 < end) {
            char c = p[1];
            if (c == '"' || c == '\\' || c == '/') {
                out[len++] = c;
                p += 2;
                continue;
            }
            if (c == 'n') { out[len++] = '\n'; p += 2; continue; }
            if (c == 'r') { out[len++] = '\r'; p += 2; continue; }
            if (c == 't') { out[len++] = '\t'; p += 2; continue; }
            /* Unknown escape — copy backslash, advance one. */
            out[len++] = *p;
            p++;
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

/* Find the bounds of the current JSON object starting at p (which must
 * point at '{').  end_out is set to the matching '}'.  Returns false on
 * unterminated input. */
static bool object_bounds(const char *p, const char *end, const char **end_out) {
    if (p >= end || *p != '{') return false;
    int depth = 0;
    while (p < end) {
        if (*p == '{') depth++;
        else if (*p == '}') {
            depth--;
            if (depth == 0) { *end_out = p; return true; }
        } else if (*p == '"') {
            /* skip string */
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

/* --- Catalog fetch + parse --- */

/* Parse one catalog page already buffered in ``scratch_buf`` and append
 * its entries to ``catalog``.  Sets ``has_more_out`` from the response
 * (false on absent field — i.e. server returned everything in one shot).
 * Returns false only on parse error (response shape unexpected); a page
 * with zero entries is a normal end-of-list and returns true. */
static bool parse_catalog_page(const char *scratch_buf, int n,
                               RomCatalog *catalog,
                               bool *has_more_out) {
    if (has_more_out) *has_more_out = false;

    const char *body_end = scratch_buf + n;
    const char *body = skip_ws(scratch_buf);
    if (body < body_end && *body == '{') body++;

    /* Top-level has_more flag tells us when to stop the page loop. */
    const char *more_v = find_key(body, body_end, "has_more");
    if (more_v && has_more_out) {
        const char *q = skip_ws(more_v);
        *has_more_out = (q < body_end && *q == 't');
    }

    const char *roms_v = find_key(body, body_end, "roms");
    if (!roms_v || *roms_v != '[') {
        snprintf(catalog->last_error, sizeof(catalog->last_error),
                 "Catalog response missing 'roms' array");
        debug_log("roms: %s", catalog->last_error);
        return false;
    }

    const char *p = roms_v + 1;  /* past '[' */
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
        /* Server hint: when set, catalog row needs ``?extract=<fmt>``
         * appended on download.  Used by PS1 .chd entries that should
         * be served as CUE/BIN ZIP rather than raw CHD. */
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
            if (extract_u64(v, obj_end, &fc) && fc < (uint64_t)ROM_BUNDLE_FILE_MAX * 16) {
                e->file_count = (int)fc;
            }
        }

        if (e->rom_id[0] && e->filename[0]) {
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

    /* Walk pages of 500 rows each until the server says ``has_more=false``
     * or we hit ROM_CATALOG_MAX.  Server caps at 20 000 per request but
     * the PS3 client's RAM budget — and the perceived latency of a single
     * giant fetch — make smaller pages a better trade.  500 rows of
     * typical catalog JSON sits well under 1 MB so the same scratch
     * buffer is reused per page. */
    const int page_size = 500;
    int offset = 0;
    int pages = 0;
    while (catalog->count < ROM_CATALOG_MAX) {
        int status = 0;
        int n = network_fetch_rom_catalog(
            state,
            (system_code && system_code[0]) ? system_code : "PS3",
            offset, page_size,
            scratch_buf, scratch_buf_size, &status);
        if (n <= 0 || status != 200) {
            /* Network or server error.  Keep entries we already
             * parsed so the user sees something rather than nothing,
             * and surface the page-specific error. */
            if (catalog->count == 0) {
                snprintf(catalog->last_error, sizeof(catalog->last_error),
                         "Catalog fetch failed (HTTP %d, n=%d)", status, n);
                debug_log("roms: %s", catalog->last_error);
                return false;
            }
            debug_log("roms: page %d at offset %d failed (status=%d n=%d) "
                      "— keeping %d entries already parsed",
                      pages, offset, status, n, catalog->count);
            break;
        }

        int before = catalog->count;
        bool has_more = false;
        if (!parse_catalog_page(scratch_buf, n, catalog, &has_more)) {
            return false;
        }
        int parsed = catalog->count - before;
        pages++;
        debug_log("roms: page %d offset=%d parsed=%d total=%d has_more=%d",
                  pages, offset, parsed, catalog->count, (int)has_more);

        if (!has_more || parsed == 0) break;
        offset += page_size;
        /* Safety net: server should send has_more=false eventually,
         * but cap the page count anyway so a buggy server can't loop
         * us forever. */
        if (pages >= (ROM_CATALOG_MAX / page_size) + 2) break;
    }

    debug_log("roms: parsed %d catalog entries (%s) across %d pages",
              catalog->count,
              system_code && system_code[0] ? system_code : "all", pages);
    return true;
}

/* --- Target path resolution --- */

static const char *file_extension(const char *filename) {
    const char *dot = strrchr(filename, '.');
    return dot ? dot : "";
}

/* Sanitize a game name into a valid PS3 filesystem path component.
 *
 * Strips characters real-PS3 vfat can choke on (`<>:"/\|?*` plus
 * NUL/control); collapses runs of whitespace to single spaces; trims
 * leading/trailing spaces and dots so we never produce a directory name
 * Windows refuses to display when the user mounts the drive elsewhere.
 *
 * Output is written to ``out`` and is guaranteed NUL-terminated.  At
 * least 1 byte of payload is emitted ("game" fallback) when the input
 * sanitizes down to nothing, so callers don't have to handle empty
 * directory names. */
static void sanitize_game_name(const char *in, char *out, size_t out_size) {
    if (!out || out_size == 0) return;
    if (!in || !*in) {
        snprintf(out, out_size, "game");
        return;
    }
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
            out[j++] = ' ';
            last_space = true;
            continue;
        }
        out[j++] = (char)c;
        last_space = false;
    }
    /* Trim trailing whitespace + dots (Windows reads the SD card too). */
    while (j > 0 && (out[j - 1] == ' ' || out[j - 1] == '.')) j--;
    out[j] = '\0';
    if (j == 0) snprintf(out, out_size, "game");
}

bool roms_resolve_target_path(const RomEntry *rom,
                              char *out_path, size_t out_size) {
    if (!rom || !out_path || out_size < 32) return false;

    const char *ext = file_extension(rom->filename);

    /* PS1 single-file ROMs always go inside a per-game subfolder so
     * webMAN's PS1 emulator can find them at /dev_hdd0/PSXISO/<game>/. */
    if (strcasecmp(rom->system, "PS1") == 0) {
        char safe_name[160];
        sanitize_game_name(rom->name[0] ? rom->name : rom->filename,
                           safe_name, sizeof(safe_name));
        int n = snprintf(out_path, out_size,
                         "%s/%s/%s",
                         ROM_TARGET_PSXISO_DIR, safe_name, rom->filename);
        return n > 0 && (size_t)n < out_size;
    }

    const char *dir;
    if (strcasecmp(ext, ".iso") == 0)      dir = ROM_TARGET_ISO_DIR;
    else if (strcasecmp(ext, ".pkg") == 0) dir = ROM_TARGET_PKG_DIR;
    else                                   dir = ROM_TARGET_FALLBACK_DIR;

    int n = snprintf(out_path, out_size, "%s/%s", dir, rom->filename);
    return n > 0 && (size_t)n < out_size;
}

bool roms_resolve_bundle_file_target(const char *system,
                                     const char *game_name,
                                     const char *bundle_file_name,
                                     char *out_path, size_t out_size) {
    if (!bundle_file_name || !out_path || out_size < 32) return false;

    /* Use the basename (everything after the last '/') so a manifest
     * entry like "DLC/Foo.pkg" still lands as /dev_hdd0/packages/Foo.pkg.
     * The per-file installer (e.g. MultiMAN) doesn't care about the
     * subfolder structure inside the bundle — only the file extension
     * matters for routing. */
    const char *slash = strrchr(bundle_file_name, '/');
    const char *base = slash ? slash + 1 : bundle_file_name;
    if (!*base) return false;

    /* PS1 bundles always route to /dev_hdd0/PSXISO/<game name>/<file>. */
    if (system && strcasecmp(system, "PS1") == 0) {
        char safe_name[160];
        sanitize_game_name(game_name, safe_name, sizeof(safe_name));
        int n = snprintf(out_path, out_size,
                         "%s/%s/%s",
                         ROM_TARGET_PSXISO_DIR, safe_name, base);
        return n > 0 && (size_t)n < out_size;
    }

    const char *ext = file_extension(base);
    const char *dir;

    if (strcasecmp(ext, ".pkg") == 0)      dir = ROM_TARGET_PKG_DIR;
    else if (strcasecmp(ext, ".rap") == 0) dir = ROM_TARGET_EXDATA_DIR;
    else if (strcasecmp(ext, ".edat") == 0) dir = ROM_TARGET_EXDATA_DIR;
    else if (strcasecmp(ext, ".iso") == 0) dir = ROM_TARGET_ISO_DIR;
    else                                   dir = ROM_TARGET_PKG_DIR;

    int n = snprintf(out_path, out_size, "%s/%s", dir, base);
    return n > 0 && (size_t)n < out_size;
}

/* --- Bundle manifest fetch --- */

bool roms_fetch_bundle_manifest(const SyncState *state,
                                const char *rom_id,
                                char *scratch_buf, uint32_t scratch_buf_size,
                                RomBundleManifest *manifest) {
    if (!state || !rom_id || !scratch_buf || !manifest) return false;
    manifest->count = 0;
    manifest->total_size = 0;
    manifest->last_error[0] = '\0';

    /* Direct HTTP request via the catalog helper — manifest endpoint is
     * just /roms/<id>/manifest, returns small JSON, fits comfortably in
     * the catalog scratch buffer. */
    char path[256];
    snprintf(path, sizeof(path), "/api/v1/roms/%s/manifest", rom_id);

    int status = 0;
    int n = -1;
    /* Reuse network_fetch_rom_catalog's underlying http_request path
     * indirectly — re-issue via a manifest-specific helper.  Defined
     * inline because it's only used here. */
    extern int network_fetch_rom_manifest_http(const SyncState *state,
                                               const char *rom_id,
                                               char *out, uint32_t out_size,
                                               int *status_out);
    n = network_fetch_rom_manifest_http(state, rom_id,
                                        scratch_buf, scratch_buf_size, &status);
    if (n <= 0 || status != 200) {
        snprintf(manifest->last_error, sizeof(manifest->last_error),
                 "Manifest fetch failed (HTTP %d, n=%d)", status, n);
        debug_log("roms: %s", manifest->last_error);
        return false;
    }

    /* Skip past the outer ``{`` so find_key matches top-level keys —
     * see the comment in roms_fetch_ps3_catalog for the depth-counting
     * rationale. */
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

    debug_log("roms: bundle %s manifest = %d files, total=%llu",
              rom_id, manifest->count,
              (unsigned long long)manifest->total_size);
    return true;
}

/* --- Filesystem prep --- */

static void mkdir_p(const char *path) {
    /* First try stdlib mkdir() — works on most PSL1GHT setups.  If it fails
     * with anything other than EEXIST, fall back to sysFsMkdir() which is
     * the real CellFS API.  Both are safe to call repeatedly. */
    if (mkdir(path, 0755) == 0) return;
    if (errno == EEXIST) return;

    s32 r = sysFsMkdir(path, 0777);
    if (r != 0 && r != (s32)0x80010014 /* ALREADY_EXISTS */) {
        debug_log("roms: mkdir %s failed (errno=%d sysfs=0x%x)",
                  path, errno, (unsigned)r);
    }
}

void roms_ensure_target_dirs(void) {
    mkdir_p(ROM_TARGET_ISO_DIR);
    mkdir_p(ROM_TARGET_PKG_DIR);
    mkdir_p(ROM_TARGET_EXDATA_DIR);
    mkdir_p(ROM_TARGET_PSXISO_DIR);
    mkdir_p(ROM_TARGET_FALLBACK_DIR);
}

/* Public mkdir_p so main.c can ensure a per-game PS1 subfolder exists
 * right before running the download streamer (the streamer fopen's a
 * .part file inside it). */
void roms_mkdir_p(const char *path) { mkdir_p(path); }

/* --- Free-space check --- */

bool roms_check_free_space(uint64_t required_bytes, uint64_t *available_out) {
    /* PSL1GHT exposes sysFsGetFreeSize via lv2/sysfs.h — try it first.
     * On failure fall through to a "we don't know, allow the download"
     * policy so a misconfigured FS doesn't block the user. */
    u32 block_size = 0;
    u64 free_blocks = 0;
    s32 r = sysFsGetFreeSize("/dev_hdd0", &block_size, &free_blocks);
    if (r != 0) {
        debug_log("roms: sysFsGetFreeSize failed 0x%x — skipping precheck",
                  (unsigned)r);
        if (available_out) *available_out = 0;
        return true;
    }

    uint64_t free_bytes = (uint64_t)block_size * (uint64_t)free_blocks;
    if (available_out) *available_out = free_bytes;

    /* Keep a 200 MB cushion so we don't fill the drive completely (the
     * XMB needs scratch space for its own operations). */
    const uint64_t cushion = 200ULL * 1024ULL * 1024ULL;
    if (free_bytes < required_bytes + cushion) {
        debug_log("roms: insufficient space free=%llu required=%llu (+%llu cushion)",
                  (unsigned long long)free_bytes,
                  (unsigned long long)required_bytes,
                  (unsigned long long)cushion);
        return false;
    }
    return true;
}
