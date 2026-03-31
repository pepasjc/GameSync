#!/bin/bash
# SaveSync launcher for Steam Deck
# Add this script as a non-Steam game in Steam.
#   Target: /bin/bash
#   Launch Options: /home/deck/3dssync/steamdeck/launch.sh

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(dirname "$SCRIPT_DIR")"

# Pull latest changes
cd "$REPO_DIR" && git pull --ff-only 2>&1 | head -20

# Launch the client
cd "$SCRIPT_DIR" && exec uv run python3 main.py
