/*
 * wiiusync — Save Sync Wii U client (Aroma homebrew).
 *
 * Boot: ProcUI + OSScreen UI -> mount SD -> config -> network -> libmocha
 *       (SLC + MLC) -> menu loop.
 *
 * Views (ZL/ZR cycle):
 *   CATALOG    server ROM catalog, GC / WII toggle
 *   LOCAL      installed games on SD
 *   DOWNLOADS  resumable queue
 *   GC CARDS   Nintendont memory-card images and the saves inside them
 *   SERVER     every GC save the server holds
 *   VWII       vWii NAND saves      (three-way-hash smart sync)
 *   WII U      Wii U MLC saves      (three-way-hash smart sync)
 *   CONFIG
 */

#include <ctype.h>

#include "common.h"
#include "config.h"
#include "downloads.h"
#include "appstate.h"
#include "gcsaves.h"
#include "http.h"
#include "install.h"
#include "natives.h"
#include "roms.h"
#include "sync.h"
#include "ui.h"
#include "vmcfs.h"
#include "wiiunet.h"

#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>   /* strcasecmp — GC_/WII_ title ids are case-insensitive */
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>

#include <malloc.h>

#include <coreinit/core.h>
#include <coreinit/systeminfo.h>
#include <coreinit/title.h>
#include <coreinit/thread.h>
#include <coreinit/time.h>
#include <proc_ui/procui.h>
#include <sysapp/launch.h>
#include <vpad/input.h>
#include <whb/log.h>
#include <whb/log_udp.h>
#include <whb/sdcard.h>

static SyncState     g_state;
static AppView       g_view = APP_VIEW_ROMS;

static RomCatalog    g_catalog;
static LocalRomList  g_local;
static DownloadList  g_downloads;
static char          g_cat_system[8] = "GC";

static SaveVmcList   g_cards;              /* card images found on SD */
static VmcfsCard     g_card;               /* currently opened image */
static int           g_card_active = 0;
static ServerSaveList g_server;

static SaveTitleList g_vwii, g_wiiu;
static SyncPlan      g_plan;
static bool          g_plan_valid = false;

/* Boot deliberately does not hit the network (an unreachable server used to
 * stall startup).  Instead the catalog and the server save list load the
 * first time their view is opened — these track whether that has happened. */
static bool g_catalog_loaded = false;
static bool g_server_loaded  = false;
/* Set while that list is being fetched, so its empty view says "loading". */
static bool g_catalog_loading = false;
static bool g_server_loading  = false;

static int g_cfg_sel  = 0;
static int g_rom_sel  = 0, g_rom_scroll  = 0;
static int g_loc_sel  = 0, g_loc_scroll  = 0;
static int g_dl_sel   = 0, g_dl_scroll   = 0;
static int g_gc_sel   = 0, g_gc_scroll   = 0;
static int g_sv_sel   = 0, g_sv_scroll   = 0;
static int g_vw_sel   = 0, g_vw_scroll   = 0;
static int g_wu_sel   = 0, g_wu_scroll   = 0;

static char g_scratch[512 * 1024];   /* catalog / titles JSON page buffer */

/* Live download progress (updated from progress_cb). */
static volatile uint64_t g_active_done  = 0;
static volatile uint64_t g_active_total = 0;
static volatile uint64_t g_active_bps   = 0;
static volatile bool     g_pause_req    = false;
static uint64_t          g_last_frame_ms = 0;  /* when redraw() last ran */
static uint64_t          g_pad_poll_ms  = 0;
static uint64_t          g_spd_ms       = 0;
static uint64_t          g_spd_bytes    = 0;

static void redraw(void);
static void scan_local(void);
static void scan_natives_quiet(void);
static int  wait_cb(uint32_t ms);
static void open_card(int idx);
static void fetch_server(void);

/* ---- input ---- */

/* Set from the HOME_BUTTON_DENIED callback, which runs inside ProcUI while
 * the code is blocked in app_running() — from the main loop or from any of
 * the progress/wait callbacks.  Every input loop below must check it, or a
 * HOME press during that loop does nothing (there is no VPAD bit for HOME). */
static volatile bool g_quit_requested = false;

static uint32_t home_button_denied(void *ctx) {
    (void)ctx;
    g_quit_requested = true;
    return 0;
}

static uint32_t pad_read(uint32_t *held_out) {
    VPADStatus st;
    VPADReadError err = VPAD_READ_SUCCESS;
    if (held_out) *held_out = 0;
    if (VPADRead(VPAD_CHAN_0, &st, 1, &err) < 1 || err != VPAD_READ_SUCCESS)
        return 0;
    if (held_out) *held_out = st.hold;
    return st.trigger;
}

static uint64_t now_ms(void) {
    return (uint64_t)OSTicksToMilliseconds(OSGetTime());
}

/* ---- helpers ---- */

static void human_size(uint64_t b, char *o, size_t n) { ui_human_size(b, o, n); }

static void clamp_scroll(int *sel, int *scroll, int count) {
    if (count == 0) { *sel = 0; *scroll = 0; return; }
    if (*sel < 0) *sel = 0;
    if (*sel >= count) *sel = count - 1;
    int vis = ui_list_visible();
    if (vis < 1) vis = 1;
    if (*sel < *scroll) *scroll = *sel;
    if (*sel >= *scroll + vis) *scroll = *sel - vis + 1;
    if (*scroll < 0) *scroll = 0;
}

/* ---- modal overlays drawn on top of the current view ----
 *
 * A long operation (download, install, sync run) shows a progress card; a
 * question shows a dialog card.  Both are drawn by draw_frame() over the
 * dimmed view, so the context stays visible behind them. */

typedef enum {
    BUSY_NONE = 0,
    BUSY_DOWNLOAD,      /* fed live from the g_active_* counters */
    BUSY_STATIC,        /* fields set by the caller */
} BusyKind;

static BusyKind   g_busy_kind = BUSY_NONE;
static UiProgress g_busy;
static char       g_busy_title[48];
static char       g_busy_name[160];
static char       g_busy_detail[200];
static uint32_t   g_wait_secs = 0;     /* server-side conversion wait */

static void busy_begin(BusyKind kind, const char *title, const char *name,
                       const char *detail) {
    g_busy_kind = kind;
    memset(&g_busy, 0, sizeof(g_busy));
    snprintf(g_busy_title, sizeof(g_busy_title), "%s", title ? title : "");
    snprintf(g_busy_name, sizeof(g_busy_name), "%s", name ? name : "");
    snprintf(g_busy_detail, sizeof(g_busy_detail), "%s", detail ? detail : "");
    g_wait_secs = 0;
}

static void busy_end(void) { g_busy_kind = BUSY_NONE; g_wait_secs = 0; }

typedef struct {
    const char *title;
    const char *message;
    uint32_t    tone;
    const UiHint *hints;
    int         hint_count;
} Dialog;

static const Dialog *g_dialog = NULL;

/* Boot progress.  Every step is numbered and painted BEFORE the work runs,
 * so if a stage wedges the screen names it instead of showing a dead frame. */
#define BOOT_STEPS 8
static const char *g_boot_labels[BOOT_STEPS] = {
    "Mounting SD card",
    "Reading config",
    "Bringing up the network",
    "Opening NAND (libmocha)",
    "Scanning installed games",
    "Scanning GC memory-card images",
    "Scanning console saves",
    "Checking server",
};
static int g_boot_step = 0;
static const char *g_boot_note = NULL;

static bool g_booting = false;   /* the boot card owns the screen */

static void draw_boot(void *ctx) {
    (void)ctx;
    char wait[96];
    const char *note = g_boot_note;
    if (g_wait_secs) {
        snprintf(wait, sizeof(wait), "Waiting for the server... %u s (B skips)",
                 (unsigned)g_wait_secs);
        note = wait;
    }
    ui_draw_boot(g_boot_labels, BOOT_STEPS, g_boot_step - 1, note);
}

static void show_boot(const char *label, const char *note) {
    if (g_boot_step < BOOT_STEPS) g_boot_labels[g_boot_step] = label;
    g_boot_step++;
    g_boot_note = note;
    ui_render(draw_boot, NULL);
    /* Mirrored to the UDP logger: when a stage wedges hard enough that the
     * screen never updates, the last line received on the PC is the answer. */
    WHBLogPrintf("boot step %d: %s", g_boot_step, label);
}

static const UiHint YES_NO[] = { { "A", "Yes" }, { "B", "No" } };

/* Modal yes/no over the current view.  A = yes, B = no. */
static bool confirm_v(const char *title, uint32_t tone, const char *fmt, va_list ap) {
    char msg[320];
    vsnprintf(msg, sizeof(msg), fmt, ap);

    Dialog d = { title, msg, tone, YES_NO, 2 };
    g_dialog = &d;
    bool repaint = true, answer = false;
    while (app_running() && !g_quit_requested) {
        if (repaint || ui_consume_repaint_request()) {
            redraw();
            repaint = false;
        }
        uint32_t b = pad_read(NULL);
        if (b & VPAD_BUTTON_A) { answer = true; break; }
        if (b & VPAD_BUTTON_B) break;
        OSSleepTicks(OSMillisecondsToTicks(16));
    }
    g_dialog = NULL;
    return answer;
}

static bool confirm(const char *fmt, ...) __attribute__((format(printf, 1, 2)));
static bool confirm(const char *fmt, ...) {
    va_list ap;
    va_start(ap, fmt);
    bool r = confirm_v("Confirm", HEX_ACCENT, fmt, ap);
    va_end(ap);
    return r;
}

/* Same, styled as a warning — for anything that overwrites or deletes. */
static bool confirm_danger(const char *title, const char *fmt, ...)
    __attribute__((format(printf, 2, 3)));
static bool confirm_danger(const char *title, const char *fmt, ...) {
    va_list ap;
    va_start(ap, fmt);
    bool r = confirm_v(title, HEX_ERR, fmt, ap);
    va_end(ap);
    return r;
}

/* ---- controller text editor (on-screen character picker) ---- */

static const char CHARSET[] =
    " ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789.:/_-?=&%@";

static char charset_step(char c, int dir) {
    int n = (int)sizeof(CHARSET) - 1;
    const char *pos = strchr(CHARSET, c);
    int idx = pos ? (int)(pos - CHARSET) : 1;
    idx = (idx + dir + n) % n;
    return CHARSET[idx];
}

typedef struct {
    const char *label;
    const char *buf;
    int         cur;
} EditCtx;

static void draw_edit_frame(void *ctx) {
    const EditCtx *e = (const EditCtx *)ctx;
    static const UiHint hints[] = {
        { "UD", "Character" }, { "LR", "Move" }, { "ZR", "Insert" },
        { "X", "Delete" }, { "A", "Done" }, { "B", "Cancel" },
    };
    int len = (int)strlen(e->buf);
    char at = e->cur < len ? e->buf[e->cur] : ' ';
    ui_draw_background();
    ui_draw_header_plain(&g_state, "Edit setting");
    ui_draw_editor(e->label, e->buf, e->cur, charset_step(at, +1), charset_step(at, -1));
    ui_draw_footer(hints, (int)(sizeof(hints) / sizeof(hints[0])));
}

static void draw_edit(const char *label, const char *buf, int cur) {
    EditCtx e = { label, buf, cur };
    ui_render(draw_edit_frame, &e);
}

static bool edit_text(char *out, size_t cap, const char *label) {
    char buf[256];
    strncpy(buf, out, sizeof(buf) - 1);
    buf[sizeof(buf) - 1] = '\0';
    int len = (int)strlen(buf);
    int cur = len;
    int maxlen = (int)(cap < sizeof(buf) ? cap : sizeof(buf)) - 1;

    draw_edit(label, buf, cur);
    while (app_running() && !g_quit_requested) {
        if (ui_consume_repaint_request()) draw_edit(label, buf, cur);
        uint32_t d = pad_read(NULL);
        if (d == 0) { OSSleepTicks(OSMillisecondsToTicks(16)); continue; }

        if (d & VPAD_BUTTON_A) {   /* PLUS is the global quit, not "accept" */
            strncpy(out, buf, cap - 1);
            out[cap - 1] = '\0';
            return true;
        }
        if (d & VPAD_BUTTON_B) return false;
        if ((d & VPAD_BUTTON_LEFT)  && cur > 0)   cur--;
        if ((d & VPAD_BUTTON_RIGHT) && cur < len) cur++;
        if (d & (VPAD_BUTTON_UP | VPAD_BUTTON_DOWN)) {
            int dir = (d & VPAD_BUTTON_UP) ? 1 : -1;
            if (cur == len) {
                if (len < maxlen) { buf[len++] = charset_step(' ', dir); buf[len] = '\0'; }
            } else {
                buf[cur] = charset_step(buf[cur], dir);
            }
        }
        if (d & VPAD_BUTTON_X) {
            if (cur < len) { memmove(&buf[cur], &buf[cur + 1], (size_t)(len - cur)); len--; }
            else if (len > 0) { buf[--len] = '\0'; cur = len; }
        }
        if (d & VPAD_BUTTON_ZR) {
            if (len < maxlen) {
                memmove(&buf[cur + 1], &buf[cur], (size_t)(len - cur + 1));
                buf[cur] = ' ';
                len++;
            }
        }
        draw_edit(label, buf, cur);
    }
    return false;
}

/* ---- Config view ---- */

typedef enum {
    CF_SERVER = 0, CF_APIKEY, CF_STORAGE, CF_INSTTARGET,
    CF_NINSAVES, CF_GAMES, CF_WBFS, CF_INSTALL,
    CF_SYNCVWII, CF_SYNCWIIU, CF_SAVE, CF_COUNT
} CfgField;

/* Where downloads land.  "usb" only takes effect once a FAT32 drive mounts,
 * so show what is actually in use next to what was asked for. */
static const char *storage_label(void) {
    bool want_usb = !strcasecmp(g_state.rom_storage, "usb");
    if (!want_usb) return "SD card";
    return g_state.usb_fat_ready ? "USB (FAT32)" : "USB (not found -> SD)";
}

static const char *install_target_label(void) {
    if (strcasecmp(g_state.install_target, "usb") != 0) return "NAND (MLC)";
    return g_state.usb_mounted ? "USB (Wii U drive)" : "USB (no drive found)";
}

static const struct {
    const char *label;
    const char *help;
} CFG_INFO[CF_COUNT] = {
    [CF_SERVER]     = { "Server URL",
                        "Address of your GameSync server, e.g. http://192.168.1.100:8000. "
                        "Hostnames are resolved through DNS." },
    [CF_APIKEY]     = { "API key",
                        "Sent as X-API-Key with every request. Must match the server's "
                        "SYNC_API_KEY." },
    [CF_STORAGE]    = { "Download to",
                        "Where catalog downloads land: the SD card or a FAT32 USB drive. "
                        "App data always stays on SD. A retries a USB mount." },
    [CF_INSTTARGET] = { "Install to",
                        "Where Wii U titles are installed: internal NAND (MLC) or the "
                        "console's own Wii U-formatted USB drive." },
    [CF_NINSAVES]   = { "GC memory cards",
                        "Folder scanned for Nintendont virtual memory-card images." },
    [CF_GAMES]      = { "GC games",
                        "Nintendont install folder: <folder>/<GAMEID>/game.iso." },
    [CF_WBFS]       = { "Wii games",
                        "USB Loader GX / WiiFlow folder for split WBFS Wii games." },
    [CF_INSTALL]    = { "Wii U WUP",
                        "Staging folder for Wii U downloads. Install them from the "
                        "Installed view." },
    [CF_SYNCVWII]   = { "Sync vWii",
                        "List and sync vWii NAND saves (needs Aroma and libmocha)." },
    [CF_SYNCWIIU]   = { "Sync Wii U",
                        "List and sync Wii U saves (needs Aroma and libmocha). Turn both "
                        "sync options off on emulators." },
    [CF_SAVE]       = { "Save settings",
                        "Write these settings to sd:/3dssync/config.txt." },
};

static int g_cfg_scroll = 0;

/* Value column for one settings row (API key masked). */
static void cfg_value(int f, char *out, size_t n) {
    switch (f) {
        case CF_SERVER:     snprintf(out, n, "%s", g_state.server_url); break;
        case CF_APIKEY: {
            size_t len = strlen(g_state.api_key);
            snprintf(out, n, "%.4s%s", g_state.api_key, len > 4 ? "\xE2\x80\xA2\xE2\x80\xA2\xE2\x80\xA2\xE2\x80\xA2" : "");
            break;
        }
        case CF_STORAGE:    snprintf(out, n, "%s", storage_label()); break;
        case CF_INSTTARGET: snprintf(out, n, "%s", install_target_label()); break;
        case CF_NINSAVES:   snprintf(out, n, "%s", g_state.nin_saves_dir); break;
        case CF_GAMES:      snprintf(out, n, "%s", g_state.games_dir); break;
        case CF_WBFS:       snprintf(out, n, "%s", g_state.wbfs_dir); break;
        case CF_INSTALL:    snprintf(out, n, "%s", g_state.install_dir); break;
        default:            out[0] = '\0'; break;
    }
}

static void draw_config(void) {
    char right[48];
    snprintf(right, sizeof(right), "sd:/3dssync/config.txt");
    ui_list_panel("Settings", right);

    int vis = ui_list_visible();
    for (int i = 0; i < vis; i++) {
        int f = g_cfg_scroll + i;
        if (f >= CF_COUNT) break;
        char val[160];
        cfg_value(f, val, sizeof(val));
        UiRow r = { .text = CFG_INFO[f].label, .right = val, .permille = -1 };
        if (f == CF_SYNCVWII || f == CF_SYNCWIIU) {
            bool on = f == CF_SYNCVWII ? g_state.sync_vwii : g_state.sync_wiiu;
            r.badge = on ? "On" : "Off";
            r.badge_color = on ? HEX_OK : HEX_MUTED;
        } else if (f == CF_SAVE) {
            r.icon = UI_ICON_OK;
            r.icon_color = HEX_OK;
            r.text_color = HEX_OK;
        } else if ((f == CF_STORAGE && !strcasecmp(g_state.rom_storage, "usb") &&
                    !g_state.usb_fat_ready) ||
                   (f == CF_INSTTARGET && !strcasecmp(g_state.install_target, "usb") &&
                    !g_state.usb_mounted)) {
            r.badge = "No drive";
            r.badge_color = HEX_WARN;
            r.right = NULL;
        }
        ui_list_row(i, f == g_cfg_sel, &r);
    }
    ui_list_scrollbar(g_cfg_scroll, vis, CF_COUNT);

    /* Detail: what the selected setting does, plus live storage facts. */
    int f = g_cfg_sel;
    ui_detail_begin(CFG_INFO[f].label);
    ui_detail_note(HEX_DIM, "%s", CFG_INFO[f].help);
    ui_detail_gap(6);
    if (f == CF_APIKEY || f == CF_SERVER) {
        ui_detail_field("Value", "%s", f == CF_SERVER ? g_state.server_url : g_state.api_key);
        ui_detail_field("Console", "%s", g_state.console_id);
    } else if (f == CF_STORAGE || f == CF_GAMES || f == CF_WBFS || f == CF_INSTALL) {
        char root[SAVE_DIR_LEN];
        ui_detail_field("Storage", "%s", storage_label());
        roms_games_dir(root, sizeof(root));
        ui_detail_field("GC games", "%s", root);
        roms_wbfs_dir(root, sizeof(root));
        ui_detail_field("Wii games", "%s", root);
        roms_install_dir(root, sizeof(root));
        ui_detail_field("Wii U WUP", "%s", root);
        if (!strcasecmp(g_state.rom_storage, "usb") && !g_state.usb_fat_ready)
            ui_detail_note(HEX_WARN, "USB: %s", g_state.usb_fat_error);
    } else if (f == CF_SYNCVWII || f == CF_SYNCWIIU || f == CF_INSTTARGET) {
        ui_detail_field("libmocha", "%s", g_state.mocha_ok ? "available" : "not available");
        ui_detail_field("Wii U USB", "%s", g_state.usb_mounted ? "attached" : "not found");
        if (g_state.mocha_error[0])
            ui_detail_note(HEX_WARN, "mocha: %s", g_state.mocha_error);
    }
    ui_detail_gap(6);
    if (f == CF_SYNCVWII || f == CF_SYNCWIIU || f == CF_STORAGE || f == CF_INSTTARGET)
        ui_detail_action("LR", "Change");
    ui_detail_action("A", f == CF_SAVE ? "Save to SD"
                        : (f == CF_SYNCVWII || f == CF_SYNCWIIU || f == CF_INSTTARGET)
                            ? "Toggle"
                        : f == CF_STORAGE ? "Change / retry USB" : "Edit");
}

static void config_change(void) {
    switch (g_cfg_sel) {
        case CF_SYNCVWII: g_state.sync_vwii = !g_state.sync_vwii; break;
        case CF_SYNCWIIU: g_state.sync_wiiu = !g_state.sync_wiiu; break;
        case CF_STORAGE:
            snprintf(g_state.rom_storage, sizeof(g_state.rom_storage), "%s",
                     !strcasecmp(g_state.rom_storage, "usb") ? "sd" : "usb");
            /* Mount on demand so the user can plug a drive in without
             * relaunching; roms_set_target then repoints every download dir. */
            if (!strcasecmp(g_state.rom_storage, "usb") && !g_state.usb_fat_ready)
                natives_mount_usb_fat(&g_state);
            roms_set_target(&g_state);
            roms_ensure_target_dirs();
            break;
        case CF_INSTTARGET:
            snprintf(g_state.install_target, sizeof(g_state.install_target), "%s",
                     !strcasecmp(g_state.install_target, "usb") ? "mlc" : "usb");
            break;
        default: break;
    }
}

static void config_activate(void) {
    switch (g_cfg_sel) {
        case CF_SERVER:   edit_text(g_state.server_url, sizeof(g_state.server_url), "Server URL"); break;
        case CF_APIKEY:   edit_text(g_state.api_key, sizeof(g_state.api_key), "API Key"); break;
        case CF_NINSAVES: edit_text(g_state.nin_saves_dir, sizeof(g_state.nin_saves_dir), "GC memcard dir"); break;
        case CF_GAMES:    edit_text(g_state.games_dir, sizeof(g_state.games_dir), "GC games dir");
                          roms_set_target(&g_state); break;
        case CF_WBFS:     edit_text(g_state.wbfs_dir, sizeof(g_state.wbfs_dir), "Wii wbfs dir");
                          roms_set_target(&g_state); break;
        case CF_INSTALL:  edit_text(g_state.install_dir, sizeof(g_state.install_dir), "Wii U WUP dir");
                          roms_set_target(&g_state); break;
        case CF_STORAGE:
            /* Retry the mount without flipping the setting — the common case
             * is "I picked USB, then plugged the drive in". */
            if (!strcasecmp(g_state.rom_storage, "usb") && !g_state.usb_fat_ready) {
                if (natives_mount_usb_fat(&g_state)) {
                    roms_set_target(&g_state);
                    roms_ensure_target_dirs();
                    ui_status("FAT32 USB mounted");
                } else {
                    ui_error("%s", g_state.usb_fat_error);
                }
            } else {
                config_change();
            }
            break;
        case CF_INSTTARGET:
        case CF_SYNCVWII:
        case CF_SYNCWIIU: config_change(); break;
        case CF_SAVE:
            if (!g_state.sd_ready) ui_error("No SD mounted - cannot save config");
            else if (config_save(&g_state)) ui_status("Config saved to SD");
            else ui_error("Failed to write config");
            break;
        default: break;
    }
}

static void config_input(uint32_t d) {
    if (d & VPAD_BUTTON_UP)    g_cfg_sel = (g_cfg_sel - 1 + CF_COUNT) % CF_COUNT;
    if (d & VPAD_BUTTON_DOWN)  g_cfg_sel = (g_cfg_sel + 1) % CF_COUNT;
    if (d & (VPAD_BUTTON_LEFT | VPAD_BUTTON_RIGHT)) config_change();
    if (d & VPAD_BUTTON_A)     config_activate();
    clamp_scroll(&g_cfg_sel, &g_cfg_scroll, CF_COUNT);
}

/* ---- catalog / downloads ---- */

static void fetch_catalog(void) {
    if (!network_is_ready(&g_state)) { ui_error("Network not ready (%s)", g_state.ip); return; }
    ui_status("Fetching %s catalog...", g_cat_system);
    g_catalog_loading = true;
    redraw();
    bool ok = roms_fetch_catalog(&g_state, g_cat_system,
                                 g_scratch, sizeof(g_scratch), &g_catalog);
    g_catalog_loading = false;
    /* Only a successful fetch counts as loaded — a failure (server briefly
     * down at boot, say) retries the next time the view is entered. */
    g_catalog_loaded = ok;
    if (!ok) ui_error("%s", g_catalog.last_error);
    else     ui_status("Catalog: %d %s ROM(s)", g_catalog.count, g_cat_system);
    g_rom_sel = 0; g_rom_scroll = 0;
    redraw();
}

static void toggle_catalog_system(void) {
    /* GC -> WII -> WIIU -> GC */
    const char *next = "GC";
    if (strcmp(g_cat_system, "GC") == 0)       next = "WII";
    else if (strcmp(g_cat_system, "WII") == 0) next = "WIIU";
    snprintf(g_cat_system, sizeof(g_cat_system), "%s", next);
    g_catalog.count = 0;
    g_catalog_loaded = false;
    g_rom_sel = 0; g_rom_scroll = 0;
    /* Fetch right away rather than asking for a button press. */
    if (network_is_ready(&g_state)) fetch_catalog();
    else ui_status("Catalog set to %s (no network - Y retries)", g_cat_system);
}

/* Staged WUP folders are named by title id, so give the list a real game name
 * when the catalog has been fetched and knows one. */
static void fill_wiiu_names(void) {
    for (int i = 0; i < g_local.count; i++) {
        LocalRom *r = &g_local.items[i];
        if (strcmp(r->system, "WIIU") != 0) continue;
        if (!roms_is_wiiu_title_id(r->name)) continue;
        for (int j = 0; j < g_catalog.count; j++) {
            if (strcasecmp(g_catalog.items[j].rom_id, r->name) != 0) continue;
            if (!g_catalog.items[j].name[0]) break;
            snprintf(r->name, sizeof(r->name), "%s", g_catalog.items[j].name);
            break;
        }
    }
}

static void scan_local(void) {
    if (!g_state.sd_ready) { ui_error("SD not ready"); return; }
    ui_status("Scanning installed games...");
    redraw();
    roms_scan_local(&g_local);
    fill_wiiu_names();
    if (g_local.count > 0) ui_status("Local: %d game(s)", g_local.count);
    else ui_status("%s", g_local.last_error);
    redraw();
}

/* Repaint at most ~7 times a second from transfer / install loops: a frame is
 * a full software render of both screens, and the transfer must not wait on
 * it.  Speed is sampled once a second. */
#define PROGRESS_FRAME_MS 150

static void redraw_throttled(void) {
    if (now_ms() - g_last_frame_ms < PROGRESS_FRAME_MS) return;
    redraw();
}

static void update_speed(uint64_t now, uint64_t done) {
    if (g_spd_ms == 0) { g_spd_ms = now; g_spd_bytes = done; return; }
    if (now - g_spd_ms < 1000) return;
    g_active_bps = (done - g_spd_bytes) * 1000 / (now - g_spd_ms);
    g_spd_ms = now;
    g_spd_bytes = done;
}

static int progress_cb(uint64_t done, uint64_t total) {
    g_active_done  = done;
    g_active_total = total;

    /* On the download worker thread only the shared counters are touched —
     * every UI/pad call belongs to the main thread (which watches these
     * counters from its monitor loop). */
    if (!OSIsMainCore())
        return (g_pause_req || g_quit_requested) ? 1 : 0;

    if (!app_running() || g_quit_requested)
        return 1;                   /* HOME / shutdown -> pause cleanly */
    if (ui_consume_repaint_request()) redraw();

    uint64_t now = now_ms();

    /* This runs once per 64 KB chunk, which at full LAN speed is ~150x a
     * second — well past the gamepad's own 60 Hz sample rate, so polling it
     * every time is pure overhead in the transfer's hot loop.  10 Hz is still
     * instant to a human pressing B. */
    if (now - g_pad_poll_ms >= 100) {
        g_pad_poll_ms = now;
        if (pad_read(NULL) & VPAD_BUTTON_B) g_pause_req = true;
    }

    update_speed(now, done);
    redraw_throttled();
    return g_pause_req ? 1 : 0;
}

/* Pump the UI while the server prepares a response (RVZ->ISO / RVZ->WBFS
 * conversion can take minutes).  B cancels. */
static int wait_cb(uint32_t ms) {
    if (!OSIsMainCore())            /* worker thread: no UI, just the flag */
        return (g_pause_req || g_quit_requested) ? 1 : 0;

    if (!app_running() || g_quit_requested) return 1;
    if (ui_consume_repaint_request()) redraw();
    if (pad_read(NULL) & VPAD_BUTTON_B) return 1;
    if (ms && (ms % 2000) == 0) {
        g_wait_secs = ms / 1000;
        ui_status("Waiting for server... %us (converting? B cancels)", ms / 1000);
        redraw();
    }
    return 0;
}

/* ---- download worker thread ----
 *
 * The transfer itself runs on CPU0 (main loop: CPU1, file writer: CPU2) so
 * its blocking recv() never waits on UI work and vice versa — the same
 * three-way split NUSspli uses.  The main thread stays here in a monitor
 * loop: pad polling, speed line, redraws, and the stall/cancel watchdog
 * that unblocks the worker via http_abort_active(). */

typedef struct {
    DownloadEntry *e;
    uint64_t       total;
    int            rc;
} DlWork;

static DlWork   g_dlwork;
static OSThread g_dlthread;
static uint8_t *g_dlstack = NULL;

#define DL_STACK_SIZE (128 * 1024)

static int dl_worker(int argc, const char **argv) {
    (void)argc;
    DlWork *w = (DlWork *)argv;
    DownloadEntry *e = w->e;
    if (e->url_path[0])
        w->rc = network_download_path_resumable(&g_state, e->url_path,
                                                e->target_path, e->offset,
                                                &w->total);
    else
        w->rc = network_download_rom_resumable(&g_state, e->rom_id,
                                               e->extract_format,
                                               e->target_path, e->offset,
                                               &w->total);
    return 0;
}

static void run_active_download(DownloadEntry *e) {
    if (!e) return;
    if (!g_state.sd_ready) { ui_error("No SD - cannot download"); return; }

    /* Ensure the target directory exists. */
    char dir[DOWNLOAD_PATH_LEN];
    strncpy(dir, e->target_path, sizeof(dir) - 1);
    dir[sizeof(dir) - 1] = '\0';
    char *slash = strrchr(dir, '/');
    if (slash) { *slash = '\0'; roms_mkdir_p(dir); }

    g_active_done  = e->offset;
    g_active_total = e->total;
    g_pause_req    = false;
    g_active_bps   = 0;
    g_spd_ms       = 0;
    network_set_progress64_cb(progress_cb);
    http_set_wait_cb(wait_cb);

    e->status = DL_STATUS_ACTIVE;
    downloads_save(&g_downloads);
    ui_status("Requesting %s...", e->name);
    {
        char where[200];
        snprintf(where, sizeof(where), "%s  ->  %s",
                 e->filename[0] ? e->filename : e->rom_id,
                 roms_storage_is_usb() ? "USB" : "SD card");
        busy_begin(BUSY_DOWNLOAD, "Downloading", e->name, where);
    }
    redraw();

    http_perf_reset();
    uint64_t total = 0;
    int rc;

    if (!g_dlstack) g_dlstack = (uint8_t *)memalign(16, DL_STACK_SIZE);
    g_dlwork = (DlWork){ e, 0, -1 };

    if (g_dlstack &&
        OSCreateThread(&g_dlthread, dl_worker, 0, (char *)&g_dlwork,
                       g_dlstack + DL_STACK_SIZE, DL_STACK_SIZE,
                       15, OS_THREAD_ATTRIB_AFFINITY_CPU0)) {
        OSSetThreadName(&g_dlthread, "wiiusync dl");
        OSResumeThread(&g_dlthread);

        uint64_t last_bytes  = g_active_done;
        uint64_t last_change = now_ms();
        uint64_t started     = last_change;
        while (!OSIsThreadTerminated(&g_dlthread)) {
            if (!app_running() || g_quit_requested) g_pause_req = true;
            if (ui_consume_repaint_request()) redraw();

            uint64_t now = now_ms();
            if (now - g_pad_poll_ms >= 100) {
                g_pad_poll_ms = now;
                if (pad_read(NULL) & VPAD_BUTTON_B) g_pause_req = true;
            }

            uint64_t done = g_active_done;
            if (done != last_bytes) { last_bytes = done; last_change = now; }

            /* Nothing yet: the server may be converting the ROM first. */
            g_wait_secs = (done == e->offset && now - started >= 2000)
                        ? (uint32_t)((now - started) / 1000) : 0;
            update_speed(now, done);
            redraw_throttled();

            /* The worker's blocking recv only returns when bytes land —
             * cancel and stall detection have to reach in from here.  Before
             * the first body byte the server may legitimately be converting
             * a ROM for many minutes; once data flows, 60 s of silence is a
             * dead link. */
            uint64_t stall_limit = (done > e->offset) ? 60u * 1000
                                                      : 30u * 60u * 1000;
            if (g_pause_req || now - last_change > stall_limit)
                http_abort_active();

            OSSleepTicks(OSMillisecondsToTicks(16));
        }
        int trc;
        OSJoinThread(&g_dlthread, &trc);
        rc    = g_dlwork.rc;
        total = g_dlwork.total;
        /* A transfer killed by our own abort is a pause, not an error. */
        if (g_pause_req && rc < 0) rc = 1;
    } else {
        /* Thread setup failed — synchronous fallback on the main thread. */
        if (e->url_path[0])
            rc = network_download_path_resumable(&g_state, e->url_path,
                                                 e->target_path, e->offset, &total);
        else
            rc = network_download_rom_resumable(&g_state, e->rom_id, e->extract_format,
                                                e->target_path, e->offset, &total);
    }

    network_set_progress64_cb(NULL);
    http_set_wait_cb(wait_cb);
    busy_end();
    if (total > 0) e->total = total;

    if (rc == 0) {
        e->status = DL_STATUS_COMPLETED;
        e->offset = e->total > 0 ? e->total : g_active_done;

        if (e->install == DL_INSTALL_GC_ISO) {
            char msg[192];
            if (roms_install_gc_iso(e->target_path, msg, sizeof(msg)) == 0) {
                ui_status("%s: %s", e->name, msg);
            } else {
                e->status = DL_STATUS_ERROR;
                ui_error("%s", msg);
            }
        } else {
            /* Attribute the wall-clock so a slow link can be told apart from
             * slow storage without another guess-and-reflash cycle. */
            HttpPerf p;
            http_perf_get(&p);
            uint64_t mb = p.recv_bytes >> 20;
            if (mb >= 4) {
                uint64_t net_ms = p.recv_us / 1000;
                uint64_t wr_ms  = p.write_us / 1000;
                uint64_t ui_ms  = p.progress_us / 1000;
                ui_status("Done: %s | %lluMB net=%llums wr=%llums ui=%llums "
                          "chunks=%u avg=%lluKB rcvbuf=%dKB",
                          e->name, (unsigned long long)mb,
                          (unsigned long long)net_ms,
                          (unsigned long long)wr_ms,
                          (unsigned long long)ui_ms,
                          (unsigned)p.recv_calls,
                          (unsigned long long)(p.recv_calls
                              ? (p.recv_bytes / p.recv_calls) >> 10 : 0),
                          p.rcvbuf >> 10);
            } else {
                ui_status("Done: %s", e->name);
            }
        }
    } else if (rc == 1) {
        e->status = DL_STATUS_PAUSED;
        e->offset = g_active_done;
        ui_status("Paused: %s (resume with A)", e->name);
    } else {
        e->status = DL_STATUS_ERROR;
        e->offset = g_active_done;
        ui_error("Download failed rc=%d: %s", rc, e->name);
    }
    downloads_save(&g_downloads);
    g_active_total = 0;
    redraw();
}

/* One pass over the queue.  A user cancel stops the run; an error moves on. */
static void run_download_queue(void) {
    if (!g_state.sd_ready) { ui_error("No SD - cannot download"); return; }
    int done = 0, failed = 0;
    bool stopped = false;
    for (int i = 0; i < g_downloads.count && !stopped; i++) {
        DownloadEntry *e = &g_downloads.items[i];
        if (e->status != DL_STATUS_QUEUED &&
            e->status != DL_STATUS_PAUSED &&
            e->status != DL_STATUS_ERROR)
            continue;
        run_active_download(e);
        switch (e->status) {
            case DL_STATUS_COMPLETED: done++; break;
            case DL_STATUS_PAUSED:    stopped = true; break;
            default:                  failed++; break;
        }
    }
    if (done > 0) scan_local();
    ui_status("Queue: %d done, %d failed%s", done, failed, stopped ? ", stopped" : "");
    redraw();
}

/* ---- download groups ----
 *
 * A Wii U WUP set is 25-47 queued files and a Wii game several WBFS parts;
 * showing each file is noise (the PS3 client already folds these).  The
 * Downloads view lists one row per title (rom_id), aggregated on the fly —
 * the underlying per-file entries and their resume state are untouched. */

typedef struct {
    char     rom_id[ROM_ID_LEN];
    char     name[DOWNLOAD_NAME_LEN];
    int      files, files_done;
    uint64_t done, total;
    DownloadStatus status;
} DlGroup;

static DlGroup g_dl_groups[DOWNLOAD_MAX];
static int     g_dl_group_count = 0;

/* Which aggregate status represents the group: an active file makes the
 * whole title "active", any error trumps paused, and only an entirely
 * finished set shows "done". */
static int dl_status_prio(DownloadStatus s) {
    switch (s) {
        case DL_STATUS_ACTIVE:    return 5;
        case DL_STATUS_ERROR:     return 4;
        case DL_STATUS_PAUSED:    return 3;
        case DL_STATUS_QUEUED:    return 2;
        case DL_STATUS_COMPLETED: return 1;
    }
    return 0;
}

/* Per-file entries are named "<Game> (<file>)" — fold back to the game. */
static void dl_group_name(DlGroup *g, const DownloadEntry *e) {
    snprintf(g->name, sizeof(g->name), "%s", e->name);
    size_t nl = strlen(g->name), fl = strlen(e->filename);
    if (fl && nl > fl + 3 && g->name[nl - 1] == ')' &&
        g->name[nl - 3 - fl] == ' ' && g->name[nl - 2 - fl] == '(' &&
        memcmp(g->name + nl - 1 - fl, e->filename, fl) == 0)
        g->name[nl - 3 - fl] = '\0';
}

static void dl_build_groups(void) {
    int n = 0;
    for (int i = 0; i < g_downloads.count; i++) {
        const DownloadEntry *e = &g_downloads.items[i];
        const char *gid = e->rom_id[0] ? e->rom_id : e->key;

        DlGroup *g = NULL;
        for (int j = 0; j < n; j++)
            if (strcmp(g_dl_groups[j].rom_id, gid) == 0) { g = &g_dl_groups[j]; break; }
        if (!g) {
            if (n >= DOWNLOAD_MAX) break;
            g = &g_dl_groups[n++];
            memset(g, 0, sizeof(*g));
            snprintf(g->rom_id, sizeof(g->rom_id), "%s", gid);
            dl_group_name(g, e);
            g->status = e->status;
        }

        uint64_t done = e->status == DL_STATUS_COMPLETED ? e->total
                      : e->status == DL_STATUS_ACTIVE    ? g_active_done
                      : e->offset;
        g->files++;
        if (e->status == DL_STATUS_COMPLETED) g->files_done++;
        g->done  += done;
        g->total += e->total;
        if (dl_status_prio(e->status) > dl_status_prio(g->status))
            g->status = e->status;
    }
    g_dl_group_count = n;
}

static int dl_group_index(const char *rom_id) {
    dl_build_groups();
    for (int i = 0; i < g_dl_group_count; i++)
        if (strcmp(g_dl_groups[i].rom_id, rom_id) == 0) return i;
    return 0;
}

/* Run every runnable file of one title, in queue order. */
static void run_download_group(const char *rom_id) {
    if (!g_state.sd_ready) { ui_error("No SD - cannot download"); return; }
    for (int i = 0; i < g_downloads.count; i++) {
        DownloadEntry *e = &g_downloads.items[i];
        const char *gid = e->rom_id[0] ? e->rom_id : e->key;
        if (strcmp(gid, rom_id) != 0) continue;
        if (e->status != DL_STATUS_QUEUED &&
            e->status != DL_STATUS_PAUSED &&
            e->status != DL_STATUS_ERROR)
            continue;
        run_active_download(e);
        if (e->status == DL_STATUS_PAUSED) break;   /* user cancelled */
        if (e->status != DL_STATUS_COMPLETED) break;
    }
    scan_local();
}

/* Drop every file of one title from the queue. */
static void remove_download_group(const char *rom_id) {
    bool removed = true;
    while (removed) {
        removed = false;
        for (int i = 0; i < g_downloads.count; i++) {
            DownloadEntry *e = &g_downloads.items[i];
            const char *gid = e->rom_id[0] ? e->rom_id : e->key;
            if (strcmp(gid, rom_id) == 0) {
                downloads_remove(&g_downloads, e->key);
                removed = true;
                break;
            }
        }
    }
    downloads_save(&g_downloads);
}

/* Expand a Wii catalog entry into one download per split WBFS part. */
static int queue_wii_rom(const RomEntry *rom, bool run_now) {
    ui_status("Asking server for WBFS parts (may take minutes)...");
    redraw();

    http_set_wait_cb(wait_cb);
    WbfsManifest man;
    int rc = network_fetch_wbfs_manifest(&g_state, rom->rom_id,
                                         g_scratch, sizeof(g_scratch), &man);
    http_set_wait_cb(wait_cb);
    if (rc != 0) { ui_error("%s", man.last_error); return -1; }

    char game_dir[SAVE_DIR_LEN];
    roms_wbfs_game_dir(man.name[0] ? man.name : rom->name, man.game_id,
                       game_dir, sizeof(game_dir));
    roms_mkdir_p(game_dir);

    DownloadEntry *first = NULL;
    for (int i = 0; i < man.part_count; i++) {
        DownloadEntry *e = downloads_upsert_wbfs_part(&g_downloads, rom,
                                                      man.parts[i].name,
                                                      man.parts[i].size,
                                                      game_dir);
        if (!e) { ui_error("Download list full"); break; }
        if (!first) first = e;
    }
    downloads_save(&g_downloads);
    ui_status("Queued %s [%s]: %d part(s)", man.name, man.game_id, man.part_count);

    if (run_now && first) {
        g_dl_sel = dl_group_index(rom->rom_id);
        g_view = APP_VIEW_DOWNLOADS;
        run_download_queue();
    }
    return 0;
}

static const RomEntry *catalog_find(const char *rom_id) {
    if (!rom_id || !rom_id[0]) return NULL;
    for (int i = 0; i < g_catalog.count; i++)
        if (strcmp(g_catalog.items[i].rom_id, rom_id) == 0)
            return &g_catalog.items[i];
    return NULL;
}

/* Expand a Wii U bundle entry into one download per WUP file.  The folder is
 * only installable once every file has landed (title.tmd is what MCP reads
 * first), so nothing auto-installs — the Local view does that on request. */
static int queue_wiiu_rom(const RomEntry *rom, bool run_now) {
    if (!rom->is_bundle) {
        ui_error("%s is not a WUP folder on the server (unzip it there)",
                 rom->name);
        return -1;
    }

    ui_status("Asking server for the WUP file list...");
    redraw();

    http_set_wait_cb(wait_cb);
    BundleManifest man;
    int rc = network_fetch_bundle_manifest(&g_state, rom->rom_id,
                                           g_scratch, sizeof(g_scratch), &man);
    http_set_wait_cb(wait_cb);
    if (rc != 0) { ui_error("%s", man.last_error); return -1; }

    char game_dir[SAVE_DIR_LEN];
    roms_wup_game_dir(rom->rom_id, man.name[0] ? man.name : rom->name,
                      game_dir, sizeof(game_dir));
    roms_mkdir_p(game_dir);

    DownloadEntry *first = NULL;
    for (int i = 0; i < man.file_count; i++) {
        DownloadEntry *e = downloads_upsert_wup_file(&g_downloads, rom,
                                                     man.files[i].name,
                                                     man.files[i].size,
                                                     game_dir);
        if (!e) { ui_error("Download list full (%d files needed)", man.file_count); break; }
        if (!first) first = e;
    }
    downloads_save(&g_downloads);
    ui_status("Queued %s: %d file(s), %llu MB", man.name, man.file_count,
              (unsigned long long)(man.total_size >> 20));

    if (run_now && first) {
        g_dl_sel = dl_group_index(rom->rom_id);
        g_view = APP_VIEW_DOWNLOADS;
        run_download_queue();
    }
    return 0;
}

static void queue_selected_rom(bool run_now) {
    if (!g_state.sd_ready) { ui_error("No SD - cannot install"); return; }
    if (g_catalog.count == 0 || g_rom_sel >= g_catalog.count) return;
    const RomEntry *rom = &g_catalog.items[g_rom_sel];

    if (strcmp(g_cat_system, "WII") == 0) {
        queue_wii_rom(rom, run_now);
        return;
    }
    if (strcmp(g_cat_system, "WIIU") == 0) {
        /* A game's update and DLC are separate titles that are useless apart,
         * so one action queues the whole set.  The server already ordered
         * related[] game -> update -> DLC, which is the order MCP needs. */
        int queued = queue_wiiu_rom(rom, false) == 0 ? 1 : 0;
        for (int i = 0; i < rom->related_count; i++) {
            const RomEntry *part = catalog_find(rom->related[i]);
            if (!part) continue;                 /* not in this page/system */
            if (queue_wiiu_rom(part, false) == 0) queued++;
        }
        if (queued > 1)
            ui_status("Queued %d titles (game + update/DLC)", queued);
        if (run_now && queued > 0) {
            g_view = APP_VIEW_DOWNLOADS;
            run_download_queue();
        }
        return;
    }

    DownloadEntry *e = downloads_upsert_gc(&g_downloads, rom);
    if (!e) { ui_error("Download list full"); return; }
    downloads_save(&g_downloads);
    if (run_now) {
        g_dl_sel = dl_group_index(rom->rom_id);
        g_view = APP_VIEW_DOWNLOADS;
        run_active_download(e);
        scan_local();
    } else {
        ui_status("Queued: %s", e->name);
    }
}

/* ---- view rendering ---- */

/* System tag text + colour for list rows and detail pills. */
static const char *sys_label(const char *sys) {
    if (!strcasecmp(sys, "GC"))   return "GC";
    if (!strcasecmp(sys, "WII"))  return "Wii";
    if (!strcasecmp(sys, "WIIU")) return "Wii U";
    return sys;
}

static uint32_t sys_color(const char *sys) {
    if (!strcasecmp(sys, "GC"))   return HEX_GC;
    if (!strcasecmp(sys, "WII"))  return HEX_WII;
    if (!strcasecmp(sys, "WIIU")) return HEX_WIIU;
    return HEX_DIM;
}

/* Aggregate queue state of every file belonging to ``rom_id``.  Returns false
 * when the title is not queued at all. */
static bool dl_title_status(const char *rom_id, DownloadStatus *out) {
    bool found = false;
    for (int i = 0; i < g_downloads.count; i++) {
        const DownloadEntry *e = &g_downloads.items[i];
        const char *gid = e->rom_id[0] ? e->rom_id : e->key;
        if (strcmp(gid, rom_id) != 0) continue;
        if (!found || dl_status_prio(e->status) > dl_status_prio(*out))
            *out = e->status;
        found = true;
    }
    return found;
}

static const char *dl_status_label(DownloadStatus s) {
    switch (s) {
        case DL_STATUS_ACTIVE:    return "Downloading";
        case DL_STATUS_COMPLETED: return "Done";
        case DL_STATUS_PAUSED:    return "Paused";
        case DL_STATUS_ERROR:     return "Error";
        case DL_STATUS_QUEUED:    return "Queued";
    }
    return "?";
}

static uint32_t dl_status_color(DownloadStatus s) {
    switch (s) {
        case DL_STATUS_ACTIVE:    return HEX_ACCENT;
        case DL_STATUS_COMPLETED: return HEX_OK;
        case DL_STATUS_PAUSED:    return HEX_WARN;
        case DL_STATUS_ERROR:     return HEX_ERR;
        default:                  return HEX_INFO;
    }
}

static UiIcon dl_status_icon(DownloadStatus s) {
    switch (s) {
        case DL_STATUS_ACTIVE:    return UI_ICON_DOWN;
        case DL_STATUS_COMPLETED: return UI_ICON_OK;
        case DL_STATUS_ERROR:     return UI_ICON_ALERT;
        default:                  return UI_ICON_DOT;
    }
}

static const char *content_label(const char *t) {
    if (!strcasecmp(t, "update")) return "Update";
    if (!strcasecmp(t, "dlc"))    return "DLC";
    if (!strcasecmp(t, "demo"))   return "Demo";
    return NULL;
}

/* UTF-8 bullet for inline separators. */
#define BULLET "\xE2\x80\xA2"

static void draw_roms(void) {
    static const char *const tabs[] = { "GC", "Wii", "Wii U" };
    int active = !strcmp(g_cat_system, "WII") ? 1 : !strcmp(g_cat_system, "WIIU") ? 2 : 0;
    char right[32] = "";
    if (g_catalog.count > 0)
        snprintf(right, sizeof(right), "%d / %d", g_rom_sel + 1, g_catalog.count);
    ui_list_panel_tabs(tabs, 3, active, right);

    if (g_catalog.count == 0) {
        char title[64];
        if (g_catalog_loading) {
            snprintf(title, sizeof(title), "Loading the %s catalog...", sys_label(g_cat_system));
            ui_list_empty(title, "Waiting for the server. B cancels.");
        } else {
            snprintf(title, sizeof(title), "%s catalog not loaded", sys_label(g_cat_system));
            ui_list_empty(g_catalog.last_error[0] ? "Could not load the catalog" : title,
                          g_catalog.last_error[0] ? g_catalog.last_error
                              : "Press A to fetch it from the server. MINUS switches system.");
        }
        ui_detail_begin("Game Catalog");
        ui_detail_note(HEX_DIM, "Games on your GameSync server. GameCube games install "
                       "for Nintendont, Wii games as split WBFS for USB Loader GX / "
                       "WiiFlow, Wii U games as WUP folders for installing.");
        ui_detail_gap(6);
        ui_detail_action("A", "Fetch catalog");
        ui_detail_action("-", "Switch system");
        return;
    }

    int vis = ui_list_visible();
    for (int i = 0; i < vis; i++) {
        int idx = g_rom_scroll + i;
        if (idx >= g_catalog.count) break;
        const RomEntry *r = &g_catalog.items[idx];
        char sz[16];
        human_size(r->size, sz, sizeof(sz));
        DownloadStatus st;
        bool queued = dl_title_status(r->rom_id, &st);
        UiRow row = { .text = r->name, .right = sz, .permille = -1 };
        const char *ct = content_label(r->content_type);
        if (ct) { row.tag = ct; row.tag_color = HEX_WARN; }
        if (queued) {
            row.icon = dl_status_icon(st);
            row.icon_color = dl_status_color(st);
            row.badge = dl_status_label(st);
            row.badge_color = dl_status_color(st);
        } else {
            row.icon = UI_ICON_DOT;
            row.icon_color = HEX_MUTED;
        }
        ui_list_row(i, idx == g_rom_sel, &row);
    }
    ui_list_scrollbar(g_rom_scroll, vis, g_catalog.count);

    /* Detail for the selection. */
    const RomEntry *r = &g_catalog.items[g_rom_sel < g_catalog.count ? g_rom_sel : 0];
    const char *sys = r->system[0] ? r->system : g_cat_system;
    char sz[16], dir[SAVE_DIR_LEN];
    human_size(r->size, sz, sizeof(sz));
    ui_detail_begin(r->name);
    ui_detail_pill(sys_label(sys), sys_color(sys));
    ui_detail_pill(sz, HEX_DIM);
    if (content_label(r->content_type)) ui_detail_pill(content_label(r->content_type), HEX_WARN);
    if (r->filename[0]) ui_detail_field("File", "%s", r->filename);

    if (!strcmp(g_cat_system, "WII")) {
        ui_detail_field("Installs as", "split WBFS (USB Loader GX / WiiFlow)");
        ui_detail_field("Goes to", "%s", roms_wbfs_dir(dir, sizeof(dir)));
    } else if (!strcmp(g_cat_system, "WIIU")) {
        ui_detail_field("Installs as", "%s", r->is_bundle ? "WUP folder, then MCP install"
                                                          : "not a WUP folder");
        ui_detail_field("Goes to", "%s", roms_install_dir(dir, sizeof(dir)));
        if (r->related_count > 0)
            ui_detail_field("Also queues", "%d update / DLC title(s)", r->related_count);
    } else {
        ui_detail_field("Installs as", "Nintendont game.iso");
        ui_detail_field("Goes to", "%s", roms_games_dir(dir, sizeof(dir)));
    }
    DownloadStatus st;
    if (dl_title_status(r->rom_id, &st))
        ui_detail_status(dl_status_icon(st), dl_status_color(st), dl_status_label(st),
                         "In the download queue");
    ui_detail_gap(4);
    ui_detail_action("A", "Download now");
    ui_detail_action("X", "Add to queue");
}

static void draw_local(void) {
    char right[48];
    snprintf(right, sizeof(right), "%d game%s  " BULLET "  %s", g_local.count,
             g_local.count == 1 ? "" : "s", roms_storage_is_usb() ? "USB" : "SD card");
    ui_list_panel("Installed games", right);

    if (g_local.count == 0) {
        ui_list_empty("No installed games",
                      g_local.last_error[0] ? g_local.last_error
                          : "Download games from the Catalog view. Y rescans.");
        ui_detail_begin("Installed Games");
        ui_detail_note(HEX_DIM, "GameCube and Wii games downloaded to this console, and "
                       "Wii U WUP folders waiting to be installed.");
        ui_detail_gap(6);
        ui_detail_action("Y", "Rescan");
        return;
    }

    int vis = ui_list_visible();
    for (int i = 0; i < vis; i++) {
        int idx = g_loc_scroll + i;
        if (idx >= g_local.count) break;
        const LocalRom *r = &g_local.items[idx];
        char sz[16];
        human_size(r->size, sz, sizeof(sz));
        /* WUP folders are not sized by the scan; don't claim "0 B". */
        UiRow row = {
            .tag = sys_label(r->system), .tag_color = sys_color(r->system),
            .text = r->name, .right = r->size ? sz : "folder", .permille = -1,
        };
        ui_list_row(i, idx == g_loc_sel, &row);
    }
    ui_list_scrollbar(g_loc_scroll, vis, g_local.count);

    const LocalRom *r = &g_local.items[g_loc_sel < g_local.count ? g_loc_sel : 0];
    char sz[16];
    human_size(r->size, sz, sizeof(sz));
    bool wiiu = !strcmp(r->system, "WIIU");
    ui_detail_begin(r->name);
    ui_detail_pill(sys_label(r->system), sys_color(r->system));
    if (r->size) ui_detail_pill(sz, HEX_DIM);
    if (wiiu && r->title_id[0]) ui_detail_field("Title ID", "%s", r->title_id);
    ui_detail_field(wiiu ? "Folder" : "File", "%s", r->filename);
    ui_detail_field("Path", "%s", r->path);
    ui_detail_gap(4);
    if (wiiu) {
        ui_detail_note(HEX_DIM, "Installs with any update / DLC staged beside it: base "
                       "game first, then update, then DLC.");
        ui_detail_action("A", !strcasecmp(g_state.install_target, "usb")
                                  ? "Install to USB" : "Install to NAND");
    } else {
        ui_detail_note(HEX_DIM, "%s", !strcmp(r->system, "GC")
                       ? "Loaded by Nintendont."
                       : "Loaded by USB Loader GX / WiiFlow.");
    }
    ui_detail_action("X", "Delete from storage");
}

/* ---- Wii U title install (MCP) ---- */

static void install_progress_cb(const InstallProgress *p) {
    if (!p) return;
    g_busy.done  = p->size_done;
    g_busy.total = p->size_total;
    if (p->contents_total)
        snprintf(g_busy_detail, sizeof(g_busy_detail), "MCP install  " BULLET "  content %u of %u",
                 (unsigned)p->contents_done, (unsigned)p->contents_total);
    if (ui_consume_repaint_request()) redraw();
    else redraw_throttled();
}

/* Wii U install order: base game, then update, then DLC.  MCP rejects an
 * update or DLC whose base game is not on the console yet, so a set has to
 * go in sequence. */
static int install_rank(const char *title_id) {
    if (!title_id || strlen(title_id) < 8) return 9;
    if (!strncasecmp(title_id, "00050000", 8)) return 0;   /* game   */
    if (!strncasecmp(title_id, "0005000E", 8)) return 1;   /* update */
    if (!strncasecmp(title_id, "0005000C", 8)) return 2;   /* DLC    */
    if (!strncasecmp(title_id, "00050002", 8)) return 3;   /* demo   */
    return 9;
}

/* Every staged WUP folder belonging to the same game as ``title_id`` — i.e.
 * sharing its low word — sorted into install order. */
static int collect_install_set(const char *title_id, int *idx_out, int max) {
    int n = 0;
    if (!title_id || !title_id[0]) return 0;
    const char *low = title_id + 8;

    for (int i = 0; i < g_local.count && n < max; i++) {
        if (strcmp(g_local.items[i].system, "WIIU") != 0) continue;
        const char *tid = g_local.items[i].title_id;
        if (strlen(tid) != 16 || strcasecmp(tid + 8, low) != 0) continue;
        idx_out[n++] = i;
    }

    /* Insertion sort by install rank — n is <= 4 in practice. */
    for (int a = 1; a < n; a++) {
        int key = idx_out[a];
        int rk = install_rank(g_local.items[key].title_id), b = a - 1;
        while (b >= 0) {
            if (install_rank(g_local.items[idx_out[b]].title_id) <= rk) break;
            idx_out[b + 1] = idx_out[b];
            b--;
        }
        idx_out[b + 1] = key;
    }
    return n;
}

static void install_selected_local(void) {
    if (g_local.count == 0 || g_loc_sel >= g_local.count) return;
    LocalRom *r = &g_local.items[g_loc_sel];

    if (strcmp(r->system, "WIIU") != 0) {
        ui_error("%s games are loaded by Nintendont / a USB loader, not installed",
                 r->system);
        return;
    }
    if (!roms_is_wup_dir(r->path)) {
        ui_error("%s has no title.tmd — download incomplete?", r->name);
        return;
    }

    /* Pull in the update / DLC staged alongside this game so they install in
     * the right order without the user having to know there is one. */
    int set[WIIU_RELATED_MAX + 1];
    int count = collect_install_set(r->title_id, set,
                                    (int)(sizeof(set) / sizeof(set[0])));
    if (count == 0) { set[0] = g_loc_sel; count = 1; }

    bool to_usb = !strcasecmp(g_state.install_target, "usb");

    /* Do NOT gate the install on g_state.usb_mounted.  That flag is our own
     * libmocha attach-mount of storage_usb01, used only for *reading* saves —
     * MCP installs to the WFS USB through IOSU and does not need our mount at
     * all.  A failed (or simply not-yet-attempted) Mocha probe was blocking
     * perfectly good USB installs here, which is why the install step never
     * ran.  MCP_InstallSetTargetDevice reports a genuinely missing USB with a
     * clear message, so let it be the authority.  Re-probe once so the flag
     * and the UI label reflect reality, but proceed regardless. */
    if (to_usb && !g_state.usb_mounted)
        natives_mount_usb_wfs(&g_state);

    if (count > 1) {
        if (!confirm("Install %s and %d related title(s) to %s?\n\n"
                     "Base game first, then update, then DLC.",
                     r->name, count - 1,
                     to_usb ? "USB (Wii U drive)" : "NAND (MLC)"))
            return;
    } else if (!confirm("Install %s to %s?\n\nThis writes to the console's storage.",
                        r->name, to_usb ? "USB (Wii U drive)" : "NAND (MLC)")) {
        return;
    }

    int done = 0;
    for (int i = 0; i < count; i++) {
        LocalRom *part = &g_local.items[set[i]];
        char title[48];
        snprintf(title, sizeof(title), count > 1 ? "Installing %d of %d" : "Installing",
                 i + 1, count);
        busy_begin(BUSY_STATIC, title, part->name,
                   to_usb ? "MCP install to USB" : "MCP install to NAND");
        g_busy.bytes = true;
        ui_status("Installing %d/%d: %s", i + 1, count, part->name);
        redraw();

        InstallProgress prog;
        int rc = install_wup_folder(part->path, g_state.install_target,
                                    &prog, install_progress_cb);
        busy_end();
        if (rc != 0) {
            /* Stop the chain: an update on top of a base game that failed to
             * install is worse than nothing. */
            ui_error("%s: %s", part->name, prog.message);
            redraw();
            return;
        }
        done++;
    }
    ui_status("Installed %d title(s) to %s", done, to_usb ? "USB" : "NAND");
    redraw();
}

static void draw_downloads(void) {
    dl_build_groups();
    char right[32] = "";
    if (g_dl_group_count > 0)
        snprintf(right, sizeof(right), "%d title%s", g_dl_group_count,
                 g_dl_group_count == 1 ? "" : "s");
    ui_list_panel("Download queue", right);

    if (g_dl_group_count == 0) {
        ui_list_empty("The queue is empty",
                      "Queue games from the Catalog view with X, or download one right "
                      "away with A.");
        ui_detail_begin("Downloads");
        ui_detail_note(HEX_DIM, "Downloads resume where they stopped. A Wii U game is one "
                       "row here even though it is many files.");
        return;
    }

    int vis = ui_list_visible();
    for (int i = 0; i < vis; i++) {
        int idx = g_dl_scroll + i;
        if (idx >= g_dl_group_count) break;
        const DlGroup *g = &g_dl_groups[idx];
        char right_txt[24];
        if (g->files > 1)
            snprintf(right_txt, sizeof(right_txt), "%d/%d files", g->files_done, g->files);
        else
            human_size(g->total, right_txt, sizeof(right_txt));
        UiRow row = {
            .icon = dl_status_icon(g->status), .icon_color = dl_status_color(g->status),
            .text = g->name, .right = right_txt,
            .badge = dl_status_label(g->status), .badge_color = dl_status_color(g->status),
            .permille = -1,
        };
        if (g->total && g->status != DL_STATUS_COMPLETED && g->done > 0)
            row.permille = (int)(g->done * 1000 / g->total);
        ui_list_row(i, idx == g_dl_sel, &row);
    }
    ui_list_scrollbar(g_dl_scroll, vis, g_dl_group_count);

    const DlGroup *g = &g_dl_groups[g_dl_sel < g_dl_group_count ? g_dl_sel : 0];
    ui_detail_begin(g->name);
    ui_detail_pill(dl_status_label(g->status), dl_status_color(g->status));
    if (g->files > 1) {
        char f[24];
        snprintf(f, sizeof(f), "%d files", g->files);
        ui_detail_pill(f, HEX_DIM);
    }
    ui_detail_gap(4);
    ui_detail_progress(g->done, g->total, dl_status_color(g->status));
    if (g->files > 1) ui_detail_field("Files done", "%d of %d", g->files_done, g->files);
    if (g->status == DL_STATUS_ACTIVE && g_active_bps) {
        char spd[24];
        human_size(g_active_bps, spd, sizeof(spd));
        ui_detail_field("Speed", "%s/s", spd);
    }
    if (g->status == DL_STATUS_ERROR)
        ui_detail_note(HEX_ERR, "The last attempt failed. A retries from where it stopped.");
    ui_detail_gap(4);
    ui_detail_action("A", "Start / resume");
    ui_detail_action("Y", "Run the whole queue");
    ui_detail_action("X", "Remove from queue");
}

/* ---- GC memory-card images ---- */

static void fill_card_names(void) {
    for (int i = 0; i < g_card.count; i++) {
        for (int j = 0; j < g_server.count; j++) {
            const ServerSave *sv = &g_server.items[j];
            if (strcasecmp(sv->title_id, g_card.saves[i].title_id) != 0) continue;
            if (sv->name[0] && strcasecmp(sv->name, sv->title_id) != 0)
                strncpy(g_card.saves[i].name, sv->name,
                        sizeof(g_card.saves[i].name) - 1);
            break;
        }
    }
}

static void open_card(int idx) {
    if (g_cards.count == 0) { g_card.count = 0; return; }
    if (idx < 0) idx = 0;
    if (idx >= g_cards.count) idx = g_cards.count - 1;
    g_card_active = idx;
    g_gc_sel = 0;
    g_gc_scroll = 0;
    if (!vmcfs_open(&g_card, g_cards.items[idx].path)) {
        ui_error("Open %s failed: %s", g_cards.items[idx].filename, g_card.last_error);
        g_card.count = 0;
        return;
    }
    fill_card_names();
    ui_status("%s: %d save(s)", g_card.filename, g_card.count);
}

static void scan_cards(void) {
    if (!g_state.sd_ready) { ui_error("SD not ready"); return; }
    ui_status("Scanning GC card images...");
    redraw();
    gcsaves_scan_cards(&g_state, &g_cards);
    if (g_cards.count == 0) { g_card.count = 0; ui_status("%s", g_cards.last_error); }
    else open_card(g_card_active);
    redraw();
}

static void mark_server_local(void) {
    for (int i = 0; i < g_server.count; i++) {
        ServerSave *s = &g_server.items[i];
        s->local = false;
        for (int j = 0; j < g_card.count && !s->local; j++)
            if (strcasecmp(g_card.saves[j].title_id, s->title_id) == 0) s->local = true;
    }
}

static void fetch_server(void) {
    if (!network_is_ready(&g_state)) { ui_error("Network not ready"); return; }
    ui_status("Fetching server GC saves...");
    g_server_loading = true;
    redraw();
    gcsaves_fetch_server(&g_state, g_scratch, sizeof(g_scratch), &g_server);
    g_server_loading = false;
    fill_card_names();
    mark_server_local();
    /* An error leaves the view unloaded so the next entry retries. */
    g_server_loaded = g_server.count > 0 || !g_server.last_error[0];
    if (g_server.count > 0) ui_status("Server: %d GC save(s)", g_server.count);
    else ui_error("%s", g_server.last_error);
    redraw();
}

static const ServerSave *server_save_find(const char *title_id) {
    for (int i = 0; i < g_server.count; i++)
        if (strcasecmp(g_server.items[i].title_id, title_id) == 0)
            return &g_server.items[i];
    return NULL;
}

static void draw_gccards(void) {
    char title[160], right[48] = "";
    if (g_cards.count > 0) {
        snprintf(title, sizeof(title), "Card %d/%d  " BULLET "  %s",
                 g_card_active + 1, g_cards.count, g_card.filename);
        snprintf(right, sizeof(right), "%d save%s", g_card.count,
                 g_card.count == 1 ? "" : "s");
    } else {
        snprintf(title, sizeof(title), "Nintendont memory cards");
    }
    ui_list_panel(title, right);

    if (g_cards.count == 0 || g_card.count == 0) {
        const char *why = g_cards.count == 0
            ? (g_cards.last_error[0] ? g_cards.last_error : "No card images found.")
            : (g_card.last_error[0] ? g_card.last_error : "No saves in this image.");
        ui_list_empty(g_cards.count == 0 ? "No memory cards" : "Empty memory card", why);
        ui_detail_begin("GC Memory Cards");
        ui_detail_note(HEX_DIM, "Saves inside Nintendont's virtual memory cards, synced "
                       "one game at a time with the GameCube client, Dolphin and Android.");
        ui_detail_field("Folder", "%s", g_state.nin_saves_dir);
        ui_detail_gap(6);
        ui_detail_action("X", "Rescan");
        if (g_cards.count > 1) ui_detail_action("R", "Next card");
        return;
    }

    int vis = ui_list_visible();
    for (int i = 0; i < vis; i++) {
        int idx = g_gc_scroll + i;
        if (idx >= g_card.count) break;
        const VmcfsSave *s = &g_card.saves[idx];
        char code[12], blk[16];
        snprintf(code, sizeof(code), "%s%s", s->gamecode, s->company);
        snprintf(blk, sizeof(blk), "%d blk", s->blocks);
        bool on_server = server_save_find(s->title_id) != NULL;
        UiRow row = {
            .icon = on_server ? UI_ICON_OK : UI_ICON_DOT,
            .icon_color = on_server ? HEX_OK : HEX_MUTED,
            .tag = code, .tag_color = HEX_GC,
            .text = s->name[0] ? s->name : s->filename,
            .right = blk, .permille = -1,
        };
        ui_list_row(i, idx == g_gc_sel, &row);
    }
    ui_list_scrollbar(g_gc_scroll, vis, g_card.count);

    const VmcfsSave *s = &g_card.saves[g_gc_sel < g_card.count ? g_gc_sel : 0];
    const ServerSave *sv = server_save_find(s->title_id);
    ui_detail_begin(s->name[0] ? s->name : s->filename);
    ui_detail_pill("GC", HEX_GC);
    char blk[16];
    snprintf(blk, sizeof(blk), "%d blocks", s->blocks);
    ui_detail_pill(blk, HEX_DIM);
    ui_detail_field("Title ID", "%s", s->title_id);
    ui_detail_field("Save file", "%s", s->filename);
    ui_detail_field("Card", "%s", g_card.filename);
    if (sv)
        ui_detail_status(UI_ICON_OK, HEX_OK, "On the server", "Y restores that copy here");
    else if (g_server_loaded)
        ui_detail_status(UI_ICON_UP, HEX_WARN, "Not on the server", "A uploads it");
    else
        ui_detail_status(UI_ICON_UNKNOWN, HEX_DIM, "Server not checked",
                         "Open the GC Server view to compare");
    ui_detail_action("A", "Upload to server");
    ui_detail_action("Y", "Restore from server");
    ui_detail_action("L", "Import the whole card");
}

/* "2026-09-30 21:04" (UTC) from a unix timestamp. */
static void format_time(uint32_t ts, char *out, size_t n) {
    if (!ts) { snprintf(out, n, "unknown"); return; }
    time_t t = (time_t)ts;
    struct tm tm;
    if (!gmtime_r(&t, &tm)) { snprintf(out, n, "%u", (unsigned)ts); return; }
    snprintf(out, n, "%04d-%02d-%02d %02d:%02d UTC", tm.tm_year + 1900, tm.tm_mon + 1,
             tm.tm_mday, tm.tm_hour, tm.tm_min);
}

static void draw_server(void) {
    char right[32] = "";
    if (g_server.count > 0)
        snprintf(right, sizeof(right), "%d save%s", g_server.count,
                 g_server.count == 1 ? "" : "s");
    ui_list_panel("GameCube saves on the server", right);

    if (g_server.count == 0) {
        const char *why = g_server.last_error[0] ? g_server.last_error
                        : g_server_loaded        ? "No GC saves on the server yet."
                        : "Not fetched yet. Press X to load the list.";
        if (g_server_loading)
            ui_list_empty("Loading server saves...", "Waiting for the server. B cancels.");
        else
            ui_list_empty(g_server.last_error[0] ? "Could not load the list"
                                                 : "No server saves", why);
        ui_detail_begin("Server GC Saves");
        ui_detail_note(HEX_DIM, "Every GameCube save the server holds, from any device. "
                       "Restore one into the open memory card.");
        ui_detail_gap(6);
        ui_detail_action("X", "Refresh");
        return;
    }

    int vis = ui_list_visible();
    for (int i = 0; i < vis; i++) {
        int idx = g_sv_scroll + i;
        if (idx >= g_server.count) break;
        const ServerSave *s = &g_server.items[idx];
        UiRow row = {
            .icon = s->local ? UI_ICON_OK : UI_ICON_DOWN,
            .icon_color = s->local ? HEX_OK : HEX_INFO,
            .text = s->name[0] ? s->name : s->title_id,
            .right = s->title_id,
            .badge = s->local ? "On card" : NULL, .badge_color = HEX_OK,
            .permille = -1,
        };
        ui_list_row(i, idx == g_sv_sel, &row);
    }
    ui_list_scrollbar(g_sv_scroll, vis, g_server.count);

    const ServerSave *s = &g_server.items[g_sv_sel < g_server.count ? g_sv_sel : 0];
    char when[40];
    format_time(s->timestamp, when, sizeof(when));
    ui_detail_begin(s->name[0] ? s->name : s->title_id);
    ui_detail_pill("GC", HEX_GC);
    if (s->local) ui_detail_pill("On card", HEX_OK);
    ui_detail_field("Title ID", "%s", s->title_id);
    ui_detail_field("Uploaded", "%s", when);
    ui_detail_field("Target", "%s", g_cards.count ? g_card.filename : "no card open");
    ui_detail_gap(4);
    ui_detail_action("A", "Restore into the card");
    ui_detail_action("Y", "Pull every save as .gci");
    ui_detail_action("X", "Refresh");
}

static void upload_gc_save(void) {
    if (!network_is_ready(&g_state)) { ui_error("Network not ready"); return; }
    if (g_card.count == 0 || g_gc_sel >= g_card.count) return;
    const VmcfsSave *s = &g_card.saves[g_gc_sel];
    if (!confirm("Upload %s\nfrom %s to the server?", s->title_id, g_card.filename)) return;
    ui_status("Uploading %s...", s->title_id);
    redraw();
    char msg[160];
    int rc = gcsaves_upload_save(&g_state, &g_card, g_gc_sel, msg, sizeof(msg));
    if (rc < 0) ui_error("%s", msg); else ui_status("%s", msg);
    redraw();
}

static void restore_gc_save(const char *tid) {
    if (!network_is_ready(&g_state)) { ui_error("Network not ready"); return; }
    if (g_cards.count == 0) { ui_error("No card image open"); return; }
    if (!confirm_danger("Restore save", "Restore %s from the server into %s?\n\nOverwrites that game's save in the image.",
                 tid, g_card.filename))
        return;
    ui_status("Restoring %s...", tid);
    redraw();
    char msg[160];
    int rc = gcsaves_restore_save(&g_state, &g_card, tid, msg, sizeof(msg));
    fill_card_names();
    mark_server_local();
    if (rc < 0) ui_error("%s", msg); else ui_status("%s", msg);
    redraw();
}

static void import_whole_card(void) {
    if (!network_is_ready(&g_state)) { ui_error("Network not ready"); return; }
    if (g_cards.count == 0) return;
    const SaveVmc *v = &g_cards.items[g_card_active];
    if (!confirm("Import ALL saves from\n%s\nto the server (split per game)?", v->filename))
        return;
    ui_status("Importing %s...", v->filename);
    redraw();
    char msg[160];
    int rc = gcsaves_import_card(&g_state, v, msg, sizeof(msg));
    if (rc < 0) ui_error("%s", msg);
    else { ui_status("%s", msg); fetch_server(); }
    redraw();
}

/* ---- native (vWii / Wii U) smart sync ---- */

static SaveTitleList *native_list(AppView v) {
    return v == APP_VIEW_VWII ? &g_vwii : &g_wiiu;
}

static int *native_sel(AppView v)    { return v == APP_VIEW_VWII ? &g_vw_sel : &g_wu_sel; }
static int *native_scroll(AppView v) { return v == APP_VIEW_VWII ? &g_vw_scroll : &g_wu_scroll; }

/* Resolve display names for a scanned list via POST /titles/names.
 *
 * vWii saves have no on-console name source at all (the id is just the ASCII
 * game code out of the NAND title id), so without this the list reads
 * "WII_RB4E".  The server resolves WII_<code> from the Wii DAT.  Wii U titles
 * already got a name from their meta.xml; only fill the ones that missed. */
static void fill_native_names(SaveTitleList *list) {
    if (!list || list->title_count == 0) return;
    if (!network_is_ready(&g_state)) return;

    static char (*ids)[TITLE_ID_LEN];
    static char (*names)[SAVE_NAME_MAX];
    if (!ids)   ids   = calloc(SAVE_MAX_TITLES, TITLE_ID_LEN);
    if (!names) names = calloc(SAVE_MAX_TITLES, SAVE_NAME_MAX);
    if (!ids || !names) return;

    int n = 0;
    int index[SAVE_MAX_TITLES];
    for (int i = 0; i < list->title_count && n < SAVE_MAX_TITLES; i++) {
        if (list->titles[i].name[0]) continue;   /* meta.xml already won */
        snprintf(ids[n], TITLE_ID_LEN, "%s", list->titles[i].title_id);
        index[n] = i;
        n++;
    }
    if (n == 0) return;

    if (network_fetch_names(&g_state, (const char (*)[TITLE_ID_LEN])ids,
                            n, names) != 0)
        return;

    for (int k = 0; k < n; k++) {
        if (!names[k][0]) continue;
        snprintf(list->titles[index[k]].name, SAVE_NAME_MAX, "%s", names[k]);
    }
}

/* Boot-time variant: no status line, no redraw — the boot screen owns the
 * display until the main loop starts. */
static void scan_natives_quiet(void) {
    if (g_state.sync_vwii) vwiisaves_scan(&g_state, &g_vwii);
    else savetree_free_list(&g_vwii);
    if (g_state.sync_wiiu) wiiusaves_scan(&g_state, &g_wiiu);
    else savetree_free_list(&g_wiiu);
    fill_native_names(&g_vwii);
    fill_native_names(&g_wiiu);
    g_plan_valid = false;
}

static void scan_natives(void) {
    if (g_state.sync_vwii) {
        ui_status("Scanning vWii NAND saves...");
        redraw();
        vwiisaves_scan(&g_state, &g_vwii);
    } else {
        savetree_free_list(&g_vwii);
        snprintf(g_vwii.last_error, sizeof(g_vwii.last_error), "vWii sync disabled in Config");
    }
    if (g_state.sync_wiiu) {
        ui_status("Scanning Wii U saves...");
        redraw();
        wiiusaves_scan(&g_state, &g_wiiu);
    } else {
        savetree_free_list(&g_wiiu);
        snprintf(g_wiiu.last_error, sizeof(g_wiiu.last_error), "Wii U sync disabled in Config");
    }
    if (network_is_ready(&g_state)) {
        ui_status("Resolving game names...");
        redraw();
        fill_native_names(&g_vwii);
        fill_native_names(&g_wiiu);
    }
    g_plan_valid = false;
    ui_status("vWii: %d save(s), Wii U: %d save(s)",
              g_vwii.title_count, g_wiiu.title_count);
    redraw();
}

/* One combined plan covering both families — the server accepts a mixed
 * title list and the platforms filter keeps other consoles' saves out of the
 * server_only bucket. */
static void compute_plan_ex(bool quiet) {
    if (!network_is_ready(&g_state)) {
        if (!quiet) ui_error("Network not ready");
        return;
    }

    static SaveTitleList merged;
    merged.title_count = 0;
    merged.last_error[0] = '\0';
    /* Titles whose tree could not be read are excluded: hashing a partial
     * read would tell the server the save changed and could overwrite a good
     * copy with an incomplete one. */
    for (int i = 0; i < g_vwii.title_count && merged.title_count < SAVE_MAX_TITLES; i++)
        if (!g_vwii.titles[i].error[0])
            merged.titles[merged.title_count++] = g_vwii.titles[i];
    for (int i = 0; i < g_wiiu.title_count && merged.title_count < SAVE_MAX_TITLES; i++)
        if (!g_wiiu.titles[i].error[0])
            merged.titles[merged.title_count++] = g_wiiu.titles[i];

    const char *platforms[2];
    int np = 0;
    if (g_state.sync_vwii) platforms[np++] = "WII";
    if (g_state.sync_wiiu) platforms[np++] = "WIIU";
    if (np == 0) { ui_error("Both native sync families are disabled"); return; }

    /* A quiet refresh must not leave its progress line in the banner: put
     * back whatever it said before (usually the last action's outcome). */
    char prev[200];
    bool prev_err = ui_status_is_error();
    snprintf(prev, sizeof(prev), "%s", ui_status_text());
    ui_status("Hashing saves and asking for a plan...");
    redraw();

    sync_plan_free(&g_plan);
    /* The merged list shares SaveFile pointers with g_vwii / g_wiiu; it is
     * only read here, and never freed, so ownership stays with the originals. */
    if (sync_compute_plan(&g_state, &merged, platforms, np, &g_plan) != 0) {
        g_plan_valid = false;
        ui_error("%s", sync_last_message());
    } else {
        g_plan_valid = true;
        if (quiet) {
            if (prev_err) ui_error("%s", prev);
            else          ui_status("%s", prev);
        } else
            ui_status("Plan: %d up, %d down, %d new, %d conflict, %d ok",
                      g_plan.upload_count, g_plan.download_count,
                      g_plan.server_only_count, g_plan.conflict_count,
                      g_plan.up_to_date_count);
    }
    redraw();
}

static void compute_plan(void) { compute_plan_ex(false); }

static void sync_progress(const char *msg, int done, int total, void *user) {
    (void)user;
    snprintf(g_busy_name, sizeof(g_busy_name), "%s", msg);
    g_busy.done  = (uint64_t)(done < 0 ? 0 : done);
    g_busy.total = (uint64_t)(total < 0 ? 0 : total);
    ui_status("%s [%d/%d]", msg, done, total);
    redraw();
}

static void sync_all_natives(void) {
    if (!g_plan_valid) { ui_error("Compute a plan first (MINUS)"); return; }
    if (!confirm("Run the full sync plan?\n%d upload, %d download, %d new.\n"
                 "Conflicts are skipped; restores back up to SD first.",
                 g_plan.upload_count, g_plan.download_count, g_plan.server_only_count))
        return;

    /* Downloads need a list to re-hash against; pass the view's own list plus
     * the other family so cross-family entries still resolve. */
    static SaveTitleList merged;
    merged.title_count = 0;
    /* Titles whose tree could not be read are excluded: hashing a partial
     * read would tell the server the save changed and could overwrite a good
     * copy with an incomplete one. */
    for (int i = 0; i < g_vwii.title_count && merged.title_count < SAVE_MAX_TITLES; i++)
        if (!g_vwii.titles[i].error[0])
            merged.titles[merged.title_count++] = g_vwii.titles[i];
    for (int i = 0; i < g_wiiu.title_count && merged.title_count < SAVE_MAX_TITLES; i++)
        if (!g_wiiu.titles[i].error[0])
            merged.titles[merged.title_count++] = g_wiiu.titles[i];

    SyncSummary sum;
    busy_begin(BUSY_STATIC, "Syncing saves", "Starting...", "Conflicts are skipped");
    sync_run_all(&g_state, &merged, &g_plan, sync_progress, NULL, &sum);
    busy_end();
    scan_natives();
    compute_plan_ex(true);
    ui_status("Sync done: %d up, %d down, %d failed, %d conflict",
              sum.uploaded, sum.downloaded,
              sum.upload_failed + sum.download_failed, sum.conflicts);
    redraw();
}

static void native_action(AppView view, int action) {
    SaveTitleList *list = native_list(view);
    int sel = *native_sel(view);

    /* The selected row may be a local save or a server-only entry appended
     * after them. */
    const char *tid = NULL;
    SaveTitle *local = NULL;
    if (sel < list->title_count) {
        local = &list->titles[sel];
        tid = local->title_id;
    } else if (g_plan_valid) {
        int k = sel - list->title_count;
        if (k >= 0 && k < g_plan.server_only_count) tid = g_plan.server_only_ids[k];
    }
    if (!tid) return;
    if (!network_is_ready(&g_state)) { ui_error("Network not ready"); return; }

    int rc = 0;
    switch (action) {
        case 0:   /* smart */
            if (!g_plan_valid) { ui_error("Compute a plan first (MINUS)"); return; }
            if (!confirm("Sync %s now?", tid)) return;
            busy_begin(BUSY_STATIC, "Syncing", tid, "Smart sync");
            redraw();
            rc = sync_one_smart(&g_state, list, tid, &g_plan);
            break;
        case 1:   /* force upload */
            if (!local) { ui_error("%s has no local save to upload", tid); return; }
            if (!confirm_danger("Force upload", "Upload %s and overwrite the server copy?", tid)) return;
            busy_begin(BUSY_STATIC, "Uploading", tid, "Force upload to the server");
            redraw();
            rc = sync_one_upload_force(&g_state, local);
            break;
        case 2:   /* force download */
            if (!confirm_danger("Force download", "Download %s and overwrite the console save?\n"
                         "A backup is written to SD first.", tid)) return;
            busy_begin(BUSY_STATIC, "Downloading", tid, "Backing up, then restoring the save");
            redraw();
            rc = sync_one_download(&g_state, list, tid);
            break;
        default: return;
    }
    busy_end();
    /* Keep the action's outcome visible, but refresh the plan underneath —
     * dropping it outright made the server-only rows vanish after every
     * action. */
    char outcome[256];
    snprintf(outcome, sizeof(outcome), "%s", sync_last_message());
    g_plan_valid = false;
    compute_plan_ex(true);
    if (rc == 0) ui_status("%s", outcome);
    else         ui_error("%s", outcome);
    redraw();
}

static const char *ts_label(TitleStatus s) {
    switch (s) {
        case TITLE_STATUS_UP_TO_DATE:     return "Synced";
        case TITLE_STATUS_NEEDS_UPLOAD:   return "Upload";
        case TITLE_STATUS_NEEDS_DOWNLOAD: return "Download";
        case TITLE_STATUS_CONFLICT:       return "Conflict";
        case TITLE_STATUS_SERVER_ONLY:    return "Server only";
        default:                          return "Not compared";
    }
}

static uint32_t ts_color(TitleStatus s) {
    switch (s) {
        case TITLE_STATUS_UP_TO_DATE:     return HEX_OK;
        case TITLE_STATUS_NEEDS_UPLOAD:   return HEX_WARN;
        case TITLE_STATUS_NEEDS_DOWNLOAD: return HEX_INFO;
        case TITLE_STATUS_CONFLICT:       return HEX_ERR;
        case TITLE_STATUS_SERVER_ONLY:    return HEX_INFO;
        default:                          return HEX_MUTED;
    }
}

static UiIcon ts_icon(TitleStatus s) {
    switch (s) {
        case TITLE_STATUS_UP_TO_DATE:     return UI_ICON_OK;
        case TITLE_STATUS_NEEDS_UPLOAD:   return UI_ICON_UP;
        case TITLE_STATUS_NEEDS_DOWNLOAD: return UI_ICON_DOWN;
        case TITLE_STATUS_CONFLICT:       return UI_ICON_ALERT;
        case TITLE_STATUS_SERVER_ONLY:    return UI_ICON_PLUS;
        default:                          return UI_ICON_UNKNOWN;
    }
}

static const char *ts_explain(TitleStatus s) {
    switch (s) {
        case TITLE_STATUS_UP_TO_DATE:     return "Console and server match";
        case TITLE_STATUS_NEEDS_UPLOAD:   return "Only this console changed - A uploads";
        case TITLE_STATUS_NEEDS_DOWNLOAD: return "Only the server changed - A downloads";
        case TITLE_STATUS_CONFLICT:       return "Both changed - pick X or Y";
        case TITLE_STATUS_SERVER_ONLY:    return "A downloads it to this console";
        default:                          return "MINUS scans and asks the server";
    }
}

/* Plan totals as a row of pills. */
static void detail_plan_summary(void) {
    if (!g_plan_valid) return;
    char b[32];
    ui_detail_gap(2);
    ui_detail_note(HEX_DIM, "Sync plan (vWii + Wii U)");
    snprintf(b, sizeof(b), "%d upload", g_plan.upload_count);
    ui_detail_pill(b, HEX_WARN);
    snprintf(b, sizeof(b), "%d download", g_plan.download_count);
    ui_detail_pill(b, HEX_INFO);
    snprintf(b, sizeof(b), "%d new", g_plan.server_only_count);
    ui_detail_pill(b, HEX_ACCENT);
    snprintf(b, sizeof(b), "%d conflict", g_plan.conflict_count);
    ui_detail_pill(b, HEX_ERR);
    snprintf(b, sizeof(b), "%d synced", g_plan.up_to_date_count);
    ui_detail_pill(b, HEX_OK);
    ui_detail_gap(0);
}

static void draw_natives(AppView view) {
    SaveTitleList *list = native_list(view);
    int sel = *native_sel(view), scroll = *native_scroll(view);
    bool vwii = view == APP_VIEW_VWII;
    const char *fam = vwii ? "vWii" : "Wii U";
    uint32_t fam_color = vwii ? HEX_WII : HEX_WIIU;

    int extra = g_plan_valid ? g_plan.server_only_count : 0;
    char right[64];
    snprintf(right, sizeof(right), "%d on console  " BULLET "  %d server only",
             list->title_count, extra);
    ui_list_panel(vwii ? "vWii saves (SLC NAND)" : "Wii U saves", right);

    if (list->title_count == 0 && extra == 0) {
        ui_list_empty("No saves found",
                      list->last_error[0] ? list->last_error
                          : "MINUS rescans the console and asks the server for a plan.");
        ui_detail_begin(ui_view_name(view));
        ui_detail_note(HEX_DIM, vwii
                       ? "vWii NAND saves, synced as whole save trees with three-way hash "
                         "smart sync. nocopy/ data stays on the console."
                       : "Wii U saves, per account, synced with three-way hash smart sync.");
        ui_detail_field("libmocha", "%s", g_state.mocha_ok ? "available" : "not available");
        if (g_state.mocha_error[0]) ui_detail_note(HEX_WARN, "%s", g_state.mocha_error);
        detail_plan_summary();
        ui_detail_action("-", "Rescan + plan");
        return;
    }

    int vis = ui_list_visible();
    int total = list->title_count + extra;
    for (int i = 0; i < vis; i++) {
        int idx = scroll + i;
        if (idx >= total) break;
        UiRow row = { .permille = -1 };
        char info[32];
        if (idx < list->title_count) {
            const SaveTitle *t = &list->titles[idx];
            row.text = t->name[0] ? t->name : t->title_id;
            if (t->error[0]) {
                /* An unreadable save is shown with its reason instead of
                 * being hidden, so a missing game is never a silent omission. */
                row.icon = UI_ICON_ALERT;
                row.icon_color = HEX_ERR;
                row.badge = "Unreadable";
                row.badge_color = HEX_ERR;
            } else {
                TitleStatus st = g_plan_valid ? sync_plan_status(&g_plan, t->title_id)
                                              : TITLE_STATUS_UNKNOWN;
                char sz[16];
                human_size(t->total_size, sz, sizeof(sz));
                snprintf(info, sizeof(info), "%d files  %s", t->file_count, sz);
                row.right = info;
                row.icon = ts_icon(st);
                row.icon_color = ts_color(st);
                row.badge = ts_label(st);
                row.badge_color = ts_color(st);
            }
        } else {
            int k = idx - list->title_count;
            row.text = g_plan.server_only_names[k][0] ? g_plan.server_only_names[k]
                                                      : g_plan.server_only_ids[k];
            row.icon = UI_ICON_PLUS;
            row.icon_color = HEX_INFO;
            row.badge = "Server only";
            row.badge_color = HEX_INFO;
            row.text_color = HEX_DIM;
        }
        ui_list_row(i, idx == sel, &row);
    }
    ui_list_scrollbar(scroll, vis, total);

    /* Detail. */
    if (sel < list->title_count) {
        const SaveTitle *t = &list->titles[sel];
        ui_detail_begin(t->name[0] ? t->name : t->title_id);
        ui_detail_pill(fam, fam_color);
        if (t->error[0]) {
            ui_detail_field("Title ID", "%s", t->title_id);
            ui_detail_status(UI_ICON_ALERT, HEX_ERR, "Unreadable", t->error);
            ui_detail_note(HEX_DIM, "Never uploaded while unreadable: a partial read could "
                           "overwrite a good server copy.");
            return;
        }
        TitleStatus st = g_plan_valid ? sync_plan_status(&g_plan, t->title_id)
                                      : TITLE_STATUS_UNKNOWN;
        char sz[16];
        human_size(t->total_size, sz, sizeof(sz));
        ui_detail_pill(sz, HEX_DIM);
        ui_detail_field("Title ID", "%s", t->title_id);
        ui_detail_field("Files", "%d", t->file_count);
        ui_detail_status(ts_icon(st), ts_color(st), ts_label(st), ts_explain(st));
    } else if (g_plan_valid && sel - list->title_count < g_plan.server_only_count) {
        int k = sel - list->title_count;
        ui_detail_begin(g_plan.server_only_names[k][0] ? g_plan.server_only_names[k]
                                                       : g_plan.server_only_ids[k]);
        ui_detail_field("Title ID", "%s", g_plan.server_only_ids[k]);
        ui_detail_status(UI_ICON_PLUS, HEX_INFO, "Only on the server",
                         "A or Y downloads it here");
    } else {
        ui_detail_begin(ui_view_name(view));
    }
    detail_plan_summary();
    ui_detail_action("A", "Smart sync");
    ui_detail_action("L", "Run the whole plan");
}

/* ---- frame ---- */

static const UiHint HINTS_ROMS[] = {
    { "A", "Download" }, { "X", "Queue" }, { "Y", "Refresh" }, { "-", "System" },
    { "LR", "Page" }, { "+", "Quit" },
};
static const UiHint HINTS_LOCAL[] = {
    { "A", "Install" }, { "X", "Delete" }, { "Y", "Rescan" }, { "LR", "Page" },
    { "+", "Quit" },
};
static const UiHint HINTS_DOWNLOADS[] = {
    { "A", "Start" }, { "Y", "Run all" }, { "X", "Remove" }, { "B", "Pause" },
    { "+", "Quit" },
};
static const UiHint HINTS_GCCARDS[] = {
    { "A", "Upload" }, { "Y", "Restore" }, { "R", "Next card" }, { "L", "Import card" },
    { "X", "Rescan" }, { "+", "Quit" },
};
static const UiHint HINTS_SERVER[] = {
    { "A", "Restore" }, { "X", "Refresh" }, { "Y", "Pull all .gci" }, { "LR", "Page" },
    { "+", "Quit" },
};
static const UiHint HINTS_NATIVE[] = {
    { "A", "Sync" }, { "X", "Force upload" }, { "Y", "Force download" },
    { "-", "Rescan" }, { "L", "Sync all" }, { "+", "Quit" },
};
static const UiHint HINTS_CONFIG[] = {
    { "UD", "Select" }, { "LR", "Change" }, { "A", "Edit / save" }, { "+", "Quit" },
};

#define HINTS(a) a, (int)(sizeof(a) / sizeof(a[0]))

static void draw_hints(AppView v) {
    switch (v) {
        case APP_VIEW_ROMS:      ui_draw_footer(HINTS(HINTS_ROMS)); break;
        case APP_VIEW_LOCAL:     ui_draw_footer(HINTS(HINTS_LOCAL)); break;
        case APP_VIEW_DOWNLOADS: ui_draw_footer(HINTS(HINTS_DOWNLOADS)); break;
        case APP_VIEW_GCCARDS:   ui_draw_footer(HINTS(HINTS_GCCARDS)); break;
        case APP_VIEW_SERVER:    ui_draw_footer(HINTS(HINTS_SERVER)); break;
        case APP_VIEW_VWII:
        case APP_VIEW_WIIU:      ui_draw_footer(HINTS(HINTS_NATIVE)); break;
        case APP_VIEW_CONFIG:    ui_draw_footer(HINTS(HINTS_CONFIG)); break;
        default:                 ui_draw_footer(NULL, 0); break;
    }
}

/* The live fields of the progress card for the transfer in flight. */
static void busy_refresh(void) {
    g_busy.title  = g_busy_title;
    g_busy.name   = g_busy_name;
    g_busy.detail = g_busy_detail;
    if (g_busy_kind != BUSY_DOWNLOAD) return;
    g_busy.done  = g_active_done;
    g_busy.total = g_active_total;
    g_busy.bytes = true;
    g_busy.bps   = g_active_bps;
    g_busy.hint_button = "B";
    g_busy.hint_label  = "to pause (resumes later)";
    if (g_wait_secs && g_active_done == 0) {
        static char wait[96];
        snprintf(wait, sizeof(wait), "Waiting for the server (converting?)  %u s",
                 (unsigned)g_wait_secs);
        g_busy.detail = wait;
    }
}

static void draw_frame(void *ctx) {
    (void)ctx;
    ui_draw_background();
    ui_draw_header(&g_state, g_view);
    switch (g_view) {
        case APP_VIEW_ROMS:      draw_roms(); break;
        case APP_VIEW_LOCAL:     draw_local(); break;
        case APP_VIEW_DOWNLOADS: draw_downloads(); break;
        case APP_VIEW_GCCARDS:   draw_gccards(); break;
        case APP_VIEW_SERVER:    draw_server(); break;
        case APP_VIEW_VWII:      draw_natives(APP_VIEW_VWII); break;
        case APP_VIEW_WIIU:      draw_natives(APP_VIEW_WIIU); break;
        case APP_VIEW_CONFIG:    draw_config(); break;
        default: break;
    }
    draw_hints(g_view);

    if (g_busy_kind != BUSY_NONE) ui_progress_card(&g_busy);
    if (g_dialog)
        ui_dialog(g_dialog->title, g_dialog->tone, g_dialog->message,
                  g_dialog->hints, g_dialog->hint_count);
}

static void redraw(void) {
    busy_refresh();
    /* HTTP waits repaint through here; during boot that must keep showing
     * the boot card, not a half-initialised main view. */
    ui_render(g_booting ? draw_boot : draw_frame, NULL);
    g_last_frame_ms = now_ms();
}

/* Lazy load: the first time a networked view is opened, populate it.
 * Boot stays offline (an unreachable server must never stall startup), but
 * the user should not have to know that a view needs a manual refresh. */
static void enter_view(void) {
    if (!network_is_ready(&g_state)) return;
    if (g_view == APP_VIEW_ROMS && !g_catalog_loaded)
        fetch_catalog();          /* sets g_catalog_loaded on success */
    else if (g_view == APP_VIEW_SERVER && !g_server_loaded)
        fetch_server();           /* sets g_server_loaded on success */
    else if ((g_view == APP_VIEW_VWII || g_view == APP_VIEW_WIIU) && !g_plan_valid)
        /* Without a plan the view only shows saves found on the console —
         * after a drive swap that hides everything that lives on the server.
         * Compute it on entry so server-only titles are always listed. */
        compute_plan_ex(true);
}

/* Title ids libwhb itself recognises as "hosted by the Homebrew Launcher".
 * Only for these is a SYSRelaunchTitle the canonical exit; everything else —
 * Aroma's Health & Safety redirect (000500101004Ex00), standalone wuhb,
 * forwarders — exits by simply returning from main, and the hosting loader
 * brings the menu back itself. */
static bool hosted_by_hbl(void) {
    switch (OSGetTitleID()) {
        case 0x0005000013374842ULL:  /* Homebrew Launcher */
        case 0x000500101004A000ULL:  /* Mii Maker JPN */
        case 0x000500101004A100ULL:  /* Mii Maker USA */
        case 0x000500101004A200ULL:  /* Mii Maker EUR */
            return true;
        default:
            return false;
    }
}

/*
 * Leave the app — NUSspli's exit sequence (the reference that works
 * everywhere): tear the app down, then ALWAYS issue a launch request
 * (SYSLaunchMenu, or SYSRelaunchTitle for the classic HBL/Mii Maker hosts)
 * and pump ProcUI until EXITING before ProcUIShutdown.  The previous flow
 * skipped both the request and the pump under Aroma, trusting the host
 * loader to relaunch the menu — nothing did, the process died holding the
 * foreground, and the screen stayed black.
 *
 * Teardown is always full: mocha mounts, the AC connection and the SD
 * devoptab must be gone before the process dies or the exit is not clean.
 * (The old exit_mode=minimal skip is deliberately ignored for this reason.)
 */
static void quit_to_menu(void) {
    /* Reachable twice — once from the + / HOME handler and once after the
     * loop falls out — and a second launch request would compete. */
    static bool done = false;
    if (done) return;
    done = true;

    const char *mode = g_state.exit_mode[0] ? g_state.exit_mode : "auto";

    WHBLogPrintf("quit: closing down (exit_mode=%s, aroma=%d, exiting=%d, hbl=%d)",
                 mode, (int)app_is_aroma(), (int)app_exit_pending(),
                 (int)hosted_by_hbl());
    if (!app_exit_pending()) {   /* screen is gone once EXITING is under way */
        static const Dialog bye = {
            "Closing GameSync", "Returning to the Wii U Menu...", HEX_ACCENT, NULL, 0,
        };
        g_dialog = &bye;
        redraw();
        g_dialog = NULL;
        OSSleepTicks(OSMillisecondsToTicks(400));   /* let the message be seen */
    }

    /*
     * EVERYTHING is torn down here, while ProcUI is still alive.
     *
     * ProcUI owns the MEM1 heap that OSScreen's framebuffers come out of, and
     * ProcUIShutdown() resets it.  Releasing our frame-heap state afterwards
     * — which is what the old tail-of-main teardown did — frees against a
     * heap that has already been reset, so the process faulted on its way out
     * and never completed the handover: a black screen with a log that looked
     * like a clean exit.
     */
    WHBLogPrintf("exit: freeing lists");
    sync_plan_free(&g_plan);
    savetree_free_list(&g_vwii);
    savetree_free_list(&g_wiiu);

    WHBLogPrintf("exit: unmounting NAND");
    natives_shutdown(&g_state);

    WHBLogPrintf("exit: network shutdown");
    network_shutdown();

    WHBLogPrintf("exit: unmounting SD");
    WHBUnmountSdCard();

    WHBLogPrintf("exit: releasing screen (MEM1) before ProcUI shuts down");
    ui_shutdown();

    /*
     * NUSspli's exit sequence, verbatim in spirit: unless CafeOS already
     * ordered the exit, ALWAYS issue a launch request ourselves, then pump
     * ProcUI (answering RELEASE_FOREGROUND with ProcUIDrawDoneRelease) until
     * EXITING arrives.  The old "no launch request — host loader restores
     * the menu" branch returned from main still holding the foreground with
     * nothing scheduled to take it: that was the black screen on exit.
     * SYSRelaunchTitle instead of SYSLaunchMenu only for the classic
     * HBL / Mii Maker hijack hosts, where "back to the launcher" is the
     * expected destination.
     */
    bool pump = true;
    if (app_exit_pending()) {
        WHBLogPrintf("quit: system already exiting - no launch request");
    } else if (strcmp(mode, "none") == 0) {
        /* Debug escape hatch: no request means EXITING never comes, so do
         * not sit in the pump waiting for it either. */
        WHBLogPrintf("quit: issuing NO launch request (exit_mode=none)");
        pump = false;
    } else if (strcmp(mode, "relaunch") == 0 ||
               (strcmp(mode, "menu") != 0 && hosted_by_hbl())) {
        WHBLogPrintf("quit: SYSRelaunchTitle (%s)",
                     strcmp(mode, "relaunch") == 0 ? "exit_mode" : "HBL host");
        SYSRelaunchTitle(0, NULL);
    } else {
        WHBLogPrintf("quit: SYSLaunchMenu");
        SYSLaunchMenu();
    }

    if (pump)
        app_wait_exit();
    WHBLogPrintf("quit: done");
}

static void cycle_view(int delta) {
    int n = (int)APP_VIEW_COUNT;
    g_view = (AppView)((((int)g_view + delta) % n + n) % n);
    enter_view();
}

/* ---- main ---- */

int main(int argc, char **argv) {
    (void)argc; (void)argv;

    /* Do NOT call OSEnableHomeButtonMenu(TRUE) anywhere.
     *
     * Enabling the overlay asks the system to render the HOME menu over this
     * process — which only works when the process is a real standalone
     * title.  Hosted through any wrapper hijack (Homebrew Launcher .rpx,
     * wiiload, the Health & Safety redirect) the overlay comes up black with
     * no way back, and detecting Aroma does not prove which case we are in.
     * app_init therefore disables it in both environments; the DENIED
     * callback below turns the press into a clean quit — same path as +. */
    WHBLogUdpInit();
    app_init();
    ProcUIRegisterCallback(PROCUI_CALLBACK_HOME_BUTTON_DENIED,
                           home_button_denied, NULL, 100);

    /* The title id decides whether libwhb relaunches for us on exit; log it so
     * the launcher environment is never in doubt. */
    WHBLogPrintf("wiiusync " APP_VERSION " starting (titleID %016llx)",
                 (unsigned long long)OSGetTitleID());
    VPADInit();
    ui_init();

    /* One global wait callback for the whole app, not just downloads: any
     * HTTP stall then repaints and honours B as cancel, instead of looking
     * like a lockup. */
    http_set_wait_cb(wait_cb);

    g_booting = true;
    show_boot("Mounting SD card", NULL);
    g_state.sd_ready = WHBMountSdCard();
    const char *root = WHBGetSdCardMountPath();
    snprintf(g_state.sd_root, sizeof(g_state.sd_root), "%s",
             (root && root[0]) ? root : SD_ROOT_DEFAULT);

    char err[256] = {0};
    bool sd_ready = g_state.sd_ready;
    char sd_root[64];
    snprintf(sd_root, sizeof(sd_root), "%s", g_state.sd_root);

    show_boot("Reading config", NULL);

    /* config_load resets the whole state to defaults — restore the mount
     * results afterwards. */
    config_load(&g_state, err, sizeof(err));
    g_state.sd_ready = sd_ready;
    snprintf(g_state.sd_root, sizeof(g_state.sd_root), "%s", sd_root);

    config_load_console_id(&g_state);
    roms_set_target(&g_state);
    if (g_state.sd_ready) {
        roms_ensure_target_dirs();
        downloads_load(&g_downloads);
    }

    show_boot("Bringing up the network", NULL);
    network_init(&g_state);

    /* libmocha only exists under CFW.  Emulators have no /dev/iosuhax, and a
     * stuck IOS_Open there would hang the boot with nothing on screen — so
     * make the whole stage opt-out via the two sync_* config keys. */
    if (g_state.sync_vwii || g_state.sync_wiiu) {
        show_boot("Opening NAND (libmocha)",
                  "If it stops here, set sync_vwii=false and sync_wiiu=false "
                  "in 3dssync/config.txt.");
        natives_init(&g_state);
    } else {
        show_boot("Skipping NAND (sync_vwii / sync_wiiu off)", NULL);
        snprintf(g_state.mocha_error, sizeof(g_state.mocha_error),
                 "disabled in config");
    }

    show_boot("Scanning installed games", NULL);
    if (g_state.sd_ready) roms_scan_local(&g_local);

    show_boot("Scanning GC memory-card images", NULL);
    if (g_state.sd_ready) {
        gcsaves_scan_cards(&g_state, &g_cards);
        if (g_cards.count > 0) open_card(0);
    }

    show_boot("Scanning console saves", NULL);
    scan_natives_quiet();

    /* Deliberately NOT fetching the catalog or the save list here.  Both are
     * multi-request round trips, and when the configured server is not
     * reachable (the out-of-the-box default, or an emulator with no LAN
     * route) they used to stall the whole boot — which reads as a freeze.
     * A single bounded /status probe tells the user where they stand; the
     * lists load on demand from their own views. */
    show_boot("Checking server", NULL);
    bool server_ok = network_is_ready(&g_state) && network_check_server(&g_state);

    if (!g_state.sd_ready)
        ui_error("No SD card (downloads / GC cards disabled)");
    else if (err[0])
        ui_error("%s", err);
    else if (!network_is_ready(&g_state))
        ui_error("No network - check System Settings (SD ready)");
    else if (!server_ok)
        ui_error("No reply from %s - check Config", g_state.server_url);
    else
        ui_status("Ready - ip %s, server OK", g_state.ip);

    g_booting = false;
    g_wait_secs = 0;
    enter_view();
    redraw();

    while (app_running()) {
        /* Coming back from the HOME menu hands us freshly allocated, blank
         * framebuffers.  The loop otherwise only paints on input, so without
         * this the app sits on a black screen until a button is pressed. */
        if (ui_consume_repaint_request()) redraw();

        /* HOME sets g_quit_requested from inside app_running() and produces
         * no VPAD bit — check it BEFORE the down==0 early-out or the press
         * does nothing until some other button is hit. */
        if (g_quit_requested) {
            quit_to_menu();
            break;
        }

        uint32_t held = 0;
        uint32_t down = pad_read(&held);
        if (down == 0) {
            OSSleepTicks(OSMillisecondsToTicks(16));
            continue;
        }

        /* PLUS quits too — an explicit, reliable way out that does not
         * depend on the HOME press reaching us. */
        if (down & VPAD_BUTTON_PLUS) {
            if (!confirm("Close GameSync?")) {
                if (g_quit_requested) { quit_to_menu(); break; }
                redraw();
                continue;
            }
            quit_to_menu();
            break;
        }

        if (down & VPAD_BUTTON_ZL)      cycle_view(-1);
        else if (down & VPAD_BUTTON_ZR) cycle_view(+1);

        else if (g_view == APP_VIEW_ROMS) {
            if      (down & VPAD_BUTTON_UP)    g_rom_sel--;
            else if (down & VPAD_BUTTON_DOWN)  g_rom_sel++;
            else if (down & VPAD_BUTTON_LEFT)  g_rom_sel -= ui_list_visible();
            else if (down & VPAD_BUTTON_RIGHT) g_rom_sel += ui_list_visible();
            else if (down & VPAD_BUTTON_A) {
                /* A is the confirm/primary action: download the selection.
                 * On an empty list it (re)fetches instead. */
                if (g_catalog.count == 0) fetch_catalog();
                else if (g_rom_sel < g_catalog.count) {
                    const RomEntry *rom = &g_catalog.items[g_rom_sel];
                    char sz[16]; human_size(rom->size, sz, sizeof(sz));
                    if (confirm("Download %s (%s) now?", rom->name, sz))
                        queue_selected_rom(true);
                }
            }
            else if (down & VPAD_BUTTON_MINUS) toggle_catalog_system();
            else if (down & VPAD_BUTTON_X)     queue_selected_rom(false);
            else if (down & VPAD_BUTTON_Y)     fetch_catalog();
            clamp_scroll(&g_rom_sel, &g_rom_scroll, g_catalog.count);
        }
        else if (g_view == APP_VIEW_LOCAL) {
            int c = g_local.count;
            if      (down & VPAD_BUTTON_UP)    g_loc_sel--;
            else if (down & VPAD_BUTTON_DOWN)  g_loc_sel++;
            else if (down & VPAD_BUTTON_LEFT)  g_loc_sel -= ui_list_visible();
            else if (down & VPAD_BUTTON_RIGHT) g_loc_sel += ui_list_visible();
            else if (down & VPAD_BUTTON_A)     install_selected_local();
            else if (down & VPAD_BUTTON_Y)     scan_local();
            else if (down & VPAD_BUTTON_X) {
                if (c > 0 && g_loc_sel < c &&
                    confirm_danger("Delete game", "Delete %s\n(%s) from storage?",
                            g_local.items[g_loc_sel].name,
                            g_local.items[g_loc_sel].filename)) {
                    if (remove(g_local.items[g_loc_sel].path) == 0) {
                        ui_status("Deleted %s", g_local.items[g_loc_sel].name);
                        scan_local();
                    } else ui_error("Delete failed");
                }
            }
            clamp_scroll(&g_loc_sel, &g_loc_scroll, g_local.count);
        }
        else if (g_view == APP_VIEW_DOWNLOADS) {
            dl_build_groups();
            int c = g_dl_group_count;
            if      (down & VPAD_BUTTON_UP)    g_dl_sel--;
            else if (down & VPAD_BUTTON_DOWN)  g_dl_sel++;
            else if (down & VPAD_BUTTON_A) {
                if (c > 0 && g_dl_sel < c)
                    run_download_group(g_dl_groups[g_dl_sel].rom_id);
            }
            else if (down & VPAD_BUTTON_Y) run_download_queue();
            else if (down & VPAD_BUTTON_X) {
                if (c > 0 && g_dl_sel < c) {
                    DlGroup *g = &g_dl_groups[g_dl_sel];
                    if (g->files <= 1 ||
                        confirm("Remove %s\n(%d files) from the queue?",
                                g->name, g->files))
                        remove_download_group(g->rom_id);
                }
            }
            dl_build_groups();
            clamp_scroll(&g_dl_sel, &g_dl_scroll, g_dl_group_count);
        }
        else if (g_view == APP_VIEW_GCCARDS) {
            int vis = ui_list_visible();
            if (vis < 1) vis = 1;
            if      (down & VPAD_BUTTON_UP)    g_gc_sel--;
            else if (down & VPAD_BUTTON_DOWN)  g_gc_sel++;
            else if (down & VPAD_BUTTON_LEFT)  g_gc_sel -= vis;
            else if (down & VPAD_BUTTON_RIGHT) g_gc_sel += vis;
            else if (down & VPAD_BUTTON_R) {
                if (g_cards.count > 0) { open_card((g_card_active + 1) % g_cards.count); }
            }
            else if (down & VPAD_BUTTON_X)     scan_cards();
            else if (down & VPAD_BUTTON_A)     upload_gc_save();
            else if (down & VPAD_BUTTON_Y) {
                if (g_card.count > 0 && g_gc_sel < g_card.count)
                    restore_gc_save(g_card.saves[g_gc_sel].title_id);
            }
            else if (down & VPAD_BUTTON_L)     import_whole_card();

            if (g_card.count == 0) { g_gc_sel = 0; g_gc_scroll = 0; }
            else {
                if (g_gc_sel < 0) g_gc_sel = 0;
                if (g_gc_sel >= g_card.count) g_gc_sel = g_card.count - 1;
                if (g_gc_sel < g_gc_scroll) g_gc_scroll = g_gc_sel;
                if (g_gc_sel >= g_gc_scroll + vis) g_gc_scroll = g_gc_sel - vis + 1;
                if (g_gc_scroll < 0) g_gc_scroll = 0;
            }
        }
        else if (g_view == APP_VIEW_SERVER) {
            if      (down & VPAD_BUTTON_UP)    g_sv_sel--;
            else if (down & VPAD_BUTTON_DOWN)  g_sv_sel++;
            else if (down & VPAD_BUTTON_LEFT)  g_sv_sel -= ui_list_visible();
            else if (down & VPAD_BUTTON_RIGHT) g_sv_sel += ui_list_visible();
            else if (down & VPAD_BUTTON_X)     fetch_server();
            else if (down & VPAD_BUTTON_A) {
                if (g_server.count > 0 && g_sv_sel < g_server.count)
                    restore_gc_save(g_server.items[g_sv_sel].title_id);
            }
            else if (down & VPAD_BUTTON_Y) {
                if (confirm("Download every server GC save as a .gci\ninto 3dssync/gci?")) {
                    char msg[160];
                    ui_status("Pulling GCIs...");
                    redraw();
                    gcsaves_pull_all(&g_state, &g_server, msg, sizeof(msg));
                    ui_status("%s", msg);
                }
            }
            clamp_scroll(&g_sv_sel, &g_sv_scroll, g_server.count);
        }
        else if (g_view == APP_VIEW_VWII || g_view == APP_VIEW_WIIU) {
            SaveTitleList *list = native_list(g_view);
            int *sel = native_sel(g_view);
            int *scroll = native_scroll(g_view);
            int count = list->title_count + (g_plan_valid ? g_plan.server_only_count : 0);

            if      (down & VPAD_BUTTON_UP)    (*sel)--;
            else if (down & VPAD_BUTTON_DOWN)  (*sel)++;
            else if (down & VPAD_BUTTON_LEFT)  (*sel) -= ui_list_visible();
            else if (down & VPAD_BUTTON_RIGHT) (*sel) += ui_list_visible();
            else if (down & VPAD_BUTTON_MINUS) { scan_natives(); compute_plan(); }
            else if (down & VPAD_BUTTON_L)     sync_all_natives();
            else if (down & VPAD_BUTTON_A)     native_action(g_view, 0);
            else if (down & VPAD_BUTTON_X)     native_action(g_view, 1);
            else if (down & VPAD_BUTTON_Y)     native_action(g_view, 2);
            clamp_scroll(sel, scroll, count);
        }
        else if (g_view == APP_VIEW_CONFIG) config_input(down);

        redraw();
    }

    /* HOME is the normal way out of wrapper-hosted homebrew, so the teardown
     * below runs on every exit.  Unmounting NAND and the SD card is not
     * instant; keep the screen alive and say what is happening, otherwise the
     * user just sees an unexplained black gap before the launcher returns. */
    /* quit_to_menu() has already torn everything down (it has to, while
     * ProcUI is still alive).  The loop can also fall out on its own — the
     * ProcUI EXITING path — so run it here too; every step is idempotent. */
    quit_to_menu();

    WHBLogPrintf("wiiusync exiting (ProcUIShutdown next)");
    app_shutdown();
    WHBLogPrintf("wiiusync: shutdown complete, returning to loader");
    WHBLogUdpDeinit();
    return 0;
}
