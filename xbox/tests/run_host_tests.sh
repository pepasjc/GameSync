#!/bin/sh
# Builds and runs the Xbox client's host tests (plain C parts only) with the
# host gcc, e.g. from WSL:
#   sh xbox/tests/run_host_tests.sh
# Set CATALOG_JSON to a saved /api/v1/roms?system=XBOX response to also parse
# a real catalog page. Sanitizers: SAN=undefined by default; SAN=address,undefined
# adds ASan; SAN=none for neither.
set -e
cd "$(dirname "$0")/.."
out=$(mktemp -d)
CFLAGS="-std=gnu11 -Wall -Wextra -O1 -g -Isource"
SAN=${SAN:-undefined}
if [ "$SAN" != "none" ]; then
    CFLAGS="$CFLAGS -fsanitize=$SAN -fno-sanitize-recover=all"
fi

gcc $CFLAGS -o "$out/test_catalog_cache" tests/test_catalog_cache.c source/catalog_cache.c
"$out/test_catalog_cache" ${CATALOG_JSON:+"$CATALOG_JSON"}

rm -rf "$out"
