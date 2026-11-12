/*
 * GameSync PS5 client - systems, folder names and save file names.
 * SPDX-License-Identifier: GPL-3.0-or-later
 */
#ifndef GAMESYNC_NAMING_H
#define GAMESYNC_NAMING_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

typedef enum
{
    EMU_NONE,
    EMU_RETROARCH,
    EMU_SWANSTATION,
    EMU_PS5SX2,
    EMU_PORPOISE,
    EMU_EDEN,
    EMU_CEMU,  /* PS5CEMU-HAR: Cemu (Wii U) and Azahar (3DS) */
    EMU_XEMU,  /* XPSemu (original Xbox) */
} Emulator;

const char *emulator_name(Emulator emu);
const char *emulator_tag(Emulator emu); /* "RA", "SS", "PS2" */

/* A RetroArch content folder ("snes", "Saturn", "megadrive") -> server system
 * code ("SNES", "SAT", "MD"); NULL when unknown. Mirrors FOLDER_TO_SYSTEM in
 * shared/systems.py. */
const char *system_from_folder(const char *folder);
/* Server system code -> folder under RetroArch's content/ (the PS5 port's
 * library ids); NULL when RetroArch on PS5 has no core for it. */
const char *retroarch_folder_for_system(const char *system);
/* Systems whose RetroArch .srm the client syncs. */
bool retroarch_syncs_system(const char *system);
/* Where a catalog ROM of this system is installed. */
Emulator install_emulator_for_system(const char *system, bool ps1_in_retroarch);
const char *system_display_name(const char *system);
/* Badge colour (0xRRGGBB) shared with the other clients. */
uint32_t system_color(const char *system);

/* SwanStationPS5 names a game's card "<serial>_1.mcd", serial as the disc
 * spells it ("SLUS-01041_1.mcd"). "SLUS-01041_1.mcd" -> "SLUS01041". false
 * for cards not named after a serial. */
bool swanstation_card_serial(const char *filename, char *out, size_t size);
/* "SLUS01041" -> "SLUS-01041_1.mcd". */
void swanstation_card_name(const char *serial, char *out, size_t size);
/* PS1/PS2 product code check: 4 letters + 5 digits, compact form. */
bool is_ps_serial(const char *s);

/* A ROM file the emulator would load (not a disc's .bin track, artwork,
 * saves...). */
bool is_launchable_rom(const char *filename, const char *system);

/* Systems whose multi-disc games install as one folder of discs. */
bool is_multidisc_system(const char *system);
/* name without its "(Disc N)" / "[Disk N of M]" tag - N in digits, a Roman
 * numeral or a letter - and the (...) tags after it, which name the disc
 * ("(Disc 1) (GDI)"); [...] tags (translations, hacks) stay. Returns the
 * disc's place for sorting - N + 1 ("(Disc 0)" counts), a numeral's or a
 * letter's (A = 1) value + 1 - or 0 when there was no disc tag. */
int disc_tag_strip(const char *name, char *out, size_t size);

#endif
