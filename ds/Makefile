#---------------------------------------------------------------------------------
.SUFFIXES:
#---------------------------------------------------------------------------------

ifeq ($(strip $(DEVKITARM)),)
$(error "Please set DEVKITARM in your environment. export DEVKITARM=<path to devkitARM>")
endif

include $(DEVKITARM)/ds_rules

#---------------------------------------------------------------------------------
OUTPUT := $(CURDIR)/$(TARGET)
TARGET := ndssync
TARGET_DSI := ndssync_dsi
BUILD := build
BUILD_DSI := build_dsi
SOURCES := source
INCLUDES := include

# NDS banner settings (used by ds_rules built-in ndstool rule)
GAME_TITLE := NDS Save Sync
GAME_SUBTITLE1 := Save synchronization
GAME_SUBTITLE2 := for Nintendo DS
GAME_ICON := $(CURDIR)/icon.bmp

#---------------------------------------------------------------------------------
# Version handling (from root VERSION file)
#---------------------------------------------------------------------------------
APP_VERSION_STR := $(shell cat $(CURDIR)/../VERSION | tr -d '[:space:]')

ifeq ($(APP_VERSION_STR),)
$(error Failed to read version from VERSION file)
endif

#---------------------------------------------------------------------------------
# Options for code generation
#---------------------------------------------------------------------------------
ARCH := -march=armv5te -mtune=arm946e-s

CFLAGS := -g -Wall -O2 -ffunction-sections -fdata-sections \
	$(ARCH)

LIBDIRS := $(LIBNDS)

CFLAGS += $(foreach dir,$(INCLUDES),-I$(CURDIR)/$(dir)) \
	$(foreach dir,$(LIBDIRS),-I$(dir)/include) \
	-D__NDS__ -DARM9 -DAPP_VERSION=\"$(APP_VERSION_STR)\" -I$(CALICO)/include

CXXFLAGS := $(CFLAGS) -fno-rtti -fno-exceptions

ASFLAGS := -g $(ARCH)

LDFLAGS = -specs=ds_arm9.specs -g $(ARCH) -Wl,-Map,$(BUILD)/$(TARGET).map -L$(LIBNDS)/lib
LDFLAGS_DSI = -specs=dsi_arm9.specs -g $(ARCH) -Wl,-Map,$(BUILD_DSI)/$(TARGET_DSI).map -L$(LIBNDS)/lib

LIBS := -ldswifi9 -lnds9 -lfat
LIBS_DSI := -ldswifi9 -lnds9 -lfat

#---------------------------------------------------------------------------------
CFILES := $(wildcard $(SOURCES)/*.c)

OFILES_DSI := $(CFILES:$(SOURCES)/%.c=$(BUILD_DSI)/%.o)
OFILES := $(CFILES:$(SOURCES)/%.c=$(BUILD)/%.o)
LIBPATHS := $(foreach dir,$(LIBDIRS),-L$(dir)/lib)

#---------------------------------------------------------------------------------
CC := arm-none-eabi-gcc
CXX := arm-none-eabi-g++
LD := arm-none-eabi-g++

#---------------- dsi

all: $(TARGET).nds

dsi: $(TARGET_DSI).nds

both: all dsi

$(TARGET).nds: $(TARGET).elf

$(TARGET).elf: $(BUILD) $(OFILES)

$(TARGET_DSI).nds: $(TARGET_DSI).elf

$(TARGET_DSI).elf: $(BUILD_DSI) $(OFILES_DSI)
	@echo "linking DSi-enhanced $(notdir $@)"
	@$(LD) $(LDFLAGS_DSI) $(OFILES_DSI) $(LIBPATHS) $(LIBS_DSI) -o $@

$(BUILD):
	@mkdir -p $@

$(BUILD_DSI):
	@mkdir -p $@

$(BUILD)/%.o: $(SOURCES)/%.c
	@echo "$(notdir $<)"
	@$(CC) $(CFLAGS) -c $< -o $@

$(BUILD_DSI)/%.o: $(SOURCES)/%.c
	@echo "DSi: $(notdir $<)"
	@$(CC) $(CFLAGS) -D__DSI__ -c $< -o $@

clean:
	@echo clean ...
	@rm -fr $(BUILD) $(BUILD_DSI) $(TARGET).elf $(TARGET).nds $(TARGET_DSI).elf $(TARGET_DSI).nds

-include $(OFILES:.o=.d)
-include $(OFILES_DSI:.o=.d)
