from pathlib import Path
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    save_dir: Path = Path(__file__).parent.parent / "saves"
    rom_dir: Path | None = None
    # Optional command templates for 3DS ROM conversion.
    # Supports either a shell-style string or a JSON array of args.
    # Available placeholders: {input}, {output}, {output_dir}, {stem}
    rom_3ds_cia_command: str = ""
    rom_3ds_decrypted_cia_command: str = ""
    api_key: str = "anything"
    host: str = "0.0.0.0"
    port: int = 8000
    max_history_versions: int = 10
    rom_scan_interval: int = 300
    site_title: str = "GameSync"
    # Comma-separated list of nginx Basic Auth usernames that get admin access.
    # Everyone else can download but cannot trigger rescans or change settings.
    # Example: SYNC_ADMIN_USERS=admin,pepas
    admin_users: str = "admin"

    @property
    def admin_users_set(self) -> frozenset[str]:
        return frozenset(u.strip() for u in self.admin_users.split(",") if u.strip())

    model_config = {
        "env_prefix": "SYNC_",
        "env_file": ".env",
        "env_file_encoding": "utf-8",
    }


settings = Settings()
