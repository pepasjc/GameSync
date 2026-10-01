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
 *   3. Boot splash (gsKit) with a live log of every step
 *   4. Load config from mc0:/3DSSYNC/CONFIG.TXT
 *   5. Bring up networking via ps2ip (static IP by default, DHCP optional)
 *   6. Probe storage: APA/HDLoader hdd0: or mass:/ folder installs
 *   7. Run the menu loop (ROMs / Downloads / Config views)
 *
 * Views: ROM catalog / installed games / download queue / VMC images /
 * memory card (SELECT: slot 1 / 2) / server saves (SELECT: sync source) /
 * settings.  L1 / R1 cycle the views.
 *
 * Controls are the GameSync scheme shared by every client:
 *   D-pad Up/Down     move one row          Left/Right  page up / down
 *   L1 / R1           previous / next view  SELECT      cycle the sub-tab
 *   CROSS             primary action (or the row's action menu)
 *   CIRCLE            cancel / back; pauses a running download
 *   SQUARE            secondary action (Sync all / Queue / Rescan)
 *   TRIANGLE          details of the selected row
 *   START             exit (asks first)
 */

#include "common.h"
#include "catcache.h"
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
        ui_log("  IRX %s failed (id=%d ret=%d)\n", label, id, ret);
        return id;
    }
    if (ret != 0 && ret != 1) {
        ui_log("  IRX %s start error (id=%d ret=%d)\n", label, id, ret);
        return -1;
    }
    ui_log("  IRX %s ok (id=%d)\n", label, id);
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

    ui_log("Loading IOP modules...\n");

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
    ui_log("  rom0:SIO2MAN -> %d\n", ret);
    ret = SifLoadModule("rom0:MCMAN",   0, NULL);
    ui_log("  rom0:MCMAN   -> %d\n", ret);
    ret = SifLoadModule("rom0:MCSERV",  0, NULL);
    ui_log("  rom0:MCSERV  -> %d\n", ret);
    ret = SifLoadModule("rom0:PADMAN",  0, NULL);
    ui_log("  rom0:PADMAN  -> %d\n", ret);

    /* mmceman: MMCE protocol for MemCard Pro 2 / SD2PSX GameID switching.
     * Needs iomanX + fileXio (loaded above) and SIO2MAN (rom0, above). */
    load_irx(mmceman_irx, mmceman_irx_size, "mmceman", 0, NULL);
}

static void boot_device_modules(const SyncState *state) {
    bool want_usb = !state || state->storage_pref != STORAGE_PREF_HDD;
    bool want_hdd = !state || state->storage_pref != STORAGE_PREF_USB;

    ui_log("Loading device modules (storage=%s)...\n",
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
        ui_log("  bdm stack failed - falling back to usbhdfsd\n");
        load_irx(usbhdfsd_irx, usbhdfsd_irx_size, "usbhdfsd", 0, NULL);
    }
    if (want_hdd && ata_rc != 0) {
        ui_log("  internal HDD BDM bridge unavailable (rc=%d)\n", ata_rc);
    }

    load_irx(netman_irx,      netman_irx_size,      "netman",      0, NULL);
    load_irx(smap_irx,        smap_irx_size,        "smap",        0, NULL);

    /* Bind the EE-side fileXio stub.  Without this, every newlib stdio
     * call against an iomanX-registered device (mass:, hdd:, etc.)
     * silently fails — the RPC descriptor is uninitialised. */
    int fxr = fileXioInit();
    ui_log("  fileXioInit -> %d\n", fxr);
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
                ui_log("  storage: stat ok at %s\n", data_dir);
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
                ui_log("  storage: mkdir ok at %s\n", data_dir);
                strncpy(state->usb_root, roots[r], sizeof(state->usb_root) - 1);
                state->usb_root[sizeof(state->usb_root) - 1] = '\0';
                state->usb_ready = true;
                roms_set_storage_root(state->usb_root);
                return true;
            }

            errno = 0;
            if (stat(root_dir, &st) == 0) {
                ui_log("  storage: root stat ok at %s - using anyway\n", root_dir);
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
    ui_log("  storage: wait_for_mass timed out after %ds\n", timeout_seconds);
    for (int r = 0; roots[r]; r++) {
        ui_log("    %-7s last errno=%d\n", roots[r], last_errno[r]);
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
static uint32_t     g_repeat_at = 0;

/* Held D-pad directions repeat: first after REPEAT_DELAY_MS, then every
 * REPEAT_RATE_MS. */
#define REPEAT_MASK      (PAD_UP | PAD_DOWN | PAD_LEFT | PAD_RIGHT)
#define REPEAT_DELAY_MS  380
#define REPEAT_RATE_MS   70

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

    uint32_t now = ui_ms();
    unsigned int held_dir = btns & REPEAT_MASK;
    if (pressed & REPEAT_MASK) {
        g_repeat_at = now + REPEAT_DELAY_MS;
    } else if (held_dir && (int32_t)(now - g_repeat_at) >= 0) {
        pressed |= held_dir;
        g_repeat_at = now + REPEAT_RATE_MS;
    }
    return pressed;
}

static void draw_screen(void);

/* Modal yes/no prompt for read/write/sync operations.  Blocks until the
 * user presses CROSS (confirm) or CIRCLE (cancel). */
static bool confirm(const char *fmt, ...) {
    char msg[200];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(msg, sizeof(msg), fmt, ap);
    va_end(ap);

    draw_screen();
    ui_draw_confirm("Confirm", msg);
    ui_flush();

    for (;;) {
        unsigned int p = pad_read_pressed();
        if (p & PAD_CROSS)  return true;
        if (p & PAD_CIRCLE) return false;
        DelayThread(16000);
    }
}

/* Modal action menu.  Returns the chosen index, or -1 on CIRCLE. */
static int choose(const char *title, const char *const *items, int count) {
    int sel = 0;
    for (;;) {
        draw_screen();
        ui_draw_menu(title, items, count, sel);
        ui_flush();
        unsigned int p;
        while ((p = pad_read_pressed()) == 0) DelayThread(16000);
        if (p & PAD_CIRCLE) return -1;
        if (p & PAD_CROSS)  return sel;
        if (p & PAD_UP)     sel = (sel + count - 1) % count;
        if (p & PAD_DOWN)   sel = (sel + 1) % count;
    }
}

/* Modal details card; any of CIRCLE / CROSS / TRIANGLE closes it. */
static void show_info(const char *title, const char *fmt, ...)
    __attribute__((format(printf, 2, 3)));
static void show_info(const char *title, const char *fmt, ...) {
    char msg[512];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(msg, sizeof(msg), fmt, ap);
    va_end(ap);

    draw_screen();
    ui_draw_info(title, msg);
    ui_flush();
    for (;;) {
        unsigned int p = pad_read_pressed();
        if (p & (PAD_CIRCLE | PAD_CROSS | PAD_TRIANGLE)) return;
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
static int           g_mc_slot = 0; /* Memory Card view sub-tab: 0 slot 1, 1 slot 2 */
static AppView       g_view = APP_VIEW_ROMS;
static int           g_cfg_row = CFG_ROW_STORAGE;
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
    ui_log("BOOT: probing APA/HDLoader hdd0:...\n");
    if (!ensure_hdd_format_modules()) return false;

    if (hddCheckPresent() != 0) {
        ui_log("BOOT: no internal HDD present\n");
        return false;
    }

    if (hddCheckFormatted() != 0) {
        ui_log("BOOT: internal HDD is not APA-formatted\n");
        ui_status("HDD needs APA format: Settings > Format internal HDD");
        return false;
    }

    g_state.storage_backend = STORAGE_BACKEND_HDLOADER;
    g_state.usb_ready = true;     /* legacy readiness flag used by UI/actions */
    strncpy(g_state.usb_root, "hdd0:hdl", sizeof(g_state.usb_root) - 1);
    g_state.usb_root[sizeof(g_state.usb_root) - 1] = '\0';

    roms_set_storage_root("hdd0:");
    roms_set_downloads_file(HDL_DOWNLOADS_FILE);
    downloads_load(&g_downloads);
    ui_log("BOOT: APA/HDLoader storage ready at hdd0:\n");
    return true;
}

static void init_storage_targets(void) {
    g_state.storage_backend = STORAGE_BACKEND_NONE;
    g_state.usb_ready = false;
    g_downloads.count = 0;

    ui_log("BOOT: probing storage (%s)...\n",
           config_storage_pref_to_str(g_state.storage_pref));

    if (g_state.storage_pref != STORAGE_PREF_USB) {
        if (init_hdloader_targets()) return;
        if (g_state.storage_pref == STORAGE_PREF_HDD) {
            ui_log("WARN: APA/HDLoader storage unavailable\n");
            ui_status("HDD not ready; format APA from Settings");
            return;
        }
    }

    if (g_state.storage_pref != STORAGE_PREF_HDD) {
        if (!wait_for_mass(&g_state, 12)) {
            ui_log("WARN: no storage root mounted\n");
            ui_status("Storage not ready; downloads disabled");
            return;
        }

        g_state.storage_backend = STORAGE_BACKEND_MASS;
        ui_log("BOOT: mass storage ready at %s\n", g_state.usb_root);
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
    ui_log("  fileXioInit (hdd formatter) -> %d\n", rc);
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
static bool              g_transfer_active = false;
static char              g_active_name[160];
static uint32_t          g_active_start_ms = 0;
static uint32_t          g_last_draw_ms = 0;
static time_t            g_last_speed_t = 0;
static uint64_t          g_last_speed_bytes = 0;

/* Progress redraws are capped at 4 per second and skip the vblank wait,
 * so the HTTP receive loop never stalls on the display. */
#define PROGRESS_REDRAW_MS 250

static int progress_cb(uint64_t done, uint64_t total) {
    g_active_done  = done;
    g_active_total = total;

    time_t now = time(NULL);
    if (now != g_last_speed_t) {
        g_active_bps = done > g_last_speed_bytes
                     ? (done - g_last_speed_bytes) / (uint64_t)(now - g_last_speed_t + 1)
                     : 0;
        g_last_speed_t     = now;
        g_last_speed_bytes = done;
    }

    uint32_t ms = ui_ms();
    if (ms - g_last_draw_ms >= PROGRESS_REDRAW_MS) {
        g_last_draw_ms = ms;
        /* CIRCLE held at a refresh pauses (USB downloads resume later;
         * HDLoader installs restart from zero). */
        unsigned int p = pad_read_pressed();
        if ((p | g_prev_btns) & PAD_CIRCLE) g_pause_requested = true;
        draw_screen();
        ui_flush_nowait();
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

/* ---- Catalog (fingerprint-cached, see catcache.h) ----
 *
 * Where the cache lives:
 *   mass storage (USB / BDM HDD)  <root>/3dssync/catalog.dat — next to the
 *                                 download queue, no size limit
 *   otherwise (APA/HDLoader, or no storage)
 *                                 mc0:/3DSSYNC/CATALOG.DAT, but only while
 *                                 it is <= CATCACHE_MC_MAX_BYTES; a bigger
 *                                 catalog lives in RAM for the session.
 * The PS2 client has no catalog search or RetroAchievements filter, so
 * there is no server-side query to preserve: the rows are cached whole. */

#define CATALOG_SYSTEM "PS2"

static char g_cache_path[96];   /* shown in Settings; stdio path when !on_mc */
static bool g_cache_on_mc;

static void resolve_cache_path(void) {
    if (g_state.storage_backend == STORAGE_BACKEND_MASS && g_state.usb_ready) {
        snprintf(g_cache_path, sizeof(g_cache_path), "%s%s%s",
                 g_state.usb_root, STORAGE_DATA_SUBDIR, CATALOG_CACHE_LEAF);
        g_cache_on_mc = false;
    } else {
        snprintf(g_cache_path, sizeof(g_cache_path), "%s", CATALOG_CACHE_MC);
        g_cache_on_mc = true;
    }
    ui_set_catalog_info(NULL, g_cache_path);
}

/* The three cache operations, on mass storage (stdio) or the memory card
 * (libmc via config.c, staged in g_scratch — free outside a fetch). */
static bool cache_read(char *fp, size_t fp_size) {
    if (!g_cache_on_mc)
        return catcache_load(g_cache_path, CATALOG_SYSTEM, fp, fp_size, &g_catalog);
    int n = config_mc_read_file(CATALOG_CACHE_MC_REL, g_scratch, sizeof(g_scratch));
    if (n <= 0) { g_catalog.count = 0; return false; }
    return catcache_decode(g_scratch, (size_t)n, CATALOG_SYSTEM, fp, fp_size, &g_catalog);
}

static void cache_remove(void) {
    if (g_cache_on_mc) config_mc_delete_file(CATALOG_CACHE_MC_REL);
    else               catcache_remove(g_cache_path);
}

/* Keep `fingerprint`'s rows (already in g_catalog) for the next start. */
static void store_catalog_cache(const char *fingerprint) {
    bool ok;
    if (g_cache_on_mc) {
        size_t need = catcache_encoded_size(CATALOG_SYSTEM, fingerprint, &g_catalog);
        if (need > CATCACHE_MC_MAX_BYTES || need > sizeof(g_scratch)) {
            /* Too big for an 8 MB memory card: RAM for this session only. */
            cache_remove();
            ui_set_catalog_info(NULL, "RAM only (too big for mc0:)");
            return;
        }
        size_t n = catcache_encode(g_scratch, sizeof(g_scratch), CATALOG_SYSTEM,
                                   fingerprint, &g_catalog);
        ok = n > 0 && config_mc_write_file(CATALOG_CACHE_MC_REL, g_scratch, n);
    } else {
        ok = catcache_save(g_cache_path, CATALOG_SYSTEM, fingerprint, &g_catalog);
    }
    ui_set_catalog_info(NULL, ok ? g_cache_path : "RAM only (write failed)");
}

/* Server unreachable: keep this session's rows, else the cached copy. */
static void use_cached_catalog(const char *why) {
    char fp[80];
    if (g_catalog.count > 0) {
        ui_set_catalog_info("Offline", NULL);
        ui_status("%s - catalog from this session", why);
        return;
    }
    if (cache_read(fp, sizeof(fp))) {
        ui_set_catalog_info("Offline", NULL);
        ui_status("%s - cached catalog (%d games)", why, g_catalog.count);
        return;
    }
    snprintf(g_catalog.last_error, sizeof(g_catalog.last_error), "%s", why);
    ui_error("%s", why);
}

static bool fetch_catalog_rows(void) {
    ui_status("Fetching PS2 catalog...");
    redraw();   /* show "Fetching..." before HTTP blocks the loop */
    return roms_fetch_catalog(&g_state, CATALOG_SYSTEM,
                              g_scratch, sizeof(g_scratch), &g_catalog);
}

/* Load the catalog: cached copy when its fingerprint still matches the
 * server's, otherwise a fresh fetch.  force = Settings > Refresh catalog:
 * ask the server to rescan, drop the cache, refetch everything. */
static void load_catalog(bool force) {
    char rescan_note[48] = "";
    ui_set_catalog_info("", NULL);

    if (force) {
        if (!network_is_ready(&g_state)) {
            ui_error("Network not ready - catalog not refreshed");
            redraw();
            return;
        }
        ui_status("Asking the server to rescan its ROMs...");
        redraw();
        int st = 0;
        int n = network_get(&g_state, "/api/v1/roms/scan", g_scratch, sizeof(g_scratch), &st);
        if (st == 403 || st == 404 || st == 405)
            snprintf(rescan_note, sizeof(rescan_note), "; server rescan not allowed");
        else if (n < 0 || st != 200)
            snprintf(rescan_note, sizeof(rescan_note), "; server rescan failed (%d)", st);
        cache_remove();
        g_catalog.count = 0;
    }

    if (!network_is_ready(&g_state)) {
        char why[64];
        snprintf(why, sizeof(why), "Network not ready (ip=%s)", g_state.ip);
        use_cached_catalog(why);
        redraw();
        return;
    }

    ui_status("Checking the catalog...");
    redraw();
    char fingerprint[80] = "";
    int st = 0;
    int n = network_get(&g_state, "/api/v1/roms/fingerprints", g_scratch, sizeof(g_scratch), &st);
    int found = -1;
    if (n >= 0 && st == 200)
        found = catcache_parse_fingerprint(g_scratch, (size_t)n, CATALOG_SYSTEM,
                                           fingerprint, sizeof(fingerprint), NULL);

    if (n < 0 || st == 0) {
        use_cached_catalog("Server unreachable");
        redraw();
        return;
    }

    if (st == 404 || st == 405 || (st == 200 && found < 0)) {
        /* Server predates /roms/fingerprints: fetch whole, cache nothing. */
        if (fetch_catalog_rows())
            ui_status("Catalog: %d games%s", g_catalog.count, rescan_note);
        else
            ui_error("%s", g_catalog.last_error);
        redraw();
        return;
    }

    if (st != 200) {
        char why[64];
        snprintf(why, sizeof(why), "Catalog check failed (HTTP %d)", st);
        use_cached_catalog(why);
        redraw();
        return;
    }

    if (found == 0) {
        /* The server lists no PS2 games at all. */
        cache_remove();
        g_catalog.count = 0;
        snprintf(g_catalog.last_error, sizeof(g_catalog.last_error),
                 "No PS2 games on the server");
        ui_status("Catalog: no PS2 games on the server%s", rescan_note);
        redraw();
        return;
    }

    char cached_fp[80];
    if (!force &&
        cache_read(cached_fp, sizeof(cached_fp)) &&
        strcmp(cached_fp, fingerprint) == 0) {
        ui_status("Catalog: %d games (unchanged)", g_catalog.count);
        redraw();
        return;
    }

    if (fetch_catalog_rows()) {
        store_catalog_cache(fingerprint);
        ui_status("Catalog: %d games (%s)%s", g_catalog.count,
                  force ? "refreshed" : "updated", rescan_note);
    } else {
        char why[128];
        snprintf(why, sizeof(why), "%s", g_catalog.last_error);
        use_cached_catalog(why);
    }
    redraw();
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
    return g_server_source == 1 ? "Slot 1" : g_server_source == 2 ? "Slot 2" : "VMC";
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

    ui_status("Reading %s on %s...", serial, source_name());
    redraw();

    McGameList *list = source_list();
    saves_scan_mcard(port, list);
    int idx = -1;
    for (int i = 0; i < list->count; i++)
        if (strcmp(list->items[i].serial, serial) == 0) { idx = i; break; }
    if (idx < 0) {
        ui_error("%s not on %s (Cross > Switch MemCard Pro first)", serial, source_name());
        return;
    }
    char msg[128];
    int rc = upload_one_game(list, &list->items[idx], msg, sizeof(msg));
    if (rc < 0) ui_error("%s", msg); else ui_status("%s", msg);
    mark_server_local();
    redraw();
}

/* Upload every save on the card in `port` to the server. */
static void upload_card_all(int port) {
    if (!network_is_ready(&g_state)) { ui_error("Network not ready"); return; }
    port = port == 1 ? 1 : 0;
    char name[8];
    snprintf(name, sizeof(name), "Slot %d", port + 1);

    if (!confirm("Upload ALL saves on %s\nto the server?", name)) return;
    ui_status("Scanning %s...", name);
    redraw();

    McGameList *list = port == 1 ? &g_mcard2 : &g_mcard;
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
    ui_status("%s sync: %d uploaded, %d failed", name, ok, fail);
    redraw();
}

/* SQUARE on the Server view: sync every save of the current source. */
static void server_sync_all(void) {
    if (!network_is_ready(&g_state)) { ui_error("Network not ready"); return; }
    int port = source_port();
    if (port < 0) pull_all_saves();     /* VMC: pull all server saves into VMC/ */
    else          upload_card_all(port);
}

static void cycle_server_source(void) {
    g_server_source = (g_server_source + 1) % 3;
    ui_set_server_source(g_server_source);
    ui_status("Sync source: %s", source_name());
}

static void cycle_mmce_mode(int port, int delta) {
    port = port == 1 ? 1 : 0;
    g_state.mmce_mode[port] = (g_state.mmce_mode[port] + 4 + delta) % 4;
    ui_set_mmce(port, g_state.mmce_mode[port]);
    config_save(&g_state);   /* persist the per-slot choice */
    ui_status("Slot %d GameID: %s (saved)", port + 1,
              g_mmce_mode_names[g_state.mmce_mode[port]]);
}

static void mcp_switch_gameid(const char *serial, int port) {
    port = port == 1 ? 1 : 0;
    if (g_state.mmce_mode[port] == 0) {
        ui_error("Slot %d GameID is off - set the device in Settings", port + 1);
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
     * libmc (gen1 freeze).  Tell the user to rescan once it has switched. */
    ui_status("[%s] %s - rescan from the Cross menu", g_mmce_mode_names[mode], msg);
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
    g_active_bps        = 0;
    g_pause_requested   = false;
    g_last_speed_t      = time(NULL);
    g_last_speed_bytes  = e->offset;
    g_active_start_ms   = ui_ms();
    g_last_draw_ms      = 0;
    snprintf(g_active_name, sizeof(g_active_name), "%s",
             e->name[0] ? e->name : e->filename);
    g_transfer_active   = true;

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
    g_transfer_active = false;
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
    int n = (int)APP_VIEW_COUNT;
    g_view = (AppView)((((int)g_view + delta) % n + n) % n);
}

static void cycle_storage_pref(int delta) {
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

static void fmt_size(uint64_t bytes, char *out, size_t out_size) {
    if (bytes >= 1024ULL * 1024ULL * 1024ULL)
        snprintf(out, out_size, "%llu.%llu GB",
                 (unsigned long long)(bytes >> 30),
                 (unsigned long long)(((bytes * 10) >> 30) % 10));
    else if (bytes >= 1024ULL * 1024ULL)
        snprintf(out, out_size, "%llu MB", (unsigned long long)(bytes >> 20));
    else
        snprintf(out, out_size, "%llu KB", (unsigned long long)((bytes + 1023) >> 10));
}

/* D-pad: Up/Down one row, Left/Right one page.  Returns true if handled. */
static bool move_selection(unsigned int pressed, int *selected) {
    if      (pressed & PAD_UP)    (*selected)--;
    else if (pressed & PAD_DOWN)  (*selected)++;
    else if (pressed & PAD_LEFT)  *selected -= ui_list_visible();
    else if (pressed & PAD_RIGHT) *selected += ui_list_visible();
    else return false;
    return true;
}

/* -- Catalog -- */

static void handle_roms(unsigned int pressed) {
    int count = g_catalog.count;
    if (move_selection(pressed, &g_rom_selected)) {
        /* moved */
    } else if (count > 0 && g_rom_selected < count) {
        const RomEntry *rom = &g_catalog.items[g_rom_selected];
        if (pressed & PAD_CROSS) {
            /* Install now. */
            if (require_storage_ready()) {
                DownloadEntry *e = queue_catalog_entry(rom);
                if (e) run_active_download(e);
                else   ui_error("Download list full");
            }
        } else if (pressed & PAD_SQUARE) {
            /* Add to the queue. */
            if (require_storage_ready()) {
                DownloadEntry *e = queue_catalog_entry(rom);
                if (e) {
                    downloads_save(&g_downloads);
                    ui_status("Queued: %s", e->name);
                } else {
                    ui_error("Download list full");
                }
            }
        } else if (pressed & PAD_TRIANGLE) {
            char size[24], target[260];
            fmt_size(rom->size, size, sizeof(size));
            if (g_state.storage_backend == STORAGE_BACKEND_HDLOADER)
                hdl_resolve_target_path_from_rom(rom, target, sizeof(target));
            else if (!roms_resolve_target_path(rom, target, sizeof(target)))
                snprintf(target, sizeof(target), "-");
            show_info("Game details",
                      "%s\nSerial: %s\nFile: %s\nSize: %s (%s)\nFormat: %s\nInstalls to: %s",
                      rom->name[0] ? rom->name : rom->filename,
                      rom->serial[0] ? rom->serial : "unknown", rom->filename, size,
                      rom->is_cd ? "CD" : "DVD",
                      rom->extract_format[0] ? rom->extract_format : "iso", target);
        }
    }
    clamp_scroll(&g_rom_selected, &g_rom_scroll, g_catalog.count);
}

/* -- Installed games -- */

static void handle_local(unsigned int pressed) {
    int count = g_local.count;
    if (move_selection(pressed, &g_local_selected)) {
        /* moved */
    } else if (pressed & PAD_SQUARE) {
        scan_local();
    } else if (count > 0 && g_local_selected < count) {
        const LocalRom *r = &g_local.items[g_local_selected];
        if (pressed & PAD_CROSS) {
            if (confirm("Delete %s\nfrom this console?", r->name[0] ? r->name : r->filename)) {
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
        } else if (pressed & PAD_TRIANGLE) {
            char size[24];
            fmt_size(r->size, size, sizeof(size));
            show_info("Installed game", "%s\nSerial: %s\nSize: %s (%s)\nLocation: %s",
                      r->name[0] ? r->name : r->filename, r->serial, size,
                      r->is_cd ? "CD" : "DVD", r->path);
        }
    }
    clamp_scroll(&g_local_selected, &g_local_scroll, g_local.count);
}

/* -- Downloads -- */

static void handle_downloads(unsigned int pressed) {
    int count = g_downloads.count;
    if (move_selection(pressed, &g_dl_selected)) {
        /* moved */
    } else if (count > 0 && g_dl_selected < count) {
        DownloadEntry *e = &g_downloads.items[g_dl_selected];
        if (pressed & PAD_CROSS) {
            run_active_download(e);
        } else if (pressed & PAD_SQUARE) {
            if (require_storage_ready()) {
                downloads_remove(&g_downloads, e->rom_id);
                downloads_save(&g_downloads);
            }
        } else if (pressed & PAD_TRIANGLE) {
            char done[24], total[24];
            fmt_size(e->offset, done, sizeof(done));
            fmt_size(e->total, total, sizeof(total));
            show_info("Download", "%s\nSerial: %s\nProgress: %s / %s\nTarget: %s",
                      e->name[0] ? e->name : e->filename, e->serial, done, total,
                      e->target_path);
        }
    }
    clamp_scroll(&g_dl_selected, &g_dl_scroll, g_downloads.count);
}

/* -- Virtual memory cards -- */

static void handle_saves(unsigned int pressed) {
    int count = g_saves.count;
    bool have = count > 0 && g_saves_selected < count;
    if (move_selection(pressed, &g_saves_selected)) {
        /* moved */
    } else if (pressed & PAD_CROSS) {
        static const char *const with_card[] = { "Upload card to server", "Rescan VMC folder" };
        static const char *const no_card[]   = { "Rescan VMC folder" };
        int pick = have ? choose(g_saves.items[g_saves_selected].filename, with_card, 2)
                        : choose("Card images", no_card, 1);
        if (!have && pick == 0) pick = 1;
        if (pick == 0)      upload_selected_save();
        else if (pick == 1) scan_saves();
    } else if (pressed & PAD_SQUARE) {
        pull_all_saves();
    } else if ((pressed & PAD_TRIANGLE) && have) {
        const SaveVmc *v = &g_saves.items[g_saves_selected];
        char size[24];
        fmt_size(v->size, size, sizeof(size));
        show_info("Card image", "%s\nFile: %s\nSerial: %s\nFormat: %s\nSize: %s",
                  v->name[0] ? v->name : v->filename, v->filename,
                  v->serial[0] ? v->serial : "unknown",
                  v->is_ps1 ? "PS1 card" : (v->has_ecc ? "PS2 card (ECC)" : "PS2 card"), size);
    }
    clamp_scroll(&g_saves_selected, &g_saves_scroll, g_saves.count);
}

/* -- Physical memory card (SELECT: slot 1 / slot 2) -- */

static void handle_mcard(unsigned int pressed) {
    int port = g_mc_slot;
    McGameList *list = port == 1 ? &g_mcard2 : &g_mcard;
    int *sel    = port == 1 ? &g_mcard2_selected : &g_mcard_selected;
    int *scroll = port == 1 ? &g_mcard2_scroll : &g_mcard_scroll;
    bool have = list->count > 0 && *sel < list->count;

    if (move_selection(pressed, sel)) {
        /* moved */
    } else if (pressed & PAD_SELECT) {
        g_mc_slot ^= 1;
        return;
    } else if (pressed & PAD_CROSS) {
        static const char *const with_save[] = {
            "Upload to server", "Restore from server",
            "Switch MemCard Pro to this game", "Rescan card",
        };
        static const char *const no_save[] = { "Rescan card" };
        char title[48];
        snprintf(title, sizeof(title), "Slot %d", port + 1);
        int pick;
        if (have) {
            const McGame *g = &list->items[*sel];
            snprintf(title, sizeof(title), "Slot %d: %s", port + 1,
                     g->serial[0] ? g->serial : g->dir);
            pick = choose(title, with_save, 4);
        } else {
            pick = choose(title, no_save, 1) == 0 ? 3 : -1;
        }
        switch (pick) {
            case 0: upload_mc_game_at(list, *sel); break;
            case 1: restore_mc_game_at(list, *sel); break;
            case 2: mcp_switch_gameid(list->items[*sel].serial, port); break;
            case 3: scan_mcard_list(port, list); break;
            default: break;
        }
    } else if (pressed & PAD_SQUARE) {
        upload_card_all(port);
    } else if ((pressed & PAD_TRIANGLE) && have) {
        const McGame *g = &list->items[*sel];
        char size[24];
        fmt_size(g->total_size, size, sizeof(size));
        show_info("Memory card save", "%s\nSlot %d (%s card)\nSerial: %s\nFolder: %s\n"
                  "Files: %d, %s\nGameID device: %s",
                  g->name[0] ? g->name : (g->serial[0] ? g->serial : g->dir), port + 1,
                  list->is_ps1 ? "PS1" : "PS2", g->serial[0] ? g->serial : "unknown",
                  g->dir, g->file_count, size, g_mmce_mode_names[g_state.mmce_mode[port] & 3]);
    }
    clamp_scroll(sel, scroll, list->count);
}

/* -- Server saves (SELECT: sync source VMC / slot 1 / slot 2) -- */

static void handle_server(unsigned int pressed) {
    bool have = g_server.count > 0 && g_server_selected < g_server.count;
    if (move_selection(pressed, &g_server_selected)) {
        /* moved */
    } else if (pressed & PAD_SELECT) {
        cycle_server_source();
    } else if (pressed & PAD_CROSS) {
        int port = source_port();
        int gid_port = port < 0 ? 0 : port;
        char download[40], upload[40], sw[48];
        snprintf(download, sizeof(download), "Download to %s", source_name());
        snprintf(upload, sizeof(upload), "Upload from %s", source_name());
        snprintf(sw, sizeof(sw), "Switch MemCard Pro (slot %d) to it", gid_port + 1);

        const char *items[4];
        int actions[4], n = 0;
        enum { ACT_DOWNLOAD, ACT_UPLOAD, ACT_SWITCH, ACT_REFRESH };
        if (have) {
            items[n] = download; actions[n++] = ACT_DOWNLOAD;
            if (port >= 0) { items[n] = upload; actions[n++] = ACT_UPLOAD; }
            items[n] = sw; actions[n++] = ACT_SWITCH;
        }
        items[n] = "Refresh server list"; actions[n++] = ACT_REFRESH;

        const char *title = have ? (g_server.items[g_server_selected].name[0]
                                        ? g_server.items[g_server_selected].name
                                        : g_server.items[g_server_selected].serial)
                                 : "Server saves";
        int pick = choose(title, items, n);
        if (pick >= 0) {
            switch (actions[pick]) {
                case ACT_DOWNLOAD: server_download_to_source(); break;
                case ACT_UPLOAD:   server_upload_from_source(); break;
                case ACT_SWITCH:
                    mcp_switch_gameid(g_server.items[g_server_selected].serial, gid_port);
                    break;
                case ACT_REFRESH:  fetch_server_saves(); break;
                default: break;
            }
        }
    } else if (pressed & PAD_SQUARE) {
        server_sync_all();
    } else if ((pressed & PAD_TRIANGLE) && have) {
        const ServerSave *sv = &g_server.items[g_server_selected];
        char when[32] = "-";
        if (sv->timestamp) {
            time_t t = (time_t)sv->timestamp;
            struct tm *tm = gmtime(&t);
            if (tm) strftime(when, sizeof(when), "%Y-%m-%d %H:%M", tm);
        }
        show_info("Server save", "%s\nSerial: %s (%s)\nSaved: %s\n%s\nSync source: %s",
                  sv->name[0] ? sv->name : sv->serial, sv->serial, sv->is_ps1 ? "PS1" : "PS2",
                  when, sv->local ? "Also on a memory card" : "Only on the server",
                  source_name());
    }
    clamp_scroll(&g_server_selected, &g_server_scroll, g_server.count);
}

/* -- Settings -- */

static void config_change(int delta) {
    switch (g_cfg_row) {
        case CFG_ROW_STORAGE: cycle_storage_pref(delta); break;
        case CFG_ROW_GAMEID1: cycle_mmce_mode(0, delta); break;
        case CFG_ROW_GAMEID2: cycle_mmce_mode(1, delta); break;
        default: break;
    }
}

static void handle_config(unsigned int pressed) {
    if (pressed & PAD_UP) {
        if (g_cfg_row > 0) g_cfg_row--;
    } else if (pressed & PAD_DOWN) {
        if (g_cfg_row < CFG_ROW_COUNT - 1) g_cfg_row++;
    } else if (pressed & PAD_LEFT) {
        config_change(-1);
    } else if (pressed & PAD_RIGHT) {
        config_change(+1);
    } else if (pressed & PAD_CROSS) {
        if (g_cfg_row == CFG_ROW_REFRESH) {
            load_catalog(true);
        } else if (g_cfg_row == CFG_ROW_FORMAT) {
            if (confirm("Format the internal HDD as PS2 APA for OPL?") &&
                confirm("This ERASES EVERYTHING on the internal HDD.\nReally format it?"))
                format_internal_hdd();
        } else {
            config_change(+1);
        }
    }
}

/* Build the current view into the frame without presenting it, so modal
 * cards (confirm, menu, details, transfer) can be layered on top. */
static void draw_screen(void) {
    ui_begin();
    ui_draw_header(&g_state, g_view);
    switch (g_view) {
        case APP_VIEW_ROMS:
            ui_draw_roms(&g_catalog, g_rom_selected, g_rom_scroll);
            break;
        case APP_VIEW_LOCAL:
            ui_draw_local(&g_local, g_local_selected, g_local_scroll);
            break;
        case APP_VIEW_DOWNLOADS:
            ui_draw_downloads(&g_downloads, g_dl_selected, g_dl_scroll);
            break;
        case APP_VIEW_SAVES:
            ui_draw_saves(&g_saves, g_saves_selected, g_saves_scroll);
            break;
        case APP_VIEW_MCARD:
            if (g_mc_slot == 1)
                ui_draw_mcard(&g_mcard2, g_mcard2_selected, g_mcard2_scroll);
            else
                ui_draw_mcard(&g_mcard, g_mcard_selected, g_mcard_scroll);
            break;
        case APP_VIEW_SERVER:
            ui_draw_server(&g_server, g_server_selected, g_server_scroll);
            break;
        case APP_VIEW_CONFIG:
            ui_draw_config(&g_state, g_cfg_row);
            break;
        default: break;
    }
    if (g_transfer_active) {
        char target[48];
        if (g_state.storage_backend == STORAGE_BACKEND_HDLOADER)
            snprintf(target, sizeof(target), "the internal HDD (HDLoader)");
        else
            snprintf(target, sizeof(target), "USB / HDD storage (%s)", g_state.usb_root);
        ui_draw_transfer(g_active_name, target,
                         g_active_done, g_active_total, g_active_bps,
                         ui_ms() - g_active_start_ms);
    }
}

static void redraw(void) {
    draw_screen();
    ui_flush();
}

int main(int argc, char *argv[]) {
    (void)argc; (void)argv;

    /* gsKit owns the GS from the very first frame: the boot splash logs
     * every step (IRX load, mc init, network bring-up) as it happens.
     * gsKit only drives the EE DMA controller and the GS, so the IOP
     * reset below doesn't disturb it. */
    ui_init();
    ui_set_context(&g_local, &g_downloads);
    ui_log("ps2sync v%s booting...\n", APP_VERSION);

    boot_iop_modules();
    ui_log("BOOT: IRX done - pausing 3 s so you can read the log\n");
    DelayThread(3000000);

    ui_log("BOOT: loading config (mcInit...)\n");
    char err[256];
    if (!config_load(&g_state, err, sizeof(err))) {
        ui_boot_done();
        pad_init();
        ui_draw_message("Config error", err);
        for (;;) {
            unsigned int p = pad_read_pressed();
            if (p & PAD_CIRCLE) break;
            DelayThread(16000);
        }
        return 1;
    }
    config_load_console_id(&g_state);
    ui_set_mmce(0, g_state.mmce_mode[0]);   /* reflect persisted GameID modes */
    ui_set_mmce(1, g_state.mmce_mode[1]);
    ui_log("BOOT: console_id=%s server=%s\n",
           g_state.console_id, g_state.server_url);

    boot_device_modules(&g_state);

    ui_log("BOOT: bringing up network\n");
    network_init(&g_state);
    ui_log("BOOT: net ready=%d dhcp=%d ip=%s\n",
           g_state.net_ready, g_state.dhcp_ok, g_state.ip);

    init_storage_targets();
    resolve_cache_path();

    ui_log("BOOT: pad init\n");
    pad_init();

    ui_log("BOOT: ready\n");
    ui_boot_done();

    /* Auto-populate the views so the user lands on usable lists.  The
     * catalog comes from the cache when the server's fingerprint is
     * unchanged (or the server is unreachable). */
    if (g_state.usb_ready) {
        scan_local();
        scan_saves();
    }
    scan_mcard_list(0, &g_mcard);
    scan_mcard_list(1, &g_mcard2);
    load_catalog(false);
    if (network_is_ready(&g_state)) {
        fetch_server_saves();
    }

    redraw();

    for (;;) {
        unsigned int pressed = pad_read_pressed();

        /* Event-driven redraw: the last frame stays on screen (double
         * buffered), so only repaint when input changed something. */
        if (pressed == 0) {
            DelayThread(16000);
            continue;
        }

        if (pressed & PAD_START) {
            if (confirm("Exit GameSync?")) break;
        } else if (pressed & PAD_R1) {
            cycle_view(+1);
        } else if (pressed & PAD_L1) {
            cycle_view(-1);
        } else {
            switch (g_view) {
                case APP_VIEW_ROMS:      handle_roms(pressed);      break;
                case APP_VIEW_LOCAL:     handle_local(pressed);     break;
                case APP_VIEW_DOWNLOADS: handle_downloads(pressed); break;
                case APP_VIEW_SAVES:     handle_saves(pressed);     break;
                case APP_VIEW_MCARD:     handle_mcard(pressed);     break;
                case APP_VIEW_SERVER:    handle_server(pressed);    break;
                case APP_VIEW_CONFIG:    handle_config(pressed);    break;
                default: break;
            }
        }

        redraw();
    }

    network_shutdown();
    return 0;
}
