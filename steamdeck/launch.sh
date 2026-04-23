#!/bin/bash
# SaveSync launcher for Steam Deck
# Add this script as a non-Steam game in Steam.
#   Target: /bin/bash
#   Launch Options: -lc '"/home/deck/3dssync/steamdeck/launch.sh"'
#
# Optional: pin to a specific branch by passing its name as an argument:
#   Launch Options: -lc '"/home/deck/3dssync/steamdeck/launch.sh" v052'
# The launcher will `git fetch` + `git checkout <branch>` + `git pull --ff-only`
# before starting the client.  Omit the argument to stay on the current branch.

set -u

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(dirname "$SCRIPT_DIR")"
LOG_DIR="$SCRIPT_DIR/logs"
LOG_FILE="$LOG_DIR/launch.log"
BRANCH="${1:-}"

mkdir -p "$LOG_DIR"

# Gaming Mode can have a thinner PATH than Desktop Mode.
export HOME="${HOME:-/home/deck}"
export PATH="$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin:$PATH"

exec >"$LOG_FILE" 2>&1

echo "==== SaveSync launcher ===="
date
echo "SCRIPT_DIR=$SCRIPT_DIR"
echo "REPO_DIR=$REPO_DIR"
echo "PATH=$PATH"
echo "BRANCH=${BRANCH:-<current>}"

find_cmd() {
    local name="$1"
    if command -v "$name" >/dev/null 2>&1; then
        command -v "$name"
        return 0
    fi
    return 1
}

GIT_BIN="$(find_cmd git || true)"
UV_BIN="$(find_cmd uv || true)"

if [ -z "$GIT_BIN" ]; then
    echo "git not found"
    exit 1
fi

if [ -z "$UV_BIN" ]; then
    echo "uv not found"
    exit 1
fi

echo "Using git: $GIT_BIN"
echo "Using uv: $UV_BIN"

cd "$REPO_DIR" || exit 1

if [ -n "$BRANCH" ]; then
    echo "Fetching origin (for branch switch)"
    "$GIT_BIN" fetch origin || echo "git fetch failed; continuing with local branches"
    echo "Checking out $BRANCH"
    if ! "$GIT_BIN" checkout "$BRANCH"; then
        echo "git checkout failed; staying on $("$GIT_BIN" rev-parse --abbrev-ref HEAD)"
    fi
fi

echo "Running git pull --ff-only"
"$GIT_BIN" pull --ff-only || echo "git pull failed; continuing with local checkout"

cd "$SCRIPT_DIR" || exit 1
echo "Launching client"
exec "$UV_BIN" run python3 main.py
