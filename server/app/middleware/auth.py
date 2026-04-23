from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app.config import settings

# Paths that never require an API key
_OPEN_PATHS = {"/", "/docs", "/openapi.json", "/api/v1/status"}


class APIKeyMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.url.path in _OPEN_PATHS:
            return await call_next(request)

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
