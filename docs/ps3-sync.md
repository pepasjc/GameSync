# GameSync PS3 Sync Notes

This document describes the current PS3 client behavior as implemented in
`ps3/`. It replaces the earlier planning notes and should be treated as the
current reference for PS3 sync semantics.

## Goal

GameSync on PS3 is focused first on reliable native PS3 save sync.

The current priorities are:

- stable sync for native PS3 HDD saves
- safe conflict detection using the same three-way model as the other clients
- predictable upload/download behavior on real hardware
- compatibility with Apollo-style export sources when useful

PS1 on PS3 is present, but it is still considered secondary for now. Manual
PS1 operations exist, but PS1 is intentionally excluded from batch auto-sync
until PS3 sync is fully settled.

## Current Scope

### PS3 saves

Supported and actively maintained:

- scan native PS3 HDD saves under `/dev_hdd0/home/<user>/savedata/`
- compute comparable hashes for PS3 saves
- upload/download through the normal bundle endpoints
- maintain `last_synced_hash` state for three-way sync decisions
- skip conflicts during batch sync
- preserve native PS3 metadata when downloading into an existing save slot

### PS1 saves

Supported, but not part of auto-sync yet:

- scan PS1 memory card images
- manual per-save sync remains available
- `R3` batch sync skips PS1 titles on purpose for now

## Save Identity

### PS3 title IDs

For PS3, the server storage key is the exact save-directory name, normalized to
uppercase.

Examples:

- `BLUS30464SAVE`
- `BLES01017AUTOSAVE01`
- `BLJS10001GAME`

This means different save slots for the same game remain separate on the
server.

### Game code usage

The 9-character product code is still used for:

- game-name lookup
- platform classification
- game key lookup for PS3 decrypt/re-encrypt logic

But it is not used as the server storage key.

## PS3 Hashing Model

PS3 sync compares the gameplay-relevant files, not the full native save folder
byte-for-byte.

### Files ignored for comparison

These files do not participate in the PS3 sync hash:

- `PARAM.SFO`
- `PARAM.PFD`
- any `.PNG` file such as `ICON0.PNG`, `PIC0.PNG`, `PIC1.PNG`

This keeps sync decisions stable even when console-owned metadata differs.

### Files included for comparison

The comparable hash is built from:

- visible save files only
- sorted by relative path
- file contents only

Relative paths affect ordering, but filenames are not hashed directly.

### HDD saves vs export sources

For native PS3 HDD saves, GameSync prefers the HDD save as the hash source.
When hashing or uploading a native HDD save, the client decrypts the save data
to a temporary location and hashes the comparable files from that decrypted
view.

USB/export sources are treated as auxiliary upload sources:

- they can be used when there is no real HDD save slot available
- they are skipped during automatic hashing and batch sync
- they do not override a real HDD save when both are present

### Hash cache

The PS3 client keeps a local hash cache, but the cache key now includes a
source fingerprint in addition to title ID, file count, and total size. This
prevents stale hashes from being reused when save contents change without a
size change.

## Sync Semantics

The PS3 client uses the same three-way sync model as the rest of GameSync.

Decision rules:

- local hash equals server hash: up to date
- server has no save: upload
- `last_synced_hash` equals server hash: upload
- `last_synced_hash` equals local hash: download
- all three differ: conflict
- no `last_synced_hash` and hashes differ: download

### Conflict handling

Conflicts are detected conservatively and are not auto-resolved.

- manual sync shows the action and asks for confirmation
- `R3` batch sync skips conflicts instead of forcing them

## Upload Behavior

For PS3 uploads, the client:

1. identifies the local source
2. decrypts HDD saves when needed
3. hashes the comparable files
4. builds a 3DSS bundle
5. uploads the bundle to the server
6. stores the synced hash locally

The uploaded bundle may still contain the full save directory contents, but the
sync decision hash is based only on the comparable PS3 file set described
above.

## Download Behavior

PS3 downloads have two different paths.

### Existing local PS3 save slot

If the save already exists locally, GameSync:

- downloads the bundle
- writes only the actual save data files
- preserves native metadata such as `PARAM.SFO`, `PARAM.PFD`, icons, and audio
- re-encrypts the changed files using the existing save keys from `PARAM.PFD`
- re-signs the save metadata

This is the safest path and is the main native PS3 workflow.

### Server-only PS3 save

For PS3, server-only downloads are currently blocked unless a matching local
save slot already exists on the console.

In practice:

- create a save in-game first
- then run the download/sync again

This is intentional because native PS3 saves need console/user-specific
metadata and key material.

## Apollo Relationship

GameSync is still designed around Apollo-style save handling, but it does not
delegate network sync decisions to Apollo.

Apollo-related assumptions that remain useful here:

- PS3 HDD saves live under `/dev_hdd0/home/<user>/savedata/`
- PS1 cards may exist in Apollo-managed locations
- exported saves and USB saves are useful as auxiliary sources

GameSync itself owns:

- network transfer
- hash comparison
- sync state
- conflict handling
- server interaction

## Controls

Current PS3 client controls:

- `Up / Down`: navigate saves
- `Left / Right`: page up/down
- `Cross (X)`: smart sync selected save
- `Square`: force upload selected save
- `Triangle`: force download selected save
- `R1`: compare local files with the server copy
- `R3`: sync all PS3 saves automatically, skipping conflicts
- `L3`: calculate hash for the selected save now
- `Circle`: rescan saves and refresh hashes/statuses
- `L1`: toggle server-only entries in the list
- `L2 / R2`: switch PS3 user profile
- `Hold Start`: exit

## PS/Home Button

The PSL1GHT/sysutil layer used here does not expose a supported way to disable
the PS/Home button entirely.

To avoid console freezes seen on some real PS3 setups, the app now treats
`SYSUTIL_MENU_OPEN` as a request to exit cleanly instead of trying to remain
active under the XMB overlay.

## Current Intentional Limitations

These are current product decisions, not accidental omissions:

- `R3` batch sync is PS3-only for now
- PS1 saves are manual-only until PS3 sync is considered stable
- server-only PS3 downloads require an existing local save slot
- exported/USB-only PS3 sources are not part of automatic batch sync

## Recommended Next Focus

Before broadening PS1 support, the PS3 path should remain the primary focus:

- keep validating PS3 hashing on real hardware
- keep validating download/re-encrypt/re-sign behavior on existing save slots
- keep server-only slot creation out of scope until there is a safe native path
- only then bring PS1 titles into batch sync
