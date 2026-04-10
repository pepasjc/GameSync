from pathlib import Path
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    save_dir: Path = Path(__file__).parent.parent / "saves"
    rom_dir: Path | None = None
    api_key: str = "anything"
    host: str = "0.0.0.0"
    port: int = 8000
    max_history_versions: int = 10
    rom_scan_interval: int = 300

    model_config = {
        "env_prefix": "SYNC_",
        "env_file": ".env",
        "env_file_encoding": "utf-8",
    }


settings = Settings()
