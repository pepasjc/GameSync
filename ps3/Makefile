ifeq ($(strip $(PSL1GHT)),)
$(error "Please set PSL1GHT in your environment. export PSL1GHT=<path>")
endif

TARGET      := ps3sync
BUILD       := build
SOURCES     := source
DATA        :=
INCLUDES    := include
VPATH       := $(foreach dir,$(SOURCES),$(CURDIR)/$(dir))

TITLE       := Save Sync PS3
APPID       := 3DSSYNC00
CONTENTID   := UP0001-$(APPID)_00-0000000000000000

APP_VERSION_STR := $(shell cat $(CURDIR)/../VERSION | tr -d '[:space:]')
ifeq ($(APP_VERSION_STR),)
$(error Failed to read version from VERSION file)
endif

CFLAGS      := -O2 -Wall -DAPP_VERSION=\"$(APP_VERSION_STR)\"
CXXFLAGS    := $(CFLAGS)
ASFLAGS     := $(CFLAGS)
LDFLAGS     += -L$(PSL1GHT)/portlibs/ppu/lib
LD          := ppu-gcc
LIBS        := -lSDL_gfx -lSDL -lrsx -lgcm_sys -laudio -lsysutil -lio -lnet -lnetctl -lsysmodule -lrt -llv2 -lpolarssl -lz -lm

CPPFILES    :=
CFILES      := $(foreach dir,$(SOURCES),$(notdir $(wildcard $(dir)/*.c)))
SFILES      := $(foreach dir,$(SOURCES),$(notdir $(wildcard $(dir)/*.s)))
BINFILES    := $(foreach dir,$(DATA),$(notdir $(wildcard $(dir)/*.*)))
OFILES      := $(addprefix $(BUILD)/,$(BINFILES:.bin=.bin.o) $(SFILES:.s=.o) $(CFILES:.c=.o) $(CPPFILES:.cpp=.o))

include $(PSL1GHT)/ppu_rules

.PHONY: all clean

all: $(TARGET).pkg

# Copy games.conf into the package so it's installed alongside EBOOT.BIN
$(TARGET).pkg: $(BUILD)/pkg/USRDIR/games.conf

$(BUILD)/pkg/USRDIR/games.conf: data/games.conf | $(BUILD)
	@mkdir -p $(BUILD)/pkg/USRDIR
	cp data/games.conf $(BUILD)/pkg/USRDIR/games.conf

$(BUILD):
	mkdir -p $(BUILD)

$(BUILD)/%.o: %.c | $(BUILD)
	$(CC) $(CFLAGS) $(LIBPSL1GHT_INC) -I$(PSL1GHT)/portlibs/ppu/include $(foreach dir,$(INCLUDES),-I$(CURDIR)/$(dir)) -c $< -o $@

$(BUILD)/%.o: %.cpp | $(BUILD)
	$(CXX) $(CXXFLAGS) $(LIBPSL1GHT_INC) -I$(PSL1GHT)/portlibs/ppu/include $(foreach dir,$(INCLUDES),-I$(CURDIR)/$(dir)) -c $< -o $@

$(BUILD)/%.o: %.s | $(BUILD)
	$(CC) -x assembler-with-cpp $(ASFLAGS) $(LIBPSL1GHT_INC) $(foreach dir,$(INCLUDES),-I$(CURDIR)/$(dir)) -c $< -o $@

clean:
	rm -rf $(BUILD) $(TARGET).elf $(TARGET).self $(TARGET).pkg $(TARGET).fake.self $(TARGET).gnpdrm.pkg
