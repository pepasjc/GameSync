"""Console Mode menu entry: the boot hook and the Load Script handoff.

The hook is spliced into linux/user-startup.sh, a file other add-ons (MiSTer
Companion Remote, MisterZine) write to as well, so setup must be idempotent
and removal must take out exactly our block and nothing else.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for candidate in (ROOT, ROOT / "mister"):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from gamesync import consolemode as cm  # noqa: E402

COMPANION = (
    "#!/bin/sh\n"
    "\n"
    "# MiSTer Companion Remote BEGIN\n"
    "/media/fat/Scripts/companion_remote.sh start --unattended &\n"
    "# MiSTer Companion Remote END\n"
)


def _setup_hook(tmp_path, monkeypatch, text):
    startup = tmp_path / "user-startup.sh"
    startup.write_text(text)
    monkeypatch.setattr(cm, "STARTUP", str(startup))
    return startup


def _apply(startup):
    """The startup-file half of setup(), without forking a watcher."""
    text = cm._without_block(cm._startup_text())
    if not text.endswith("\n"):
        text += "\n"
    startup.write_text(text + "\n" + cm.STARTUP_BLOCK)


def test_hook_is_added_once(tmp_path, monkeypatch):
    startup = _setup_hook(tmp_path, monkeypatch, COMPANION)
    _apply(startup)
    _apply(startup)
    text = startup.read_text()
    assert text.count(cm.BEGIN) == 1
    assert cm.WATCH_FLAG in text
    assert "companion_remote.sh start" in text


def test_removal_leaves_other_add_ons(tmp_path, monkeypatch):
    startup = _setup_hook(tmp_path, monkeypatch, COMPANION)
    _apply(startup)
    stripped = cm._without_block(startup.read_text())
    assert cm.BEGIN not in stripped and cm.WATCH_FLAG not in stripped
    assert "# MiSTer Companion Remote BEGIN" in stripped
    assert "# MiSTer Companion Remote END" in stripped


def test_hook_only_runs_when_both_ends_exist():
    # A card that dropped Console Mode, or GameSync, must boot silently.
    assert "[ -e %s ]" % cm.PYZ in cm.STARTUP_BLOCK
    assert "[ -e %s ]" % cm.CM_FRONTEND in cm.STARTUP_BLOCK


def test_request_script_is_what_load_script_leaves(tmp_path, monkeypatch):
    path_file = tmp_path / "script_path"
    code_file = tmp_path / "exit_code"
    monkeypatch.setattr(cm, "SCRIPT_PATH_FILE", str(path_file))
    monkeypatch.setattr(cm, "EXIT_CODE_FILE", str(code_file))
    cm.request_script(cm.LAUNCHER)
    assert path_file.read_text() == cm.LAUNCHER
    assert cm._read(str(code_file)) == cm.KEEP_SHELL == "12"


def test_section_is_one_system_listing_every_script():
    # A second system brings back Console Mode's system screen: one more
    # click on the way to every script.
    assert "consoleList = SCRIPTS\n" in cm.SECTION_BODY
    assert cm.SECTION_BODY.count("romDirs") == 1
    assert "romDirs = %s/\n" % cm.SCRIPTS_DIR in cm.SECTION_BODY
    assert cm.SECTION_INI.endswith("/Scripts.ini")
    assert cm.SECTION_BODY not in cm.OLD_SECTION_BODIES


def test_only_scripts_folder_launches_are_taken_over(tmp_path, monkeypatch):
    scripts = tmp_path / "Scripts"
    scripts.mkdir()
    (scripts / "update_all.sh").write_text("#!/bin/bash\n")
    (scripts / "sub").mkdir()
    (scripts / "sub" / "x.sh").write_text("")
    monkeypatch.setattr(cm, "SCRIPTS_DIR", str(scripts))
    assert cm.is_script(str(scripts / "update_all.sh"))
    # Games, missing files and scripts in subfolders stay Console Mode's.
    assert not cm.is_script("/media/fat/games/PSX/Game.cue")
    assert not cm.is_script(str(scripts / "missing.sh"))
    assert not cm.is_script(str(scripts / "sub" / "x.sh"))
    assert not cm.is_script(None)
