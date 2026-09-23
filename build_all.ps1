# Build script for 3dssync - builds all or specific versions
# Usage: .\build_all.ps1 [targets...]
#   Targets: 3ds, nds, wiiu, ps3, psp, vita, xbox, all (default: 3ds, nds)
# Example: .\build_all.ps1 xbox
#          .\build_all.ps1 all

[CmdletBinding()]
param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$Targets
)

$ErrorActionPreference = "Stop"
$VERSION = (Get-Content VERSION).Trim()

# Determine which targets to build. With no args, default to 3DS + NDS to
# preserve previous behaviour; "all" expands to every supported client.
$wantAll  = $false
$want3ds  = $false
$wantNds  = $false
$wantWiiu = $false
$wantPs3  = $false
$wantPsp  = $false
$wantVita = $false
$wantXbox = $false

if (-not $Targets -or $Targets.Count -eq 0) {
    $want3ds = $true
    $wantNds = $true
} else {
    foreach ($t in $Targets) {
        switch ($t.ToLower()) {
            "all"  { $wantAll = $true }
            "3ds"  { $want3ds = $true }
            "nds"  { $wantNds = $true }
            "wiiu" { $wantWiiu = $true }
            "ps3"  { $wantPs3 = $true }
            "psp"  { $wantPsp = $true }
            "vita" { $wantVita = $true }
            "xbox" { $wantXbox = $true }
            default {
                Write-Host "Unknown target: $t" -ForegroundColor Red
                exit 1
            }
        }
    }
}

if ($wantAll) {
    $want3ds = $true
    $wantNds = $true
    $wantWiiu = $true
    $wantPs3 = $true
    $wantPsp = $true
    $wantVita = $true
    $wantXbox = $true
}

Write-Host "Building 3dssync v$VERSION" -ForegroundColor Cyan
Write-Host ""

# Create output directory
$OUTPUT_DIR = "build_output"
if (Test-Path $OUTPUT_DIR) {
    Remove-Item -Recurse -Force $OUTPUT_DIR
}
New-Item -ItemType Directory -Path $OUTPUT_DIR | Out-Null

# Build 3DS client
if ($want3ds) {
    Write-Host "Building 3DS client..." -ForegroundColor Yellow
    Push-Location 3ds
    & C:\devkitpro\msys2\usr\bin\bash.exe --login -c 'cd /e/projects/3dssync/3ds && make clean && make'
    if ($LASTEXITCODE -ne 0) {
        Pop-Location
        Write-Host "3DS build failed!" -ForegroundColor Red
        exit 1
    }
    Pop-Location

    Copy-Item "3ds\3dssync.3dsx" "$OUTPUT_DIR\3dssync-$VERSION.3dsx"
    Copy-Item "3ds\3dssync.cia" "$OUTPUT_DIR\3dssync-$VERSION.cia"
    Write-Host "3DS build complete!" -ForegroundColor Green
    Write-Host ""
}

# Build NDS client
if ($wantNds) {
    Write-Host "Building NDS client..." -ForegroundColor Yellow
    Push-Location ds
    & C:\devkitpro\msys2\usr\bin\bash.exe --login -c 'cd /e/projects/3dssync/ds && make clean && make'
    if ($LASTEXITCODE -ne 0) {
        Pop-Location
        Write-Host "NDS build failed!" -ForegroundColor Red
        exit 1
    }
    Pop-Location

    Copy-Item "ds\ndssync.nds" "$OUTPUT_DIR\ndssync-$VERSION.nds"
    Write-Host "NDS build complete!" -ForegroundColor Green
    Write-Host ""
}

# Build Wii U client (wut / Aroma)
if ($wantWiiu) {
    Write-Host "Building Wii U client..." -ForegroundColor Yellow
    & C:\devkitpro\msys2\usr\bin\bash.exe --login /e/projects/3dssync/wiiu/build.sh clean
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Wii U build failed!" -ForegroundColor Red
        exit 1
    }
    Copy-Item "wiiu\wiiusync.rpx" "$OUTPUT_DIR\wiiusync-$VERSION.rpx"
    Copy-Item "wiiu\wiiusync.wuhb" "$OUTPUT_DIR\wiiusync-$VERSION.wuhb"
    Write-Host "Wii U build complete!" -ForegroundColor Green
    Write-Host ""
}

# Build PS3 client via WSL
if ($wantPs3) {
    Write-Host "Building PS3 client via WSL..." -ForegroundColor Yellow
    wsl bash -c "export PS3DEV=/usr/local/ps3dev && export PSL1GHT=/usr/local/ps3dev && export PATH=/usr/local/ps3dev/bin:/usr/local/ps3dev/ppu/bin:/usr/bin:/bin && cd /mnt/e/projects/3dssync/ps3 && make clean && make"
    if ($LASTEXITCODE -ne 0) {
        Write-Host "PS3 build failed!" -ForegroundColor Red
        exit 1
    }
    Copy-Item "ps3\ps3sync.pkg" "$OUTPUT_DIR\ps3sync-$VERSION.pkg"
    Write-Host "PS3 build complete!" -ForegroundColor Green
    Write-Host ""
}

# Build PSP client via WSL
if ($wantPsp) {
    Write-Host "Building PSP client via WSL..." -ForegroundColor Yellow
    wsl bash -c "export PSPDEV=/home/pepa/pspdev && export PSPSDK=/home/pepa/pspdev/psp/sdk && export PATH=/home/pepa/pspdev/bin:/usr/local/bin:/usr/bin:/bin && cd /mnt/e/projects/3dssync/psp && make clean && make"
    if ($LASTEXITCODE -ne 0) {
        Write-Host "PSP build failed!" -ForegroundColor Red
        exit 1
    }
    Copy-Item "psp\EBOOT.PBP" "$OUTPUT_DIR\EBOOT-$VERSION.PBP"
    Write-Host "PSP build complete!" -ForegroundColor Green
    Write-Host ""
}

# Build Vita client via WSL
if ($wantVita) {
    Write-Host "Building Vita client via WSL..." -ForegroundColor Yellow
    wsl bash -c "export VITASDK=/usr/local/vitasdk && export PATH=/usr/local/vitasdk/bin:/usr/local/bin:/usr/bin:/bin && cd /mnt/e/projects/3dssync/vita && ./build.sh"
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Vita build failed!" -ForegroundColor Red
        exit 1
    }
    Copy-Item "vita\build\vitasync.vpk" "$OUTPUT_DIR\vitasync-$VERSION.vpk"
    Write-Host "Vita build complete!" -ForegroundColor Green
    Write-Host ""
}

# Build Xbox (original) client via WSL (nxdk)
if ($wantXbox) {
    Write-Host "Building Xbox (original) client via WSL (nxdk)..." -ForegroundColor Yellow
    wsl bash /mnt/e/projects/3dssync/xbox/build.sh clean
    wsl bash /mnt/e/projects/3dssync/xbox/build.sh
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Xbox build failed!" -ForegroundColor Red
        exit 1
    }
    Copy-Item "xbox\bin\default.xbe" "$OUTPUT_DIR\xboxsync-$VERSION.xbe"
    if (Test-Path "xbox\SaveSync.iso") {
        Copy-Item "xbox\SaveSync.iso" "$OUTPUT_DIR\xboxsync-$VERSION.iso"
    }
    Write-Host "Xbox build complete!" -ForegroundColor Green
    Write-Host ""
}

# Summary
Write-Host "============================================" -ForegroundColor Cyan
Write-Host "All builds complete!" -ForegroundColor Green
Write-Host "Output files in: $OUTPUT_DIR" -ForegroundColor Cyan
Write-Host ""
Get-ChildItem $OUTPUT_DIR | ForEach-Object {
    $size = [math]::Round($_.Length / 1KB, 2)
    Write-Host "  $($_.Name) - ${size}KB"
}
Write-Host "============================================" -ForegroundColor Cyan
