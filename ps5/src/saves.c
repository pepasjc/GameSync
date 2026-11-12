/*
 * GameSync PS5 client - the emulator saves on this console.
 * SPDX-License-Identifier: GPL-3.0-or-later
 *
 * Layouts (as each PS5 port ships them):
 *  - RetroArch (PPSA99169): see retroarch.c.
 *  - SwanStationPS5 (PPSA98510): one card per game, <dir>/saves/<serial>_1.mcd
 *    with the serial as the disc spells it (SLUS-01041).
 *  - PS5SX2 (PPSA99203): PCSX2 cards in <dir>/memcards/ (or <dir>): a card per game
 *    (<game file>.ps2, set in the game's settings/<game file>.ini), or every
 *    game on one shared 8 MB card (Mcd001.ps2 unless the user picked another).
 *  - PSP (RetroArch's PPSSPP core): PSP/SAVEDATA/<slot>/ on its memory stick
 *    (retroarch_psp_savedata), each slot folder a save keyed by its name
 *    (ULUS10551DATA00) as the PSP client files it, synced as a 3DSS bundle.
 */
#include "saves.h"

#include "bundle.h"
#include "xbox_hdd.h"
#include "melonds.h"
#include "retroarch.h"
#include "json.h"
#include "ps2mc.h"

#include <ctype.h>
#include <dirent.h>
#include <sys/stat.h>
#include <time.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

void saves_free(SaveList *list)
{
    free(list->items);
    memset(list, 0, sizeof(*list));
}

SaveEntry *saves_add(SaveList *list)
{
    if (list->count == list->cap)
    {
        int cap = list->cap ? list->cap * 2 : 128;
        SaveEntry *items = realloc(list->items, (size_t)cap * sizeof(SaveEntry));
        if (!items)
            return NULL;
        list->items = items;
        list->cap = cap;
    }
    SaveEntry *e = &list->items[list->count++];
    memset(e, 0, sizeof(*e));
    return e;
}

SaveEntry *saves_find(SaveList *list, const char *title_id)
{
    if (!title_id[0])
        return NULL;
    for (int i = 0; i < list->count; ++i)
        if (strcmp(list->items[i].title_id, title_id) == 0)
            return &list->items[i];
    return NULL;
}

bool saves_xbox_tree(const SaveEntry *e, SaveTree *tree, char *err, size_t err_size)
{
    memset(tree, 0, sizeof(*tree));
    XboxHdd *hdd = xhdd_open(e->path, err, err_size);
    if (!hdd)
        return false;
    bool ok = xhdd_read_title(hdd, e->title_id, XBOX_SAVE_MAX, tree, err, err_size);
    xhdd_close(hdd);
    return ok;
}

void saves_hash_local(SaveEntry *e)
{
    e->local = false;
    e->local_hash[0] = 0;
    e->local_size = 0;
    if (e->kind == SAVE_XBOX)
    {
        SaveTree tree;
        char err[96];
        if (!saves_xbox_tree(e, &tree, err, sizeof(err)))
        {
            app_log("XPSemu: %s skipped: %s", e->title_id, err);
            return;
        }
        if (tree.count)
        {
            tree_hash(&tree, e->local_hash, &e->local_size);
            e->local = true;
        }
        tree_free(&tree);
        return;
    }
    if (e->kind == SAVE_FOLDER)
    {
        SaveTree tree;
        if (!tree_read(e->path, "nocopy", &tree))
            return;
        if (tree.count)
        {
            tree_hash(&tree, e->local_hash, &e->local_size);
            e->local = true;
        }
        tree_free(&tree);
        return;
    }
    if (e->kind == SAVE_GCI && e->path_is_dir)
        return;
    if (e->kind != SAVE_PS2_FOLDER)
    {
        int64_t size = util_file_size(e->path);
        if (size < 0 || !util_sha256_file(e->path, e->local_hash))
            return;
        e->local = true;
        e->local_size = (uint64_t)size;
        return;
    }
    Ps2CardFile card;
    char error[96];
    if (!ps2mc_load_file(e->path, &card, error, sizeof(error)))
        return;
    int i = ps2mc_find_dir(&card.card, e->ps2_dir);
    if (i >= 0)
    {
        size_t size;
        uint8_t *p2fd = ps2mc_build_p2fd(&card.card.dirs[i], &size);
        if (p2fd)
        {
            util_sha256_hex(p2fd, size, e->local_hash);
            e->local_size = size;
            e->local = true;
            free(p2fd);
        }
    }
    ps2mc_free(&card.card);
}

/* ------------------------------------------------------------ SwanStation */

/* SwanStationPS5 keeps each profile's cards in its own folder: the main
 * profile is the data root, others are <root>/profiles/<name>/ (name made
 * safe: letters, digits, space, - and _), and <root>/profile.txt holds the
 * current one's name (src/profiles.c). The card type is the core option
 * swanstation_MemoryCards_Card1Type in that profile's SwanStationPS5.ini
 * (psxs5.ini in builds before the rename), overridable per game in
 * game-settings/<serial>.ini (src/config.c, src/main.c):
 *   Libretro (default)  the app writes the core's card as <serial>_1.mcd, or
 *                       <game file>_1.mcd when it can't read a serial (a .chd,
 *                       unless a serial.txt sits beside it)
 *   PerGame             the core names it <serial>_1.mcd itself
 *   PerGameTitle        the core names it <game title>_1.mcd
 *   Shared / None       no card of the game's own: nothing to sync */
static void ss_user_dir(const Config *config, char *out, size_t size)
{
    util_copy(out, size, config->swanstation_dir);
    char path[PATH_LEN];
    snprintf(path, sizeof(path), "%s/profile.txt", config->swanstation_dir);
    char *text = (char *)util_read_file(path, NULL);
    if (!text)
        return;
    text[strcspn(text, "\r\n")] = 0;
    if (text[0])
    {
        char folder[128];
        size_t w = 0;
        for (const unsigned char *p = (const unsigned char *)text; *p && w + 1 < sizeof(folder); ++p)
            folder[w++] = (isalnum(*p) || *p == ' ' || *p == '-' || *p == '_') ? (char)*p : '_';
        folder[w] = 0;
        char dir[PATH_LEN];
        snprintf(dir, sizeof(dir), "%s/profiles/%s", config->swanstation_dir, folder);
        if (util_dir_exists(dir))
            util_copy(out, size, dir);
    }
    free(text);
}

static SsCardMode mode_in(const char *path, SsCardMode fallback)
{
    char *text = (char *)util_read_file(path, NULL);
    if (!text)
        return fallback;
    SsCardMode mode = fallback;
    const char *key = "swanstation_MemoryCards_Card1Type=";
    const char *p = strstr(text, key);
    if (p)
    {
        p += strlen(key);
        if (strncmp(p, "PerGameTitle", 12) == 0)
            mode = SS_CARD_TITLE;
        else if (strncmp(p, "PerGame", 7) == 0)
            mode = SS_CARD_SERIAL;
        else if (strncmp(p, "Shared", 6) == 0)
            mode = SS_CARD_SHARED;
        else if (strncmp(p, "None", 4) == 0)
            mode = SS_CARD_NONE;
        else
            mode = SS_CARD_LIBRETRO;
    }
    free(text);
    return mode;
}

void saves_swanstation_info(const Config *config, SsInfo *info)
{
    memset(info, 0, sizeof(*info));
    ss_user_dir(config, info->user_dir, sizeof(info->user_dir));
    util_path_join(info->saves_dir, sizeof(info->saves_dir), info->user_dir, "saves");
    char path[PATH_LEN];
    util_path_join(path, sizeof(path), info->user_dir, "SwanStationPS5.ini");
    info->mode = SS_CARD_LIBRETRO;
    if (util_file_exists(path))
        info->mode = mode_in(path, SS_CARD_LIBRETRO);
    else
    {
        util_path_join(path, sizeof(path), info->user_dir, "psxs5.ini");
        info->mode = mode_in(path, SS_CARD_LIBRETRO);
    }
}

SsCardMode saves_swanstation_mode_for(const SsInfo *info, const char *serial)
{
    if (!is_ps_serial(serial))
        return info->mode;
    char path[PATH_LEN];
    snprintf(path, sizeof(path), "%s/game-settings/%.4s-%s.ini", info->user_dir, serial, serial + 4);
    return mode_in(path, info->mode);
}

const char *saves_swanstation_mode_name(SsCardMode mode)
{
    switch (mode)
    {
    case SS_CARD_SERIAL: return "Separate Card Per Game (Game Code)";
    case SS_CARD_TITLE: return "Separate Card Per Game (Game Title)";
    case SS_CARD_SHARED: return "Shared Between All Games";
    case SS_CARD_NONE: return "No Memory Card";
    default: return "Libretro";
    }
}

bool saves_swanstation_rename_card(const Config *config, const char *from_stem, const char *to_stem)
{
    SsInfo info;
    saves_swanstation_info(config, &info);
    char from[PATH_LEN], to[PATH_LEN], name[220];
    snprintf(name, sizeof(name), "%.200s_1.mcd", from_stem);
    util_path_join(from, sizeof(from), info.saves_dir, name);
    snprintf(name, sizeof(name), "%.200s_1.mcd", to_stem);
    util_path_join(to, sizeof(to), info.saves_dir, name);
    if (strcmp(from, to) == 0 || !util_file_exists(from))
        return false;
    if (!util_file_exists(to))
    {
        if (rename(from, to) != 0)
            return false;
        chmod(to, 0666);
        app_log("SwanStation: %s is now %s (the game's playlist names its card)", from, to);
        return true;
    }
    char a[65], b[65];
    if (util_sha256_file(from, a) && util_sha256_file(to, b) && strcmp(a, b) == 0)
    {
        char bak[PATH_LEN + 8];
        snprintf(bak, sizeof(bak), "%s.bak", from);
        remove(bak);
        if (rename(from, bak) == 0)
            app_log("SwanStation: %s set aside as .bak (a copy of %s)", from, to);
        return true;
    }
    app_log("SwanStation: %s and %s hold different saves; left both", from, to);
    return false;
}

void saves_swanstation_card_path(const Config *config, const char *serial, const char *game_stem,
                                 char *out, size_t size)
{
    SsInfo info;
    saves_swanstation_info(config, &info);
    char name[200];
    SsCardMode mode = saves_swanstation_mode_for(&info, serial);
    if (mode == SS_CARD_TITLE && game_stem && game_stem[0])
        snprintf(name, sizeof(name), "%.180s_1.mcd", game_stem);
    else
        swanstation_card_name(serial, name, sizeof(name));
    util_path_join(out, size, info.saves_dir, name);
}

/* Serial-named and title-named cards are both listed; when one game has
 * both, the kind the current mode reads wins (the other is a leftover). A
 * title-named card gets its id from the server's resolver, by name. */
static void scan_swanstation(const Config *config, SaveList *list)
{
    SsInfo info;
    saves_swanstation_info(config, &info);
    if (info.mode == SS_CARD_SHARED || info.mode == SS_CARD_NONE)
        snprintf(list->warning, sizeof(list->warning),
                 "SwanStation's Memory Card 1 Type is \"%s\": no per-game card to sync (use Libretro "
                 "or Separate Card Per Game)",
                 saves_swanstation_mode_name(info.mode));
    bool titles_first = info.mode == SS_CARD_TITLE;
    for (int pass = 0; pass < 2; ++pass)
    {
        DIR *d = opendir(info.saves_dir);
        if (!d)
            return;
        struct dirent *de;
        while ((de = readdir(d)) != NULL)
        {
            if (!util_has_suffix_i(de->d_name, "_1.mcd"))
                continue;
            char serial[16] = "";
            bool by_serial = swanstation_card_serial(de->d_name, serial, sizeof(serial));
            if (by_serial != (pass == (titles_first ? 1 : 0)))
                continue;
            if (by_serial && saves_find(list, serial))
                continue; /* the same card under two spellings: first one wins */
            SaveEntry *e = saves_add(list);
            if (!e)
                break;
            e->emu = EMU_SWANSTATION;
            e->kind = SAVE_PS1_CARD;
            util_copy(e->system, sizeof(e->system), "PS1");
            util_path_join(e->path, sizeof(e->path), info.saves_dir, de->d_name);
            if (by_serial)
            {
                util_copy(e->title_id, sizeof(e->title_id), serial);
                util_copy(e->name, sizeof(e->name), serial);
            }
            else
            {
                /* "<game>_1.mcd": named like the game, resolved by name */
                util_copy(e->name, sizeof(e->name), de->d_name);
                e->name[strlen(e->name) - 6] = 0;
                util_copy(e->rom_file, sizeof(e->rom_file), e->name);
            }
            saves_hash_local(e);
        }
        closedir(d);
    }
}

/* A save state holds the memory card as it was: loading one copies that card
 * into the running one (SwanStation core, Pad::DoStateMemcard ->
 * MemoryCard::DoState), so after a download the game would see the old card
 * and its next save would write it back over the new one. SwanStationPS5's
 * quick resume loads <user>/states/<game id>.resume on every start; the game
 * id is the serial ("SLUS-01324") when known, else the title with every
 * non-alphanumeric character as '_', at most 63 chars (src/library.c, Game.id;
 * src/play.c play_resume_path). Those are set aside as .bak; numbered slots
 * are the player's own and stay. */
int saves_swanstation_drop_resume(const Config *config, const char *card_path, const char *serial)
{
    SsInfo info;
    saves_swanstation_info(config, &info);
    char ids[2][200];
    int n = 0;
    if (is_ps_serial(serial))
        snprintf(ids[n++], sizeof(ids[0]), "%.4s-%s", serial, serial + 4);
    /* "<title>_1.mcd" -> the title, the way the library makes an id of it */
    char stem[200];
    util_copy(stem, sizeof(stem), util_basename(card_path));
    size_t len = strlen(stem);
    if (len > 6 && util_has_suffix_i(stem, "_1.mcd"))
    {
        stem[len - 6] = 0;
        char compact[32];
        util_compact_serial(compact, sizeof(compact), stem);
        if (!is_ps_serial(compact))
        {
            for (char *p = stem; *p; ++p)
                if (!isalnum((unsigned char)*p))
                    *p = '_';
            stem[63] = 0; /* Game.id is char[64] */
            util_copy(ids[n++], sizeof(ids[0]), stem);
        }
    }
    int moved = 0;
    for (int i = 0; i < n; ++i)
    {
        static const char *const exts[] = {".resume", ".resume.thumb"};
        for (int k = 0; k < 2; ++k)
        {
            char path[PATH_LEN], bak[PATH_LEN + 8];
            snprintf(path, sizeof(path), "%s/states/%s%s", info.user_dir, ids[i], exts[k]);
            if (!util_file_exists(path))
                continue;
            snprintf(bak, sizeof(bak), "%s.bak", path);
            remove(bak);
            if (rename(path, bak) == 0 && k == 0)
            {
                moved++;
                app_log("SwanStation: quick-resume state %s set aside (it holds the old card)", path);
            }
        }
    }
    return moved;
}

/* Puts a game's card where the current mode makes SwanStation look for it.
 * The two names a card can have are <SERIAL>_1.mcd and <game file>_1.mcd;
 * which one SwanStation reads depends on the mode (and, in Libretro mode,
 * on the serial.txt this also writes). A card under the other name is:
 *  - renamed, when the right name is free;
 *  - set aside as .bak, when it is a copy (same bytes as the card in use, or
 *    as the server's save - a stray download);
 *  - left alone, with SS_FIX_CONFLICT, when it differs from both. */
SsFixResult saves_swanstation_fix(const Config *config, const char *game_path, const char *serial,
                                  const char *server_hash)
{
    if (!is_ps_serial(serial))
        return SS_FIX_NONE;
    SsInfo info;
    saves_swanstation_info(config, &info);
    SsCardMode mode = saves_swanstation_mode_for(&info, serial);
    if (mode == SS_CARD_SHARED || mode == SS_CARD_NONE)
        return SS_FIX_NONE;
    char folder[PATH_LEN];
    util_copy(folder, sizeof(folder), game_path);
    char *slash = strrchr(folder, '/');
    if (!slash)
        return SS_FIX_NONE;
    *slash = 0;
    char games[PATH_LEN];
    snprintf(games, sizeof(games), "%s/games/", config->swanstation_dir);
    /* SwanStationPS5 also lists the games left in the pre-rename /data/PSXS5 */
    bool own_folder = strncmp(folder, games, strlen(games)) == 0 ||
                      strncmp(folder, "/data/PSXS5/games/", 18) == 0;

    SsFixResult result = SS_FIX_NONE;
    /* Libretro: the app names the card after the serial once it knows it */
    if (mode == SS_CARD_LIBRETRO && own_folder)
    {
        char txt[PATH_LEN + 16];
        snprintf(txt, sizeof(txt), "%s/serial.txt", folder);
        if (!util_file_exists(txt))
        {
            char line[16];
            snprintf(line, sizeof(line), "%.4s-%s\n", serial, serial + 4);
            if (util_write_file_atomic(txt, line, strlen(line)))
                result = SS_FIX_CHANGED;
        }
    }

    char stem[200], name[64], serial_card[PATH_LEN], title_card[PATH_LEN];
    util_copy(stem, sizeof(stem), util_basename(game_path));
    util_strip_ext(stem);
    snprintf(title_card, sizeof(title_card), "%s/%s_1.mcd", info.saves_dir, stem);
    swanstation_card_name(serial, name, sizeof(name));
    snprintf(serial_card, sizeof(serial_card), "%s/%s", info.saves_dir, name);
    if (strcmp(title_card, serial_card) == 0)
        return result;
    bool by_title = mode == SS_CARD_TITLE;
    const char *right = by_title ? title_card : serial_card;
    const char *wrong = by_title ? serial_card : title_card;
    if (!util_file_exists(wrong))
        return result;
    if (!util_file_exists(right))
    {
        if (rename(wrong, right) != 0)
            return result;
        chmod(right, 0666);
        app_log("SwanStation (%s): card %s is now %s", saves_swanstation_mode_name(mode), wrong,
                right);
        return SS_FIX_CHANGED;
    }
    char hw[65], hr[65];
    if (!util_sha256_file(wrong, hw) || !util_sha256_file(right, hr))
        return result;
    bool copy = strcmp(hw, hr) == 0 || (server_hash && server_hash[0] && strcmp(hw, server_hash) == 0);
    if (!copy)
        return server_hash ? SS_FIX_CONFLICT : SS_FIX_NEED_SERVER;
    char bak[PATH_LEN + 8];
    snprintf(bak, sizeof(bak), "%s.bak", wrong);
    remove(bak);
    if (rename(wrong, bak) != 0)
        return result;
    app_log("SwanStation (%s): %s is a copy, set aside as .bak (in use: %s)",
            saves_swanstation_mode_name(mode), wrong, right);
    return SS_FIX_CHANGED;
}

/* ------------------------------------------------------------ PS5SX2 */

static void scan_ps2_card(const char *path, SaveList *list, int *duplicates)
{
    Ps2CardFile card;
    char error[96];
    if (!ps2mc_load_file(path, &card, error, sizeof(error)))
    {
        /* usually PS5SX2's blank Mcd002.ps2 (made, never formatted): no saves
         * on it, so nothing to show - just a line in the log */
        app_log("PS5SX2: %s skipped: %s", util_basename(path), error);
        return;
    }
    list->ps2_cards++;
    for (int i = 0; i < card.card.count; ++i)
    {
        const Ps2Dir *dir = &card.card.dirs[i];
        char serial[16];
        if (!ps2mc_serial_from_dirname(dir->name, serial, sizeof(serial)))
            continue;
        if (saves_find(list, serial))
        {
            /* A game with two folders (or on two cards): the server keeps one
             * folder per serial, so only the first is synced. */
            (*duplicates)++;
            continue;
        }
        SaveEntry *e = saves_add(list);
        if (!e)
            break;
        e->emu = EMU_PS5SX2;
        e->kind = SAVE_PS2_FOLDER;
        util_copy(e->system, sizeof(e->system), "PS2");
        util_copy(e->title_id, sizeof(e->title_id), serial);
        util_copy(e->name, sizeof(e->name), dir->name);
        util_copy(e->ps2_dir, sizeof(e->ps2_dir), dir->name);
        util_copy(e->path, sizeof(e->path), path);
        size_t size;
        uint8_t *p2fd = ps2mc_build_p2fd(dir, &size);
        if (p2fd)
        {
            util_sha256_hex(p2fd, size, e->local_hash);
            e->local_size = size;
            e->local = true;
            free(p2fd);
        }
    }
    ps2mc_free(&card.card);
}

/* PS5SX2's card folder: memcards/ when it exists, else the PCSX2 folder
 * itself (ps5/coreorbis/orbis-shims/orbis_paths.cpp, OrbisDir). Where no
 * card is yet, memcards/ is made (create), the folder PS5SX2 then uses. */
void saves_ps5sx2_card_dir(const Config *config, bool create, char *out, size_t size)
{
    util_path_join(out, size, config->ps5sx2_dir, "memcards");
    if (util_dir_exists(out))
        return;
    DIR *d = opendir(config->ps5sx2_dir);
    if (d)
    {
        struct dirent *de;
        bool cards = false;
        while (!cards && (de = readdir(d)) != NULL)
            cards = util_has_suffix_i(de->d_name, ".ps2") || util_has_suffix_i(de->d_name, ".mc2");
        closedir(d);
        if (cards)
        {
            util_copy(out, size, config->ps5sx2_dir);
            return;
        }
    }
    if (create)
        util_mkdirs(out);
}

/* The cards every game shares: PCSX2's two default slots and the configured one. */
static bool is_shared_card(const Config *config, const char *name)
{
    return util_ieq(name, "Mcd001.ps2") || util_ieq(name, "Mcd002.ps2") ||
           util_ieq(name, config->ps2_card);
}

static void scan_card_dir(const Config *config, const char *dir, bool shared, SaveList *list,
                          int *duplicates)
{
    DIR *d = opendir(dir);
    if (!d)
        return;
    struct dirent *de;
    while ((de = readdir(d)) != NULL)
    {
        if (!util_has_suffix_i(de->d_name, ".ps2") && !util_has_suffix_i(de->d_name, ".mc2"))
            continue;
        if (is_shared_card(config, de->d_name) != shared || strcmp(de->d_name, config->ps2_card) == 0)
            continue;
        char path[PATH_LEN];
        util_path_join(path, sizeof(path), dir, de->d_name);
        int64_t size = util_file_size(path);
        if (size == PS2MC_PS2_SIZE || size == PS2MC_MC2_SIZE)
            scan_ps2_card(path, list, duplicates);
    }
    closedir(d);
}

static void scan_ps5sx2(const Config *config, SaveList *list)
{
    char dir[PATH_LEN];
    saves_ps5sx2_card_dir(config, false, dir, sizeof(dir));
    int duplicates = 0;
    /* Per game: each game's own card first, so it wins over a copy of the
     * same save left on a shared card (expected after the move, not counted). */
    if (config->ps2_per_game)
        scan_card_dir(config, dir, false, list, &duplicates);
    int own_duplicates = duplicates;
    /* the configured card next, so a game on two shared cards binds to it */
    char preferred[PATH_LEN];
    util_path_join(preferred, sizeof(preferred), dir, config->ps2_card);
    int64_t size = util_file_size(preferred);
    if (size == PS2MC_PS2_SIZE || size == PS2MC_MC2_SIZE)
        scan_ps2_card(preferred, list, &duplicates);
    scan_card_dir(config, dir, true, list, &duplicates);
    if (!config->ps2_per_game)
        scan_card_dir(config, dir, false, list, &duplicates);
    else
        duplicates = own_duplicates;
    if (duplicates && !list->warning[0])
        snprintf(list->warning, sizeof(list->warning),
                 "%d extra PS2 save folder(s) share a game's serial and aren't synced",
                 duplicates);
}

/* PS5SX2 per-game cards. When a game starts, PS5SX2 applies
 * settings/<game file>.ini over gs.ini (ps5/coreorbis/main-boot.cpp,
 * orbis_apply_ini_file): "Section/Key=value" lines, the last one winning,
 * <game file> being the image's name without its extension. A game's card
 * is the one its MemoryCards/Slot1_Filename names in memcards/. */
static void ini_value(const char *text, const char *key, char *out, size_t size)
{
    out[0] = 0;
    size_t key_len = strlen(key);
    for (const char *line = text; line && *line;)
    {
        const char *end = strchr(line, '\n');
        size_t len = end ? (size_t)(end - line) : strlen(line);
        while (len && (*line == ' ' || *line == '\t'))
            line++, len--;
        if (len > key_len && strncmp(line, key, key_len) == 0)
        {
            const char *p = line + key_len;
            const char *stop = line + len;
            while (p < stop && (*p == ' ' || *p == '\t'))
                p++;
            if (p < stop && *p == '=')
            {
                p++;
                while (p < stop && (*p == ' ' || *p == '\t'))
                    p++;
                while (stop > p && (stop[-1] == ' ' || stop[-1] == '\t' || stop[-1] == '\r'))
                    stop--;
                size_t n = (size_t)(stop - p);
                if (n >= size)
                    n = size - 1;
                memcpy(out, p, n);
                out[n] = 0;
            }
        }
        line = end ? end + 1 : NULL;
    }
}

/* Replaces every key= line of text (or appends one): a malloc'd text. */
static char *ini_set(const char *text, const char *key, const char *value)
{
    size_t key_len = strlen(key);
    size_t cap = strlen(text) + key_len + strlen(value) + 4;
    char *out = malloc(cap);
    if (!out)
        return NULL;
    size_t n = 0;
    bool done = false;
    for (const char *line = text; *line;)
    {
        const char *end = strchr(line, '\n');
        size_t len = end ? (size_t)(end - line + 1) : strlen(line);
        const char *k = line;
        while (*k == ' ' || *k == '\t')
            k++;
        const char *after = k + key_len;
        bool match = strncmp(k, key, key_len) == 0 && after < line + len;
        while (match && after < line + len && (*after == ' ' || *after == '\t'))
            after++;
        if (match && after < line + len && *after == '=')
        {
            if (!done)
                n += (size_t)sprintf(out + n, "%s=%s\n", key, value);
            done = true;
        }
        else
        {
            memcpy(out + n, line, len);
            n += len;
            if (!end)
                out[n++] = '\n';
        }
        line += len;
    }
    if (!done)
        n += (size_t)sprintf(out + n, "%s=%s\n", key, value);
    out[n] = 0;
    return out;
}

/* PS5SX2's serial cache: <cache>/chd-serials.txt, OrbisDir("cache") being
 * cache/ when it exists, else the PCSX2 folder (ps5/frontend/fe_games.cpp,
 * MakeSerialKey). One line per image: path TAB size TAB mtime TAB serial.
 * The shelf reads a serial once and keeps it there, CHDs included, which the
 * catalog can't always name (its id is then a name slug). Calls found() with
 * each image path and its compact serial ("SLUS20488") until it returns true. */
static bool ps5sx2_serials(const Config *config,
                           bool (*found)(const char *path, const char *serial, void *user),
                           void *user)
{
    char file[PATH_LEN];
    snprintf(file, sizeof(file), "%s/cache/chd-serials.txt", config->ps5sx2_dir);
    char *text = (char *)util_read_file(file, NULL);
    if (!text)
    {
        snprintf(file, sizeof(file), "%s/chd-serials.txt", config->ps5sx2_dir);
        text = (char *)util_read_file(file, NULL);
    }
    if (!text)
        return false;
    bool done = false;
    char *save = NULL;
    for (char *line = strtok_r(text, "\r\n", &save); line && !done;
         line = strtok_r(NULL, "\r\n", &save))
    {
        char *fields[4];
        int n = 0;
        for (char *p = line; n < 4; ++n)
        {
            fields[n] = p;
            char *tab = strchr(p, '\t');
            if (!tab)
            {
                ++n;
                break;
            }
            *tab = 0;
            p = tab + 1;
        }
        if (n < 4 || !fields[3][0])
            continue;
        char serial[32];
        util_compact_serial(serial, sizeof(serial), fields[3]);
        if (serial[0])
            done = found(fields[0], serial, user);
    }
    free(text);
    return done;
}

typedef struct
{
    const char *key;  /* the image path, or the serial */
    char *out;
    size_t size;
} SerialLookup;

static bool serial_of_path(const char *path, const char *serial, void *user)
{
    SerialLookup *l = user;
    if (strcmp(path, l->key) != 0)
        return false;
    util_copy(l->out, l->size, serial);
    return true;
}

static bool path_of_serial(const char *path, const char *serial, void *user)
{
    SerialLookup *l = user;
    if (strcmp(serial, l->key) != 0 || !util_file_exists(path))
        return false;
    util_copy(l->out, l->size, path);
    return true;
}

bool saves_ps5sx2_serial(const Config *config, const char *game_path, char *out, size_t size)
{
    SerialLookup l = {game_path, out, size};
    return ps5sx2_serials(config, serial_of_path, &l);
}

bool saves_ps5sx2_game_for_serial(const Config *config, const char *serial, char *stem,
                                  size_t size)
{
    char compact[32], path[PATH_LEN];
    util_compact_serial(compact, sizeof(compact), serial);
    SerialLookup l = {compact, path, sizeof(path)};
    if (!compact[0] || !ps5sx2_serials(config, path_of_serial, &l))
        return false;
    util_copy(stem, size, util_basename(path));
    util_strip_ext(stem);
    return true;
}

bool saves_ps5sx2_game_card(const Config *config, const char *stem, bool assign, char *out,
                            size_t size)
{
    if (!stem || !stem[0])
        return false;
    char ini[PATH_LEN];
    snprintf(ini, sizeof(ini), "%s/settings/%s.ini", config->ps5sx2_dir, stem);
    size_t len;
    char *text = (char *)util_read_file(ini, &len);
    char card[200];
    ini_value(text ? text : "", "MemoryCards/Slot1_Filename", card, sizeof(card));
    /* a card of the player's own choosing stays; a shared one is replaced */
    if (!card[0] || is_shared_card(config, card))
    {
        /* the core reads the line into a 256-byte buffer; '#' would read as a comment.
         * Named without "(Disc N)": every disc of a game shares the card */
        char name[160], game[200];
        disc_tag_strip(stem, game, sizeof(game));
        util_safe_filename(name, sizeof(name) - 4, game);
        for (char *p = name; *p; ++p)
            if (*p == '#')
                *p = '_';
        snprintf(card, sizeof(card), "%s.ps2", name);
        if (assign)
        {
            char *named = ini_set(text ? text : "", "MemoryCards/Slot1_Filename", card);
            /* and the slot on: a card of its own is the point */
            char *updated = named ? ini_set(named, "MemoryCards/Slot1_Enable", "true") : NULL;
            free(named);
            if (updated && util_mkdirs_parent(ini) &&
                util_write_file_atomic(ini, updated, strlen(updated)))
                app_log("PS5SX2: %s uses its own card %s", stem, card);
            free(updated);
        }
    }
    free(text);
    char dir[PATH_LEN];
    saves_ps5sx2_card_dir(config, assign, dir, sizeof(dir));
    snprintf(out, size, "%s/%s", dir, card);
    return true;
}

static bool card_has_serial(const Ps2Card *card, const char *serial)
{
    char s[16];
    for (int i = 0; i < card->count; ++i)
        if (ps2mc_serial_from_dirname(card->dirs[i].name, s, sizeof(s)) && strcmp(s, serial) == 0)
            return true;
    return false;
}

bool saves_ps5sx2_setup_game(const Config *config, const char *stem, const char *serial)
{
    char path[PATH_LEN];
    if (!saves_ps5sx2_game_card(config, stem, true, path, sizeof(path)))
        return false;
    Ps2CardFile own;
    char error[96] = "";
    bool exists = util_file_exists(path);
    if (exists && !ps2mc_load_file(path, &own, error, sizeof(error)))
    {
        app_log("PS5SX2: %s: %s", path, error);
        return false;
    }
    if (!exists)
    {
        memset(&own, 0, sizeof(own));
        own.ecc = !util_has_suffix_i(path, ".mc2");
    }
    bool changed = false;
    char ps2_serial[16] = "";
    util_compact_serial(ps2_serial, sizeof(ps2_serial), serial ? serial : "");
    /* the game's save still on a shared card: copied over, the original stays */
    if (ps2_serial[0] && !card_has_serial(&own.card, ps2_serial))
    {
        char dir[PATH_LEN];
        saves_ps5sx2_card_dir(config, false, dir, sizeof(dir));
        const char *shared[3] = {config->ps2_card, "Mcd001.ps2", "Mcd002.ps2"};
        for (int c = 0; c < 3 && !changed; ++c)
        {
            if (c && util_ieq(shared[c], config->ps2_card))
                continue;
            char from[PATH_LEN];
            util_path_join(from, sizeof(from), dir, shared[c]);
            int64_t size = util_file_size(from);
            if (size != PS2MC_PS2_SIZE && size != PS2MC_MC2_SIZE)
                continue;
            Ps2CardFile src;
            if (!ps2mc_load_file(from, &src, error, sizeof(error)))
                continue;
            for (int i = 0; i < src.card.count; ++i)
            {
                char s[16];
                if (!ps2mc_serial_from_dirname(src.card.dirs[i].name, s, sizeof(s)) ||
                    strcmp(s, ps2_serial) != 0)
                    continue;
                /* a deep copy through the server's folder format */
                size_t p2fd_size;
                uint8_t *p2fd = ps2mc_build_p2fd(&src.card.dirs[i], &p2fd_size);
                Ps2Dir copy;
                if (p2fd && ps2mc_parse_p2fd(p2fd, p2fd_size, &copy))
                {
                    if (ps2mc_put_dir(&own.card, &copy))
                        changed = true;
                    ps2mc_free_dir(&copy);
                }
                free(p2fd);
                if (changed)
                    app_log("PS5SX2: %s's save %s copied from %s to its own card", stem,
                            src.card.dirs[i].name, shared[c]);
            }
            ps2mc_free(&src.card);
        }
    }
    bool ok = true;
    if (changed || !exists)
        ok = ps2mc_save_file(path, &own, (uint32_t)time(NULL), error, sizeof(error));
    if (!ok)
        app_log("PS5SX2: can't write %s: %s", path, error);
    ps2mc_free(&own.card);
    return ok && (changed || !exists);
}

/* ------------------------------------------------------------ Porpoise */

static const char *const GC_REGIONS[] = {"USA", "EUR", "JAP"};

void saves_porpoise_card_dir(const Config *config, const char *code, char *out, size_t size)
{
    /* the disc code's last letter is its region, as Dolphin files cards */
    char r = code && strlen(code) >= 4 ? code[3] : 'E';
    const char *region = r == 'J' ? "JAP" : (r == 'P' || r == 'D' || r == 'F' || r == 'S' ||
                                             r == 'I' || r == 'U' || r == 'X' || r == 'Y')
                                                ? "EUR"
                                                : "USA";
    snprintf(out, size, "%s/saves/User/GC/%s/Card A", config->porpoise_dir, region);
}

static bool hex_val(char c, int *v)
{
    if (c >= '0' && c <= '9')
        *v = c - '0';
    else if (c >= 'a' && c <= 'f')
        *v = c - 'a' + 10;
    else if (c >= 'A' && c <= 'F')
        *v = c - 'A' + 10;
    else
        return false;
    return true;
}

static bool code_char_ok(char c)
{
    return (c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9');
}

bool saves_porpoise_wii_dir(const Config *config, const char *title_id, char *out, size_t size)
{
    /* WII_RMCE -> title/00010000/524d4345/data (lower-case hex, as Dolphin) */
    if (strncmp(title_id, "WII_", 4) != 0 || strlen(title_id) != 8)
        return false;
    char hex[9];
    for (int i = 0; i < 4; ++i)
    {
        if (!code_char_ok(title_id[4 + i]))
            return false;
        snprintf(hex + i * 2, 3, "%02x", (unsigned char)title_id[4 + i]);
    }
    snprintf(out, size, "%s/saves/User/Wii/title/00010000/%s/data", config->porpoise_dir, hex);
    return true;
}

/* A .gci's game code: the first four bytes of its directory entry. */
static bool gci_code(const char *path, char code[5])
{
    FILE *f = fopen(path, "rb");
    if (!f)
        return false;
    uint8_t head[6];
    bool ok = fread(head, 1, 6, f) == 6;
    fclose(f);
    for (int i = 0; ok && i < 4; ++i)
    {
        char c = (char)head[i];
        if (c >= 'a' && c <= 'z')
            c = (char)(c - 32);
        ok = code_char_ok(c);
        code[i] = c;
    }
    code[4] = 0;
    return ok;
}

static int64_t mtime_of(const char *path)
{
    struct stat st;
    return stat(path, &st) == 0 ? (int64_t)st.st_mtime : 0;
}

static void scan_porpoise_gc(const Config *config, SaveList *list)
{
    for (size_t r = 0; r < sizeof(GC_REGIONS) / sizeof(GC_REGIONS[0]); ++r)
    {
        char region[PATH_LEN];
        snprintf(region, sizeof(region), "%s/saves/User/GC/%s", config->porpoise_dir, GC_REGIONS[r]);
        DIR *rd = opendir(region);
        if (!rd)
            continue;
        struct dirent *card;
        while ((card = readdir(rd)) != NULL)
        {
            if (strncmp(card->d_name, "Card", 4) != 0)
                continue;
            char dir[PATH_LEN];
            util_path_join(dir, sizeof(dir), region, card->d_name);
            DIR *d = opendir(dir);
            if (!d)
                continue;
            struct dirent *de;
            while ((de = readdir(d)) != NULL)
            {
                if (!util_has_suffix_i(de->d_name, ".gci"))
                    continue;
                char path[PATH_LEN], code[5], tid[16];
                util_path_join(path, sizeof(path), dir, de->d_name);
                if (!gci_code(path, code))
                    continue;
                snprintf(tid, sizeof(tid), "GC_%s", code);
                SaveEntry *e = saves_find(list, tid);
                if (e)
                {
                    /* several files for one game: the newest is the save (as on
                     * Android / Steam Deck) */
                    if (mtime_of(path) > mtime_of(e->path))
                    {
                        util_copy(e->path, sizeof(e->path), path);
                        saves_hash_local(e);
                    }
                    continue;
                }
                e = saves_add(list);
                if (!e)
                    break;
                e->emu = EMU_PORPOISE;
                e->kind = SAVE_GCI;
                util_copy(e->system, sizeof(e->system), "GC");
                util_copy(e->title_id, sizeof(e->title_id), tid);
                util_copy(e->name, sizeof(e->name), tid);
                util_copy(e->path, sizeof(e->path), path);
                saves_hash_local(e);
            }
            closedir(d);
        }
        closedir(rd);
    }
}

static void scan_porpoise_wii(const Config *config, SaveList *list)
{
    char root[PATH_LEN];
    snprintf(root, sizeof(root), "%s/saves/User/Wii/title/00010000", config->porpoise_dir);
    DIR *d = opendir(root);
    if (!d)
        return;
    struct dirent *de;
    while ((de = readdir(d)) != NULL)
    {
        if (strlen(de->d_name) != 8)
            continue;
        char code[5];
        bool ok = true;
        for (int i = 0; i < 4 && ok; ++i)
        {
            int hi = 0, lo = 0;
            ok = hex_val(de->d_name[i * 2], &hi) && hex_val(de->d_name[i * 2 + 1], &lo);
            code[i] = (char)(hi << 4 | lo);
            ok = ok && code_char_ok(code[i]);
        }
        code[4] = 0;
        if (!ok)
            continue;
        SaveEntry *e = saves_add(list);
        if (!e)
            break;
        e->emu = EMU_PORPOISE;
        e->kind = SAVE_FOLDER;
        util_copy(e->system, sizeof(e->system), "WII");
        snprintf(e->title_id, sizeof(e->title_id), "WII_%s", code);
        util_copy(e->name, sizeof(e->name), e->title_id);
        snprintf(e->path, sizeof(e->path), "%s/%s/data", root, de->d_name);
        saves_hash_local(e);
        if (!e->local)
            list->count--; /* an empty data/ folder: nothing saved yet */
    }
    closedir(d);
}

/* ------------------------------------------------------------ ProsperoEden */

static bool is_hex_name(const char *s, size_t len)
{
    if (strlen(s) != len)
        return false;
    for (size_t i = 0; i < len; ++i)
    {
        int v;
        if (!hex_val(s[i], &v))
            return false;
    }
    return true;
}

bool saves_eden_user_dir(const Config *config, char *out, size_t size)
{
    char root[PATH_LEN];
    snprintf(root, sizeof(root), "%s/user/nand/user/save/0000000000000000", config->eden_dir);
    /* the profile the app has selected (config/prosperoeden.json profiles.current) */
    char cfg[PATH_LEN];
    snprintf(cfg, sizeof(cfg), "%s/config/prosperoeden.json", config->eden_dir);
    char *text = (char *)util_read_file(cfg, NULL);
    if (text)
    {
        JsonValue *json = json_parse(text);
        free(text);
        const char *current = json_get_str(json_get(json, "profiles"), "current", "");
        bool ok = is_hex_name(current, 32);
        if (ok)
        {
            char upper[33];
            for (int i = 0; i < 33; ++i)
                upper[i] = (char)(current[i] >= 'a' && current[i] <= 'f' ? current[i] - 32 : current[i]);
            snprintf(out, size, "%s/%s", root, upper);
        }
        json_free(json);
        if (ok)
            return true;
    }
    /* otherwise the one user folder there is (Eden's own fallback) */
    DIR *d = opendir(root);
    if (!d)
        return false;
    int found = 0;
    struct dirent *de;
    while ((de = readdir(d)) != NULL)
    {
        if (!is_hex_name(de->d_name, 32) ||
            strcmp(de->d_name, "00000000000000000000000000000000") == 0)
            continue;
        if (found++ == 0)
            snprintf(out, size, "%s/%s", root, de->d_name);
    }
    closedir(d);
    return found == 1;
}

static void scan_eden(const Config *config, SaveList *list)
{
    char user[PATH_LEN];
    if (!saves_eden_user_dir(config, user, sizeof(user)))
        return;
    DIR *d = opendir(user);
    if (!d)
        return;
    struct dirent *de;
    while ((de = readdir(d)) != NULL)
    {
        if (!is_hex_name(de->d_name, 16))
            continue;
        SaveEntry *e = saves_add(list);
        if (!e)
            break;
        e->emu = EMU_EDEN;
        e->kind = SAVE_FOLDER;
        util_copy(e->system, sizeof(e->system), "NSW");
        snprintf(e->title_id, sizeof(e->title_id), "NSW_%s", de->d_name);
        for (char *p = e->title_id + 4; *p; ++p)
            if (*p >= 'a' && *p <= 'f')
                *p = (char)(*p - 32);
        util_copy(e->name, sizeof(e->name), e->title_id);
        util_path_join(e->path, sizeof(e->path), user, de->d_name);
        saves_hash_local(e);
        if (!e->local)
            list->count--;
    }
    closedir(d);
}

/* ------------------------------------------------------------ PS5CEMU-HAR */

/* Cemu: <cemu>/mlc01/usr/save/00050000/<lo>/user, holding common/ and the
 * account folders (80000001...). That folder is the bundle root, as the Wii U
 * client files a console's saves, so both share the 00050000<LO> slot. */
#define CEMU_SAVES "/mlc01/usr/save/00050000"
/* Azahar: ID0/ID1 are all zeros on PS5 (as on PC); data/00000001 is the save
 * archive's root, the files the 3DS client bundles. */
#define AZAHAR_SAVES "/azahar/sdmc/Nintendo 3DS/00000000000000000000000000000000/" \
                     "00000000000000000000000000000000/title/00040000"

bool saves_cemu_dir(const Config *config, const char *title_id, char *out, size_t size)
{
    if (strlen(title_id) != 16 || !is_hex_name(title_id, 16))
        return false;
    char lo[9];
    for (int i = 0; i < 8; ++i)
        lo[i] = (char)(title_id[8 + i] >= 'A' && title_id[8 + i] <= 'F' ? title_id[8 + i] + 32
                                                                        : title_id[8 + i]);
    lo[8] = 0;
    if (strncmp(title_id, "00050000", 8) == 0)
        snprintf(out, size, "%s" CEMU_SAVES "/%s/user", config->cemu_dir, lo);
    else if (strncmp(title_id, "00040000", 8) == 0)
        snprintf(out, size, "%s" AZAHAR_SAVES "/%s/data/00000001", config->cemu_dir, lo);
    else
        return false;
    return true;
}

static void scan_cemu_root(const Config *config, SaveList *list, const char *root,
                           const char *hi, const char *system, const char *tail)
{
    DIR *d = opendir(root);
    if (!d)
        return;
    struct dirent *de;
    while ((de = readdir(d)) != NULL)
    {
        if (!is_hex_name(de->d_name, 8))
            continue;
        SaveEntry *e = saves_add(list);
        if (!e)
            break;
        e->emu = EMU_CEMU;
        e->kind = SAVE_FOLDER;
        util_copy(e->system, sizeof(e->system), system);
        snprintf(e->title_id, sizeof(e->title_id), "%s%s", hi, de->d_name);
        for (char *p = e->title_id; *p; ++p)
            if (*p >= 'a' && *p <= 'f')
                *p = (char)(*p - 32);
        util_copy(e->name, sizeof(e->name), e->title_id);
        snprintf(e->path, sizeof(e->path), "%s/%s%s", root, de->d_name, tail);
        saves_hash_local(e);
        if (!e->local)
            list->count--;
    }
    closedir(d);
    (void)config;
}

static void scan_cemu(const Config *config, SaveList *list)
{
    char root[PATH_LEN];
    snprintf(root, sizeof(root), "%s" CEMU_SAVES, config->cemu_dir);
    scan_cemu_root(config, list, root, "00050000", "WIIU", "/user");
    snprintf(root, sizeof(root), "%s" AZAHAR_SAVES, config->cemu_dir);
    scan_cemu_root(config, list, root, "00040000", "3DS", "/data/00000001");
}

/* XPSemu: the game folders in E:\UDATA on its disk image. Read while
 * XPSemu runs, a save it is writing can come out half-written: the next
 * scan sees the finished one. */
static void scan_xemu(const Config *config, SaveList *list)
{
    char image[PATH_LEN];
    xhdd_image_path(config->xemu_dir, image, sizeof(image));
    if (!util_file_exists(image))
        return;
    char err[96];
    XboxHdd *hdd = xhdd_open(image, err, sizeof(err));
    if (!hdd)
    {
        app_log("XPSemu: %s: %s", image, err);
        return;
    }
    char ids[256][9];
    int n = xhdd_titles(hdd, ids, 256);
    for (int i = 0; i < n; ++i)
    {
        SaveTree tree;
        if (!xhdd_read_title(hdd, ids[i], XBOX_SAVE_MAX, &tree, err, sizeof(err)))
        {
            app_log("XPSemu: %s skipped: %s", ids[i], err);
            continue;
        }
        if (tree.count)
        {
            SaveEntry *e = saves_add(list);
            if (!e)
            {
                tree_free(&tree);
                break;
            }
            e->emu = EMU_XEMU;
            e->kind = SAVE_XBOX;
            util_copy(e->system, sizeof(e->system), "XBOX");
            util_copy(e->title_id, sizeof(e->title_id), ids[i]);
            util_copy(e->name, sizeof(e->name), ids[i]);
            util_copy(e->path, sizeof(e->path), image);
            tree_hash(&tree, e->local_hash, &e->local_size);
            e->local = true;
        }
        tree_free(&tree);
    }
    xhdd_close(hdd);
}

/* PS5CEMU-HAR's melonDS: every DS game in its games folder (and one level
 * of sub-folders, which its library also scans), its save
 * melonds/saves/<game file>.sav as a raw /raw save keyed by the gamecode.
 * A game with no save on either side is dropped after the server compare. */
static void scan_melonds_dir(const Config *config, SaveList *list, const char *dir, int depth)
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
        if (!melonds_is_game(de->d_name))
        {
            if (depth < 1 && util_dir_exists(path))
                scan_melonds_dir(config, list, path, depth + 1);
            continue;
        }
        char id[20];
        if (!melonds_title_id(path, id, sizeof(id)) || saves_find(list, id))
            continue;
        SaveEntry *e = saves_add(list);
        if (!e)
            break;
        e->emu = EMU_CEMU;
        e->kind = SAVE_SRM;
        util_copy(e->system, sizeof(e->system), "NDS");
        util_copy(e->title_id, sizeof(e->title_id), id);
        util_copy(e->rom_file, sizeof(e->rom_file), de->d_name);
        util_copy(e->name, sizeof(e->name), de->d_name);
        util_strip_ext(e->name);
        melonds_save_path(config, de->d_name, e->path, sizeof(e->path));
        e->installed = true; /* the game is here: Sync all may fetch its save */
        saves_hash_local(e);
    }
    closedir(d);
}

static void scan_melonds(const Config *config, SaveList *list)
{
    char games[PATH_LEN];
    melonds_games_dir(config, games, sizeof(games));
    scan_melonds_dir(config, list, games, 0);
}

/* ------------------------------------------------------------ PPSSPP */

/* PS1 disc prefixes: PSone Classics on a memory stick, which the PSP client
 * leaves to the PS1 side too. */
static const char *const PSX_PREFIXES[] = {
    "SLUS", "SCUS", "PAPX", "SLES", "SCES", "SCED", "SLPS",
    "SLPM", "SCPS", "SCPM", "SLAJ", "SLEJ", "SCAJ",
};

bool saves_is_psp_slot(const char *name)
{
    size_t len = strlen(name);
    if (len < 9 || len > 31)
        return false;
    for (size_t i = 0; i < len; ++i)
    {
        unsigned char c = (unsigned char)name[i];
        if (i < 4 ? !isupper(c) : i < 9 ? !isdigit(c) : !isupper(c) && !isdigit(c))
            return false;
    }
    for (size_t i = 0; i < sizeof(PSX_PREFIXES) / sizeof(PSX_PREFIXES[0]); ++i)
        if (strncmp(name, PSX_PREFIXES[i], 4) == 0)
            return false;
    return true;
}

/* Older PS5_RetroArch builds gave PPSSPP system/PPSSPP as its memory stick:
 * a slot only there is copied to the one the core reads now (the old copy
 * stays). */
static void ppsspp_adopt_legacy(const Config *config, const char *savedata)
{
    char legacy[PATH_LEN];
    snprintf(legacy, sizeof(legacy), "%s/system/PPSSPP/PSP/SAVEDATA", config->retroarch_dir);
    if (strcmp(legacy, savedata) == 0)
        return;
    DIR *d = opendir(legacy);
    if (!d)
        return;
    struct dirent *de;
    while ((de = readdir(d)) != NULL)
    {
        if (!saves_is_psp_slot(de->d_name))
            continue;
        char from[PATH_LEN], to[PATH_LEN];
        util_path_join(from, sizeof(from), legacy, de->d_name);
        util_path_join(to, sizeof(to), savedata, de->d_name);
        if (util_dir_exists(to))
            continue;
        SaveTree tree;
        if (!tree_read(from, NULL, &tree))
            continue;
        if (tree.count && util_mkdirs(to) && tree_write_replace(to, &tree))
        {
            chmod(to, 0777);
            app_log("PPSSPP: %s copied from the old memory stick (%s)", de->d_name, legacy);
        }
        tree_free(&tree);
    }
    closedir(d);
}

static void scan_ppsspp(const Config *config, SaveList *list)
{
    if (!config->retroarch_dir[0] || !util_dir_exists(config->retroarch_dir))
        return;
    char savedata[PATH_LEN];
    retroarch_psp_savedata(config, savedata, sizeof(savedata));
    ppsspp_adopt_legacy(config, savedata);
    DIR *d = opendir(savedata);
    if (!d)
        return;
    struct dirent *de;
    while ((de = readdir(d)) != NULL)
    {
        if (!saves_is_psp_slot(de->d_name))
            continue;
        SaveEntry *e = saves_add(list);
        if (!e)
            break;
        e->emu = EMU_RETROARCH;
        e->kind = SAVE_FOLDER;
        util_copy(e->system, sizeof(e->system), "PSP");
        util_copy(e->title_id, sizeof(e->title_id), de->d_name);
        util_copy(e->name, sizeof(e->name), de->d_name);
        util_path_join(e->path, sizeof(e->path), savedata, de->d_name);
        e->installed = true; /* the game ran here */
        saves_hash_local(e);
        if (!e->local)
            list->count--;
    }
    closedir(d);
}

void saves_scan(const Config *config, const KvStore *ids, const KvStore *installed,
                SaveList *list)
{
    saves_free(list);
    /* SwanStation before RetroArch: a PS1 game in both keeps SwanStation's card */
    scan_swanstation(config, list);
    scan_ps5sx2(config, list);
    scan_porpoise_gc(config, list);
    scan_porpoise_wii(config, list);
    scan_eden(config, list);
    scan_cemu(config, list);
    scan_xemu(config, list);
    scan_melonds(config, list);
    scan_ppsspp(config, list);
    retroarch_scan(config, ids, list);
    for (int i = 0; i < list->count; ++i)
    {
        SaveEntry *e = &list->items[i];
        e->status = ST_UNKNOWN;
        if (e->title_id[0] && kv_get(installed, e->title_id))
            e->installed = true;
    }
}

const char *save_status_text(SaveStatus status)
{
    switch (status)
    {
    case ST_SYNCED: return "Synced";
    case ST_UPLOAD: return "Upload";
    case ST_DOWNLOAD: return "Download";
    case ST_CONFLICT: return "Conflict";
    case ST_LOCAL_ONLY: return "Local Only";
    case ST_SERVER_ONLY: return "Server Only";
    case ST_NONE: return "No Save";
    case ST_ERROR: return "Error";
    default: return "Unknown";
    }
}
