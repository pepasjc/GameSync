#!/usr/bin/env python3
"""Audit the server's save store for inconsistencies.

Read-only. It never writes, moves or deletes anything - it reports, and the
fixing is a separate decision.

    python3 tools/audit_saves.py                     # uses SYNC_SAVE_DIR or ./saves
    python3 tools/audit_saves.py --save-dir /path/to/saves
    python3 tools/audit_saves.py --json report.json
    python3 tools/audit_saves.py --check hash,orphan # only some checks

What it looks for
-----------------
``missing``     a row in the database with no files on disk
``orphan``      a save directory on disk that the database does not know about
``empty``       a save whose files are all zero bytes, or which has none
``size``        ``save_size`` disagreeing with the bytes actually stored
``count``       ``file_count`` disagreeing with the files actually stored
``hash``        ``save_hash`` disagreeing with a recomputed hash
``title_id``    an identifier that does not match any known form
``duplicate``   one game stored twice under different identifiers
``slug``        a serial-keyed system (PS1, Saturn, ...) stored under a
                name-derived slug instead of its serial
``temp``        a leftover ``.part``/``.tmp`` from an interrupted write
``history``     malformed or excessive history snapshots

On hashing: the server hashes the concatenated file data in *bundle order*,
which cannot be recovered from disk once a save has more than one file, and
PS1 and PS3 hash a filtered subset under their own rules. So hashes are only
verified where the answer is unambiguous - single-file saves that are neither
PS1 nor PS3 - and everything else is counted as "not verified" rather than
being reported as corrupt. An audit that cries wolf is worse than no audit.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import sys
from collections import defaultdict

DEFAULT_MAX_HISTORY = 10

# Identifier shapes, mirroring server/app/models/save.py.
_HEX16_RE = re.compile(r"^[0-9A-F]{16}$")
_PRODUCT_CODE_RE = re.compile(r"^[A-Z0-9]{4,31}$")
_SAVE_DIR_TITLE_ID_RE = re.compile(r"^[A-Z]{4}\d{5}[A-Z0-9._-]{0,54}$")
_EMULATOR_TITLE_ID_RE = re.compile(r"^[A-Z0-9]{2,8}_[A-Za-z0-9][A-Za-z0-9_-]{0,99}$")
#: Only the lowercase-slug form counts as "named after the game" for the
#: duplicate and slug checks; SAT_T-14410G is a serial, not a name.
_SYSTEM_SLUG_RE = re.compile(r"^[A-Z0-9]+_[a-z0-9_]+$")
_SERIAL_RE = re.compile(r"^[A-Z]{2,5}[-_]?[0-9]{3,6}[A-Z0-9]*$", re.IGNORECASE)

def _serial_systems():
    """Systems keyed by a disc serial, from the shared rules when available.

    Guessing this wrongly produces confident nonsense - an early version of
    this script listed SEGACD, which is slug-keyed, and reported six healthy
    saves as misfiled.
    """
    try:
        from shared.systems import SYNC_ID_RULES

        return {system for system, rule in SYNC_ID_RULES.items()
                if rule.get("strategy") == "serial"}
    except Exception:
        return {"PS1", "PS2", "PSP", "VITA", "SAT", "GC"}


SERIAL_SYSTEMS = _serial_systems()

#: The server computes a save's hash, size and file count three different ways
#: depending on the title, so the audit borrows its actual implementations
#: rather than guessing. Run this under the server's virtualenv
#: (``uv run python tools/audit_saves.py``) to get the exact comparison; with
#: the bare system Python these are unavailable and those checks are skipped
#: rather than reported wrongly.
#:
#: Guessing here has already produced two rounds of false alarms: PARAM.SFO
#: looked like a reliable "this is a PS3 save" marker, but PSP saves carry one
#: too, so a healthy 4-file PSP save was reported as having 1.
SERVER_RULES = {}


def _load_server_rules():
    # The script may be run from anywhere (including /tmp during development),
    # so make sure the server package is importable before trying.
    here = os.path.dirname(os.path.abspath(__file__))
    for candidate in (os.path.dirname(here), os.getcwd()):
        if os.path.isdir(os.path.join(candidate, "app")) and candidate not in sys.path:
            sys.path.insert(0, candidate)
    try:
        from app.services.game_names import detect_platform
        from app.services.ps1_cards import is_ps1_title_id, psp_visible_stats
        from app.services.storage import _ps3_visible_stats
    except Exception:
        return {}
    return {"is_ps1": is_ps1_title_id, "ps1_stats": psp_visible_stats,
            "ps3_stats": _ps3_visible_stats, "platform": detect_platform}


def _expected_stats(title_id, row, current, files):
    """(hash, size, count) the server would record, or None when unknowable.

    ``hash`` is None for a multi-file save under the generic rule: the server
    concatenates in *bundle order*, which the filesystem cannot tell us.
    """
    if not SERVER_RULES:
        return None

    # Which rule applies is decided from the *title id*, exactly as
    # storage.py does. The stored system column is not the same thing: three
    # saves on the live server say "PSP" but were uploaded by a PS3 client,
    # and trusting the column reported all three as corrupt.
    payload = []
    for name, _size in files:
        try:
            with open(os.path.join(current, name), "rb") as handle:
                payload.append((name, handle.read()))
        except OSError:
            return None

    if SERVER_RULES["is_ps1"](title_id):
        return SERVER_RULES["ps1_stats"](payload)
    if SERVER_RULES["platform"](title_id) == "PS3":
        return SERVER_RULES["ps3_stats"](payload)

    total = sum(len(data) for _name, data in payload)
    if len(payload) == 1:
        return (hashlib.sha256(payload[0][1]).hexdigest(), total, 1)
    return (None, total, len(payload))

SEVERITY = {
    "missing": "error",
    "orphan": "warning",
    "empty": "warning",
    "size": "error",
    "count": "error",
    "hash": "error",
    "title_id": "warning",
    "duplicate": "warning",
    "slug": "warning",
    "temp": "warning",
    "history": "warning",
}

ALL_CHECKS = tuple(SEVERITY)


class Finding:
    __slots__ = ("check", "title_id", "detail")

    def __init__(self, check, title_id, detail):
        self.check = check
        self.title_id = title_id
        self.detail = detail

    def as_dict(self):
        return {"check": self.check, "severity": SEVERITY.get(self.check, "info"),
                "title_id": self.title_id, "detail": self.detail}


def human(size):
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return "%.0f %s" % (size, unit)
        size /= 1024.0
    return "%d B" % size


def normalize_name(name):
    """A loose key for "is this the same game?".

    Deliberately cruder than the real slug rules: it drops every parenthetical
    and bracketed tag, so regional and revision variants collapse together.
    That is what makes it useful for spotting one game stored twice.
    """
    text = re.sub(r"\([^)]*\)|\[[^\]]*\]", " ", str(name or ""))
    text = re.sub(r"\.[A-Za-z0-9]{1,5}$", "", text.strip())
    text = re.sub(r"[^a-z0-9]+", "_", text.lower())
    return re.sub(r"_+", "_", text).strip("_")


def looks_like_serial(title_id):
    return bool(_SERIAL_RE.match(title_id or ""))


_VALIDATOR = None


def valid_title_id(title_id):
    """Defer to the server's own rule so the audit cannot disagree with it.

    The server accepts far more forms than an obvious guess would - 8-char hex
    for original Xbox, bare product codes, PS save-directory ids - and guessing
    produced 263 false warnings before this used the real validator.
    """
    global _VALIDATOR
    if _VALIDATOR is None:
        try:
            from app.models.save import validate_any_title_id

            _VALIDATOR = validate_any_title_id
        except Exception:
            _VALIDATOR = False

    if not title_id:
        return False
    if _VALIDATOR:
        try:
            _VALIDATOR(title_id)
            return True
        except Exception:
            return False
    upper = title_id.strip().upper()
    return bool(_HEX16_RE.match(upper) or _PRODUCT_CODE_RE.match(upper)
                or _SAVE_DIR_TITLE_ID_RE.match(upper)
                or _EMULATOR_TITLE_ID_RE.match(title_id.strip()))


def read_rows(db_path):
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    try:
        return [dict(row) for row in connection.execute("SELECT * FROM saves")]
    finally:
        connection.close()


def scan_files(directory):
    """Every file under a directory as ``(relative path, size)``, sorted."""
    found = []
    for base, _dirs, files in os.walk(directory):
        for name in files:
            full = os.path.join(base, name)
            relative = os.path.relpath(full, directory).replace(os.sep, "/")
            try:
                found.append((relative, os.path.getsize(full)))
            except OSError:
                found.append((relative, -1))
    return sorted(found)


def audit(save_dir, checks, max_history=DEFAULT_MAX_HISTORY, verbose=False):
    db_path = os.path.join(save_dir, "metadata.db")
    if not os.path.exists(db_path):
        raise SystemExit("no metadata.db in %s" % save_dir)

    rows = read_rows(db_path)
    by_id = {row["title_id"]: row for row in rows}
    findings = []
    stats = {"rows": len(rows), "verified_hashes": 0, "unverifiable_hashes": 0,
             "total_bytes": 0}

    on_disk = set()
    for entry in sorted(os.listdir(save_dir)):
        full = os.path.join(save_dir, entry)
        if os.path.isdir(full) and entry not in ("__pycache__",):
            on_disk.add(entry)

    for title_id, row in sorted(by_id.items()):
        directory = os.path.join(save_dir, title_id)
        current = os.path.join(directory, "current")

        if "title_id" in checks and not valid_title_id(title_id):
            findings.append(Finding("title_id", title_id,
                                    "unrecognised identifier form"))

        if not os.path.isdir(current):
            if "missing" in checks:
                findings.append(Finding(
                    "missing", title_id,
                    "no current/ directory (name=%r system=%r)"
                    % (row.get("name"), row.get("system"))))
            continue

        files = scan_files(current)
        stats["total_bytes"] += sum(size for _n, size in files if size > 0)
        expected = _expected_stats(title_id, row, current, files)
        total = sum(size for _name, size in files if size > 0)

        if "empty" in checks and (not files or total == 0):
            findings.append(Finding("empty", title_id,
                                    "%d file(s), %d bytes" % (len(files), total)))

        if "count" in checks and expected:
            recorded = int(row.get("file_count") or 0)
            if recorded and recorded != expected[2]:
                findings.append(Finding(
                    "count", title_id,
                    "database says %d file(s), the stored files give %d"
                    % (recorded, expected[2])))

        if "size" in checks and expected:
            recorded = int(row.get("save_size") or 0)
            if recorded and recorded != expected[1]:
                findings.append(Finding(
                    "size", title_id,
                    "database says %s, the stored files give %s"
                    % (human(recorded), human(expected[1]))))

        if "hash" in checks:
            _check_hash(title_id, row, expected, findings, stats)

        if "temp" in checks:
            for name, _size in files:
                if name.endswith((".part", ".tmp")):
                    findings.append(Finding("temp", title_id,
                                            "leftover %s" % name))

        if "history" in checks:
            _check_history(title_id, directory, max_history, findings)

    if "orphan" in checks:
        for entry in sorted(on_disk - set(by_id)):
            files = scan_files(os.path.join(save_dir, entry))
            findings.append(Finding(
                "orphan", entry,
                "directory on disk with no database row (%d file(s))"
                % len(files)))

    if "duplicate" in checks:
        findings.extend(_find_duplicates(rows))

    if "slug" in checks:
        for title_id, row in sorted(by_id.items()):
            system = str(row.get("system") or "").upper()
            if system in SERIAL_SYSTEMS and _SYSTEM_SLUG_RE.match(title_id):
                findings.append(Finding(
                    "slug", title_id,
                    "%s is keyed by serial, but this is a name slug "
                    "(name=%r)" % (system, row.get("name"))))

    return findings, stats


#: PS1 saves are hashed and sized over the PSP-visible subset, so their totals
#: legitimately differ from what is on disk.
_PS1_PREFIXES = ("SLUS", "SCUS", "SLES", "SCES", "SLPS", "SLPM", "SCPS",
                 "SCPM", "SLAJ", "SLEJ", "SCAJ", "PAPX", "SCED")


def _ps1_hashing(title_id, row):
    system = str(row.get("system") or row.get("platform") or "").upper()
    if system == "PS1":
        return True
    return (looks_like_serial(title_id)
            and title_id[:4].upper() in _PS1_PREFIXES)


def _check_hash(title_id, row, expected, findings, stats):
    recorded = str(row.get("save_hash") or "")
    if not recorded or not expected or expected[0] is None:
        # No expectation available: a multi-file save under the generic rule
        # hashes in bundle order, which the filesystem cannot reproduce.
        stats["unverifiable_hashes"] += 1
        return

    stats["verified_hashes"] += 1
    if expected[0] != recorded:
        findings.append(Finding(
            "hash", title_id,
            "database says %s..., stored files give %s..."
            % (recorded[:12], expected[0][:12])))


#: e.g. "2026-03-30T01_24_22.114451_00_00_GC_gm8e" - the timestamp has its
#: colons replaced and the title id appended.
_TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T[\d_.]+")


def _check_history(title_id, directory, max_history, findings):
    history = os.path.join(directory, "history")
    if not os.path.isdir(history):
        return
    try:
        snapshots = sorted(os.listdir(history))
    except OSError as exc:
        findings.append(Finding("history", title_id, "unreadable: %s" % exc))
        return

    if len(snapshots) > max_history:
        findings.append(Finding(
            "history", title_id,
            "%d snapshots kept, limit is %d" % (len(snapshots), max_history)))
    for name in snapshots:
        if not _TS_RE.match(name):
            findings.append(Finding("history", title_id,
                                    "odd snapshot name %r" % name))


def _find_duplicates(rows):
    """One game stored under two identifiers.

    This is the failure mode where a client that could not resolve a disc
    serial fell back to a name slug, so the same game ends up in two slots and
    neither side ever sees the other's progress.
    """
    findings = []
    groups = defaultdict(list)
    for row in rows:
        system = str(row.get("system") or "").upper()
        key = normalize_name(row.get("name") or "")
        if not key or not system:
            continue
        groups[(system, key)].append(row)

    for (system, key), members in sorted(groups.items()):
        if len(members) < 2:
            continue
        ids = sorted(row["title_id"] for row in members)
        serials = [i for i in ids if looks_like_serial(i)]
        slugs = [i for i in ids if _SYSTEM_SLUG_RE.match(i)]
        # A slug sitting beside a serial is the real fault: a client that could
        # not resolve the serial fell back to the name, so the same game now
        # occupies two slots and neither sees the other's progress.
        #
        # Several *serials* for one game is NOT reported: PS3 and PSP titles
        # normally use one save directory per slot or per mode, and flagging
        # those buried the real findings.
        if serials and slugs:
            findings.append(Finding(
                "duplicate", ", ".join(ids),
                "%s %r stored under both a serial and a slug" % (system, key)))
    return findings


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--save-dir",
                        default=os.environ.get("SYNC_SAVE_DIR", "saves"))
    parser.add_argument("--check", default="all",
                        help="comma-separated: %s" % ",".join(ALL_CHECKS))
    parser.add_argument("--max-history", type=int,
                        default=int(os.environ.get("SYNC_MAX_HISTORY_VERSIONS",
                                                   DEFAULT_MAX_HISTORY)))
    parser.add_argument("--json", metavar="PATH",
                        help="also write the findings as JSON")
    parser.add_argument("--limit", type=int, default=20,
                        help="detail lines to print per check (0 = all)")
    args = parser.parse_args()

    checks = (set(ALL_CHECKS) if args.check == "all"
              else {c.strip() for c in args.check.split(",") if c.strip()})
    unknown = checks - set(ALL_CHECKS)
    if unknown:
        raise SystemExit("unknown check(s): %s" % ", ".join(sorted(unknown)))

    global SERVER_RULES
    SERVER_RULES = _load_server_rules()

    save_dir = os.path.abspath(args.save_dir)
    findings, stats = audit(save_dir, checks, args.max_history)

    print("GameSync save audit")
    print("  save dir      : %s" % save_dir)
    print("  database rows : %d" % stats["rows"])
    print("  stored        : %s" % human(stats["total_bytes"]))
    print("  hashes        : %d verified, %d not verifiable"
          % (stats["verified_hashes"], stats["unverifiable_hashes"]))
    if not SERVER_RULES:
        print("  NOTE: the server's own hashing rules could not be imported, so")
        print("        hash, size and count were skipped. Re-run it with the")
        print("        server's virtualenv: uv run python tools/audit_saves.py")
    print()

    by_check = defaultdict(list)
    for finding in findings:
        by_check[finding.check].append(finding)

    errors = warnings = 0
    for check in ALL_CHECKS:
        items = by_check.get(check)
        if not items:
            continue
        severity = SEVERITY[check]
        if severity == "error":
            errors += len(items)
        else:
            warnings += len(items)
        print("%-9s %-7s %d" % (check, severity, len(items)))
        shown = items if args.limit == 0 else items[: args.limit]
        for finding in shown:
            print("    %-42s %s" % (finding.title_id[:42], finding.detail))
        if len(items) > len(shown):
            print("    ... %d more" % (len(items) - len(shown)))
        print()

    if not findings:
        print("No problems found.")
    else:
        print("%d error(s), %d warning(s)" % (errors, warnings))

    if args.json:
        with open(args.json, "w") as handle:
            json.dump({"save_dir": save_dir, "stats": stats,
                       "findings": [f.as_dict() for f in findings]},
                      handle, indent=2)
        print("wrote %s" % args.json)

    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
