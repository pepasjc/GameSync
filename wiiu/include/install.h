#ifndef WIIUSYNC_INSTALL_H
#define WIIUSYNC_INSTALL_H

#include "common.h"

/*
 * Wii U title installation via MCP.
 *
 * A WUP/NUS folder downloaded from the catalog (<install>/<Name>/ holding
 * title.tmd, title.tik, title.cert and the numbered .app contents) is handed
 * to the system's own installer, exactly as WUP Installer GX2 does:
 *
 *   1. MCP_InstallSetTargetDevice   — NAND (MLC) or the console's USB drive
 *   2. MCP_InstallSetTargetUsb      — which USB device, when target is USB
 *   3. MCP_InstallTitleAsync        — kick it off, then poll progress
 *
 * The path MCP receives is an *FSA* path, not a devoptab path: it wants
 * "/vol/external01/install/Foo" or "/vol/usb/install/Foo", never
 * "fs:/vol/external01/..." or "usb:/...".  install_fsa_path() does that
 * translation — getting it wrong is the classic -21 (invalid arg) failure.
 *
 * IMPORTANT: MCP validates the ticket.  A title dumped without title.tik, or
 * one whose ticket does not match this console's region/keys, is rejected by
 * the system installer and nothing we do client-side changes that.
 */

/* Poll cadence, and how long to wait for MCP to actually pick the job up
 * before declaring it refused.  Without the timeout a rejected install spins
 * forever waiting for an inProgress that never arrives. */
#define INSTALL_POLL_MS           500u
#define INSTALL_START_TIMEOUT_MS  15000u

/* Contents listed in a TMD.  Retail titles run well under this; the cap just
 * bounds the pre-flight scan. */
#define INSTALL_MAX_CONTENTS      256

typedef struct {
    bool     running;
    bool     done;
    int      result;              /* 0 = installed, negative = MCP error */
    uint64_t size_total;
    uint64_t size_done;
    uint32_t contents_total;
    uint32_t contents_done;
    char     message[160];
} InstallProgress;

/* Translate a devoptab path ("fs:/vol/external01/install/Foo", "usb:/install/
 * Foo") into the FSA path MCP expects ("/vol/external01/install/Foo",
 * "/vol/usb/install/Foo").  Returns false when the root is unrecognised. */
/* FSA path MCP sees the SD card at.  /vol/external01 is the application's
 * own mount and is invisible to IOSU's installer, so it has to be bind
 * mounted somewhere MCP can reach before any install will do anything. */
#define MCP_SD_MOUNT "/vol/app_sd"

bool install_fsa_path(const char *devoptab_path, char *out, size_t out_size);

/* Bind /vol/external01 -> /vol/app_sd through a libmocha-unlocked FSA client.
 * Idempotent; called automatically by install_wup_folder(). */
bool install_mount_sd(char *err, size_t err_size);
void install_unmount_sd(void);

/* True when the console's USB drive is usb02 rather than the usual usb01. */
bool install_usb_is_second(void);

/*
 * Verify a staged WUP folder before handing it to MCP.
 *
 * Reads title.tmd for the title id and its content list, then confirms every
 * <content-id>.app is present and non-empty and that no .part file is left
 * over.  MCP installs an incomplete set without complaining and the title
 * then never shows up on the menu, so this is what turns "nothing happened"
 * into an actionable message.
 *
 * Returns 0 when installable; -1 with a reason in ``msg``.
 */
int install_check_folder(const char *dir, uint64_t *title_id_out,
                         char *msg, size_t msg_size);

/*
 * Install the WUP folder at ``dir`` (a devoptab path).  ``target`` is "mlc"
 * or "usb" and selects where the title lands, independent of where the WUP
 * folder was staged.  Blocks until the install finishes, calling ``on_poll``
 * (may be NULL) roughly twice a second so the caller can repaint.
 *
 * Returns 0 on success; ``progress->message`` always carries a result line.
 */
int install_wup_folder(const char *dir, const char *target,
                       InstallProgress *progress,
                       void (*on_poll)(const InstallProgress *));

#endif /* WIIUSYNC_INSTALL_H */
