#!/bin/bash
#
# PS2 SDK install for WSL/Linux.
#
# Builds ps2dev toolchain (ee-gcc, iop-gcc, dvp-gcc + ps2sdk) from
# https://github.com/ps2dev/ps2dev — the official meta-repo that wraps
# binutils/gcc + ps2sdk + ps2sdk-ports + ps2-packages.
#
# Result:
#   $PS2DEV         /usr/local/ps2dev
#   $PS2SDK         $PS2DEV/ps2sdk
#   ee-gcc, iop-gcc, dvp-gcc on PATH
#
# After install, source ~/.bashrc (or open a new shell) and:
#   cd /mnt/e/projects/3dssync/ps2 && make

set -e

PS2DEV="${PS2DEV:-/usr/local/ps2dev}"
PS2SDK="$PS2DEV/ps2sdk"

echo "=== Installing PS2 SDK to $PS2DEV ==="

# 1) Build deps
echo ""
echo "[1/5] Installing build dependencies..."
sudo apt-get update
sudo apt-get install -y build-essential cmake pkg-config git autoconf automake \
    bison flex libncurses-dev texinfo libgmp-dev libmpfr-dev libmpc-dev \
    libelf-dev libreadline-dev libtool libtool-bin wget gawk python3 \
    zip unzip patch

# 2) Owned dir at $PS2DEV (not /opt) so ./configure --prefix can write
echo ""
echo "[2/5] Preparing $PS2DEV..."
sudo mkdir -p "$PS2DEV"
sudo chown -R "$USER":"$USER" "$PS2DEV"

# 3) Clone ps2dev meta-repo
echo ""
echo "[3/5] Cloning ps2dev repo..."
SRC="$HOME/ps2dev-src"
if [ ! -d "$SRC" ]; then
    git clone https://github.com/ps2dev/ps2dev "$SRC"
fi
cd "$SRC"
git pull --ff-only || true

# 4) Build everything (binutils + gcc + newlib + ps2sdk + ports)
#
# build-all.sh respects $PS2DEV / $PS2SDK env vars; export before running
# so the toolchain installs into our owned prefix.
echo ""
echo "[4/5] Building toolchain (slow — 30-90 min)..."
export PS2DEV="$PS2DEV"
export PS2SDK="$PS2SDK"
export PATH="$PS2DEV/bin:$PS2DEV/ee/bin:$PS2DEV/iop/bin:$PS2DEV/dvp/bin:$PS2SDK/bin:$PATH"
export MAKEFLAGS="-j$(nproc)"

# Run scripts individually so we can SKIP 003-ps2sdk-ports.sh.  That
# script builds SDL / lua / madplay etc. and frequently fails on modern
# Ubuntu (autoconf in libtap), but ps2sync needs none of those — only
# the core toolchain (001) and ps2sdk libs / headers (002).
mkdir -p build
cd build
for dep in ../depends/*.sh; do
    [ -e "$dep" ] && bash "$dep"
done

bash ../scripts/001-toolchain.sh
bash ../scripts/002-ps2sdk.sh
# 003-ps2sdk-ports.sh skipped — SDL / lua ports not needed.
bash ../scripts/004-ps2-packer-extra.sh || echo "WARN: 004 failed (optional)"
bash ../scripts/005-ps2client-extra.sh  || echo "WARN: 005 failed (optional)"
cd ..

# 5) (Skipped) ps2sync does not need ps2sdk-ports — ps2ip/netman live in
#     the core ps2sdk built in step 4.  No zlib dep either.
echo ""
echo "[5/5] (skipped — no extra ports required)"

# 6) Persist env vars
echo ""
echo "Persisting PS2DEV in ~/.bashrc..."
if ! grep -q "export PS2DEV=" ~/.bashrc; then
    cat >> ~/.bashrc <<EOF

# PS2 Development Environment
export PS2DEV="$PS2DEV"
export PS2SDK="\$PS2DEV/ps2sdk"
export PATH="\$PS2DEV/bin:\$PS2DEV/ee/bin:\$PS2DEV/iop/bin:\$PS2DEV/dvp/bin:\$PS2SDK/bin:\$PATH"
EOF
fi

echo ""
echo "======================================"
echo "PS2 SDK install complete."
echo ""
echo "Activate in current shell:"
echo "  source ~/.bashrc"
echo ""
echo "Verify:"
echo "  ee-gcc --version"
echo "  ls \$PS2SDK/samples"
echo ""
echo "Build ps2sync:"
echo "  cd /mnt/e/projects/3dssync/ps2 && make"
echo "======================================"
