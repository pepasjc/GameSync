#!/bin/bash
# MiSTer Save Sync — syncs /media/fat/saves/ with the 3dssync server
#
# Place in /media/fat/Scripts/ on MiSTer and run from the Scripts menu.
# Requires: curl, sha256sum (both available on stock MiSTer Linux)
# Copy systems.json alongside this script. It is generated from the repo's
# shared definitions so MiSTer folder mappings stay in sync with desktop/server.
#
# Configuration file: /media/fat/3dssync.cfg
# Required keys:
#   SERVER_URL=http://192.168.0.201:8000
#   API_KEY=your_api_key_here
# Optional:
#   SYSTEMS=GBA,SNES,NES   (comma-separated; omit to sync all detected systems)
#   LOG_FILE=/tmp/3dssync.log

set -euo pipefail

CONFIG_FILE="/media/fat/3dssync.cfg"
STATE_FILE="/media/fat/3dssync_state.json"
SAVES_DIR="/media/fat/saves"
SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
SYSTEMS_JSON="${SYSTEMS_JSON:-$SCRIPT_DIR/systems.json}"

# ---------------------------------------------------------------------------
# Load configuration
# ---------------------------------------------------------------------------

if [ ! -f "$CONFIG_FILE" ]; then
    echo "ERROR: Config file not found: $CONFIG_FILE"
    echo "Create $CONFIG_FILE with:"
    echo "  SERVER_URL=http://192.168.0.201:8000"
    echo "  API_KEY=your_api_key_here"
    exit 1
fi

# Source config (simple KEY=VALUE format)
while IFS='=' read -r key value; do
    [[ "$key" =~ ^#.*$ ]] && continue
    [[ -z "$key" ]] && continue
    export "$key"="$value"
done < "$CONFIG_FILE"

: "${SERVER_URL:?SERVER_URL not set in $CONFIG_FILE}"
: "${API_KEY:?API_KEY not set in $CONFIG_FILE}"
LOG_FILE="${LOG_FILE:-/tmp/3dssync.log}"
SYSTEMS_FILTER="${SYSTEMS:-}"

if [ ! -f "$SYSTEMS_JSON" ]; then
    echo "ERROR: systems.json not found: $SYSTEMS_JSON"
    echo "Copy the generated systems.json next to sync_saves.sh, or set SYSTEMS_JSON."
    exit 1
fi

# ---------------------------------------------------------------------------
# MiSTer folder → system code mapping
# ---------------------------------------------------------------------------

mister_folder_to_system() {
    python3 - "$SYSTEMS_JSON" "$1" <<'PY'
import json
import sys

path, folder = sys.argv[1], sys.argv[2]
with open(path, encoding="utf-8") as fh:
    data = json.load(fh)
print(data.get("mister_folder_to_system", {}).get(folder, ""))
PY
}

# ---------------------------------------------------------------------------
# ROM name normalization (minimal version)
# ---------------------------------------------------------------------------

normalize_name() {
    local name="$1"
    # Strip extension
    name="${name%.*}"
    # Lowercase
    name="${name,,}"
    # Strip region/revision tags like (USA), (Europe), (Rev 1), etc.
    name=$(echo "$name" | sed -E 's/[[:space:]]*\([^)]*\)//g')
    # Replace non-alphanumeric with underscore
    name=$(echo "$name" | sed -E 's/[^a-z0-9]+/_/g')
    # Strip leading/trailing underscores and collapse multiples
    name=$(echo "$name" | sed -E 's/_+/_/g' | sed -E 's/^_|_$//g')
    echo "${name:-unknown}"
}

# ---------------------------------------------------------------------------
# State file helpers (JSON: {"title_id": "last_synced_hash", ...})
# ---------------------------------------------------------------------------

state_get() {
    local title_id="$1"
    if [ -f "$STATE_FILE" ]; then
        python3 -c "
import json, sys
data = json.load(open('$STATE_FILE'))
print(data.get('$title_id', ''))
" 2>/dev/null || echo ""
    fi
}

state_set() {
    local title_id="$1"
    local hash="$2"
    python3 -c "
import json, os
path = '$STATE_FILE'
data = json.load(open(path)) if os.path.exists(path) else {}
data['$title_id'] = '$hash'
json.dump(data, open(path, 'w'), indent=2)
" 2>/dev/null
}

# ---------------------------------------------------------------------------
# Should we sync this system?
# ---------------------------------------------------------------------------

should_sync_system() {
    local system="$1"
    if [ -z "$SYSTEMS_FILTER" ]; then
        return 0  # Sync everything
    fi
    echo "$SYSTEMS_FILTER" | tr ',' '\n' | grep -qx "$system"
}

# ---------------------------------------------------------------------------
# Main sync loop
# ---------------------------------------------------------------------------

log() {
    echo "[$(date '+%H:%M:%S')] $*" | tee -a "$LOG_FILE"
}

log "=== MiSTer Save Sync starting ==="
log "Server: $SERVER_URL"

if [ ! -d "$SAVES_DIR" ]; then
    log "ERROR: Saves directory not found: $SAVES_DIR"
    exit 1
fi

synced=0
uploaded=0
downloaded=0
errors=0

for SYSTEM_DIR in "$SAVES_DIR"/*/; do
    FOLDER=$(basename "$SYSTEM_DIR")
    SYSTEM=$(mister_folder_to_system "$FOLDER")

    if [ -z "$SYSTEM" ]; then
        continue
    fi

    if ! should_sync_system "$SYSTEM"; then
        continue
    fi

    log "Scanning $FOLDER -> $SYSTEM"

    for SAVE_FILE in "$SYSTEM_DIR"*.sav "$SYSTEM_DIR"*.srm "$SYSTEM_DIR"*.fs; do
        [ -f "$SAVE_FILE" ] || continue

        GAME=$(basename "$SAVE_FILE")
        SLUG=$(normalize_name "$GAME")
        TITLE_ID="${SYSTEM}_${SLUG}"
        LOCAL_HASH=$(sha256sum "$SAVE_FILE" | cut -d' ' -f1)
        LAST_SYNCED=$(state_get "$TITLE_ID")

        # Get server metadata
        HTTP_CODE=$(curl -s -o /tmp/3dssync_meta.json -w "%{http_code}" \
            -H "X-API-Key: $API_KEY" \
            "$SERVER_URL/api/v1/saves/$TITLE_ID/meta" 2>/dev/null)

        if [ "$HTTP_CODE" = "404" ]; then
            # Not on server — upload
            log "  UPLOAD (new) $TITLE_ID"
            HTTP_UP=$(curl -s -o /dev/null -w "%{http_code}" \
                -X POST \
                -H "X-API-Key: $API_KEY" \
                -H "Content-Type: application/octet-stream" \
                --data-binary "@$SAVE_FILE" \
                "$SERVER_URL/api/v1/saves/$TITLE_ID/raw" 2>/dev/null)
            if [ "$HTTP_UP" = "200" ]; then
                state_set "$TITLE_ID" "$LOCAL_HASH"
                uploaded=$((uploaded + 1))
            else
                log "  ERROR upload failed (HTTP $HTTP_UP)"
                errors=$((errors + 1))
            fi
            continue
        fi

        if [ "$HTTP_CODE" != "200" ]; then
            log "  ERROR fetching metadata for $TITLE_ID (HTTP $HTTP_CODE)"
            errors=$((errors + 1))
            continue
        fi

        SERVER_HASH=$(python3 -c "import json; d=json.load(open('/tmp/3dssync_meta.json')); print(d.get('save_hash',''))" 2>/dev/null)

        # Three-way hash comparison
        if [ "$LOCAL_HASH" = "$SERVER_HASH" ]; then
            # Up to date
            synced=$((synced + 1))
        elif [ -z "$LAST_SYNCED" ]; then
            # No sync history — prefer server (safe default)
            log "  DOWNLOAD (no history) $TITLE_ID"
            HTTP_DL=$(curl -s -o "$SAVE_FILE" -w "%{http_code}" \
                -H "X-API-Key: $API_KEY" \
                "$SERVER_URL/api/v1/saves/$TITLE_ID/raw" 2>/dev/null)
            if [ "$HTTP_DL" = "200" ]; then
                state_set "$TITLE_ID" "$SERVER_HASH"
                downloaded=$((downloaded + 1))
            else
                log "  ERROR download failed (HTTP $HTTP_DL)"
                errors=$((errors + 1))
            fi
        elif [ "$LAST_SYNCED" = "$SERVER_HASH" ]; then
            # Only local changed — upload
            log "  UPLOAD (local newer) $TITLE_ID"
            HTTP_UP=$(curl -s -o /dev/null -w "%{http_code}" \
                -X POST \
                -H "X-API-Key: $API_KEY" \
                -H "Content-Type: application/octet-stream" \
                --data-binary "@$SAVE_FILE" \
                "$SERVER_URL/api/v1/saves/$TITLE_ID/raw" 2>/dev/null)
            if [ "$HTTP_UP" = "200" ]; then
                state_set "$TITLE_ID" "$LOCAL_HASH"
                uploaded=$((uploaded + 1))
            else
                log "  ERROR upload failed (HTTP $HTTP_UP)"
                errors=$((errors + 1))
            fi
        elif [ "$LAST_SYNCED" = "$LOCAL_HASH" ]; then
            # Only server changed — download
            log "  DOWNLOAD (server newer) $TITLE_ID"
            HTTP_DL=$(curl -s -o "$SAVE_FILE" -w "%{http_code}" \
                -H "X-API-Key: $API_KEY" \
                "$SERVER_URL/api/v1/saves/$TITLE_ID/raw" 2>/dev/null)
            if [ "$HTTP_DL" = "200" ]; then
                state_set "$TITLE_ID" "$SERVER_HASH"
                downloaded=$((downloaded + 1))
            else
                log "  ERROR download failed (HTTP $HTTP_DL)"
                errors=$((errors + 1))
            fi
        else
            # Both changed — conflict, skip (server wins by preference — change if desired)
            log "  CONFLICT (skipped) $TITLE_ID local=$LOCAL_HASH server=$SERVER_HASH"
        fi
    done
done

log "=== Done: $uploaded uploaded, $downloaded downloaded, $synced up-to-date, $errors errors ==="
