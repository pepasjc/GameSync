#!/bin/bash
# Build CIA for 3DS Save Sync
# Requires: makerom, bannertool (download from GitHub releases)
#   - makerom: https://github.com/3DSGuy/Project_CTR/releases
#   - bannertool: https://github.com/Steveice10/bannertool/releases
# Place both executables in this directory or in PATH

set -e

# Check for required tools
if ! command -v makerom &> /dev/null; then
    if [ -f "./makerom.exe" ]; then
        MAKEROM="./makerom.exe"
    elif [ -f "./makerom" ]; then
        MAKEROM="./makerom"
    else
        echo "Error: makerom not found!"
        echo "Download from: https://github.com/3DSGuy/Project_CTR/releases"
        exit 1
    fi
else
    MAKEROM="makerom"
fi

if ! command -v bannertool &> /dev/null; then
    if [ -f "./bannertool.exe" ]; then
        BANNERTOOL="./bannertool.exe"
    elif [ -f "./bannertool" ]; then
        BANNERTOOL="./bannertool"
    else
        echo "Error: bannertool not found!"
        echo "Download from: https://github.com/Steveice10/bannertool/releases"
        exit 1
    fi
else
    BANNERTOOL="bannertool"
fi

# Build the .3dsx first (which also creates .elf and .smdh)
echo "Building 3dsx..."
make

# Check that required files exist
if [ ! -f "3dssync.elf" ]; then
    echo "Error: 3dssync.elf not found. Run 'make' first."
    exit 1
fi

if [ ! -f "3dssync.smdh" ]; then
    echo "Error: 3dssync.smdh not found."
    exit 1
fi

# Create banner (uses icon if available, otherwise creates blank)
echo "Creating banner..."
if [ -f "icon.png" ]; then
    $BANNERTOOL makebanner -i icon.png -a audio.wav -o banner.bnr 2>/dev/null || \
    $BANNERTOOL makebanner -i icon.png -o banner.bnr
elif [ -f "3dssync.png" ]; then
    $BANNERTOOL makebanner -i 3dssync.png -a audio.wav -o banner.bnr 2>/dev/null || \
    $BANNERTOOL makebanner -i 3dssync.png -o banner.bnr
else
    # Create a simple blank banner
    echo "No icon.png found, creating minimal banner..."
    # bannertool needs an image, so we'll skip the banner for now
    echo "Warning: No icon found. Please add icon.png for proper banner."
fi

# Build CIA
echo "Building CIA..."
$MAKEROM -f cia -o 3dssync.cia -elf 3dssync.elf -rsf cia.rsf -icon 3dssync.smdh -banner banner.bnr

if [ -f "3dssync.cia" ]; then
    echo ""
    echo "Success! Created 3dssync.cia"
    echo "Install with FBI or other CIA installer on your 3DS."
else
    echo "Error: CIA creation failed."
    exit 1
fi
