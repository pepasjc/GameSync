from pathlib import Path
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    save_dir: Path = Path(__file__).parent.parent / "saves"
    rom_dir: Path | None = None
    # Optional override for where ROM-conversion working directories are
    # created.  When unset, ``tempfile.gettempdir()`` is used (typically
    # ``/tmp``, which is a 1.9 GB tmpfs on a default Raspberry Pi).
    #
    # 3DS games can decompress + decrypt to 4+ GB of intermediate files,
    # so a Pi or any host with a small ``/tmp`` should point this at a
    # spinning-disk or SSD path with plenty of headroom.  Example:
    #
    #     SYNC_TMP_DIR=/mnt/hd/tmp
    #
    # We pass this value as ``dir=`` to every ``tempfile.mkdtemp()``
    # call in ``app/routes/roms.py`` and the mcr2vmp tool, which means
    # we don't have to rely on the ``TMPDIR`` env var — uv's bundled
    # Python build (python-build-standalone) silently strips ``TMPDIR``
    # on startup, so the env-var approach is unreliable here.
    tmp_dir: Path | None = None
    # Optional command templates for 3DS ROM conversion.
    # Supports either a shell-style string or a JSON array of args.
    # Available placeholders: {input}, {output}, {output_dir}, {stem}
    #
    # Only two formats are exposed: CIA (decrypted, installable on CFW 3DS AND
    # usable in emulators — covered by a single command) and decrypted CCI
    # (for emulators that prefer the CCI container).
    rom_3ds_cia_command: str = ""
    rom_3ds_decrypted_cci_command: str = ""
    # Optional command templates for Xbox / Xbox 360 ROM conversion.
    # Same placeholder set as the 3DS commands: {input}, {output},
    # {output_dir}, {stem}.  The expected outputs are a single .iso
    # for ``rom_xbox_iso_command`` (decompresses Xbox CCI / xiso to a
    # plain ISO) and a .zip of the extracted game-folder layout for
    # ``rom_xbox_folder_command`` (e.g. ``extract-xiso -x`` followed
    # by zipping the resulting directory).  Both stay empty until the
    # operator configures the toolchain — until then the server
    # returns 503 with a hint pointing at SYNC_ROM_XBOX_*.
    rom_xbox_iso_command: str = ""
    rom_xbox_folder_command: str = ""
    # Optional command template for converting a PS1 disc image to a PSP
    # EBOOT.PBP (popstation-style) so the PSP client can drop the result
    # into ms0:/PSP/GAME/<id>/ and play PS1 games on real PSP hardware.
    # Same placeholder set as the other extract commands: {input},
    # {output}, {output_dir}, {stem}.  Expected output is a single file
    # at {output} (an EBOOT.PBP).  Recommended Pi-friendly tools:
    #   - ``psx2psp`` (Python, no compile)
    #   - ``popstation_md`` (C, builds with ``make``)
    # Empty by default; until set the server returns 503 with a hint
    # pointing at SYNC_ROM_PS1_EBOOT_COMMAND.
    rom_ps1_eboot_command: str = ""
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
        # Ignore unknown SYNC_* vars so old .env files (e.g. with the removed
        # SYNC_ROM_3DS_DECRYPTED_CIA_COMMAND) don't crash the server on boot.
        "extra": "ignore",
    }


settings = Settings()
