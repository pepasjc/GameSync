#ifndef WIIUSYNC_APPSTATE_H
#define WIIUSYNC_APPSTATE_H

#include <stdbool.h>

/*
 * Process lifecycle — direct ProcUI in every environment, modelled on
 * NUSspli's state.c (the reference that exits cleanly everywhere).
 *
 * libwhb's WHBProc* is deliberately not used at all: its shutdown path fires
 * its own SYSRelaunchTitle for recognised host titles, competing with the
 * launch request our quit path issues, and under Aroma it recognises nothing
 * and issues none.  NUSspli's flow instead is: run ProcUIInit ourselves,
 * pump ProcUIProcessMessages on the main core, and on a user quit ALWAYS
 * issue a launch request (SYSLaunchMenu, or SYSRelaunchTitle for HBL-hosted)
 * and pump until EXITING before ProcUIShutdown.  Returning from main without
 * having done that leaves the foreground held with nothing scheduled to take
 * it — a black screen.
 *
 * The HOME overlay is disabled in BOTH environments and the denied press is
 * treated as a quit.  Detecting Aroma only proves the module loader is there,
 * not that this process is a real standalone title — hosted through any
 * wrapper hijack (HBL .rpx, wiiload) the overlay renders black with no way
 * out but a hard reboot.  HOME = quit is safe everywhere.
 */

void app_init(void);
bool app_running(void);
void app_shutdown(void);

/* True when running under Aroma (homebrew_kernel present). */
bool app_is_aroma(void);

/* True once ProcUI has reported (or begun) the EXITING transition — i.e. the
 * system is already switching away and no SYSLaunchMenu/SYSRelaunchTitle
 * request may be issued any more. */
bool app_exit_pending(void);

/* Pump ProcUI until it reports EXITING (calling ProcUIDrawDoneRelease on the
 * way, which is what actually performs the foreground handover).  Safe to
 * call in both environments and when the exit has already happened.  Bounded
 * by a timeout so a failed launch request can never hang the console. */
void app_wait_exit(void);

#endif /* WIIUSYNC_APPSTATE_H */
