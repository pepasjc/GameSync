#!/usr/bin/env bash
# Build the PS2 client from WSL with the ps2dev toolchain on PATH.
# Run: wsl bash /mnt/e/projects/3dssync/ps2/tools/build-wsl.sh [clean]
set -e
export PS2DEV=/usr/local/ps2dev
export PS2SDK="$PS2DEV/ps2sdk"
export PATH="$PS2DEV/bin:$PS2DEV/ee/bin:$PS2DEV/iop/bin:$PS2DEV/dvp/bin:$PS2SDK/bin:$PATH"
cd /mnt/e/projects/3dssync/ps2
if [ "$1" = "clean" ]; then
    make clean
fi
make "${@:2}"
