#!/usr/bin/env python3
"""Compare every local MiSTer save against the server's copy and say where
they differ. Run on the device against the installed package:

    python3 diff_saves.py [/media/fat/Scripts/.gamesync]

Read-only. Written to chase saves that report "upload" after a session in
which nothing was saved.
"""

import glob
import hashlib
import os
import sys

pkg = sys.argv[1] if len(sys.argv) > 1 else "/media/fat/Scripts/.gamesync"
candidates = [pkg] + glob.glob(os.path.join(pkg, "*.pyz"))
for candidate in candidates:
    if candidate not in sys.path:
        sys.path.insert(0, candidate)

from gamesync import api as gsapi          # noqa: E402
from gamesync import config as gsconfig    # noqa: E402
from gamesync.sync import SyncEngine       # noqa: E402
from shared import mister_saves            # noqa: E402

config = gsconfig.load_config()
client = gsapi.Client(config.base_url, config.api_key)
engine = SyncEngine(config, client=client)
entries = engine.scan()
state = engine.state

only = set(sys.argv[2:])
for entry in entries:
    if only and entry.system not in only:
        continue
    if not entry.exists or not entry.path:
        continue
    try:
        remote = client.download_save(entry.title_id, system=entry.system)
    except Exception as exc:
        print("%-7s %-45s %s  FETCH FAILED: %s" % (
            entry.system, entry.name[:45], entry.title_id, exc))
        continue
    if remote is None:
        print("%-7s %-45s %s  server has none" % (
            entry.system, entry.name[:45], entry.title_id))
        continue
    local = engine.provider.read(entry.path)
    if entry.system == "SAT":
        local = mister_saves.resolve_save_identity(entry.system, local).hash_payload
    elif entry.system == "MD":
        local = mister_saves.md_from_mister(local, target_size=len(remote))
    same = mister_saves.same_content(entry.system, local, remote)
    where = "" if local == remote else mister_saves.describe_difference(
        entry.system, local, remote, limit=8)
    last = state.get(entry.title_id, "")
    print("%-7s %-45s %s" % (entry.system, entry.name[:45], entry.title_id))
    print("        local  %s  mtime %s" % (
        hashlib.sha256(local).hexdigest()[:16],
        __import__("time").strftime("%m-%d %H:%M",
                                    __import__("time").localtime(entry.mtime))))
    print("        server %s   last-synced %s" % (
        hashlib.sha256(remote).hexdigest()[:16], last[:16] or "-"))
    print("        %s%s" % ("IDENTICAL" if local == remote else
                            ("housekeeping only" if same else "DIFFERS: "),
                            where if not same else ""))
    if entry.system == "PS1" and local != remote:
        # Every differing byte inside block 0, for the record.
        diffs = [i for i in range(8192) if local[i] != remote[i]]
        if diffs:
            print("        block0 byte offsets: %s%s" % (
                diffs[:24], " ..." if len(diffs) > 24 else ""))
            for i in diffs[:12]:
                print("          0x%04X local %02X server %02X" % (
                    i, local[i], remote[i]))
        print("        first dir entry local  %r" % local[128:128 + 32])
        print("        first dir entry server %r" % remote[128:128 + 32])
        print("        frame63 local  %r" % local[8064:8064 + 8])
        print("        frame63 server %r" % remote[8064:8064 + 8])
