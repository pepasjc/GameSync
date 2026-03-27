# PS1 Sync Notes

This document tracks the PS1 / PSone Classics interoperability model.

## Goal

One logical PS1 save should work across:

- PSP / Vita / Adrenaline PSone Classics
- Android DuckStation
- desktop tools
- other raw-card PS1 emulators

## Formats

There are two relevant save formats:

### Raw memory card

Used by desktop emulators and tools:

- `.mcd`
- `.mcr`

### PSone Classics container

Used by PSP / Vita / Adrenaline:

- `SCEVMC0.VMP`
- `SCEVMC1.VMP`

Often accompanied by:

- `PARAM.SFO`
- `ICON0.PNG`
- `CONFIG.BIN`

## Server Storage Model

For PS1 titles, the server stores both representations in each title's `current/` folder:

- `slot0.mcd`
- `slot1.mcd`
- `SCEVMC0.VMP`
- `SCEVMC1.VMP`

Companion PSP/Vita files are preserved when present.

This allows:

- raw-card clients to use `slot*.mcd`
- PSP/Vita clients to keep working with `SCEVMC*.VMP`

## Conversion Direction

### PSP/Vita upload

When a PSP/Vita client uploads a PS1 save:

- the original PSone Classics files are stored
- raw `slot*.mcd` cards are materialized from the uploaded `VMP`

### Raw-card upload

When Android/Desktop uploads a raw PS1 card:

- raw `slot*.mcd` is stored
- the server regenerates `SCEVMC*.VMP` for PSP/Vita compatibility

## Why `mcr2vmp` Is Used

The handwritten Python `VMP` generation path was not accepted by Adrenaline in end-to-end testing.

The validated working conversion is the upstream reference converter:

- [chrisbrasington/psp_psx_save_sync](https://github.com/chrisbrasington/psp_psx_save_sync)

Bundled source:

- `server/third_party/mcr2vmp`

Server wrapper:

- `server/app/services/mcr2vmp_tool.py`

PS1 helpers:

- `server/app/services/ps1_cards.py`

## Hashing Rules

PS1 uses two hash domains on purpose.

### PSP / Vita hash domain

Used by generic PSP/Vita metadata and bundle flows.

Hash is based on the PSP-visible file set, such as:

- `SCEVMC0.VMP`
- `SCEVMC1.VMP`
- `PARAM.SFO`
- `ICON0.PNG`
- `CONFIG.BIN`

This preserves compatibility with legacy PSP/Vita clients.

### Raw-card hash domain

Used by Android/Desktop PS1 raw-card flows.

Hash is based on:

- `slot0.mcd`
- `slot1.mcd`

This avoids comparing different container formats as if they were the same file.

## Endpoints

Raw-card clients use:

- `GET /api/v1/saves/{title_id}/ps1-card?slot=0|1`
- `GET /api/v1/saves/{title_id}/ps1-card/meta?slot=0|1`
- `POST /api/v1/saves/{title_id}/ps1-card?slot=0|1`

PSP/Vita clients still use:

- `GET /api/v1/saves/{title_id}`
- `POST /api/v1/saves/{title_id}`
- `GET /api/v1/saves/{title_id}/meta`

## Migration Scripts

Relevant scripts:

- `server/migrate_ps1_vmp_to_mcd.py`
- `server/migrate_platform_to_ps1.py`

## Verified Flows

Manually validated:

- PSP/Vita-origin save on server -> Android DuckStation download -> game loads
- Android raw-card upload -> server regenerates `VMP` -> Vita/Adrenaline download -> game loads
- Vita local save deleted -> Vita client download -> game loads again

## Current Notes

- Handheld UI treats PS1 saves as one title entry even when the server stores two card slots.
- Desktop and raw-card clients can work with explicit card files directly.
