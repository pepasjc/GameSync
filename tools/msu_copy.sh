#!/usr/bin/env bash
# Copy the packs chosen by msu_pick.py to the server, renaming as planned.
#
#   wsl bash /mnt/e/projects/3dssync/tools/msu_copy.sh <plan.txt> <user@host> <remote dir>
#
# The plan is msu_pick.py's output: one "<source path>\t<destination name>" per
# line, Windows paths.  Each file goes through rsync with --partial so an
# interrupted 1 GB pack resumes instead of restarting.  A file already on the
# server with the same size is skipped (rsync's default quick check).
set -u

plan="$1"; host="$2"; remote="$3"
: "${SSH_CTL:=/tmp/msu_copy_ctl}"

# One SSH connection for the whole run: 200 handshakes to a Pi add up.
ssh -o ControlMaster=yes -o ControlPath="$SSH_CTL" -o ControlPersist=600 -fN "$host"
trap 'ssh -o ControlPath="$SSH_CTL" -O exit "$host" 2>/dev/null' EXIT
ssh -o ControlPath="$SSH_CTL" "$host" "mkdir -p '$remote'"

total=$(grep -c . "$plan"); n=0; failed=0
while IFS=$'\t' read -r src dest; do
    [ -z "$src" ] && continue
    n=$((n + 1))
    # C:\x  ->  /mnt/c/x
    src=${src//\\//}
    drive=$(printf '%s' "$src" | cut -c1 | tr 'A-Z' 'a-z')
    wsl_src="/mnt/$drive${src:2}"
    printf '[%d/%d] %s\n' "$n" "$total" "$dest"
    if ! rsync --partial --inplace --times --info=progress2 \
            -e "ssh -o ControlPath=$SSH_CTL" \
            "$wsl_src" "$host:$remote/$dest"; then
        failed=$((failed + 1))
        printf '  FAILED: %s\n' "$src" >&2
    fi
done < <(tr -d '\r' < "$plan")

printf 'done: %d files, %d failed\n' "$n" "$failed"
[ "$failed" -eq 0 ]
