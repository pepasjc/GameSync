/*
 * gcsync — GameCube Save Sync client.
 *
 * Phase 1: boot + UI + controller-editable config.
 * Phase 2: ROM catalog browse + SD install (sd:/games) + resumable downloads.
 *
 * Boot: ui_init -> mount SD -> config -> BBA net -> CARD init -> menu loop.
 * Memory-card / VMC save sync (Phase 3) and GameID (Phase 4) are still stubbed.
 */

#include "common.h"
#include "config.h"
#include "gcnet.h"
#include "ui.h"
#include "roms.h"
#include "downloads.h"
#include "saves.h"
#include "http.h"

#include <stdio.h>
#include <string.h>
#include <strings.h>   /* strcasecmp — GC_ title ids are case-insensitive */
#include <stdlib.h>
#include <stdarg.h>
#include <unistd.h>
#include <dirent.h>
#include <errno.h>
#include <sys/stat.h>

#include <gccore.h>
#include <ogc/card.h>
#include <ogc/pad.h>
#include <ogc/lwp_watchdog.h>   /* gettime / ticks_to_millisecs */
#include <fat.h>
#include <sdcard/gcsd.h>

static SyncState     g_state;
static AppView       g_view = APP_VIEW_ROMS;
static RomCatalog    g_catalog;
static LocalRomList  g_local;
static DownloadList  g_downloads;

static GcSaveList     g_carda, g_cardb;     /* slot A (chan 0), slot B (chan 1) */
static ServerSaveList g_server;
static SaveVmcList    g_vmc;                /* card image FILES found on SD */
static VmcfsCard      g_vmccard;            /* currently opened card image */
static int            g_vmc_active = 0;     /* which file is opened */

static int g_cfg_sel    = 0;
static int g_rom_sel    = 0, g_rom_scroll   = 0;
static int g_local_sel  = 0, g_local_scroll = 0;
static int g_dl_sel     = 0, g_dl_scroll    = 0;
static int g_ca_sel     = 0, g_ca_scroll    = 0;
static int g_cb_sel     = 0, g_cb_scroll    = 0;
static int g_sv_sel     = 0, g_sv_scroll    = 0;
static int g_vmc_sel    = 0, g_vmc_scroll   = 0;

static char g_scratch[256 * 1024];   /* catalog JSON page buffer */

/* Live download progress (updated from progress_cb). */
static volatile uint64_t g_active_done  = 0;
static volatile uint64_t g_active_total = 0;
static volatile uint64_t g_active_bps   = 0;
static volatile bool     g_pause_req    = false;
static uint64_t          g_last_draw    = 0;
static uint64_t          g_spd_ms       = 0;
static uint64_t          g_spd_bytes    = 0;

static void redraw(void);   /* forward decl: long ops flush mid-run */
static void scan_local(void);
static void scan_vmc_view(void);
static void fill_vmccard_names(void);

/* ---- helpers ---- */

static void human_size(uint64_t b, char *o, size_t n) {
    if (b >= (1ULL << 30)) snprintf(o, n, "%lluMB", (unsigned long long)(b >> 20));
    else if (b >= (1ULL << 20)) snprintf(o, n, "%lluMB", (unsigned long long)(b >> 20));
    else if (b >= (1ULL << 10)) snprintf(o, n, "%lluKB", (unsigned long long)(b >> 10));
    else snprintf(o, n, "%lluB", (unsigned long long)b);
}

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

/* ---- SD mounting ---- */

static const DISC_INTERFACE *sd_iface(SdDevice dev) {
    switch (dev) {
        case SD_DEV_GECKO_A: return &__io_gcsda;
        case SD_DEV_GECKO_B: return &__io_gcsdb;
        case SD_DEV_SP2:
        default:             return &__io_gcsd2;
    }
}

/* Per-device probe result shown in the Config view: distinguishes "nothing in
 * the port" from "card detected but the filesystem didn't mount" (libfat is
 * FAT12/16/32 only — exFAT cards read as the latter). */
static char g_sd_diag[SD_DEV_COUNT][16] = { "?", "?", "?" };

static bool try_mount_dev(SdDevice dev) {
    const DISC_INTERFACE *io = sd_iface(dev);
    if (!io->startup() || !io->isInserted()) {
        snprintf(g_sd_diag[dev], sizeof(g_sd_diag[dev]), "none");
        return false;
    }
    if (fatMountSimple(SD_MOUNT_NAME, io)) {
        snprintf(g_sd_diag[dev], sizeof(g_sd_diag[dev]), "MOUNTED");
        return true;
    }
    /* Device answered but no FAT volume — almost always exFAT/NTFS. */
    snprintf(g_sd_diag[dev], sizeof(g_sd_diag[dev]), "notFAT32");
    return false;
}

/* Mount trying ``pref`` first, then the other devices.  Updates state. */
static bool mount_sd_preferring(SyncState *st, SdDevice pref) {
    if (st->sd_ready) {
        fatUnmount(SD_ROOT);
        st->sd_ready = false;
    }
    SdDevice order[SD_DEV_COUNT];
    int n = 0;
    order[n++] = pref;
    for (int d = 0; d < SD_DEV_COUNT; d++)
        if ((SdDevice)d != pref) order[n++] = (SdDevice)d;

    for (int i = 0; i < n; i++) {
        if (try_mount_dev(order[i])) {
            st->sd_ready  = true;
            st->sd_device = order[i];
            strncpy(st->sd_root, SD_ROOT, sizeof(st->sd_root) - 1);
            st->sd_root[sizeof(st->sd_root) - 1] = '\0';
            return true;
        }
    }
    return false;
}

static bool mount_sd(SyncState *st) {
    return mount_sd_preferring(st, SD_DEV_SP2);
}

static void show_boot(const char *msg) {
    ui_clear();
    ui_draw_message("gcsync boot", msg);
    ui_flush();
}

/* ---- Controller text editor (on-screen character picker) ---- */

static const char CHARSET[] =
    " ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789.:/_-?=&%@";

static char charset_step(char c, int dir) {
    int n = (int)sizeof(CHARSET) - 1;
    const char *pos = strchr(CHARSET, c);
    int idx = pos ? (int)(pos - CHARSET) : 1;
    idx = (idx + dir + n) % n;
    return CHARSET[idx];
}

static void draw_edit(const char *label, const char *buf, int cur) {
    ui_clear();
    ui_draw_header(&g_state, APP_VIEW_CONFIG);
    ui_text(ui_list_top(), 2, UI_YELLOW, "Edit %s", label);
    ui_text(ui_list_top() + 2, 2, UI_WHITE, "%s", buf[0] ? buf : "(empty)");

    char caret[200];
    int cols = ui_cols();
    if (cur > cols - 4) cur = cols - 4;
    memset(caret, ' ', sizeof(caret));
    if (cur >= 0 && cur < (int)sizeof(caret) - 1) caret[cur] = '^';
    caret[cols < (int)sizeof(caret) ? cols : (int)sizeof(caret) - 1] = '\0';
    ui_text(ui_list_top() + 3, 2, UI_CYAN, "%s", caret);

    ui_draw_footer("Up/Dn=char  L/R=move  Z=insert  X=delete  A=ok  B=cancel");
    ui_flush();
}

static bool edit_text(char *out, size_t cap, const char *label) {
    char buf[256];
    strncpy(buf, out, sizeof(buf) - 1);
    buf[sizeof(buf) - 1] = '\0';
    int len = (int)strlen(buf);
    int cur = len;
    int maxlen = (int)(cap < sizeof(buf) ? cap : sizeof(buf)) - 1;

    draw_edit(label, buf, cur);
    for (;;) {
        VIDEO_WaitVSync();
        PAD_ScanPads();
        u32 d = PAD_ButtonsDown(0);
        if (d == 0) continue;   /* redraw only on input */

        if (d & (PAD_BUTTON_A | PAD_BUTTON_START)) {
            strncpy(out, buf, cap - 1); out[cap - 1] = '\0'; return true;
        }
        if (d & PAD_BUTTON_B) return false;
        if ((d & PAD_BUTTON_LEFT)  && cur > 0)   cur--;
        if ((d & PAD_BUTTON_RIGHT) && cur < len) cur++;
        if (d & (PAD_BUTTON_UP | PAD_BUTTON_DOWN)) {
            int dir = (d & PAD_BUTTON_UP) ? 1 : -1;
            if (cur == len) { if (len < maxlen) { buf[len++] = charset_step(' ', dir); buf[len] = '\0'; } }
            else buf[cur] = charset_step(buf[cur], dir);
        }
        if (d & PAD_BUTTON_X) {
            if (cur < len) { memmove(&buf[cur], &buf[cur + 1], len - cur); len--; }
            else if (len > 0) { buf[--len] = '\0'; cur = len; }
        }
        if (d & PAD_TRIGGER_Z) {
            if (len < maxlen) { memmove(&buf[cur + 1], &buf[cur], len - cur + 1); buf[cur] = ' '; len++; }
        }
        draw_edit(label, buf, cur);
    }
}

/* ---- Config view ---- */

typedef enum {
    CF_SERVER = 0, CF_APIKEY, CF_NETMODE, CF_IP, CF_NM, CF_GW,
    CF_SDDEV, CF_GAMES, CF_GIDA, CF_GIDB, CF_TESTSD, CF_SAVE, CF_COUNT
} CfgField;

/* Step-by-step SD probe: reports the FIRST filesystem operation that fails
 * (with errno) so "mounts but nothing works" cases become diagnosable. */
static void sd_self_test(void) {
    if (!g_state.sd_ready) {
        ui_error("SD not mounted (sp2:%s A:%s B:%s)",
                 g_sd_diag[0], g_sd_diag[1], g_sd_diag[2]);
        return;
    }
    errno = 0;
    DIR *d = opendir("sd:/");
    if (!d) { ui_error("TEST opendir sd:/ FAILED errno=%d", errno); return; }
    int n = 0;
    while (readdir(d) != NULL) n++;
    closedir(d);

    errno = 0;
    mkdir("sd:/3dssync", 0777);
    FILE *fp = fopen("sd:/3dssync/probe.txt", "wb");
    if (!fp) { ui_error("TEST root ok (%d entries); WRITE failed errno=%d", n, errno); return; }
    fputs("ok", fp);
    fclose(fp);

    char buf[8] = {0};
    fp = fopen("sd:/3dssync/probe.txt", "rb");
    if (fp) { fread(buf, 1, 2, fp); fclose(fp); }
    unlink("sd:/3dssync/probe.txt");

    if (strcmp(buf, "ok") == 0)
        ui_status("SD OK: %d root entries, write+readback OK", n);
    else
        ui_error("TEST write ok but READBACK failed");
}

static void draw_config(void) {
    int top = ui_list_top();
    const char *net = g_state.use_static_ip ? "static" : "DHCP";
    ui_text_hl(top + CF_SERVER,  g_cfg_sel == CF_SERVER,  UI_WHITE, " Server URL : %s", g_state.server_url);
    ui_text_hl(top + CF_APIKEY,  g_cfg_sel == CF_APIKEY,  UI_WHITE, " API Key    : %s", g_state.api_key);
    ui_text_hl(top + CF_NETMODE, g_cfg_sel == CF_NETMODE, UI_WHITE, " Network    : %s", net);
    ui_text_hl(top + CF_IP,      g_cfg_sel == CF_IP,      UI_WHITE, " Static IP  : %s", g_state.static_ip);
    ui_text_hl(top + CF_NM,      g_cfg_sel == CF_NM,      UI_WHITE, " Netmask    : %s", g_state.static_netmask);
    ui_text_hl(top + CF_GW,      g_cfg_sel == CF_GW,      UI_WHITE, " Gateway    : %s", g_state.static_gateway);
    ui_text_hl(top + CF_SDDEV,   g_cfg_sel == CF_SDDEV,   UI_WHITE, " SD device  : %s", sd_device_to_str(g_state.sd_device));
    ui_text_hl(top + CF_GAMES,   g_cfg_sel == CF_GAMES,   UI_WHITE, " Games dir  : %s", g_state.games_folder);
    ui_text_hl(top + CF_GIDA,    g_cfg_sel == CF_GIDA,    UI_WHITE, " GameID A   : %s", g_state.mmce_mode[0] ? "on" : "off");
    ui_text_hl(top + CF_GIDB,    g_cfg_sel == CF_GIDB,    UI_WHITE, " GameID B   : %s", g_state.mmce_mode[1] ? "on" : "off");
    ui_text_hl(top + CF_TESTSD,  g_cfg_sel == CF_TESTSD,  UI_CYAN,  " [ Test SD read/write ]");
    ui_text_hl(top + CF_SAVE,    g_cfg_sel == CF_SAVE,    UI_GREEN, " [ Save config to SD ]");

    /* SD probe diagnostics (updated on every mount attempt). */
    ui_text(top + CF_COUNT + 1, 1, UI_GREY,
            "sp2:%s  geckoA:%s  geckoB:%s",
            g_sd_diag[SD_DEV_SP2], g_sd_diag[SD_DEV_GECKO_A], g_sd_diag[SD_DEV_GECKO_B]);
    ui_text(top + CF_COUNT + 2, 1, UI_GREY,
            "notFAT32 = card seen but no FAT volume (exFAT unsupported)");
}

/* Remount the SD on the (newly chosen) device and refresh SD-backed views. */
static void apply_sd_device(void) {
    ui_status("Mounting SD on %s...", sd_device_to_str(g_state.sd_device));
    redraw();
    if (mount_sd_preferring(&g_state, g_state.sd_device)) {
        roms_set_target(g_state.sd_root, g_state.games_folder);
        roms_ensure_target_dirs();
        downloads_load(&g_downloads);
        scan_local();
        saves_scan_vmc(&g_vmc);
        ui_status("SD mounted on %s", sd_device_to_str(g_state.sd_device));
    } else {
        ui_error("No FAT SD found (sp2:%s A:%s B:%s)",
                 g_sd_diag[0], g_sd_diag[1], g_sd_diag[2]);
    }
}

static void config_change(int dir) {
    switch (g_cfg_sel) {
        case CF_NETMODE: g_state.use_static_ip = !g_state.use_static_ip; break;
        case CF_SDDEV:
            g_state.sd_device = (SdDevice)(((int)g_state.sd_device + dir + SD_DEV_COUNT) % SD_DEV_COUNT);
            break;
        case CF_GIDA: g_state.mmce_mode[0] = !g_state.mmce_mode[0]; break;
        case CF_GIDB: g_state.mmce_mode[1] = !g_state.mmce_mode[1]; break;
        default: break;
    }
}

static void config_activate(void) {
    switch (g_cfg_sel) {
        case CF_SERVER: edit_text(g_state.server_url, sizeof(g_state.server_url), "Server URL"); break;
        case CF_APIKEY: edit_text(g_state.api_key, sizeof(g_state.api_key), "API Key"); break;
        case CF_IP:     edit_text(g_state.static_ip, sizeof(g_state.static_ip), "Static IP"); break;
        case CF_NM:     edit_text(g_state.static_netmask, sizeof(g_state.static_netmask), "Netmask"); break;
        case CF_GW:     edit_text(g_state.static_gateway, sizeof(g_state.static_gateway), "Gateway"); break;
        case CF_GAMES:  edit_text(g_state.games_folder, sizeof(g_state.games_folder), "Games folder");
                        roms_set_target(g_state.sd_root, g_state.games_folder); break;
        case CF_SDDEV:  config_change(+1); apply_sd_device(); break;
        case CF_TESTSD: sd_self_test(); break;
        case CF_NETMODE: case CF_GIDA: case CF_GIDB: config_change(+1); break;
        case CF_SAVE:
            if (!g_state.sd_ready) ui_error("No SD mounted - cannot save config");
            else if (config_save(&g_state)) ui_status("Config saved to SD");
            else ui_error("Failed to write config");
            break;
        default: break;
    }
}

static void config_input(u32 d) {
    if (d & PAD_BUTTON_UP)    g_cfg_sel = (g_cfg_sel - 1 + CF_COUNT) % CF_COUNT;
    if (d & PAD_BUTTON_DOWN)  g_cfg_sel = (g_cfg_sel + 1) % CF_COUNT;
    if (d & PAD_BUTTON_LEFT)  config_change(-1);
    if (d & PAD_BUTTON_RIGHT) config_change(+1);
    if (d & PAD_BUTTON_A)     config_activate();
}

/* ---- Catalog / local / downloads ---- */

static void fetch_catalog(void) {
    if (!network_is_ready(&g_state)) { ui_error("Network not ready (%s)", g_state.ip); return; }
    ui_status("Fetching GC catalog...");
    redraw();
    bool ok = roms_fetch_catalog(&g_state, "GC", g_scratch, sizeof(g_scratch), &g_catalog);
    if (!ok) ui_error("%s", g_catalog.last_error);
    else     ui_status("Catalog: %d GC ROM(s)", g_catalog.count);
    g_rom_sel = 0; g_rom_scroll = 0;
    redraw();
}

static void scan_local(void) {
    if (!g_state.sd_ready) { ui_error("Storage not ready"); return; }
    ui_status("Scanning sd:/games...");
    redraw();
    roms_scan_local(&g_local);
    if (g_local.count > 0) ui_status("Local: %d ISO(s)", g_local.count);
    else ui_status("%s", g_local.last_error);
    redraw();
}

static int progress_cb(uint64_t done, uint64_t total) {
    g_active_done = done;
    g_active_total = total;
    PAD_ScanPads();
    if (PAD_ButtonsDown(0) & PAD_BUTTON_B) g_pause_req = true;

    uint64_t now = ticks_to_millisecs(gettime());
    if (g_spd_ms == 0) { g_spd_ms = now; g_spd_bytes = done; }
    else if (now - g_spd_ms >= 1000) {
        g_active_bps = (done - g_spd_bytes) * 1000 / (now - g_spd_ms);
        g_spd_ms = now;
        g_spd_bytes = done;
        redraw();   /* refresh the speed/ETA line once per second */
    }
    if (done - g_last_draw >= (1U << 19) || (total && done >= total)) {
        g_last_draw = done;
        redraw();
    }
    return g_pause_req ? 1 : 0;
}

/* Pump the UI while the server prepares a response (e.g. RVZ->ISO
 * conversion, which can take minutes).  B cancels -> download pauses. */
static int download_wait_cb(uint32_t ms) {
    PAD_ScanPads();
    if (PAD_ButtonsDown(0) & PAD_BUTTON_B) return 1;
    /* ms==0 is the parallel loop's per-pass cancel poll — progress_cb owns
     * the periodic redraw there; only the (single-stream) conversion wait
     * passes a real elapsed time and wants the "waiting" status. */
    if (ms && (ms % 2000) == 0) {
        ui_status("Waiting for server... %us (converting? B=cancel)", ms / 1000);
        redraw();
    }
    return 0;
}

static void run_active_download(DownloadEntry *e) {
    if (!e) return;
    if (!g_state.sd_ready) { ui_error("No SD - cannot download"); return; }

    /* Ensure target dir exists. */
    char dir[256];
    strncpy(dir, e->target_path, sizeof(dir) - 1);
    dir[sizeof(dir) - 1] = '\0';
    char *slash = strrchr(dir, '/');
    if (slash) { *slash = '\0'; roms_mkdir_p(dir); }

    g_active_done  = e->offset;
    g_active_total = e->total;
    g_pause_req    = false;
    g_last_draw    = e->offset;
    g_active_bps   = 0;
    g_spd_ms       = 0;
    network_set_progress64_cb(progress_cb);
    http_set_wait_cb(download_wait_cb);

    e->status = DL_STATUS_ACTIVE;
    downloads_save(&g_downloads);
    ui_status("Requesting %s... (server may convert first; B=cancel)", e->name);
    redraw();

    uint64_t total = 0;
    int rc = network_download_rom_resumable(&g_state, e->rom_id, e->extract_format,
                                            e->target_path, e->offset, &total);
    network_set_progress64_cb(NULL);
    http_set_wait_cb(NULL);
    if (total > 0) e->total = total;

    if (rc == 0) {
        e->status = DL_STATUS_COMPLETED;
        e->offset = e->total > 0 ? e->total : g_active_done;
        ui_status("Done: %s", e->name);
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

/* One pass over the queue: run everything runnable, in order.  A download
 * the user cancels (B -> paused) stops the run; an error moves on to the
 * next entry. */
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
            case DL_STATUS_PAUSED:    stopped = true; break;   /* user cancel */
            default:                  failed++; break;
        }
    }
    if (done > 0) scan_local();
    ui_status("Queue: %d done, %d failed%s", done, failed,
              stopped ? ", stopped" : "");
    redraw();
}

static void queue_selected_rom(bool run_now) {
    if (!g_state.sd_ready) { ui_error("No SD - cannot install"); return; }
    if (g_catalog.count == 0 || g_rom_sel >= g_catalog.count) return;
    DownloadEntry *e = downloads_upsert_from_catalog(&g_downloads, &g_catalog.items[g_rom_sel]);
    if (!e) { ui_error("Download list full"); return; }
    downloads_save(&g_downloads);
    if (run_now) {
        /* Switch to the Downloads view so progress/speed are visible — a
         * download started from the catalog otherwise runs behind the ROM
         * list with only a status line, which reads as "frozen". */
        g_dl_sel = (int)(e - g_downloads.items);
        g_view = APP_VIEW_DOWNLOADS;
        run_active_download(e);
    } else {
        ui_status("Queued: %s", e->name);
    }
}

/* ---- View rendering ---- */

static void draw_roms(void) {
    int top = ui_list_top(), vis = ui_list_visible();
    if (g_catalog.count == 0) {
        ui_text(top + 1, 2, UI_GREY, "%s",
                g_catalog.last_error[0] ? g_catalog.last_error : "No GC ROMs. Press A to fetch.");
        return;
    }
    int namew = ui_cols() - 12;
    if (namew < 8) namew = 8;
    for (int i = 0; i < vis; i++) {
        int idx = g_rom_scroll + i;
        if (idx >= g_catalog.count) break;
        RomEntry *r = &g_catalog.items[idx];
        char sz[16]; human_size(r->size, sz, sizeof(sz));
        bool queued = downloads_find(&g_downloads, r->rom_id) != NULL;
        char line[200];
        snprintf(line, sizeof(line), " %-*.*s %7s%s", namew, namew,
                 r->name, sz, queued ? " *" : "");
        ui_text_hl(top + i, idx == g_rom_sel, queued ? UI_GREEN : UI_WHITE, "%s", line);
    }
}

static void draw_local(void) {
    int top = ui_list_top(), vis = ui_list_visible();
    if (g_local.count == 0) {
        ui_text(top + 1, 2, UI_GREY, "%s",
                g_local.last_error[0] ? g_local.last_error : "No installed ISOs.");
        return;
    }
    int namew = ui_cols() - 12;
    if (namew < 8) namew = 8;
    for (int i = 0; i < vis; i++) {
        int idx = g_local_scroll + i;
        if (idx >= g_local.count) break;
        LocalRom *r = &g_local.items[idx];
        char sz[16]; human_size(r->size, sz, sizeof(sz));
        char line[200];
        snprintf(line, sizeof(line), " %-*.*s %7s", namew, namew, r->name, sz);
        ui_text_hl(top + i, idx == g_local_sel, UI_WHITE, "%s", line);
    }
}

static void draw_downloads(void) {
    int top = ui_list_top(), vis = ui_list_visible();
    if (g_downloads.count == 0) {
        ui_text(top + 1, 2, UI_GREY, "Queue empty. Queue ROMs from the ROMs view (X).");
        return;
    }
    for (int i = 0; i < vis; i++) {
        int idx = g_dl_scroll + i;
        if (idx >= g_downloads.count) break;
        DownloadEntry *e = &g_downloads.items[idx];
        uint64_t done = (e->status == DL_STATUS_ACTIVE) ? g_active_done : e->offset;
        uint64_t tot  = (e->status == DL_STATUS_ACTIVE && g_active_total) ? g_active_total : e->total;
        int pct = tot ? (int)(done * 100 / tot) : 0;
        int color = e->status == DL_STATUS_COMPLETED ? UI_GREEN
                  : e->status == DL_STATUS_ERROR     ? UI_RED
                  : e->status == DL_STATUS_ACTIVE    ? UI_YELLOW : UI_WHITE;
        int namew = ui_cols() - 22;
        if (namew < 6) namew = 6;
        char line[200];
        snprintf(line, sizeof(line), " %-9s %3d%% %-*.*s",
                 downloads_status_to_str(e->status), pct, namew, namew, e->name);
        ui_text_hl(top + i, idx == g_dl_sel, color, "%s", line);
    }

    /* Live speed / ETA line for whatever is downloading. */
    for (int i = 0; i < g_downloads.count; i++) {
        if (g_downloads.items[i].status != DL_STATUS_ACTIVE) continue;
        char a[24], b[24];
        human_size(g_active_done, a, sizeof(a));
        human_size(g_active_total, b, sizeof(b));
        uint64_t bps = g_active_bps;
        uint64_t eta = (bps && g_active_total > g_active_done)
                     ? (g_active_total - g_active_done) / bps : 0;
        ui_text(ui_rows() - 3, 1, UI_CYAN,
                "%s / %s   %llu KB/s   ETA %llu:%02llu   B=pause",
                a, b, (unsigned long long)(bps / 1024),
                (unsigned long long)(eta / 60), (unsigned long long)(eta % 60));
        break;
    }
}

/* ---- Memory-card / server save sync ---- */

/* Modal yes/no.  A = yes, B = no. */
static bool confirm(const char *fmt, ...) {
    char msg[200];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(msg, sizeof(msg), fmt, ap);
    va_end(ap);
    char body[280];
    snprintf(body, sizeof(body), "%s\n\nA = Yes      B = No", msg);
    ui_clear();
    ui_draw_message("Confirm", body);
    ui_draw_footer("A = Yes   B = No");
    ui_flush();
    for (;;) {
        VIDEO_WaitVSync();
        PAD_ScanPads();
        u32 d = PAD_ButtonsDown(0);
        if (d & PAD_BUTTON_A) return true;
        if (d & PAD_BUTTON_B) return false;
    }
}

/* Overlay server names, but only real ones: the server falls back to the
 * title_id when it has no name, and the scan already filled names from the
 * saves' own comment fields — don't clobber those. */
static void fill_card_names(GcSaveList *list) {
    for (int i = 0; i < list->count; i++) {
        for (int j = 0; j < g_server.count; j++) {
            const ServerSave *sv = &g_server.items[j];
            if (strcasecmp(sv->title_id, list->items[i].title_id) != 0) continue;
            if (sv->name[0] && strcasecmp(sv->name, sv->title_id) != 0) {
                strncpy(list->items[i].name, sv->name,
                        sizeof(list->items[i].name) - 1);
            }
            break;
        }
    }
}

static void mark_server_local(void) {
    for (int i = 0; i < g_server.count; i++) {
        ServerSave *s = &g_server.items[i];
        s->local = false;
        for (int j = 0; j < g_carda.count && !s->local; j++)
            if (strcasecmp(g_carda.items[j].title_id, s->title_id) == 0) s->local = true;
        for (int j = 0; j < g_cardb.count && !s->local; j++)
            if (strcasecmp(g_cardb.items[j].title_id, s->title_id) == 0) s->local = true;
    }
}

static void scan_card_view(int port, GcSaveList *list) {
    ui_status("Scanning memory card slot %c...", 'A' + port);
    redraw();
    saves_scan_card(port, list);
    fill_card_names(list);
    if (list->count > 0) ui_status("Slot %c: %d save(s)", 'A' + port, list->count);
    else ui_status("%s", list->last_error);
    redraw();
}

static void fetch_server(void) {
    if (!network_is_ready(&g_state)) { ui_error("Network not ready"); return; }
    ui_status("Fetching server saves...");
    redraw();
    saves_fetch_server(&g_state, g_scratch, sizeof(g_scratch), &g_server);
    mark_server_local();
    fill_card_names(&g_carda);
    fill_card_names(&g_cardb);
    fill_vmccard_names();
    if (g_server.count > 0) ui_status("Server: %d GC save(s)", g_server.count);
    else ui_error("%s", g_server.last_error);
    redraw();
}

static void upload_card_at(GcSaveList *list, int sel) {
    if (!network_is_ready(&g_state)) { ui_error("Network not ready"); return; }
    if (list->count == 0 || sel >= list->count) return;
    const GcSave *s = &list->items[sel];
    if (!confirm("Upload %s\nfrom slot %c to the server?", s->title_id, 'A' + list->port)) return;
    ui_status("Uploading %s...", s->title_id);
    redraw();
    char msg[128];
    int rc = saves_upload_card_game(&g_state, list->port, s, msg, sizeof(msg));
    if (rc < 0) ui_error("%s", msg); else ui_status("%s", msg);
    redraw();
}

static void restore_card_at(GcSaveList *list, int sel) {
    if (!network_is_ready(&g_state)) { ui_error("Network not ready"); return; }
    if (list->count == 0 || sel >= list->count) return;
    const char *tid = list->items[sel].title_id;
    int port = list->port;
    if (!confirm("Restore %s to slot %c?\nOverwrites the on-card save.", tid, 'A' + port)) return;
    ui_status("Restoring %s...", tid);
    redraw();
    char msg[128];
    int rc = saves_restore_card_game(&g_state, port, tid, msg, sizeof(msg));
    saves_scan_card(port, list);
    fill_card_names(list);
    if (rc < 0) ui_error("%s", msg); else ui_status("%s", msg);
    redraw();
}

/* Server view: restore the selected server save onto a memory-card slot. */
static void server_restore_to(int port) {
    if (!network_is_ready(&g_state)) { ui_error("Network not ready"); return; }
    if (g_server.count == 0 || g_sv_sel >= g_server.count) return;
    const char *tid = g_server.items[g_sv_sel].title_id;
    if (!confirm("Restore %s\nto memory card slot %c?\nOverwrites the on-card save.", tid, 'A' + port))
        return;
    ui_status("Restoring %s to slot %c...", tid, 'A' + port);
    redraw();
    char msg[128];
    int rc = saves_restore_card_game(&g_state, port, tid, msg, sizeof(msg));
    GcSaveList *list = port == 0 ? &g_carda : &g_cardb;
    saves_scan_card(port, list);
    fill_card_names(list);
    mark_server_local();
    if (rc < 0) ui_error("%s", msg); else ui_status("%s", msg);
    redraw();
}

static void draw_card(const GcSaveList *list, int sel, int scroll) {
    int top = ui_list_top(), vis = ui_list_visible();
    if (list->count == 0) {
        ui_text(top + 1, 2, UI_GREY, "%s",
                list->last_error[0] ? list->last_error : "No saves on this card.");
        return;
    }
    int namew = ui_cols() - 25;
    if (namew < 8) namew = 8;
    for (int i = 0; i < vis; i++) {
        int idx = scroll + i;
        if (idx >= list->count) break;
        const GcSave *s = &list->items[idx];
        /* Game ID first (like a real memory card), then server name or the
         * on-card filename as description. */
        const char *nm = s->name[0] ? s->name : s->filename;
        char line[200];
        snprintf(line, sizeof(line), " %s%s %-*.*s %4d blk",
                 s->gamecode, s->company, namew, namew, nm, s->blocks);
        ui_text_hl(top + i, idx == sel, UI_WHITE, "%s", line);
    }
}

static void draw_server(int sel, int scroll) {
    int top = ui_list_top(), vis = ui_list_visible();
    if (g_server.count == 0) {
        ui_text(top + 1, 2, UI_GREY, "%s",
                g_server.last_error[0] ? g_server.last_error : "No GC saves on the server.");
        return;
    }
    int namew = ui_cols() - 6;
    if (namew < 8) namew = 8;
    for (int i = 0; i < vis; i++) {
        int idx = scroll + i;
        if (idx >= g_server.count) break;
        const ServerSave *s = &g_server.items[idx];
        const char *nm = s->name[0] ? s->name : s->title_id;
        char line[200];
        snprintf(line, sizeof(line), " %-*.*s %s", namew, namew, nm, s->local ? "L" : " ");
        ui_text_hl(top + i, idx == sel, s->local ? UI_GREEN : UI_WHITE, "%s", line);
    }
}

/* ---- VMC (saves inside card images on SD) ---- */

static void fill_vmccard_names(void) {
    for (int i = 0; i < g_vmccard.count; i++) {
        for (int j = 0; j < g_server.count; j++) {
            const ServerSave *sv = &g_server.items[j];
            if (strcasecmp(sv->title_id, g_vmccard.saves[i].title_id) != 0) continue;
            if (sv->name[0] && strcasecmp(sv->name, sv->title_id) != 0) {
                strncpy(g_vmccard.saves[i].name, sv->name,
                        sizeof(g_vmccard.saves[i].name) - 1);
            }
            break;
        }
    }
}

/* Open card file ``idx`` from the scan list and list its saves. */
static void open_vmc_card(int idx) {
    if (g_vmc.count == 0) { g_vmccard.count = 0; return; }
    if (idx < 0) idx = 0;
    if (idx >= g_vmc.count) idx = g_vmc.count - 1;
    g_vmc_active = idx;
    g_vmc_sel = 0;
    g_vmc_scroll = 0;
    if (!vmcfs_open(&g_vmccard, g_vmc.items[idx].path)) {
        ui_error("Open %s failed: %s", g_vmc.items[idx].filename, g_vmccard.last_error);
        g_vmccard.count = 0;
        return;
    }
    fill_vmccard_names();
    ui_status("%s: %d save(s)", g_vmccard.filename, g_vmccard.count);
}

static void scan_vmc_view(void) {
    if (!g_state.sd_ready) { ui_error("Storage not ready"); return; }
    ui_status("Scanning card images...");
    redraw();
    saves_scan_vmc(&g_vmc);
    if (g_vmc.count == 0) { g_vmccard.count = 0; ui_status("%s", g_vmc.last_error); }
    else open_vmc_card(g_vmc_active);
    redraw();
}

static void cycle_vmc_card(void) {
    if (g_vmc.count < 1) return;
    open_vmc_card((g_vmc_active + 1) % g_vmc.count);
    redraw();
}

static void upload_vmc_save_at(int sel) {
    if (!network_is_ready(&g_state)) { ui_error("Network not ready"); return; }
    if (g_vmccard.count == 0 || sel >= g_vmccard.count) return;
    const VmcfsSave *s = &g_vmccard.saves[sel];
    if (!confirm("Upload %s\nfrom %s to the server?", s->title_id, g_vmccard.filename))
        return;
    ui_status("Uploading %s...", s->title_id);
    redraw();
    char msg[128];
    int rc = saves_upload_vmc_save(&g_state, &g_vmccard, sel, msg, sizeof(msg));
    if (rc < 0) ui_error("%s", msg); else ui_status("%s", msg);
    redraw();
}

static void restore_vmc_save_at(int sel) {
    if (!network_is_ready(&g_state)) { ui_error("Network not ready"); return; }
    if (g_vmccard.count == 0 || sel >= g_vmccard.count) return;
    const char *tid = g_vmccard.saves[sel].title_id;
    if (!confirm("Restore %s from server\ninto %s?\nOverwrites the save in the image.",
                 tid, g_vmccard.filename))
        return;
    ui_status("Restoring %s...", tid);
    redraw();
    char msg[128];
    int rc = saves_restore_vmc_save(&g_state, &g_vmccard, tid, msg, sizeof(msg));
    fill_vmccard_names();
    if (rc < 0) ui_error("%s", msg); else ui_status("%s", msg);
    redraw();
}

static void import_whole_vmc(void) {
    if (!network_is_ready(&g_state)) { ui_error("Network not ready"); return; }
    if (g_vmc.count == 0) return;
    const SaveVmc *v = &g_vmc.items[g_vmc_active];
    if (!confirm("Import ALL saves from\n%s\nto the server (split per game)?", v->filename))
        return;
    ui_status("Importing %s...", v->filename);
    redraw();
    char msg[128];
    int rc = saves_upload_vmc(&g_state, v, msg, sizeof(msg));
    if (rc < 0) ui_error("%s", msg);
    else { ui_status("%s", msg); fetch_server(); }
    redraw();
}

static void draw_vmc(void) {
    int top = ui_list_top(), vis = ui_list_visible();
    if (g_vmc.count == 0) {
        ui_text(top + 1, 2, UI_GREY, "%s",
                g_vmc.last_error[0] ? g_vmc.last_error : "No card images found.");
        return;
    }

    /* Card banner row: which image is open + cycle position. */
    ui_text(top, 1, UI_CYAN, "Card %d/%d: %s (%d saves)  [Z=next card]",
            g_vmc_active + 1, g_vmc.count, g_vmccard.filename, g_vmccard.count);

    if (g_vmccard.count == 0) {
        ui_text(top + 2, 2, UI_GREY, "%s",
                g_vmccard.last_error[0] ? g_vmccard.last_error : "No saves in this image.");
        return;
    }

    int rows = vis - 1;   /* banner uses the first row */
    int namew = ui_cols() - 25;
    if (namew < 8) namew = 8;
    for (int i = 0; i < rows; i++) {
        int idx = g_vmc_scroll + i;
        if (idx >= g_vmccard.count) break;
        const VmcfsSave *s = &g_vmccard.saves[idx];
        /* Lead with the game ID (like a real memory card); the server name
         * when known, otherwise the on-card filename, as description. */
        const char *nm = s->name[0] ? s->name : s->filename;
        char line[200];
        snprintf(line, sizeof(line), " %s%s %-*.*s %4d blk",
                 s->gamecode, s->company, namew, namew, nm, s->blocks);
        ui_text_hl(top + 1 + i, idx == g_vmc_sel, UI_WHITE, "%s", line);
    }
}

/* ---- MemCard Pro GC GameID switch ---- */

static void mcp_gameid(int port, const char *gamecode, const char *company, const char *name) {
    if (!g_state.mmce_mode[port]) {
        ui_error("Slot %c GameID off (enable in Config)", 'A' + port);
        return;
    }
    char msg[128];
    int rc = saves_mcp_set_gameid(port, gamecode, company, name, msg, sizeof(msg));
    if (rc < 0) { ui_error("%s", msg); redraw(); return; }

    /* The device detaches its virtual card while switching channels
     * (~0.5-1 s): mounting inside that window reads as "no card" (-3).
     * Wait it out, then rescan automatically. */
    ui_status("%s - switching...", msg);
    redraw();
    for (int i = 0; i < 150; i++) VIDEO_WaitVSync();   /* ~2.5 s NTSC */
    scan_card_view(port, port == 0 ? &g_carda : &g_cardb);
    mark_server_local();
    redraw();
}

static const char *hint_for(AppView v) {
    switch (v) {
        case APP_VIEW_CONFIG:    return "Up/Dn=select  L/R=change  A=edit/save        L+R+Start=quit";
        case APP_VIEW_ROMS:      return "A=fetch  X=queue  Y=download now  L/R=view    L+R+Start=quit";
        case APP_VIEW_LOCAL:     return "A=rescan  X=delete  L/R=switch view           L+R+Start=quit";
        case APP_VIEW_DOWNLOADS: return "A=start one  Y=run all  X=remove  B=pause     L+R+Start=quit";
        case APP_VIEW_SAVES:     return "A=upload  Y=restore  Z=next card  X=rescan  Start=import all";
        case APP_VIEW_CARDA:
        case APP_VIEW_CARDB:     return "A=upload  Y=restore  Z=GameID  X=rescan        L+R+Start=quit";
        case APP_VIEW_SERVER:    return "A=restore->A  Y=->B  Z=GameID  X=refresh        L+R+Start=quit";
        default:                 return "L/R=switch view                               L+R+Start=quit";
    }
}

static void redraw(void) {
    ui_clear();
    ui_draw_header(&g_state, g_view);
    switch (g_view) {
        case APP_VIEW_ROMS:      draw_roms(); break;
        case APP_VIEW_LOCAL:     draw_local(); break;
        case APP_VIEW_DOWNLOADS: draw_downloads(); break;
        case APP_VIEW_SAVES:     draw_vmc(); break;
        case APP_VIEW_CARDA:     draw_card(&g_carda, g_ca_sel, g_ca_scroll); break;
        case APP_VIEW_CARDB:     draw_card(&g_cardb, g_cb_sel, g_cb_scroll); break;
        case APP_VIEW_SERVER:    draw_server(g_sv_sel, g_sv_scroll); break;
        case APP_VIEW_CONFIG:    draw_config(); break;
        default: break;
    }
    ui_draw_footer(hint_for(g_view));
    ui_flush();
}

static void cycle_view(int delta) {
    int n = (int)APP_VIEW_COUNT;
    g_view = (AppView)((((int)g_view + delta) % n + n) % n);
}

/* ---- main ---- */

int main(int argc, char **argv) {
    (void)argc; (void)argv;

    /* VIDEO must come up before PAD: libogc's pad sampling is configured
     * from the active video mode.  PAD_Init() first works on Dolphin but
     * leaves the controller dead on real hardware. */
    ui_init();
    PAD_Init();

    show_boot("Mounting SD card...");
    mount_sd(&g_state);
    bool     mounted     = g_state.sd_ready;
    SdDevice mounted_dev = g_state.sd_device;

    /* config_load resets the whole state to defaults (wiping the runtime
     * mount flags) before parsing sd:/3dssync/config.txt — restore the mount
     * result afterwards, then honor the configured device preference. */
    char err[256] = {0};
    config_load(&g_state, err, sizeof(err));
    config_load_console_id(&g_state);
    SdDevice want = g_state.sd_device;

    g_state.sd_ready = mounted;
    if (mounted) g_state.sd_device = mounted_dev;

    if (mounted && want != mounted_dev) {
        show_boot("Remounting SD on configured device...");
        mount_sd_preferring(&g_state, want);
    } else if (!mounted) {
        /* First attempt found nothing; retry preferring the configured
         * device (some adapters need a second probe after power-on). */
        mount_sd_preferring(&g_state, want);
    }
    roms_set_target(g_state.sd_root, g_state.games_folder);

    /* NULL identity = wildcard.  A concrete gamecode/company here makes
     * CARD_Open reject every other game's file (comment reads, uploads) and
     * CARD_Create stamp our code onto restored saves. */
    CARD_Init(NULL, NULL);

    /* Scan memory cards BEFORE the network comes up: the BBA shares EXI
     * channel 0 with slot A, and net traffic during CARD_Mount produced
     * EXI I/O errors (-5) on real hardware. */
    g_view = APP_VIEW_ROMS;
    scan_card_view(0, &g_carda);
    scan_card_view(1, &g_cardb);

    show_boot("Bringing up network (BBA)...");
    network_init(&g_state);

    if (g_state.sd_ready) {
        roms_ensure_target_dirs();
        downloads_load(&g_downloads);
    }

    if (!g_state.sd_ready)
        ui_error("No SD card found (ROM install / VMC disabled)");
    else if (network_is_ready(&g_state))
        ui_status("Ready - ip %s, sd %s", g_state.ip, sd_device_to_str(g_state.sd_device));
    else
        ui_status("SD ready (%s); network not up", sd_device_to_str(g_state.sd_device));

    if (g_state.sd_ready) { scan_local(); saves_scan_vmc(&g_vmc); open_vmc_card(0); }
    if (network_is_ready(&g_state)) { fetch_catalog(); fetch_server(); }

    redraw();

    bool running = true;
    while (running) {
        /* Pace the loop at vsync and only redraw on input.  An unthrottled
         * redraw-every-iteration loop outruns gxflux's frame lifecycle and
         * wedges the GX FIFO on real hardware (screen freezes, input dead). */
        VIDEO_WaitVSync();
        PAD_ScanPads();
        u32 down = PAD_ButtonsDown(0);
        u32 held = PAD_ButtonsHeld(0);

        const u32 quit_combo = PAD_TRIGGER_L | PAD_TRIGGER_R | PAD_BUTTON_START;
        if ((held & quit_combo) == quit_combo) { running = false; }

        else if (down == 0) continue;   /* nothing pressed - keep last frame */

        else if (down & PAD_TRIGGER_L) cycle_view(-1);
        else if (down & PAD_TRIGGER_R) cycle_view(+1);

        else if (g_view == APP_VIEW_ROMS) {
            int c = g_catalog.count;
            if      (down & PAD_BUTTON_UP)    g_rom_sel--;
            else if (down & PAD_BUTTON_DOWN)  g_rom_sel++;
            else if (down & PAD_BUTTON_LEFT)  g_rom_sel -= ui_list_visible();
            else if (down & PAD_BUTTON_RIGHT) g_rom_sel += ui_list_visible();
            else if (down & PAD_BUTTON_A)     fetch_catalog();
            else if (down & PAD_BUTTON_X)     queue_selected_rom(false);
            else if (down & PAD_BUTTON_Y)     queue_selected_rom(true);
            clamp_scroll(&g_rom_sel, &g_rom_scroll, c);
        }
        else if (g_view == APP_VIEW_LOCAL) {
            int c = g_local.count;
            if      (down & PAD_BUTTON_UP)    g_local_sel--;
            else if (down & PAD_BUTTON_DOWN)  g_local_sel++;
            else if (down & PAD_BUTTON_LEFT)  g_local_sel -= ui_list_visible();
            else if (down & PAD_BUTTON_RIGHT) g_local_sel += ui_list_visible();
            else if (down & PAD_BUTTON_A)     scan_local();
            else if (down & PAD_BUTTON_X) {
                if (c > 0 && g_local_sel < c &&
                    confirm("Delete %s\nfrom the SD card?", g_local.items[g_local_sel].filename)) {
                    if (unlink(g_local.items[g_local_sel].path) == 0) {
                        ui_status("Deleted: %s", g_local.items[g_local_sel].filename);
                        scan_local();
                    } else ui_error("Delete failed");
                }
            }
            clamp_scroll(&g_local_sel, &g_local_scroll, g_local.count);
        }
        else if (g_view == APP_VIEW_DOWNLOADS) {
            int c = g_downloads.count;
            if      (down & PAD_BUTTON_UP)    g_dl_sel--;
            else if (down & PAD_BUTTON_DOWN)  g_dl_sel++;
            else if (down & PAD_BUTTON_A) {
                if (c > 0 && g_dl_sel < c) run_active_download(&g_downloads.items[g_dl_sel]);
            }
            else if (down & PAD_BUTTON_Y) run_download_queue();
            else if (down & PAD_BUTTON_X) {
                if (c > 0 && g_dl_sel < c) {
                    downloads_remove(&g_downloads, g_downloads.items[g_dl_sel].rom_id);
                    downloads_save(&g_downloads);
                }
            }
            clamp_scroll(&g_dl_sel, &g_dl_scroll, g_downloads.count);
        }
        else if (g_view == APP_VIEW_SAVES) {
            int vis = ui_list_visible() - 1;   /* banner row */
            if (vis < 1) vis = 1;
            if      (down & PAD_BUTTON_UP)    g_vmc_sel--;
            else if (down & PAD_BUTTON_DOWN)  g_vmc_sel++;
            else if (down & PAD_BUTTON_LEFT)  g_vmc_sel -= vis;
            else if (down & PAD_BUTTON_RIGHT) g_vmc_sel += vis;
            else if (down & PAD_TRIGGER_Z)    cycle_vmc_card();
            else if (down & PAD_BUTTON_X)     scan_vmc_view();
            else if (down & PAD_BUTTON_A)     upload_vmc_save_at(g_vmc_sel);
            else if (down & PAD_BUTTON_Y)     restore_vmc_save_at(g_vmc_sel);
            else if (down & PAD_BUTTON_START) import_whole_vmc();
            /* clamp against save count with the banner-reduced window */
            if (g_vmccard.count == 0) { g_vmc_sel = 0; g_vmc_scroll = 0; }
            else {
                if (g_vmc_sel < 0) g_vmc_sel = 0;
                if (g_vmc_sel >= g_vmccard.count) g_vmc_sel = g_vmccard.count - 1;
                if (g_vmc_sel < g_vmc_scroll) g_vmc_scroll = g_vmc_sel;
                if (g_vmc_sel >= g_vmc_scroll + vis) g_vmc_scroll = g_vmc_sel - vis + 1;
                if (g_vmc_scroll < 0) g_vmc_scroll = 0;
            }
        }
        else if (g_view == APP_VIEW_CARDA) {
            if      (down & PAD_BUTTON_UP)    g_ca_sel--;
            else if (down & PAD_BUTTON_DOWN)  g_ca_sel++;
            else if (down & PAD_BUTTON_LEFT)  g_ca_sel -= ui_list_visible();
            else if (down & PAD_BUTTON_RIGHT) g_ca_sel += ui_list_visible();
            else if (down & PAD_BUTTON_X)     scan_card_view(0, &g_carda);
            else if (down & PAD_BUTTON_A)     upload_card_at(&g_carda, g_ca_sel);
            else if (down & PAD_BUTTON_Y)     restore_card_at(&g_carda, g_ca_sel);
            else if (down & PAD_TRIGGER_Z) {
                if (g_carda.count > 0 && g_ca_sel < g_carda.count) {
                    const GcSave *s = &g_carda.items[g_ca_sel];
                    mcp_gameid(0, s->gamecode, s->company, s->name[0] ? s->name : s->title_id);
                }
            }
            clamp_scroll(&g_ca_sel, &g_ca_scroll, g_carda.count);
        }
        else if (g_view == APP_VIEW_CARDB) {
            if      (down & PAD_BUTTON_UP)    g_cb_sel--;
            else if (down & PAD_BUTTON_DOWN)  g_cb_sel++;
            else if (down & PAD_BUTTON_LEFT)  g_cb_sel -= ui_list_visible();
            else if (down & PAD_BUTTON_RIGHT) g_cb_sel += ui_list_visible();
            else if (down & PAD_BUTTON_X)     scan_card_view(1, &g_cardb);
            else if (down & PAD_BUTTON_A)     upload_card_at(&g_cardb, g_cb_sel);
            else if (down & PAD_BUTTON_Y)     restore_card_at(&g_cardb, g_cb_sel);
            else if (down & PAD_TRIGGER_Z) {
                if (g_cardb.count > 0 && g_cb_sel < g_cardb.count) {
                    const GcSave *s = &g_cardb.items[g_cb_sel];
                    mcp_gameid(1, s->gamecode, s->company, s->name[0] ? s->name : s->title_id);
                }
            }
            clamp_scroll(&g_cb_sel, &g_cb_scroll, g_cardb.count);
        }
        else if (g_view == APP_VIEW_SERVER) {
            if      (down & PAD_BUTTON_UP)    g_sv_sel--;
            else if (down & PAD_BUTTON_DOWN)  g_sv_sel++;
            else if (down & PAD_BUTTON_LEFT)  g_sv_sel -= ui_list_visible();
            else if (down & PAD_BUTTON_RIGHT) g_sv_sel += ui_list_visible();
            else if (down & PAD_BUTTON_X)     fetch_server();
            else if (down & PAD_BUTTON_A)     server_restore_to(0);
            else if (down & PAD_BUTTON_Y)     server_restore_to(1);
            else if (down & PAD_TRIGGER_Z) {
                if (g_server.count > 0 && g_sv_sel < g_server.count) {
                    const ServerSave *s = &g_server.items[g_sv_sel];
                    const char *gc = strncasecmp(s->title_id, "GC_", 3) == 0 ? s->title_id + 3 : s->title_id;
                    /* Use the real maker code when the game is on a scanned
                     * card — MMCE devices key channels on the full 6-char ID. */
                    const char *company = "";
                    for (int i = 0; i < g_carda.count && !company[0]; i++)
                        if (!strcasecmp(g_carda.items[i].title_id, s->title_id))
                            company = g_carda.items[i].company;
                    for (int i = 0; i < g_cardb.count && !company[0]; i++)
                        if (!strcasecmp(g_cardb.items[i].title_id, s->title_id))
                            company = g_cardb.items[i].company;
                    int port = g_state.mmce_mode[0] ? 0 : (g_state.mmce_mode[1] ? 1 : 0);
                    mcp_gameid(port, gc, company, s->name[0] ? s->name : s->title_id);
                }
            }
            clamp_scroll(&g_sv_sel, &g_sv_scroll, g_server.count);
        }
        else if (g_view == APP_VIEW_CONFIG) config_input(down);

        redraw();
    }

    network_shutdown();
    return 0;
}
