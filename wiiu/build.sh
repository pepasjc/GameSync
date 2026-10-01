#!/bin/bash
# Build helper — run through devkitPro's MSYS2 login shell:
#   C:/devkitpro/msys2/usr/bin/bash.exe --login <repo>/wiiu/build.sh [clean]
cd "$(dirname "$0")" || exit 1
if [ "$1" = "clean" ]; then
    make clean
fi
make -j4
