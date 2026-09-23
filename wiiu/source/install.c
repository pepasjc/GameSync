/*
 * install.c — hand a downloaded WUP folder to the system installer (MCP).
 *
 * Sequence and the alignment requirement are as used by WUP Installer GX2
 * (Dimok / Maschell, GPL-2.0-or-later) — MCP_InstallGetInfo and
 * MCP_InstallTitleAsync write into IOS-visible buffers, so the structs have
 * to come from the default heap at 0x40 alignment, not the stack.  Based on
 * analysis of that project; no code copied.
 *
 * Installing an unsigned title needs the IOSU install patch that Aroma /
 * Tiramisu apply.  Without it MCP rejects the title and we surface its error
 * rather than pretending the install worked.
 */

#include "install.h"

#include <ctype.h>
#include <dirent.h>
#include <stdarg.h>
#include <stdio.h>
#include <string.h>
#include <strings.h>
#include <sys/stat.h>

#include <coreinit/filesystem_fsa.h>
#include <coreinit/mcp.h>
#include <coreinit/memdefaultheap.h>
#include <coreinit/thread.h>
#include <coreinit/time.h>

#include <mocha/mocha.h>

/* --- install log ---------------------------------------------------------
 *
 * Every MCP call's return value goes to sd:/3dssync/install.log.  MCP reports
 * failures asynchronously and the on-screen status line only has room for the
 * final verdict, so without a file there is nothing to inspect after an
 * install quietly does nothing — which is exactly the state this code spent
 * several rounds guessing at.  Always on SD: app data never follows
 * rom_storage, and a log on a drive that may not be mounted is useless.
 *
 * Reopened per line so a freeze or a power-off still leaves everything
 * written up to that point on disk.
 */
#define INSTALL_LOG_PATH SD_ROOT_DEFAULT APP_DATA_SUBDIR "/install.log"

static void ilog(const char *fmt, ...) {
    FILE *fp = fopen(INSTALL_LOG_PATH, "a");
    if (!fp) return;
    OSCalendarTime t;
    OSTicksToCalendarTime(OSGetTime(), &t);
    fprintf(fp, "[%02d:%02d:%02d] ", t.tm_hour, t.tm_min, t.tm_sec);
    va_list ap;
    va_start(ap, fmt);
    vfprintf(fp, fmt, ap);
    va_end(ap);
    fputc('\n', fp);
    fclose(fp);
}

/* --- title.tmd parsing ---------------------------------------------------
 *
 * Layout per wiiubrew (https://wiiubrew.org/wiki/Title_metadata).  Only the
 * RSA-2048/SHA-256 signature type (0x00010004) is used by retail Wii U
 * titles, which puts the header at 0x140:
 *
 *   0x18C  title id            u64 BE
 *   0x1DE  content count       u16 BE
 *   0xB04  content chunks      48 bytes each: id u32, index u16, type u16,
 *                              size u64, SHA-256 hash
 */
#define TMD_SIG_RSA2048_SHA256 0x00010004u
#define TMD_HEADER_OFF         0x140
#define TMD_TITLE_ID_OFF       (TMD_HEADER_OFF + 0x4C)
#define TMD_CONTENT_COUNT_OFF  (TMD_HEADER_OFF + 0x9E)
#define TMD_CHUNKS_OFF         (TMD_HEADER_OFF + 0xC4 + 64 * 36)
#define TMD_CHUNK_SIZE         48

static uint16_t rd_be16(const uint8_t *p) {
    return (uint16_t)((p[0] << 8) | p[1]);
}
static uint32_t rd_be32(const uint8_t *p) {
    return ((uint32_t)p[0] << 24) | ((uint32_t)p[1] << 16) |
           ((uint32_t)p[2] << 8) | (uint32_t)p[3];
}
static uint64_t rd_be64(const uint8_t *p) {
    return ((uint64_t)rd_be32(p) << 32) | rd_be32(p + 4);
}

/* Read the title id and the content-id list out of <dir>/title.tmd.
 * Returns the content count, or -1 when the TMD is missing/unparseable. */
static int read_tmd(const char *dir, uint64_t *title_id_out,
                    uint32_t *ids_out, int ids_max) {
    char path[SAVE_DIR_LEN];
    snprintf(path, sizeof(path), "%s/title.tmd", dir);

    FILE *fp = fopen(path, "rb");
    if (!fp) {
        snprintf(path, sizeof(path), "%s/TITLE.TMD", dir);
        fp = fopen(path, "rb");
    }
    if (!fp) return -1;

    static uint8_t tmd[64 * 1024];
    size_t n = fread(tmd, 1, sizeof(tmd), fp);
    fclose(fp);
    if (n < TMD_CHUNKS_OFF) return -1;
    if (rd_be32(tmd) != TMD_SIG_RSA2048_SHA256) return -1;

    if (title_id_out) *title_id_out = rd_be64(tmd + TMD_TITLE_ID_OFF);

    int count = (int)rd_be16(tmd + TMD_CONTENT_COUNT_OFF);
    if (count < 0) return -1;
    if ((size_t)(TMD_CHUNKS_OFF + count * TMD_CHUNK_SIZE) > n) return -1;

    int got = 0;
    for (int i = 0; i < count && got < ids_max; i++) {
        ids_out[got++] = rd_be32(tmd + TMD_CHUNKS_OFF + i * TMD_CHUNK_SIZE);
    }
    return count;
}

/* True when <dir>/<name> exists (exact case, then upper-case). */
static bool file_present(const char *dir, const char *name) {
    char path[SAVE_DIR_LEN];
    struct stat st;
    snprintf(path, sizeof(path), "%s/%s", dir, name);
    if (stat(path, &st) == 0 && S_ISREG(st.st_mode) && st.st_size > 0) return true;

    char upper[64];
    size_t i;
    for (i = 0; name[i] && i < sizeof(upper) - 1; i++)
        upper[i] = (char)toupper((unsigned char)name[i]);
    upper[i] = '\0';
    snprintf(path, sizeof(path), "%s/%s", dir, upper);
    return stat(path, &st) == 0 && S_ISREG(st.st_mode) && st.st_size > 0;
}

/* --- title.cert generation -----------------------------------------------
 *
 * NUS-style dumps often ship without title.cert, and MCP refuses the folder
 * without one.  Under Aroma/Tiramisu the IOSU install patch skips signature
 * verification, so all the file has to get right is the structure: the
 * standard chain CA00000003 / CP0000000b / XS0000000c with correct issuer,
 * type, version and sig-type fields; the signature bodies are irrelevant.
 * Layout per NUSspli's ticket.h/ticket.c (GPL-3.0-or-later); no code copied.
 *
 * All fields big-endian == PowerPC native. */
#define CERT_SIZE 0xA00

static void cert_u32(uint8_t *p, size_t off, uint32_t v) {
    p[off]     = (uint8_t)(v >> 24);
    p[off + 1] = (uint8_t)(v >> 16);
    p[off + 2] = (uint8_t)(v >> 8);
    p[off + 3] = (uint8_t)v;
}

static bool write_default_cert(const char *dir) {
    static uint8_t c[CERT_SIZE];
    memset(c, 0, sizeof(c));

    /* cert1: Root -> CA00000003 (RSA-4096 signed, 0x400 bytes) */
    cert_u32(c, 0x000, 0x00010003);
    memcpy(c + 0x240, "Root", 4);
    cert_u32(c, 0x280, 1);
    memcpy(c + 0x284, "CA00000003", 10);
    cert_u32(c, 0x3C8, 0x00010001);

    /* cert2: Root-CA00000003 -> CP0000000b (RSA-2048, 0x300 bytes) */
    cert_u32(c, 0x400, 0x00010004);
    memcpy(c + 0x400 + 0x140, "Root-CA00000003", 15);
    cert_u32(c, 0x400 + 0x180, 1);
    memcpy(c + 0x400 + 0x184, "CP0000000b", 10);
    cert_u32(c, 0x400 + 0x2C8, 0x00010001);

    /* cert3: Root-CA00000003 -> XS0000000c */
    cert_u32(c, 0x700, 0x00010004);
    memcpy(c + 0x700 + 0x140, "Root-CA00000003", 15);
    cert_u32(c, 0x700 + 0x180, 1);
    memcpy(c + 0x700 + 0x184, "XS0000000c", 10);
    cert_u32(c, 0x700 + 0x2C8, 0x00010001);

    char path[SAVE_DIR_LEN];
    snprintf(path, sizeof(path), "%s/title.cert", dir);
    FILE *fp = fopen(path, "wb");
    if (!fp) return false;
    bool ok = fwrite(c, 1, sizeof(c), fp) == sizeof(c);
    fclose(fp);
    if (!ok) remove(path);
    return ok;
}

/* True when <dir>/<id>.app exists and is non-empty (either case). */
static bool content_present(const char *dir, uint32_t content_id) {
    char path[SAVE_DIR_LEN];
    struct stat st;

    snprintf(path, sizeof(path), "%s/%08x.app", dir, (unsigned)content_id);
    if (stat(path, &st) == 0 && S_ISREG(st.st_mode) && st.st_size > 0) return true;

    snprintf(path, sizeof(path), "%s/%08X.app", dir, (unsigned)content_id);
    return stat(path, &st) == 0 && S_ISREG(st.st_mode) && st.st_size > 0;
}

int install_check_folder(const char *dir, uint64_t *title_id_out,
                         char *msg, size_t msg_size) {
    if (title_id_out) *title_id_out = 0;

    uint32_t ids[INSTALL_MAX_CONTENTS];
    uint64_t title_id = 0;
    int count = read_tmd(dir, &title_id, ids, INSTALL_MAX_CONTENTS);
    if (count < 0) {
        snprintf(msg, msg_size, "No readable title.tmd in %s", dir);
        return -1;
    }
    if (title_id_out) *title_id_out = title_id;

    /* A .part file means the download manager never finished this folder.
     * MCP would install a truncated title that then fails to launch — which
     * looks exactly like "the install did nothing". */
    DIR *d = opendir(dir);
    if (d) {
        struct dirent *de;
        while ((de = readdir(d)) != NULL) {
            size_t len = strlen(de->d_name);
            if (len > 5 && !strcasecmp(de->d_name + len - 5, ".part")) {
                snprintf(msg, msg_size,
                         "Download unfinished: %s still present", de->d_name);
                closedir(d);
                return -1;
            }
        }
        closedir(d);
    }

    int checked = count < INSTALL_MAX_CONTENTS ? count : INSTALL_MAX_CONTENTS;
    for (int i = 0; i < checked; i++) {
        if (!content_present(dir, ids[i])) {
            snprintf(msg, msg_size,
                     "Missing content %08x.app (%d of %d) — re-download",
                     (unsigned)ids[i], i + 1, count);
            return -1;
        }
    }

    /* MCP refuses a folder without ticket + cert.  The ticket carries the
     * title key and cannot be fabricated here; the cert chain is public
     * structure and can. */
    if (!file_present(dir, "title.tik")) {
        snprintf(msg, msg_size,
                 "No title.tik in this dump — MCP cannot install it "
                 "(re-dump with the ticket)");
        return -1;
    }
    if (!file_present(dir, "title.cert")) {
        if (write_default_cert(dir)) {
            ilog("generated default title.cert in %s", dir);
        } else {
            snprintf(msg, msg_size, "No title.cert and generating one failed");
            return -1;
        }
    }

    snprintf(msg, msg_size, "%d content(s) present", count);
    return 0;
}

bool install_fsa_path(const char *devoptab_path, char *out, size_t out_size) {
    if (!devoptab_path || !out) return false;

    /* "fs:/vol/external01/..." -> "/vol/app_sd/...".
     *
     * NOT a cosmetic rewrite.  /vol/external01 is the *application's* view of
     * the SD card; MCP runs in IOSU and cannot read it, so an install pointed
     * there does nothing at all — no error, no title.  NUSspli and WUP
     * Installer both bind-mount external01 to /vol/app_sd first and hand MCP
     * that path instead; install_mount_sd() below does the same. */
    if (!strncmp(devoptab_path, "fs:/vol/external01/", 19)) {
        snprintf(out, out_size, "%s%s", MCP_SD_MOUNT "/", devoptab_path + 19);
        return true;
    }
    if (!strncmp(devoptab_path, "fs:/", 4)) {
        snprintf(out, out_size, "%s", devoptab_path + 3);
        return true;
    }
    /* "usb:/..." — our own libmocha mount, made at /vol/usb. */
    if (!strncmp(devoptab_path, USB_ROOT_DEFAULT "/", sizeof(USB_ROOT_DEFAULT))) {
        snprintf(out, out_size, "/vol/" USB_FAT_NAME "/%s",
                 devoptab_path + sizeof(USB_ROOT_DEFAULT));
        return true;
    }
    /* Already an FSA path. */
    if (devoptab_path[0] == '/') {
        snprintf(out, out_size, "%s", devoptab_path);
        return true;
    }
    return false;
}

/* --- SD bind mount for MCP ------------------------------------------------
 *
 * FSA_MOUNT_FLAG_BIND_MOUNT re-exposes /vol/external01 at /vol/app_sd, which
 * is a path IOSU's installer can actually open.  Requires an FSA client that
 * libmocha has unlocked — without that the mount is refused. */
static FSAClientHandle g_fsa;
static bool g_sd_bound;

bool install_mount_sd(char *err, size_t err_size) {
    if (g_sd_bound) return true;

    if (FSAInit() != FS_ERROR_OK) {
        snprintf(err, err_size, "FSAInit failed");
        return false;
    }
    if (!g_fsa) g_fsa = FSAAddClient(NULL);
    if (!g_fsa) {
        snprintf(err, err_size, "FSAAddClient failed");
        return false;
    }
    MochaUtilsStatus st = Mocha_UnlockFSClientEx(g_fsa);
    if (st != MOCHA_RESULT_SUCCESS) {
        snprintf(err, err_size, "Mocha_UnlockFSClientEx: %s",
                 Mocha_GetStatusStr(st));
        return false;
    }

    FSError merr = FSAMount(g_fsa, "/vol/external01", MCP_SD_MOUNT,
                            FSA_MOUNT_FLAG_BIND_MOUNT, NULL, 0);
    /* Already bound from a previous run is success, not failure. */
    if (merr != FS_ERROR_OK && merr != FS_ERROR_ALREADY_EXISTS) {
        snprintf(err, err_size, "FSAMount %s: %d", MCP_SD_MOUNT, (int)merr);
        return false;
    }
    g_sd_bound = true;
    return true;
}

void install_unmount_sd(void) {
    if (g_sd_bound) {
        FSAUnmount(g_fsa, MCP_SD_MOUNT, FSA_UNMOUNT_FLAG_BIND_MOUNT);
        g_sd_bound = false;
    }
    if (g_fsa) { FSADelClient(g_fsa); g_fsa = 0; }
    FSAShutdown();
}

/* usb01 is the normal case and needs no SetTargetUsb call; only a second
 * drive (usb02) does.  Probing the FSA paths is how NUSspli decides. */
bool install_usb_is_second(void) {
    FSAStat st;
    if (!g_fsa) return false;
    if (FSAGetStat(g_fsa, "/vol/storage_usb01", &st) == FS_ERROR_OK) return false;
    return FSAGetStat(g_fsa, "/vol/storage_usb02", &st) == FS_ERROR_OK;
}

/* --- MCP async completion -------------------------------------------------
 *
 * The last argument to MCP_InstallTitleAsync is NOT an output struct: MCP
 * reads a completion callback out of its first two words and calls it when
 * the job finishes.  That callback is the only place the real result appears
 * — polling MCP_InstallGetProgress tells you a job is running, never whether
 * it succeeded.  Passing a zeroed struct (as this code used to) leaves MCP
 * with no way to report anything.
 *
 * Layout and technique per NUSspli's glueMcpData()/mcpCallback(); no code
 * copied. */
typedef struct {
    volatile bool processing;
    volatile MCPError err;
} McpAsync;

static void mcp_done_cb(MCPError err, void *raw) {
    McpAsync *a = (McpAsync *)raw;
    if (a->err == 0) a->err = err;
    a->processing = false;
}

static void glue_mcp_async(MCPInstallTitleInfo *info, McpAsync *a) {
    a->processing = true;
    a->err = 0;
    uint32_t *ptr = (uint32_t *)info;
    *ptr   = (uint32_t)mcp_done_cb;
    *++ptr = (uint32_t)a;
}

int install_wup_folder(const char *dir, const char *target,
                       InstallProgress *progress,
                       void (*on_poll)(const InstallProgress *)) {
    InstallProgress local;
    if (!progress) progress = &local;
    memset(progress, 0, sizeof(*progress));

    if (!dir || !dir[0]) {
        snprintf(progress->message, sizeof(progress->message), "No folder given");
        progress->done = true;
        return -1;
    }

    ilog("---- install request: dir=%s target=%s", dir, target ? target : "(null)");

    /* MCP cannot read /vol/external01, so bind it to /vol/app_sd first.  This
     * is what made every previous attempt a silent no-op. */
    char mount_err[96];
    if (!install_mount_sd(mount_err, sizeof(mount_err))) {
        ilog("FAIL bind mount: %s", mount_err);
        snprintf(progress->message, sizeof(progress->message),
                 "Cannot expose SD to the installer: %s", mount_err);
        progress->done = true;
        return -1;
    }

    char fsa[SAVE_DIR_LEN];
    if (!install_fsa_path(dir, fsa, sizeof(fsa))) {
        ilog("FAIL install_fsa_path: unsupported root");
        snprintf(progress->message, sizeof(progress->message),
                 "Unsupported storage root: %s", dir);
        progress->done = true;
        return -1;
    }

    /* Refuse an incomplete folder up front.  MCP will happily install a WUP
     * set that is missing contents, and the result is a title that either
     * never appears or refuses to launch. */
    uint64_t title_id = 0;
    char check_msg[160];
    if (install_check_folder(dir, &title_id, check_msg, sizeof(check_msg)) != 0) {
        ilog("FAIL precheck: %s", check_msg);
        snprintf(progress->message, sizeof(progress->message), "%s", check_msg);
        progress->done = true;
        return -1;
    }

    ilog("fsa=%s title_id=%016llx precheck=%s",
         fsa, (unsigned long long)title_id, check_msg);

    int handle = MCP_Open();
    ilog("MCP_Open -> %d", handle);
    if (handle < 0) {
        snprintf(progress->message, sizeof(progress->message),
                 "MCP_Open failed (%d)", handle);
        progress->done = true;
        return -1;
    }

    /* IOS writes these, so they must be heap allocated at 0x40 alignment.
     * ``info`` serves double duty exactly as MCP expects: MCP_InstallGetInfo
     * fills it, then glue_mcp_async() overwrites its first two words with the
     * completion callback before MCP_InstallTitleAsync consumes it. */
    MCPInstallTitleInfo *info = MEMAllocFromDefaultHeapEx(sizeof(MCPInstallTitleInfo), 0x40);
    MCPInstallProgress *prog = MEMAllocFromDefaultHeapEx(sizeof(MCPInstallProgress), 0x40);
    int rc = -1;
    bool to_usb = target && !strcasecmp(target, "usb");

    if (!info || !prog) {
        snprintf(progress->message, sizeof(progress->message),
                 "Out of memory for MCP buffers");
        goto out;
    }
    memset(info, 0, sizeof(*info));
    memset(prog, 0, sizeof(*prog));

    MCPError err = MCP_InstallGetInfo(handle, fsa, (MCPInstallInfo *)info);
    ilog("MCP_InstallGetInfo(%s) -> %d (%#010x)", fsa, (int)err, (unsigned)err);
    if (err != 0) {
        /* MCP's own codes, as decoded by NUSspli. */
        const char *why =
            ((unsigned)err == 0xfffbf3e2) ? "no title.tmd at that path" :
            ((unsigned)err == 0xfffbfc17) ? "internal MCP error" : "";
        snprintf(progress->message, sizeof(progress->message),
                 "Not installable (MCP %#010x)%s%s", (unsigned)err,
                 why[0] ? " - " : "", why);
        rc = (int)err;
        goto out;
    }

    MCPInstallTarget tgt = to_usb ? MCP_INSTALL_TARGET_USB : MCP_INSTALL_TARGET_MLC;
    err = MCP_InstallSetTargetDevice(handle, tgt);
    ilog("MCP_InstallSetTargetDevice(%s) -> %d (%#010x)",
         to_usb ? "USB" : "MLC", (int)err, (unsigned)err);
    if (err != 0) {
        snprintf(progress->message, sizeof(progress->message),
                 "Target %s rejected (MCP %#010x)%s",
                 to_usb ? "USB" : "NAND", (unsigned)err,
                 to_usb ? " - no Wii U formatted USB drive attached "
                          "(switch 'Install to' to NAND in Config)" : "");
        rc = (int)err;
        goto out;
    }
    /* Only a *second* USB device needs SetTargetUsb; calling it for the usual
     * usb01 is what NUSspli deliberately does not do. */
    if (to_usb && install_usb_is_second()) {
        err = MCP_InstallSetTargetUsb(handle, (int)tgt + 1);
        ilog("MCP_InstallSetTargetUsb(%d) -> %d", (int)tgt + 1, (int)err);
        if (err != 0) {
            snprintf(progress->message, sizeof(progress->message),
                     "MCP_InstallSetTargetUsb failed (%d)", (int)err);
            rc = (int)err;
            goto out;
        }
    }

    /* Hand MCP somewhere to report the outcome, then start the job. */
    McpAsync async;
    glue_mcp_async(info, &async);

    progress->running = true;
    err = MCP_InstallTitleAsync(handle, fsa, info);
    ilog("MCP_InstallTitleAsync -> %d (%#010x)", (int)err, (unsigned)err);
    if (err != 0) {
        progress->running = false;
        snprintf(progress->message, sizeof(progress->message),
                 "Install refused (MCP %#010x) - IOSU install patch active?",
                 (unsigned)err);
        rc = (int)err;
        goto out;
    }

    /* Wait on the callback, not on inProgress.  Progress polls are only for
     * drawing the bar; ``async.processing`` is the authoritative done signal. */
    while (async.processing) {
        memset(prog, 0, sizeof(*prog));
        if (MCP_InstallGetProgress(handle, prog) == 0 && prog->inProgress == 1) {
            progress->size_total     = prog->sizeTotal;
            progress->size_done      = prog->sizeProgress;
            progress->contents_total = prog->contentsTotal;
            progress->contents_done  = prog->contentsProgress;
            if (on_poll) on_poll(progress);
        }
        OSSleepTicks(OSMillisecondsToTicks(INSTALL_POLL_MS));
    }

    progress->running = false;
    ilog("async result -> %d (%#010x), %llu/%llu bytes",
         (int)async.err, (unsigned)async.err,
         (unsigned long long)progress->size_done,
         (unsigned long long)progress->size_total);

    if (async.err != 0) {
        const char *why =
            ((unsigned)async.err == 0xFFFCFFE9)
                ? " - install the base game to this device first"
            : ((unsigned)async.err == 0xFFFBF446 ||
               (unsigned)async.err == 0xFFFBF441)
                ? " - not enough space" : "";
        snprintf(progress->message, sizeof(progress->message),
                 "Install failed (MCP %#010x)%s", (unsigned)async.err, why);
        rc = (int)async.err;
        goto out;
    }

    /* Belt and braces: confirm the system now knows the title. */
    if (title_id != 0) {
        MCPTitleListType listing;
        memset(&listing, 0, sizeof(listing));
        MCPError present = MCP_GetTitleInfo(handle, title_id, &listing);
        ilog("MCP_GetTitleInfo(%016llx) -> %d",
             (unsigned long long)title_id, (int)present);
        if (present != 0) {
            snprintf(progress->message, sizeof(progress->message),
                     "Install did not register %016llx (MCP %d)",
                     (unsigned long long)title_id, (int)present);
            rc = -1;
            goto out;
        }
    }

    rc = 0;
    ilog("OK installed %016llx to %s",
         (unsigned long long)title_id, to_usb ? "USB" : "NAND");
    snprintf(progress->message, sizeof(progress->message),
             "Installed %016llx to %s",
             (unsigned long long)title_id, to_usb ? "USB" : "NAND");

out:
    if (info) MEMFreeToDefaultHeap(info);
    if (prog) MEMFreeToDefaultHeap(prog);
    MCP_Close(handle);

    progress->done = true;
    progress->result = rc;
    if (!progress->message[0])
        snprintf(progress->message, sizeof(progress->message),
                 rc == 0 ? "Installed" : "Install failed (%d)", rc);
    if (on_poll) on_poll(progress);
    return rc;
}
