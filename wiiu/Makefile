#---------------------------------------------------------------------------------
# wiiusync — Wii U Save Sync client (wut / Aroma).
#
# Build (devkitPro MSYS2 login shell):
#   /c/devkitpro/msys2/usr/bin/bash.exe --login -c \
#     'cd /e/projects/3dssync/wiiu && make'
#
# Outputs:
#   wiiusync.rpx   — put in sd:/wiiu/apps/wiiusync/ (Aroma / Homebrew Launcher)
#   wiiusync.wuhb  — single-file bundle for the Wii U Menu (Aroma)
#
# Requires: pacman -S wiiu-dev ppc-zlib   plus libmocha built from source
# (see wiiu/README.md).
#---------------------------------------------------------------------------------
.SUFFIXES:
#---------------------------------------------------------------------------------

ifeq ($(strip $(DEVKITPRO)),)
$(error "Please set DEVKITPRO in your environment. export DEVKITPRO=<path to>devkitPro")
endif

TOPDIR ?= $(CURDIR)

include $(DEVKITPRO)/wut/share/wut_rules

#---------------------------------------------------------------------------------
# TARGET is the name of the output
# BUILD is the directory where object files & intermediate files will be placed
# SOURCES is a list of directories containing source code
# INCLUDES is a list of directories containing extra header files
#---------------------------------------------------------------------------------
TARGET		:=	wiiusync
BUILD		:=	build
SOURCES		:=	source
DATA		:=	data
INCLUDES	:=	include

#---------------------------------------------------------------------------------
# .wuhb metadata (consumed by wuhbtool via wut_rules)
#---------------------------------------------------------------------------------
APP_NAME	:=	Save Sync
APP_SHORTNAME	:=	SaveSync
APP_AUTHOR	:=	Save Sync

#---------------------------------------------------------------------------------
# Version — single source of truth in the repo-root VERSION file, same as the
# 3DS / NDS / PS2 / GameCube clients.  Passed to the compiler as APP_VERSION.
#---------------------------------------------------------------------------------
ROOT_DIR	:=	$(abspath $(dir $(firstword $(MAKEFILE_LIST)))/..)
APP_VERSION_STR	:=	$(shell cat $(ROOT_DIR)/VERSION | tr -d '[:space:]')
ifeq ($(APP_VERSION_STR),)
$(error Failed to read version from VERSION file)
endif

#---------------------------------------------------------------------------------
# options for code generation
#---------------------------------------------------------------------------------
# The truncation warnings all fire on deliberate strncpy/snprintf clamping
# into fixed-size record fields, so they are silenced rather than papered over
# with larger buffers.
CFLAGS	:=	-g -Wall -Wno-unused-function -O2 -ffunction-sections \
		-Wno-stringop-truncation -Wno-format-truncation \
		$(MACHDEP)

CFLAGS	+=	$(INCLUDE) -D__WIIU__ -D__WUT__ \
		-DAPP_VERSION=\"$(APP_VERSION_STR)\"

CXXFLAGS	:= $(CFLAGS) -std=gnu++20

ASFLAGS	:=	-g $(ARCH)
LDFLAGS	=	-g $(ARCH) $(RPXSPECS) -Wl,-Map,$(notdir $*.map)

#---------------------------------------------------------------------------------
# any extra libraries we wish to link with the project
#   -lmocha  libmocha (FSA access to SLC/MLC — built from source, see README)
#   -lz      ppc-zlib (3DSS v5 bundle compression)
#   -lwut    wut runtime
#---------------------------------------------------------------------------------
LIBS	:=	-lmocha -lz -lwut

#---------------------------------------------------------------------------------
# list of directories containing libraries, this must be the top level containing
# include and lib.  libmocha's `make install` drops into $(WUT_ROOT)/usr.
#---------------------------------------------------------------------------------
LIBDIRS	:=	$(PORTLIBS) $(WUT_ROOT) $(WUT_ROOT)/usr

#---------------------------------------------------------------------------------
# no real need to edit anything past this point unless you need to add additional
# rules for different file extensions
#---------------------------------------------------------------------------------
ifneq ($(BUILD),$(notdir $(CURDIR)))
#---------------------------------------------------------------------------------

export OUTPUT	:=	$(CURDIR)/$(TARGET)
export TOPDIR	:=	$(CURDIR)

export VPATH	:=	$(foreach dir,$(SOURCES),$(CURDIR)/$(dir)) \
			$(foreach dir,$(DATA),$(CURDIR)/$(dir))

export DEPSDIR	:=	$(CURDIR)/$(BUILD)

CFILES		:=	$(foreach dir,$(SOURCES),$(notdir $(wildcard $(dir)/*.c)))
CPPFILES	:=	$(foreach dir,$(SOURCES),$(notdir $(wildcard $(dir)/*.cpp)))
SFILES		:=	$(foreach dir,$(SOURCES),$(notdir $(wildcard $(dir)/*.S)))
BINFILES	:=	$(foreach dir,$(DATA),$(notdir $(wildcard $(dir)/*.*)))

#---------------------------------------------------------------------------------
# Always link with the C++ driver: the sources are plain C, but libmocha is
# built from C++ and pulls in operator new/delete and std::__throw_system_error.
#---------------------------------------------------------------------------------
export LD	:=	$(CXX)

export OFILES_BIN	:=	$(addsuffix .o,$(BINFILES))
export OFILES_SRC	:=	$(CPPFILES:.cpp=.o) $(CFILES:.c=.o) $(SFILES:.S=.o)
export OFILES		:=	$(OFILES_BIN) $(OFILES_SRC)
export HFILES_BIN	:=	$(addsuffix .h,$(subst .,_,$(BINFILES)))

export INCLUDE	:=	$(foreach dir,$(INCLUDES),-I$(CURDIR)/$(dir)) \
			$(foreach dir,$(LIBDIRS),-I$(dir)/include) \
			-I$(CURDIR)/$(BUILD)

export LIBPATHS	:=	$(foreach dir,$(LIBDIRS),-L$(dir)/lib)

.PHONY: $(BUILD) clean all

#---------------------------------------------------------------------------------
all: $(BUILD)

$(BUILD):
	@[ -d $@ ] || mkdir -p $@
	@$(MAKE) --no-print-directory -C $(BUILD) -f $(CURDIR)/Makefile

#---------------------------------------------------------------------------------
clean:
	@echo clean ...
	@rm -fr $(BUILD) $(TARGET).rpx $(TARGET).wuhb $(TARGET).elf

#---------------------------------------------------------------------------------
else
.PHONY:	all

DEPENDS	:=	$(OFILES:.o=.d)

#---------------------------------------------------------------------------------
# main targets
#---------------------------------------------------------------------------------
all		:	$(OUTPUT).wuhb

$(OUTPUT).wuhb	:	$(OUTPUT).rpx
$(OUTPUT).rpx	:	$(OUTPUT).elf
$(OUTPUT).elf	:	$(OFILES)

$(OFILES_SRC)	:	$(HFILES_BIN)

#---------------------------------------------------------------------------------
%.bin.o	%_bin.h :	%.bin
#---------------------------------------------------------------------------------
	@echo $(notdir $<)
	@$(bin2o)

-include $(DEPENDS)

#---------------------------------------------------------------------------------
endif
#---------------------------------------------------------------------------------
