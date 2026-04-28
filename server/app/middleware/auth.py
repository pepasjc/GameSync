from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app.config import settings
from app.services import share_token

# Paths that never require an API key
_OPEN_PATHS = {"/", "/docs", "/openapi.json", "/api/v1/status"}


class APIKeyMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.url.path in _OPEN_PATHS:
            return await call_next(request)

        # Share-link path: GET/HEAD on a download route with ``?token=...``
        # is authorised by an HMAC token bound to (path, expiry) instead
        # of the global API key.  This lets the WebUI mint copy-and-share
        # URLs without leaking the api_key.  The allow-list in
        # ``share_token.is_shareable_path`` keeps tokens scoped to read-
        # only download endpoints — they can never authorise rescans,
        # POSTs, or anything else.
        if request.method in ("GET", "HEAD"):
            token = request.query_params.get("token")
            if token and share_token.is_shareable_path(request.url.path):
                if share_token.verify(request.url.path, token):
                    return await call_next(request)
                # Bad/expired token: fall through to api_key check so a
                # legitimate api_key on the same request still wins.

        # Accept the key via header (devices) or query param (direct download links)
        api_key = (
            request.headers.get("X-API-Key")
            or request.query_params.get("api_key")
        )
        if api_key != settings.api_key:
            return JSONResponse(
                status_code=401,
                content={"detail": "Invalid or missing API key"},
            )

        return await call_next(request)
