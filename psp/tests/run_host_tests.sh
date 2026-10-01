#!/bin/sh
# Builds and runs the PSP client's host tests (plain C parts only) with the
# host gcc, e.g. from the repo root:
#   sh psp/tests/run_host_tests.sh
# MinGW/w64devkit works too: SAN=none. Sanitizers: SAN=undefined by default;
# SAN=address,undefined adds ASan; SAN=none for neither.
set -e
cd "$(dirname "$0")/.."
out=$(mktemp -d)
CFLAGS="-std=gnu11 -Wall -Wextra -Wno-unused-parameter -O1 -g -Iinclude"
SAN=${SAN:-undefined}
if [ "$SAN" != "none" ]; then
    CFLAGS="$CFLAGS -fsanitize=$SAN -fno-sanitize-recover=all"
fi

gcc $CFLAGS -o "$out/test_catcache" tests/test_catcache.c source/catcache.c
"$out/test_catcache" "$out"

rm -rf "$out"
