"""Store a RetroAchievements connect token in .env for the DS achievement routes.

    uv run python ra_login.py

Asks for the RA password, trades it for a token (the password is not saved)
and writes SYNC_RA_USERNAME / SYNC_RA_TOKEN to server/.env.  Restart the
server afterwards.
"""

import getpass
import sys
from pathlib import Path

from app.config import settings
from app.services import ra_connect

ENV_PATH = Path(__file__).parent / ".env"


def _set_env(lines: list[str], key: str, value: str) -> list[str]:
    out = [ln for ln in lines if not ln.startswith(f"{key}=")]
    out.append(f"{key}={value}")
    return out


def main() -> int:
    username = input(f"RetroAchievements username [{settings.ra_username}]: ").strip() \
        or settings.ra_username
    if not username:
        print("username required")
        return 1
    password = getpass.getpass("Password (not stored): ")
    try:
        token = ra_connect.login(username, password)
    except Exception as exc:  # noqa: BLE001 - report and exit
        print(f"login failed: {exc}")
        return 1

    lines = []
    if ENV_PATH.exists():
        original = ENV_PATH.read_text(encoding="utf-8")
        ENV_PATH.with_name(".env.bak-ra-login").write_text(original, encoding="utf-8")
        lines = original.splitlines()
    lines = _set_env(lines, "SYNC_RA_USERNAME", username)
    lines = _set_env(lines, "SYNC_RA_TOKEN", token)
    ENV_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"token saved to {ENV_PATH} — restart the server to pick it up")
    return 0


if __name__ == "__main__":
    sys.exit(main())
