"""PS2 memory-card filesystem (ps2mc) parser and builder.

This bridges the two representations of a PS2 save that Save Sync deals with:

  * a **per-game folder** — the list of files inside one ``B?SLUS-xxxxx`` save
    directory, as read file-by-file from a physical card via ``libmc`` on the
    PS2 client; and
  * an **8 MB card image** (canonical ``.mc2``) — what MemCard Pro stores per
    game and what OPL VMC files / emulators consume.

``parse_card`` splits a full card image into ``{dir_name: [(file_name, data)]}``.
``build_card`` does the reverse: it formats a fresh empty card and writes one or
more game folders back into it.  ``serial_from_dirname`` maps a save-dir name to
the compact server title id (e.g. ``BASLUS-20312`` -> ``SLUS20312``).

Format reference: ps2savetools.com PS2 Memory Card File System + ps2dev/mymc.
The card image operated on here is the *canonical* ``.mc2`` form (512-byte pages,
no 16-byte ECC spare); callers use ``ps2_cards.add_ecc``/``strip_ecc`` to convert
to/from the ``.ps2`` (ECC) form that PCSX2 expects.
"""

from __future__ import annotations

import re
import struct
from dataclasses import dataclass

# ---- Geometry (standard 8 MB card) ----

PAGE_SIZE = 512
PAGES_PER_CLUSTER = 2
CLUSTER_SIZE = PAGE_SIZE * PAGES_PER_CLUSTER          # 1024
PAGES_PER_BLOCK = 16
CLUSTERS_PER_CARD = 8192
CARD_SIZE = PAGE_SIZE * PAGES_PER_CLUSTER * CLUSTERS_PER_CARD  # 8 MB

ALLOC_OFFSET = 41          # first allocatable cluster
ALLOC_END = 8135           # highest allocatable cluster + 1
ROOTDIR_CLUSTER = 0        # relative to ALLOC_OFFSET
BACKUP_BLOCK1 = 1023
BACKUP_BLOCK2 = 1022
IFC_CLUSTER = 8            # ifc_list[0]: the single indirect-FAT cluster
FAT_CLUSTER_START = 9      # 32 FAT clusters live at 9..40
FAT_CLUSTER_COUNT = 32
FAT_ENTRIES_PER_CLUSTER = CLUSTER_SIZE // 4           # 256
DIRENTS_PER_CLUSTER = CLUSTER_SIZE // 512             # 2

# ---- FAT entry encoding ----

FAT_ALLOCATED_BIT = 0x80000000
FAT_CLUSTER_MASK = 0x7FFFFFFF
FAT_CHAIN_END = 0xFFFFFFFF          # allocated, last in chain
FAT_FREE = 0x7FFFFFFF               # unallocated (allocated bit clear)

# ---- Directory-entry mode flags ----

DF_READ = 0x0001
DF_WRITE = 0x0002
DF_EXECUTE = 0x0004
DF_RWX = DF_READ | DF_WRITE | DF_EXECUTE
DF_PROTECTED = 0x0008
DF_FILE = 0x0010
DF_DIRECTORY = 0x0020
DF_0400 = 0x0400
DF_HIDDEN = 0x2000
DF_EXISTS = 0x8000

MODE_DIR = DF_RWX | DF_DIRECTORY | DF_0400 | DF_EXISTS          # 0x8427
MODE_DOTDOT = DF_WRITE | DF_EXECUTE | DF_DIRECTORY | DF_0400 | DF_HIDDEN | DF_EXISTS  # 0xA426
MODE_FILE = DF_FILE | DF_RWX | DF_0400 | DF_EXISTS              # 0x8417

SUPERBLOCK_MAGIC = b"Sony PS2 Memory Card Format "  # 28 bytes
SUPERBLOCK_VERSION = b"1.2.0.0\x00\x00\x00\x00\x00"  # 12 bytes
CARD_TYPE = 2
CARD_FLAGS = 0x52

_SUPERBLOCK_FMT = "<28s12sHHHHLLLLLL8x128s128sbbxx"
assert struct.calcsize(_SUPERBLOCK_FMT) == 340

# dirent: mode(H) pad(2) length(L) created(8) cluster(L) dir_entry(L)
#         modified(8) attr(L) pad(28) name(32) pad(416)  == 512
_DIRENT_FMT = "<H2xL8sLL8sL28x32s416x"
assert struct.calcsize(_DIRENT_FMT) == 512

_TIMESTAMP_FMT = "<xBBBBBH"  # unused, sec, min, hour, day, month, year
assert struct.calcsize(_TIMESTAMP_FMT) == 8

# PS2 disc-serial prefixes; first letters of a save dir like "BASLUS-20312".
_PS2_SERIAL_RE = re.compile(
    r"(SLUS|SCUS|SLES|SCES|SLPS|SLPM|SLKA|SCAJ|SLAJ|SCKA|SLPN|TCPX|PBPX|PCPX)"
    r"[-_ ]?(\d{3})[.]?(\d{2})",
    re.IGNORECASE,
)


def serial_from_dirname(name: str) -> str | None:
    """Map a PS2 save-dir name to the compact server title id.

    ``BASLUS-20312`` / ``BISLPM-65432`` -> ``SLUS20312`` / ``SLPM65432``.
    Returns ``None`` for system/utility dirs that carry no game serial.
    """
    m = _PS2_SERIAL_RE.search(name)
    if not m:
        return None
    return (m.group(1) + m.group(2) + m.group(3)).upper()


# ---- low-level access ----


@dataclass
class Superblock:
    page_len: int
    pages_per_cluster: int
    pages_per_block: int
    clusters_per_card: int
    alloc_offset: int
    alloc_end: int
    rootdir_cluster: int
    ifc_list: list[int]
    card_type: int
    card_flags: int


@dataclass
class Dirent:
    mode: int
    length: int
    cluster: int
    dir_entry: int
    name: str


def _u32(buf: bytes, off: int) -> int:
    return struct.unpack_from("<L", buf, off)[0]


def parse_superblock(data: bytes) -> Superblock:
    fields = struct.unpack_from(_SUPERBLOCK_FMT, data, 0)
    (magic, _version, page_len, ppc, ppb, _unused, clusters_per_card,
     alloc_offset, alloc_end, rootdir_cluster, _bk1, _bk2,
     ifc_raw, _bad_raw, card_type, card_flags) = fields
    if not magic.startswith(b"Sony PS2 Memory Card Format"):
        raise ValueError("Not a PS2 memory card image (bad superblock magic)")
    ifc_list = list(struct.unpack("<32L", ifc_raw))
    return Superblock(
        page_len=page_len,
        pages_per_cluster=ppc,
        pages_per_block=ppb,
        clusters_per_card=clusters_per_card,
        alloc_offset=alloc_offset,
        alloc_end=alloc_end,
        rootdir_cluster=rootdir_cluster,
        ifc_list=ifc_list,
        card_type=card_type,
        card_flags=card_flags,
    )


def _cluster_bytes(data: bytes, cluster: int) -> bytes:
    off = cluster * CLUSTER_SIZE
    return data[off : off + CLUSTER_SIZE]


def _fat_value(data: bytes, sb: Superblock, index: int) -> int:
    """Read the FAT entry for allocatable cluster ``index``."""
    fat_offset = index % FAT_ENTRIES_PER_CLUSTER
    indirect_index = index // FAT_ENTRIES_PER_CLUSTER
    ifc = sb.ifc_list[indirect_index // FAT_ENTRIES_PER_CLUSTER]
    indirect_cluster = _cluster_bytes(data, ifc)
    fat_cluster_num = _u32(indirect_cluster, (indirect_index % FAT_ENTRIES_PER_CLUSTER) * 4)
    fat_cluster = _cluster_bytes(data, fat_cluster_num)
    return _u32(fat_cluster, fat_offset * 4)


def _set_fat_value(data: bytearray, sb: Superblock, index: int, value: int) -> None:
    fat_offset = index % FAT_ENTRIES_PER_CLUSTER
    indirect_index = index // FAT_ENTRIES_PER_CLUSTER
    ifc = sb.ifc_list[indirect_index // FAT_ENTRIES_PER_CLUSTER]
    indirect_cluster = _cluster_bytes(data, ifc)
    fat_cluster_num = _u32(indirect_cluster, (indirect_index % FAT_ENTRIES_PER_CLUSTER) * 4)
    struct.pack_into("<L", data, fat_cluster_num * CLUSTER_SIZE + fat_offset * 4, value)


def _next_cluster(data: bytes, sb: Superblock, index: int) -> int | None:
    entry = _fat_value(data, sb, index)
    if not (entry & FAT_ALLOCATED_BIT):
        return None
    nxt = entry & FAT_CLUSTER_MASK
    if nxt == FAT_CLUSTER_MASK:        # 0xFFFFFFFF terminal
        return None
    return nxt


def _chain(data: bytes, sb: Superblock, first: int, max_clusters: int) -> list[int]:
    out: list[int] = []
    cluster = first
    while cluster is not None and cluster != FAT_CLUSTER_MASK and len(out) < max_clusters:
        out.append(cluster)
        cluster = _next_cluster(data, sb, cluster)
    return out


def _unpack_dirent(buf: bytes) -> Dirent:
    mode, length, _created, cluster, dir_entry, _modified, _attr, name = \
        struct.unpack(_DIRENT_FMT, buf)
    return Dirent(
        mode=mode,
        length=length,
        cluster=cluster,
        dir_entry=dir_entry,
        name=name.split(b"\x00", 1)[0].decode("ascii", "replace"),
    )


def _read_dir(data: bytes, sb: Superblock, first_cluster: int, count: int) -> list[Dirent]:
    """Read ``count`` dirents from the directory starting at ``first_cluster``."""
    clusters_needed = (count + DIRENTS_PER_CLUSTER - 1) // DIRENTS_PER_CLUSTER
    clusters = _chain(data, sb, first_cluster, clusters_needed)
    entries: list[Dirent] = []
    for cluster in clusters:
        phys = sb.alloc_offset + cluster
        cbytes = _cluster_bytes(data, phys)
        for i in range(DIRENTS_PER_CLUSTER):
            if len(entries) >= count:
                break
            entries.append(_unpack_dirent(cbytes[i * 512 : (i + 1) * 512]))
    return entries


def _read_file(data: bytes, sb: Superblock, first_cluster: int, length: int) -> bytes:
    if length <= 0 or first_cluster == FAT_CLUSTER_MASK:
        return b""
    clusters_needed = (length + CLUSTER_SIZE - 1) // CLUSTER_SIZE
    clusters = _chain(data, sb, first_cluster, clusters_needed)
    out = bytearray()
    for cluster in clusters:
        phys = sb.alloc_offset + cluster
        out += _cluster_bytes(data, phys)
    return bytes(out[:length])


def parse_card(data: bytes) -> dict[str, list[tuple[str, bytes]]]:
    """Split a full card image into ``{game_dir_name: [(file_name, data), ...]}``.

    Only top-level save directories with their regular files are returned;
    ``.``/``..`` and nested directories are skipped.
    """
    if len(data) != CARD_SIZE:
        raise ValueError(f"Unsupported PS2 card size: {len(data)} (expected {CARD_SIZE})")

    sb = parse_superblock(data)

    # Entry count of the root directory lives in its own "." dirent.
    phys = sb.alloc_offset + sb.rootdir_cluster
    root_dot = _unpack_dirent(_cluster_bytes(data, phys)[:512])
    root_count = root_dot.length
    if root_count <= 0 or root_count > CLUSTERS_PER_CARD:
        raise ValueError("Corrupt root directory entry count")

    games: dict[str, list[tuple[str, bytes]]] = {}
    for ent in _read_dir(data, sb, sb.rootdir_cluster, root_count):
        if ent.name in (".", "..") or not (ent.mode & DF_EXISTS):
            continue
        if not (ent.mode & DF_DIRECTORY):
            continue
        files: list[tuple[str, bytes]] = []
        for f in _read_dir(data, sb, ent.cluster, ent.length):
            if f.name in (".", "..") or not (f.mode & DF_EXISTS):
                continue
            if f.mode & DF_DIRECTORY:
                continue
            files.append((f.name, _read_file(data, sb, f.cluster, f.length)))
        games[ent.name] = files
    return games


# ---- builder ----


def _pack_timestamp(ts: int) -> bytes:
    import time

    t = time.gmtime(ts + 9 * 3600) if ts else time.gmtime(0)  # PS2 stores JST
    return struct.pack(_TIMESTAMP_FMT, t.tm_sec, t.tm_min, t.tm_hour,
                       t.tm_mday, t.tm_mon, t.tm_year)


def _pack_dirent(name: str, mode: int, length: int, cluster: int,
                 dir_entry: int, ts: bytes) -> bytes:
    return struct.pack(
        _DIRENT_FMT,
        mode,
        length,
        ts,
        cluster,
        dir_entry,
        ts,
        0,                       # attr
        name.encode("ascii", "replace")[:32],
    )


def format_empty_card() -> bytearray:
    """Return a freshly formatted, empty 8 MB ``.mc2`` card image."""
    data = bytearray(CARD_SIZE)

    struct.pack_into(
        _SUPERBLOCK_FMT, data, 0,
        SUPERBLOCK_MAGIC,
        SUPERBLOCK_VERSION,
        PAGE_SIZE,
        PAGES_PER_CLUSTER,
        PAGES_PER_BLOCK,
        0xFF00,                  # unused half
        CLUSTERS_PER_CARD,
        ALLOC_OFFSET,
        ALLOC_END,
        ROOTDIR_CLUSTER,
        BACKUP_BLOCK1,
        BACKUP_BLOCK2,
        struct.pack("<32L", IFC_CLUSTER, *([0] * 31)),
        struct.pack("<32L", *([0xFFFFFFFF] * 32)),
        CARD_TYPE,
        CARD_FLAGS,
    )

    # Indirect FAT cluster (8): first 32 entries point at FAT clusters 9..40.
    ifc_off = IFC_CLUSTER * CLUSTER_SIZE
    for i in range(FAT_ENTRIES_PER_CLUSTER):
        val = (FAT_CLUSTER_START + i) if i < FAT_CLUSTER_COUNT else 0xFFFFFFFF
        struct.pack_into("<L", data, ifc_off + i * 4, val)

    # FAT clusters (9..40): every allocatable cluster starts free.
    for c in range(FAT_CLUSTER_START, FAT_CLUSTER_START + FAT_CLUSTER_COUNT):
        base = c * CLUSTER_SIZE
        for i in range(FAT_ENTRIES_PER_CLUSTER):
            struct.pack_into("<L", data, base + i * 4, FAT_FREE)

    # Empty but present root directory at allocatable cluster 0 ("." + "..").
    sb = parse_superblock(data)
    _set_fat_value(data, sb, ROOTDIR_CLUSTER, FAT_CHAIN_END)
    ts = _pack_timestamp(0)
    root_phys = sb.alloc_offset + ROOTDIR_CLUSTER
    data[root_phys * CLUSTER_SIZE : root_phys * CLUSTER_SIZE + 512] = \
        _pack_dirent(".", MODE_DIR, 2, 0, 0, ts)
    data[root_phys * CLUSTER_SIZE + 512 : root_phys * CLUSTER_SIZE + 1024] = \
        _pack_dirent("..", MODE_DOTDOT, 2, 0, 0, ts)

    return data


P2FD_MAGIC = b"P2FD"


def build_p2fd(dir_name: str, files: list[tuple[str, bytes]]) -> bytes:
    """Serialize one game's save folder into the compact P2FD wire format.

    Used by the PS2 client's physical-memory-card path: the console reads a
    game directory file-by-file via libmc and ships it here, where the server
    builds the canonical card image.  Layout (all little-endian)::

        "P2FD" | u32 version=1 | u32 dir_len | dir | u32 file_count |
        ( u32 name_len | name | u32 data_len | data ) * file_count
    """
    name_bytes = dir_name.encode("ascii", "replace")
    out = bytearray()
    out += P2FD_MAGIC
    out += struct.pack("<L", 1)
    out += struct.pack("<L", len(name_bytes))
    out += name_bytes
    out += struct.pack("<L", len(files))
    for fname, data in files:
        fn = fname.encode("ascii", "replace")
        out += struct.pack("<L", len(fn))
        out += fn
        out += struct.pack("<L", len(data))
        out += data
    return bytes(out)


def parse_p2fd(data: bytes) -> tuple[str, list[tuple[str, bytes]]]:
    """Parse a P2FD payload into ``(dir_name, [(file_name, data), ...])``."""
    if len(data) < 16 or data[:4] != P2FD_MAGIC:
        raise ValueError("Not a P2FD payload")
    pos = 4
    (version,) = struct.unpack_from("<L", data, pos); pos += 4
    if version != 1:
        raise ValueError(f"Unsupported P2FD version: {version}")
    (dir_len,) = struct.unpack_from("<L", data, pos); pos += 4
    if pos + dir_len > len(data):
        raise ValueError("Truncated P2FD directory name")
    dir_name = data[pos : pos + dir_len].decode("ascii", "replace"); pos += dir_len
    (count,) = struct.unpack_from("<L", data, pos); pos += 4
    files: list[tuple[str, bytes]] = []
    for _ in range(count):
        if pos + 4 > len(data):
            raise ValueError("Truncated P2FD file table")
        (name_len,) = struct.unpack_from("<L", data, pos); pos += 4
        fname = data[pos : pos + name_len].decode("ascii", "replace"); pos += name_len
        (data_len,) = struct.unpack_from("<L", data, pos); pos += 4
        if pos + data_len > len(data):
            raise ValueError("Truncated P2FD file data")
        files.append((fname, data[pos : pos + data_len])); pos += data_len
    return dir_name, files


def build_card(games: dict[str, list[tuple[str, bytes]]], timestamp: int = 0) -> bytes:
    """Build an 8 MB ``.mc2`` card image containing ``games``.

    ``games`` maps a save-dir name (e.g. ``BASLUS-20312``) to its files.  Used
    with a single entry to render one game's stored save back to a card image
    for MemCard Pro / VMC / emulator consumption.
    """
    data = format_empty_card()
    sb = parse_superblock(data)
    ts = _pack_timestamp(timestamp)

    def set_chain(start: int, n: int) -> None:
        for k in range(n):
            idx = start + k
            value = FAT_CHAIN_END if k == n - 1 else (FAT_ALLOCATED_BIT | (idx + 1))
            _set_fat_value(data, sb, idx, value)

    def write_dirents(start_cluster: int, dirents: list[bytes]) -> None:
        for i, ent in enumerate(dirents):
            phys = sb.alloc_offset + start_cluster + (i // DIRENTS_PER_CLUSTER)
            off = phys * CLUSTER_SIZE + (i % DIRENTS_PER_CLUSTER) * 512
            data[off : off + 512] = ent

    def write_file_data(start: int, blob: bytes) -> None:
        for k, base in enumerate(range(0, len(blob), CLUSTER_SIZE)):
            phys = sb.alloc_offset + start + k
            chunk = blob[base : base + CLUSTER_SIZE]
            data[phys * CLUSTER_SIZE : phys * CLUSTER_SIZE + len(chunk)] = chunk

    root_entry_count = 2 + len(games)
    root_clusters = (root_entry_count + DIRENTS_PER_CLUSTER - 1) // DIRENTS_PER_CLUSTER
    next_idx = root_clusters
    set_chain(0, root_clusters)

    root_dirents = [
        _pack_dirent(".", MODE_DIR, root_entry_count, 0, 0, ts),
        _pack_dirent("..", MODE_DOTDOT, root_entry_count, 0, 0, ts),
    ]

    for gi, (gname, files) in enumerate(games.items()):
        dir_entry_count = 2 + len(files)
        dir_clusters = (dir_entry_count + DIRENTS_PER_CLUSTER - 1) // DIRENTS_PER_CLUSTER
        dir_start = next_idx
        next_idx += dir_clusters
        set_chain(dir_start, dir_clusters)

        # This game's entry index within the root directory.
        entry_index = 2 + gi
        root_dirents.append(
            _pack_dirent(gname, MODE_DIR, dir_entry_count, dir_start, 0, ts)
        )

        dir_dirents = [
            _pack_dirent(".", MODE_DIR, dir_entry_count,
                         entry_index // DIRENTS_PER_CLUSTER, entry_index, ts),
            _pack_dirent("..", MODE_DOTDOT, root_entry_count, 0, 0, ts),
        ]
        for fname, fdata in files:
            n = (len(fdata) + CLUSTER_SIZE - 1) // CLUSTER_SIZE
            if n == 0:
                fstart = FAT_CLUSTER_MASK
            else:
                fstart = next_idx
                next_idx += n
                set_chain(fstart, n)
                write_file_data(fstart, fdata)
            dir_dirents.append(
                _pack_dirent(fname, MODE_FILE, len(fdata), fstart, 0, ts)
            )
        write_dirents(dir_start, dir_dirents)

    write_dirents(0, root_dirents)

    if next_idx > (ALLOC_END - ALLOC_OFFSET):
        raise ValueError("Save data exceeds PS2 card capacity")

    return bytes(data)
