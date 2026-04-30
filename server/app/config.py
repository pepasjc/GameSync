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
    # {output_dir}, {stem}.  ``rom_xbox_iso_command`` must write one .iso,
    # and ``rom_xbox_cci_command`` must write one .cci; the server wraps CCI
    # downloads in a .zip because CCI libraries may also carry launcher files.
    # XGDTool is the intended converter:
    #   XGDTool --xiso --quiet {input} {output_dir}
    #   XGDTool --cci --quiet {input} {output_dir}
    # Both stay empty until the operator configures the toolchain — until
    # then the server returns 503 with a hint pointing at SYNC_ROM_XBOX_*.
    rom_xbox_iso_command: str = ""
    rom_xbox_cci_command: str = ""
    # Optional command template for converting a PS1 disc image to a PSP
    # EBOOT.PBP so the PSP client can drop the result into
    # ms0:/PSP/GAME/<id>/ and play PS1 games on real PSP hardware.  The
    # template is invoked with these placeholders expanded:
    #   {inputs}     — one shell-escaped path per disc, space-joined
    #                  (multi-disc: "Disc1.chd Disc2.chd Disc3.chd")
    #   {input}      — primary-disc path only (single-disc convenience)
    #   {title}      — human-readable game name (e.g. "Final Fantasy VII")
    #   {gamecode}   — PS1 product code (e.g. "SCUS94503", 9 chars no dash)
    #   {output_dir} — fresh per-request scratch dir (must contain EBOOT.PBP
    #                  somewhere underneath when the command finishes)
    # We recommend pop-fe (https://github.com/sahlberg/pop-fe).  It handles
    # CHD extraction, multi-track binmerge, ATRAC3 audio encoding, asset
    # fetching, and multi-disc PBP packaging in one invocation.  Example:
    #   ["python3","/home/pi/pop-fe/pop-fe.py","--psp-dir","{output_dir}",
    #    "--title","{title}","--game_id","{gamecode}","--no-libcrypt",
    #    "{inputs}"]
    # NOTE: ``{inputs}`` is interpreted as ONE token in the JSON array form
    # and expanded into multiple argv entries at runtime — never wrap it in
    # quotes or split it into multiple template entries.
    rom_ps1_eboot_command: str = ""
    # Working directory for the PS1 EBOOT command.  pop-fe (and similar
    # tools) resolve sibling binaries (binmerge, atracdenc, cue2cu2.py) by
    # relative path from cwd, so the subprocess must start inside the
    # pop-fe source tree.  When unset we fall back to the per-request
    # output_dir, which works for self-contained converters but breaks
    # pop-fe.
    rom_ps1_eboot_cwd: str = ""
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
