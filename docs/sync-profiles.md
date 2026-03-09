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
