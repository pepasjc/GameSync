# No-Intro / Redump DAT Files

Place No-Intro or Redump XML DAT files in this folder.
The server auto-detects the system from the filename on startup.

## Where to get them

- **No-Intro**: https://www.no-intro.org/ (requires free account)
  Download individual system DATs or the full pack ("Daily Download")
- **Redump**: http://redump.org/downloads/
  Used for CD-based systems (PS1, PS2, Saturn, Dreamcast, GameCube, etc.)

## Naming

The filename is used to detect the system — any No-Intro or Redump
filename works as-is. Examples of recognized names:

| DAT filename (partial)                        | System |
|-----------------------------------------------|--------|
| Nintendo - Game Boy Advance                   | GBA    |
| Nintendo - Super Nintendo Entertainment System| SNES   |
| Nintendo - Nintendo 64                        | N64    |
| Nintendo - Nintendo DS                        | NDS    |
| Sony - PlayStation                            | PS1    |
| Sony - PlayStation 2                          | PS2    |
| Sony - PlayStation Portable                   | PSP    |
| Sega - Mega Drive - Genesis                   | GEN    |
| Sega - Saturn                                 | SAT    |
| Sega - Dreamcast                              | DC     |
| Sega - Master System                          | SMS    |
| Sega - Game Gear                              | GG     |
| Sega CD / Mega-CD                             | SCD    |
| SNK - Neo Geo Pocket                          | NGP    |
| NEC - PC Engine                               | PCE    |
| Bandai - WonderSwan                           | WS     |

## How it's used

When the Android app scans your ROM directory it calls
`POST /api/v1/normalize/batch` with filenames (and optional CRC32s).
The server returns the canonical No-Intro name and the matching `title_id`,
so saves sync correctly across different devices even if the ROM filename
differs (e.g. one device has the USA dump, another has a different region).

CRC32 lookup is the most accurate — if your emulator reports CRC32 for
games it can be passed and the server will do an exact DAT match.
Without CRC32, the server does a fuzzy slug match which works for most
standard No-Intro filenames.
