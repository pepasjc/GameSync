#!/bin/bash
#
# Restic backup script for 3DSSync save data.
# Reads B2 credentials and restic passphrase from .env.backup in the same dir.
# (Kept separate from .env so server config and backup creds are independent.)
#
# Cron example (nightly at 03:00):
#   0 3 * * * /home/pi/3dssync/server/backup_saves.sh
#

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${SCRIPT_DIR}/.env.backup"
LOG_FILE="${SCRIPT_DIR}/restic-backup.log"

# --- load .env ---------------------------------------------------------------
if [ ! -f "${ENV_FILE}" ]; then
    echo "Error: ${ENV_FILE} not found" >&2
    exit 1
fi

set -a
# shellcheck disable=SC1090
source "${ENV_FILE}"
set +a

# --- required vars -----------------------------------------------------------
: "${B2_ACCOUNT_ID:?B2_ACCOUNT_ID not set in .env}"
: "${B2_ACCOUNT_KEY:?B2_ACCOUNT_KEY not set in .env}"
: "${RESTIC_REPOSITORY:?RESTIC_REPOSITORY not set in .env (e.g. b2:my-bucket:)}"
: "${RESTIC_PASSWORD:?RESTIC_PASSWORD not set in .env}"

# --- optional vars (with defaults) -------------------------------------------
BACKUP_PATH="${BACKUP_PATH:-/home/pi/Documents/3ds_sync}"
KEEP_DAILY="${KEEP_DAILY:-7}"
KEEP_WEEKLY="${KEEP_WEEKLY:-4}"
KEEP_MONTHLY="${KEEP_MONTHLY:-6}"

# --- run ---------------------------------------------------------------------
{
    echo ""
    echo "=== Backup started: $(date -Iseconds) ==="
    echo "Source:     ${BACKUP_PATH}"
    echo "Repository: ${RESTIC_REPOSITORY}"

    if [ ! -d "${BACKUP_PATH}" ]; then
        echo "Error: backup source ${BACKUP_PATH} does not exist" >&2
        exit 1
    fi

    echo ""
    echo "[1/2] restic backup..."
    restic backup "${BACKUP_PATH}" --tag nightly --host "$(hostname)"

    echo ""
    echo "[2/2] restic forget --prune (retention: ${KEEP_DAILY}d / ${KEEP_WEEKLY}w / ${KEEP_MONTHLY}m)..."
    restic forget \
        --keep-daily "${KEEP_DAILY}" \
        --keep-weekly "${KEEP_WEEKLY}" \
        --keep-monthly "${KEEP_MONTHLY}" \
        --prune

    echo "=== Backup finished: $(date -Iseconds) ==="
} >> "${LOG_FILE}" 2>&1
