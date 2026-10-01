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
    # ``rom_xbox_cci_command`` must write one .cci, and
    # ``rom_xbox_folder_command`` must extract files into {output_dir}. The
    # server wraps CCI/folder downloads in a .zip because CCI libraries may
    # also carry launcher files and extracted games contain many files.
    # XGDTool is the intended converter:
    #   XGDTool --xiso --offline --quiet {input} {output_dir}
    #   XGDTool --cci --offline --quiet {input} {output_dir}
    #   XGDTool --extract --offline --quiet {input} {output_dir}
    # Both stay empty until the operator configures the toolchain — until
    # then the server returns 503 with a hint pointing at SYNC_ROM_XBOX_*.
    rom_xbox_iso_command: str = ""
    rom_xbox_cci_command: str = ""
    rom_xbox_folder_command: str = ""
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
    # Optional command template for converting a PS1 disc image into a
    # POPStarter .VCD so OPL (Open PS2 Loader) on a PS2 can play PS1 games
    # via the built-in POPS emulator.  POPStarter expects one .VCD per
    # disc (no multi-disc merge), so the template is invoked once per disc
    # with the single-disc placeholder set:
    #   {input}      — path to this disc's image (.chd / .cue / .bin / .iso)
    #   {output}     — path the converter should write the .VCD to
    #   {output_dir} — fresh per-request scratch dir (must contain the .VCD
    #                  somewhere underneath when the command finishes)
    #   {stem}       — disc filename without extension
    #   {title}      — human-readable game name (catalog ``name``)
    #   {gamecode}   — PS1 product code (e.g. "SCUS94503", 9 chars no dash)
    # Example using krHACKen's popstation-based VCD tool:
    #   ["popstation","-p","-c","{output_dir}","{input}"]
    # Stay empty until the operator configures the toolchain — until then
    # the server returns 503 with a hint pointing at SYNC_ROM_PS1_VCD_COMMAND.
    rom_ps1_vcd_command: str = ""
    rom_ps1_vcd_cwd: str = ""
    # OPTIONAL command templates for decrypting a Wii U WUP/NUS title.
    #
    # You probably do not need these.  A WUP dump's ``.app`` contents are
    # encrypted, but the bundled ``title.tik`` carries the title key, so both
    # a real console and Cemu 2.x decrypt it themselves — the raw download
    # already works on both.  These exist only for people who want a
    # decrypted ``code``/``content``/``meta`` tree or a ``.wua`` on disk.
    #
    # Leaving them empty is a supported configuration: the catalog then
    # advertises no Wii U extract formats and clients take the raw bundle.
    # Placeholders:
    #   {input}      — the WUP bundle *directory* (not a file)
    #   {output_dir} — fresh per-request scratch dir; the converter must
    #                  write its result somewhere underneath
    #   {output}     — suggested output path (``.wua`` command only)
    #   {stem}       — bundle folder name
    #   {title}      — game name (catalog ``name``)
    #   {title_id}   — 16-hex Wii U title id (catalog ``title_id``)
    # ``rom_wiiu_loadiine_command`` must leave ``code``/``content``/``meta``
    # under {output_dir}; the server zips that tree.  ``rom_wiiu_wua_command``
    # must produce exactly one ``.wua``.
    #
    # CDecrypt is the intended decrypter.  Check your build's own usage line
    # for argument order; the common shape is input-dir then output-dir:
    #   ["cdecrypt","{input}","{output_dir}"]
    # Older forks read the Wii U common key from a ``keys.txt`` in the working
    # directory — point ``rom_wiiu_cwd`` at the folder holding it.  GameSync
    # does not ship that key and cannot redistribute it.
    #
    # ``rom_wiiu_wua_command`` has no widely-available CLI tool behind it:
    # Cemu converts to .wua from its GUI, not a documented headless flag.
    # Leave it empty unless you have your own packer — no shipped client
    # requests 'wua'.
    rom_wiiu_loadiine_command: str = ""
    rom_wiiu_wua_command: str = ""
    # Working directory for the Wii U commands.  CDecrypt resolves its key
    # file relative to cwd, so this usually points at the folder holding
    # ``keys.txt``.  When unset we fall back to the per-request scratch dir.
    rom_wiiu_cwd: str = ""
    api_key: str = "anything"
    host: str = "0.0.0.0"
    port: int = 8000
    max_history_versions: int = 10
    rom_scan_interval: int = 300

    # RetroAchievements catalog badges.  The index is built in the
    # background after a ROM scan and cached in roms.db, so leaving this on
    # costs one read of each cartridge ROM, once.  Without an API key the
    # public endpoints are used, which report no achievement counts.
    ra_enabled: bool = True
    ra_api_key: str = ""
    ra_username: str = ""
    # Connect token for the DS real-hardware achievements (routes/ra.py):
    # set sets and awards need it, the catalog badges above don't.  Get it
    # with ``uv run python ra_login.py``.  Unlocks are only logged until
    # ra_submit is turned on.
    ra_token: str = ""
    ra_submit: bool = False
    # Rewrite zstd-compressed CHDs in the ROM library to chdman's standard
    # codecs after each scan (lossless, verified; see
    # services/chd_normalize.py).  The server's own CHD reader cannot decode
    # zstd, so such discs would otherwise never get an exact RA badge.
    chd_recompress_zstd: bool = True
    site_title: str = "GameSync"
    # Comma-separated list of nginx Basic Auth usernames that get admin access.
    # Everyone else can download but cannot trigger rescans or change settings.
    # Example: SYNC_ADMIN_USERS=admin,pepas
    admin_users: str = "admin"

    # ── Security toggles (secure-by-default) ─────────────────────────────
    # The X-Remote-User header is only trustworthy when set by a reverse
    # proxy we control (nginx Basic Auth).  A client hitting the server
    # directly can forge it, so we IGNORE the header unless the operator
    # explicitly confirms a trusted proxy sits in front.  Default False so
    # a directly-exposed server can never be tricked into granting admin.
    trust_proxy_auth: bool = False
    # Homelab convenience: treat an unauthenticated LAN request as admin and
    # hand the web UI the API key inline.  This is exactly what leaks the key
    # to anyone who can reach the port, so it is OFF by default and must be
    # opted into — and ONLY on a network that is never exposed to the
    # internet.  When False, the web UI loads without a key and prompts the
    # user to paste it (stored in their browser's localStorage).
    lan_admin: bool = False
    # Escape hatch: allow booting with the placeholder/empty api_key.  Off by
    # default so a fresh deploy fails loudly instead of running wide open.
    allow_weak_key: bool = False

    @property
    def admin_users_set(self) -> frozenset[str]:
        return frozenset(u.strip() for u in self.admin_users.split(",") if u.strip())

    def validate_security(self) -> None:
        """Fail fast on an insecure key unless the operator opted out."""
        weak = {"", "anything", "changeme", "password", "secret"}
        if self.api_key.strip().lower() in weak and not self.allow_weak_key:
            raise RuntimeError(
                "Refusing to start: SYNC_API_KEY is unset or weak "
                f"({self.api_key!r}). Set a strong SYNC_API_KEY in the "
                "environment / .env, or set SYNC_ALLOW_WEAK_KEY=true to "
                "override (NOT recommended)."
            )

    model_config = {
        "env_prefix": "SYNC_",
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        # Ignore unknown SYNC_* vars so old .env files (e.g. with the removed
        # SYNC_ROM_3DS_DECRYPTED_CIA_COMMAND) don't crash the server on boot.
        "extra": "ignore",
    }


settings = Settings()
