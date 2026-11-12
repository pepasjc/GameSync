/*
 * GameSync PS5 client - systems, folder names and save file names.
 * SPDX-License-Identifier: GPL-3.0-or-later
 */
#include "naming.h"

#include "util.h"

#include <ctype.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <strings.h>

const char *emulator_name(Emulator emu)
{
    switch (emu)
    {
    case EMU_RETROARCH: return "RetroArch";
    case EMU_SWANSTATION: return "SwanStation";
    case EMU_PS5SX2: return "PS5SX2";
    case EMU_PORPOISE: return "Porpoise";
    case EMU_EDEN: return "ProsperoEden";
    case EMU_CEMU: return "PS5CEMU";
    case EMU_XEMU: return "XPSemu";
    default: return "-";
    }
}

const char *emulator_tag(Emulator emu)
{
    switch (emu)
    {
    case EMU_RETROARCH: return "RA";
    case EMU_SWANSTATION: return "SS";
    case EMU_PS5SX2: return "PS2";
    case EMU_PORPOISE: return "PRP";
    case EMU_EDEN: return "EDEN";
    case EMU_CEMU: return "CEMU";
    case EMU_XEMU: return "XEMU";
    default: return "";
    }
}

typedef struct
{
    const char *folder;
    const char *system;
} FolderMap;

/* shared/systems.py FOLDER_TO_SYSTEM, the entries a PS5 RetroArch core plays,
 * plus the PS5 port's own library ids (ps5_library.c). */
static const FolderMap FOLDERS[] = {
    {"3do", "3DO"},
    {"atari2600", "A2600"},
    {"atari5200", "A5200"},
    {"atari7800", "A7800"},
    {"atarijaguar", "JAGUAR"},
    {"atarilynx", "LYNX"},
    {"arcade", "ARCADE"},
    {"fbneo", "FBNEO"},
    {"cps", "CPS1"},
    {"cps1", "CPS1"},
    {"cps2", "CPS2"},
    {"cps3", "CPS3"},
    {"dreamcast", "DC"},
    {"famicom", "NES"},
    {"fds", "FDS"},
    {"gamegear", "GG"},
    {"gb", "GB"},
    {"gba", "GBA"},
    {"gbc", "GBC"},
    {"gc", "GC"},
    {"genesis", "MD"},
    {"megadrive", "MD"},
    {"megadrivejp", "MD"},
    {"mastersystem", "SMS"},
    {"sg-1000", "SG1000"},
    {"sg1000", "SG1000"},
    {"megacd", "SEGACD"},
    {"segacd", "SEGACD"},
    {"sega32x", "32X"},
    {"n3ds", "3DS"},
    {"n64", "N64"},
    {"nds", "NDS"},
    {"nes", "NES"},
    {"neogeo", "NEOGEO"},
    {"neogeocd", "NEOCD"},
    {"ngp", "NGP"},
    {"ngpc", "NGPC"},
    {"pcengine", "PCE"},
    {"pcenginecd", "PCECD"},
    {"supergrafx", "PCSG"},
    {"pcfx", "PCFX"},
    {"pokemini", "POKEMINI"},
    {"psx", "PS1"},
    {"ps1", "PS1"},
    {"ps2", "PS2"},
    {"psp", "PSP"},
    {"saturn", "SAT"},
    {"sfc", "SNES"},
    {"snes", "SNES"},
    {"tg16", "TG16"},
    {"virtualboy", "VB"},
    {"wii", "WII"},
    {"wonderswan", "WSWAN"},
    {"wonderswancolor", "WSWANC"},
    {"mame", "MAME"},
};

const char *system_from_folder(const char *folder)
{
    for (size_t i = 0; i < sizeof(FOLDERS) / sizeof(FOLDERS[0]); ++i)
        if (util_ieq(FOLDERS[i].folder, folder))
            return FOLDERS[i].system;
    return NULL;
}

typedef struct
{
    const char *system;
    const char *folder;
    const char *display;
    bool sync_srm;
} SystemInfo;

/* Install folder under content/ and display name per server system code. */
static const SystemInfo SYSTEMS[] = {
    {"NES", "nes", "NES", true},
    {"FDS", "fds", "Famicom Disk System", false},
    {"SNES", "snes", "Super Nintendo", true},
    {"GB", "gb", "Game Boy", true},
    {"GBC", "gbc", "Game Boy Color", true},
    {"GBA", "gba", "Game Boy Advance", true},
    {"N64", "n64", "Nintendo 64", true},
    {"NDS", "nds", "Nintendo DS", false},
    {"3DS", "n3ds", "Nintendo 3DS", false},
    {"GC", "gc", "GameCube", false},
    {"WII", "wii", "Wii", false},
    {"VB", "virtualboy", "Virtual Boy", true},
    {"POKEMINI", "pokemini", "Pokemon Mini", true},
    {"MD", "genesis", "Mega Drive / Genesis", true},
    {"SMS", "mastersystem", "Master System", true},
    {"GG", "gamegear", "Game Gear", true},
    {"SG1000", "sg-1000", "SG-1000", true},
    {"32X", "sega32x", "32X", true},
    {"SEGACD", "segacd", "Sega CD", false},
    {"SAT", "Saturn", "Saturn", true}, /* Beetle Saturn: backup RAM as <game>.srm */
    {"DC", "dreamcast", "Dreamcast", true}, /* Flycast: per-game VMU <game>.A1.bin */
    {"PCE", "pcengine", "PC Engine", true},
    {"TG16", "pcengine", "TurboGrafx-16", true},
    {"PCECD", "pcengine", "PC Engine CD", false},
    {"PCSG", "supergrafx", "SuperGrafx", true},
    {"PCFX", "pcfx", "PC-FX", false},
    {"NGP", "ngp", "Neo Geo Pocket", true},
    {"NGPC", "ngpc", "Neo Geo Pocket Color", true},
    {"WSWAN", "wonderswan", "WonderSwan", true},
    {"WSWANC", "wonderswancolor", "WonderSwan Color", true},
    {"LYNX", "atarilynx", "Atari Lynx", true},
    {"A2600", "atari2600", "Atari 2600", false},
    {"A5200", "atari5200", "Atari 5200", false},
    {"A7800", "atari7800", "Atari 7800", false},
    {"JAGUAR", "atarijaguar", "Atari Jaguar", true},
    {"3DO", "3do", "3DO", false},
    {"NEOGEO", "neogeo", "Neo Geo", false},
    {"NEOCD", "neogeocd", "Neo Geo CD", false},
    {"FBNEO", "fbneo", "FinalBurn Neo", false},
    {"MAME", "mame", "MAME", false},
    {"ARCADE", "arcade", "Arcade", false},
    {"CPS1", "cps", "CPS-1", false},
    {"CPS2", "cps", "CPS-2", false},
    {"CPS3", "cps", "CPS-3", false},
    {"PSP", "psp", "PSP", false},
    {"PS1", "psx", "PlayStation", false},
    {"PS2", "ps2", "PlayStation 2", false},
    {"NSW", NULL, "Switch", false},
    {"WIIU", NULL, "Wii U", false},
    {"XBOX", NULL, "Xbox", false},
};

static const SystemInfo *system_info(const char *system)
{
    if (!system)
        return NULL;
    for (size_t i = 0; i < sizeof(SYSTEMS) / sizeof(SYSTEMS[0]); ++i)
        if (util_ieq(SYSTEMS[i].system, system))
            return &SYSTEMS[i];
    return NULL;
}

const char *retroarch_folder_for_system(const char *system)
{
    const SystemInfo *info = system_info(system);
    return info ? info->folder : NULL;
}

bool retroarch_syncs_system(const char *system)
{
    const SystemInfo *info = system_info(system);
    return info && info->sync_srm;
}

Emulator install_emulator_for_system(const char *system, bool ps1_in_retroarch)
{
    if (!system)
        return EMU_NONE;
    if (util_ieq(system, "PS2"))
        return EMU_PS5SX2;
    if (util_ieq(system, "GC") || util_ieq(system, "WII"))
        return EMU_PORPOISE;
    if (util_ieq(system, "NSW"))
        return EMU_EDEN;
    if (util_ieq(system, "WIIU") || util_ieq(system, "3DS") || util_ieq(system, "NDS"))
        return EMU_CEMU; /* PS5CEMU-HAR: Cemu, Azahar and (4.x) melonDS */
    if (util_ieq(system, "XBOX"))
        return EMU_XEMU;
    if (util_ieq(system, "PS1"))
        return ps1_in_retroarch ? EMU_RETROARCH : EMU_SWANSTATION;
    return retroarch_folder_for_system(system) ? EMU_RETROARCH : EMU_NONE;
}

typedef struct
{
    const char *system;
    uint32_t rgb;
} SystemColor;

/* shared/systems.py SYSTEM_COLOR, the badge colours every client uses */
static const SystemColor COLORS[] = {
    {"GBA", 0x7B1FA2},
    {"GB", 0x388E3C},
    {"GBC", 0x2E7D32},
    {"SNES", 0x5D4037},
    {"NES", 0xC62828},
    {"N64", 0x1565C0},
    {"MD", 0x0D47A1},
    {"SMS", 0x1976D2},
    {"SG1000", 0x1E88E5},
    {"GG", 0x00838F},
    {"32X", 0x0277BD},
    {"SEGACD", 0x006064},
    {"PCE", 0x558B2F},
    {"PCECD", 0x558B2F},
    {"PCSG", 0x689F38},
    {"PCFX", 0x827717},
    {"TG16", 0x33691E},
    {"TGCD", 0x33691E},
    {"A2600", 0xE65100},
    {"A7800", 0xBF360C},
    {"LYNX", 0x4E342E},
    {"NGP", 0x37474F},
    {"NGPC", 0x263238},
    {"WSWAN", 0x4527A0},
    {"WSWANC", 0x311B92},
    {"NEOGEO", 0xB71C1C},
    {"ARCADE", 0x880E4F},
    {"PS1", 0x37474F},
    {"PS2", 0x1A237E},
    {"PS3", 0x212121},
    {"PSP", 0x004D40},
    {"NDS", 0x1B5E20},
    {"3DS", 0xB71C1C},
    {"GC", 0x4A148C},
    {"WII", 0x880E4F},
    {"WIIU", 0x006064},
    {"NSW", 0xE53935},
    {"VITA", 0x283593},
    {"3DO", 0x5D4037},
    {"ATARIST", 0x455A64},
    {"ATARIXED", 0x6D4C41},
    {"A5200", 0xD84315},
    {"A800", 0x8D6E63},
    {"JAGUAR", 0x212121},
    {"JAGCD", 0x263238},
    {"NEOCD", 0xAD1457},
    {"PC98", 0x00695C},
    {"X1", 0x0097A7},
    {"X68K", 0x00838F},
    {"POKEMINI", 0x9E9D24},
    {"BS", 0x6A1B9A},
    {"NAOMI", 0x880E4F},
    {"NAOMI2", 0xAD1457},
    {"FBA", 0xB71C1C},
    {"FBNEO", 0xB71C1C},
    {"CPS1", 0xC62828},
    {"CPS2", 0xAD1457},
    {"CPS3", 0x880E4F},
};

uint32_t system_color(const char *system)
{
    for (size_t i = 0; system && i < sizeof(COLORS) / sizeof(COLORS[0]); ++i)
        if (util_ieq(COLORS[i].system, system))
            return COLORS[i].rgb;
    return 0x424242;
}

const char *system_display_name(const char *system)
{
    const SystemInfo *info = system_info(system);
    return info ? info->display : system;
}

bool is_ps_serial(const char *s)
{
    if (strlen(s) != 9)
        return false;
    for (int i = 0; i < 4; ++i)
        if (!isupper((unsigned char)s[i]))
            return false;
    for (int i = 4; i < 9; ++i)
        if (!isdigit((unsigned char)s[i]))
            return false;
    return true;
}

bool swanstation_card_serial(const char *filename, char *out, size_t size)
{
    if (!util_has_suffix_i(filename, "_1.mcd"))
        return false;
    char stem[128];
    size_t len = strlen(filename) - 6;
    if (len >= sizeof(stem))
        return false;
    memcpy(stem, filename, len);
    stem[len] = 0;
    char compact[32];
    util_compact_serial(compact, sizeof(compact), stem);
    if (!is_ps_serial(compact))
        return false;
    snprintf(out, size, "%s", compact);
    return true;
}

void swanstation_card_name(const char *serial, char *out, size_t size)
{
    if (is_ps_serial(serial))
        snprintf(out, size, "%.4s-%s_1.mcd", serial, serial + 4);
    else
        snprintf(out, size, "%s_1.mcd", serial);
}

static bool is_cd_system(const char *system)
{
    static const char *const cd[] = {"PS1", "SEGACD", "PCECD", "SAT", "NEOCD", "3DO", "PCFX", "DC"};
    for (size_t i = 0; system && i < sizeof(cd) / sizeof(cd[0]); ++i)
        if (util_ieq(system, cd[i]))
            return true;
    return false;
}

/* Systems whose games can span discs that install side by side: the CD
 * systems, plus PS2 and GameCube discs. */
bool is_multidisc_system(const char *system)
{
    return is_cd_system(system) || util_ieq(system, "PS2") || util_ieq(system, "GC");
}

/* A disc's number as written: digits (0 counts), a Roman numeral up to X, or
 * one letter (A = 1); its length in *len, -1 when it isn't one. */
static int disc_number(const char *p, size_t *len)
{
    if (isdigit((unsigned char)*p))
    {
        int n = 0;
        const char *q = p;
        while (isdigit((unsigned char)*q))
            n = n * 10 + (*q++ - '0');
        *len = (size_t)(q - p);
        return n;
    }
    static const char *const roman[] = {"I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X"};
    size_t run = 0;
    while (p[run] && strchr("IVXivx", p[run]))
        ++run;
    if (run && (p[run] == ')' || p[run] == ']' || p[run] == ' '))
        for (int i = 9; i >= 0; --i)
            if (strlen(roman[i]) == run && strncasecmp(p, roman[i], run) == 0)
            {
                *len = run;
                return i + 1;
            }
    if (isalpha((unsigned char)p[0]) && (p[1] == ')' || p[1] == ']' || p[1] == ' '))
    {
        *len = 1;
        return toupper((unsigned char)p[0]) - 'A' + 1;
    }
    return -1;
}

/* One "(Disc N)" / "[Disk N of M]" tag at s (case-insensitive), N as
 * disc_number reads it; the tag's length, or 0. */
static size_t disc_tag_at(const char *s, int *number)
{
    const char *p = s;
    if (*p != '(' && *p != '[')
        return 0;
    char close = *p == '(' ? ')' : ']';
    ++p;
    while (*p == ' ')
        ++p;
    if (tolower((unsigned char)p[0]) != 'd' || tolower((unsigned char)p[1]) != 'i' ||
        tolower((unsigned char)p[2]) != 's' ||
        (tolower((unsigned char)p[3]) != 'c' && tolower((unsigned char)p[3]) != 'k'))
        return 0;
    p += 4;
    if (*p != ' ')
        return 0;
    while (*p == ' ')
        ++p;
    size_t len;
    int n = disc_number(p, &len);
    if (n < 0)
        return 0;
    p += len;
    while (*p == ' ')
        ++p;
    if (tolower((unsigned char)p[0]) == 'o' && tolower((unsigned char)p[1]) == 'f' && p[2] == ' ')
    {
        p += 2;
        while (*p == ' ')
            ++p;
        if (disc_number(p, &len) < 0)
            return 0;
        p += len;
        while (*p == ' ')
            ++p;
    }
    if (*p != close)
        return 0;
    *number = n;
    return (size_t)(p + 1 - s);
}

int disc_tag_strip(const char *name, char *out, size_t size)
{
    int disc = -1;
    size_t n = 0;
    bool after = false; /* past the disc tag: (...) there name the disc, not the game */
    for (const char *p = name; *p;)
    {
        int number;
        size_t tag = disc < 0 ? disc_tag_at(p, &number) : 0;
        if (tag)
        {
            disc = number;
            after = true;
            while (n && out[n - 1] == ' ')
                --n;
            p += tag;
            continue;
        }
        if (*p == '[')
        {
            /* a [...] tag stays whole, parentheses inside it too */
            const char *close = strchr(p, ']');
            const char *end = close ? close + 1 : p + strlen(p);
            for (; p < end; ++p)
                if (n + 1 < size)
                    out[n++] = *p;
            continue;
        }
        if (after && *p == '(')
        {
            const char *close = strchr(p, ')');
            if (close)
            {
                while (n && out[n - 1] == ' ')
                    --n;
                p = close + 1;
                continue;
            }
        }
        if (n + 1 < size && !(*p == ' ' && n && out[n - 1] == ' '))
            out[n++] = *p; /* runs of spaces fold to one */
        ++p;
    }
    while (n && out[n - 1] == ' ')
        --n;
    size_t start = 0;
    while (start < n && out[start] == ' ')
        ++start;
    memmove(out, out + start, n - start);
    out[n - start] = 0;
    /* "(Disc 0)" counts: 0 means no tag */
    return disc < 0 ? 0 : disc + 1;
}

bool is_launchable_rom(const char *filename, const char *system)
{
    static const char *const skip[] = {
        ".sub", ".ccd", ".mds", ".mdf", ".raw", ".wav", ".ogg", ".mp3",
        ".txt", ".nfo", ".xml", ".dat", ".jpg", ".jpeg", ".png", ".bmp", ".srm", ".sav",
        ".state", ".cfg", ".ini", ".part", ".tmp", ".msu", ".pcm", ".lpl", ".pdf", ".sbi",
    };
    if (filename[0] == '.')
        return false;
    const char *ext = util_ext(filename);
    if (!ext[0])
        return false;
    /* a disc's .bin/.img tracks load through the .cue beside them */
    if (is_cd_system(system) && (util_ieq(ext, ".bin") || util_ieq(ext, ".img")))
        return false;
    for (size_t i = 0; i < sizeof(skip) / sizeof(skip[0]); ++i)
        if (util_ieq(ext, skip[i]))
            return false;
    return true;
}
