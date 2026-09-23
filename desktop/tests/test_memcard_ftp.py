import hashlib
import os
import struct

import pytest

import sync_engine as se


class FakeFTP:
    def __init__(self, fs: dict[str, bytes | None]):
        self.fs = fs
        self.cwd_path = "/"

    def connect(self, host, port, timeout=None):
        self.host = host
        self.port = port

    def login(self, *args):
        self.login_args = args

    def set_pasv(self, passive):
        self.passive = passive

    def quit(self):
        pass

    def close(self):
        pass

    def pwd(self):
        return self.cwd_path

    def cwd(self, path):
        path = se._ftp_norm(path)
        if self.fs.get(path) is not None:
            raise se.ftplib.error_perm("not a directory")
        self.cwd_path = path

    def mkd(self, path):
        self.fs[se._ftp_norm(path)] = None

    def voidcmd(self, command):
        return "200 OK"

    def size(self, path):
        value = self.fs.get(se._ftp_norm(path))
        if not isinstance(value, bytes):
            raise se.ftplib.error_perm("not a file")
        return len(value)

    def sendcmd(self, command):
        if command.startswith("MDTM "):
            path = se._ftp_norm(command[5:])
            if isinstance(self.fs.get(path), bytes):
                return "213 20260505000000"
        raise se.ftplib.error_perm("unsupported")

    def mlsd(self, path):
        path = se._ftp_norm(path)
        if self.fs.get(path) is not None:
            raise se.ftplib.error_perm("not a directory")
        prefix = "/" if path == "/" else path + "/"
        children: dict[str, tuple[str, bool, int]] = {}
        for child_path, value in self.fs.items():
            if child_path == path or not child_path.startswith(prefix):
                continue
            rest = child_path[len(prefix) :]
            name = rest.split("/", 1)[0]
            full = se._ftp_join(path, name)
            is_dir = "/" in rest or self.fs.get(full) is None
            size = 0 if is_dir else len(self.fs[full] or b"")
            children[name] = (full, is_dir, size)
        for name, (_full, is_dir, size) in sorted(children.items()):
            yield name, {
                "type": "dir" if is_dir else "file",
                "size": str(size),
                "modify": "20260505000000",
            }

    def nlst(self, path):
        return [entry.path for entry in se._ftp_list_entries(self, path)]

    def retrbinary(self, command, callback):
        _cmd, path = command.split(" ", 1)
        value = self.fs.get(se._ftp_norm(path))
        if not isinstance(value, bytes):
            raise se.ftplib.error_perm("not a file")
        callback(value)

    def storbinary(self, command, fp):
        _cmd, path = command.split(" ", 1)
        self.fs[se._ftp_norm(path)] = fp.read()


class DummyResponse:
    def __init__(self, content: bytes):
        self.content = content
        self.headers = {"X-Save-Hash": hashlib.sha256(content).hexdigest()}

    def raise_for_status(self):
        pass


class RejectLoginFTP(FakeFTP):
    def login(self, *args):
        raise se.ftplib.error_perm("530")


def install_fake_ftp(monkeypatch, fs):
    instances = []

    def factory():
        ftp = FakeFTP(fs)
        instances.append(ftp)
        return ftp

    monkeypatch.setattr(se.ftplib, "FTP", factory)
    return instances


def reset_remote_hash_cache(monkeypatch, tmp_path):
    monkeypatch.setattr(se, "REMOTE_HASH_CACHE_FILE", tmp_path / ".remote_hash_cache.json")
    monkeypatch.setattr(se, "_REMOTE_HASH_CACHE", None)
    monkeypatch.setattr(se, "_REMOTE_HASH_CACHE_DIRTY", False)


def ftp_profile(system: str, root: str = "/") -> dict:
    return {
        "name": f"MCP FTP {system}",
        "device_type": "MemCard Pro FTP",
        "ftp_host": "192.0.2.10",
        "ftp_port": 21,
        "ftp_username": "user",
        "ftp_password": "pass",
        "path": root,
        "system": system,
        "save_ext": ".mc2" if system == "PS2" else ".mcd",
    }


def usb_memcard_profile(system: str, root) -> dict:
    return {
        "name": f"MCP USB {system}",
        "device_type": "MemCard Pro",
        "path": str(root),
        "system": system,
        "save_ext": ".mc2" if system == "PS2" else ".mcd",
    }


def test_scan_memcard_pro_ftp_ps2(monkeypatch):
    fs = {
        "/": None,
        "/PS2": None,
        "/PS2/SLUS-20002": None,
        "/PS2/SLUS-20002/SLUS-20002-1.mc2": b"ps2 card",
        "/PS2/SLUS-20002/name.txt": b"Ridge Racer V\n",
    }
    install_fake_ftp(monkeypatch, fs)

    saves = se.scan_profile(ftp_profile("PS2"), enable_auto_normalize=False)

    assert len(saves) == 1
    save = saves[0]
    assert save.title_id == "SLUS20002"
    assert save.game_name == "Ridge Racer V"
    assert save.hash == hashlib.sha256(b"ps2 card").hexdigest()
    assert isinstance(save.path, se.FtpSavePath)
    assert save.path.remote_path == "/PS2/SLUS-20002/SLUS-20002-1.mc2"


def test_scan_memcard_pro_ftp_gc_hashes_extracted_gci(monkeypatch):
    raw = bytearray(b"\xff" * 0x8000)
    entry = bytearray(b"\xff" * 64)
    entry[0:4] = b"GBZE"
    struct.pack_into(">H", entry, 54, 3)
    struct.pack_into(">H", entry, 56, 1)
    raw[0x2000 : 0x2000 + 64] = entry
    raw[0x6000:0x8000] = b"A" * 0x2000
    fs = {
        "/": None,
        "/GC": None,
        "/GC/DL-DOL-GBZE-USA": None,
        "/GC/DL-DOL-GBZE-USA/DL-DOL-GBZE-USA-1.raw": bytes(raw),
    }
    install_fake_ftp(monkeypatch, fs)

    saves = se.scan_profile(ftp_profile("GC"), enable_auto_normalize=False)

    assert len(saves) == 1
    assert saves[0].title_id == "GC_GBZE"
    assert saves[0].hash == hashlib.sha256(bytes(entry) + b"A" * 0x2000).hexdigest()


def test_memcard_pro_ftp_download_and_metadata_write(monkeypatch):
    fs = {
        "/": None,
        "/PS2": None,
    }
    install_fake_ftp(monkeypatch, fs)
    monkeypatch.setattr(se, "_update_state", lambda title_id, hash_val: None)
    monkeypatch.setattr(
        se.requests,
        "get",
        lambda *args, **kwargs: DummyResponse(b"server card"),
    )

    profile = ftp_profile("PS2")
    dest = se.build_memcard_pro_ftp_path(profile, "SLUS20002", "PS2")
    server_hash = se.download_save(
        "SLUS20002",
        dest,
        "http://server",
        {"X-API-Key": "x"},
        system="PS2",
    )
    se.finalize_memcard_pro_download(dest, "Ridge Racer V")

    assert server_hash == hashlib.sha256(b"server card").hexdigest()
    assert fs["/PS2/SLUS-20002/SLUS-20002-1.mc2"] == b"server card"
    assert fs["/PS2/SLUS-20002/name.txt"] == b"Ridge Racer V\n"


def test_memcard_pro_ftp_login_error_is_actionable(monkeypatch):
    fs = {"/": None}
    monkeypatch.setattr(se.ftplib, "FTP", lambda: RejectLoginFTP(fs))

    with pytest.raises(se.SyncUserError) as excinfo:
        se.scan_profile(ftp_profile("PS2"), enable_auto_normalize=False)

    message = str(excinfo.value)
    assert "MemCard Pro FTP login failed (530)" in message
    assert "sent username 'user'" in message
    assert "rejected the password" in message
    assert "power-cycle" in message


def test_memcard_pro_ftp_reuses_cached_hash_when_metadata_matches(monkeypatch, tmp_path):
    reset_remote_hash_cache(monkeypatch, tmp_path)
    fs = {
        "/": None,
        "/PS2": None,
        "/PS2/SLUS-20002": None,
        "/PS2/SLUS-20002/SLUS-20002-1.mc2": b"ps2 card",
        "/PS2/SLUS-20002/name.txt": b"Ridge Racer V\n",
    }
    install_fake_ftp(monkeypatch, fs)
    original_download = se._ftp_download_bytes
    card_downloads = []

    def tracking_download(ftp, path, expected_size=None):
        if path.endswith(".mc2"):
            card_downloads.append((path, expected_size))
        return original_download(ftp, path, expected_size=expected_size)

    monkeypatch.setattr(se, "_ftp_download_bytes", tracking_download)

    first = se.scan_profile(ftp_profile("PS2"), enable_auto_normalize=False)
    monkeypatch.setattr(se, "_REMOTE_HASH_CACHE", None)
    second = se.scan_profile(ftp_profile("PS2"), enable_auto_normalize=False)

    assert first[0].hash == hashlib.sha256(b"ps2 card").hexdigest()
    assert second[0].hash == first[0].hash
    assert card_downloads == [("/PS2/SLUS-20002/SLUS-20002-1.mc2", len(b"ps2 card"))]


def test_memcard_pro_ftp_rehashes_when_mtime_changes(monkeypatch, tmp_path):
    reset_remote_hash_cache(monkeypatch, tmp_path)
    fs = {
        "/": None,
        "/PS2": None,
        "/PS2/SLUS-20002": None,
        "/PS2/SLUS-20002/SLUS-20002-1.mc2": b"old card",
        "/PS2/SLUS-20002/name.txt": b"Ridge Racer V\n",
    }
    install_fake_ftp(monkeypatch, fs)
    mtime = {"value": 1.0}
    monkeypatch.setattr(
        se,
        "_ftp_mtime",
        lambda ftp, path: mtime["value"] if path.endswith(".mc2") else 0.0,
    )
    original_download = se._ftp_download_bytes
    card_downloads = []

    def tracking_download(ftp, path, expected_size=None):
        if path.endswith(".mc2"):
            card_downloads.append((path, expected_size))
        return original_download(ftp, path, expected_size=expected_size)

    monkeypatch.setattr(se, "_ftp_download_bytes", tracking_download)

    first = se.scan_profile(ftp_profile("PS2"), enable_auto_normalize=False)
    fs["/PS2/SLUS-20002/SLUS-20002-1.mc2"] = b"new card"
    mtime["value"] = 2.0
    monkeypatch.setattr(se, "_REMOTE_HASH_CACHE", None)
    second = se.scan_profile(ftp_profile("PS2"), enable_auto_normalize=False)

    assert first[0].hash == hashlib.sha256(b"old card").hexdigest()
    assert second[0].hash == hashlib.sha256(b"new card").hexdigest()
    assert card_downloads == [
        ("/PS2/SLUS-20002/SLUS-20002-1.mc2", len(b"old card")),
        ("/PS2/SLUS-20002/SLUS-20002-1.mc2", len(b"new card")),
    ]


def test_memcard_pro_usb_hash_cache_seeds_matching_ftp_scan(monkeypatch, tmp_path):
    reset_remote_hash_cache(monkeypatch, tmp_path)
    mtime = se._ftp_parse_modify("20260505000000")
    usb_root = tmp_path / "usb"
    usb_card_dir = usb_root / "PS2" / "SLUS-20002"
    usb_card_dir.mkdir(parents=True)
    usb_card = usb_card_dir / "SLUS-20002-1.mc2"
    usb_card.write_bytes(b"ps2 card")
    (usb_card_dir / "name.txt").write_text("Ridge Racer V\n", encoding="utf-8")
    os.utime(usb_card, (mtime, mtime))

    usb_saves = se.scan_profile(
        usb_memcard_profile("PS2", usb_root),
        enable_auto_normalize=False,
    )
    assert usb_saves[0].hash == hashlib.sha256(b"ps2 card").hexdigest()

    fs = {
        "/": None,
        "/PS2": None,
        "/PS2/SLUS-20002": None,
        "/PS2/SLUS-20002/SLUS-20002-1.mc2": b"ps2 card",
        "/PS2/SLUS-20002/name.txt": b"Ridge Racer V\n",
    }
    install_fake_ftp(monkeypatch, fs)
    original_download = se._ftp_download_bytes
    card_downloads = []

    def tracking_download(ftp, path, expected_size=None):
        if path.endswith(".mc2"):
            card_downloads.append((path, expected_size))
        return original_download(ftp, path, expected_size=expected_size)

    monkeypatch.setattr(se, "_REMOTE_HASH_CACHE", None)
    monkeypatch.setattr(se, "_ftp_download_bytes", tracking_download)

    ftp_saves = se.scan_profile(ftp_profile("PS2"), enable_auto_normalize=False)

    assert ftp_saves[0].hash == usb_saves[0].hash
    assert card_downloads == []
