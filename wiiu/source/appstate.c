/*
 * appstate.c — ProcUI lifecycle, driven directly in every environment.
 * See appstate.h; the exit sequence mirrors NUSspli's, which is the reference
 * implementation that exits cleanly everywhere.
 */

#include "appstate.h"

#include <coreinit/core.h>
#include <coreinit/dynload.h>
#include <coreinit/foreground.h>
#include <coreinit/systeminfo.h>
#include <coreinit/thread.h>
#include <coreinit/time.h>
#include <proc_ui/procui.h>
#include <whb/log.h>

static bool g_aroma = false;
static bool g_shutdown_done = false;
static volatile bool g_exiting = false;

bool app_is_aroma(void) { return g_aroma; }
bool app_exit_pending(void) { return g_exiting; }

/* Registered on PROCUI_CALLBACK_EXIT so the EXITING transition is seen no
 * matter who pumps the messages: once this has fired, the system is switching
 * away and any further launch request would compete with the one in flight. */
static uint32_t mark_exiting(void *ctx) {
    (void)ctx;
    g_exiting = true;
    return 0;
}

void app_init(void) {
    /* Aroma ships a "homebrew_kernel" RPL; its presence is the standard way
     * homebrew tells the two environments apart.  Only used for logging and
     * the HBL relaunch special-case in main.c — the ProcUI flow itself is
     * identical everywhere, exactly as NUSspli runs it. */
    OSDynLoad_Module mod;
    g_aroma = (OSDynLoad_Acquire("homebrew_kernel", &mod) == OS_DYNLOAD_OK);
    if (g_aroma) OSDynLoad_Release(mod);

    ProcUIInit(&OSSavesDone_ReadyToRelease);

    /* HOME = quit in both environments (see appstate.h): the overlay renders
     * black whenever the process is a hijacked wrapper title, and there is no
     * reliable way to tell.  The DENIED callback (main.c) turns the press
     * into a clean quit. */
    OSEnableHomeButtonMenu(FALSE);
    ProcUIRegisterCallback(PROCUI_CALLBACK_EXIT, mark_exiting, NULL, 99);

    WHBLogPrintf("proc: direct ProcUI (%s environment)",
                 g_aroma ? "Aroma" : "legacy/HBL");
}

bool app_running(void) {
    /* ProcUI messages are only pumped on the main core. */
    if (!OSIsMainCore()) return true;

    switch (ProcUIProcessMessages(TRUE)) {
        case PROCUI_STATUS_EXITING:
            g_exiting = true;
            return false;
        case PROCUI_STATUS_RELEASE_FOREGROUND:
            /* MEM1 is handed back by the registered RELEASE callback; this
             * tells the system we are done with it. */
            ProcUIDrawDoneRelease();
            break;
        case PROCUI_STATUS_IN_BACKGROUND:
            OSSleepTicks(OSMillisecondsToTicks(20));
            break;
        case PROCUI_STATUS_IN_FOREGROUND:
        default:
            break;
    }
    return true;
}

void app_wait_exit(void) {
    /* NUSspli's exit pump: after the launch request, keep answering ProcUI —
     * RELEASE_FOREGROUND gets ProcUIDrawDoneRelease (that is what actually
     * performs the foreground handover) — until EXITING arrives.  NUSspli
     * loops unbounded; a deadline is kept here so a failed launch request
     * degrades into a fall-through exit instead of hanging the console. */
    OSTime deadline = OSGetTime() + OSMillisecondsToTicks(10000);
    ProcUIStatus last = (ProcUIStatus)-1;
    while (!g_exiting) {
        ProcUIStatus status = ProcUIProcessMessages(TRUE);
        if (status != last) {   /* transition trace for the UDP log */
            WHBLogPrintf("app_wait_exit: ProcUI status %d", (int)status);
            last = status;
        }
        if (status == PROCUI_STATUS_EXITING) {
            g_exiting = true;
            break;
        }
        if (status == PROCUI_STATUS_RELEASE_FOREGROUND)
            ProcUIDrawDoneRelease();
        else
            OSSleepTicks(OSMillisecondsToTicks(16));
        if (OSGetTime() - deadline > 0) {
            WHBLogPrintf("app_wait_exit: EXITING never arrived, giving up");
            break;
        }
    }
    WHBLogPrintf("app_wait_exit: done (exiting=%d)", (int)g_exiting);
}

void app_shutdown(void) {
    if (g_shutdown_done) return;
    g_shutdown_done = true;

    /* Just ProcUIShutdown, like NUSspli.  WHBProcShutdown is deliberately
     * NOT used any more: it fires its own SYSRelaunchTitle for recognised
     * host titles, which competes with the launch request quit_to_menu has
     * already issued and pumped to completion. */
    ProcUIShutdown();
}
