/*
 * PS2 Save Sync — main entry.
 *
 * Boot order:
 *   1. SifInitRpc, kernel + IOP setup
 *   2. Reset IOP and load embedded IRX modules:
 *        sio2man, mcman, mcserv, padman   — controllers + memcards
 *        ata_bd                           — internal HDD as BDM mass storage
 *        usbd, usbhdfsd                   — USB mass storage (mass:/)
 *        ps2dev9, netman, smap            — network adapter / ATA bridge
 *   3. Initialise libdebug screen
 *   4. Load config from mc0:/3DSSYNC/CONFIG.TXT
 *   5. Bring up networking via ps2ip (static IP by default, DHCP optional)
 *   6. Probe storage: APA/HDLoader hdd0: or mass:/ folder installs
 *   7. Run the menu loop (ROMs / Downloads / Config views)
 *
 * Phase 1 only: ROM browser + USB/APA HDD installer.  Save sync (MCP2)
 * is stubbed in the menu - the next phase wires it up.
 */

#include "common.h"
#include "config.h"
#include "downloads.h"
#include "hdl.h"
#include "http.h"
#include "network.h"
#include "roms.h"
#include "saves.h"
#include "ui.h"
#include "irx_mods.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdarg.h>
#include <time.h>
#include <errno.h>
#include <sys/stat.h>
#include <unistd.h>

#include <kernel.h>
#include <sifrpc.h>
#include <loadfile.h>
#include <iopcontrol.h>
#include <iopheap.h>
#include <libpad.h>
#include <libmc.h>
#include <libhdd.h>
#include <debug.h>
#include <delaythread.h>
#include <netman.h>
#include <ps2ip.h>
#include <sbv_patches.h>

/* fileXio_rpc.h is gated by NEWLIB_PORT_AWARE — defining it before
 * inclusion is the supported way to call fileXioInit() from a newlib
 * project.  We need that call: even with fileXio.irx loaded on the IOP
 * side, the EE-side stub never binds its RPC descriptor until
 * fileXioInit() is invoked, and any fopen/stat on a bdm-mounted volume
 * (mass:/, hdd0:/, etc.) silently returns ENODEV. */
#define NEWLIB_PORT_AWARE
#include <fileXio_rpc.h>

/* ---- IRX loading helpers ---- */

static bool g_ps2dev9_loaded = false;
static bool g_ps2atad_loaded = false;
static bool g_poweroff_loaded = false;
static bool g_ps2hdd_loaded = false;
static bool g_ps2fs_loaded = false;

/* Returns 0 on success, negative on failure.
 *
 * SifExecModuleBuffer return values (per ps2lib_err.h):
 *   >= 0  module id, success
 *   -200  E_IOP_DEPENDANCY   — depends on a module that didn't load
 *   -201  E_LF_NOT_IRX       — buffer not a valid IRX (often misaligned)
 *   -203  E_LF_FILE_NOT_FOUND
 *   -204  E_LF_FILE_IO_ERROR
 *
 * `ret` is the IRX `_start` return value (MODULE_RESIDENT_END=0,
 * MODULE_NO_RESIDENT_END=1, or a module-defined error). */
static int load_irx(const unsigned char *blob, unsigned int size,
                    const char *label,
                    int argc, const char *argv)
{
    int ret = 0;
    int id = SifExecModuleBuffer((void *)blob, size, argc,
                                 (char *)argv, &ret);
    if (id < 0) {
        scr_printf("  IRX %s failed (id=%d ret=%d)\n", label, id, ret);
        return id;
    }
    if (ret != 0 && ret != 1) {
        scr_printf("  IRX %s start error (id=%d ret=%d)\n", label, id, ret);
        return -1;
    }
    scr_printf("  IRX %s ok (id=%d)\n", label, id);
    return 0;
}

static void boot_iop_modules(void) {
    /* SifInitRpc must run before SifIopReset — establishes the EE-side
     * RPC state that SifIopSync waits on. */
    SifInitRpc(0);
    while (!SifIopReset("", 0)) {}
    while (!SifIopSync()) {}

    /* Re-establish RPC after reset; bring up the SIF heap so loaded
     * IRX modules can allocate from IOP RAM. */
    SifInitRpc(0);
    SifInitIopHeap();
    SifLoadFileInit();

    /* sbv patches let unsigned IRX run from EE memory. */
    sbv_patch_enable_lmb();
    sbv_patch_disable_prefix_check();

    scr_printf("Loading IOP modules...\n");

    /* iomanX + fileXio first. Storage modules are loaded after config
     * so storage=usb/hdd/auto can decide which bridges come up. */
    load_irx(iomanX_irx,      iomanX_irx_size,      "iomanX",      0, NULL);
    load_irx(fileXio_irx,     fileXio_irx_size,     "fileXio",     0, NULL);

    /* SIO2/memcard/pad load from Sony's IOP ROM (rom0:) — the PS2SDK
     * mcman.irx / mcserv.irx have a different RPC ABI and cause libmc
     * mcInit to hang. rom0: modules are guaranteed present on every
     * PS2 and use the original Sony RPC numbers libmc expects. */
    int ret;
    ret = SifLoadModule("rom0:SIO2MAN", 0, NULL);
    scr_printf("  rom0:SIO2MAN -> %d\n", ret);
    ret = SifLoadModule("rom0:MCMAN",   0, NULL);
    scr_printf("  rom0:MCMAN   -> %d\n", ret);
    ret = SifLoadModule("rom0:MCSERV",  0, NULL);
    scr_printf("  rom0:MCSERV  -> %d\n", ret);
    ret = SifLoadModule("rom0:PADMAN",  0, NULL);
    scr_printf("  rom0:PADMAN  -> %d\n", ret);

    /* mmceman: MMCE protocol for MemCard Pro 2 / SD2PSX GameID switching.
     * Needs iomanX + fileXio (loaded above) and SIO2MAN (rom0, above). */
    load_irx(mmceman_irx, mmceman_irx_size, "mmceman", 0, NULL);
}

static void boot_device_modules(const SyncState *state) {
    bool want_usb = !state || state->storage_pref != STORAGE_PREF_HDD;
    bool want_hdd = !state || state->storage_pref != STORAGE_PREF_USB;

    scr_printf("Loading device modules (storage=%s)...\n",
               state ? config_storage_pref_to_str(state->storage_pref) : "auto");

    /* DEV9 backs both the PS2 fat network adapter and internal ATA HDD. */
    if (load_irx(ps2dev9_irx, ps2dev9_irx_size, "ps2dev9", 0, NULL) == 0) {
        g_ps2dev9_loaded = true;
    }

    /* BDM mass storage stack.  Two competing USB options:
     *   (a) Modern split: bdm + usbmass_bd + bdmfs_fatfs
     *       bdmfs_fatfs registers mass: once a USB or ATA device with a
     *       FAT/exFAT partition attaches.
     *   (b) Legacy: usbhdfsd — single module, no bdm dependency.
     *
     * Some launchers (and PCSX2) preload an old bdm.irx that's binary
     * incompatible with our newer usbmass_bd.irx, causing usbmass_bd to
     * fail with id=-200 (unresolved symbol).  When that happens we fall
     * back to usbhdfsd, which has no external dependencies. */
    int bdm_rc        = load_irx(bdm_irx,         bdm_irx_size,         "bdm",         0, NULL);
    int ata_rc        = 0;
    int usbmass_rc    = 0;

    if (want_hdd) {
        ata_rc = load_irx(ata_bd_irx, ata_bd_irx_size, "ata_bd", 0, NULL);
    }

    if (want_usb) {
        load_irx(usbd_irx, usbd_irx_size, "usbd", 0, NULL);
        usbmass_rc = load_irx(usbmass_bd_irx, usbmass_bd_irx_size,
                              "usbmass_bd", 0, NULL);
    }

    int bdmfs_rc      = load_irx(bdmfs_fatfs_irx, bdmfs_fatfs_irx_size, "bdmfs_fatfs", 0, NULL);

    if (want_usb && (bdm_rc != 0 || usbmass_rc != 0 || bdmfs_rc != 0)) {
        scr_printf("  bdm stack failed - falling back to usbhdfsd\n");
        load_irx(usbhdfsd_irx, usbhdfsd_irx_size, "usbhdfsd", 0, NULL);
    }
    if (want_hdd && ata_rc != 0) {
        scr_printf("  internal HDD BDM bridge unavailable (rc=%d)\n", ata_rc);
    }

    load_irx(netman_irx,      netman_irx_size,      "netman",      0, NULL);
    load_irx(smap_irx,        smap_irx_size,        "smap",        0, NULL);

    /* Bind the EE-side fileXio stub.  Without this, every newlib stdio
     * call against an iomanX-registered device (mass:, hdd:, etc.)
     * silently fails — the RPC descriptor is uninitialised. */
    int fxr = fileXioInit();
    scr_printf("  fileXioInit -> %d\n", fxr);
}

/* ---- mass:/ wait ---- */

static bool wait_for_mass(SyncState *state, int timeout_seconds) {
    /* BDM enumeration is asynchronous — wait until a ``mass:`` root is
     * usable. Some PS2 USB stacks reject fopen() on a directory, so we
     * probe by creating the app data directory on each possible root.
     *
     * bdmfs_fatfs registers both internal ATA HDD and USB FAT/exFAT
     * partitions as massN:. Because ata_bd loads before usbmass_bd, HDD
     * is normally mass:/mass0: and USB normally starts at mass1: when
     * both are connected. */
    static const char *auto_roots[] = {
        "mass:", "mass0:", "mass1:", "mass2:", "mass3:", NULL
    };
    static const char *hdd_roots[] = {
        "mass:", "mass0:", "mass1:", "mass2:", "mass3:", NULL
    };
    static const char *usb_roots[] = {
        "mass1:", "mass2:", "mass3:", "mass:", "mass0:", NULL
    };

    const char **roots = auto_roots;
    if (state->storage_pref == STORAGE_PREF_USB) roots = usb_roots;
    else if (state->storage_pref == STORAGE_PREF_HDD) roots = hdd_roots;

    state->usb_ready = false;
    strncpy(state->usb_root, STORAGE_DEFAULT_ROOT, sizeof(state->usb_root) - 1);
    state->usb_root[sizeof(state->usb_root) - 1] = '\0';
    roms_set_storage_root(state->usb_root);

    int last_errno[8] = {0};

    for (int i = 0; i < timeout_seconds * 4; i++) {
        for (int r = 0; roots[r]; r++) {
            char data_dir[96];
            char root_dir[32];
            snprintf(data_dir, sizeof(data_dir), "%s%s",
                     roots[r], STORAGE_DATA_SUBDIR);
            snprintf(root_dir, sizeof(root_dir), "%s/", roots[r]);

            errno = 0;
            struct stat st;
            int stat_rc = stat(data_dir, &st);
            int stat_errno = errno;

            if (stat_rc == 0) {
                scr_printf("  storage: stat ok at %s\n", data_dir);
                strncpy(state->usb_root, roots[r], sizeof(state->usb_root) - 1);
                state->usb_root[sizeof(state->usb_root) - 1] = '\0';
                state->usb_ready = true;
                roms_set_storage_root(state->usb_root);
                return true;
            }

            errno = 0;
            int mkdir_rc = mkdir(data_dir, 0777);
            int mkdir_errno = errno;
            if (mkdir_rc == 0 || mkdir_errno == EEXIST) {
                scr_printf("  storage: mkdir ok at %s\n", data_dir);
                strncpy(state->usb_root, roots[r], sizeof(state->usb_root) - 1);
                state->usb_root[sizeof(state->usb_root) - 1] = '\0';
                state->usb_ready = true;
                roms_set_storage_root(state->usb_root);
                return true;
            }

            errno = 0;
            if (stat(root_dir, &st) == 0) {
                scr_printf("  storage: root stat ok at %s - using anyway\n", root_dir);
                mkdir(data_dir, 0777);
                strncpy(state->usb_root, roots[r], sizeof(state->usb_root) - 1);
                state->usb_root[sizeof(state->usb_root) - 1] = '\0';
                state->usb_ready = true;
                roms_set_storage_root(state->usb_root);
                return true;
            }

            last_errno[r] = mkdir_errno ? mkdir_errno : stat_errno;
        }
        DelayThread(250000);
    }

    /* Show why we gave up — last errno from each candidate root. */
    scr_printf("  storage: wait_for_mass timed out after %ds\n", timeout_seconds);
    for (int r = 0; roots[r]; r++) {
        scr_printf("    %-7s last errno=%d\n", roots[r], last_errno[r]);
    }
    return false;
}

/* ---- Pad input ---- */

static char g_pad_buf[256] __attribute__((aligned(64)));
static struct padButtonStatus g_pad_state;

static void pad_init(void) {
    padInit(0);
    padPortOpen(0, 0, g_pad_buf);
    padSetMainMode(0, 0, PAD_MMODE_DUALSHOCK, PAD_MMODE_LOCK);
}

/* Re-open the controller port to recover padman/SIO2 state after an MMCE/gen1
 * GameID transaction.  mmceman briefly takes over the SIO2 unit; on a slow
 * gen1 that can leave padman's controller port in a bad state, hanging the
 * next poll (apparent freeze).  Closing + reopening the port resyncs it. */
static void pad_reinit(void) {
    padPortClose(0, 0);
    padPortOpen(0, 0, g_pad_buf);
    padSetMainMode(0, 0, PAD_MMODE_DUALSHOCK, PAD_MMODE_LOCK);
    /* Wait for the port to come back to a stable/ready state. */
    for (int i = 0; i < 200; i++) {
        int st = padGetState(0, 0);
        if (st == PAD_STATE_STABLE || st == PAD_STATE_FINDCTP1) break;
        if (st == PAD_STATE_DISCONN) break;
        DelayThread(2000);
    }
}

static unsigned int g_prev_btns = 0;

static unsigned int pad_read_pressed(void) {
    /* Non-blocking: bail this frame if the pad isn't in a stable state.
     * The previous version spun on padGetState() until STABLE, which
     * deadlocks the main loop when the pad RPC stalls (seen post-gsKit
     * init). Returning 0 here just means "no input this tick" — the
     * loop will retry next iteration. */
    int state = padGetState(0, 0);
    if (state != PAD_STATE_STABLE && state != PAD_STATE_FINDCTP1) {
        return 0;
    }
    if (padRead(0, 0, &g_pad_state) == 0) return 0;
    unsigned int btns = 0xFFFF ^ g_pad_state.btns;
    unsigned int pressed = btns & ~g_prev_btns;
    g_prev_btns = btns;
    return pressed;
}

/* Modal yes/no prompt for read/write/sync operations.  Blocks until the
 * user presses CROSS (confirm) or CIRCLE (cancel). */
static bool confirm(const char *fmt, ...) {
    char msg[200];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(msg, sizeof(msg), fmt, ap);
    va_end(ap);

    char body[320];
    snprintf(body, sizeof(body), "%s\n\nCROSS = Confirm    CIRCLE = Cancel", msg);
    ui_clear();
    ui_draw_message("Confirm", body);
    ui_flush();

    for (;;) {
        unsigned int p = pad_read_pressed();
        if (p & PAD_CROSS)  return true;
        if (p & PAD_CIRCLE) return false;
        DelayThread(16000);
    }
}

/* ---- App state ---- */

/* Forward declaration: fetch_catalog / scan_local / run_active_download
 * call redraw() so long-running operations flush their "started" /
 * "finished" status messages to the screen instead of leaving the
 * previous frame on screen until the next button press. */
static void redraw(void);

static SyncState     g_state;
static RomCatalog    g_catalog;
static LocalRomList  g_local;
static DownloadList  g_downloads;
static SaveVmcList   g_saves;
static McGameList    g_mcard;       /* slot 1 (port 0) */
static McGameList    g_mcard2;      /* slot 2 (port 1) */
static AppView       g_view = APP_VIEW_ROMS;
static int           g_rom_selected   = 0, g_rom_scroll   = 0;
static int           g_local_selected = 0, g_local_scroll = 0;
static int           g_dl_selected    = 0, g_dl_scroll    = 0;
static int           g_saves_selected = 0, g_saves_scroll = 0;
static int           g_mcard_selected  = 0, g_mcard_scroll  = 0;
static int           g_mcard2_selected = 0, g_mcard2_scroll = 0;
static ServerSaveList g_server;
static int           g_server_selected = 0, g_server_scroll = 0;
static int           g_server_source = 0;   /* 0=VMC, 1=Slot1, 2=Slot2 */
/* GameID device per slot lives in g_state.mmce_mode[] (persisted to config).
 * 0=off, 1=auto, 2=gen1, 3=gen2. */
static const char   *g_mmce_mode_names[4] = {"off", "auto", "gen1", "gen2"};
static bool          g_hdd_format_confirm = false;

static char          g_scratch[256 * 1024];      /* JSON page buffer */

static bool ensure_hdd_format_modules(void);

static bool require_storage_ready(void) {
    if (!g_state.usb_ready) {
        ui_error("Storage not ready; catalog browse still works");
        return false;
    }
    return true;
}

static bool init_hdloader_targets(void) {
    scr_printf("BOOT: probing APA/HDLoader hdd0:...\n");
    if (!ensure_hdd_format_modules()) return false;

    if (hddCheckPresent() != 0) {
        scr_printf("BOOT: no internal HDD present\n");
        return false;
    }

    if (hddCheckFormatted() != 0) {
        scr_printf("BOOT: internal HDD is not APA-formatted\n");
        ui_status("HDD needs APA format; Config TRIANGLE twice");
        return false;
    }

    g_state.storage_backend = STORAGE_BACKEND_HDLOADER;
    g_state.usb_ready = true;     /* legacy readiness flag used by UI/actions */
    strncpy(g_state.usb_root, "hdd0:hdl", sizeof(g_state.usb_root) - 1);
    g_state.usb_root[sizeof(g_state.usb_root) - 1] = '\0';

    roms_set_storage_root("hdd0:");
    roms_set_downloads_file(HDL_DOWNLOADS_FILE);
    downloads_load(&g_downloads);
    scr_printf("BOOT: APA/HDLoader storage ready at hdd0:\n");
    return true;
}

static void init_storage_targets(void) {
    g_state.storage_backend = STORAGE_BACKEND_NONE;
    g_state.usb_ready = false;
    g_downloads.count = 0;

    scr_printf("BOOT: probing storage (%s)...\n",
               config_storage_pref_to_str(g_state.storage_pref));

    if (g_state.storage_pref != STORAGE_PREF_USB) {
        if (init_hdloader_targets()) return;
        if (g_state.storage_pref == STORAGE_PREF_HDD) {
            scr_printf("WARN: APA/HDLoader storage unavailable\n");
            ui_status("HDD not ready; format APA from Config");
            return;
        }
    }

    if (g_state.storage_pref != STORAGE_PREF_HDD) {
        if (!wait_for_mass(&g_state, 12)) {
            scr_printf("WARN: no storage root mounted\n");
            ui_status("Storage not ready; downloads disabled");
            return;
        }

        g_state.storage_backend = STORAGE_BACKEND_MASS;
        scr_printf("BOOT: mass storage ready at %s\n", g_state.usb_root);
        roms_ensure_target_dirs();
        downloads_load(&g_downloads);
    }
}

static bool ensure_hdd_format_modules(void) {
    int rc;
    static const char hdd_args[] = "-o\0" "4\0" "-n\0" "20";
    static const char pfs_args[] = "-m\0" "4\0" "-o\0" "10\0" "-n\0" "40";

    if (!g_ps2dev9_loaded) {
        rc = load_irx(ps2dev9_irx, ps2dev9_irx_size, "ps2dev9", 0, NULL);
        if (rc != 0) {
            ui_error("ps2dev9 failed (%d)", rc);
            return false;
        }
        g_ps2dev9_loaded = true;
    }
    if (!g_poweroff_loaded) {
        rc = load_irx(poweroff_irx, poweroff_irx_size, "poweroff", 0, NULL);
        if (rc != 0) {
            ui_error("poweroff.irx failed (%d)", rc);
            return false;
        }
        g_poweroff_loaded = true;
    }
    if (!g_ps2atad_loaded) {
        rc = load_irx(ps2atad_irx, ps2atad_irx_size, "ps2atad", 0, NULL);
        if (rc != 0) {
            ui_error("ps2atad.irx failed (%d)", rc);
            return false;
        }
        g_ps2atad_loaded = true;
    }
    if (!g_ps2hdd_loaded) {
        rc = load_irx(ps2hdd_irx, ps2hdd_irx_size, "ps2hdd", 4, hdd_args);
        if (rc != 0) {
            ui_error("ps2hdd.irx failed (%d)", rc);
            return false;
        }
        g_ps2hdd_loaded = true;
    }
    if (!g_ps2fs_loaded) {
        rc = load_irx(ps2fs_irx, ps2fs_irx_size, "ps2fs", 6, pfs_args);
        if (rc != 0) {
            ui_error("ps2fs.irx failed (%d)", rc);
            return false;
        }
        g_ps2fs_loaded = true;
    }

    (void)hddPreparePoweroff();
    rc = fileXioInit();
    scr_printf("  fileXioInit (hdd formatter) -> %d\n", rc);
    return true;
}

static int ensure_opl_partition(void) {
    t_hddFilesystem filesystems[32];
    int count = hddGetFilesystemList(filesystems, 32);

    if (count >= 0) {
        for (int i = 0; i < count && i < 32; i++) {
            if (filesystems[i].fileSystemGroup == FS_GROUP_COMMON &&
                strcmp(filesystems[i].name, "OPL") == 0)
            {
                return 1;       /* already exists */
            }
        }
    }

    char name[] = "OPL";
    int rc = hddMakeFilesystem(512, name, FS_GROUP_COMMON);
    return rc < 0 ? rc : 0;      /* 0 = created */
}

static void format_internal_hdd(void) {
    ui_status("Checking internal HDD...");
    redraw();

    if (!ensure_hdd_format_modules()) return;

    if (hddCheckPresent() != 0) {
        ui_error("No internal HDD detected");
        return;
    }

    if (hddCheckFormatted() == 0) {
        int opl = ensure_opl_partition();
        if (opl < 0) {
            ui_error("HDD is APA; +OPL create failed (%d)", opl);
        } else {
            ui_status("HDD already APA-formatted; +OPL %s",
                      opl ? "ready" : "created");
        }
        return;
    }

    ui_status("Formatting internal HDD as PS2 APA...");
    redraw();

    int rc = hddFormat();
    if (rc != 0) {
        ui_error("HDD format failed (%d)", rc);
        return;
    }

    int opl = ensure_opl_partition();
    if (opl < 0) {
        ui_error("HDD formatted; +OPL create failed (%d)", opl);
    } else {
        ui_status("HDD formatted as PS2 APA; +OPL %s",
                  opl ? "ready" : "created");
    }
}

/* Live progress from network_set_progress64_cb. */
static volatile uint64_t g_active_done  = 0;
static volatile uint64_t g_active_total = 0;
static volatile uint64_t g_active_bps   = 0;
static volatile bool     g_pause_requested = false;
static time_t            g_last_speed_t = 0;
static uint64_t          g_last_speed_bytes = 0;

static int progress_cb(uint64_t done, uint64_t total) {
    g_active_done  = done;
    g_active_total = total;

    time_t now = time(NULL);
    if (now != g_last_speed_t) {
        g_active_bps = done > g_last_speed_bytes
                     ? (done - g_last_speed_bytes) / (uint64_t)(now - g_last_speed_t + 1)
                     : 0;
        /* Refresh the screen at most once per wall-clock second so the
         * progress bar / KB/s line update without flooding libdebug
         * with full-frame char DMA. */
        redraw();
        g_last_speed_t     = now;
        g_last_speed_bytes = done;
    }

    return g_pause_requested ? 1 : 0;
}

typedef struct {
    DownloadEntry *entry;
    HdlInstall install;
} HdlDownloadContext;

static int hdl_download_begin(uint64_t content_length, void *user) {
    HdlDownloadContext *ctx = (HdlDownloadContext *)user;
    if (!ctx || !ctx->entry) return -1;
    return hdl_install_begin(&ctx->install, ctx->entry, content_length);
}

static int hdl_download_write(const void *data, uint32_t len, void *user) {
    HdlDownloadContext *ctx = (HdlDownloadContext *)user;
    if (!ctx) return -1;
    return hdl_install_write(data, len, &ctx->install);
}

static DownloadEntry *queue_catalog_entry(const RomEntry *rom) {
    DownloadEntry *e = downloads_upsert_from_catalog(&g_downloads, rom);
    if (e && g_state.storage_backend == STORAGE_BACKEND_HDLOADER) {
        hdl_resolve_target_path_from_rom(rom, e->target_path,
                                         sizeof(e->target_path));
        e->offset = 0;  /* HDLoader installs are rewritten atomically. */
    }
    return e;
}

/* ---- View handlers ---- */

static void clamp_scroll(int *selected, int *scroll, int count) {
    if (count == 0) { *selected = 0; *scroll = 0; return; }
    if (*selected < 0) *selected = 0;
    if (*selected >= count) *selected = count - 1;
    int visible = ui_list_visible();
    if (visible < 1) visible = 1;
    if (*selected < *scroll) *scroll = *selected;
    if (*selected >= *scroll + visible) *scroll = *selected - visible + 1;
    if (*scroll < 0) *scroll = 0;
}

static void fetch_catalog(void) {
    ui_status("Fetching PS2 catalog...");
    redraw();   /* show "Fetching..." before HTTP blocks the loop */
    bool ok = roms_fetch_catalog(&g_state, "PS2",
                                 g_scratch, sizeof(g_scratch),
                                 &g_catalog);
    if (!ok) {
        ui_error("%s", g_catalog.last_error);
    } else {
        ui_status("Catalog: %d entries", g_catalog.count);
    }
    redraw();   /* show result */
}

static void scan_local(void) {
    if (!g_state.usb_ready) {
        snprintf(g_local.last_error, sizeof(g_local.last_error),
                 "Storage not ready");
        g_local.count = 0;
        return;
    }
    ui_status("Scanning local games...");
    redraw();
    if (g_state.storage_backend == STORAGE_BACKEND_HDLOADER) {
        hdl_scan_local(&g_local);
    } else {
        roms_scan_local(&g_local);
    }
    if (g_local.count > 0) {
        ui_status("Local: %d %s", g_local.count,
                  g_state.storage_backend == STORAGE_BACKEND_HDLOADER
                      ? "HDL games"
                      : "ISOs");
    }
    redraw();
}

static void fill_vmc_names(SaveVmcList *list);   /* forward decl */

static void scan_saves(void) {
    if (!g_state.usb_ready) {
        snprintf(g_saves.last_error, sizeof(g_saves.last_error),
                 "Storage not ready");
        g_saves.count = 0;
        return;
    }
    ui_status("Scanning card images...");
    redraw();
    saves_scan_local(&g_saves);
    fill_vmc_names(&g_saves);
    if (g_saves.count > 0) {
        ui_status("Saves: %d card image(s)", g_saves.count);
    }
    redraw();
}

static void upload_selected_save(void) {
    if (!require_storage_ready()) return;
    if (g_saves.count == 0 || g_saves_selected >= g_saves.count) return;

    const SaveVmc *v = &g_saves.items[g_saves_selected];
    if (!confirm("Upload card image\n%s\nto server (split per game)?", v->filename))
        return;
    ui_status("Uploading %s...", v->filename);
    redraw();

    char msg[128];
    int rc = saves_upload_vmc(&g_state, v, msg, sizeof(msg));
    if (rc < 0) ui_error("%s", msg);
    else        ui_status("%s", msg);
    redraw();
}

static void pull_all_saves(void) {
    if (!require_storage_ready()) return;
    if (!confirm("Download ALL server PS1/PS2 saves\ninto VMC/ ?\nExisting VMC files overwritten."))
        return;
    ui_status("Pulling PS2 saves from server...");
    redraw();

    char msg[128];
    int rc = saves_pull_all(&g_state, g_scratch, sizeof(g_scratch),
                            msg, sizeof(msg));
    if (rc < 0) ui_error("%s", msg);
    else        ui_status("%s", msg);
    scan_saves();
}

/* Fill each card game's display name from the fetched server save list. */
static void fill_mc_names(McGameList *list) {
    for (int i = 0; i < list->count; i++) {
        list->items[i].name[0] = '\0';
        for (int j = 0; j < g_server.count; j++) {
            if (strcmp(g_server.items[j].serial, list->items[i].serial) == 0) {
                strncpy(list->items[i].name, g_server.items[j].name,
                        sizeof(list->items[i].name) - 1);
                break;
            }
        }
    }
}

/* Fill each VMC file's display name from the fetched server save list. */
static void fill_vmc_names(SaveVmcList *list) {
    for (int i = 0; i < list->count; i++) {
        list->items[i].name[0] = '\0';
        if (list->items[i].serial[0] == '\0') continue;
        for (int j = 0; j < g_server.count; j++) {
            if (strcmp(g_server.items[j].serial, list->items[i].serial) == 0) {
                strncpy(list->items[i].name, g_server.items[j].name,
                        sizeof(list->items[i].name) - 1);
                break;
            }
        }
    }
}

static void scan_mcard_list(int port, McGameList *list) {
    ui_status("Scanning memory card slot %d...", port + 1);
    redraw();
    saves_scan_mcard(port, list);
    fill_mc_names(list);
    if (list->count > 0) {
        ui_status("Slot %d: %d game save(s)", port + 1, list->count);
    }
    redraw();
}

/* Upload one card game, dispatching by card type (PS1 single save vs PS2 dir). */
static int upload_one_game(McGameList *list, const McGame *g,
                           char *msg, size_t msg_size) {
    return list->is_ps1
        ? saves_upload_ps1_save(&g_state, list->port, g, msg, msg_size)
        : saves_upload_mc_game(&g_state, list->port, g, msg, msg_size);
}

static void upload_mc_game_at(McGameList *list, int sel) {
    if (!network_is_ready(&g_state)) { ui_error("Network not ready"); return; }
    if (list->count == 0 || sel >= list->count) return;

    const McGame *g = &list->items[sel];
    if (!confirm("Upload %s\nfrom Slot%d to server?",
                 g->serial[0] ? g->serial : g->dir, list->port + 1))
        return;
    ui_status("Uploading %s...", g->serial[0] ? g->serial : g->dir);
    redraw();

    char msg[128];
    int rc = upload_one_game(list, g, msg, sizeof(msg));
    if (rc < 0) ui_error("%s", msg);
    else        ui_status("%s", msg);
    redraw();
}

static void restore_mc_game_at(McGameList *list, int sel) {
    if (!network_is_ready(&g_state)) { ui_error("Network not ready"); return; }
    if (list->count == 0 || sel >= list->count) return;

    const McGame *g = &list->items[sel];
    if (g->serial[0] == '\0') { ui_error("No serial for %s", g->dir); return; }
    int port = list->port;
    if (!confirm("Restore %s to Slot%d?\nWrites only this game's save\n(overwrites it if present).",
                 g->serial, port + 1))
        return;
    ui_status("Restoring %s...", g->serial);
    redraw();

    char msg[128];
    int rc = list->is_ps1
        ? saves_restore_ps1_save(&g_state, port, g->serial, msg, sizeof(msg))
        : saves_restore_mc_game(&g_state, port, g->serial, msg, sizeof(msg));
    /* Rescan silently, then show the restore result LAST. */
    saves_scan_mcard(port, list);
    fill_mc_names(list);
    if (rc < 0) ui_error("%s", msg);
    else        ui_status("%s", msg);
    redraw();
}

static void mark_server_local(void) {
    for (int i = 0; i < g_server.count; i++) {
        ServerSave *s = &g_server.items[i];
        s->local = false;
        for (int j = 0; j < g_mcard.count && !s->local; j++)
            if (strcmp(g_mcard.items[j].serial, s->serial) == 0) s->local = true;
        for (int j = 0; j < g_mcard2.count && !s->local; j++)
            if (strcmp(g_mcard2.items[j].serial, s->serial) == 0) s->local = true;
    }
}

static void fetch_server_saves(void) {
    if (!network_is_ready(&g_state)) { ui_error("Network not ready"); return; }
    ui_status("Fetching server saves...");
    redraw();
    saves_fetch_server(&g_state, g_scratch, sizeof(g_scratch), &g_server);
    mark_server_local();
    fill_mc_names(&g_mcard);
    fill_mc_names(&g_mcard2);
    fill_vmc_names(&g_saves);
    if (g_server.count > 0) ui_status("Server: %d save(s)", g_server.count);
    else if (g_server.last_error[0]) ui_error("%s", g_server.last_error);
    redraw();
}

/* ---- Server view sync source (VMC / Slot1 / Slot2) ---- */

static const char *source_name(void) {
    return g_server_source == 1 ? "Slot1" : g_server_source == 2 ? "Slot2" : "VMC";
}

/* Memory-card port for the current source, or -1 for VMC. */
static int source_port(void) {
    return g_server_source == 1 ? 0 : g_server_source == 2 ? 1 : -1;
}

static McGameList *source_list(void) {
    return g_server_source == 2 ? &g_mcard2 : &g_mcard;
}

/* X: download the selected server save into the current source. */
static void server_download_to_source(void) {
    if (g_server.count == 0 || g_server_selected >= g_server.count) return;
    const ServerSave *s = &g_server.items[g_server_selected];
    int port = source_port();

    if (port < 0) {                 /* VMC */
        if (!confirm("Download %s\nfrom server into VMC/ ?", s->serial)) return;
        ui_status("Downloading %s...", s->serial);
        redraw();
        char msg[128];
        int rc = saves_download_server_to_vmc(&g_state, s, msg, sizeof(msg));
        if (rc < 0) ui_error("%s", msg); else ui_status("%s", msg);
        redraw();
        return;
    }

    if (!confirm("Restore %s to %s?\nWrites only this game's save\n(overwrites it if present).",
                 s->serial, source_name()))
        return;
    ui_status("Restoring %s to %s...", s->serial, source_name());
    redraw();
    char msg[128];
    int rc = s->is_ps1
        ? saves_restore_ps1_save(&g_state, port, s->serial, msg, sizeof(msg))
        : saves_restore_mc_game(&g_state, port, s->serial, msg, sizeof(msg));
    /* Rescan first (it sets its own status), then show the restore result LAST
     * so it isn't clobbered by the scan line. */
    saves_scan_mcard(port, source_list());
    fill_mc_names(source_list());
    mark_server_local();
    if (rc < 0) ui_error("%s", msg); else ui_status("%s", msg);
    redraw();
}

/* TRIANGLE: upload the selected game's copy from the current source to server. */
static void server_upload_from_source(void) {
    if (!network_is_ready(&g_state)) { ui_error("Network not ready"); return; }
    if (g_server.count == 0 || g_server_selected >= g_server.count) return;
    const char *serial = g_server.items[g_server_selected].serial;
    int port = source_port();

    if (port < 0) {                 /* VMC: per-game upload not possible from a file */
        ui_error("VMC source: upload whole card from the VMC screen");
        return;
    }

    if (!confirm("Upload %s from %s\nto server?", serial, source_name())) return;
    ui_status("Reading %s on %s...", serial, source_name());
    redraw();

    McGameList *list = source_list();
    saves_scan_mcard(port, list);
    int idx = -1;
    for (int i = 0; i < list->count; i++)
        if (strcmp(list->items[i].serial, serial) == 0) { idx = i; break; }
    if (idx < 0) {
        ui_error("%s not on %s (R1 switches channel)", serial, source_name());
        return;
    }
    char msg[128];
    int rc = upload_one_game(list, &list->items[idx], msg, sizeof(msg));
    if (rc < 0) ui_error("%s", msg); else ui_status("%s", msg);
    mark_server_local();
    redraw();
}

/* L1: sync every save of the current source with the server. */
static void server_sync_all(void) {
    if (!network_is_ready(&g_state)) { ui_error("Network not ready"); return; }
    int port = source_port();

    if (port < 0) {                 /* VMC: pull all server saves into VMC/ */
        pull_all_saves();
        return;
    }

    if (!confirm("Upload ALL saves on %s\nto the server?", source_name())) return;
    ui_status("Scanning %s...", source_name());
    redraw();

    McGameList *list = source_list();
    saves_scan_mcard(port, list);

    int ok = 0, fail = 0;
    char msg[128];
    for (int i = 0; i < list->count; i++) {
        if (list->items[i].serial[0] == '\0') continue;
        ui_status("Uploading %s (%d/%d)...",
                  list->items[i].serial, i + 1, list->count);
        redraw();
        if (upload_one_game(list, &list->items[i], msg, sizeof(msg)) == 0)
            ok++;
        else
            fail++;
    }

    /* Refresh server list so the L (local) flags and names update. */
    saves_fetch_server(&g_state, g_scratch, sizeof(g_scratch), &g_server);
    mark_server_local();
    fill_mc_names(&g_mcard);
    fill_mc_names(&g_mcard2);
    ui_status("%s sync: %d uploaded, %d failed", source_name(), ok, fail);
    redraw();
}

static void cycle_server_source(void) {
    g_server_source = (g_server_source + 1) % 3;
    ui_set_server_source(source_name());
    ui_status("Sync source: %s", source_name());
    redraw();
}

static void cycle_mmce_mode(int port) {
    port = port == 1 ? 1 : 0;
    g_state.mmce_mode[port] = (g_state.mmce_mode[port] + 1) % 4;
    ui_set_mmce(port, g_state.mmce_mode[port]);
    config_save(&g_state);   /* persist the per-slot choice */
    ui_status("Slot%d GameID: %s (saved)", port + 1,
              g_mmce_mode_names[g_state.mmce_mode[port]]);
    redraw();
}

static void mcp_switch_gameid(const char *serial, int port) {
    port = port == 1 ? 1 : 0;
    if (g_state.mmce_mode[port] == 0) {
        ui_error("Slot%d GameID off - SELECT to set device", port + 1);
        redraw();
        return;
    }
    if (!serial || !serial[0]) { ui_error("No serial to switch to"); return; }
    int mode = g_state.mmce_mode[port];
    char msg[128];
    int rc = saves_mcp_set_gameid(serial, port, mode, msg, sizeof(msg));
    if (rc < 0) { ui_error("[mode=%s] %s", g_mmce_mode_names[mode], msg); redraw(); return; }

    /* Do NOT auto-rescan here: right after a channel switch the MemCard Pro is
     * still mounting the new VMC, and probing it mid-mount intermittently hangs
     * libmc (gen1 freeze).  Tell the user to rescan ([]) once it has switched. */
    ui_status("[%s] %s - press [] to rescan", g_mmce_mode_names[mode], msg);
    redraw();
}

static void run_active_download(DownloadEntry *e) {
    if (!e || !require_storage_ready()) return;

    if (g_state.storage_backend == STORAGE_BACKEND_HDLOADER) {
        hdl_resolve_target_path_from_download(e, e->target_path,
                                              sizeof(e->target_path));
        e->offset = 0;   /* No partial APA resume; failed installs are removed. */
    }

    g_active_done       = e->offset;
    g_active_total      = e->total;
    g_pause_requested   = false;
    g_last_speed_t      = time(NULL);
    g_last_speed_bytes  = e->offset;

    network_set_progress64_cb(progress_cb);

    if (g_state.storage_backend != STORAGE_BACKEND_HDLOADER) {
        /* Make sure the destination dir exists. */
        char dir[256];
        strncpy(dir, e->target_path, sizeof(dir));
        dir[sizeof(dir) - 1] = '\0';
        char *slash = strrchr(dir, '/');
        if (slash) {
            *slash = '\0';
            roms_mkdir_p(dir);
        }
    }

    e->status = DL_STATUS_ACTIVE;
    downloads_save(&g_downloads);

    uint64_t total = 0;
    int rc;
    int finish_rc = 0;
    HdlDownloadContext hdl_ctx;
    memset(&hdl_ctx, 0, sizeof(hdl_ctx));
    hdl_ctx.entry = e;

    if (g_state.storage_backend == STORAGE_BACKEND_HDLOADER) {
        rc = network_download_rom_to_sink(&g_state, e->rom_id,
                                          e->extract_format,
                                          hdl_download_begin,
                                          hdl_download_write,
                                          &hdl_ctx,
                                          0,
                                          &total);
        finish_rc = hdl_install_finish(&hdl_ctx.install, rc == 0);
        if (rc == 0 && finish_rc != 0) rc = finish_rc;
    } else {
        rc = network_download_rom_resumable(&g_state, e->rom_id,
                                            e->extract_format,
                                            e->target_path,
                                            e->offset,
                                            &total);
    }
    network_set_progress64_cb(NULL);
    if (total > 0) e->total = total;

    if (rc == 0) {
        e->status = DL_STATUS_COMPLETED;
        e->offset = e->total > 0 ? e->total : g_active_done;
        ui_status("Done: %s", e->name);
    } else if (rc == 1) {
        e->status = DL_STATUS_PAUSED;
        e->offset = (g_state.storage_backend == STORAGE_BACKEND_HDLOADER)
                  ? 0
                  : g_active_done;
        ui_status("Paused: %s", e->name);
    } else {
        e->status = DL_STATUS_ERROR;
        e->offset = (g_state.storage_backend == STORAGE_BACKEND_HDLOADER)
                  ? 0
                  : g_active_done;
        ui_error("Download failed (rc=%d)", rc);
    }
    downloads_save(&g_downloads);

    g_active_total = 0;
    redraw();   /* surface the result line so the user can read it */
}

/* ---- Main loop ---- */

static void cycle_view(int delta) {
    g_hdd_format_confirm = false;
    int n = (int)APP_VIEW_COUNT;
    g_view = (AppView)((((int)g_view + delta) % n + n) % n);
}

static void cycle_storage_pref(int delta) {
    g_hdd_format_confirm = false;
    int next = (int)g_state.storage_pref + delta;
    if (next < (int)STORAGE_PREF_AUTO) next = (int)STORAGE_PREF_HDD;
    if (next > (int)STORAGE_PREF_HDD) next = (int)STORAGE_PREF_AUTO;
    g_state.storage_pref = (StoragePreference)next;

    if (config_save(&g_state)) {
        ui_status("storage=%s saved; relaunch to apply",
                  config_storage_pref_to_str(g_state.storage_pref));
    } else {
        ui_error("Could not save storage setting");
    }
}

static void redraw(void) {
    ui_clear();
    ui_draw_header(&g_state, g_view);
    switch (g_view) {
        case APP_VIEW_ROMS:
            ui_draw_roms(&g_catalog, g_rom_selected, g_rom_scroll);
            break;
        case APP_VIEW_LOCAL:
            ui_draw_local(&g_local, g_local_selected, g_local_scroll);
            break;
        case APP_VIEW_DOWNLOADS:
            ui_draw_downloads(&g_downloads, g_dl_selected, g_dl_scroll,
                              g_active_done, g_active_total, g_active_bps);
            break;
        case APP_VIEW_SAVES:
            ui_draw_saves(&g_saves, g_saves_selected, g_saves_scroll);
            break;
        case APP_VIEW_MCARD:
            ui_draw_mcard(&g_mcard, g_mcard_selected, g_mcard_scroll);
            break;
        case APP_VIEW_MCARD2:
            ui_draw_mcard(&g_mcard2, g_mcard2_selected, g_mcard2_scroll);
            break;
        case APP_VIEW_SERVER:
            ui_draw_server(&g_server, g_server_selected, g_server_scroll);
            break;
        case APP_VIEW_CONFIG:
            ui_draw_config(&g_state);
            break;
        default: break;
    }
}

int main(int argc, char *argv[]) {
    (void)argc; (void)argv;

    /* Phase 1: bring up the libdebug scrolling console so every boot
     * step (IRX load, mc init, network bring-up) leaves a visible
     * line.  gsKit isn't initialised yet — that happens after boot
     * completes, in the ui_init() call below. */
    ui_boot_init();
    scr_printf("ps2sync v%s booting...\n", APP_VERSION);

    boot_iop_modules();
    scr_printf("BOOT: IRX done — pausing 3 s so you can read the log\n");
    DelayThread(3000000);

    scr_printf("BOOT: loading config (mcInit...)\n");
    char err[256];
    if (!config_load(&g_state, err, sizeof(err))) {
        ui_draw_message("Config", err);
        scr_printf("\nPress CIRCLE to exit.\n");
        pad_init();
        for (;;) {
            unsigned int p = pad_read_pressed();
            if (p & PAD_CIRCLE) break;
        }
        return 1;
    }
    config_load_console_id(&g_state);
    ui_set_mmce(0, g_state.mmce_mode[0]);   /* reflect persisted GameID modes */
    ui_set_mmce(1, g_state.mmce_mode[1]);
    scr_printf("BOOT: console_id=%s server=%s\n",
               g_state.console_id, g_state.server_url);

    boot_device_modules(&g_state);

    scr_printf("BOOT: bringing up network\n");
    network_init(&g_state);
    scr_printf("BOOT: net ready=%d dhcp=%d ip=%s\n",
               g_state.net_ready, g_state.dhcp_ok, g_state.ip);

    init_storage_targets();

    scr_printf("BOOT: pad init\n");
    pad_init();

    scr_printf("BOOT: ready - switching to gsKit UI\n");
    /* Pause briefly so user sees boot log before gsKit overwrites it. */
    DelayThread(2000000);

    /* Phase 2: hand the GS to gsKit. */
    ui_init();

    /* Auto-populate the views so the user lands on a usable list
     * instead of "Press X to fetch catalog" / an empty Local view. */
    if (g_state.usb_ready) {
        scan_local();
        scan_saves();
    }
    scan_mcard_list(0, &g_mcard);
    scan_mcard_list(1, &g_mcard2);
    if (network_is_ready(&g_state)) {
        fetch_catalog();
        fetch_server_saves();
    }

    redraw();
    ui_flush();

    for (;;) {
        unsigned int pressed = pad_read_pressed();

        /* Event-driven redraw: libdebug renders synchronously into the
         * GS frame buffer with no double buffer, so repainting on every
         * frame produces visible flicker (~140 KB of char DMA per
         * frame).  Only redraw when state actually changed. */
        if (pressed == 0) {
            DelayThread(16000);
            continue;
        }

        bool dirty = true;

        if (pressed & PAD_CIRCLE) break;

        if (pressed & PAD_R2) {
            cycle_view(+1);
        } else if (pressed & PAD_L2) {
            cycle_view(-1);
        } else if (g_view == APP_VIEW_ROMS) {
            int count = g_catalog.count;
            if      (pressed & PAD_UP)       g_rom_selected--;
            else if (pressed & PAD_DOWN)     g_rom_selected++;
            else if (pressed & PAD_LEFT)     g_rom_selected -= ui_list_visible();
            else if (pressed & PAD_RIGHT)    g_rom_selected += ui_list_visible();
            else if (pressed & PAD_CROSS)    fetch_catalog();
            else if (pressed & PAD_SQUARE) {
                /* Queue selected ROM for download. */
                if (require_storage_ready() &&
                    count > 0 && g_rom_selected < count)
                {
                    DownloadEntry *e =
                        queue_catalog_entry(&g_catalog.items[g_rom_selected]);
                    if (e) {
                        downloads_save(&g_downloads);
                        ui_status("Queued: %s", e->name);
                    } else {
                        ui_error("Download list full");
                    }
                }
            } else if (pressed & PAD_TRIANGLE) {
                /* Trigger active download for the selected entry. */
                if (require_storage_ready() &&
                    count > 0 && g_rom_selected < count)
                {
                    DownloadEntry *e =
                        queue_catalog_entry(&g_catalog.items[g_rom_selected]);
                    if (e) run_active_download(e);
                }
            }
            clamp_scroll(&g_rom_selected, &g_rom_scroll, count);
        } else if (g_view == APP_VIEW_LOCAL) {
            int count = g_local.count;
            if      (pressed & PAD_UP)       g_local_selected--;
            else if (pressed & PAD_DOWN)     g_local_selected++;
            else if (pressed & PAD_LEFT)     g_local_selected -= ui_list_visible();
            else if (pressed & PAD_RIGHT)    g_local_selected += ui_list_visible();
            else if (pressed & PAD_CROSS)    scan_local();
            else if (pressed & PAD_SQUARE) {
                /* Delete the selected installed game. */
                if (count > 0 && g_local_selected < count) {
                    const LocalRom *r = &g_local.items[g_local_selected];
                    int del_rc = (g_state.storage_backend == STORAGE_BACKEND_HDLOADER)
                               ? hdl_remove_partition(r->path)
                               : unlink(r->path);
                    if (del_rc == 0) {
                        ui_status("Deleted: %s", r->filename);
                        scan_local();
                    } else {
                        ui_error("Delete failed: %s", r->filename);
                    }
                }
            }
            clamp_scroll(&g_local_selected, &g_local_scroll, count);
        } else if (g_view == APP_VIEW_DOWNLOADS) {
            int count = g_downloads.count;
            if      (pressed & PAD_UP)       g_dl_selected--;
            else if (pressed & PAD_DOWN)     g_dl_selected++;
            else if (pressed & PAD_CROSS) {
                if (count > 0 && g_dl_selected < count) {
                    run_active_download(&g_downloads.items[g_dl_selected]);
                }
            } else if (pressed & PAD_SQUARE) {
                if (require_storage_ready() &&
                    count > 0 && g_dl_selected < count)
                {
                    downloads_remove(&g_downloads,
                                     g_downloads.items[g_dl_selected].rom_id);
                    downloads_save(&g_downloads);
                    count = g_downloads.count;
                }
            }
            clamp_scroll(&g_dl_selected, &g_dl_scroll, count);
        } else if (g_view == APP_VIEW_SAVES) {
            int count = g_saves.count;
            if      (pressed & PAD_UP)       g_saves_selected--;
            else if (pressed & PAD_DOWN)     g_saves_selected++;
            else if (pressed & PAD_LEFT)     g_saves_selected -= ui_list_visible();
            else if (pressed & PAD_RIGHT)    g_saves_selected += ui_list_visible();
            else if (pressed & PAD_SQUARE)   scan_saves();
            else if (pressed & PAD_CROSS)    upload_selected_save();
            else if (pressed & PAD_TRIANGLE) pull_all_saves();
            clamp_scroll(&g_saves_selected, &g_saves_scroll, g_saves.count);
        } else if (g_view == APP_VIEW_MCARD) {
            if      (pressed & PAD_UP)       g_mcard_selected--;
            else if (pressed & PAD_DOWN)     g_mcard_selected++;
            else if (pressed & PAD_LEFT)     g_mcard_selected -= ui_list_visible();
            else if (pressed & PAD_RIGHT)    g_mcard_selected += ui_list_visible();
            else if (pressed & PAD_SQUARE)   scan_mcard_list(0, &g_mcard);
            else if (pressed & PAD_CROSS)    upload_mc_game_at(&g_mcard, g_mcard_selected);
            else if (pressed & PAD_TRIANGLE) restore_mc_game_at(&g_mcard, g_mcard_selected);
            else if (pressed & PAD_SELECT)   cycle_mmce_mode(0);
            else if (pressed & PAD_R1) {
                if (g_mcard.count > 0 && g_mcard_selected < g_mcard.count)
                    mcp_switch_gameid(g_mcard.items[g_mcard_selected].serial, 0);
            }
            clamp_scroll(&g_mcard_selected, &g_mcard_scroll, g_mcard.count);
        } else if (g_view == APP_VIEW_MCARD2) {
            if      (pressed & PAD_UP)       g_mcard2_selected--;
            else if (pressed & PAD_DOWN)     g_mcard2_selected++;
            else if (pressed & PAD_LEFT)     g_mcard2_selected -= ui_list_visible();
            else if (pressed & PAD_RIGHT)    g_mcard2_selected += ui_list_visible();
            else if (pressed & PAD_SQUARE)   scan_mcard_list(1, &g_mcard2);
            else if (pressed & PAD_CROSS)    upload_mc_game_at(&g_mcard2, g_mcard2_selected);
            else if (pressed & PAD_TRIANGLE) restore_mc_game_at(&g_mcard2, g_mcard2_selected);
            else if (pressed & PAD_SELECT)   cycle_mmce_mode(1);
            else if (pressed & PAD_R1) {
                if (g_mcard2.count > 0 && g_mcard2_selected < g_mcard2.count)
                    mcp_switch_gameid(g_mcard2.items[g_mcard2_selected].serial, 1);
            }
            clamp_scroll(&g_mcard2_selected, &g_mcard2_scroll, g_mcard2.count);
        } else if (g_view == APP_VIEW_SERVER) {
            if      (pressed & PAD_UP)       g_server_selected--;
            else if (pressed & PAD_DOWN)     g_server_selected++;
            else if (pressed & PAD_LEFT)     g_server_selected -= ui_list_visible();
            else if (pressed & PAD_RIGHT)    g_server_selected += ui_list_visible();
            else if (pressed & PAD_START)    cycle_server_source();
            else if (pressed & PAD_SQUARE)   fetch_server_saves();
            else if (pressed & PAD_CROSS)    server_download_to_source();
            else if (pressed & PAD_TRIANGLE) server_upload_from_source();
            else if (pressed & PAD_L1)       server_sync_all();
            else if (pressed & PAD_SELECT)   cycle_mmce_mode(source_port() < 0 ? 0 : source_port());
            else if (pressed & PAD_R1) {
                if (g_server.count > 0 && g_server_selected < g_server.count)
                    mcp_switch_gameid(g_server.items[g_server_selected].serial,
                                      source_port() < 0 ? 0 : source_port());
            }
            clamp_scroll(&g_server_selected, &g_server_scroll, g_server.count);
        } else if (g_view == APP_VIEW_CONFIG) {
            if (pressed & PAD_LEFT) {
                cycle_storage_pref(-1);
            } else if (pressed & PAD_RIGHT) {
                cycle_storage_pref(1);
            } else if (pressed & PAD_TRIANGLE) {
                if (!g_hdd_format_confirm) {
                    g_hdd_format_confirm = true;
                    ui_error("TRIANGLE again wipes ALL HDD data (APA)");
                } else {
                    g_hdd_format_confirm = false;
                    format_internal_hdd();
                }
            } else {
                g_hdd_format_confirm = false;
            }
        }

        if (dirty) redraw();
    }

    network_shutdown();
    return 0;
}
