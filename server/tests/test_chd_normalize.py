"""zstd CHDs are rewritten to standard codecs - and only when provably identical."""
import subprocess

import pytest

from app.services import chd_normalize as cn


def _header(codecs=(b"cdzs", b"cdlz", b"cdzl", b"cdfl"), sha=b"\x11" * 20, version=5):
    h = bytearray(124)
    h[0:8] = b"MComprHD"
    h[8:12] = (124).to_bytes(4, "big")
    h[12:16] = version.to_bytes(4, "big")
    for i, c in enumerate(codecs):
        h[16 + 4 * i: 20 + 4 * i] = c
    h[64:84] = sha
    return bytes(h)


def test_zstd_detection():
    assert cn.is_zstd(_header())
    assert cn.is_zstd(_header((b"zstd", b"lzma", b"zlib", b"huff")))
    assert not cn.is_zstd(_header((b"cdlz", b"cdzl", b"cdfl", b"\0\0\0\0")))
    assert not cn.is_zstd(_header(version=4))
    assert not cn.is_zstd(b"not a chd" + bytes(120))


class _FakeChdman:
    """Stands in for subprocess.run: ``copy`` writes ``out_header``."""

    def __init__(self, out_header, copy_rc=0, verify_rc=0):
        self.out_header = out_header
        self.copy_rc = copy_rc
        self.verify_rc = verify_rc
        self.calls = []

    def __call__(self, cmd, capture_output=True, text=True):
        cmd = [c for c in cmd if c not in ("nice", "-n", "19")]
        self.calls.append(cmd)
        if cmd[1] == "copy":
            out = cmd[cmd.index("-o") + 1]
            if self.copy_rc == 0:
                with open(out, "wb") as fh:
                    fh.write(self.out_header + b"payload")
            return subprocess.CompletedProcess(cmd, self.copy_rc, "", "")
        return subprocess.CompletedProcess(cmd, self.verify_rc, "", "")


@pytest.fixture
def chd(tmp_path):
    p = tmp_path / "Game (USA).chd"
    p.write_bytes(_header() + b"original")
    return p


def test_identical_image_replaces_the_original(chd, monkeypatch):
    fake = _FakeChdman(_header((b"cdlz", b"cdzl", b"cdfl", b"\0\0\0\0")))
    monkeypatch.setattr(cn.subprocess, "run", fake)
    changed, _ = cn.recompress(chd)
    assert changed
    assert chd.read_bytes().endswith(b"payload")
    assert not cn.is_zstd(cn.read_header(chd))
    copy = fake.calls[0]
    assert copy[copy.index("-c") + 1] == cn.CD_CODECS
    assert not list(chd.parent.glob("*.tmp"))


def test_dvd_images_get_dvd_codecs(tmp_path, monkeypatch):
    p = tmp_path / "Game.chd"
    p.write_bytes(_header((b"zstd", b"lzma", b"zlib", b"huff")))
    fake = _FakeChdman(_header((b"lzma", b"zlib", b"huff", b"flac")))
    monkeypatch.setattr(cn.subprocess, "run", fake)
    assert cn.recompress(p)[0]
    assert fake.calls[0][fake.calls[0].index("-c") + 1] == cn.DVD_CODECS


@pytest.mark.parametrize("fake", [
    _FakeChdman(_header((b"cdlz",) * 4, sha=b"\x22" * 20)),      # different image
    _FakeChdman(_header((b"cdlz",) * 4), verify_rc=1),              # verify failed
    _FakeChdman(_header((b"cdlz",) * 4), copy_rc=1),                # copy failed
    _FakeChdman(_header()),                                         # still zstd
])
def test_anything_short_of_identical_keeps_the_original(chd, monkeypatch, fake):
    monkeypatch.setattr(cn.subprocess, "run", fake)
    changed, _ = cn.recompress(chd)
    assert not changed
    assert chd.read_bytes().endswith(b"original")
    assert not list(chd.parent.glob("*.tmp"))


def test_normalize_skips_non_zstd_and_remembers_failures(tmp_path, monkeypatch):
    good = tmp_path / "a.chd"
    good.write_bytes(_header((b"cdlz",) * 4))
    bad = tmp_path / "b.chd"
    bad.write_bytes(_header())
    fake = _FakeChdman(_header((b"cdlz",) * 4, sha=b"\x33" * 20))   # never identical
    monkeypatch.setattr(cn.subprocess, "run", fake)
    monkeypatch.setattr(cn.shutil, "which", lambda name: "/usr/bin/" + name)
    cn._failed.clear()
    first = cn.normalize([good, bad])
    assert first == {"checked": 2, "recompressed": 0, "failed": 1}
    calls = len(fake.calls)
    second = cn.normalize([good, bad])
    assert second["failed"] == 0 and len(fake.calls) == calls        # not retried
    cn._failed.clear()


def test_catalog_paths_resolve_against_rom_dir(tmp_path):
    class E:
        def __init__(self, path, is_bundle=False):
            self.path, self.is_bundle = path, is_bundle
    paths = cn.catalog_chds([E("ps1/a.chd"), E("ps1/b.cue"), E("ps1/set", True), E("ps2/c.CHD")], tmp_path)
    assert paths == [tmp_path / "ps1/a.chd", tmp_path / "ps2/c.CHD"]
