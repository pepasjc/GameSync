#
# ps2sync — PS2 Save Sync client.
#
# Build (WSL):
#   wsl bash -lc "export PS2DEV=/usr/local/ps2dev && \
#     export PS2SDK=\$PS2DEV/ps2sdk && \
#     export PATH=\$PS2DEV/bin:\$PS2SDK/bin:\$PATH && \
#     cd /mnt/e/projects/3dssync/ps2 && make"
#
# Output: ps2sync.elf — load via uLaunchELF / FMCB / OPL ELF launcher.
#

ifeq ($(strip $(PS2SDK)),)
$(error "PS2SDK not set. Run install-ps2sdk-wsl.sh and source ~/.bashrc.")
endif

EE_BIN     = ps2sync.elf
EE_OBJS    = source/main.o \
             source/config.o \
             source/network.o \
             source/http.o \
             source/roms.o \
             source/downloads.o \
             source/hdl.o \
             source/sha256.o \
             source/saves.o \
             source/ui.o \
             source/irx_mods.o

# Default target — must come before any other rule so plain ``make``
# builds the ELF instead of stopping at the irx_mods generator below.
.PHONY: all clean run
all: $(EE_BIN)

# Version pulled from root VERSION file (single source of truth).
APP_VERSION_STR := $(shell cat $(CURDIR)/../VERSION | tr -d '[:space:]')
ifeq ($(APP_VERSION_STR),)
$(error Failed to read version from VERSION file)
endif

EE_INCS    = -I$(CURDIR)/include -I$(PS2SDK)/ee/include -I$(PS2SDK)/common/include -I$(PS2SDK)/ports/include
EE_CFLAGS  = -O2 -Wall -Wno-unused-function -DAPP_VERSION=\"$(APP_VERSION_STR)\" $(EE_INCS)

# IRX modules linked into the ELF as embedded blobs (bin2c-style).  This
# avoids hunting for IRX files at runtime and lets a single .elf boot
# from any device (USB, HDD, MC, host:).
#
# IRX list:
#   iomanX       — extended IOP I/O manager (block devices register here)
#   fileXio      — fileXio RPC, the EE→IOP bridge newlib stdio routes through
#   usbd         — USB driver core
#   bdm          — block-device manager (mount dispatcher)
#   ata_bd       — internal ATA HDD → bdm bridge (PS2 fat network adapter)
#   usbmass_bd   — USB mass storage → bdm bridge
#   bdmfs_fatfs  — FAT filesystem on bdm; registers ``mass:`` once a USB
#                  or ATA device with a FAT/exFAT partition is attached
#   usbhdfsd     — legacy single-module USB mass storage; loaded as
#                  fallback when bdm stack fails (older / mismatched ABI)
#   poweroff     — IOP power-off callbacks required by ps2fs
#   ps2atad      — classic ATA driver used by ps2hdd APA/PFS
#   ps2hdd       — APA partition driver used by the HDD formatter
#   ps2fs        — PFS filesystem driver used by the HDD formatter
#   ps2dev9      — DEV9 (network adapter / HDD bridge)
#   netman       — network manager
#   smap         — SMAP ethernet driver (built into PS2 slim, ext on fat)
#
# sio2man / mcman / mcserv / padman are loaded from rom0: at runtime — the
# PS2SDK IRX equivalents have a different RPC ABI than what libmc expects,
# so embedding them causes mcInit() to hang.
EE_LIBS    = -lpatches \
             -lps2ip \
             -lps2ips \
             -lnetman \
             -lpoweroff \
             -lcdvd \
             -lmc \
             -lpadx \
             -lhdd \
             -lfileXio \
             -liopreboot \
             -ldebug \
             -lm \
             -lc

# IRX blobs we embed.  Source IRX files live under $(PS2SDK)/iop/irx/.
IRX_SRC_DIR = $(PS2SDK)/iop/irx
IRX_FILES   = iomanX.irx \
              fileXio.irx \
              usbd.irx \
              bdm.irx \
              ps2dev9.irx \
              ata_bd.irx \
              usbmass_bd.irx \
              bdmfs_fatfs.irx \
              usbhdfsd.irx \
              poweroff.irx \
              ps2atad.irx \
              ps2hdd.irx \
              ps2fs.irx \
              netman.irx \
              smap.irx

# IRX outside the SDK tree (full path). mmceman.irx talks the MMCE protocol to
# MemCard Pro 2 / SD2PSX for GameID channel switching.
IRX_EXTRA   = irx/mmceman.irx

# Generate include/irx_mods.h + source/irx_mods.c with bin2c blobs.
# bin2c output is one .o per IRX, concatenated via the wrapper file.
source/irx_mods.c include/irx_mods.h: $(addprefix $(IRX_SRC_DIR)/,$(IRX_FILES)) $(IRX_EXTRA) tools/gen_irx_mods.sh Makefile
	@mkdir -p source include
	bash tools/gen_irx_mods.sh "$(IRX_SRC_DIR)" "$(IRX_FILES) $(IRX_EXTRA)" source/irx_mods.c include/irx_mods.h

source/irx_mods.o: source/irx_mods.c include/irx_mods.h

# Every EE object indirectly depends on the generated header so a
# clean build doesn't try to compile main.c before gen_irx_mods.sh
# has produced include/irx_mods.h.
$(EE_OBJS): include/irx_mods.h

clean:
	rm -f $(EE_BIN) $(EE_OBJS) source/irx_mods.c include/irx_mods.h

run: $(EE_BIN)
	@echo "Copy $(EE_BIN) to mass:/3dssync/ps2sync.elf or boot via host: with ps2client."

include $(PS2SDK)/samples/Makefile.pref
include $(PS2SDK)/samples/Makefile.eeglobal
