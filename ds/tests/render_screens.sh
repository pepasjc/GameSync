#!/bin/sh
# Renders every DS client screen on a PC (tests/render_screens.c) into
# OUT_DIR (default: a temp dir) as PPM, plus 2x PNGs if Python + Pillow are
# available. From the repo root:
#   sh ds/tests/render_screens.sh [OUT_DIR]
set -e
cd "$(dirname "$0")/.."
out=${1:-$(mktemp -d)}
mkdir -p "$out"
version=$(tr -d '[:space:]' < ../VERSION)
gcc -std=gnu11 -Wall -Wextra -Wno-unused-parameter -O1 -g \
    -Itests/host -Iinclude -DAPP_VERSION="\"$version\"" \
    -o "$out/render_screens" \
    tests/render_screens.c source/views.c source/theme.c source/gfx.c source/font_data.c \
    source/ui_log.c
"$out/render_screens" "$out"
for py in python3 python; do
    if command -v $py >/dev/null 2>&1 && $py -c "import PIL" 2>/dev/null; then
        $py - "$out" <<'PY'
import glob, os, sys
from PIL import Image
for p in sorted(glob.glob(os.path.join(sys.argv[1], "*.ppm"))):
    im = Image.open(p)
    im.resize((im.width * 2, im.height * 2), Image.NEAREST).save(p[:-4] + ".png")
PY
        break
    fi
done
echo "screens in $out"
