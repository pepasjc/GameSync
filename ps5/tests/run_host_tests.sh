#!/usr/bin/env bash
# Host tests for the PS5 client's portable modules.
#   bash ps5/tests/run_host_tests.sh
# Needs a C compiler (cc), zlib, and Python with the server's dependencies
# importable (run from an environment where `server/` imports, e.g.
# `cd server && uv run`); set PYTHON to pick the interpreter.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC="$HERE/../src"
OUT="$(mktemp -d)"
trap 'rm -rf "$OUT"' EXIT
PY="${PYTHON:-python3}"

printf '#define APP_VERSION "test"\n' > "$OUT/version.h"
CC="${CC:-cc}"
CFLAGS="-std=c11 -D_DEFAULT_SOURCE -D_GNU_SOURCE -Wall -Wextra -Wno-format-truncation -O1 -g -I$SRC -I$OUT"

$CC $CFLAGS -o "$OUT/test_ps2mc" "$HERE/test_ps2mc.c" "$SRC/ps2mc.c" "$SRC/util.c" "$SRC/sha256.c"
"$PY" "$HERE/ps2mc_fixture.py" make "$OUT/server.ps2" "$OUT/expected.txt"
"$OUT/test_ps2mc" "$OUT/server.ps2" "$OUT/expected.txt" "$OUT/client.ps2"
"$PY" "$HERE/ps2mc_fixture.py" check "$OUT/client.ps2"

$CC $CFLAGS -o "$OUT/test_bundle" "$HERE/test_bundle.c" "$SRC/bundle.c" "$SRC/util.c" \
    "$SRC/sha256.c" -lz
mkdir -p "$OUT/tree/sub"
printf 'banner' > "$OUT/tree/banner.bin"
printf 'data-a' > "$OUT/tree/sub/a.dat"
printf 'x' > "$OUT/tree/a-b"
mkdir -p "$OUT/tree/nocopy" && printf 'skip' > "$OUT/tree/nocopy/n.bin"
"$OUT/test_bundle" make "$OUT/tree" "$OUT/client.3dss" > "$OUT/client_hash.txt"
"$PY" "$HERE/bundle_fixture.py" check "$OUT/client.3dss" "$(cat "$OUT/client_hash.txt")"
"$PY" "$HERE/bundle_fixture.py" make "$OUT/server.3dss" > "$OUT/server_hash.txt"
"$OUT/test_bundle" parse "$OUT/server.3dss" "$(cat "$OUT/server_hash.txt")" "$OUT/written"

$CC $CFLAGS -o "$OUT/test_xbox_hdd" "$HERE/test_xbox_hdd.c" "$SRC/xbox_hdd.c" "$SRC/bundle.c" \
    "$SRC/util.c" "$SRC/sha256.c" -lz
"$PY" "$HERE/xbox_hdd_fixture.py" make "$OUT/xbox_hdd.qcow2"
"$OUT/test_xbox_hdd" "$OUT/xbox_hdd.qcow2" > "$OUT/xbox_hdd.txt"
"$PY" "$HERE/xbox_hdd_fixture.py" check "$OUT/xbox_hdd.txt"
"$PY" "$HERE/xbox_hdd_fixture.py" check-image "$OUT/xbox_hdd.qcow2"

$CC $CFLAGS -o "$OUT/test_unzip" "$HERE/test_unzip.c" "$SRC/unzip.c" "$SRC/util.c" "$SRC/sha256.c"
"$PY" - "$OUT/set.zip" <<'PY'
import sys, zipfile
with zipfile.ZipFile(sys.argv[1], "w", zipfile.ZIP_STORED, allowZip64=True) as zf:
    zf.writestr("Game (USA).cue", 'FILE "Game (USA) (Track 1).bin" BINARY\n  TRACK 01 MODE1/2352\n    INDEX 01 00:00:00\n')
    zf.writestr("Game (USA) (Track 1).bin", bytes(2352 * 300))
PY
"$OUT/test_unzip" "$OUT/set.zip" "$OUT/set"
"$PY" - "$OUT/evil.zip" <<'PY'
import sys, zipfile
with zipfile.ZipFile(sys.argv[1], "w", zipfile.ZIP_STORED) as zf:
    zf.writestr("../escape.txt", b"x")
PY
if "$OUT/test_unzip" "$OUT/evil.zip" "$OUT/evil" 2>/dev/null; then echo "traversal not refused"; exit 1; fi

$CC $CFLAGS -o "$OUT/test_logic" "$HERE/test_logic.c" "$SRC/json.c" "$SRC/naming.c" \
    "$SRC/http.c" "$SRC/sync.c" "$SRC/saves.c" "$SRC/retroarch.c" "$SRC/bundle.c" \
    "$SRC/config.c" "$SRC/ps2mc.c" "$SRC/saturn.c" "$SRC/xbox_hdd.c" "$SRC/melonds.c" "$SRC/installed.c" "$SRC/xcci.c" "$SRC/fetch.c" "$SRC/helper_client.c" "$SRC/catalog.c" "$SRC/unzip.c" "$SRC/util.c" "$SRC/sha256.c" -lz
# the GameIndex.yaml download, against a stand-in for the server's route
head -c 300000 /dev/urandom > "$OUT/GameIndex.yaml"
"$PY" "$HERE/game_index_fixture.py" "$OUT/port" "$OUT/GameIndex.yaml" &
FIXTURE=$!
trap 'kill $FIXTURE 2>/dev/null; rm -rf "$OUT"' EXIT
for _ in $(seq 50); do [ -s "$OUT/port" ] && break; sleep 0.1; done
"$OUT/test_logic" "http://127.0.0.1:$(cat "$OUT/port")" "$OUT/GameIndex.yaml"

echo "all host tests passed"
