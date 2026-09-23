#!/bin/bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVICE_USER="${SERVICE_USER:-$(id -un)}"
SERVICE_NAME="${SERVICE_NAME:-3dssync@${SERVICE_USER}}"

echo "=== Reloading 3DSSync Server ==="
echo "Repo: ${SCRIPT_DIR}"
echo "Service: ${SERVICE_NAME}"

cd "${SCRIPT_DIR}"

echo ""
echo "[1/3] Pulling latest changes..."
git pull --ff-only

echo ""
echo "[2/3] Restarting systemd service..."
sudo systemctl restart "${SERVICE_NAME}"

echo ""
echo "[3/3] Service status:"
sudo systemctl --no-pager --full status "${SERVICE_NAME}"
