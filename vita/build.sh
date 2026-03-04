#!/bin/bash
# Build VPK for Vita Save Sync

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VITA_DIR="$SCRIPT_DIR"

if [ ! -d "$VITA_DIR" ]; then
    echo "Error: vita directory not found at $VITA_DIR"
    exit 1
fi

if [ -z "$VITASDK" ]; then
    echo "Error: VITASDK environment variable not set!"
    echo "Install VitaSDK: https://vitasdk.org/"
    exit 1
fi

ZLIB_DIR="$VITA_DIR/source/zlib"
if [ ! -d "$ZLIB_DIR" ] || [ ! -f "$ZLIB_DIR/zlib.h" ]; then
    echo "Downloading zlib source..."
    mkdir -p "$ZLIB_DIR"
    cd "$VITA_DIR"
    curl -sL https://zlib.net/zlib-1.3.2.tar.gz | tar xz --strip-components=1 -C "$ZLIB_DIR"
fi

BUILD_DIR="$VITA_DIR/build"

if [ -d "$BUILD_DIR" ]; then
    echo "Cleaning existing build directory..."
    rm -rf "$BUILD_DIR"
fi

echo "Creating build directory..."
mkdir -p "$BUILD_DIR"

echo "Running CMake..."
cmake -S "$VITA_DIR" -B "$BUILD_DIR"

echo "Building..."
make -C "$BUILD_DIR"

if [ -f "$BUILD_DIR/vitasync.vpk" ]; then
    echo ""
    echo "Success! Created vitasync.vpk"
    echo "Install via VitaShell (ftp or QCMA)"
else
    echo "Error: VPK creation failed."
    exit 1
fi
