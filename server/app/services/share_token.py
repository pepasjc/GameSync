"""HMAC-signed download tokens for shareable links.

Goal: let the WebUI mint a self-contained URL that lets a recipient
download a specific ROM or save *without* exposing the server's API
key in the URL.  The token is bound to:

  * the request path (so a token for ``/api/v1/roms/X`` can't be
    replayed against ``/api/v1/roms/Y`` or ``/api/v1/saves/Y``)
  * an expiry timestamp (default 7 days, capped at 30)

We sign with HMAC-SHA256 keyed on ``settings.api_key`` so rotating the
API key invalidates every previously-issued share link.  That doubles
as a built-in revocation mechanism — change the key, every old link
goes 401.

Token format::

    <urlsafe-b64(expires_unix_seconds)>.<urlsafe-b64(sig_first_16_bytes)>

We truncate the HMAC to 16 bytes (128 bits) — plenty for a download
authorisation that's already scoped to a path + expiry, and keeps the
URL short enough to copy-paste comfortably.
"""

from __future__ import annotations

import base64
import hmac
import hashlib
import time

from app.config import settings


# Default lifetime — 7 days is short enough to limit blast radius if a
# link leaks and long enough to cover "share with a friend, they grab it
# next weekend".  Callers can override up to ``MAX_TTL_SECONDS``.
DEFAULT_TTL_SECONDS = 7 * 86400
MAX_TTL_SECONDS = 30 * 86400
MIN_TTL_SECONDS = 60  # one minute floor — anything shorter is a self-DoS


def _b64encode(b: bytes) -> str:
    """URL-safe base64 without padding (cleaner-looking URLs)."""
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")


def _b64decode(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


def _sign(path: str, expires: int) -> bytes:
    """Compute the 16-byte HMAC tag for ``(path, expires)``."""
    # ``api_key`` may be empty in dev — use a non-empty stand-in so HMAC
    # doesn't choke and so dev builds don't accidentally accept ``""``
    # as a valid signing secret.
    secret = (settings.api_key or "missing-share-secret").encode("utf-8")
    msg = f"{path}|{expires}".encode("utf-8")
    return hmac.new(secret, msg, hashlib.sha256).digest()[:16]


def make(path: str, ttl_seconds: int = DEFAULT_TTL_SECONDS) -> tuple[str, int]:
    """Return ``(token, expires_unix_seconds)`` for the given path.

    Caller is responsible for sanity-checking ``path`` against the
    allow-list of routes that may be shared (see ``share_link`` in
    ``routes/roms.py``); this function blindly signs whatever it's
    given.
    """
    ttl = max(MIN_TTL_SECONDS, min(int(ttl_seconds), MAX_TTL_SECONDS))
    expires = int(time.time()) + ttl
    sig = _sign(path, expires)
    token = f"{_b64encode(str(expires).encode())}.{_b64encode(sig)}"
    return token, expires


def verify(path: str, token: str) -> bool:
    """True iff ``token`` was issued for ``path`` and hasn't expired."""
    if not token or "." not in token:
        return False
    try:
        exp_part, sig_part = token.split(".", 1)
        expires = int(_b64decode(exp_part).decode("ascii"))
        sig = _b64decode(sig_part)
    except (ValueError, UnicodeDecodeError):
        return False
    if expires < int(time.time()):
        return False
    expected = _sign(path, expires)
    # constant-time comparison — prevents timing attacks against the tag.
    return hmac.compare_digest(expected, sig)


# Routes that share-link tokens are allowed to authorise.  Kept
# narrow on purpose: any GET we'd be willing to expose via a
# password-less share link.  Reads only — never POST/PUT/DELETE.
SHAREABLE_PATH_PREFIXES = (
    "/api/v1/roms/",
    "/api/v1/saves/",
)


def is_shareable_path(path: str) -> bool:
    """Check ``path`` matches an allow-listed prefix.

    Excludes admin-shaped sub-routes (``/roms/scan``, ``/roms/systems``,
    ``/roms/share-link``) so a leaked token can't pivot to listing or
    rescanning.
    """
    if not any(path.startswith(p) for p in SHAREABLE_PATH_PREFIXES):
        return False
    # Block any sub-route that isn't a download.
    forbidden_tail = ("/scan", "/systems", "/share-link")
    if any(path.endswith(tail) for tail in forbidden_tail):
        return False
    # Bare prefix without an id ("/api/v1/roms/") is a list endpoint —
    # not shareable either.
    for prefix in SHAREABLE_PATH_PREFIXES:
        if path == prefix or path == prefix.rstrip("/"):
            return False
    return True
