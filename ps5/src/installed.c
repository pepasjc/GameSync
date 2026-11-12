/*
 * GameSync PS5 client - games installed in the PS5 emulators.
 * SPDX-License-Identifier: GPL-3.0-or-later
 */
#include "installed.h"

#include "bundle.h"
#include "melonds.h"

#include <dirent.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <unistd.h>

void installed_free(InstalledList *list)
{
    free(list->items);
    memset(list, 0, sizeof(*list));
}

static InstalledGame *add(InstalledList *l)
{
    if (l->count == l->cap)
    {
        int cap = l->cap ? l->cap * 2 : 256;
        InstalledGame *grown = realloc(l->items, (size_t)cap * sizeof(InstalledGame));
        if (!grown)
            return NULL;
        l->items = grown;
        l->cap = cap;
    }
    InstalledGame *g = &l->items[l->count++];
    memset(g, 0, sizeof(*g));
    return g;
}

static void add_file(InstalledList *l, Emulator emu, const char *system, const char *dir,
                     const char *file, bool own_folder)
{
    InstalledGame *g = add(l);
    if (!g)
        return;
    g->emu = emu;
    util_copy(g->system, sizeof(g->system), system);
    util_copy(g->filename, sizeof(g->filename), file);
    util_copy(g->name, sizeof(g->name), file);
    util_strip_ext(g->name);
    util_path_join(g->path, sizeof(g->path), dir, file);
    g->own_folder = own_folder;
    int64_t size = util_file_size(g->path);
    g->size = size > 0 ? (uint64_t)size : 0;
}

static bool is_dir_entry(const struct dirent *de, const char *path)
{
    return de->d_type == DT_DIR || (de->d_type == DT_UNKNOWN && util_dir_exists(path));
}

static void walk(InstalledList *l, Emulator emu, const char *system, const char *dir, int depth,
                 bool own_folder)
{
    DIR *d = opendir(dir);
    if (!d)
        return;
    struct dirent *de;
    while ((de = readdir(d)) != NULL)
    {
        if (de->d_name[0] == '.')
            continue;
        char path[PATH_LEN];
        util_path_join(path, sizeof(path), dir, de->d_name);
        if (is_dir_entry(de, path))
        {
            if (depth < 3)
                walk(l, emu, system, path, depth + 1, own_folder);
            continue;
        }
        if (is_launchable_rom(de->d_name, system))
            add_file(l, emu, system, dir, de->d_name, own_folder);
    }
    closedir(d);
}

/* GameCube or Wii, from the disc header (or a WIA/RVZ header's disc type). */
static const char *dolphin_system(const char *path)
{
    if (util_has_suffix_i(path, ".wbfs"))
        return "WII";
    FILE *f = fopen(path, "rb");
    if (!f)
        return "GC";
    uint8_t h[0x8020];
    size_t n = fread(h, 1, sizeof(h), f);
    fclose(f);
    const char *sys = "GC";
    if (n >= 0x50 && (memcmp(h, "WIA\x01", 4) == 0 || memcmp(h, "RVZ\x01", 4) == 0))
    {
        /* header 2 starts at 0x48 with its disc type: 1 GameCube, 2 Wii */
        uint32_t type = (uint32_t)h[0x48] << 24 | h[0x49] << 16 | h[0x4A] << 8 | h[0x4B];
        sys = type == 2 ? "WII" : "GC";
    }
    else
    {
        size_t base = memcmp(h, "CISO", 4) == 0 ? 0x8000 : 0;
        if (n >= base + 0x20 && h[base + 0x18] == 0x5D && h[base + 0x19] == 0x1C &&
            h[base + 0x1A] == 0x9E && h[base + 0x1B] == 0xA3)
            sys = "WII";
    }
    return sys;
}

static void walk_dolphin(InstalledList *l, const char *dir, int depth)
{
    static const char *const exts[] = {".iso", ".gcm", ".rvz", ".ciso", ".gcz", ".wbfs", ".wia"};
    DIR *d = opendir(dir);
    if (!d)
        return;
    struct dirent *de;
    while ((de = readdir(d)) != NULL)
    {
        if (de->d_name[0] == '.')
            continue;
        char path[PATH_LEN];
        util_path_join(path, sizeof(path), dir, de->d_name);
        if (is_dir_entry(de, path))
        {
            if (depth < 4)
                walk_dolphin(l, path, depth + 1);
            continue;
        }
        bool ok = false;
        for (size_t i = 0; i < sizeof(exts) / sizeof(exts[0]) && !ok; ++i)
            ok = util_has_suffix_i(de->d_name, exts[i]);
        if (ok)
            add_file(l, EMU_PORPOISE, dolphin_system(path), dir, de->d_name, false);
    }
    closedir(d);
}

static void scan_eden_roms(InstalledList *l, const char *dir)
{
    DIR *d = opendir(dir);
    if (!d)
        return;
    struct dirent *de;
    while ((de = readdir(d)) != NULL)
        if (util_has_suffix_i(de->d_name, ".nsp") || util_has_suffix_i(de->d_name, ".xci"))
            add_file(l, EMU_EDEN, "NSW", dir, de->d_name, false);
    closedir(d);
}

/* a flat game folder: files with one of exts */
static void scan_flat(InstalledList *l, Emulator emu, const char *system, const char *dir,
                      const char *const *exts, size_t n)
{
    DIR *d = opendir(dir);
    if (!d)
        return;
    struct dirent *de;
    while ((de = readdir(d)) != NULL)
    {
        if (de->d_name[0] == '.')
            continue;
        for (size_t i = 0; i < n; ++i)
            if (util_has_suffix_i(de->d_name, exts[i]))
            {
                add_file(l, emu, system, dir, de->d_name, false);
                break;
            }
    }
    closedir(d);
}

static int compare(const void *a, const void *b)
{
    const InstalledGame *x = a, *y = b;
    int c = strcasecmp(system_display_name(x->system), system_display_name(y->system));
    return c ? c : strcasecmp(x->name, y->name);
}

void installed_scan(const Config *config, InstalledList *list)
{
    installed_free(list);
    char dir[PATH_LEN];
    util_path_join(dir, sizeof(dir), config->retroarch_dir, "content");
    DIR *d = opendir(dir);
    if (d)
    {
        struct dirent *de;
        while ((de = readdir(d)) != NULL)
        {
            const char *system = de->d_name[0] != '.' ? system_from_folder(de->d_name) : NULL;
            if (!system)
                continue;
            char path[PATH_LEN];
            util_path_join(path, sizeof(path), dir, de->d_name);
            walk(list, EMU_RETROARCH, system, path, 1, false);
        }
        closedir(d);
    }
    /* SwanStation: one folder per game; a .cue loads its tracks, an .m3u its discs */
    util_path_join(dir, sizeof(dir), config->swanstation_dir, "games");
    walk(list, EMU_SWANSTATION, "PS1", dir, 1, true);
    util_path_join(dir, sizeof(dir), config->ps5sx2_dir, "games");
    walk(list, EMU_PS5SX2, "PS2", dir, 2, false); /* and one level of sub-folders, as PS5SX2 */
    util_path_join(dir, sizeof(dir), config->porpoise_dir, "games");
    walk_dolphin(list, dir, 1);
    util_path_join(dir, sizeof(dir), config->eden_dir, "roms");
    scan_eden_roms(list, dir);
    static const char *const wiiu[] = {".wua", ".wud", ".wux", ".rpx", ".wuhb"};
    static const char *const ds[] = {".3ds", ".cci", ".cxi", ".3dsx", ".app",
                                     ".z3ds", ".zcci", ".zcxi", ".z3dsx"};
    static const char *const xbox[] = {".iso", ".xiso"};
    util_path_join(dir, sizeof(dir), config->cemu_dir, "games");
    scan_flat(list, EMU_CEMU, "WIIU", dir, wiiu, 5);
    util_path_join(dir, sizeof(dir), config->cemu_dir, "azahar/games");
    scan_flat(list, EMU_CEMU, "3DS", dir, ds, 9);
    static const char *const nds[] = {".nds", ".srl", ".dsi"};
    melonds_games_dir(config, dir, sizeof(dir));
    scan_flat(list, EMU_CEMU, "NDS", dir, nds, 3);
    util_path_join(dir, sizeof(dir), config->xemu_dir, "games");
    scan_flat(list, EMU_XEMU, "XBOX", dir, xbox, 2);
    if (list->count > 1)
        qsort(list->items, (size_t)list->count, sizeof(InstalledGame), compare);
}

/* Bytes under dir (one level of sub-folders: RetroArch's .discs/). */
static uint64_t folder_bytes(const char *dir, int depth)
{
    DIR *d = opendir(dir);
    if (!d)
        return 0;
    uint64_t total = 0;
    struct dirent *de;
    while ((de = readdir(d)) != NULL)
    {
        if (strcmp(de->d_name, ".") == 0 || strcmp(de->d_name, "..") == 0)
            continue;
        char path[PATH_LEN];
        util_path_join(path, sizeof(path), dir, de->d_name);
        if (is_dir_entry(de, path))
        {
            if (depth < 1)
                total += folder_bytes(path, depth + 1);
            continue;
        }
        int64_t size = util_file_size(path);
        if (size > 0)
            total += (uint64_t)size;
    }
    closedir(d);
    return total;
}

void installed_group_discs(InstalledList *l)
{
    int w = 0;
    for (int i = 0; i < l->count; ++i)
    {
        InstalledGame g = l->items[i];
        if (!is_multidisc_system(g.system))
        {
            l->items[w++] = g;
            continue;
        }
        char dir[PATH_LEN], base[160];
        util_copy(dir, sizeof(dir), g.path);
        char *slash = strrchr(dir, '/');
        if (slash)
            *slash = 0;
        bool m3u = util_has_suffix_i(g.filename, ".m3u");
        if (!disc_tag_strip(g.name, base, sizeof(base)) && !m3u)
        {
            l->items[w++] = g;
            continue;
        }
        /* the game's folder: <...>/<base>/ holds its discs (and .m3u) */
        bool own = strcmp(util_basename(dir), base) == 0;
        int into = -1;
        for (int k = 0; k < w && into < 0; ++k)
        {
            InstalledGame *o = &l->items[k];
            if ((o->discs || o->folder) && o->emu == g.emu && strcmp(o->name, base) == 0 &&
                strncmp(o->path, dir, strlen(dir)) == 0)
                into = k;
        }
        if (into >= 0)
        {
            InstalledGame *o = &l->items[into];
            if (!m3u)
            {
                o->discs++;
                if (!o->folder) /* a folder's size counts every disc already */
                    o->size += g.size;
            }
            continue;
        }
        util_copy(g.name, sizeof(g.name), base);
        g.discs = m3u ? 0 : 1;
        if (own)
        {
            /* the whole folder is the game: deleting it deletes every disc
             * (RetroArch's are out of sight in .discs/) */
            g.folder = true;
            util_copy(g.path, sizeof(g.path), dir);
            g.size = folder_bytes(dir, 0);
        }
        l->items[w++] = g;
    }
    l->count = w;
}

bool installed_delete(const InstalledGame *game, char *error, size_t size)
{
    if (game->folder && strchr(game->path + 1, '/'))
    {
        /* a multi-disc game: its folder, every disc in it */
        if (remove_tree(game->path))
            return true;
        snprintf(error, size, "can't delete %s", game->name);
        return false;
    }
    char folder[PATH_LEN];
    util_copy(folder, sizeof(folder), game->path);
    char *slash = strrchr(folder, '/');
    if (slash)
        *slash = 0;
    if (game->own_folder && strstr(folder, "/games/"))
    {
        /* the game's own folder under games/: everything in it goes */
        DIR *d = opendir(folder);
        if (d)
        {
            struct dirent *de;
            while ((de = readdir(d)) != NULL)
            {
                if (strcmp(de->d_name, ".") == 0 || strcmp(de->d_name, "..") == 0)
                    continue;
                char path[PATH_LEN];
                util_path_join(path, sizeof(path), folder, de->d_name);
                remove(path);
            }
            closedir(d);
        }
        if (rmdir(folder) == 0)
            return true;
    }
    if (remove(game->path) != 0 && util_file_exists(game->path))
    {
        snprintf(error, size, "can't delete %s", game->filename);
        return false;
    }
    return true;
}
