#!/bin/sh
# Builds and runs the DS client's host tests (plain C parts only) with the
# host gcc. From the repo root, e.g. in a Debian container:
#   docker run --rm -v "$PWD":/src -w /src debian:bookworm-slim sh ds/tests/run_host_tests.sh
# Set CATALOG_JSON to a saved /api/v1/roms?system=NDS response to also parse a
# full real catalog. Sanitizers: SAN=undefined by default; SAN=address,undefined
# adds ASan (older gcc's ASan can spin forever on "DEADLYSIGNAL" on kernels with
# high mmap ASLR entropy, hence not the default); SAN=none for neither. Each run
# is capped by a timeout.
set -e
cd "$(dirname "$0")/.."
if ! command -v gcc >/dev/null 2>&1; then
    apt-get update -qq >/dev/null && apt-get install -y -qq gcc libc6-dev >/dev/null
fi
out=$(mktemp -d)
CFLAGS="-std=gnu11 -Wall -Wextra -Wno-unused-parameter -O1 -g -Iinclude"
SAN=${SAN:-undefined}
if [ "$SAN" != "none" ]; then
    CFLAGS="$CFLAGS -fsanitize=$SAN -fno-sanitize-recover=all"
fi

gcc $CFLAGS -o "$out/test_ra_sets" tests/test_ra_sets.c source/ra_sets.c
timeout 120 "$out/test_ra_sets"

gcc $CFLAGS -o "$out/test_catalog_data" tests/test_catalog_data.c source/catalog_data.c
timeout 120 "$out/test_catalog_data" "$out" ${CATALOG_JSON:+"$CATALOG_JSON"}

rm -rf "$out"
