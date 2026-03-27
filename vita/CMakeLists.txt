cmake_minimum_required(VERSION 2.8)

if(NOT DEFINED CMAKE_TOOLCHAIN_FILE)
  if(DEFINED ENV{VITASDK})
    set(CMAKE_TOOLCHAIN_FILE "$ENV{VITASDK}/share/vita.toolchain.cmake" CACHE PATH "toolchain file")
  else()
    message(FATAL_ERROR "VITASDK env var not set. Install VitaSDK: https://vitasdk.org/")
  endif()
endif()

project(vitasync C)
include("${VITASDK}/share/vita.cmake" REQUIRED)

# Version from root VERSION file
file(READ "${CMAKE_CURRENT_SOURCE_DIR}/../VERSION" APP_VERSION_STR)
string(STRIP "${APP_VERSION_STR}" APP_VERSION_STR)

set(VITA_APP_NAME "Vita Save Sync")
set(VITA_TITLEID  "VTSS00001")   # Custom title ID for the homebrew app itself
set(VITA_VERSION  "00.50")

add_definitions(-DAPP_VERSION="${APP_VERSION_STR}")

set(SOURCES
    source/main.c
    source/config.c
    source/saves.c
    source/network.c
    source/sync.c
    source/bundle.c
    source/sha256.c
    source/ui.c
    source/debugScreen.c
    # Bundled zlib (system libz.a has -fPIC GOT relocations vita-elf-create can't handle)
    source/zlib/adler32.c
    source/zlib/compress.c
    source/zlib/crc32.c
    source/zlib/deflate.c
    source/zlib/infback.c
    source/zlib/inffast.c
    source/zlib/inflate.c
    source/zlib/inftrees.c
    source/zlib/trees.c
    source/zlib/uncompr.c
    source/zlib/zutil.c
)

include_directories(include source/zlib source)

add_executable(${PROJECT_NAME} ${SOURCES})

target_link_libraries(${PROJECT_NAME}
    SceDisplay_stub
    SceCtrl_stub
    SceAudio_stub
    SceSysmodule_stub
    SceNet_stub
    SceNetCtl_stub
    SceHttp_stub
    SceSsl_stub
    SceIofilemgr_stub
    SceKernelThreadMgr_stub
    SceKernelModulemgr_stub
    SceLibKernel_stub
    SceRtc_stub
    m
    c
)

vita_create_self(${PROJECT_NAME}.self ${PROJECT_NAME} UNSAFE 1)
vita_create_vpk(${PROJECT_NAME}.vpk ${VITA_TITLEID} ${PROJECT_NAME}.self
    VERSION ${VITA_VERSION}
    NAME ${VITA_APP_NAME}
    FILE sce_sys/icon0.png sce_sys/icon0.png
    FILE sce_sys/livearea/contents/bg.png sce_sys/livearea/contents/bg.png
    FILE sce_sys/livearea/contents/startup.png sce_sys/livearea/contents/startup.png
    FILE sce_sys/livearea/contents/template.xml sce_sys/livearea/contents/template.xml
)

# Build instructions (printed once):
# mkdir build && cd build
# cmake .. && make
# The vitasync.vpk can be installed via VitaShell
