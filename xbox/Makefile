# ---------------------------------------------------------------------------
# Save Sync — original Xbox client (nxdk)
#
# Build (from WSL or another POSIX shell with nxdk on PATH):
#     . ~/nxdk/bin/activate                 # sets NXDK_DIR + toolchain env
#     cd /mnt/e/projects/3dssync/xbox
#     make                                  # produces bin/default.xbe
#     make iso                              # produces bin/$(XBE_TITLE).iso
#     make clean
#
# nxdk install: see https://github.com/XboxDev/nxdk
# ---------------------------------------------------------------------------

XBE_TITLE   := SaveSync
GEN_XISO    := $(XBE_TITLE).iso
OUTPUT_DIR  := $(CURDIR)/bin

# nxdk root: trust the env var set by `. nxdk/bin/activate`, fall back to a
# couple of conventional locations so a fresh shell can still drive `make`.
NXDK_DIR    ?= $(HOME)/nxdk
ifeq ($(wildcard $(NXDK_DIR)/Makefile),)
NXDK_DIR    := $(CURDIR)/../../nxdk
endif
ifeq ($(wildcard $(NXDK_DIR)/Makefile),)
$(error nxdk not found. Source ~/nxdk/bin/activate or set NXDK_DIR)
endif

# ---------------------------------------------------------------------------
# Version (single source of truth: root VERSION file)
# ---------------------------------------------------------------------------
APP_VERSION_STR := $(shell tr -d '[:space:]' < $(CURDIR)/../VERSION)
ifeq ($(APP_VERSION_STR),)
$(error Failed to read version from VERSION file)
endif

# ---------------------------------------------------------------------------
# Sources
# ---------------------------------------------------------------------------
SRCS := $(wildcard $(CURDIR)/source/*.c)

# ---------------------------------------------------------------------------
# Compiler flags
# ---------------------------------------------------------------------------
CFLAGS  += -DAPP_VERSION=\"$(APP_VERSION_STR)\" -I$(CURDIR)/source
NXDK_CFLAGS += -DAPP_VERSION=\"$(APP_VERSION_STR)\" -I$(CURDIR)/source

# ---------------------------------------------------------------------------
# Phase 5+ toggles (uncomment as features land)
# ---------------------------------------------------------------------------
NXDK_NET = y    # lwIP/BSD sockets (HTTP client)
NXDK_SDL = y    # SDL2 + SDL_ttf (auto-included): video, controller, TTF text

# nxdk's lib/zlib/Makefile defines `$(NXDK_DIR)/lib/libzlib.lib: ...` as the
# first rule, which would otherwise become make's default goal. Pin "all"
# (defined by nxdk's main Makefile) so the XBE actually builds when run with
# no args.
.DEFAULT_GOAL := all_local

# Copy the TTF font into the XISO output dir so TTF_OpenFont("D:\\font.ttf")
# can find it at runtime. ``all_local`` is our default; it depends on
# cp_assets first then chains to nxdk's real ``all`` (defined further down).
all_local: cp_assets all
.PHONY: all_local cp_assets

cp_assets:
	@mkdir -p $(OUTPUT_DIR)
	@cp -u $(CURDIR)/assets/font.ttf $(OUTPUT_DIR)/font.ttf

# zlib for bundle compression (v5 bundles are zlib-deflated). Pulling in the
# zlib snippet adds -I/-DZ_SOLO and links libzlib.lib into main.exe.
include $(NXDK_DIR)/lib/zlib/Makefile

include $(NXDK_DIR)/Makefile
