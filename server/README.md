# GameSync — Server

FastAPI server that stores save files, manages history, coordinates sync across all clients, hosts a ROM library, and serves a web UI for browsing and downloading games and saves.

## Requirements

- Python 3.11+
- [uv](https://docs.astral.sh/uv/getting-started/installation/)
- `chdman` (for CHD extraction) — `sudo apt install mame-tools`
- `ciso` (for PSP CSO conversion) — `sudo apt install ciso`
- Optional: a 3DS conversion toolchain wired through command templates if you
  want `3ds.zip -> .cia` / decrypted `.cia` downloads from the ROM library

## Quick Start

```bash
cd server
uv sync
uv run python run.py
```

The server starts on `http://0.0.0.0:8000` by default.  
Web UI: `http://<server-ip>:8000/`  
API docs: `http://<server-ip>:8000/docs`

---

## Configuration

All settings are via environment variables with the `SYNC_` prefix, or in a `server/.env` file.

| Variable | Default | Description |
|---|---|---|
| `SYNC_API_KEY` | `anything` | Key required in the `X-API-Key` header by all device clients |
| `SYNC_SAVE_DIR` | `./saves` | Directory where save files and history are stored |
| `SYNC_ROM_DIR` | *(unset)* | ROM root directory for the web library (see [ROM Library](#rom-library)) |
| `SYNC_ROM_SCAN_INTERVAL` | `300` | Background ROM rescan interval in seconds (`0` disables it) |
| `SYNC_ROM_3DS_CIA_COMMAND` | `""` | Optional command template that converts a `.3ds` cart image into an installable `.cia` |
| `SYNC_ROM_3DS_DECRYPTED_CIA_COMMAND` | `""` | Optional command template that converts a `.3ds` cart image into a decrypted `.cia` for emulators |
| `SYNC_HOST` | `0.0.0.0` | Bind address |
| `SYNC_PORT` | `8000` | Port |
| `SYNC_MAX_HISTORY_VERSIONS` | `10` | Previous save versions to keep per title |
| `SYNC_SITE_TITLE` | `GameSync` | Title shown in the web UI header and browser tab |
| `SYNC_ADMIN_USERS` | `admin` | Comma-separated nginx Basic Auth usernames with admin access (see [User Roles](#user-roles)) |

Example `server/.env`:

```ini
SYNC_API_KEY=your-secret-key
SYNC_SAVE_DIR=/home/pi/Documents/3ds_sync/server/saves
SYNC_ROM_DIR=/mnt/roms
SYNC_ROM_3DS_CIA_COMMAND=["/usr/local/bin/your-3ds-tool","{input}","{output}"]
SYNC_ROM_3DS_DECRYPTED_CIA_COMMAND=["/usr/local/bin/your-3ds-tool","--decrypted","{input}","{output}"]
SYNC_SITE_TITLE=My Game Library
SYNC_ADMIN_USERS=pepas
```

For the 3DS command templates, the server replaces these placeholders:
`{input}`, `{output}`, `{output_dir}`, `{stem}`.

`Game.3ds.zip` uploads are cataloged as 3DS ROMs by stripping the outer
archive layer for name matching, and the conversion endpoint will unpack the
single `.3ds` file from the ZIP before running your configured command.

---

## Running as a Service (Raspberry Pi / Linux)

A systemd unit template is included at `server/3dssync@.service`.

```bash
sudo cp 3dssync@.service /etc/systemd/system/3dssync@.service
sudo nano /etc/default/3dssync   # set APP_DIR and UV_BIN
sudo systemctl daemon-reload
sudo systemctl enable --now 3dssync@pi
```

Example `/etc/default/3dssync`:

```bash
APP_DIR=/home/pi/Documents/3ds_sync/server
UV_BIN=/home/pi/.local/bin/uv
```

Check logs:

```bash
sudo journalctl -u 3dssync@pi -f
```

---

## Web UI

The web UI is served at `GET /` and requires no authentication from the browser — the server injects the API key into the page automatically.

Features:
- **ROMs tab** — browse all ROMs grouped by system with download buttons
- **Saves tab** — browse all saves grouped by platform with download buttons
- **Search** — live filter across the active tab
- **System chips** (mobile) — horizontal scrollable system filter bar, sticky below the header
- **CHD conversion** — for CD-ROM systems, converts CHDs on the fly before download:
  - PS1, Saturn, Sega CD, PC Engine CD, 3DO, etc. → CUE/BIN (ZIP)
  - Dreamcast → GDI (ZIP)
  - PSP → ISO or CSO
- **Settings** (admin only) — rescan ROM directory

### ROM folder structure

The scanner expects EmuDeck-style folder names under `SYNC_ROM_DIR`:

```
roms/
  gba/          → GBA
  snes/         → SNES
  psx/          → PS1
  psp/          → PSP
  dreamcast/    → DC
  saturn/       → SAT
  atarijaguar/  → JAGUAR
  atarijaguarcd/→ JAGCD
  atari2600/    → A2600
  atari5200/    → A5200
  atari7800/    → A7800
  atarilynx/    → LYNX
  atarist/      → ATARIST
  ...
```

Trigger a rescan from the ⚙ Settings button in the web UI, or via:

```bash
curl -H "X-API-Key: your-key" http://localhost:8000/api/v1/roms/scan
```

---

## Reverse Proxy & HTTPS (nginx)

For external access with HTTPS and user authentication, use nginx as a reverse proxy. The server itself stays on port 8000; nginx handles the rest.

> **Note:** Many residential ISPs block inbound ports 80 and 443. The config below uses **port 8443** for external HTTPS to work around this. LAN access remains on the standard ports 80 and 443.

### Install nginx

```bash
sudo apt install nginx -y
```

### Get a TLS certificate (Let's Encrypt via acme.sh + DuckDNS)

If your ISP blocks port 80 (common on residential connections), use the DNS-01 challenge instead:

```bash
# Install acme.sh
curl https://get.acme.sh | sh -s email=you@example.com
source ~/.bashrc

# Switch to Let's Encrypt (acme.sh defaults to ZeroSSL which requires extra setup)
~/.acme.sh/acme.sh --set-default-ca --server letsencrypt

# Issue cert via DuckDNS DNS challenge (replace token and domain)
DuckDNS_Token="your-duckdns-token" ~/.acme.sh/acme.sh --issue --dns dns_duckdns -d yourdomain.duckdns.org --force

# Install cert into nginx's folder
sudo mkdir -p /etc/nginx/ssl
sudo chown -R $USER:$USER /etc/nginx/ssl
~/.acme.sh/acme.sh --install-cert -d yourdomain.duckdns.org \
  --cert-file      /etc/nginx/ssl/cert.pem \
  --key-file       /etc/nginx/ssl/key.pem \
  --fullchain-file /etc/nginx/ssl/fullchain.pem \
  --reloadcmd      "sudo systemctl reload nginx"
```

Cert auto-renews via cron every 60 days.

### nginx config

```nginx
# /etc/nginx/sites-available/gamesync

limit_req_zone $binary_remote_addr zone=main:10m rate=20r/s;

# ── LAN: plain HTTP (no password) ────────────────────────────────────────────
server {
    listen 80;
    server_name 192.168.1.201;   # your Pi's LAN IP

    client_max_body_size 0;
    proxy_read_timeout 600s;
    proxy_send_timeout 600s;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Remote-User $remote_user;
    }
}

# ── LAN: HTTPS (no password, self-signed cert is fine here) ──────────────────
server {
    listen 443 ssl;
    server_name 192.168.1.201;

    ssl_certificate     /etc/nginx/ssl/fullchain.pem;
    ssl_certificate_key /etc/nginx/ssl/key.pem;

    client_max_body_size 0;
    proxy_read_timeout 600s;
    proxy_send_timeout 600s;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Remote-User $remote_user;
    }
}

# ── External: redirect HTTP → HTTPS on port 8443 ─────────────────────────────
server {
    listen 80;
    server_name yourdomain.duckdns.org;
    return 301 https://$host:8443$request_uri;
}

# ── External: HTTPS on port 8443 + Basic Auth ────────────────────────────────
# Uses port 8443 because most residential ISPs block inbound 443.
server {
    listen 8443 ssl;
    server_name yourdomain.duckdns.org;

    ssl_certificate     /etc/nginx/ssl/fullchain.pem;
    ssl_certificate_key /etc/nginx/ssl/key.pem;

    limit_req zone=main burst=30 nodelay;

    client_max_body_size 0;
    proxy_read_timeout 600s;
    proxy_send_timeout 600s;

    auth_basic "GameSync";
    auth_basic_user_file /etc/nginx/.htpasswd;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Remote-User $remote_user;  # passes username for role check
    }
}
```

```bash
sudo ln -sf /etc/nginx/sites-available/gamesync /etc/nginx/sites-enabled/gamesync
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t && sudo systemctl restart nginx
```

Make sure port 8443 is forwarded in your router/DMZ to the Pi, the same way you forwarded port 8000 previously.

---

## User Roles

The web UI supports two roles: **admin** and **read-only**.

- **Admin** — can browse, download, and trigger ROM rescans via ⚙ Settings.
- **Read-only** — can browse and download only. Settings button is hidden; the scan API returns 403.

Roles are determined by the nginx Basic Auth username forwarded in the `X-Remote-User` header. Admin usernames are configured via `SYNC_ADMIN_USERS`.

### Add users

```bash
# Create the htpasswd file and add the first (admin) user
sudo htpasswd -c /etc/nginx/.htpasswd youradminuser

# Add a read-only user
sudo htpasswd /etc/nginx/.htpasswd readonlyuser

# Change a password
sudo htpasswd /etc/nginx/.htpasswd youradminuser
```

Then set the admin username in `.env`:

```ini
SYNC_ADMIN_USERS=youradminuser
# Multiple admins:
# SYNC_ADMIN_USERS=pepas,admin
```

> **LAN access** (direct to port 8000, no nginx): always treated as admin regardless of `SYNC_ADMIN_USERS`.

---

## DS/PSP Access Point

If you run a dedicated open WiFi AP on the Pi for Nintendo DS and PSP clients (which don't support WPA2), you need NAT forwarding. This is separate from the nginx port 80/443 setup.

```bash
# Re-add NAT masquerade if it was lost (e.g. after iptables flush)
sudo iptables -t nat -A POSTROUTING -o eth0 -j MASQUERADE

# Persist across reboots
sudo sh -c "iptables-save > /etc/iptables/rules.v4"
sudo netfilter-persistent save

# Ensure IP forwarding is enabled
echo "net.ipv4.ip_forward=1" | sudo tee /etc/sysctl.d/99-ip-forward.conf
sudo sysctl -w net.ipv4.ip_forward=1
```

DS and PSP clients connect to the AP and reach the server at the AP gateway IP (default `192.168.4.1`) on port 8000.

---

## Game Database (DAT Files)

The server uses No-Intro / Redump DAT files to resolve canonical game names and enable CRC32-based ROM matching. Without them, names fall back to the raw filename.

Download DAT files from the [libretro-database repository](https://github.com/libretro/libretro-database/tree/master/dat) and place them in `server/data/dats/`.

The filename is used to detect the system automatically — standard No-Intro and Redump filenames work as-is.

**Supported DAT filename patterns (examples):**

| DAT filename | System |
|---|---|
| `Nintendo - Game Boy Advance.dat` | GBA |
| `Nintendo - Super Nintendo Entertainment System.dat` | SNES |
| `Sony - PlayStation.dat` | PS1 |
| `Sony - PlayStation Portable.dat` | PSP |
| `Sega - Saturn.dat` | SAT |
| `Atari - 2600.dat` | A2600 |
| `Atari - 5200.dat` | A5200 |
| `Atari - 7800.dat` | A7800 |
| `Atari - Jaguar.dat` | JAGUAR |
| `Atari - Jaguar CD.dat` | JAGCD |
| `Atari - Lynx.dat` | LYNX |
| `Atari - ST.dat` | ATARIST |

---

## API Reference

All endpoints except `GET /` and `GET /api/v1/status` require `X-API-Key` header.

### Saves

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/v1/status` | Health check, returns save count |
| `GET` | `/api/v1/titles` | List all saves with metadata |
| `POST` | `/api/v1/titles/names` | Resolve product codes to game names |
| `GET` | `/api/v1/saves/{title_id}` | Download save bundle |
| `POST` | `/api/v1/saves/{title_id}` | Upload save bundle |
| `DELETE` | `/api/v1/saves/{title_id}` | Delete a save |
| `GET` | `/api/v1/saves/{title_id}/history` | List previous versions |
| `GET` | `/api/v1/saves/{title_id}/raw` | Download raw save bytes (NDS clients) |
| `POST` | `/api/v1/saves/{title_id}/raw` | Upload raw save bytes (NDS clients) |
| `POST` | `/api/v1/sync` | Batch sync plan (upload / download / conflict) |

### ROMs

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/v1/roms` | List all ROMs with optional `?system=` / `?search=` filters |
| `GET` | `/api/v1/roms/systems` | List systems with ROM counts |
| `GET` | `/api/v1/roms/scan` | Trigger ROM directory rescan (admin only) |
| `GET` | `/api/v1/roms/{title_id}` | Download ROM file (supports HTTP Range) |
| `GET` | `/api/v1/roms/{title_id}?extract=cue` | CHD → CUE/BIN ZIP |
| `GET` | `/api/v1/roms/{title_id}?extract=gdi` | CHD → GDI ZIP (Dreamcast) |
| `GET` | `/api/v1/roms/{title_id}?extract=iso` | CHD → ISO (PSP) |
| `GET` | `/api/v1/roms/{title_id}?extract=cso` | CHD → CSO (PSP, requires `ciso`) |
| `GET` | `/api/v1/roms/{title_id}?extract=cia` | 3DS cart image / `*.3ds.zip` → installable CIA |
| `GET` | `/api/v1/roms/{title_id}?extract=decrypted_cia` | 3DS cart image / `*.3ds.zip` → decrypted CIA for emulators |

### Web UI

| Method | Path | Description |
|---|---|---|
| `GET` | `/` | Web library UI (no auth required) |

---

## Storage Layout

```
saves/
  metadata.db             # SQLite — all save metadata
  roms.db                 # SQLite — ROM catalog cache
  <title_id>/
    current/              # active save files
    history/              # previous versions (up to SYNC_MAX_HISTORY_VERSIONS)
```

Title IDs are 16-character uppercase hex for 3DS/NDS (`0004000000055D00`), product codes for PSP/Vita (`ULUS10272`), or `SYSTEM_slug` for emulator saves (`GBA_zelda_the_minish_cap`).

---

## Tests

```bash
uv run pytest tests/ -v

# Specific file or test
uv run pytest tests/test_bundle.py -v
uv run pytest tests/test_api.py::TestUploadEndpoint::test_upload_success -v
```
