#!/bin/bash
# Build helper — run through devkitPro's MSYS2 login shell:
#   C:/devkitpro/msys2/usr/bin/bash.exe --login /e/projects/3dssync/wiiu/build.sh [clean]
cd /e/projects/3dssync/wiiu || exit 1
if [ "$1" = "clean" ]; then
    make clean
fi
make -j4
