#!/bin/sh
# Builds and runs the DS client's host tests (plain C parts only) with the
# host gcc. From the repo root, e.g. in a Debian container:
#   docker run --rm -v "$PWD":/src -w /src debian:bookworm-slim sh ds/tests/run_host_tests.sh
set -e
cd "$(dirname "$0")/.."
if ! command -v gcc >/dev/null 2>&1; then
    apt-get update -qq >/dev/null && apt-get install -y -qq gcc libc6-dev >/dev/null
fi
out=$(mktemp -d)
CFLAGS="-std=gnu11 -Wall -Wextra -Wno-unused-parameter -O1 -g -fsanitize=address,undefined -Iinclude"

gcc $CFLAGS -o "$out/test_ra_sets" tests/test_ra_sets.c source/ra_sets.c
"$out/test_ra_sets"

if [ -f tests/test_catalog_data.c ]; then
    gcc $CFLAGS -o "$out/test_catalog_data" tests/test_catalog_data.c source/catalog_data.c
    "$out/test_catalog_data" "$out"
fi
rm -rf "$out"
