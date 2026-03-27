#---------------------------------------------------------------------------------
.SUFFIXES:
#---------------------------------------------------------------------------------

ifeq ($(strip $(DEVKITARM)),)
$(error "Please set DEVKITARM in your environment. export DEVKITARM=<path to>devkitARM")
endif

TOPDIR ?= $(CURDIR)
include $(DEVKITARM)/3ds_rules

#---------------------------------------------------------------------------------
TARGET		:=	3dssync
BUILD		:=	build
SOURCES		:=	source
DATA		:=	data
INCLUDES	:=	include

APP_TITLE		:=	3DS Save Sync
APP_DESCRIPTION	:=	Sync save files between consoles
APP_AUTHOR		:=	3dssync

#---------------------------------------------------------------------------------
# Version handling (from root VERSION file)
#---------------------------------------------------------------------------------

APP_VERSION_STR := $(shell cat $(TOPDIR)/../VERSION | tr -d '[:space:]')

APP_VERSION_CIA := $(shell \
	echo $(APP_VERSION_STR) | \
	awk -F. '{ printf "%d", ($$1 * 1024) + ($$2 * 16) + $$3 }')

APP_VERSION_MAJOR := $(shell echo $(APP_VERSION_STR) | awk -F. '{ print $$1 }')
APP_VERSION_MINOR := $(shell echo $(APP_VERSION_STR) | awk -F. '{ print $$2 }')
APP_VERSION_MICRO := $(shell echo $(APP_VERSION_STR) | awk -F. '{ print $$3 }')

ifeq ($(APP_VERSION_STR),)
$(error Failed to read version from VERSION file)
endif

GEN_RSF := $(BUILD)/cia.gen.rsf

#---------------------------------------------------------------------------------
# options for code generation
#---------------------------------------------------------------------------------
ARCH	:=	-march=armv6k -mtune=mpcore -mfloat-abi=hard -mtp=soft

CFLAGS	:=	-g -Wall -O2 -mword-relocations \
			-ffunction-sections \
			$(ARCH)

CFLAGS	+=	$(INCLUDE) -D__3DS__ -DAPP_VERSION=\"$(APP_VERSION_STR)\"

CXXFLAGS	:= $(CFLAGS) -fno-rtti -fno-exceptions -std=gnu++11

ASFLAGS	:=	-g $(ARCH)
LDFLAGS	=	-specs=3dsx.specs -g $(ARCH) -Wl,-Map,$(notdir $*.map)

LIBS	:= -lz -lctru -lm

#---------------------------------------------------------------------------------
LIBDIRS	:= $(DEVKITPRO)/portlibs/3ds $(CTRULIB)

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
SFILES		:=	$(foreach dir,$(SOURCES),$(notdir $(wildcard $(dir)/*.s)))
BINFILES	:=	$(foreach dir,$(DATA),$(notdir $(wildcard $(dir)/*.*)))

#---------------------------------------------------------------------------------
ifeq ($(strip $(CPPFILES)),)
	export LD	:=	$(CC)
else
	export LD	:=	$(CXX)
endif
#---------------------------------------------------------------------------------

export OFILES_SOURCES 	:=	$(CPPFILES:.cpp=.o) $(CFILES:.c=.o) $(SFILES:.s=.o)
export OFILES_BIN	:=	$(addsuffix .o,$(BINFILES))
export OFILES := $(OFILES_BIN) $(OFILES_SOURCES)

export HFILES	:=	$(addsuffix .h,$(subst .,_,$(BINFILES)))

export INCLUDE	:=	$(foreach dir,$(INCLUDES),-I$(CURDIR)/$(dir)) \
			$(foreach dir,$(LIBDIRS),-I$(dir)/include) \
			-I$(CURDIR)/$(BUILD)

export LIBPATHS	:=	$(foreach dir,$(LIBDIRS),-L$(dir)/lib)

export _3DSXDEPS	:=	$(if $(NO_SMDH),,$(OUTPUT).smdh)

ifeq ($(strip $(ICON)),)
	icons := $(wildcard *.png)
	ifneq (,$(findstring $(TARGET).png,$(icons)))
		export APP_ICON := $(TOPDIR)/$(TARGET).png
	else ifneq (,$(findstring icon.png,$(icons)))
		export APP_ICON := $(TOPDIR)/icon.png
	endif
else
	export APP_ICON := $(TOPDIR)/$(ICON)
endif

ifeq ($(strip $(NO_SMDH)),)
	export _3DSXFLAGS += --smdh=$(CURDIR)/$(TARGET).smdh
endif

ifneq ($(ROMFS),)
	export _3DSXFLAGS += --romfs=$(CURDIR)/$(ROMFS)
endif

.PHONY: all clean cia

#---------------------------------------------------------------------------------
all: $(BUILD) $(DEPSDIR)
	@$(MAKE) --no-print-directory -C $(BUILD) -f $(CURDIR)/Makefile

$(BUILD):
	@mkdir -p $@

ifneq ($(DEPSDIR),$(BUILD))
$(DEPSDIR):
	@mkdir -p $@
endif

#---------------------------------------------------------------------------------
# RSF build
#---------------------------------------------------------------------------------
$(GEN_RSF): cia.rsf
	@sed "s/^\([[:space:]]*RemasterVersion[[:space:]]*:[[:space:]]*\).*/\1$(APP_VERSION_CIA)/" $< > $@


#---------------------------------------------------------------------------------
# CIA build
#---------------------------------------------------------------------------------
cia: $(BUILD) $(DEPSDIR) $(GEN_RSF)
	@$(MAKE) --no-print-directory -C $(BUILD) -f $(CURDIR)/Makefile
	@echo "Creating banner..."
	bannertool makebanner -i banner_img.png -a silent.wav -o banner.bnr
	@echo "Building CIA (version $(APP_VERSION_STR))..."
	makerom -f cia -o $(TARGET).cia \
		-elf $(TARGET).elf \
		-rsf $(GEN_RSF) \
		-icon $(TARGET).smdh \
		-banner banner.bnr \
		-major $(APP_VERSION_MAJOR) \
		-minor $(APP_VERSION_MINOR) \
		-micro $(APP_VERSION_MICRO)
	@echo "Patching TMD version (makerom minor version bug workaround)..."
	@cia_patch_ver() { \
		local cia="$$1" ver_hi="$$2" ver_lo="$$3"; \
		local hs=$$(od -A n -t u4 -N 4 -j 0 "$$cia" | tr -d ' '); \
		local cs=$$(od -A n -t u4 -N 4 -j 8 "$$cia" | tr -d ' '); \
		local ts=$$(od -A n -t u4 -N 4 -j 12 "$$cia" | tr -d ' '); \
		local off=$$(( ((hs+63)&~63) + ((cs+63)&~63) + ((ts+63)&~63) + 320 + 156 )); \
		printf "\x$$(printf '%02x' $$ver_hi)\x$$(printf '%02x' $$ver_lo)" | \
			dd of="$$cia" bs=1 seek=$$off count=2 conv=notrunc 2>/dev/null; \
	}; \
	cia_patch_ver "$(TARGET).cia" \
		$$(( ($(APP_VERSION_CIA) >> 8) & 0xFF )) \
		$$(( $(APP_VERSION_CIA) & 0xFF )); \
	echo "  TMD version set to $(APP_VERSION_STR) ($(APP_VERSION_CIA))"
	@echo "Built $(TARGET).cia"

#---------------------------------------------------------------------------------
clean:
	@echo clean ...
	@rm -fr $(BUILD) $(TARGET).3dsx $(OUTPUT).smdh $(TARGET).elf $(TARGET).cia banner.bnr

#---------------------------------------------------------------------------------
else

$(OUTPUT).3dsx	:	$(OUTPUT).elf $(_3DSXDEPS)
$(OFILES_SOURCES) : $(HFILES)
$(OUTPUT).elf	:	$(OFILES)

%.bin.o %_bin.h : %.bin
	@echo $(notdir $<)
	@$(bin2o)

-include $(DEPSDIR)/*.d

endif
#---------------------------------------------------------------------------------
