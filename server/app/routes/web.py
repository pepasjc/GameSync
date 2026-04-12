"""Web UI route.

GET  /   — Serves the library web interface (no auth required).
          The server injects the API key and admin flag into the page.
          nginx must forward the authenticated username via X-Remote-User.
"""

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from app.config import settings

router = APIRouter()

_TEMPLATE = Path(__file__).parent.parent / "templates" / "index.html"


@router.get("/", response_class=HTMLResponse, include_in_schema=False)
async def web_ui(request: Request):
    """Serve the GameSync web UI with API key and role pre-injected."""
    # nginx passes the Basic Auth username via this header:
    #   proxy_set_header X-Remote-User $remote_user;
    # On LAN (no nginx / no auth) the header is absent → treat as admin.
    remote_user = request.headers.get("X-Remote-User", "")
    is_admin = (not remote_user) or (remote_user in settings.admin_users_set)

    html = _TEMPLATE.read_text(encoding="utf-8")
    html = html.replace("__API_KEY__",   settings.api_key,          1)
    html = html.replace("__SITE_TITLE__", settings.site_title)
    html = html.replace("__IS_ADMIN__",  "true" if is_admin else "false", 1)
    return HTMLResponse(html)
