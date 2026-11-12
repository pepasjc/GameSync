/*
 * GameSync PS5 client - the server's ROM catalog and installing from it.
 * SPDX-License-Identifier: GPL-3.0-or-later
 *
 * Caching follows the MiSTer / Steam Deck clients (shared/catalog_cache.py):
 * GET /roms/fingerprints names a fingerprint per system; a system whose
 * fingerprint is unchanged loads from <data>/catalog/<SYS>.bin, the others
 * are refetched. Offline, every cached system is shown; a server without the
 * route is browsed live, uncached. The fingerprint covers the RA fields too,
 * so new RetroAchievements badges refresh the cache by themselves.
 *
 * RetroAchievements levels as in shared/ra_variants.py game_ra_verified():
 * verified = achievements and (ra_match "hash" or ra_dump); a title-only
 * match is the weak "RA?".
 *
 * Install targets:
 *  - PS1 -> SwanStationPS5: <dir>/games/<game>/<file> (one folder per game,
 *    as PSXS5's shelf expects). Single-file images only (.chd/.pbp/.iso).
 *  - PS2 -> PS5SX2: <dir>/games/<file> (.iso/.chd/.cso/.zso).
 *  - everything RetroArch plays -> <dir>/content/<system>/<file>, the
 *    folders the PS5 port's library groups by.
 * Multi-file bundles (zipped .cue/.bin sets, MSU packs...) aren't installed
 * by this version.
 */
#include "catalog.h"

#include "fetch.h"
#include "json.h"
#include "melonds.h"
#include "unzip.h"
#include "util.h"

#include <ctype.h>
#include <dirent.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>

#define CACHE_MAGIC "GSC1"
#define CACHE_VERSION 5u

void catalog_free(Catalog *catalog)
{
    free(catalog->systems);
    free(catalog->roms);
    memset(catalog, 0, sizeof(*catalog));
}

static void http_fail(char *error, size_t size, const char *what, int code)
{
    snprintf(error, size, "%s: %s", what, http_error_text(code));
}

/* ------------------------------------------------------------ rows */

static void parse_row(const JsonValue *r, const char *system, RomRow *row)
{
    memset(row, 0, sizeof(*row));
    util_copy(row->rom_id, sizeof(row->rom_id), json_get_str(r, "rom_id", ""));
    util_copy(row->name, sizeof(row->name), json_get_str(r, "name", ""));
    util_copy(row->filename, sizeof(row->filename), json_get_str(r, "filename", ""));
    util_copy(row->system, sizeof(row->system), system);
    util_copy(row->title_id, sizeof(row->title_id), json_get_str(r, "title_id", ""));
    util_copy(row->bundle_kind, sizeof(row->bundle_kind), json_get_str(r, "bundle_kind", ""));
    row->size = (uint64_t)json_get_num(r, "size", 0);
    row->is_bundle = json_get_bool(r, "is_bundle", false);
    row->ra_game_id = (int)json_get_num(r, "ra_game_id", 0);
    row->ra_achievements = (int)json_get_num(r, "ra_achievements", 0);
    const char *match = json_get_str(r, "ra_match", "");
    row->ra_match = row->ra_game_id ? (util_ieq(match, "title") ? 2 : 1) : 0;
    row->ra_dump = json_get_bool(r, "ra_dump", false);
    JsonValue *formats = json_get(r, "extract_formats");
    for (JsonValue *f = formats ? formats->child : NULL; f; f = f->next)
    {
        if (f->type == JSON_STRING && util_ieq(f->str, "cue"))
            row->has_cue = true;
        if (f->type == JSON_STRING && util_ieq(f->str, "iso"))
            row->has_iso = true;
        if (f->type == JSON_STRING && util_ieq(f->str, "decrypted_cci"))
            row->has_dcci = true;
    }
    row->disc_index = (uint8_t)json_get_num(r, "disc_index", 0);
    row->disc_total = (uint8_t)json_get_num(r, "disc_total", 0);
    if (!row->name[0])
        util_copy(row->name, sizeof(row->name), row->filename);
}

typedef struct
{
    RomRow *rows;
    int count;
    int cap;
} RowList;

static bool rows_push(RowList *l, const RomRow *row)
{
    if (l->count == l->cap)
    {
        int cap = l->cap ? l->cap * 2 : 1024;
        RomRow *grown = realloc(l->rows, (size_t)cap * sizeof(RomRow));
        if (!grown)
            return false;
        l->rows = grown;
        l->cap = cap;
    }
    l->rows[l->count++] = *row;
    return true;
}

/* All of one system's rows from the server. Only rows whose system is
 * exactly this one: the server also answers for alias systems. */
static bool fetch_system(const HttpServer *server, const char *system, RowList *out, char *error,
                         size_t size)
{
    int offset = 0;
    for (;;)
    {
        char path[160];
        snprintf(path, sizeof(path), "/api/v1/roms?system=%s&limit=2000&offset=%d", system, offset);
        HttpBuffer buf = {0};
        int status = http_get(server, path, &buf, NULL);
        if (status != 200)
        {
            http_buffer_free(&buf);
            http_fail(error, size, "listing ROMs", status);
            return false;
        }
        JsonValue *root = json_parse((const char *)buf.data);
        http_buffer_free(&buf);
        JsonValue *roms = json_get(root, "roms");
        int page = 0;
        for (JsonValue *r = roms ? roms->child : NULL; r; r = r->next, ++page)
        {
            if (!util_ieq(json_get_str(r, "system", system), system))
                continue;
            RomRow row;
            parse_row(r, system, &row);
            if (!rows_push(out, &row))
                break;
        }
        bool more = json_get_bool(root, "has_more", false);
        json_free(root);
        offset += page;
        if (!more || page == 0)
            return true;
    }
}

/* ------------------------------------------------------------ cache */

static void cache_path(char *out, size_t size, const char *system)
{
    char name[32];
    snprintf(name, sizeof(name), "catalog/%s.bin", system);
    util_copy(out, size, data_path(name));
}

/* Appends a cached system's rows; fills fingerprint. false when absent or stale-format. */
static bool cache_read(const char *system, RowList *out, char *fingerprint, size_t fp_size)
{
    char path[PATH_LEN];
    cache_path(path, sizeof(path), system);
    FILE *f = fopen(path, "rb");
    if (!f)
        return false;
    char magic[4];
    uint32_t version = 0, row_size = 0, count = 0;
    char fp[96];
    bool ok = fread(magic, 1, 4, f) == 4 && memcmp(magic, CACHE_MAGIC, 4) == 0 &&
              fread(&version, 4, 1, f) == 1 && version == CACHE_VERSION &&
              fread(&row_size, 4, 1, f) == 1 && row_size == sizeof(RomRow) &&
              fread(fp, 1, sizeof(fp), f) == sizeof(fp) && fread(&count, 4, 1, f) == 1 &&
              count < 1000000;
    for (uint32_t i = 0; ok && i < count; ++i)
    {
        RomRow row;
        ok = fread(&row, sizeof(row), 1, f) == 1;
        if (ok)
        {
            row.name[sizeof(row.name) - 1] = 0;
            row.filename[sizeof(row.filename) - 1] = 0;
            ok = rows_push(out, &row);
        }
    }
    fclose(f);
    if (ok)
    {
        fp[sizeof(fp) - 1] = 0;
        util_copy(fingerprint, fp_size, fp);
    }
    return ok;
}

static void cache_write(const char *system, const RomRow *rows, int count, const char *fingerprint)
{
    char path[PATH_LEN];
    cache_path(path, sizeof(path), system);
    size_t bytes = 4 + 4 + 4 + 96 + 4 + (size_t)count * sizeof(RomRow);
    uint8_t *buf = malloc(bytes);
    if (!buf)
        return;
    uint8_t *p = buf;
    uint32_t version = CACHE_VERSION, row_size = sizeof(RomRow), n = (uint32_t)count;
    char fp[96] = {0};
    util_copy(fp, sizeof(fp), fingerprint);
    memcpy(p, CACHE_MAGIC, 4);
    p += 4;
    memcpy(p, &version, 4);
    p += 4;
    memcpy(p, &row_size, 4);
    p += 4;
    memcpy(p, fp, sizeof(fp));
    p += sizeof(fp);
    memcpy(p, &n, 4);
    p += 4;
    memcpy(p, rows, (size_t)count * sizeof(RomRow));
    util_write_file_atomic(path, buf, bytes);
    free(buf);
}

void catalog_reset(const HttpServer *server)
{
    HttpBuffer buf = {0};
    http_get(server, "/api/v1/roms/scan", &buf, NULL); /* refused is fine */
    http_buffer_free(&buf);
    char dir[PATH_LEN];
    util_copy(dir, sizeof(dir), data_path("catalog"));
    DIR *d = opendir(dir);
    if (!d)
        return;
    struct dirent *de;
    while ((de = readdir(d)) != NULL)
    {
        if (!util_has_suffix_i(de->d_name, ".bin"))
            continue;
        char path[PATH_LEN];
        util_path_join(path, sizeof(path), dir, de->d_name);
        remove(path);
    }
    closedir(d);
}

/* ------------------------------------------------------------ load */

static int compare_systems(const void *a, const void *b)
{
    const SystemRow *x = a, *y = b;
    return strcasecmp(system_display_name(x->system), system_display_name(y->system));
}

static int compare_rows(const void *a, const void *b)
{
    const RomRow *x = a, *y = b;
    int c = strcasecmp(system_display_name(x->system), system_display_name(y->system));
    return c ? c : strcasecmp(x->name, y->name);
}

static bool add_system(Catalog *c, const char *system, int count, const char *fingerprint)
{
    SystemRow *grown = realloc(c->systems, (size_t)(c->system_count + 1) * sizeof(SystemRow));
    if (!grown)
        return false;
    c->systems = grown;
    SystemRow *s = &c->systems[c->system_count++];
    memset(s, 0, sizeof(*s));
    util_copy(s->system, sizeof(s->system), system);
    util_copy(s->fingerprint, sizeof(s->fingerprint), fingerprint);
    s->count = count;
    return true;
}

/* Offline: every system with a cache file. */
static void load_offline(Catalog *c, RowList *rows)
{
    char dir[PATH_LEN];
    util_copy(dir, sizeof(dir), data_path("catalog"));
    DIR *d = opendir(dir);
    if (!d)
        return;
    struct dirent *de;
    while ((de = readdir(d)) != NULL)
    {
        if (!util_has_suffix_i(de->d_name, ".bin"))
            continue;
        char system[16];
        util_copy(system, sizeof(system), de->d_name);
        util_strip_ext(system);
        int before = rows->count;
        char fp[96];
        if (cache_read(system, rows, fp, sizeof(fp)))
            add_system(c, system, rows->count - before, fp);
    }
    closedir(d);
}

bool catalog_load(const HttpServer *server, Catalog *c, CatalogProgressFn progress, void *user,
                  char *error, size_t size)
{
    catalog_free(c);
    util_mkdirs(data_path("catalog"));
    RowList rows = {0};
    HttpBuffer buf = {0};
    if (progress)
        progress("Checking the catalog", 0, 0, user);
    int status = http_get(server, "/api/v1/roms/fingerprints", &buf, NULL);
    JsonValue *root = status == 200 ? json_parse((const char *)buf.data) : NULL;
    http_buffer_free(&buf);

    if (status == 200 && root)
    {
        c->cached = true;
        JsonValue *systems = json_get(root, "systems");
        int total = 0, done = 0;
        for (JsonValue *s = systems ? systems->child : NULL; s; s = s->next)
            if (s->key && install_emulator_for_system(s->key, false) != EMU_NONE)
                total++;
        for (JsonValue *s = systems ? systems->child : NULL; s; s = s->next)
        {
            if (!s->key || install_emulator_for_system(s->key, false) == EMU_NONE)
                continue;
            const char *fp = json_get_str(s, "fingerprint", "");
            int before = rows.count;
            char cached_fp[96] = "";
            bool fresh = cache_read(s->key, &rows, cached_fp, sizeof(cached_fp)) &&
                         strcmp(cached_fp, fp) == 0;
            if (!fresh)
            {
                rows.count = before; /* drop a stale copy */
                if (progress)
                {
                    char msg[96];
                    snprintf(msg, sizeof(msg), "Updating %s", system_display_name(s->key));
                    progress(msg, done, total, user);
                }
                if (!fetch_system(server, s->key, &rows, error, size))
                {
                    rows.count = before;
                    continue;
                }
                cache_write(s->key, rows.rows + before, rows.count - before, fp);
            }
            add_system(c, s->key, rows.count - before, fp);
            done++;
        }
        json_free(root);
    }
    else if (status == 404)
    {
        /* an older server: live, uncached */
        json_free(root);
        HttpBuffer sys = {0};
        status = http_get(server, "/api/v1/roms/systems", &sys, NULL);
        JsonValue *sroot = status == 200 ? json_parse((const char *)sys.data) : NULL;
        http_buffer_free(&sys);
        JsonValue *stats = json_get(sroot, "stats");
        for (JsonValue *s = stats ? stats->child : NULL; s; s = s->next)
        {
            if (!s->key || install_emulator_for_system(s->key, false) == EMU_NONE)
                continue;
            int before = rows.count;
            if (fetch_system(server, s->key, &rows, error, size))
                add_system(c, s->key, rows.count - before, "");
        }
        json_free(sroot);
    }
    else
    {
        json_free(root);
        c->offline = true;
        load_offline(c, &rows);
        if (!c->system_count)
        {
            http_fail(error, size, "the catalog", status);
            free(rows.rows);
            return false;
        }
    }
    c->roms = rows.rows;
    c->rom_count = rows.count;
    if (c->system_count > 1)
        qsort(c->systems, (size_t)c->system_count, sizeof(SystemRow), compare_systems);
    if (c->rom_count > 1)
        qsort(c->roms, (size_t)c->rom_count, sizeof(RomRow), compare_rows);
    catalog_group_discs(c);
    if (!c->system_count)
    {
        snprintf(error, size, "the server has no ROMs for systems the PS5 emulators play");
        return false;
    }
    return true;
}

/* ------------------------------------------------------------ queries */

RaLevel catalog_ra_level(const RomRow *rom)
{
    if (rom->ra_achievements <= 0)
        return RA_NONE;
    return rom->ra_match == 1 || rom->ra_dump ? RA_VERIFIED : RA_TITLE;
}

/* "vii" <-> "7", "ii" <-> "2" ... for 1-20 */
static const char *const ROMAN[] = {"",   "i",   "ii",   "iii", "iv", "v",   "vi",
                                    "vii", "viii", "ix", "x",   "xi", "xii", "xiii",
                                    "xiv", "xv",  "xvi", "xvii", "xviii", "xix", "xx"};

static void lower_alnum(char *out, size_t size, const char *s)
{
    size_t o = 0;
    for (; *s && o + 1 < size; ++s)
    {
        unsigned char ch = (unsigned char)tolower((unsigned char)*s);
        out[o++] = (ch >= 'a' && ch <= 'z') || (ch >= '0' && ch <= '9') ? (char)ch : ' ';
    }
    out[o] = 0;
}

/* token as a whole word or a substring of the joined text */
static bool has_token(const char *text, const char *joined, const char *token)
{
    if (strstr(joined, token))
        return true;
    size_t n = strlen(token);
    for (const char *p = text; (p = strstr(p, token)) != NULL; p += n)
        if ((p == text || p[-1] == ' ') && (p[n] == ' ' || p[n] == 0))
            return true;
    return false;
}

bool catalog_matches(const RomRow *rom, const char *query)
{
    char q[160];
    lower_alnum(q, sizeof(q), query);
    char text[600], joined[600];
    char raw[600];
    snprintf(raw, sizeof(raw), "%s %s %s", rom->name, rom->filename, rom->title_id);
    lower_alnum(text, sizeof(text), raw);
    size_t j = 0;
    for (const char *p = text; *p && j + 1 < sizeof(joined); ++p)
        if (*p != ' ')
            joined[j++] = *p;
    joined[j] = 0;
    char *save = NULL;
    for (char *tok = strtok_r(q, " ", &save); tok; tok = strtok_r(NULL, " ", &save))
    {
        bool found = has_token(text, joined, tok);
        for (int n = 1; !found && n <= 20; ++n)
        {
            char digits[4];
            snprintf(digits, sizeof(digits), "%d", n);
            if (strcmp(tok, digits) == 0)
                found = has_token(text, "", ROMAN[n]);
            else if (strcmp(tok, ROMAN[n]) == 0)
                found = has_token(text, "", digits);
        }
        if (!found)
            return false;
    }
    return true;
}

const RomRow *catalog_find_file(const Catalog *catalog, const char *system, const char *filename)
{
    for (int i = 0; i < catalog->rom_count; ++i)
    {
        const RomRow *r = &catalog->roms[i];
        if (util_ieq(r->system, system) && util_ieq(r->filename, filename))
            return r;
    }
    return NULL;
}

/* ------------------------------------------------------------ installs */

static bool ext_in(const char *filename, const char *const *exts, size_t n)
{
    const char *ext = util_ext(filename);
    for (size_t i = 0; i < n; ++i)
        if (util_ieq(ext, exts[i]))
            return true;
    return false;
}

void catalog_plan(const Config *config, const RomRow *rom, InstallPlan *plan)
{
    memset(plan, 0, sizeof(*plan));
    plan->grouped = rom->group[0] != 0;
    plan->emu = install_emulator_for_system(rom->system, config->ps1_in_retroarch);
    char file[200];
    util_safe_filename(file, sizeof(file), rom->filename);
    if (plan->emu == EMU_NONE)
    {
        snprintf(plan->reason, sizeof(plan->reason), "no PS5 emulator for %s", rom->system);
        return;
    }
    if (rom->is_bundle)
    {
        char game[200];
        util_copy(game, sizeof(game), file);
        util_strip_ext(game); /* bundles are listed as <name>.zip */
        if (plan->emu == EMU_XEMU && rom->has_iso)
        {
            /* the server turns the Xbox folder (.cci + default.xbe) into one XISO */
            snprintf(plan->dest, sizeof(plan->dest), "%s/games/%s.iso", config->xemu_dir, game);
            plan->extract = "iso";
        }
        else if (plan->emu == EMU_CEMU && util_ieq(rom->system, "WIIU"))
        {
            /* a WUP/NUS set (title.tmd + .app + title.tik) or a loadiine tree,
             * which Cemu runs as it is from its games folder */
            snprintf(plan->dest, sizeof(plan->dest), "%s/games/%s", config->cemu_dir, game);
            snprintf(plan->check, sizeof(plan->check), "%s/.gamesync-complete", plan->dest);
            plan->folder = true;
            plan->supported = true;
            return;
        }
        else
        {
            snprintf(plan->reason, sizeof(plan->reason), "multi-file %s games aren't supported yet",
                     rom->system);
            return;
        }
        plan->supported = true;
        util_copy(plan->check, sizeof(plan->check), plan->dest);
        return;
    }
    if (plan->emu == EMU_SWANSTATION)
    {
        static const char *const ok[] = {".chd", ".pbp", ".iso"};
        if (!ext_in(file, ok, 3))
        {
            snprintf(plan->reason, sizeof(plan->reason), "SwanStation install needs .chd/.pbp/.iso");
            return;
        }
        char game[200];
        util_copy(game, sizeof(game), file);
        util_strip_ext(game);
        if (rom->group[0])
        {
            /* every disc in one folder, loaded through <game>.m3u */
            snprintf(plan->dest, sizeof(plan->dest), "%s/games/%s/%s", config->swanstation_dir,
                     rom->group, file);
            snprintf(plan->m3u, sizeof(plan->m3u), "%s/games/%s/%s.m3u", config->swanstation_dir,
                     rom->group, rom->group);
            util_copy(plan->m3u_line, sizeof(plan->m3u_line), file);
        }
        else
            snprintf(plan->dest, sizeof(plan->dest), "%s/games/%s/%s", config->swanstation_dir,
                     game, file);
    }
    else if (plan->emu == EMU_PORPOISE)
    {
        static const char *const ok[] = {".iso", ".gcm", ".rvz", ".ciso", ".gcz", ".wbfs", ".wia", ".wad"};
        if (util_has_suffix_i(file, ".zip"))
        {
            /* the .rvz (or .iso, .wad...) inside, served out of the zip */
            char game[200];
            util_copy(game, sizeof(game), file);
            util_strip_ext(game);
            if (rom->group[0])
                snprintf(plan->dest, sizeof(plan->dest), "%s/games/%s/%s", config->porpoise_dir,
                         rom->group, game);
            else
                snprintf(plan->dest, sizeof(plan->dest), "%s/games/%s", config->porpoise_dir, game);
            util_copy(plan->check, sizeof(plan->check), plan->dest);
            plan->zip_member = true;
            plan->supported = true;
            return;
        }
        if (!ext_in(file, ok, 8))
        {
            snprintf(plan->reason, sizeof(plan->reason), "Porpoise needs .iso/.rvz/.gcm/.wbfs...");
            return;
        }
        if (rom->group[0]) /* Porpoise can't change discs: each boots on its own */
            snprintf(plan->dest, sizeof(plan->dest), "%s/games/%s/%s", config->porpoise_dir,
                     rom->group, file);
        else
            snprintf(plan->dest, sizeof(plan->dest), "%s/games/%s", config->porpoise_dir, file);
    }
    else if (plan->emu == EMU_CEMU && util_ieq(rom->system, "NDS"))
    {
        /* PS5CEMU-HAR's melonDS: .nds/.srl/.dsi in its games folder; a zip's
         * game file is served out of it (melonDS doesn't open zips) */
        char games[PATH_LEN];
        melonds_games_dir(config, games, sizeof(games));
        if (util_has_suffix_i(file, ".zip"))
        {
            char game[200];
            util_copy(game, sizeof(game), file);
            util_strip_ext(game);
            snprintf(plan->dest, sizeof(plan->dest), "%s/%s", games, game);
            plan->zip_member = true;
            plan->supported = true;
            return;
        }
        if (!melonds_is_game(file))
        {
            snprintf(plan->reason, sizeof(plan->reason), "PS5CEMU's melonDS needs .nds/.srl/.dsi");
            return;
        }
        snprintf(plan->dest, sizeof(plan->dest), "%s/%s", games, file);
    }
    else if (plan->emu == EMU_CEMU)
    {
        /* PS5CEMU-HAR: Wii U files in games/, 3DS in azahar/games/ (both flat) */
        bool wiiu = util_ieq(rom->system, "WIIU");
        static const char *const wiiu_ok[] = {".wua", ".wud", ".wux", ".rpx", ".wuhb"};
        static const char *const ds_ok[] = {".3ds", ".cci", ".cxi", ".3dsx", ".app",
                                            ".z3ds", ".zcci", ".zcxi", ".z3dsx"};
        if (!wiiu && !ext_in(file, ds_ok, 9) && rom->has_dcci)
        {
            /* a zipped or encrypted dump: the server decrypts it into the
             * CCI Azahar loads (SYNC_ROM_3DS_DECRYPTED_CCI_COMMAND) */
            char game[200];
            util_copy(game, sizeof(game), file);
            util_strip_ext(game);
            snprintf(plan->dest, sizeof(plan->dest), "%s/azahar/games/%s.cci", config->cemu_dir, game);
            plan->extract = "decrypted_cci";
            plan->supported = true;
            util_copy(plan->check, sizeof(plan->check), plan->dest);
            return;
        }
        if (wiiu ? !ext_in(file, wiiu_ok, 5) : !ext_in(file, ds_ok, 9))
        {
            snprintf(plan->reason, sizeof(plan->reason),
                     wiiu ? "PS5CEMU needs a .wua/.wud/.wux/.rpx or a WUP folder"
                          : "PS5CEMU's Azahar needs .3ds/.cci/.cxi/.3dsx (the server can't decrypt this one)");
            return;
        }
        snprintf(plan->dest, sizeof(plan->dest), "%s/%s/%s", config->cemu_dir,
                 wiiu ? "games" : "azahar/games", file);
    }
    else if (plan->emu == EMU_XEMU)
    {
        static const char *const ok[] = {".iso", ".xiso"};
        if (!ext_in(file, ok, 2))
        {
            snprintf(plan->reason, sizeof(plan->reason), "XPSemu needs an XISO (.iso/.xiso)");
            return;
        }
        snprintf(plan->dest, sizeof(plan->dest), "%s/games/%s", config->xemu_dir, file);
    }
    else if (plan->emu == EMU_EDEN)
    {
        static const char *const ok[] = {".nsp", ".xci"};
        if (!ext_in(file, ok, 2))
        {
            snprintf(plan->reason, sizeof(plan->reason), "ProsperoEden needs .nsp/.xci");
            return;
        }
        snprintf(plan->dest, sizeof(plan->dest), "%s/roms/%s", config->eden_dir, file);
    }
    else if (plan->emu == EMU_PS5SX2)
    {
        static const char *const ok[] = {".iso", ".chd", ".cso", ".zso"};
        if (!ext_in(file, ok, 4))
        {
            snprintf(plan->reason, sizeof(plan->reason), "PS5SX2 needs .iso/.chd/.cso/.zso");
            return;
        }
        if (rom->group[0]) /* PS5SX2 swaps between the (Disc N) files of one folder */
            snprintf(plan->dest, sizeof(plan->dest), "%s/games/%s/%s", config->ps5sx2_dir,
                     rom->group, file);
        else
            snprintf(plan->dest, sizeof(plan->dest), "%s/games/%s", config->ps5sx2_dir, file);
    }
    else if (rom->group[0])
    {
        /* the discs in a dot folder its library doesn't list, the game as
         * <game>.m3u - which also names its save (<game>.srm) */
        const char *folder = retroarch_folder_for_system(rom->system);
        snprintf(plan->dest, sizeof(plan->dest), "%s/content/%s/%s/.discs/%s", config->retroarch_dir,
                 folder, rom->group, file);
        snprintf(plan->m3u, sizeof(plan->m3u), "%s/content/%s/%s/%s.m3u", config->retroarch_dir,
                 folder, rom->group, rom->group);
        snprintf(plan->m3u_line, sizeof(plan->m3u_line), ".discs/%s", file);
    }
    else
    {
        snprintf(plan->dest, sizeof(plan->dest), "%s/content/%s/%s", config->retroarch_dir,
                 retroarch_folder_for_system(rom->system), file);
    }
    plan->supported = true;
    util_copy(plan->check, sizeof(plan->check), plan->dest);
}

void catalog_plan_cue(const Config *config, const RomRow *rom, InstallPlan *plan)
{
    memset(plan, 0, sizeof(*plan));
    plan->grouped = rom->group[0] != 0;
    plan->cue = true;
    plan->emu = install_emulator_for_system(rom->system, config->ps1_in_retroarch);
    if (!rom->has_cue)
    {
        snprintf(plan->reason, sizeof(plan->reason), "the server has no CUE/BIN version of it");
        return;
    }
    char game[200];
    util_safe_filename(game, sizeof(game), rom->filename);
    util_strip_ext(game);
    /* a disc of a multi-disc game: its set goes into the game's folder */
    const char *folder = rom->group[0] ? rom->group : game;
    if (plan->emu == EMU_SWANSTATION)
    {
        snprintf(plan->unzip_dir, sizeof(plan->unzip_dir), "%s/games/%s", config->swanstation_dir,
                 folder);
        if (rom->group[0])
        {
            snprintf(plan->m3u, sizeof(plan->m3u), "%s/%s.m3u", plan->unzip_dir, rom->group);
            snprintf(plan->m3u_line, sizeof(plan->m3u_line), "%s.cue", game);
        }
    }
    else if (plan->emu == EMU_PS5SX2)
        snprintf(plan->unzip_dir, sizeof(plan->unzip_dir), "%s/games/%s", config->ps5sx2_dir, folder);
    else if (plan->emu == EMU_RETROARCH && rom->group[0])
    {
        snprintf(plan->unzip_dir, sizeof(plan->unzip_dir), "%s/content/%s/%s/.discs",
                 config->retroarch_dir, retroarch_folder_for_system(rom->system), rom->group);
        snprintf(plan->m3u, sizeof(plan->m3u), "%s/content/%s/%s/%s.m3u", config->retroarch_dir,
                 retroarch_folder_for_system(rom->system), rom->group, rom->group);
        snprintf(plan->m3u_line, sizeof(plan->m3u_line), ".discs/%s.cue", game);
    }
    else if (plan->emu == EMU_RETROARCH)
        snprintf(plan->unzip_dir, sizeof(plan->unzip_dir), "%s/content/%s/%s",
                 config->retroarch_dir, retroarch_folder_for_system(rom->system), game);
    else
    {
        snprintf(plan->reason, sizeof(plan->reason), "no CUE/BIN install for %s", rom->system);
        return;
    }
    snprintf(plan->dest, sizeof(plan->dest), "%s/%s.cue.zip", plan->unzip_dir, game);
    snprintf(plan->check, sizeof(plan->check), "%s/%s.cue", plan->unzip_dir, game);
    plan->supported = true;
}

/* any .cue in the folder: the set's own name can differ from the CHD's */
static bool folder_has_cue(const char *dir)
{
    DIR *d = opendir(dir);
    if (!d)
        return false;
    bool found = false;
    struct dirent *de;
    while (!found && (de = readdir(d)) != NULL)
        found = util_has_suffix_i(de->d_name, ".cue");
    closedir(d);
    return found;
}

/* What Porpoise loads, in the order a zip's members are preferred. */
static const char *const DOLPHIN_EXTS[] = {".rvz", ".iso", ".wbfs", ".gcm", ".ciso",
                                           ".gcz", ".wia", ".wad"};
static const char *const DS_EXTS[] = {".nds", ".dsi", ".srl"};

/* The game files a zip_member plan takes out of a zip, best first. */
static const char *const *member_exts(const InstallPlan *plan, size_t *count)
{
    if (plan->emu == EMU_CEMU)
    {
        *count = sizeof(DS_EXTS) / sizeof(DS_EXTS[0]);
        return DS_EXTS;
    }
    *count = sizeof(DOLPHIN_EXTS) / sizeof(DOLPHIN_EXTS[0]);
    return DOLPHIN_EXTS;
}

bool catalog_installed(const InstallPlan *plan)
{
    if (!plan->supported)
        return false;
    if (plan->zip_member)
    {
        size_t n;
        const char *const *exts = member_exts(plan, &n);
        for (size_t i = 0; i < n; ++i)
        {
            char path[PATH_LEN + 8];
            snprintf(path, sizeof(path), "%s%s", plan->dest, exts[i]);
            if (util_file_exists(path))
                return true;
        }
        return false;
    }
    /* one game's folder holds every disc's set: only this disc's .cue says it's here */
    if (plan->cue && !plan->grouped)
        return folder_has_cue(plan->unzip_dir);
    return util_file_exists(plan->check);
}


/* Progress over a whole bundle: each file's bytes on top of the ones before. */
typedef struct
{
    HttpProgressFn progress;
    void *user;
    uint64_t base;
    uint64_t total;
} BundleProgress;

static int bundle_progress(uint64_t done, uint64_t total, void *user)
{
    (void)total;
    BundleProgress *b = user;
    return b->progress ? b->progress(b->base + done, b->total, b->user) : 0;
}

/* "content/0010/a b.bin" -> "content/0010/a%20b.bin": each part encoded, the
 * slashes kept. false for a path that could leave the game's folder. */
static bool encode_member(char *out, size_t size, const char *name)
{
    if (!name[0] || name[0] == '/' || strstr(name, "..") || strchr(name, '\\'))
        return false;
    size_t n = 0;
    out[0] = 0;
    char part[256];
    for (const char *p = name; *p;)
    {
        const char *slash = strchr(p, '/');
        size_t len = slash ? (size_t)(slash - p) : strlen(p);
        if (len >= sizeof(part))
            return false;
        memcpy(part, p, len);
        part[len] = 0;
        char enc[768];
        util_url_encode(enc, sizeof(enc), part);
        int w = snprintf(out + n, size - n, "%s%s", n ? "/" : "", enc);
        if (w < 0 || (size_t)w >= size - n)
            return false;
        n += (size_t)w;
        p = slash ? slash + 1 : p + len;
    }
    return n > 0;
}

/* A bundle's file list from /roms/<id>/manifest (NULL on failure). */
static JsonValue *fetch_manifest(const HttpServer *server, const char *id, char *error, size_t size)
{
    char path[400];
    snprintf(path, sizeof(path), "/api/v1/roms/%s/manifest", id);
    HttpBuffer out = {0};
    int status = http_get(server, path, &out, NULL);
    if (status != 200)
    {
        http_buffer_free(&out);
        http_fail(error, size, "file list", status);
        return NULL;
    }
    JsonValue *root = json_parse((const char *)out.data);
    http_buffer_free(&out);
    JsonValue *files = json_get(root, "files");
    if (!files || !files->child)
    {
        json_free(root);
        snprintf(error, size, "empty file list");
        return NULL;
    }
    return root;
}

/* One member of a bundle, /roms/<id>/file/<name>, into dest: resumed from
 * dest.part, kept when it is already there at its size. */
static bool fetch_member(const HttpServer *server, const char *id, const char *name, uint64_t want,
                         const char *dest, BundleProgress *bp, char *error, size_t size)
{
    char member[1024], path[1200], part[PATH_LEN + 8];
    if (!encode_member(member, sizeof(member), name))
    {
        snprintf(error, size, "bad file name in the set: %.60s", name);
        return false;
    }
    if (util_file_size(dest) == (int64_t)want)
    {
        bp->base += want; /* already here from an earlier attempt */
        return true;
    }
    snprintf(part, sizeof(part), "%s.part", dest);
    int64_t have = util_file_size(part);
    if (have < 0 || (uint64_t)have >= want)
        have = 0;
    if (!util_mkdirs_parent(dest))
    {
        snprintf(error, size, "can't create %s", name);
        return false;
    }
    snprintf(path, sizeof(path), "/api/v1/roms/%s/file/%s", id, member);
    bp->base += (uint64_t)have;
    int status;
    int rc = fetch_file(server, path, part, (uint64_t)have, want, 120, bundle_progress, bp, &status,
                        util_basename(name));
    bp->base -= (uint64_t)have;
    if (rc == HTTP_OK && status == 200 && have > 0)
    {
        snprintf(error, size, "resume refused, try again");
        remove(part);
        return false;
    }
    if (rc != HTTP_OK || (status != 200 && status != 206))
    {
        if (rc == HTTP_OK && status >= 300)
        {
            remove(part);
            http_fail(error, size, name, status);
        }
        else
            snprintf(error, size, "%s: %s", util_basename(name),
                     rc == HTTP_ERR_SINK ? "write failed" : http_error_text(rc));
        return false;
    }
    if (util_file_size(part) != (int64_t)want)
    {
        snprintf(error, size, "%s came out the wrong size", util_basename(name));
        remove(part);
        return false;
    }
    remove(dest);
    if (rename(part, dest) != 0)
    {
        snprintf(error, size, "can't rename %s", util_basename(name));
        return false;
    }
    chmod(dest, 0666); /* the emulator is another sandboxed title */
    bp->base += want;
    return true;
}

/* A zipped game (a Wii zip holding an .rvz): only the game file comes down,
 * out of the zip on the server, to dest + its extension. */
static bool download_zip_member(const HttpServer *server, const RomRow *rom,
                                const InstallPlan *plan, HttpProgressFn progress, void *user,
                                char *error, size_t size)
{
    char id[256];
    util_url_encode(id, sizeof(id), rom->rom_id);
    JsonValue *root = fetch_manifest(server, id, error, size);
    if (!root)
        return false;
    JsonValue *members = json_get(root, "members");
    const char *pick = NULL;
    uint64_t want = 0;
    size_t ext_count;
    const char *const *exts = member_exts(plan, &ext_count);
    size_t best = ext_count;
    for (JsonValue *m = members ? members->child : NULL; m; m = m->next)
    {
        const char *name = json_get_str(m, "name", "");
        for (size_t i = 0; i < best; ++i)
            if (util_has_suffix_i(name, exts[i]))
            {
                pick = name;
                want = (uint64_t)json_get_num(m, "size", 0);
                best = i;
                break;
            }
    }
    if (!pick)
    {
        json_free(root);
        snprintf(error, size, members ? "no game file in the zip" : "the server can't open zips (update it)");
        return false;
    }
    char dest[PATH_LEN + 8];
    snprintf(dest, sizeof(dest), "%s%s", plan->dest, exts[best]);
    BundleProgress bp = {progress, user, 0, want};
    bool ok = util_mkdirs_parent(dest) &&
              fetch_member(server, id, pick, want, dest, &bp, error, size);
    json_free(root);
    return ok;
}

/* A bundle (a Wii U WUP folder): every file of the manifest into the
 * folder. A marker file says the set is complete. */
static bool download_folder(const HttpServer *server, const RomRow *rom, const InstallPlan *plan,
                            HttpProgressFn progress, void *user, char *error, size_t size)
{
    char id[256];
    util_url_encode(id, sizeof(id), rom->rom_id);
    JsonValue *root = fetch_manifest(server, id, error, size);
    if (!root)
        return false;
    if (!util_mkdirs(plan->dest))
    {
        json_free(root);
        snprintf(error, size, "can't create the game folder");
        return false;
    }
    BundleProgress bp = {progress, user, 0, (uint64_t)json_get_num(root, "total_size", 0)};
    remove(plan->check);
    bool ok = true;
    for (JsonValue *f = json_get(root, "files")->child; f && ok; f = f->next)
    {
        const char *name = json_get_str(f, "name", "");
        char dest[PATH_LEN];
        if (snprintf(dest, sizeof(dest), "%s/%s", plan->dest, name) >= (int)sizeof(dest))
        {
            snprintf(error, size, "file name too long: %.60s", name);
            ok = false;
            break;
        }
        ok = fetch_member(server, id, name, (uint64_t)json_get_num(f, "size", 0), dest, &bp,
                          error, size);
    }
    json_free(root);
    if (ok)
        ok = util_write_file_atomic(plan->check, "complete\n", 9);
    return ok;
}

bool catalog_download(const HttpServer *server, const RomRow *rom, const InstallPlan *plan,
                      HttpProgressFn progress, void *user, char *error, size_t size)
{
    if (!plan->supported)
    {
        snprintf(error, size, "%s", plan->reason);
        return false;
    }
    if (plan->folder)
        return download_folder(server, rom, plan, progress, user, error, size);
    if (plan->zip_member)
        return download_zip_member(server, rom, plan, progress, user, error, size);
    /* Xbox: the server's XISO (?extract=iso), not the .cci decoded here - an
     * app's writes on the PS5 are throttled to ~2 MB/s after a ~1.3 GB burst,
     * and a local conversion can't be paused to let that recover. The decoder
     * (xcci.c) is kept for a helper process that isn't throttled. */
    if (!util_mkdirs_parent(plan->dest))
    {
        snprintf(error, size, "can't create the game folder");
        return false;
    }
    char part[PATH_LEN + 8];
    snprintf(part, sizeof(part), "%s.part", plan->dest);
    int64_t have = util_file_size(part);
    /* a CUE/BIN set is zipped afresh on each request: no Range on it. A
     * converted ISO/CCI is cached by the server and served with Range, so it
     * resumes; rom->size is the source's size, not the output's. */
    if (have < 0 || plan->cue || (!plan->extract && rom->size && (uint64_t)have >= rom->size))
        have = 0;
    char id[256], path[320];
    util_url_encode(id, sizeof(id), rom->rom_id);
    snprintf(path, sizeof(path), "/api/v1/roms/%s%s%s", id,
             plan->cue ? "?extract=cue" : plan->extract ? "?extract=" : "",
             plan->extract && !plan->cue ? plan->extract : "");
    int status;
    /* CUE/BIN: one request. A conversion: segments once its size is known. */
    int rc = fetch_file(server, path, part, (uint64_t)have,
                        plan->cue ? 0 : plan->extract ? FETCH_SIZE_UNKNOWN : rom->size,
                        20 * 60, /* a server-side conversion can take a while to start */
                        progress, user, &status, rom->name);
    if (rc == HTTP_OK && status == 200 && have > 0)
    {
        /* the server ignored the Range: what we appended is a full copy */
        snprintf(error, size, "resume refused, try again");
        remove(part);
        return false;
    }
    if (rc != HTTP_OK || (status != 200 && status != 206))
    {
        if (rc == HTTP_OK && status >= 300)
        {
            remove(part); /* an error page, not game data */
            http_fail(error, size, "download", status);
        }
        else
            snprintf(error, size, "download: %s",
                     rc == HTTP_ERR_SINK ? "write failed" : http_error_text(rc));
        return false;
    }
    remove(plan->dest);
    if (rename(part, plan->dest) != 0)
    {
        snprintf(error, size, "can't rename the download");
        return false;
    }
    chmod(plan->dest, 0666); /* the emulator is another sandboxed title */
    if (plan->cue)
    {
        char why[128] = "";
        bool ok = unzip_stored(plan->dest, plan->unzip_dir, why, sizeof(why));
        remove(plan->dest);
        if (!ok || !folder_has_cue(plan->unzip_dir))
        {
            snprintf(error, size, "unpacking the CUE/BIN set: %s", why[0] ? why : "no .cue in it");
            return false;
        }
    }
    return true;
}

/* ------------------------------------------------------------ multi-disc games */

typedef struct
{
    int row;
    int disc;
    char key[180]; /* system, then the folder name */
} DiscKey;

static int compare_disc_keys(const void *a, const void *b)
{
    const DiscKey *x = a, *y = b;
    int c = strcmp(x->key, y->key);
    if (c)
        return c;
    return x->disc != y->disc ? x->disc - y->disc : x->row - y->row;
}

/* Folds the discs of one game into a group, as shared/mister_install.py
 * group_discs does: rows of a multi-disc system whose names differ only by
 * a "(Disc N)" tag share the folder named after the rest. Every other tag
 * stays in the key, so a translation or a revision is a game of its own,
 * and a row without a disc tag is never grouped. */
void catalog_group_discs(Catalog *c)
{
    for (int i = 0; i < c->rom_count; ++i)
    {
        RomRow *r = &c->roms[i];
        r->group[0] = 0;
        r->group_head = i;
        r->group_size = 1;
        r->disc_no = 0;
        r->group_bytes = r->size;
    }
    int n = 0;
    for (int i = 0; i < c->rom_count; ++i)
        if (is_multidisc_system(c->roms[i].system))
            n++;
    DiscKey *keys = n ? malloc((size_t)n * sizeof(DiscKey)) : NULL;
    if (!keys)
        return;
    int k = 0;
    for (int i = 0; i < c->rom_count; ++i)
    {
        RomRow *r = &c->roms[i];
        if (!is_multidisc_system(r->system))
            continue;
        /* the file name, not the display name: the server names every disc of
         * a translation after disc 1's database entry, without its [T-En ...]
         * tag ("Grandia (Japan) (Disc 1) (4M)" for the v1.1.1 patch's Disc 2) */
        char stem[200], base[200], safe[160];
        util_copy(stem, sizeof(stem), r->filename);
        util_strip_ext(stem);
        int disc = disc_tag_strip(stem, base, sizeof(base));
        if (!disc)
            continue;
        util_safe_filename(safe, sizeof(safe), base);
        r->disc_no = (uint8_t)(disc > 255 ? 255 : disc);
        keys[k].row = i;
        keys[k].disc = disc;
        snprintf(keys[k].key, sizeof(keys[k].key), "%s\x1f%s", r->system, safe);
        k++;
    }
    if (k > 1)
        qsort(keys, (size_t)k, sizeof(DiscKey), compare_disc_keys);
    for (int a = 0; a < k;)
    {
        int b = a + 1;
        while (b < k && strcmp(keys[b].key, keys[a].key) == 0)
            b++;
        int distinct = 0;
        for (int j = a; j < b; ++j)
            if (j == a || keys[j].disc != keys[j - 1].disc)
                distinct++;
        if (distinct > 1)
        {
            const char *folder = strchr(keys[a].key, '\x1f') + 1;
            int head = keys[a].row;
            int last_disc = -1;
            for (int j = a; j < b; ++j)
            {
                /* two dumps of one disc: the first stays in the game */
                if (keys[j].disc == last_disc)
                    continue;
                last_disc = keys[j].disc;
                RomRow *r = &c->roms[keys[j].row];
                util_copy(r->group, sizeof(r->group), folder);
                r->group_head = head;
            }
            int size = 0;
            uint64_t bytes = 0;
            for (int j = a; j < b; ++j)
                if (c->roms[keys[j].row].group[0])
                {
                    size++;
                    bytes += c->roms[keys[j].row].size;
                }
            for (int j = a; j < b; ++j)
                if (c->roms[keys[j].row].group[0])
                {
                    c->roms[keys[j].row].group_size = (uint8_t)(size > 255 ? 255 : size);
                    c->roms[keys[j].row].group_bytes = bytes;
                }
        }
        a = b;
    }
    free(keys);
}

int catalog_group_rows(const Catalog *catalog, const RomRow *row, int *out, int max)
{
    int n = 0;
    /* matched by system and folder, so a copy of the row (a download's) works too */
    for (int i = 0; i < catalog->rom_count && n < max; ++i)
    {
        const RomRow *r = &catalog->roms[i];
        if (row->group[0] ? util_ieq(r->system, row->system) && strcmp(r->group, row->group) == 0
                          : strcmp(r->rom_id, row->rom_id) == 0)
            out[n++] = i;
    }
    /* disc order */
    for (int i = 1; i < n; ++i)
        for (int j = i; j > 0 && catalog->roms[out[j]].disc_no < catalog->roms[out[j - 1]].disc_no;
             --j)
        {
            int t = out[j];
            out[j] = out[j - 1];
            out[j - 1] = t;
        }
    return n;
}

/* The way a disc is installed (its file, or its CUE/BIN set); false if neither. */
static bool installed_plan(const Config *config, const RomRow *rom, InstallPlan *plan)
{
    catalog_plan(config, rom, plan);
    if (plan->supported && catalog_installed(plan))
        return true;
    if (!rom->has_cue)
        return false;
    catalog_plan_cue(config, rom, plan);
    return plan->supported && catalog_installed(plan);
}

bool catalog_group_installed(const Config *config, const Catalog *catalog, const RomRow *row)
{
    int rows[32];
    int n = catalog_group_rows(catalog, row, rows, 32);
    for (int i = 0; i < n; ++i)
    {
        InstallPlan plan;
        if (!installed_plan(config, &catalog->roms[rows[i]], &plan))
            return false;
    }
    return n > 0;
}

bool catalog_group_finish(const Config *config, const Catalog *catalog, const RomRow *row)
{
    if (!row->group[0])
        return false;
    int rows[32];
    int n = catalog_group_rows(catalog, row, rows, 32);
    char text[32 * 270];
    size_t len = 0;
    char m3u[PATH_LEN] = "";
    for (int i = 0; i < n; ++i)
    {
        InstallPlan plan;
        if (!installed_plan(config, &catalog->roms[rows[i]], &plan))
            return false; /* not every disc yet */
        if (!plan.m3u[0])
            return false; /* this emulator finds the discs itself */
        util_copy(m3u, sizeof(m3u), plan.m3u);
        len += (size_t)snprintf(text + len, sizeof(text) - len, "%s\n", plan.m3u_line);
        if (len >= sizeof(text))
            return false;
    }
    if (!m3u[0] || !util_write_file_atomic(m3u, text, len))
        return false;
    chmod(m3u, 0666);
    app_log("multi-disc: %s lists %d disc(s)", m3u, n);
    return true;
}
