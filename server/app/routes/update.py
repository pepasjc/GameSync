"""Update checking endpoint - proxies GitHub releases for 3DS/NDS clients."""

from fastapi import APIRouter
from pydantic import BaseModel
import httpx

router = APIRouter()

# GitHub repository info
# The server proxies GitHub releases API because 3DS/NDS can't do HTTPS
GITHUB_OWNER = "pepasjc"
GITHUB_REPO = "GameSync"
GITHUB_API_URL = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/releases/latest"

# Map platform to expected asset file extension
PLATFORM_EXTENSIONS = {
    "3ds": ".cia",
    "nds": ".nds",
}


class UpdateInfo(BaseModel):
    available: bool
    current_version: str
    latest_version: str | None = None
    download_url: str | None = None
    changelog: str | None = None
    file_size: int | None = None


@router.get("/update/check")
async def check_update(current: str = "0.0.0", platform: str = "3ds") -> UpdateInfo:
    """Check if a newer version is available.

    Args:
        current: The client's current version (e.g., "0.4.3")
        platform: The client platform ("3ds" or "nds")

    Returns:
        Update info with download URL if available.
    """
    extension = PLATFORM_EXTENSIONS.get(platform, ".cia")

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                GITHUB_API_URL,
                headers={"Accept": "application/vnd.github.v3+json"},
                timeout=10.0,
            )

            if resp.status_code != 200:
                return UpdateInfo(available=False, current_version=current)

            data = resp.json()
            latest_version = data.get("tag_name", "").lstrip("v")

            # Find asset matching the platform extension
            download_url = None
            file_size = None
            for asset in data.get("assets", []):
                if asset["name"].endswith(extension):
                    download_url = asset["browser_download_url"]
                    file_size = asset["size"]
                    break

            # Compare versions
            is_newer = _compare_versions(latest_version, current) > 0

            return UpdateInfo(
                available=is_newer and download_url is not None,
                current_version=current,
                latest_version=latest_version,
                download_url=download_url,
                changelog=data.get("body", ""),
                file_size=file_size,
            )

    except Exception:
        # On any error, report no update available
        return UpdateInfo(available=False, current_version=current)


@router.get("/update/download")
async def proxy_download(url: str):
    """Proxy download from GitHub (3DS/NDS can't do HTTPS with GitHub).

    Downloads the full file then returns it with Content-Length,
    which is required by the DS client's HTTP/1.0 implementation.
    Homebrew binaries are small (a few MB) so this is fine.
    """
    from fastapi.responses import Response

    filename = url.rsplit("/", 1)[-1] if "/" in url else "update"

    async with httpx.AsyncClient() as client:
        resp = await client.get(url, follow_redirects=True, timeout=300.0)

    return Response(
        content=resp.content,
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": f"attachment; filename={filename}",
            "Content-Length": str(len(resp.content)),
        },
    )


def _compare_versions(v1: str, v2: str) -> int:
    """Compare two version strings. Returns >0 if v1 > v2, <0 if v1 < v2, 0 if equal."""
    def parse(v: str) -> list[int]:
        try:
            return [int(x) for x in v.split(".")]
        except ValueError:
            return [0]

    p1, p2 = parse(v1), parse(v2)
    # Pad to same length
    while len(p1) < len(p2):
        p1.append(0)
    while len(p2) < len(p1):
        p2.append(0)

    for a, b in zip(p1, p2):
        if a > b:
            return 1
        if a < b:
            return -1
    return 0
