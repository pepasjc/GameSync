/*
 * natives.c — vWii (SLC) and Wii U (MLC) save enumeration via libmocha.
 *
 * Mocha_MountFS() registers a newlib devoptab for a raw device, so once the
 * mounts are up the NAND trees are ordinary POSIX paths and savetree.c does
 * the walking.
 *
 * Writes are deliberately confined to the save directories themselves, and
 * every restore is preceded by an SD backup — a bad write to SLC is a
 * brick-adjacent event.
 */

#include "natives.h"

#include <ctype.h>
#include <dirent.h>
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>

#include <mocha/mocha.h>
#include <whb/log.h>

#define SLC_NAME  "storage_slccmpt01"
#define MLC_NAME  "storage_mlc01"
#define USB_NAME  "storage_usb01"

#define VWII_TITLES_DIR SLC_NAME ":/title/00010000"
#define WIIU_SAVES_DIR  MLC_NAME ":/usr/save/00050000"
#define WIIU_USB_SAVES  USB_NAME ":/usr/save/00050000"

/* A title installed to USB keeps its save data on that USB drive, so the
 * Wii U scan has to cover both roots.  MLC is always present; USB only when
 * a drive is attached and mountable. */
static const char *const WIIU_SAVE_ROOTS[] = {
    WIIU_SAVES_DIR,
    WIIU_USB_SAVES,
    NULL,
};

/* nocopy/ holds console-bound data that must never be moved between
 * consoles; nobackup/ is game scratch that the Wii itself excludes. */
static const char *const VWII_EXCLUDE[] = { "nocopy", NULL };

/*
 * Mount a device and PROVE it is usable.
 *
 * A success status is not enough.  Mocha_MountFS has two modes:
 *   dev_path set   — FSAMount the device at mount_path.
 *   dev_path NULL  — attach to a mount CafeOS already has there.
 *
 * When FSAMount reports FS_ERROR_ALREADY_EXISTS (a previous run of this app
 * left the global mount behind, or the OS owns it), libmocha calls
 * fsa_free() and returns MOCHA_RESULT_ALREADY_EXISTS *without registering a
 * devoptab* — so "storage_x:" does not exist and every open fails.  Treating
 * that status as success is what broke the vWii view on a second launch.
 *
 * So: try each form, and confirm with an opendir of a directory that must
 * exist.  Returns true only when the devoptab really works.
 */
static bool mount_verified(const char *name, const char *dev,
                           const char *mount_path, const char *probe_dir,
                           char *err, size_t err_size) {
    const char *devs[2] = { dev, NULL };
    int attempts = dev ? 2 : 1;      /* device mount, then attach-to-existing */
    MochaUtilsStatus last = MOCHA_RESULT_UNKNOWN_ERROR;

    for (int i = 0; i < attempts; i++) {
        last = Mocha_MountFS(name, devs[i], mount_path);
        if (last == MOCHA_RESULT_SUCCESS) {
            DIR *d = opendir(probe_dir);
            if (d) { closedir(d); return true; }
            /* Registered but unusable — drop it before trying the other form. */
            Mocha_UnmountFS(name);
            last = MOCHA_RESULT_NOT_FOUND;
        }
    }
    if (err && err_size)
        snprintf(err, err_size, "%s: %s", name, Mocha_GetStatusStr(last));
    return false;
}

int natives_init(SyncState *state) {
    if (!state) return -1;
    state->mocha_ok = false;
    state->slc_mounted = false;
    state->mlc_mounted = false;
    state->usb_mounted = false;
    state->mocha_error[0] = '\0';

    MochaUtilsStatus st = Mocha_InitLibrary();
    if (st != MOCHA_RESULT_SUCCESS) {
        snprintf(state->mocha_error, sizeof(state->mocha_error),
                 "mocha init: %s", Mocha_GetStatusStr(st));
        return -1;
    }
    state->mocha_ok = true;

    char err[96] = "";

    /* SLC (vWii compat) is not mounted in Wii U mode, so the device path is
     * the normal case; the NULL retry covers a stale mount from a previous
     * launch. */
    state->slc_mounted = mount_verified(SLC_NAME, "/dev/slccmpt01",
                                        "/vol/" SLC_NAME,
                                        SLC_NAME ":/title",
                                        err, sizeof(err));
    if (!state->slc_mounted && !state->mocha_error[0])
        snprintf(state->mocha_error, sizeof(state->mocha_error), "slc %s", err);

    /* MLC is always mounted by CafeOS, so attach-to-existing comes first. */
    state->mlc_mounted = mount_verified(MLC_NAME, NULL,
                                        "/vol/" MLC_NAME,
                                        MLC_NAME ":/usr",
                                        err, sizeof(err));
    if (!state->mlc_mounted)
        state->mlc_mounted = mount_verified(MLC_NAME, "/dev/mlc01",
                                            "/vol/" MLC_NAME,
                                            MLC_NAME ":/usr",
                                            err, sizeof(err));
    if (!state->mlc_mounted && !state->mocha_error[0])
        snprintf(state->mocha_error, sizeof(state->mocha_error), "mlc %s", err);

    /* Optional: only present when a USB drive is attached, so a failure here
     * is normal and must not be surfaced as an error. */
    natives_mount_usb_wfs(state);

    natives_mount_usb_fat(state);

    return 0;
}

/* (Re)attach the console's WFS USB (storage_usb01) so save reads can reach it.
 * Not required for MCP installs — those go through IOSU — but the install
 * view re-probes here so the UI label is accurate.  Idempotent. */
bool natives_mount_usb_wfs(SyncState *state) {
    if (!state || !state->mocha_ok) return false;
    state->usb_mounted = mount_verified(USB_NAME, NULL, "/vol/" USB_NAME,
                                        USB_NAME ":/usr", NULL, 0);
    return state->usb_mounted;
}

bool natives_mount_usb_fat(SyncState *state) {
    if (!state) return false;
    state->usb_fat_ready = false;
    state->usb_fat_error[0] = '\0';
    if (!state->mocha_ok) {
        snprintf(state->usb_fat_error, sizeof(state->usb_fat_error),
                 "mocha not initialised");
        return false;
    }

    /* A FAT32 stick shows up as /dev/usb01; a second partition or a second
     * drive as /dev/usb02.  Unlike the WFS mount above we pass a device path
     * (CafeOS does not mount FAT32 itself, so there is nothing to attach to)
     * and probe the root, which every FAT volume has. */
    static const char *const devices[] = { "/dev/usb01", "/dev/usb02", NULL };
    char err[96] = "";
    for (int i = 0; devices[i]; i++) {
        if (mount_verified(USB_FAT_NAME, devices[i], "/vol/" USB_FAT_NAME,
                           USB_ROOT_DEFAULT "/", err, sizeof(err))) {
            state->usb_fat_ready = true;
            return true;
        }
    }

    snprintf(state->usb_fat_error, sizeof(state->usb_fat_error),
             "%s", err[0] ? err : "no FAT32 USB drive found");
    return false;
}

void natives_shutdown(SyncState *state) {
    if (!state || !state->mocha_ok) return;
    if (state->slc_mounted) Mocha_UnmountFS(SLC_NAME);
    if (state->mlc_mounted) Mocha_UnmountFS(MLC_NAME);
    if (state->usb_mounted) Mocha_UnmountFS(USB_NAME);
    if (state->usb_fat_ready) Mocha_UnmountFS(USB_FAT_NAME);
    Mocha_DeInitLibrary();
    state->mocha_ok = false;
    state->slc_mounted = false;
    state->mlc_mounted = false;
    state->usb_mounted = false;
    state->usb_fat_ready = false;
}

/* ---- helpers ---- */

static bool is_hex8(const char *s) {
    if (strlen(s) != 8) return false;
    for (int i = 0; i < 8; i++)
        if (!isxdigit((unsigned char)s[i])) return false;
    return true;
}

static bool dir_exists(const char *path) {
    struct stat st;
    return stat(path, &st) == 0 && S_ISDIR(st.st_mode);
}

/* ---- vWii ---- */

/* tidlo "524d4345" -> "RMCE" (printable ASCII only). */
static bool tidlo_to_ascii4(const char *tidlo, char *out5) {
    for (int i = 0; i < 4; i++) {
        char pair[3] = { tidlo[i * 2], tidlo[i * 2 + 1], 0 };
        long v = strtol(pair, NULL, 16);
        if (v < 0x20 || v > 0x7E) return false;
        out5[i] = (char)v;
    }
    out5[4] = '\0';
    return true;
}

static void ascii4_to_tidlo(const char *code4, char *out9) {
    snprintf(out9, 9, "%02x%02x%02x%02x",
             (unsigned char)code4[0], (unsigned char)code4[1],
             (unsigned char)code4[2], (unsigned char)code4[3]);
}

bool vwiisaves_root_for(const char *title_id, char *out, size_t out_size) {
    if (!title_id || strncmp(title_id, "WII_", 4) != 0) return false;
    const char *code = title_id + 4;
    if (strlen(code) != 4) return false;
    char tidlo[9];
    ascii4_to_tidlo(code, tidlo);
    snprintf(out, out_size, VWII_TITLES_DIR "/%s/data", tidlo);
    return true;
}

void vwiisaves_scan(const SyncState *state, SaveTitleList *out) {
    if (!out) return;
    savetree_free_list(out);
    out->title_count = 0;
    out->last_error[0] = '\0';

    if (!state->slc_mounted) {
        snprintf(out->last_error, sizeof(out->last_error),
                 "vWii NAND not mounted (%s)",
                 state->mocha_error[0] ? state->mocha_error : "mocha unavailable");
        return;
    }

    DIR *d = opendir(VWII_TITLES_DIR);
    if (!d) {
        snprintf(out->last_error, sizeof(out->last_error),
                 "Cannot open %s", VWII_TITLES_DIR);
        return;
    }

    struct dirent *de;
    while ((de = readdir(d)) != NULL && out->title_count < SAVE_MAX_TITLES) {
        if (de->d_name[0] == '.') continue;
        if (!is_hex8(de->d_name)) continue;

        char code[5];
        if (!tidlo_to_ascii4(de->d_name, code)) continue;

        SaveTitle *t = &out->titles[out->title_count];
        memset(t, 0, sizeof(*t));
        snprintf(t->title_id, sizeof(t->title_id), "WII_%s", code);
        snprintf(t->root, sizeof(t->root), VWII_TITLES_DIR "/%s/data", de->d_name);
        if (!dir_exists(t->root)) continue;

        if (savetree_scan(t, VWII_EXCLUDE) != 0) {
            savetree_free_title(t);
            snprintf(t->error, sizeof(t->error), "read failed (errno %d)", errno);
        } else if (t->file_count == 0) {
            /* data/ holding only nocopy/ is normal and not worth listing. */
            continue;
        }
        out->title_count++;
    }
    closedir(d);

    if (out->title_count == 0 && out->last_error[0] == '\0')
        snprintf(out->last_error, sizeof(out->last_error), "No vWii saves found");
}

/* ---- Wii U ---- */

bool wiiusaves_root_for(const char *title_id, char *out, size_t out_size) {
    if (!title_id || strlen(title_id) != 16) return false;
    if (strncasecmp(title_id, "00050000", 8) != 0) return false;

    /* Prefer whichever root actually holds this title's save (USB-installed
     * games keep their saves on the USB drive). */
    for (int i = 0; WIIU_SAVE_ROOTS[i]; i++) {
        snprintf(out, out_size, "%s/%.8s/user", WIIU_SAVE_ROOTS[i], title_id + 8);
        if (dir_exists(out)) return true;
    }

    /* No save exists yet (fresh restore after a drive swap, or the game has
     * never been launched).  The save MUST land on the same device the title
     * is installed on or the game will never see it — so probe where the
     * content lives and mirror that.  MLC only as the final fallback. */
    static const struct { const char *title_root; const char *save_root; } DEVS[] = {
        { USB_NAME ":/usr/title/00050000", WIIU_USB_SAVES },
        { MLC_NAME ":/usr/title/00050000", WIIU_SAVES_DIR },
        { USB_NAME ":/usr/title/0005000E", WIIU_USB_SAVES },  /* disc game: only
                                                                 the update is
                                                                 installed */
        { MLC_NAME ":/usr/title/0005000E", WIIU_SAVES_DIR },
    };
    for (size_t i = 0; i < sizeof(DEVS) / sizeof(DEVS[0]); i++) {
        char probe[SAVE_DIR_LEN];
        snprintf(probe, sizeof(probe), "%s/%.8s", DEVS[i].title_root, title_id + 8);
        if (dir_exists(probe)) {
            snprintf(out, out_size, "%s/%.8s/user", DEVS[i].save_root, title_id + 8);
            return true;
        }
    }

    snprintf(out, out_size, WIIU_SAVES_DIR "/%.8s/user", title_id + 8);
    return true;
}

/* Where a title's meta.xml can live.
 *
 * Content need not sit on the same device as its save data (USB installs),
 * and — the case that kept ids like 000500001010EC00 showing raw — the
 * pre-installed system applications in the 00050000-1010xxxx band keep their
 * content under /sys/title, not /usr/title.  Search every combination. */
static const char *const WIIU_TITLE_ROOTS[] = {
    /* 00050000 — the application itself.  Present for digital titles only:
     * a DISC game has no NAND content, which is why Mario Kart 8 and friends
     * came up nameless while eShop titles resolved fine. */
    MLC_NAME ":/usr/title/00050000",
    USB_NAME ":/usr/title/00050000",
    /* 0005000E — the update title, same low word.  A patched disc game keeps
     * this on NAND and its meta.xml carries the game's name, which is how a
     * disc title gets named at all. */
    MLC_NAME ":/usr/title/0005000E",
    USB_NAME ":/usr/title/0005000E",
    /* 0005000C — DLC, same low word; last resort for a disc game with add-on
     * content but no update. */
    MLC_NAME ":/usr/title/0005000C",
    USB_NAME ":/usr/title/0005000C",
    /* Pre-installed system applications live outside /usr. */
    MLC_NAME ":/sys/title/00050000",
    NULL,
};

/* meta.xml carries shortname/longname/publisher in a dozen languages and
 * routinely runs past 8 KB — <longname_en> sits well after the header
 * fields.  Reading only the first 8 KB found the name for some titles and
 * silently missed it for others, which is why part of the list showed raw
 * title ids.  Read enough to cover the whole file. */
#define META_XML_MAX (128 * 1024)

/* "<product_code>WUP-P-ARDE</product_code>" -> "WIIU_ARDE".
 *
 * The 4-char code is what the server's Wii U DAT is keyed by; the title id is
 * not, so this is the only thing that turns a save's raw hex id into a name
 * for the desktop / Steam Deck / Android clients. */
static bool parse_meta_product_code(const char *buf, char *out, size_t out_size) {
    const char *p = strstr(buf, "<product_code");
    if (!p) return false;
    p = strchr(p, '>');
    if (!p) return false;
    p++;
    const char *end = strchr(p, '<');
    if (!end) return false;

    char code[16];
    size_t j = 0;
    for (const char *q = p; q < end && j + 1 < sizeof(code); q++) {
        if (isalnum((unsigned char)*q))
            code[j++] = (char)toupper((unsigned char)*q);
    }
    code[j] = '\0';
    if (j < 4) return false;

    snprintf(out, out_size, "WIIU_%s", code + (j - 4));
    return true;
}

static bool parse_meta_longname(const char *path, char *out, size_t out_size,
                                char *code_out, size_t code_size) {
    FILE *fp = fopen(path, "rb");
    if (!fp) return false;

    char *buf = (char *)malloc(META_XML_MAX);
    if (!buf) { fclose(fp); return false; }

    size_t n = fread(buf, 1, META_XML_MAX - 1, fp);
    fclose(fp);
    if (n == 0) { free(buf); return false; }
    buf[n] = '\0';

    /* The product code is read first: a title whose longname is missing or
     * unparseable still gets a name on the server through its code. */
    if (code_out && code_size)
        parse_meta_product_code(buf, code_out, code_size);

    const char *p = strstr(buf, "<longname_en");
    if (!p) p = strstr(buf, "<longname_");
    if (!p) { free(buf); return false; }
    p = strchr(p, '>');
    if (!p) { free(buf); return false; }
    p++;
    const char *end = strstr(p, "</longname");
    if (!end) { free(buf); return false; }

    /* meta.xml is XML, so the name arrives escaped — without this,
     * "Dracula&apos;s Curse" is what the user reads. */
    static const struct { const char *ent; char ch; } ENTITIES[] = {
        { "&apos;", '\'' }, { "&quot;", '"' }, { "&lt;", '<' },
        { "&gt;",   '>'  }, { "&amp;",  '&' },
    };

    size_t j = 0;
    for (const char *q = p; q < end && j + 1 < out_size; ) {
        char c = *q;
        if (c == '&') {
            bool matched = false;
            for (size_t e = 0; e < sizeof(ENTITIES) / sizeof(ENTITIES[0]); e++) {
                size_t len = strlen(ENTITIES[e].ent);
                if ((size_t)(end - q) >= len &&
                    strncmp(q, ENTITIES[e].ent, len) == 0) {
                    out[j++] = ENTITIES[e].ch;
                    q += len;
                    matched = true;
                    break;
                }
            }
            if (matched) continue;
        }
        if (c == '\n' || c == '\r' || c == '\t') c = ' ';
        q++;
        if (c == ' ' && (j == 0 || out[j - 1] == ' ')) continue;
        out[j++] = c;
    }
    while (j > 0 && out[j - 1] == ' ') j--;
    out[j] = '\0';

    free(buf);
    return out[0] != '\0';
}

static void read_meta_longname(const char *tidlo, char *out, size_t out_size,
                               char *code_out, size_t code_size) {
    out[0] = '\0';
    if (code_out && code_size) code_out[0] = '\0';
    for (int i = 0; WIIU_TITLE_ROOTS[i]; i++) {
        char path[SAVE_DIR_LEN];
        snprintf(path, sizeof(path), "%s/%s/meta/meta.xml",
                 WIIU_TITLE_ROOTS[i], tidlo);
        if (parse_meta_longname(path, out, out_size, code_out, code_size)) return;
        /* A meta.xml that yielded only the product code is still a win — keep
         * it and go on looking for one that also has a longname. */
    }
    /* Nothing found — the title is uninstalled, or its content sits somewhere
     * we do not know about.  Log it so the id can be chased down instead of
     * silently rendering as hex. */
    WHBLogPrintf("wiiu: no meta.xml for 00050000%s", tidlo);
}

void wiiusaves_scan(const SyncState *state, SaveTitleList *out) {
    if (!out) return;
    savetree_free_list(out);
    out->title_count = 0;
    out->last_error[0] = '\0';

    if (!state->mlc_mounted) {
        snprintf(out->last_error, sizeof(out->last_error),
                 "MLC not mounted (%s)",
                 state->mocha_error[0] ? state->mocha_error : "mocha unavailable");
        return;
    }

    int roots_opened = 0;
    for (int r = 0; WIIU_SAVE_ROOTS[r]; r++) {
        const char *root = WIIU_SAVE_ROOTS[r];

        DIR *d = opendir(root);
        if (!d) continue;     /* USB simply may not be attached */
        roots_opened++;

        struct dirent *de;
        while ((de = readdir(d)) != NULL && out->title_count < SAVE_MAX_TITLES) {
            if (de->d_name[0] == '.') continue;
            if (!is_hex8(de->d_name)) continue;

            SaveTitle *t = &out->titles[out->title_count];
            memset(t, 0, sizeof(*t));
            memcpy(t->title_id, "00050000", 8);
            for (int i = 0; i < 8; i++)
                t->title_id[8 + i] = (char)toupper((unsigned char)de->d_name[i]);
            t->title_id[16] = '\0';

            /* A title present on both devices would otherwise be listed
             * twice with the same id. */
            if (savetree_find(out, t->title_id)) continue;

            snprintf(t->root, sizeof(t->root), "%s/%s/user", root, de->d_name);
            if (!dir_exists(t->root)) {
                WHBLogPrintf("wiiu: %s has no user/ dir under %s",
                             t->title_id, root);
                continue;
            }

            /* A title whose tree cannot be read is LISTED WITH THE REASON
             * rather than dropped.  Silently skipping it is what turns "my
             * save is missing" into an unexplained absence. */
            if (savetree_scan(t, NULL) != 0) {
                savetree_free_title(t);
                snprintf(t->error, sizeof(t->error), "read failed (errno %d)", errno);
            } else if (t->file_count == 0) {
                snprintf(t->error, sizeof(t->error), "no files in user/");
            }

            read_meta_longname(de->d_name, t->name, sizeof(t->name),
                               t->game_code, sizeof(t->game_code));
            WHBLogPrintf("wiiu: %s  files=%d size=%uKB  name='%s'%s%s",
                         t->title_id, t->file_count,
                         (unsigned)(t->total_size / 1024),
                         t->name[0] ? t->name : "(none)",
                         t->error[0] ? "  ERROR: " : "", t->error);
            out->title_count++;
        }
        closedir(d);
    }

    if (roots_opened == 0) {
        snprintf(out->last_error, sizeof(out->last_error),
                 "Cannot open %s", WIIU_SAVES_DIR);
        return;
    }
    if (out->title_count == 0)
        snprintf(out->last_error, sizeof(out->last_error), "No Wii U saves found");
}

/* ---- shared ---- */

bool natives_root_for(const char *title_id, char *out, size_t out_size) {
    if (vwiisaves_root_for(title_id, out, out_size)) return true;
    return wiiusaves_root_for(title_id, out, out_size);
}

int natives_backup(const SyncState *state, const char *title_id,
                   char *msg, size_t msg_size) {
    char src[SAVE_DIR_LEN];
    if (!natives_root_for(title_id, src, sizeof(src))) {
        snprintf(msg, msg_size, "Unknown native title id %s", title_id);
        return -1;
    }
    if (!dir_exists(src)) {
        snprintf(msg, msg_size, "No existing save to back up");
        return 0;   /* server-only title: nothing to protect */
    }

    char rel[TITLE_ID_LEN + 64];
    snprintf(rel, sizeof(rel), APP_DATA_SUBDIR "/backup/%s", title_id);
    char dst[SAVE_DIR_LEN];
    sdpath(state, rel, dst, sizeof(dst));

    if (savetree_copy_dir(src, dst) != 0) {
        snprintf(msg, msg_size, "Backup to %s FAILED", dst);
        return -1;
    }
    snprintf(msg, msg_size, "Backed up to %s", dst);
    return 0;
}
