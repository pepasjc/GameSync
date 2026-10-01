#!/bin/sh
# Starts this repo's server on a fixture library and runs e2e_catalog.c (the
# DS http.c + catalog_data.c, built for the host) against it. From the repo
# root, e.g.:
#   docker run --rm -v "$PWD":/src -w /src python:3.12-slim sh ds/tests/run_e2e.sh
# Works on copies in /tmp so nothing is written into the checkout.
set -e
root=$(cd "$(dirname "$0")/../.." && pwd)
if ! command -v gcc >/dev/null 2>&1; then
    apt-get update -qq >/dev/null && apt-get install -y -qq gcc libc6-dev >/dev/null
fi
pip install -q fastapi 'uvicorn[standard]' python-multipart pydantic-settings pillow httpx >/dev/null 2>&1

work=$(mktemp -d)
cp -r "$root/server" "$root/shared" "$work/"
rm -rf "$work/server/.venv" "$work/server/.env"
mkdir -p "$work/roms/nds" "$work/saves" "$work/out"

# Fixture: a 12 MB "ROM" (random + zero runs, so DEFLATE has work to do)
python3 - "$work" <<'EOF'
import os, sys, zipfile
work = sys.argv[1]
rom = bytearray()
while len(rom) < 12 * 1024 * 1024:
    rom += os.urandom(64 * 1024) + bytes(192 * 1024)
open(f"{work}/expected.nds", "wb").write(rom)
with zipfile.ZipFile(f"{work}/roms/nds/Alpha Quest (USA).zip", "w", zipfile.ZIP_DEFLATED) as z:
    z.writestr("Alpha Quest (USA).nds", bytes(rom))
with zipfile.ZipFile(f"{work}/roms/nds/Beta Twin (USA).zip", "w") as z:
    z.writestr("Beta Twin (USA).nds", b"a" * 1000)
    z.writestr("Beta Twin (USA) (Rev 1).nds", b"b" * 1000)
open(f"{work}/roms/nds/Gamma Loose (USA).nds", "wb").write(b"g" * 5000)
EOF

key=e2e-test-key-5f1c9a
cd "$work/server"
SYNC_API_KEY=$key SYNC_ROM_DIR="$work/roms" SYNC_SAVE_DIR="$work/saves" \
    python3 -m uvicorn app.main:app --host 127.0.0.1 --port 8765 --log-level warning &
server=$!
trap 'kill $server 2>/dev/null' EXIT
for i in $(seq 1 60); do
    python3 -c "import urllib.request as u; u.urlopen(u.Request('http://127.0.0.1:8765/api/v1/roms/scan', headers={'X-API-Key': '$key'}))" 2>/dev/null && break
    sleep 1
done

cd "$root/ds"
gcc -std=gnu11 -Wall -Wextra -Wno-unused-parameter -O1 -g -Iinclude \
    -Diprintf=printf -Dclosesocket=close \
    -o "$work/e2e" tests/e2e_catalog.c source/http.c source/catalog_data.c source/catalog_cache.c
timeout 300 "$work/e2e" http://127.0.0.1:8765 "$key" "$work/expected.nds" "$work/out"
