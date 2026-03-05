from __future__ import annotations

import re
from dataclasses import dataclass, field

from pydantic import BaseModel, field_validator


BUNDLE_MAGIC = b"3DSS"
BUNDLE_VERSION = 1
BUNDLE_VERSION_COMPRESSED = 2
BUNDLE_VERSION_V3 = 3  # String title_id for PSP/Vita (16 bytes ASCII, null-padded)

# Accepts 16-char hex IDs (3DS/DS) OR 4-16 alphanumeric product codes (PSP/Vita)
_HEX_TITLE_ID_RE = re.compile(r"^[0-9A-F]{16}$")
_PRODUCT_CODE_RE = re.compile(r"^[A-Z0-9]{4,31}$")


def is_hex_title_id(title_id: str) -> bool:
    return bool(_HEX_TITLE_ID_RE.match(title_id.upper()))


def validate_any_title_id(v: str) -> str:
    """Accept 16-char hex (3DS/DS) or 4-16 alphanumeric product codes (PSP/Vita)."""
    v = v.upper().strip()
    if _HEX_TITLE_ID_RE.match(v) or _PRODUCT_CODE_RE.match(v):
        return v
    raise ValueError(
        "title_id must be a 16-char hex string (3DS/DS) "
        "or a 4-16 char alphanumeric product code (PSP/Vita)"
    )


@dataclass
class BundleFile:
    path: str
    size: int
    sha256: bytes  # 32 bytes
    data: bytes = b""


@dataclass
class SaveBundle:
    title_id: int           # 64-bit int for v1/v2 (3DS/DS); 0 for v3
    timestamp: int          # unix epoch
    files: list[BundleFile] = field(default_factory=list)
    title_id_str: str = ""  # non-empty for v3 bundles (PSP/Vita product codes)

    @property
    def title_id_hex(self) -> str:
        return f"{self.title_id:016X}"

    @property
    def effective_title_id(self) -> str:
        """Title ID as used in URL paths and storage: string for PSP/Vita, hex for 3DS/DS."""
        return self.title_id_str if self.title_id_str else self.title_id_hex

    @property
    def total_size(self) -> int:
        return sum(f.size for f in self.files)


@dataclass
class SaveMetadata:
    title_id: str
    name: str
    last_sync: str  # ISO 8601
    last_sync_source: str
    save_hash: str  # sha256 of the full bundle
    save_size: int
    file_count: int
    client_timestamp: int  # timestamp reported by the device
    server_timestamp: str  # server wall-clock time at upload
    console_id: str = ""   # ID of the console that uploaded this save
    platform: str = ""     # "3DS", "NDS", "PSP", "PSX", "VITA"

    def to_dict(self) -> dict:
        return {
            "title_id": self.title_id,
            "name": self.name,
            "last_sync": self.last_sync,
            "last_sync_source": self.last_sync_source,
            "save_hash": self.save_hash,
            "save_size": self.save_size,
            "file_count": self.file_count,
            "client_timestamp": self.client_timestamp,
            "server_timestamp": self.server_timestamp,
            "console_id": self.console_id,
            "platform": self.platform,
        }


class TitleSyncInfo(BaseModel):
    """Metadata for a single title sent during sync."""
    title_id: str
    save_hash: str
    timestamp: int
    size: int
    last_synced_hash: str | None = None
    console_id: str | None = None

    @field_validator("title_id")
    @classmethod
    def validate_title_id(cls, v: str) -> str:
        return validate_any_title_id(v)


class SyncRequest(BaseModel):
    """Batch metadata from client for sync planning."""
    titles: list[TitleSyncInfo]
    console_id: str | None = None


class ConflictInfo(BaseModel):
    """Details about a conflicting save to help user decide."""
    title_id: str
    server_hash: str
    server_size: int
    server_timestamp: str  # ISO 8601 when server version was uploaded
    server_console_id: str  # Which console uploaded the server version
    client_hash: str
    client_size: int
    same_console: bool  # True if conflict is with our own previous upload


class SyncPlan(BaseModel):
    """Server's response telling the client what to do."""
    upload: list[str]       # title IDs where client is newer -> should upload
    download: list[str]     # title IDs where server is newer -> should download
    conflict: list[str]     # title IDs where both changed -> needs user decision
    up_to_date: list[str]   # title IDs with matching hashes
    server_only: list[str]  # title IDs only on server -> client may want to download
    conflict_info: list[ConflictInfo] = []  # Details for each conflict
