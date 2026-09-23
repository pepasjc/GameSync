"""The download queue between runs: finished entries are history, failed
ones can be put back."""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for candidate in (ROOT, ROOT / "mister"):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from gamesync import downloads as gsdownloads  # noqa: E402


def write_queue(path, items):
    path.write_text(json.dumps({"downloads": items}))


def entry(rom_id, status, **extra):
    base = {"rom_id": rom_id, "name": rom_id, "system": "SNES",
            "filename": rom_id + ".sfc", "size": 10, "directory": "/games",
            "target": "/games/" + rom_id + ".sfc", "status": status,
            "received": 0, "error": ""}
    base.update(extra)
    return base


def test_done_and_cancelled_are_dropped_on_load(tmp_path, monkeypatch):
    queue_file = tmp_path / "downloads.json"
    monkeypatch.setattr(gsdownloads, "QUEUE_PATH", str(queue_file))
    write_queue(queue_file, [
        entry("a", gsdownloads.DONE),
        entry("b", gsdownloads.QUEUED),
        entry("c", gsdownloads.FAILED, error="boom"),
        entry("d", gsdownloads.DOWNLOADING, received=5),
        entry("e", gsdownloads.CANCELLED),
    ])

    queue = gsdownloads.DownloadQueue()

    assert [(i.rom_id, i.status) for i in queue.items] == [
        ("b", gsdownloads.QUEUED),
        ("c", gsdownloads.FAILED),
        ("d", gsdownloads.QUEUED),      # mid-flight last run: resumable
    ]
    # And the file on disk no longer carries the finished ones either.
    saved = json.loads(queue_file.read_text())["downloads"]
    assert [i["rom_id"] for i in saved] == ["b", "c", "d"]


def test_retry_requeues_a_failure_and_keeps_its_progress(tmp_path, monkeypatch):
    monkeypatch.setattr(gsdownloads, "QUEUE_PATH",
                        str(tmp_path / "downloads.json"))
    queue = gsdownloads.DownloadQueue()
    failed = gsdownloads.Download("x", "X", "SNES", "x.sfc", size=100,
                                  directory="/games", target="/games/x.sfc",
                                  status=gsdownloads.FAILED, received=40,
                                  error="timed out")
    fine = gsdownloads.Download("y", "Y", "SNES", "y.sfc",
                                status=gsdownloads.QUEUED)
    queue.items = [failed, fine]

    assert queue.retry(fine) is False
    assert queue.retry(failed) is True
    assert failed.status == gsdownloads.QUEUED
    assert failed.error == ""
    assert failed.received == 40
    assert queue.pending() == [failed, fine]


class _Provider:
    def is_dir(self, path):
        return False


def test_enqueue_all_persists_once_per_game(tmp_path, monkeypatch):
    """Queueing is instant: a 4-disc game is one JSON write, not four.

    The queue file sits on an exfat card mounted ``sync``, so every
    ``os.replace`` there costs real time on the device.
    """
    monkeypatch.setattr(gsdownloads, "QUEUE_PATH",
                        str(tmp_path / "downloads.json"))
    monkeypatch.setattr(gsdownloads, "MISTER_CONFIG_DIR", str(tmp_path))
    queue = gsdownloads.DownloadQueue(provider=_Provider())
    writes = []
    monkeypatch.setattr(queue, "save", lambda: writes.append(1))

    rows = [{"rom_id": "ff9-%d" % n, "name": "FF IX (Disc %d)" % n,
             "system": "PS1", "filename": "ff9-%d.chd" % n, "size": 1}
            for n in range(1, 5)]
    items = queue.enqueue_all(rows)

    assert [i.status for i in items] == [gsdownloads.QUEUED] * 4
    assert len(writes) == 1


def test_worker_runs_the_queue_in_the_background(tmp_path, monkeypatch):
    """Queueing returns at once; the worker reports each item as it lands,
    and picks up items added while it runs."""
    import threading
    import time

    monkeypatch.setattr(gsdownloads, "QUEUE_PATH",
                        str(tmp_path / "downloads.json"))
    monkeypatch.setattr(gsdownloads, "MISTER_CONFIG_DIR", str(tmp_path))
    queue = gsdownloads.DownloadQueue(provider=_Provider())
    gate = threading.Event()

    def fake_download(item, progress=None, stop=None):
        gate.wait(5)
        item.received = item.size = 3

    monkeypatch.setattr(queue, "_download", fake_download)
    worker = gsdownloads.DownloadWorker(queue)
    first = queue.enqueue({"rom_id": "a", "name": "A", "system": "SNES",
                           "filename": "a.sfc", "size": 3})
    assert worker.start() is True
    assert worker.running and worker.finished() == []
    # Added mid-run: no restart needed.
    second = queue.enqueue({"rom_id": "b", "name": "B", "system": "SNES",
                            "filename": "b.sfc", "size": 3})
    gate.set()
    deadline = time.time() + 5
    seen = []
    while len(seen) < 2 and time.time() < deadline:
        seen += worker.finished()
        time.sleep(0.01)
    assert [i.rom_id for i in seen] == ["a", "b"]
    assert first.status == second.status == gsdownloads.DONE
    worker.stop()
    assert not worker.running


def test_stop_puts_the_current_item_back_in_the_queue(tmp_path, monkeypatch):
    monkeypatch.setattr(gsdownloads, "QUEUE_PATH",
                        str(tmp_path / "downloads.json"))
    monkeypatch.setattr(gsdownloads, "MISTER_CONFIG_DIR", str(tmp_path))
    queue = gsdownloads.DownloadQueue(provider=_Provider())

    def slow_download(item, progress=None, stop=None):
        item.status = gsdownloads.DOWNLOADING
        while not stop.is_set():
            stop.wait(0.01)
        raise gsdownloads._Stopped()

    monkeypatch.setattr(queue, "_download", slow_download)
    worker = gsdownloads.DownloadWorker(queue)
    item = queue.enqueue({"rom_id": "a", "name": "A", "system": "SNES",
                          "filename": "a.sfc", "size": 3})
    worker.start()
    worker.stop()

    assert item.status == gsdownloads.QUEUED       # resumable, not failed
    assert worker.finished() == []
    assert not worker.running


class _CoreProvider:
    """Cores whose folders already exist on the card."""

    def __init__(self, *cores):
        self.dirs = {"/media/fat/games/" + core for core in cores}

    def is_dir(self, path):
        return path.rstrip("/") in self.dirs

    def listdir(self, path):
        return []


def _pack_row(kind, system, name):
    return {"rom_id": system + "_" + name, "name": name, "system": system,
            "filename": name + ".zip", "size": 3, "is_bundle": True,
            "bundle_kind": kind}


def test_msu_packs_queue_into_their_own_core_folder(tmp_path, monkeypatch):
    """An MSU-1 pack is a SNES folder; an MSU-MD pack is a MegaCD title."""
    monkeypatch.setattr(gsdownloads, "QUEUE_PATH",
                        str(tmp_path / "downloads.json"))
    queue = gsdownloads.DownloadQueue(provider=_CoreProvider("SNES", "MegaCD"))

    snes = queue.enqueue(_pack_row("msu1", "SNES", "ActRaiser (USA) (MSU1)"))
    assert snes.directory == "/media/fat/games/SNES/ActRaiser (USA) (MSU1)"
    assert snes.target == "/media/fat/games/SNES/ActRaiser (USA) (MSU1).zip"
    assert snes.bundle_kind == "msu1" and snes.rom_rename is None

    md = queue.enqueue(_pack_row("msu-md", "MD", "Sonic 2 (MSU-MD)"))
    assert md.system == "SEGACD"
    assert md.directory == "/media/fat/games/MegaCD/Sonic 2 (MSU-MD)"
    assert md.rom_rename == "cart.rom"

    weird = queue.enqueue(_pack_row("mdplusplus", "MD", "X"))
    assert weird.status == gsdownloads.FAILED
    assert "cannot play" in weird.error

    # Survives the JSON round trip with its pack fields intact.
    reloaded = gsdownloads.DownloadQueue(provider=_CoreProvider("SNES", "MegaCD"))
    kinds = {i.rom_id: (i.bundle_kind, i.rom_rename) for i in reloaded.items}
    assert kinds["MD_Sonic 2 (MSU-MD)"] == ("msu-md", "cart.rom")


def test_landed_pack_is_unpacked_hoisted_and_cart_renamed(tmp_path, monkeypatch):
    import zipfile

    monkeypatch.setattr(gsdownloads, "QUEUE_PATH",
                        str(tmp_path / "downloads.json"))
    monkeypatch.setattr(gsdownloads, "MISTER_CONFIG_DIR", str(tmp_path))
    queue = gsdownloads.DownloadQueue(provider=_CoreProvider())

    games = tmp_path / "games" / "MegaCD"
    games.mkdir(parents=True)
    zip_path = games / "Sonic 2 (MSU-MD).zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("Sonic 2 (MSU-MD)/", b"")
        zf.writestr("Sonic 2 (MSU-MD)/Sonic 2 (MSU-MD).md", b"ROM")
        zf.writestr("Sonic 2 (MSU-MD)/Sonic 2 (MSU-MD).cue",
                    'FILE "Sonic 2 (MSU-MD).bin" BINARY\n  TRACK 01 AUDIO\n')
        zf.writestr("Sonic 2 (MSU-MD)/Sonic 2 (MSU-MD).bin", b"AUDIO")
        zf.writestr("Sonic 2 (MSU-MD)/Sonic 2 (MSU-MD).srm", b"junk")

    item = gsdownloads.Download(
        "x", "Sonic 2 (MSU-MD)", "SEGACD", "Sonic 2 (MSU-MD).zip", size=1,
        directory=str(games / "Sonic 2 (MSU-MD)"), target=str(zip_path),
        bundle_kind="msu-md", rom_rename="cart.rom")
    # The zip is already there from a run whose unpack failed: no network.
    queue._download(item)

    folder = games / "Sonic 2 (MSU-MD)"
    assert sorted(p.name for p in folder.iterdir()) == [
        "Sonic 2 (MSU-MD).bin", "Sonic 2 (MSU-MD).cue", "cart.rom"]
    assert (folder / "cart.rom").read_bytes() == b"ROM"
    assert not zip_path.exists()
