@echo off
REM Build script for 3dssync - builds all or specific versions
REM Usage: build_all.bat [targets...]
REM   Targets: 3ds, nds, gc, wiiu, android, ps2, ps3, psp, vita, xbox, all (default: all)
REM Example: build_all.bat ps2 ps3 psp
REM
REM A failing target no longer aborts the run: every requested target is
REM attempted, and a summary of failures (if any) is printed at the end.
REM Exit code is non-zero when at least one target failed.

setlocal enabledelayedexpansion

REM Read version from VERSION file
set /p VERSION=<VERSION

REM Parse arguments - default all if no args
set BUILD_ALL=0
set BUILD_3DS=0
set BUILD_NDS=0
set BUILD_GC=0
set BUILD_WIIU=0
set BUILD_ANDROID=0
set BUILD_PS2=0
set BUILD_PS3=0
set BUILD_PSP=0
set BUILD_VITA=0
set BUILD_XBOX=0

REM Accumulators for the end-of-run summary
set OK_LIST=
set FAIL_LIST=

if "%~1"=="" set BUILD_ALL=1

:parse_args
if "%~1"=="" goto :done_parsing
set ARG=%~1
if /i "%ARG%"=="all" set BUILD_ALL=1
if /i "%ARG%"=="3ds" set BUILD_3DS=1
if /i "%ARG%"=="nds" set BUILD_NDS=1
if /i "%ARG%"=="gc" set BUILD_GC=1
if /i "%ARG%"=="gamecube" set BUILD_GC=1
if /i "%ARG%"=="wiiu" set BUILD_WIIU=1
if /i "%ARG%"=="wii-u" set BUILD_WIIU=1
if /i "%ARG%"=="android" set BUILD_ANDROID=1
if /i "%ARG%"=="ps2" set BUILD_PS2=1
if /i "%ARG%"=="ps3" set BUILD_PS3=1
if /i "%ARG%"=="psp" set BUILD_PSP=1
if /i "%ARG%"=="vita" set BUILD_VITA=1
if /i "%ARG%"=="xbox" set BUILD_XBOX=1
shift
goto :parse_args
:done_parsing

if "%BUILD_ALL%"=="1" (
    set BUILD_3DS=1
    set BUILD_NDS=1
    set BUILD_GC=1
    set BUILD_WIIU=1
    set BUILD_ANDROID=1
    set BUILD_PS2=1
    set BUILD_PS3=1
    set BUILD_PSP=1
    set BUILD_VITA=1
    set BUILD_XBOX=1
)

echo ============================================
if "%BUILD_ALL%"=="1" (
    echo Building 3dssync v%VERSION% [all targets]
) else (
    echo Building 3dssync v%VERSION% targets:
    if "!BUILD_3DS!"=="1" echo   - 3DS
    if "!BUILD_NDS!"=="1" echo   - NDS
    if "!BUILD_GC!"=="1" echo   - GameCube
    if "!BUILD_WIIU!"=="1" echo   - Wii U
    if "!BUILD_ANDROID!"=="1" echo   - Android
    if "!BUILD_PS2!"=="1" echo   - PS2
    if "!BUILD_PS3!"=="1" echo   - PS3
    if "!BUILD_PSP!"=="1" echo   - PSP
    if "!BUILD_VITA!"=="1" echo   - Vita
    if "!BUILD_XBOX!"=="1" echo   - Xbox ^(original^)
)
echo ============================================
echo.

REM Create output directory
set OUTPUT_DIR=build_output
if exist "%OUTPUT_DIR%" rmdir /s /q "%OUTPUT_DIR%"
mkdir "%OUTPUT_DIR%"

REM Build 3DS client
if "!BUILD_3DS!"=="1" (
    echo.
    echo [Building 3DS client...]
    cd 3ds
    C:\devkitpro\msys2\usr\bin\bash.exe --login -c "cd /e/projects/3dssync/3ds && make clean && make cia"
    if errorlevel 1 (
        cd ..
        echo [ERROR] 3DS build failed!
        set FAIL_LIST=!FAIL_LIST! 3DS
    ) else (
        cd ..
        copy 3ds\3dssync.3dsx "%OUTPUT_DIR%\3dssync.3dsx"
        copy 3ds\3dssync.cia "%OUTPUT_DIR%\3dssync.cia"
        echo [3DS build complete!]
        set OK_LIST=!OK_LIST! 3DS
    )
)

REM Build NDS client
if "!BUILD_NDS!"=="1" (
    echo.
    echo [Building NDS client...]
    cd ds
    C:\devkitpro\msys2\usr\bin\bash.exe --login -c "cd /e/projects/3dssync/ds && make clean && make"
    if errorlevel 1 (
        cd ..
        echo [ERROR] NDS build failed!
        set FAIL_LIST=!FAIL_LIST! NDS
    ) else (
        cd ..
        copy ds\ndssync.nds "%OUTPUT_DIR%\ndssync.nds"
        echo [NDS build complete!]
        set OK_LIST=!OK_LIST! NDS
    )
)

REM Build GameCube client
if "!BUILD_GC!"=="1" (
    echo.
    echo [Building GameCube client...]
    cd gc
    C:\devkitpro\msys2\usr\bin\bash.exe --login -c "cd /e/projects/3dssync/gc && make clean && make"
    if errorlevel 1 (
        cd ..
        echo [ERROR] GameCube build failed!
        set FAIL_LIST=!FAIL_LIST! GameCube
    ) else (
        cd ..
        copy gc\gcsync.dol "%OUTPUT_DIR%\gcsync.dol"
        echo [GameCube build complete!]
        set OK_LIST=!OK_LIST! GameCube
    )
)

REM Build Wii U client (wut / Aroma)
if "!BUILD_WIIU!"=="1" (
    echo.
    echo [Building Wii U client...]
    C:\devkitpro\msys2\usr\bin\bash.exe --login /e/projects/3dssync/wiiu/build.sh clean
    if errorlevel 1 (
        echo [ERROR] Wii U build failed!
        set FAIL_LIST=!FAIL_LIST! WiiU
    ) else (
        copy wiiu\wiiusync.rpx "%OUTPUT_DIR%\wiiusync.rpx"
        copy wiiu\wiiusync.wuhb "%OUTPUT_DIR%\wiiusync.wuhb"
        echo [Wii U build complete!]
        set OK_LIST=!OK_LIST! WiiU
    )
)

REM Build Android client
if "!BUILD_ANDROID!"=="1" (
    echo.
    echo [Building Android client...]
    cd android
    call .\gradlew.bat --no-daemon :app:assembleDebug
    if errorlevel 1 (
        cd ..
        echo [ERROR] Android build failed!
        set FAIL_LIST=!FAIL_LIST! Android
    ) else (
        cd ..
        if not exist android\app\build\outputs\apk\debug\app-debug.apk (
            echo [ERROR] Android APK was not produced!
            set FAIL_LIST=!FAIL_LIST! Android
        ) else (
            copy android\app\build\outputs\apk\debug\app-debug.apk "%OUTPUT_DIR%\gamesync.apk"
            echo [Android build complete!]
            set OK_LIST=!OK_LIST! Android
        )
    )
)

REM Build PS2 client via WSL
if "!BUILD_PS2!"=="1" (
    echo.
    echo [Building PS2 client via WSL...]
    wsl bash -c "export PS2DEV=/usr/local/ps2dev && export PS2SDK=/usr/local/ps2dev/ps2sdk && export PATH=/usr/local/ps2dev/ee/bin:/usr/local/ps2dev/iop/bin:/usr/local/ps2dev/ps2sdk/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin && cd /mnt/e/projects/3dssync/ps2 && make clean && make 2>&1"
    if errorlevel 1 (
        echo [ERROR] PS2 build failed!
        set FAIL_LIST=!FAIL_LIST! PS2
    ) else (
        copy ps2\ps2sync.elf "%OUTPUT_DIR%\ps2sync.elf"
        echo [PS2 build complete!]
        set OK_LIST=!OK_LIST! PS2
    )
)

REM Build PS3 client via WSL
if "!BUILD_PS3!"=="1" (
    echo.
    echo [Building PS3 client via WSL...]
    wsl bash -c "export PS3DEV=/usr/local/ps3dev && export PSL1GHT=/usr/local/ps3dev && export PATH=/usr/local/ps3dev/bin:/usr/local/ps3dev/ppu/bin:/usr/bin:/bin && cd /mnt/e/projects/3dssync/ps3 && make clean && make 2>&1"
    if errorlevel 1 (
        echo [ERROR] PS3 build failed!
        set FAIL_LIST=!FAIL_LIST! PS3
    ) else (
        copy ps3\ps3sync.pkg "%OUTPUT_DIR%\ps3sync.pkg"
        echo [PS3 build complete!]
        set OK_LIST=!OK_LIST! PS3
    )
)

REM Build PSP client via WSL
if "!BUILD_PSP!"=="1" (
    echo.
    echo [Building PSP client via WSL...]
    wsl bash -c "export PSPDEV=/home/pepa/pspdev && export PSPSDK=/home/pepa/pspdev/psp/sdk && export PATH=/home/pepa/pspdev/bin:/usr/local/bin:/usr/bin:/bin && cd /mnt/e/projects/3dssync/psp && make clean && make"
    if errorlevel 1 (
        echo [ERROR] PSP build failed!
        set FAIL_LIST=!FAIL_LIST! PSP
    ) else (
        copy psp\EBOOT.PBP "%OUTPUT_DIR%\EBOOT.PBP"
        echo [PSP build complete!]
        set OK_LIST=!OK_LIST! PSP
    )
)

REM Build Vita client via WSL
if "!BUILD_VITA!"=="1" (
    echo.
    echo [Building Vita client via WSL...]
    wsl bash -c "export VITASDK=/usr/local/vitasdk && export PATH=/usr/local/vitasdk/bin:/usr/local/bin:/usr/bin:/bin && cd /mnt/e/projects/3dssync/vita && ./build.sh"
    if errorlevel 1 (
        echo [ERROR] Vita build failed!
        set FAIL_LIST=!FAIL_LIST! Vita
    ) else (
        copy vita\build\vitasync.vpk "%OUTPUT_DIR%\vitasync.vpk"
        echo [Vita build complete!]
        set OK_LIST=!OK_LIST! Vita
    )
)

REM Build Xbox (original) client via WSL (nxdk)
REM Note: nxdk's `make clean` also wipes its prebuilt libs (libnxdk_net.lib
REM etc.) and the parallel rebuild can race itself. Let make handle
REM incremental builds; only call `clean` manually when the lib state is
REM actually inconsistent.
if "!BUILD_XBOX!"=="1" (
    echo.
    echo [Building Xbox ^(original^) client via WSL...]
    wsl bash /mnt/e/projects/3dssync/xbox/build.sh
    if errorlevel 1 (
        echo [ERROR] Xbox build failed!
        set FAIL_LIST=!FAIL_LIST! Xbox
    ) else (
        copy xbox\bin\default.xbe "%OUTPUT_DIR%\default.xbe"
        if exist xbox\SaveSync.iso copy xbox\SaveSync.iso "%OUTPUT_DIR%\xboxsync.iso"
        echo [Xbox build complete!]
        set OK_LIST=!OK_LIST! Xbox
    )
)

REM Summary
echo.
echo ============================================
echo Build summary for 3dssync v%VERSION%
if not "!OK_LIST!"=="" echo   Succeeded:!OK_LIST!
if not "!FAIL_LIST!"=="" echo   FAILED:!FAIL_LIST!
echo ============================================
if not "!FAIL_LIST!"=="" (
    echo One or more targets failed.
    echo Output files in: %OUTPUT_DIR%
    dir /b "%OUTPUT_DIR%" 2>nul
    echo ============================================
    endlocal
    exit /b 1
)
echo All builds complete!
echo Output files in: %OUTPUT_DIR%
echo ============================================
dir /b "%OUTPUT_DIR%"
echo ============================================

endlocal
