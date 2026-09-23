# Sync Profiles

Sync Profiles let you sync save files from emulator devices (RetroArch, MiSTer FPGA, Analogue Pocket, Everdrive, or any folder) with the server, the same way your 3DS does automatically.

---

## What is a Sync Profile?

A profile tells the Save Manager:
- **Where your save files are** (a folder on your PC or a mounted SD card)
- **What kind of device** they came from (so it knows how the folders are organized)
- **What system** the saves are for (GBA, SNES, NDS, etc.)

Each profile maps to one system on one device. If you have RetroArch with both GBA and SNES saves, create two profiles.

---

## Step 1 — Open the Save Manager

```bash
cd tools
python save_manager.py
```

Click the **"Sync Profiles"** tab.

---

## Step 2 — Add a Profile

Click **Add Profile**. Fill in the dialog:

| Field | What to enter |
|-------|---------------|
| **Profile Name** | A label for yourself, e.g. `RetroArch GBA` or `MiSTer SNES` |
| **Device Type** | See table below |
| **Folder Path** | Browse to the folder containing the save files |
| **System** | The console system (GBA, SNES, NDS, PSP, etc.) |

### Device Types

| Device Type | Expected folder structure | Notes |
|-------------|--------------------------|-------|
| **Generic Folder** | All `.sav`/`.srm` files flat in one folder | Use for anything not listed below |
| **RetroArch** | `saves/CoreName/game.srm` subfolders | Point path to the RetroArch `saves/` root; all cores are scanned automatically |
| **MiSTer (mounted)** | `GBA/game.sav`, `SNES/game.sav` subfolders | Point to the MiSTer `saves/` root (e.g. `E:\saves\` if the SD card is mounted) |
| **Analogue Pocket** | `Memories/GBA/game.sav` subfolders | Point to the SD card root |
| **Everdrive** | Flat folder of `.sav` files | Point to the saves folder, select the system |

> **Note**: For **RetroArch** and **MiSTer**, the **System** dropdown is ignored — the scanner reads the subfolder names automatically (e.g. `mGBA/` → GBA, `Snes9x/` → SNES, `Genesis Plus GX/` → MD).

Click **OK** to save the profile. It appears in the profiles list.

---

## Step 3 — Sync Your Saves

Click the **"Sync"** tab, then **Scan Profiles**.

The table shows every save file found across all your profiles:

| Column | Meaning |
|--------|---------|
| System | GBA, SNES, NDS, etc. |
| Game | Normalized game name derived from the filename |
| Title ID | Server slot ID (e.g. `GBA_zelda_the_minish_cap`) |
| Local File | Path to the save on disk |
| Status | Current sync status (see below) |

### Status Values

| Status | Meaning | What happens on Sync |
|--------|---------|----------------------|
| `Up to date` | Local and server match | Nothing |
| `Local newer` | Your local save is newer than the server | Uploads to server |
| `Server newer` | Server has a newer save than local | Downloads to disk |
| `Not on server` | Save exists locally but not on server yet | Uploads to server |
| `Not local` | Save exists on server but not locally | Downloads to disk |
| `Conflict` | Both sides changed since last sync | Highlighted red — you must choose |

Click **Sync All** to automatically handle all non-conflict rows.

For **Conflict** rows, use the **Keep Local** or **Keep Server** button on each row to resolve manually, then sync again.

---

## How Game Names Work

The title ID is derived from the save filename. Region tags, revision tags, and disc tags are stripped automatically:

```
Legend of Zelda, The - The Minish Cap (USA).srm  →  GBA_zelda_the_minish_cap
Super Metroid (Europe).srm                        →  SNES_super_metroid
Sonic the Hedgehog (Rev 1).sav                    →  MD_sonic_the_hedgehog
```

The same normalization runs on all device types, so a save from MiSTer and a save from RetroArch will resolve to the **same server slot** as long as the base filename is similar.

> **Tip**: If two files normalize to the same title ID, they share the same server save. This is intentional — it is how cross-device sync works. For best results, use the [No-Intro](https://no-intro.org/) naming standard for your ROMs.

---

## Cross-Device Sync Example

1. Play GBA Minish Cap on MiSTer. Save file is at `E:\MiSTer\saves\GBA\Legend of Zelda, The - The Minish Cap.sav`
2. Open Save Manager -> Sync tab -> Scan Profiles
3. Row shows `GBA_zelda_the_minish_cap` with status **Local newer**
4. Click **Sync All** -> save uploads to server
5. On another PC with RetroArch, add a RetroArch GBA profile pointing to `saves/`
6. Scan -> row shows **Server newer**
7. Click **Sync All** -> MiSTer save downloads into RetroArch's save folder

---

## Profile Storage

Profiles are saved in `tools/config.json` under the `"profiles"` key:

```json
{
  "profiles": [
    {
      "name": "RetroArch GBA",
      "device_type": "RetroArch",
      "path": "C:/Users/you/RetroArch/saves",
      "system": "GBA"
    },
    {
      "name": "MiSTer SD Card",
      "device_type": "MiSTer (mounted)",
      "path": "E:/saves",
      "system": "GBA"
    }
  ]
}
```

You can edit this file directly if needed.

---

## MiSTer ROM Installs (SD vs USB, over the network)

MiSTer profiles have an **Install To** option controlling where the ROM
Installer puts catalog games:

| Option | Destination |
|---|---|
| Local folder (mounted SD card) | The profile's Game Folder path on this PC (default) |
| MiSTer over network — SD card | `/media/fat/games/<Core>` via SSH/SFTP |
| MiSTer over network — USB drive | `/media/usb0/games/<Core>` via SSH/SFTP |

Network installs use the SSH connection stored **in the profile** (SSH
Host/Port/User/Password fields, shown for MiSTer profiles; defaults
`root`/`1` match stock MiSTer). Per-core folders (`PSX`, `Saturn`,
`MegaCD`, `TGFX16-CD`, …) are created automatically. CD games install as
**CHD** with no conversion — MiSTer CD cores read CHD natively.

### CD games: one folder per game

CD systems (PS1, Saturn, Mega CD, PC Engine CD, Neo Geo CD, 3DO) install
into a per-game subfolder — `games/PSX/<Game>/<Game>.chd`. The PSX core's
README states it plainly: *"Games that are in their own folder will create
it's own memory card in `saves/PSX` as `<folder name>.sav`"*. Without the
folder, every game shares one memory card.

Multi-disc games collapse into a **single catalog row**, and installing it
writes every disc into one folder whose name has the `(Disc N)` tag
stripped:

```
games/PSX/Final Fantasy VII (USA)/
    Final Fantasy VII (Disc 1) (USA).chd
    Final Fantasy VII (Disc 2) (USA).chd
    Final Fantasy VII (Disc 3) (USA).chd
→ saves/PSX/Final Fantasy VII (USA).sav      (one card, all three discs)
```

That is also what the core needs for in-game disc swapping: all discs must
sit in the same folder for the automatic lid-open/close simulation.

Save sync uses the same profile: when the SSH host is set, the **Sync tab**
scans `/media/fat/saves` on the MiSTer directly (no mounted card needed)
and uploads/downloads saves over SFTP. The old standalone "MiSTer SSH" tab
was removed — everything goes through Sync Profiles now.

## Installing a game from the Sync tab

A save is only half the story — the game it belongs to may not be on the
device at all, which is true of every "Server only" row. The Sync tab has
an **Install Game** column beside Action: after a scan it loads the ROM
catalog for the profile's systems in the background and shows an *Install*
button on every row whose title is available.

Installs go through the ROM installer's own logic, so the layout matches
what the ROM Installer tab would produce — per-device format, folder-per-
game for CD systems, and all discs of a multi-disc set into one folder.

Two cases worth knowing:

- **Several dumps share one title.** `SAT_T-9527G` covers both *Castlevania
  - Symphony of the Night* translations and both *Dracula X* ones, so the
  button asks which to install rather than guessing.
- **Systems the device can't hold** are skipped — a MiSTer profile never
  offers to install a 3DS ROM, and an install into a system with no core
  folder is refused rather than dropped loose in the games root.

### How MiSTer saves are identified

Every MiSTer core writes `.sav`, but what's inside differs per system, and
the filename is the game/folder name — not the key the server uses.

| System | File contents | Key resolved from |
|---|---|---|
| PS1 | Raw 128 KB memory card | In-card product code, else a `SLPM-86219.sav`-style disc-serial filename |
| Saturn | 64 KB backup RAM, byte-expanded (`0xFF` pad at even offsets, data at odd) | ROM catalog lookup of the game name → disc serial |
| Cartridge cores | Raw SRAM (same bytes as `.srm`) | Name slug |

Saturn's backup RAM carries no disc id, so the game name is the only handle.
The server's ROM catalog holds the serial for every dump — including
translation patches — so `Castlevania - Symphony of the Night (Japan) (2M)
[T-En …].sav` resolves to `SAT_T-9527G`, the same slot as the Japanese
original. Saturn saves are converted on the way through: the server keeps
the canonical 32 KB internal BRAM that every Saturn client shares, and the
64 KB expanded form is rebuilt on download.

Blank cards (a formatted PS1 card, or empty Saturn / Sega CD backup RAM,
which the cores create on first boot) are listed as "no save data yet":
they can receive a download but never upload over a real server save.

**Downloads are named after the installed game, not the server.** A core
only loads `<game>.sav` — the name of the game file or folder it booted —
and that rarely matches the server's display name (`Ganbare Goemon 2
Kiteretsu Shougun Mcguiness Japan` on the server vs `Ganbare Goemon 2 -
Kiteretsu Shougun McGuiness (Japan).sfc` on the card). So before writing,
the installed games are searched (USB first, then SD) and the save takes
the on-device name. Matching goes through the same `make_title_id` that
keyed the save, with the ROM catalog as a bridge for serial-keyed systems.
If the game isn't installed, the server name is used — except for PS1,
which falls back to the `SLUS-00067.sav` disc-serial form the core expects
when booting from CD.

When a USB game folder is created for a CD core, the BIOS files
(`boot*.rom`) are copied over from the matching SD folder automatically:
MiSTer cores switch to the USB folder as soon as it exists, so the BIOS has
to follow the games. Existing files are never overwritten.

---

## MiSTer Auto-Sync (without PC)

If you want MiSTer to sync automatically without opening the Save Manager, use the included shell script:

1. Copy `mister/sync_saves.sh` to `/media/fat/Scripts/` on your MiSTer
2. Create `/media/fat/3dssync.cfg`:
   ```
   SERVER_URL=http://192.168.1.100:8000
   API_KEY=your_api_key
   SYSTEMS=GBA,SNES,Genesis
   ```
3. Run it from the MiSTer Scripts menu, or add it to `startup.sh` for automatic sync on boot
