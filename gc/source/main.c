/*
 * gcsync — GameCube Save Sync client.
 *
 * Boot: ui_init -> mount SD -> config -> CARD scans -> BBA net -> menu loop.
 *
 * Eight views, cycled with L / R: game catalog, installed games, download
 * queue, card images on SD (VMC), memory card slot A / B, server saves and
 * settings.  Every view is a list on the left plus a detail panel for the
 * selected item on the right (see ui.h for the layout).
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
#include <time.h>
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

static int g_cfg_sel    = 0, g_cfg_scroll   = 0;
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
static DownloadEntry    *g_dl_active    = NULL;   /* drives the progress card */
static uint32_t          g_dl_wait_ms   = 0;      /* server still preparing */
static uint64_t          g_dl_start_ms  = 0;
static uint64_t          g_dl_start_off = 0;
static uint64_t          g_last_draw_ms = 0;
static uint64_t          g_spd_ms       = 0;
static uint64_t          g_spd_bytes    = 0;

/* Progress redraws are spaced at least this far apart: every frame drawn
 * mid-transfer stalls the (one-segment-window) TCP stream for its duration. */
#define PROGRESS_REDRAW_MS 500

static void redraw(void);   /* forward decl: long ops flush mid-run */
static void scan_local(void);
static void scan_vmc_view(void);
static void fill_vmccard_names(void);

/* ---- helpers ---- */

static uint64_t now_ms(void) {
    return ticks_to_millisecs(gettime());
}

static void clamp_scroll_rows(int *sel, int *scroll, int count, int vis) {
    if (count == 0) { *sel = 0; *scroll = 0; return; }
    if (*sel < 0) *sel = 0;
    if (*sel >= count) *sel = count - 1;
    if (vis < 1) vis = 1;
    if (*sel < *scroll) *scroll = *sel;
    if (*sel >= *scroll + vis) *scroll = *sel - vis + 1;
    if (*scroll < 0) *scroll = 0;
}

static void clamp_scroll(int *sel, int *scroll, int count) {
    clamp_scroll_rows(sel, scroll, count, ui_list_visible());
}

static const ServerSave *server_find(const char *title_id) {
    for (int i = 0; i < g_server.count; i++)
        if (strcasecmp(g_server.items[i].title_id, title_id) == 0) return &g_server.items[i];
    return NULL;
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

/* Per-device probe result shown in the Settings view: distinguishes "nothing
 * in the port" from "card detected but the filesystem didn't mount" (libfat
 * is FAT12/16/32 only — exFAT cards read as the latter). */
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
    gui_begin();
    ui_draw_boot(msg);
    gui_end(true);
}

/* ---- Footer hints per view ---- */

#define HINTS(...) { __VA_ARGS__ }
static const GuiHint k_hints_roms[]   = HINTS({"A", "Fetch"}, {"X", "Queue"}, {"Y", "Download"},
                                              {"LR", "Page"}, {"START", "+L+R Quit"});
static const GuiHint k_hints_local[]  = HINTS({"A", "Rescan"}, {"X", "Delete"}, {"LR", "Page"},
                                              {"START", "+L+R Quit"});
static const GuiHint k_hints_dl[]     = HINTS({"A", "Start"}, {"Y", "Run all"}, {"X", "Remove"},
                                              {"B", "Pause"}, {"START", "+L+R Quit"});
static const GuiHint k_hints_dl_run[] = HINTS({"B", "Pause download"});
static const GuiHint k_hints_vmc[]    = HINTS({"A", "Upload"}, {"Y", "Restore"}, {"Z", "Next card"},
                                              {"X", "Rescan"}, {"START", "Import all"});
static const GuiHint k_hints_card[]   = HINTS({"A", "Upload"}, {"Y", "Restore"}, {"Z", "GameID"},
                                              {"X", "Rescan"}, {"START", "+L+R Quit"});
static const GuiHint k_hints_server[] = HINTS({"A", "To slot A"}, {"Y", "To slot B"}, {"Z", "GameID"},
                                              {"X", "Refresh"}, {"START", "+L+R Quit"});
static const GuiHint k_hints_config[] = HINTS({"UD", "Select"}, {"LR", "Change"}, {"A", "Edit / run"},
                                              {"START", "+L+R Quit"});
static const GuiHint k_hints_edit[]   = HINTS({"UD", "Letter"}, {"LR", "Move"}, {"Z", "Insert"},
                                              {"X", "Delete"}, {"A", "OK"}, {"B", "Cancel"});
#define NHINTS(a) ((int)(sizeof(a) / sizeof((a)[0])))

static void view_hints(AppView v, const GuiHint **h, int *n) {
    switch (v) {
        case APP_VIEW_ROMS:      *h = k_hints_roms;   *n = NHINTS(k_hints_roms); break;
        case APP_VIEW_LOCAL:     *h = k_hints_local;  *n = NHINTS(k_hints_local); break;
        case APP_VIEW_DOWNLOADS: *h = k_hints_dl;     *n = NHINTS(k_hints_dl); break;
        case APP_VIEW_SAVES:     *h = k_hints_vmc;    *n = NHINTS(k_hints_vmc); break;
        case APP_VIEW_CARDA:
        case APP_VIEW_CARDB:     *h = k_hints_card;   *n = NHINTS(k_hints_card); break;
        case APP_VIEW_SERVER:    *h = k_hints_server; *n = NHINTS(k_hints_server); break;
        default:                 *h = k_hints_config; *n = NHINTS(k_hints_config); break;
    }
}

static void draw_view(void);   /* body of redraw(), without begin/end */

/* ---- Controller text editor (on-screen character picker) ---- */

static const char CHARSET[] =
    " ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789.:/_-?=&%@";

static char charset_step(char c, int dir) {
    int n = (int)sizeof(CHARSET) - 1;
    const char *pos = strchr(CHARSET, c);
    int idx = pos ? (int)(pos - CHARSET) : 1;
    idx = ((idx + dir) % n + n) % n;
    return CHARSET[idx];
}

static float text_w_n(float scale, const char *s, int n) {
    char tmp[256];
    if (n < 0) n = 0;
    if (n > (int)sizeof(tmp) - 1) n = (int)sizeof(tmp) - 1;
    memcpy(tmp, s, n);
    tmp[n] = '\0';
    return gui_text_w(scale, tmp);
}

static void draw_edit(const char *label, const char *buf, int cur) {
    gui_begin();
    draw_view();
    gui_dim();

    float w = 540, h = 172, x = (GUI_W - w) / 2, y = 132;
    char title[64];
    snprintf(title, sizeof(title), "Edit %s", label);
    gui_card(x, y, w, h, title, HEX_ACCENT);

    /* Text box; scrolls so the caret stays visible */
    float bx = x + 18, by = y + 52, bw = w - 36, bh = 38;
    float sc = GUI_S_BODY;
    gui_rrect(bx - 2, by - 2, bw + 4, bh + 4, 9, gui_rgb(HEX_ACCENT));
    gui_rrect(bx, by, bw, bh, 7, gui_rgb(HEX_BG));
    int len = (int)strlen(buf);
    int start = 0;
    float cw_min = gui_text_w(sc, "M");
    while (start < cur && text_w_n(sc, buf + start, cur - start) + cw_min > bw - 24) start++;
    float tx = bx + 12, ty = by + (bh - gui_line_h(sc)) / 2 + 2;
    float caret_x = tx + text_w_n(sc, buf + start, cur - start);
    char cur_ch[2] = { cur < len ? buf[cur] : ' ', 0 };
    float caret_w = cur < len && buf[cur] != ' ' ? gui_text_w(sc, cur_ch) : cw_min * 0.7f;
    gui_rrect(caret_x - 1, by + 6, caret_w + 2, bh - 12, 3, gui_rgba(HEX_ACCENT, 0x90));
    char vis[200];
    snprintf(vis, sizeof(vis), "%s", buf + start);
    gui_text_fit(tx, ty, sc, gui_rgb(HEX_TEXT), GUI_LEFT, bw - 24, vis);
    if (len == 0)
        gui_text(tx + cw_min, ty, sc, gui_rgb(HEX_MUTED), GUI_LEFT, "(empty)");

    /* Letter wheel: what Up / Down will put under the caret */
    float wy = by + bh + 22;
    gui_text(bx, wy + 4, GUI_S_TINY, gui_rgb(HEX_DIM), GUI_LEFT, "Up / Down picks:");
    char c0 = cur < len ? buf[cur] : ' ';
    float wx = bx + 150;
    for (int d = -3; d <= 3; d++) {
        char c = c0;
        for (int k = 0; k < (d < 0 ? -d : d); k++) c = charset_step(c, d < 0 ? -1 : 1);
        char lab[8];
        if (c == ' ') snprintf(lab, sizeof(lab), "spc");
        else snprintf(lab, sizeof(lab), "%c", c);
        bool mid = d == 0;
        float pw = mid
            ? gui_pill(wx, wy, 26, GUI_S_SMALL, gui_rgb(HEX_ACCENT), gui_rgb(HEX_INK), lab)
            : gui_pill(wx, wy + 3, 20, GUI_S_TINY, gui_rgb(HEX_PANEL_HI),
                       gui_rgb(d == -1 || d == 1 ? HEX_TEXT : HEX_MUTED), lab);
        wx += pw + 6;
    }
    gui_textf(x + w - 18, y + h - 28, GUI_S_TINY, gui_rgb(HEX_MUTED), GUI_RIGHT,
              "%d chars", len);

    gui_footer(k_hints_edit, NHINTS(k_hints_edit));
    gui_end(true);
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
            snprintf(out, cap, "%s", buf); return true;
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

/* ---- Modal yes/no.  A = yes, B = no. ---- */

static bool confirm(u32 tone, const char *title, const char *fmt, ...)
    __attribute__((format(printf, 3, 4)));

static bool confirm(u32 tone, const char *title, const char *fmt, ...) {
    char msg[240];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(msg, sizeof(msg), fmt, ap);
    va_end(ap);
    gui_begin();
    draw_view();
    ui_draw_confirm(title, msg, tone);
    gui_end(true);
    for (;;) {
        VIDEO_WaitVSync();
        PAD_ScanPads();
        u32 d = PAD_ButtonsDown(0);
        if (d & PAD_BUTTON_A) return true;
        if (d & PAD_BUTTON_B) return false;
    }
}

/* ---- Settings view ---- */

typedef enum {
    CF_SERVER = 0, CF_APIKEY, CF_NETMODE, CF_IP, CF_NM, CF_GW,
    CF_SDDEV, CF_GAMES, CF_GIDA, CF_GIDB, CF_TESTSD, CF_SAVE, CF_COUNT
} CfgField;

static const char *const k_cfg_label[CF_COUNT] = {
    "Server URL", "API key", "Network", "Static IP", "Netmask", "Gateway",
    "SD device", "Games folder", "GameID slot A", "GameID slot B",
    "Test SD read / write", "Save settings to SD",
};

static const char *const k_cfg_help[CF_COUNT] = {
    "Address of your GameSync server, e.g. http://192.168.1.100:8000",
    "Must match SYNC_API_KEY on the server.",
    "DHCP or a static address for the Broadband Adapter. Used at the next boot (save first).",
    "Only used when Network is set to static.",
    "Only used when Network is set to static.",
    "Only used when Network is set to static.",
    "Where ROMs, card images and settings live. A remounts now. notFAT32 = card seen but no FAT volume (exFAT is not supported).",
    "Folder for installed ISOs. \"/\" = card root (GC Loader style).",
    "Send the game ID to a MemCard Pro GC / GCMCE in slot A (Z on card and server views) so it switches to that game's card.",
    "Send the game ID to a MemCard Pro GC / GCMCE in slot B.",
    "Lists the card root, writes a probe file and reads it back; reports the first step that fails.",
    "Writes sd:/3dssync/config.txt.",
};

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

static void cfg_value(int f, char *out, size_t n) {
    switch (f) {
        case CF_SERVER:  snprintf(out, n, "%s", g_state.server_url); break;
        case CF_APIKEY: {
            /* Show only the first characters of the key */
            size_t l = strlen(g_state.api_key);
            if (l == 0) snprintf(out, n, "(not set)");
            else snprintf(out, n, "%.4s%s", g_state.api_key, l > 4 ? "*****" : "");
            break;
        }
        case CF_NETMODE: snprintf(out, n, "%s", g_state.use_static_ip ? "Static" : "DHCP"); break;
        case CF_IP:      snprintf(out, n, "%s", g_state.static_ip); break;
        case CF_NM:      snprintf(out, n, "%s", g_state.static_netmask); break;
        case CF_GW:      snprintf(out, n, "%s", g_state.static_gateway); break;
        case CF_SDDEV:   snprintf(out, n, "%s", sd_device_to_str(g_state.sd_device)); break;
        case CF_GAMES:   snprintf(out, n, "%s", g_state.games_folder); break;
        case CF_GIDA:    snprintf(out, n, "%s", g_state.mmce_mode[0] ? "On" : "Off"); break;
        case CF_GIDB:    snprintf(out, n, "%s", g_state.mmce_mode[1] ? "On" : "Off"); break;
        default:         snprintf(out, n, ">"); break;
    }
}

static void row_config(int idx, float x, float y, float w, float h, bool sel) {
    bool action = idx >= CF_TESTSD;
    u32 lab = action ? (idx == CF_SAVE ? HEX_OK : HEX_ACCENT2) : HEX_TEXT;
    if (action) gui_rect(x + 6, y - 1, w - 12, 1, gui_rgb(sel ? HEX_ACCENT : HEX_LINE));
    float lw = gui_text_mid(x + 10, y, h, GUI_S_SMALL, gui_rgb(sel ? HEX_INK : lab), GUI_LEFT, 0,
                            k_cfg_label[idx]);
    char v[sizeof(g_state.server_url)];
    cfg_value(idx, v, sizeof(v));
    bool toggle = idx == CF_NETMODE || idx == CF_GIDA || idx == CF_GIDB || idx == CF_SDDEV;
    if (toggle && !sel) {
        bool on = (idx == CF_GIDA && g_state.mmce_mode[0]) || (idx == CF_GIDB && g_state.mmce_mode[1]) ||
                  idx == CF_NETMODE || idx == CF_SDDEV;
        float pw = gui_pill_w(h - 6, GUI_S_TINY, v);
        gui_pill(x + w - 8 - pw, y + 3, h - 6, GUI_S_TINY,
                 gui_mix(HEX_PANEL, on ? HEX_ACCENT : HEX_MUTED, 0.30f),
                 gui_rgb(on ? HEX_ACCENT2 : HEX_DIM), v);
        return;
    }
    gui_text_mid(x + w - 10, y, h, GUI_S_TINY, gui_rgb(sel ? HEX_INK : HEX_DIM), GUI_RIGHT,
                 w - lw - 34, v);
}

static void draw_config(void) {
    ui_list("Settings", CF_COUNT, g_cfg_sel, g_cfg_scroll, row_config, NULL);

    float y = ui_detail_begin(k_cfg_label[g_cfg_sel]);
    float x = UI_DETAIL_X + UI_PAD, w = UI_DETAIL_W - 2 * UI_PAD;
    int n = gui_text_wrap(x, y, GUI_S_TINY, gui_rgb(HEX_DIM), w, 6, k_cfg_help[g_cfg_sel]);
    y += n * gui_line_h(GUI_S_TINY) + 10;

    if (g_cfg_sel == CF_SERVER || g_cfg_sel == CF_APIKEY) {
        y = ui_detail_field(y, "Console ID", g_state.console_id, HEX_TEXT);
        y = ui_detail_field(y, "Config file", CONFIG_PATH, HEX_TEXT);
    } else if (g_cfg_sel <= CF_GW) {
        y = ui_detail_field(y, "Current address", g_state.net_ready ? g_state.ip : "offline",
                            g_state.net_ready ? HEX_OK : HEX_ERR);
        y = ui_detail_field(y, "Gateway in use", g_state.gateway, HEX_TEXT);
    }

    /* SD probe results (updated on every mount attempt) */
    float py = UI_LIST_Y + UI_LIST_H - 64;
    gui_text(x, py, GUI_S_TINY, gui_rgb(HEX_MUTED), GUI_LEFT, "SD probe");
    py += 20;
    static const char *const devs[SD_DEV_COUNT] = { "SP2", "GeckoA", "GeckoB" };
    float px = x;
    for (int d = 0; d < SD_DEV_COUNT; d++) {
        const char *s = g_sd_diag[d];
        u32 hex = !strcmp(s, "MOUNTED") ? HEX_OK : !strcmp(s, "notFAT32") ? HEX_ERR : HEX_MUTED;
        char lab[64];
        snprintf(lab, sizeof(lab), "%s %s", devs[d], !strcmp(s, "MOUNTED") ? "ok" : s);
        float pw = gui_pill_w(20, GUI_S_TINY, lab);
        if (px + pw > x + w) { px = x; py += 24; }
        px += gui_pill_outline(px, py, 20, GUI_S_TINY, gui_rgb(hex), gui_rgb(HEX_PANEL), lab) + 5;
    }
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
    clamp_scroll(&g_cfg_sel, &g_cfg_scroll, CF_COUNT);
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

/* Redraw for progress paths: the previous frame flipped >= 500 ms ago, so
 * skipping the retrace wait can't tear and doesn't stall the stream. */
static void redraw_progress(void) {
    g_last_draw_ms = now_ms();
    gui_begin();
    draw_view();
    gui_end(false);
}

static int progress_cb(uint64_t done, uint64_t total) {
    g_active_done = done;
    g_active_total = total;
    g_dl_wait_ms = 0;
    PAD_ScanPads();
    if (PAD_ButtonsDown(0) & PAD_BUTTON_B) g_pause_req = true;

    uint64_t now = now_ms();
    if (g_spd_ms == 0) { g_spd_ms = now; g_spd_bytes = done; }
    else if (now - g_spd_ms >= 1000) {
        g_active_bps = (done - g_spd_bytes) * 1000 / (now - g_spd_ms);
        g_spd_ms = now;
        g_spd_bytes = done;
    }
    if (now - g_last_draw_ms >= PROGRESS_REDRAW_MS || (total && done >= total))
        redraw_progress();
    return g_pause_req ? 1 : 0;
}

/* Pump the UI while the server prepares a response (e.g. RVZ->ISO
 * conversion, which can take minutes).  B cancels -> download pauses. */
static int download_wait_cb(uint32_t ms) {
    PAD_ScanPads();
    if (PAD_ButtonsDown(0) & PAD_BUTTON_B) return 1;
    /* ms==0 is the parallel loop's per-pass cancel poll — progress_cb owns
     * the periodic redraw there; only the (single-stream) conversion wait
     * passes a real elapsed time and wants the "waiting" state. */
    if (ms && (ms % 1000) == 0) {
        g_dl_wait_ms = ms;
        ui_status("Waiting for server... %us (converting? B=cancel)", ms / 1000);
        redraw_progress();
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
    g_active_bps   = 0;
    g_spd_ms       = 0;
    g_dl_wait_ms   = 0;
    g_dl_active    = e;
    g_dl_start_ms  = now_ms();
    g_dl_start_off = e->offset;
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
    g_dl_active = NULL;
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
        g_dl_sel = i;
        clamp_scroll(&g_dl_sel, &g_dl_scroll, g_downloads.count);
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
        /* Switch to the Downloads view so progress/speed are visible behind
         * the progress card. */
        g_dl_sel = (int)(e - g_downloads.items);
        clamp_scroll(&g_dl_sel, &g_dl_scroll, g_downloads.count);
        g_view = APP_VIEW_DOWNLOADS;
        run_active_download(e);
    } else {
        ui_status("Queued: %s", e->name);
    }
}

/* ---- Download status presentation ---- */

static u32 dl_status_hex(DownloadStatus s) {
    switch (s) {
        case DL_STATUS_COMPLETED: return HEX_OK;
        case DL_STATUS_ERROR:     return HEX_ERR;
        case DL_STATUS_ACTIVE:    return HEX_INFO;
        case DL_STATUS_PAUSED:    return HEX_WARN;
        default:                  return HEX_DIM;
    }
}

static const char *dl_status_label(DownloadStatus s) {
    switch (s) {
        case DL_STATUS_COMPLETED: return "Installed";
        case DL_STATUS_ERROR:     return "Error";
        case DL_STATUS_ACTIVE:    return "Downloading";
        case DL_STATUS_PAUSED:    return "Paused";
        default:                  return "Queued";
    }
}

static void dl_progress(const DownloadEntry *e, uint64_t *done, uint64_t *tot) {
    bool act = e->status == DL_STATUS_ACTIVE;
    *done = act ? g_active_done : e->offset;
    *tot  = (act && g_active_total) ? g_active_total : e->total;
}

static void fmt_clock(uint64_t secs, char *out, size_t n) {
    if (secs >= 3600)
        snprintf(out, n, "%llu:%02llu:%02llu", (unsigned long long)(secs / 3600),
                 (unsigned long long)(secs / 60 % 60), (unsigned long long)(secs % 60));
    else
        snprintf(out, n, "%llu:%02llu", (unsigned long long)(secs / 60),
                 (unsigned long long)(secs % 60));
}

/* ---- Catalog view ---- */

static bool local_has_path(const char *path) {
    for (int i = 0; i < g_local.count; i++)
        if (strcasecmp(g_local.items[i].path, path) == 0) return true;
    return false;
}

static void row_status_dot(float x, float y, float h, bool sel, u32 hex) {
    gui_circle(x + 9, y + h / 2, 4, gui_rgb(sel ? HEX_INK : hex));
}

static void row_rom(int idx, float x, float y, float w, float h, bool sel) {
    RomEntry *r = &g_catalog.items[idx];
    char sz[16];
    ui_human_size(r->size, sz, sizeof(sz));
    const DownloadEntry *e = downloads_find(&g_downloads, r->rom_id);
    if (e) row_status_dot(x, y, h, sel, dl_status_hex(e->status));
    else gui_circle(x + 9, y + h / 2, 2, gui_rgb(sel ? HEX_INK : HEX_MUTED));
    ui_row_text(x + 20, y, w - 20, h, sel, HEX_TEXT, r->name, sz);
}

static void draw_roms(void) {
    ui_list("GameCube games", g_catalog.count, g_rom_sel, g_rom_scroll, row_rom,
            g_catalog.last_error[0] ? g_catalog.last_error
                                    : "No GameCube games loaded. Press A to fetch the catalog.");
    if (g_catalog.count == 0 || g_rom_sel >= g_catalog.count) {
        ui_detail_empty("GC", network_is_ready(&g_state)
                        ? "Press A to fetch the server's game catalog."
                        : "The network is offline - check the BBA and Settings.");
        return;
    }
    const RomEntry *r = &g_catalog.items[g_rom_sel];
    float y = ui_detail_begin(r->name);
    float px = UI_DETAIL_X + UI_PAD;
    char sz[16];
    ui_human_size(r->size, sz, sizeof(sz));
    ui_detail_pill(&px, y, gui_rgb(HEX_GC), gui_rgb(0xFFFFFF), "GC");
    ui_detail_pill(&px, y, gui_rgb(HEX_PANEL_HI), gui_rgb(HEX_TEXT), sz);
    if (!strcasecmp(r->extract_format, "rvz"))
        ui_detail_pill(&px, y, gui_rgb(HEX_PANEL_HI), gui_rgb(HEX_ACCENT2), "RVZ > ISO");
    y += 30;

    char target[260];
    bool have_target = roms_resolve_target_path(r, target, sizeof(target));
    const DownloadEntry *e = downloads_find(&g_downloads, r->rom_id);
    char st[48];
    u32 st_hex = HEX_DIM;
    if (have_target && local_has_path(target)) { snprintf(st, sizeof(st), "Installed"); st_hex = HEX_OK; }
    else if (e) {
        uint64_t done, tot;
        dl_progress(e, &done, &tot);
        if (e->status == DL_STATUS_PAUSED && tot)
            snprintf(st, sizeof(st), "Paused at %d%%", (int)(done * 100 / tot));
        else snprintf(st, sizeof(st), "%s", dl_status_label(e->status));
        st_hex = dl_status_hex(e->status);
    } else snprintf(st, sizeof(st), "Not installed");

    y = ui_detail_field(y, "STATUS", st, st_hex);
    y = ui_detail_field(y, "FILE", r->filename, HEX_TEXT);
    y = ui_detail_field(y, "INSTALLS TO", have_target ? target : "-", HEX_TEXT);
    if (!strcasecmp(r->extract_format, "rvz"))
        ui_detail_field(y, "NOTE", "Server converts RVZ to ISO", HEX_DIM);
}

/* ---- Installed view ---- */

static void row_local(int idx, float x, float y, float w, float h, bool sel) {
    LocalRom *r = &g_local.items[idx];
    char sz[16];
    ui_human_size(r->size, sz, sizeof(sz));
    row_status_dot(x, y, h, sel, HEX_OK);
    ui_row_text(x + 20, y, w - 20, h, sel, HEX_TEXT, r->name, sz);
}

static void draw_local(void) {
    ui_list("Installed on SD", g_local.count, g_local_sel, g_local_scroll, row_local,
            g_local.last_error[0] ? g_local.last_error : "No installed ISOs.");
    if (g_local.count == 0 || g_local_sel >= g_local.count) {
        ui_detail_empty("SD", g_state.sd_ready ? "Games you install from the catalog show up here."
                                               : "No SD card mounted - see Settings.");
        return;
    }
    const LocalRom *r = &g_local.items[g_local_sel];
    float y = ui_detail_begin(r->name);
    float px = UI_DETAIL_X + UI_PAD;
    char sz[16];
    ui_human_size(r->size, sz, sizeof(sz));
    ui_detail_pill(&px, y, gui_rgb(HEX_GC), gui_rgb(0xFFFFFF), "GC");
    ui_detail_pill(&px, y, gui_rgb(HEX_OK), gui_rgb(HEX_INK), "Installed");
    y += 30;
    y = ui_detail_field(y, "SIZE", sz, HEX_TEXT);
    y = ui_detail_field(y, "FILE", r->filename, HEX_TEXT);
    ui_detail_field(y, "PATH", r->path, HEX_TEXT);
}

/* ---- Downloads view ---- */

static void row_download(int idx, float x, float y, float w, float h, bool sel) {
    DownloadEntry *e = &g_downloads.items[idx];
    uint64_t done, tot;
    dl_progress(e, &done, &tot);
    char right[16];
    if (e->status == DL_STATUS_COMPLETED) snprintf(right, sizeof(right), "done");
    else snprintf(right, sizeof(right), "%d%%", tot ? (int)(done * 100 / tot) : 0);
    row_status_dot(x, y, h, sel, dl_status_hex(e->status));
    ui_row_text(x + 20, y, w - 20, h, sel, HEX_TEXT, e->name, right);
    /* Thin progress underline for partial entries */
    if (tot && done > 0 && done < tot && !sel) {
        float bw = (w - 30) * (float)done / (float)tot;
        gui_rect(x + 20, y + h - 2, bw, 2, gui_rgb(dl_status_hex(e->status)));
    }
}

static void draw_downloads(void) {
    ui_list("Download queue", g_downloads.count, g_dl_sel, g_dl_scroll, row_download,
            "Queue empty. Queue games from the Catalog with X, or download right away with Y.");
    if (g_downloads.count == 0 || g_dl_sel >= g_downloads.count) {
        ui_detail_empty("DL", "Downloads resume where they stopped - B pauses, A resumes.");
        return;
    }
    const DownloadEntry *e = &g_downloads.items[g_dl_sel];
    float y = ui_detail_begin(e->name);
    float px = UI_DETAIL_X + UI_PAD, w = UI_DETAIL_W - 2 * UI_PAD;
    u32 hex = dl_status_hex(e->status);
    ui_detail_pill(&px, y, gui_mix(HEX_PANEL, hex, 0.30f), gui_rgb(hex), dl_status_label(e->status));
    y += 30;

    uint64_t done, tot;
    dl_progress(e, &done, &tot);
    float frac = tot ? (float)done / (float)tot : 0;
    gui_bar(UI_DETAIL_X + UI_PAD, y, w, 12, frac, gui_rgb(hex == HEX_DIM ? HEX_ACCENT : hex));
    y += 18;
    char a[24], b[24], line[64];
    ui_human_size(done, a, sizeof(a));
    ui_human_size(tot, b, sizeof(b));
    if (tot) snprintf(line, sizeof(line), "%s / %s  (%d%%)", a, b, (int)(done * 100 / tot));
    else snprintf(line, sizeof(line), "%s", done ? a : "Not started");
    gui_text(UI_DETAIL_X + UI_PAD, y, GUI_S_TINY, gui_rgb(HEX_TEXT), GUI_LEFT, line);
    y += gui_line_h(GUI_S_TINY) + 8;
    ui_detail_field(y, "SAVES TO", e->target_path, HEX_TEXT);
}

/* Modal progress card while a transfer runs (drawn over any view). */
static void draw_download_card(void) {
    const DownloadEntry *e = g_dl_active;
    gui_dim();
    float w = 460, h = 186, x = (GUI_W - w) / 2, y = 128;
    gui_card(x, y, w, h, "Downloading", HEX_ACCENT);
    float ix = x + 20, iw = w - 40, cy = y + 46;
    gui_text_fit(ix, cy, GUI_S_BODY, gui_rgb(HEX_TEXT), GUI_LEFT, iw, e->name);
    cy += gui_line_h(GUI_S_BODY) + 2;
    char sub[DOWNLOAD_PATH_LEN + 16];
    snprintf(sub, sizeof(sub), "Writing to %s", e->target_path);
    gui_text_fit(ix, cy, GUI_S_TINY, gui_rgb(HEX_DIM), GUI_LEFT, iw, sub);
    cy += gui_line_h(GUI_S_TINY) + 10;

    uint64_t done = g_active_done, tot = g_active_total;
    bool waiting = g_dl_wait_ms > 0 || (tot == 0 && done == 0);
    gui_bar(ix, cy, iw, 16, waiting ? -1.0f : (tot ? (float)done / (float)tot : 0),
            gui_rgb(HEX_ACCENT));
    cy += 24;

    char a[24], b[24], line[128];
    if (waiting) {
        snprintf(line, sizeof(line), "Waiting for the server... %us", g_dl_wait_ms / 1000);
        gui_text(ix, cy, GUI_S_SMALL, gui_rgb(HEX_WARN), GUI_LEFT, line);
        cy += gui_line_h(GUI_S_SMALL);
        gui_text(ix, cy, GUI_S_TINY, gui_rgb(HEX_DIM), GUI_LEFT,
                 "Large RVZ images are converted to ISO before streaming.");
    } else {
        ui_human_size(done, a, sizeof(a));
        ui_human_size(tot, b, sizeof(b));
        snprintf(line, sizeof(line), "%s / %s", a, b);
        gui_text(ix, cy, GUI_S_SMALL, gui_rgb(HEX_TEXT), GUI_LEFT, line);
        if (tot) gui_textf(ix + iw, cy, GUI_S_SMALL, gui_rgb(HEX_ACCENT2), GUI_RIGHT,
                           "%d%%", (int)(done * 100 / tot));
        cy += gui_line_h(GUI_S_SMALL) + 2;
        uint64_t bps = g_active_bps;
        uint64_t el = (now_ms() - g_dl_start_ms) / 1000;
        uint64_t eta = (bps && tot > done) ? (tot - done) / bps : 0;
        char els[32], etas[32];
        fmt_clock(el, els, sizeof(els));
        fmt_clock(eta, etas, sizeof(etas));
        if (bps) snprintf(line, sizeof(line), "Speed %llu KB/s    Elapsed %s    Remaining %s",
                          (unsigned long long)(bps / 1024), els, etas);
        else snprintf(line, sizeof(line), "Measuring speed...    Elapsed %s", els);
        gui_text(ix, cy, GUI_S_TINY, gui_rgb(HEX_DIM), GUI_LEFT, line);
    }

    float by = y + h - 30;
    gui_rect(x + 12, by - 6, w - 24, 2, gui_rgb(HEX_LINE));
    float bx = gui_text_mid(ix, by, 22, GUI_S_SMALL, gui_rgb(HEX_DIM), GUI_LEFT, 0, "Press");
    bx = ix + bx + 6;
    bx += gui_button(bx, by + 11, "B") + 6;
    gui_text_mid(bx, by, 22, GUI_S_SMALL, gui_rgb(HEX_DIM), GUI_LEFT, 0, "to pause - A resumes later");
}

/* ---- Memory-card / server save sync ---- */

/* Overlay server names, but only real ones: the server falls back to the
 * title_id when it has no name, and the scan already filled names from the
 * saves' own comment fields — don't clobber those. */
static void fill_card_names(GcSaveList *list) {
    for (int i = 0; i < list->count; i++) {
        const ServerSave *sv = server_find(list->items[i].title_id);
        if (sv && sv->name[0] && strcasecmp(sv->name, sv->title_id) != 0)
            snprintf(list->items[i].name, sizeof(list->items[i].name), "%s", sv->name);
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
    if (!confirm(HEX_WARN, "Upload save",
                 "Upload %s from slot %c to the server?\n%s",
                 s->title_id, 'A' + list->port, s->name[0] ? s->name : s->filename)) return;
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
    if (!confirm(HEX_INFO, "Restore save",
                 "Restore %s to slot %c?\nOverwrites the save on the card.", tid, 'A' + port)) return;
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
    if (!confirm(HEX_INFO, "Restore save",
                 "Restore %s to memory card slot %c?\nOverwrites the save on the card.",
                 tid, 'A' + port))
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

/* Shared save row: 6-char game ID pill (like a real card manager), name,
 * block count.  `on_server` tints the ID pill green. */
static void save_row(float x, float y, float w, float h, bool sel, const char *gamecode,
                     const char *company, const char *name, int blocks, bool on_server) {
    char id[12], blk[16];
    snprintf(id, sizeof(id), "%s%s", gamecode, company);
    snprintf(blk, sizeof(blk), "%d blk", blocks);
    float pw = ui_row_pill(x + 4, y, h, sel, on_server ? HEX_OK : HEX_DIM, id);
    ui_row_text(x + 12 + pw, y, w - 12 - pw, h, sel, HEX_TEXT, name, blk);
}

static void save_detail(const char *name, const char *gamecode, const char *company,
                        const char *title_id, const char *filename, int blocks,
                        const char *where_label, const char *where) {
    float y = ui_detail_begin(name);
    float px = UI_DETAIL_X + UI_PAD;
    char id[12], blk[24];
    snprintf(id, sizeof(id), "%s%s", gamecode, company);
    snprintf(blk, sizeof(blk), "%d block%s", blocks, blocks == 1 ? "" : "s");
    ui_detail_pill(&px, y, gui_rgb(HEX_GC), gui_rgb(0xFFFFFF), id);
    ui_detail_pill(&px, y, gui_rgb(HEX_PANEL_HI), gui_rgb(HEX_TEXT), blk);
    y += 30;
    const ServerSave *sv = server_find(title_id);
    y = ui_detail_field(y, "SERVER", sv ? "Saved on server" : (g_server.count ? "Not on server" : "Unknown"),
                        sv ? HEX_OK : HEX_WARN);
    y = ui_detail_field(y, "TITLE ID", title_id, HEX_TEXT);
    y = ui_detail_field(y, "FILE ON CARD", filename, HEX_TEXT);
    ui_detail_field(y, where_label, where, HEX_TEXT);
}

static const GcSaveList *g_row_card;   /* list the card row callback reads */

static void row_card(int idx, float x, float y, float w, float h, bool sel) {
    const GcSave *s = &g_row_card->items[idx];
    save_row(x, y, w, h, sel, s->gamecode, s->company, s->name[0] ? s->name : s->filename,
             s->blocks, server_find(s->title_id) != NULL);
}

static void draw_card(const GcSaveList *list, int sel, int scroll) {
    char title[48];
    snprintf(title, sizeof(title), "Slot %c - %d save%s", 'A' + list->port, list->count,
             list->count == 1 ? "" : "s");
    g_row_card = list;
    ui_list(title, list->count, sel, scroll, row_card,
            list->last_error[0] ? list->last_error : "No saves on this card.");
    if (list->count == 0 || sel >= list->count) {
        ui_detail_empty(list->port ? "B" : "A", "Insert a memory card and press X to rescan.");
        return;
    }
    const GcSave *s = &list->items[sel];
    char where[48];
    snprintf(where, sizeof(where), "Slot %c%s", 'A' + list->port,
             g_state.mmce_mode[list->port] ? "  (GameID on)" : "");
    save_detail(s->name[0] ? s->name : s->filename, s->gamecode, s->company, s->title_id,
                s->filename, s->blocks, "CARD", where);
}

static void row_server(int idx, float x, float y, float w, float h, bool sel) {
    const ServerSave *s = &g_server.items[idx];
    const char *nm = s->name[0] ? s->name : s->title_id;
    float pw = 0;
    if (s->local) {
        pw = gui_pill_w(h - 6, GUI_S_TINY, "on card");
        ui_row_pill(x + w - 6 - pw, y, h, sel, HEX_OK, "on card");
        pw += 8;
    }
    row_status_dot(x, y, h, sel, s->local ? HEX_OK : HEX_INFO);
    ui_row_text(x + 20, y, w - 20 - pw, h, sel, HEX_TEXT, nm, NULL);
}

static void draw_server(int sel, int scroll) {
    ui_list("Saves on the server", g_server.count, sel, scroll, row_server,
            g_server.last_error[0] ? g_server.last_error : "No GameCube saves on the server.");
    if (g_server.count == 0 || sel >= g_server.count) {
        ui_detail_empty("SV", "Press X to refresh the list from the server.");
        return;
    }
    const ServerSave *s = &g_server.items[sel];
    float y = ui_detail_begin(s->name[0] ? s->name : s->title_id);
    float px = UI_DETAIL_X + UI_PAD;
    const char *code = strncasecmp(s->title_id, "GC_", 3) == 0 ? s->title_id + 3 : s->title_id;
    ui_detail_pill(&px, y, gui_rgb(HEX_GC), gui_rgb(0xFFFFFF), code);
    if (s->local) ui_detail_pill(&px, y, gui_rgb(HEX_OK), gui_rgb(HEX_INK), "On card");
    y += 30;
    y = ui_detail_field(y, "TITLE ID", s->title_id, HEX_TEXT);
    char when[40] = "-";
    if (s->timestamp) {
        time_t t = (time_t)s->timestamp;
        struct tm *tm = gmtime(&t);
        if (tm) strftime(when, sizeof(when), "%Y-%m-%d %H:%M UTC", tm);
    }
    y = ui_detail_field(y, "LAST UPLOAD", when, HEX_TEXT);
    const char *where = "Not on a memory card";
    for (int i = 0; i < g_carda.count; i++)
        if (!strcasecmp(g_carda.items[i].title_id, s->title_id)) { where = "Memory card slot A"; break; }
    if (where[0] == 'N')
        for (int i = 0; i < g_cardb.count; i++)
            if (!strcasecmp(g_cardb.items[i].title_id, s->title_id)) { where = "Memory card slot B"; break; }
    ui_detail_field(y, "LOCAL COPY", where, s->local ? HEX_OK : HEX_DIM);
}

/* ---- VMC (saves inside card images on SD) ---- */

static void fill_vmccard_names(void) {
    for (int i = 0; i < g_vmccard.count; i++) {
        const ServerSave *sv = server_find(g_vmccard.saves[i].title_id);
        if (sv && sv->name[0] && strcasecmp(sv->name, sv->title_id) != 0)
            snprintf(g_vmccard.saves[i].name, sizeof(g_vmccard.saves[i].name), "%s", sv->name);
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
    if (!confirm(HEX_WARN, "Upload save", "Upload %s from %s to the server?",
                 s->title_id, g_vmccard.filename))
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
    if (!confirm(HEX_INFO, "Restore save",
                 "Restore %s from the server into %s?\nOverwrites the save in the image.",
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
    if (!confirm(HEX_WARN, "Import card image",
                 "Import ALL saves from %s to the server (split per game)?", v->filename))
        return;
    ui_status("Importing %s...", v->filename);
    redraw();
    char msg[128];
    int rc = saves_upload_vmc(&g_state, v, msg, sizeof(msg));
    if (rc < 0) ui_error("%s", msg);
    else { ui_status("%s", msg); fetch_server(); }
    redraw();
}

#define VMC_BANNER_H 30.0f
static int vmc_rows(void) {
    return ui_list_visible() - 1;   /* the card switcher takes one row */
}

static void row_vmc(int idx, float x, float y, float w, float h, bool sel) {
    const VmcfsSave *s = &g_vmccard.saves[idx];
    save_row(x, y, w, h, sel, s->gamecode, s->company, s->name[0] ? s->name : s->filename,
             s->blocks, server_find(s->title_id) != NULL);
}

static void draw_vmc(void) {
    if (g_vmc.count == 0) {
        ui_list("Card images", 0, 0, 0, row_vmc,
                g_vmc.last_error[0] ? g_vmc.last_error
                                    : "No card images found (sd:/swiss/saves, MemoryCards/GC, ...).");
        ui_detail_empty("VMC", "Virtual memory cards from Swiss, GCMCE / FlipperMCE or Nintendont live on the SD card.");
        return;
    }
    ui_list_ex("Saves in this card image", g_vmccard.count, g_vmc_sel, g_vmc_scroll, vmc_rows(),
               VMC_BANNER_H, row_vmc,
               g_vmccard.last_error[0] ? g_vmccard.last_error : "No saves in this image.");

    /* Card switcher strip across the top of the list */
    float x = UI_LIST_X + 6, y = UI_LIST_Y + UI_LIST_HEAD + 5, w = UI_LIST_W - 12;
    gui_rrect(x, y, w, VMC_BANNER_H - 6, 6, gui_rgb(HEX_PANEL_HI));
    float bx = x + 6 + gui_button(x + 6, y + (VMC_BANNER_H - 6) / 2, "Z") + 6;
    char lab[32];
    snprintf(lab, sizeof(lab), "%d / %d", g_vmc_active + 1, g_vmc.count);
    float lw = gui_text_mid(x + w - 8, y, VMC_BANNER_H - 6, GUI_S_TINY, gui_rgb(HEX_DIM),
                            GUI_RIGHT, 0, lab);
    gui_text_mid(bx, y, VMC_BANNER_H - 6, GUI_S_TINY, gui_rgb(HEX_ACCENT2), GUI_LEFT,
                 w - (bx - x) - lw - 16, g_vmccard.filename);

    if (g_vmccard.count == 0 || g_vmc_sel >= g_vmccard.count) {
        ui_detail_empty("VMC", "This card image holds no saves. Z switches to the next image.");
        return;
    }
    const VmcfsSave *s = &g_vmccard.saves[g_vmc_sel];
    save_detail(s->name[0] ? s->name : s->filename, s->gamecode, s->company, s->title_id,
                s->filename, s->blocks, "CARD IMAGE", g_vmc.items[g_vmc_active].path);
}

/* ---- MemCard Pro GC GameID switch ---- */

static void mcp_gameid(int port, const char *gamecode, const char *company, const char *name) {
    if (!g_state.mmce_mode[port]) {
        ui_error("Slot %c GameID off (enable in Settings)", 'A' + port);
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

/* ---- Frame ---- */

static void draw_view(void) {
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
    const GuiHint *h;
    int n;
    view_hints(g_view, &h, &n);
    if (g_dl_active) { h = k_hints_dl_run; n = NHINTS(k_hints_dl_run); }
    ui_draw_chrome(&g_state, g_view, h, n);
    if (g_dl_active) {
        draw_download_card();
        gui_footer(h, n);
    }
}

static void redraw(void) {
    g_last_draw_ms = now_ms();
    gui_begin();
    draw_view();
    gui_end(true);
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
    show_boot("Reading memory cards...");
    g_view = APP_VIEW_ROMS;
    saves_scan_card(0, &g_carda);
    saves_scan_card(1, &g_cardb);

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
        /* Pace the loop at vsync and only redraw on input. */
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
                    confirm(HEX_ERR, "Delete game", "Delete %s from the SD card?",
                            g_local.items[g_local_sel].filename)) {
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
            int vis = vmc_rows();
            if      (down & PAD_BUTTON_UP)    g_vmc_sel--;
            else if (down & PAD_BUTTON_DOWN)  g_vmc_sel++;
            else if (down & PAD_BUTTON_LEFT)  g_vmc_sel -= vis;
            else if (down & PAD_BUTTON_RIGHT) g_vmc_sel += vis;
            else if (down & PAD_TRIGGER_Z)    cycle_vmc_card();
            else if (down & PAD_BUTTON_X)     scan_vmc_view();
            else if (down & PAD_BUTTON_A)     upload_vmc_save_at(g_vmc_sel);
            else if (down & PAD_BUTTON_Y)     restore_vmc_save_at(g_vmc_sel);
            else if (down & PAD_BUTTON_START) import_whole_vmc();
            clamp_scroll_rows(&g_vmc_sel, &g_vmc_scroll, g_vmccard.count, vis);
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
