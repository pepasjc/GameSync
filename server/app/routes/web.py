"""Web UI route.

GET  /   — Serves the library web interface (no auth required).
          The server injects the API key into the page so the browser
          never has to prompt the user for credentials.
"""

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from app.config import settings

router = APIRouter()

_TEMPLATE = Path(__file__).parent.parent / "templates" / "index.html"


@router.get("/", response_class=HTMLResponse, include_in_schema=False)
async def web_ui():
    """Serve the SaveSync web library UI with the API key pre-injected."""
    html = _TEMPLATE.read_text(encoding="utf-8")
    # Replace the placeholder with the real key so JS can call the API
    # transparently without the user ever seeing or typing it.
    html = html.replace("__API_KEY__", settings.api_key, 1)
    return HTMLResponse(html)
