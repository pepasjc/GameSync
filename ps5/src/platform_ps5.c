/*
 * GameSync PS5 client - screen, controller and console services on the PS5.
 * SPDX-License-Identifier: GPL-3.0-or-later
 *
 * SDL2 (PacBrew's PS5 port) only for controllers; the screen is VideoOut via
 * ps5_video.c. Init order and controller handling follow PSXS5 /
 * SwanStationPS5 by darkxex (src/platform/plat_sdl.c, GPL-3.0-or-later).
 */
#if defined(__PROSPERO__)
#include "platform.h"

#include "config.h"
#include "gfx.h"
#include "ps5_unlock.h"
#include "ps5_video.h"

#include <SDL2/SDL.h>
#include <fcntl.h>
#include <signal.h>
#include <stdint.h>
#include <ucontext.h>
#include <unistd.h>
#include <stdio.h>
#include <string.h>
#include <time.h>

int sceSystemServiceHideSplashScreen(void);
int sceSystemServiceLoadExec(const char *path, const char **arguments);
int sceKernelSendNotificationRequest(int device, void *request, size_t size, int blocking);

#define MAX_PADS 4
static SDL_GameController *pads[MAX_PADS];
/* Each axis's resting value, sampled once a pad reports in. Movement only
 * counts relative to it: an axis that rests far from 0 (a trigger reporting
 * its full range, a second device the DualSense shows up as) must not read
 * as a held button - that kept moving the selection on its own. */
enum
{
    AX_LX,
    AX_LY,
    AX_L2,
    AX_R2,
    AX_COUNT
};
static const SDL_GameControllerAxis AXES[AX_COUNT] = {
    SDL_CONTROLLER_AXIS_LEFTX, SDL_CONTROLLER_AXIS_LEFTY, SDL_CONTROLLER_AXIS_TRIGGERLEFT,
    SDL_CONTROLLER_AXIS_TRIGGERRIGHT};
static int rest[MAX_PADS][AX_COUNT];
static int calibrate_in[MAX_PADS]; /* frames until the rest sample; 0 = done */

int gamesync_elevate(const char **route); /* elevation_shim.cpp */

/* Same order as PSXS5's plat_prepare_storage: the HEN's jailbreak request
 * first, then the Lapy helper through the ELF loader (port 9021), which
 * works with nothing but kstuff + elfldr running. */
bool plat_unlock_data(char *status, size_t size)
{
    UnlockResult r = ps5_unlock_etahen();
    if (r == UNLOCK_OK || r == UNLOCK_ALREADY)
    {
        snprintf(status, size, "%s", ps5_unlock_describe(r));
        return true;
    }
    const char *route = "none";
    int lapy = gamesync_elevate(&route);
    if (lapy == 0 && ps5_data_listable())
    {
        snprintf(status, size, "granted by Lapy (%s)", route);
        return true;
    }
    snprintf(status, size, "%s; Lapy code %d via %s", ps5_unlock_describe(r), lapy, route);
    return false;
}

/* A crash leaves a line in gamesync.log: the signal, the address it
 * faulted on and the instruction, so the next one can be traced (async-
 * signal-safe: write() only, nothing allocated). */
static char crash_log[600];

static void put_hex(char **p, uint64_t v)
{
    static const char digits[] = "0123456789abcdef";
    *(*p)++ = '0';
    *(*p)++ = 'x';
    for (int i = 60; i >= 0; i -= 4)
        *(*p)++ = digits[(v >> i) & 15];
}

static void on_crash(int sig, siginfo_t *info, void *context)
{
    char line[160], *p = line;
    const char *head = "crashed: signal ";
    while (*head)
        *p++ = *head++;
    if (sig >= 10)
        *p++ = (char)('0' + sig / 10);
    *p++ = (char)('0' + sig % 10);
    const char *at = " at ";
    while (*at)
        *p++ = *at++;
    put_hex(&p, (uint64_t)(uintptr_t)(info ? info->si_addr : 0));
    const ucontext_t *uc = context;
    if (uc)
    {
        const char *pc = " pc ";
        while (*pc)
            *p++ = *pc++;
        put_hex(&p, (uint64_t)uc->uc_mcontext.mc_rip);
    }
    *p++ = '\n';
    int fd = open(crash_log, O_WRONLY | O_APPEND | O_CREAT, 0666);
    if (fd >= 0)
    {
        write(fd, line, (size_t)(p - line));
        close(fd);
    }
    signal(sig, SIG_DFL);
    raise(sig);
}

static void watch_for_crashes(void)
{
    snprintf(crash_log, sizeof(crash_log), "%s", data_path("gamesync.log"));
    struct sigaction sa;
    memset(&sa, 0, sizeof(sa));
    sa.sa_sigaction = on_crash;
    sa.sa_flags = SA_SIGINFO;
    sigemptyset(&sa.sa_mask);
    const int sigs[] = {SIGSEGV, SIGBUS, SIGILL, SIGFPE, SIGABRT, SIGSYS};
    for (size_t i = 0; i < sizeof(sigs) / sizeof(sigs[0]); ++i)
        sigaction(sigs[i], &sa, NULL);
}

bool plat_init(char *error, size_t size)
{
    watch_for_crashes();
    sceSystemServiceHideSplashScreen();
    if (SDL_Init(SDL_INIT_EVENTS) != 0)
    {
        snprintf(error, size, "SDL: %s", SDL_GetError());
        return false;
    }
    if (SDL_InitSubSystem(SDL_INIT_GAMECONTROLLER) != 0)
    {
        snprintf(error, size, "SDL controllers: %s", SDL_GetError());
        return false;
    }
    SDL_GameControllerEventState(SDL_ENABLE);
    if (!ps5_video_open(error, size))
        return false;
    return true;
}

void plat_shutdown(void)
{
    for (int i = 0; i < MAX_PADS; ++i)
        if (pads[i])
            SDL_GameControllerClose(pads[i]);
    SDL_Quit();
}

/* As SwanStationPS5 and ProsperoStore do: LoadExec "exit" has the shell
 * close the title; asked again until it does. */
void plat_exit(void)
{
    fflush(NULL);
    for (;;)
    {
        int rc = sceSystemServiceLoadExec("exit", NULL);
        app_log("exit: LoadExec refused (0x%x)", (unsigned)rc);
        sleep(1);
    }
}

static void refresh_pads(void)
{
    for (int i = 0; i < MAX_PADS; ++i)
        if (pads[i] && !SDL_GameControllerGetAttached(pads[i]))
        {
            SDL_GameControllerClose(pads[i]);
            pads[i] = NULL;
        }
    for (int j = 0; j < SDL_NumJoysticks(); ++j)
    {
        if (!SDL_IsGameController(j))
            continue;
        SDL_JoystickID id = SDL_JoystickGetDeviceInstanceID(j);
        bool open = false;
        for (int i = 0; i < MAX_PADS; ++i)
            if (pads[i] && SDL_JoystickInstanceID(SDL_GameControllerGetJoystick(pads[i])) == id)
                open = true;
        for (int i = 0; !open && i < MAX_PADS; ++i)
            if (!pads[i])
            {
                pads[i] = SDL_GameControllerOpen(j);
                calibrate_in[i] = 3; /* let the first reports arrive */
                if (pads[i])
                    app_log("pad %d: %s", i, SDL_GameControllerName(pads[i]));
                break;
            }
    }
}

uint32_t plat_poll(bool *quit)
{
    SDL_Event ev;
    bool changed = false;
    while (SDL_PollEvent(&ev))
    {
        if (ev.type == SDL_QUIT)
            *quit = true;
        else if (ev.type == SDL_CONTROLLERDEVICEADDED || ev.type == SDL_CONTROLLERDEVICEREMOVED)
            changed = true;
    }
    static bool first = true;
    if (changed || first)
    {
        refresh_pads();
        first = false;
    }
    static const struct
    {
        SDL_GameControllerButton sdl;
        uint32_t bit;
    } map[] = {
        {SDL_CONTROLLER_BUTTON_DPAD_UP, BTN_UP},
        {SDL_CONTROLLER_BUTTON_DPAD_DOWN, BTN_DOWN},
        {SDL_CONTROLLER_BUTTON_DPAD_LEFT, BTN_LEFT},
        {SDL_CONTROLLER_BUTTON_DPAD_RIGHT, BTN_RIGHT},
        {SDL_CONTROLLER_BUTTON_A, BTN_CROSS},
        {SDL_CONTROLLER_BUTTON_B, BTN_CIRCLE},
        {SDL_CONTROLLER_BUTTON_X, BTN_SQUARE},
        {SDL_CONTROLLER_BUTTON_Y, BTN_TRIANGLE},
        {SDL_CONTROLLER_BUTTON_LEFTSHOULDER, BTN_L1},
        {SDL_CONTROLLER_BUTTON_RIGHTSHOULDER, BTN_R1},
        {SDL_CONTROLLER_BUTTON_START, BTN_OPTIONS},
        {SDL_CONTROLLER_BUTTON_BACK, BTN_SELECT},
        {SDL_CONTROLLER_BUTTON_TOUCHPAD, BTN_TOUCHPAD},
    };
    uint32_t held = 0;
    for (int i = 0; i < MAX_PADS; ++i)
    {
        SDL_GameController *c = pads[i];
        if (!c)
            continue;
        for (size_t k = 0; k < sizeof(map) / sizeof(map[0]); ++k)
            if (SDL_GameControllerGetButton(c, map[k].sdl))
                held |= map[k].bit;
        int ax[AX_COUNT];
        for (int a = 0; a < AX_COUNT; ++a)
            ax[a] = SDL_GameControllerGetAxis(c, AXES[a]);
        if (calibrate_in[i] > 0)
        {
            if (--calibrate_in[i] == 0)
            {
                for (int a = 0; a < AX_COUNT; ++a)
                    rest[i][a] = ax[a];
                app_log("pad %d rest: lx %d ly %d l2 %d r2 %d", i, ax[AX_LX], ax[AX_LY],
                        ax[AX_L2], ax[AX_R2]);
            }
            continue; /* the axes count once calibrated */
        }
        int lx = ax[AX_LX] - rest[i][AX_LX], ly = ax[AX_LY] - rest[i][AX_LY];
        if (ax[AX_L2] - rest[i][AX_L2] > 16000)
            held |= BTN_L2;
        if (ax[AX_R2] - rest[i][AX_R2] > 16000)
            held |= BTN_R2;
        if (ly < -20000)
            held |= BTN_UP;
        if (ly > 20000)
            held |= BTN_DOWN;
        if (lx < -20000)
            held |= BTN_LEFT;
        if (lx > 20000)
            held |= BTN_RIGHT;
    }
    return held;
}

void plat_present(void)
{
    ps5_video_present(gfx_pixels(), (size_t)GFX_W * 4);
}

/* The shell's notification request, as the payload SDK's examples send it. */
typedef struct
{
    char useless1[45];
    char message[3075];
} NotifyRequest;

void plat_notify(const char *text)
{
    NotifyRequest req;
    memset(&req, 0, sizeof(req));
    snprintf(req.message, sizeof(req.message), "%s", text);
    sceKernelSendNotificationRequest(0, &req, sizeof(req), 0);
}

uint64_t plat_ticks_ms(void)
{
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (uint64_t)ts.tv_sec * 1000u + (uint64_t)ts.tv_nsec / 1000000u;
}
#endif
