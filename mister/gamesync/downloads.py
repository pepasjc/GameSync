"""Resumable ROM download queue for the MiSTer client.

Mirrors the behaviour of ``steamdeck/download_manager.py`` and
``android/.../sync/DownloadManager.kt`` - ``.part`` files, HTTP ``Range``
resume, atomic rename on completion - with two differences forced by the
hardware:

* MiSTer has **no ``sqlite3`` module**, so the queue persists to JSON.
* One download at a time. The device has 492 MB of RAM, the SD card is exfat
  mounted ``sync``, and the NIC is 100 Mb; running three at once would only
  make each slower.

Progress is persisted as it goes, so a multi-gigabyte download survives the app
being closed or the console being switched off.

An MSU pack (``bundle_kind`` on the catalogue row) downloads as the zip the
server holds and is unpacked into its own game folder once complete - the
audio has to sit beside the ROM, and an MSU-MD title goes to the MegaCD core
with its cart renamed. The zip lands next to the folder, so a crash
mid-unpack leaves a resumable ``.part``, never a half folder with no origin.

``DownloadWorker`` runs the queue on a background thread so the UI stays
usable while games install. The thread touches nothing but the ``Download``
items and the queue file - never the framebuffer - and the UI thread reads
their state to paint progress.
"""

from __future__ import annotations

import json
import os
import posixpath
import shutil
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile

from shared import msu
from shared.mister import MISTER_CONFIG_DIR
from shared.mister_install import (
    bios_seed_sources,
    install_target,
    msu_pack_layout,
    msu_pack_target,
    safe_file_name,
    safe_folder_name,
)

QUEUE_PATH = posixpath.join(MISTER_CONFIG_DIR, "downloads.json")

CHUNK = 256 * 1024
#: How often progress is written back to disk while a download runs.
PERSIST_INTERVAL = 3.0

QUEUED = "queued"
DOWNLOADING = "downloading"
DONE = "done"
FAILED = "failed"
CANCELLED = "cancelled"


class Download:
    __slots__ = ("rom_id", "name", "system", "filename", "size", "directory",
                 "target", "status", "received", "error", "bundle_kind",
                 "rom_rename")

    def __init__(self, rom_id, name, system, filename, size=0, directory="",
                 target="", status=QUEUED, received=0, error="",
                 bundle_kind="", rom_rename=None):
        self.rom_id = rom_id
        self.name = name
        self.system = system
        self.filename = filename
        self.size = size
        self.directory = directory
        self.target = target
        self.status = status
        self.received = received
        self.error = error
        #: MSU pack kind when the download is one; the zip is unpacked into
        #: ``directory`` on completion, with the cart renamed ``rom_rename``
        #: when the core insists on a name (MegaCD: ``cart.rom``).
        self.bundle_kind = bundle_kind or ""
        self.rom_rename = rom_rename

    @property
    def progress(self) -> float:
        if self.size <= 0:
            return 0.0
        return min(1.0, self.received / float(self.size))

    def to_dict(self):
        return {slot: getattr(self, slot) for slot in self.__slots__}

    @classmethod
    def from_dict(cls, data):
        return cls(**{key: data.get(key) for key in cls.__slots__
                      if key in data})


class _Stopped(Exception):
    """Raised inside a transfer when the worker was asked to stop."""


class DownloadQueue:
    def __init__(self, client=None, provider=None, rom_target="sd"):
        self.client = client
        self.provider = provider
        self.rom_target = rom_target
        self.items = []
        #: The worker persists progress while the UI thread may enqueue; the
        #: two must not race for the .part file.
        self._save_lock = threading.Lock()
        self.load()

    # ------------------------------------------------------------ persistence

    def load(self):
        try:
            with open(QUEUE_PATH, "r") as handle:
                data = json.load(handle)
        except (OSError, ValueError):
            self.items = []
            return
        items = []
        dropped = 0
        for raw in data.get("downloads", []) if isinstance(data, dict) else []:
            try:
                item = Download.from_dict(raw)
            except TypeError:
                continue
            # A finished download is history by the next run; the game is on
            # the Installed tab now. Queued and failed ones are still work.
            if item.status in (DONE, CANCELLED):
                dropped += 1
                continue
            # Anything caught mid-flight last run is resumable, not lost: the
            # .part file is still there and Range picks up where it stopped.
            if item.status == DOWNLOADING:
                item.status = QUEUED
            items.append(item)
        self.items = items
        if dropped:
            self.save()

    def save(self):
        with self._save_lock:
            payload = {"downloads": [item.to_dict()
                                     for item in list(self.items)]}
            try:
                os.makedirs(MISTER_CONFIG_DIR, exist_ok=True)
            except OSError:
                pass
            temp = QUEUE_PATH + ".part"
            try:
                with open(temp, "w") as handle:
                    json.dump(payload, handle, indent=2)
                os.replace(temp, QUEUE_PATH)
            except OSError:
                pass

    # ------------------------------------------------------------------ queue

    def enqueue(self, rom, save=True):
        """Add one catalogue row. Returns the Download, or None if refused.

        ``save=False`` skips the write-back so a multi-disc game persists
        once, from :meth:`enqueue_all`, rather than once per disc.
        """
        rom_id = str(rom.get("rom_id") or rom.get("title_id") or "")
        if not rom_id:
            return None
        for existing in self.items:
            if existing.rom_id == rom_id and existing.status in (
                    QUEUED, DOWNLOADING, DONE):
                return existing

        system = str(rom.get("system") or "").upper()
        filename = safe_file_name(rom.get("filename") or rom.get("name") or "")
        display = str(rom.get("name") or "")
        bundle_kind = ""
        rom_rename = None
        if rom.get("is_bundle") and str(rom.get("bundle_kind") or ""):
            # The pack decides which core's folder it lives in, and so which
            # system the queue files it under (an MSU-MD pack is a MegaCD
            # title - that is also what seeds the right BIOS on USB).
            kind = str(rom.get("bundle_kind") or "").lower()
            layout = msu_pack_layout(kind, system)
            if layout is None:
                item = Download(rom_id, display or filename, system, filename,
                                int(rom.get("size") or 0), status=FAILED)
                item.error = "MiSTer cannot play a %s pack" % kind
                self.items.append(item)
                if save:
                    self.save()
                return item
            system, rom_rename = layout
            bundle_kind = kind
            directory, _rename = msu_pack_target(
                self.provider, system, kind, display or filename, self.rom_target)
            # The zip parks beside the game folder until it is unpacked.
            target_name = safe_folder_name(display or filename) + ".zip"
        else:
            directory, target_name = install_target(
                self.provider, system, filename, display, self.rom_target)
        if not directory:
            item = Download(rom_id, display or filename, system, filename,
                            int(rom.get("size") or 0), status=FAILED)
            item.error = "no MiSTer folder for %s" % (system or "?")
            self.items.append(item)
            if save:
                self.save()
            return item

        item = Download(
            rom_id=rom_id,
            name=display or filename,
            system=system,
            filename=target_name,
            size=int(rom.get("size") or 0),
            directory=directory,
            target=(posixpath.join(posixpath.dirname(directory), target_name)
                    if bundle_kind else posixpath.join(directory, target_name)),
            bundle_kind=bundle_kind,
            rom_rename=rom_rename,
        )
        self.items.append(item)
        if save:
            self.save()
        return item

    def enqueue_all(self, roms):
        """Queue several rows - every disc of one game - with one write."""
        items = [self.enqueue(rom, save=False) for rom in roms]
        self.save()
        return items

    def pending(self):
        return [item for item in self.items if item.status == QUEUED]

    def clear_finished(self):
        self.items = [item for item in self.items
                      if item.status in (QUEUED, DOWNLOADING)]
        self.save()

    def remove(self, item):
        if item in self.items:
            self.items.remove(item)
            self.save()

    def retry(self, item):
        """Put a failed download back in the queue. Returns True if it was.

        Whatever ``.part`` the failed attempt left is kept: the next run
        resumes from it with a Range request rather than starting over.
        A download that failed before it had a destination (no core folder
        for the system) is re-resolved, since that is what installing the
        core - or switching ROM target - fixes.
        """
        if item.status != FAILED:
            return False
        if not item.target and item.bundle_kind:
            directory, _rename = msu_pack_target(
                self.provider, item.system, item.bundle_kind, item.name,
                self.rom_target)
            if not directory:
                return False
            item.directory = directory
            item.filename = safe_folder_name(item.name) + ".zip"
            item.target = posixpath.join(posixpath.dirname(directory),
                                         item.filename)
        elif not item.target:
            directory, target_name = install_target(
                self.provider, item.system, item.filename, item.name,
                self.rom_target)
            if not directory:
                return False
            item.directory = directory
            item.filename = target_name
            item.target = posixpath.join(directory, target_name)
        item.status = QUEUED
        item.error = ""
        self.save()
        return True

    # --------------------------------------------------------------- transfer

    def run_next(self, progress=None, stop=None):
        """Download the next queued item. Returns it, or None when idle.

        ``stop`` is a ``threading.Event``; once set, the transfer breaks off
        and the item goes back to QUEUED with its .part kept, so the next run
        resumes it.
        """
        pending = self.pending()
        if not pending:
            return None
        item = pending[0]
        try:
            self._download(item, progress, stop)
            item.status = DONE
            item.error = ""
        except _Stopped:
            item.status = QUEUED
            item.error = ""
        except Exception as exc:
            item.status = FAILED
            item.error = str(exc)
        self.save()
        return item

    def run_all(self, progress=None):
        done = failed = 0
        while self.pending():
            item = self.run_next(progress)
            if item is None:
                break
            if item.status == DONE:
                done += 1
            else:
                failed += 1
        return done, failed

    def _download(self, item, progress=None, stop=None):
        item.status = DOWNLOADING
        self.save()

        self._prepare_directory(item)

        if item.bundle_kind and os.path.isfile(item.target):
            # The zip landed last time and only the unpack failed (card
            # full, say): don't fetch a gigabyte again to retry it.
            item.received = item.size = os.path.getsize(item.target)
            self._unpack(item)
            return

        part = item.target + ".part"
        existing = 0
        if os.path.exists(part):
            existing = os.path.getsize(part)

        request = urllib.request.Request(
            self._url(item), method="GET")
        if self.client is not None and self.client.api_key:
            request.add_header("X-API-Key", self.client.api_key)
        if existing:
            request.add_header("Range", "bytes=%d-" % existing)

        response = urllib.request.urlopen(request, timeout=60)
        resumed = existing and response.status == 206
        if existing and not resumed:
            # The server ignored the range; start over rather than corrupting
            # the file by appending to a partial one.
            existing = 0

        total = item.size
        header_length = response.headers.get("Content-Length")
        if header_length:
            try:
                total = int(header_length) + (existing if resumed else 0)
            except ValueError:
                pass
        content_range = response.headers.get("Content-Range")
        if content_range and "/" in content_range:
            try:
                total = int(content_range.rsplit("/", 1)[1])
            except ValueError:
                pass
        if total:
            item.size = total

        item.received = existing if resumed else 0
        last_persist = time.time()
        mode = "ab" if resumed else "wb"

        with response, open(part, mode) as handle:
            while True:
                if stop is not None and stop.is_set():
                    handle.flush()
                    self.save()
                    raise _Stopped()
                chunk = response.read(CHUNK)
                if not chunk:
                    break
                handle.write(chunk)
                item.received += len(chunk)
                now = time.time()
                if now - last_persist > PERSIST_INTERVAL:
                    handle.flush()
                    self.save()
                    last_persist = now
                if progress:
                    progress(item)
            handle.flush()
            try:
                os.fsync(handle.fileno())
            except OSError:
                pass

        os.replace(part, item.target)
        item.received = os.path.getsize(item.target)

        if item.bundle_kind:
            self._unpack(item)

    def _unpack(self, item):
        """Lay an MSU pack out in its game folder, then drop the zip.

        Members go through :func:`shared.msu.plan_extraction`: the wrapping
        folder most packs are zipped with is hoisted away, junk is dropped,
        and the cart takes the name the core wants.  Written straight to the
        card - there is no faster scratch space on a MiSTer.
        """
        try:
            with zipfile.ZipFile(item.target) as zf:
                members = [i.filename for i in zf.infolist() if not i.is_dir()]
                root, _ = msu.strip_common_root(members)

                def _read(name):
                    return zf.read(root + "/" + name if root else name).decode(
                        "utf-8", "replace")

                layout = msu.plan_extraction(
                    item.bundle_kind, members, item.rom_rename, _read)
                if not layout:
                    raise RuntimeError("archive holds no pack files")
                os.makedirs(item.directory, exist_ok=True)
                for member, rel in layout:
                    destination = os.path.join(item.directory, *rel.split("/"))
                    parent = os.path.dirname(destination)
                    if parent:
                        os.makedirs(parent, exist_ok=True)
                    with zf.open(member) as src, open(destination, "wb") as dst:
                        shutil.copyfileobj(src, dst, CHUNK)
        except (OSError, zipfile.BadZipFile, RuntimeError) as exc:
            raise RuntimeError("unpack failed: %s" % exc)
        try:
            os.remove(item.target)
        except OSError:
            pass

    def _url(self, item):
        base = self.client.base_url if self.client is not None else ""
        return "%s/roms/%s" % (base,
                               urllib.parse.quote(str(item.rom_id), safe=""))

    def _prepare_directory(self, item):
        """Create the target folder, seeding a USB core folder's BIOS first.

        A core stops looking at the SD card as soon as the USB folder exists,
        so the BIOS has to be there before the first game is.
        """
        created = not os.path.isdir(item.directory)
        try:
            os.makedirs(item.directory, exist_ok=True)
        except OSError as exc:
            raise RuntimeError("cannot create %s: %s" % (item.directory, exc))

        if not created or self.provider is None:
            return
        for source in bios_seed_sources(self.provider, item.system,
                                        self.rom_target):
            # The BIOS belongs beside the games folder for the core, which for
            # a CD system is the parent of the per-game folder.
            destination_dir = self._core_dir(item)
            destination = posixpath.join(destination_dir,
                                         posixpath.basename(source))
            if os.path.exists(destination):
                continue
            try:
                os.makedirs(destination_dir, exist_ok=True)
                with open(source, "rb") as src, open(destination, "wb") as dst:
                    dst.write(src.read())
            except OSError:
                pass

    def _core_dir(self, item):
        """The ``games/<Core>`` folder, above any per-game subfolder."""
        from shared.mister_install import games_root

        root = games_root(self.rom_target).rstrip("/")
        directory = item.directory.rstrip("/")
        if not directory.startswith(root + "/"):
            return directory
        remainder = directory[len(root) + 1:]
        first = remainder.split("/", 1)[0]
        return posixpath.join(root, first)


class DownloadWorker:
    """Runs a DownloadQueue on a background thread, one item at a time.

    The thread only ever touches Download objects and the queue file. The UI
    thread reads ``current`` to paint progress and drains ``finished()`` to
    react to each completed download (a toast, a rescan of the games
    folders). Items queued while it runs are picked up in turn; when the
    queue empties the thread ends, and the next start() begins a new one.
    """

    def __init__(self, queue):
        self.queue = queue
        self._thread = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._finished = []
        #: The item being transferred right now, or None.
        self.current = None

    @property
    def running(self):
        return self._thread is not None and self._thread.is_alive()

    def start(self):
        """Begin working through the queue. False when nothing to do."""
        if self.running or not self.queue.pending():
            return False
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="downloads",
                                        daemon=True)
        self._thread.start()
        return True

    def stop(self, timeout=3.0):
        """Break off the current transfer; it resumes on the next start.

        A read blocked on the socket can hold the thread for a moment, so
        the join is bounded - the thread is a daemon and progress was
        persisted as it went.
        """
        self._stop.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout)

    def finished(self):
        """Items that completed (DONE or FAILED) since the last call."""
        with self._lock:
            items, self._finished = self._finished, []
        return items

    def _run(self):
        try:
            while not self._stop.is_set():
                item = self.queue.run_next(progress=self._progress,
                                           stop=self._stop)
                if item is None:
                    break
                if item.status in (DONE, FAILED):
                    with self._lock:
                        self._finished.append(item)
        finally:
            self.current = None

    def _progress(self, item):
        self.current = item
