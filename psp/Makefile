TARGET = pspsync
OBJS = source/main.o source/config.o source/saves.o \
       source/network.o source/sync.o source/bundle.o \
       source/sha256.o source/ui.o

INCDIR   := include
CFLAGS   := -O2 -G0 -Wall -I$(INCDIR)
CXXFLAGS := $(CFLAGS) -fno-exceptions -fno-rtti
ASFLAGS  := $(CFLAGS)

# PSP module info
BUILD_PRX        := 1
PSP_FW_VERSION   := 600
PSP_LARGE_MEMORY := 1

EXTRA_TARGETS := EBOOT.PBP
PSP_EBOOT_TITLE := PSP Save Sync

# Version from root VERSION file
APP_VERSION_STR := $(shell cat $(CURDIR)/../VERSION | tr -d '[:space:]')
ifeq ($(APP_VERSION_STR),)
$(error Failed to read version from VERSION file)
endif
CFLAGS += -DAPP_VERSION=\"$(APP_VERSION_STR)\"

# Link against network and zlib libraries
LIBS := -lpspnet_apctl -lpspnet_inet -lpspnet -lpspsdk -lz -lm -lc -lpspuser

ifneq ($(PSPSDK),)
include $(PSPSDK)/lib/build.mak
else
$(error "Please set PSPSDK in your environment. Install pspdev toolchain.")
endif
