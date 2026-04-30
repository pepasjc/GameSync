#!/usr/bin/env bash
# Build the Xbox client. Run from WSL (or any nxdk-compatible POSIX shell).
#
# Usage:
#     bash xbox/build.sh                # equivalent to `make`
#     bash xbox/build.sh clean
#     bash xbox/build.sh iso            # build XISO for emulator/burning
set -euo pipefail

NXDK_DIR="${NXDK_DIR:-$HOME/nxdk}"
if [ ! -f "$NXDK_DIR/Makefile" ]; then
    echo "nxdk not found at $NXDK_DIR. Set NXDK_DIR env var or clone nxdk." >&2
    exit 1
fi

export NXDK_DIR
export PATH="$NXDK_DIR/bin:$PATH"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

exec make "$@"
