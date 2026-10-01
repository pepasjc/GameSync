#include "ui.h"
#include "ui_log.h"
#include "views.h"
#include "saves.h"
#include <stdio.h>
#include <string.h>
#include <sys/iosupport.h>

// ---------------------------------------------------------------------------
// Video: one 256x192 16-bit bitmap per screen, drawn in RAM and copied over
// ---------------------------------------------------------------------------

Surface ui_top, ui_bottom;

// 96 KB each; VRAM bank A (main/top) and C (sub/bottom) hold the bitmaps
alignas(32) static Color top_buffer[SCREEN_W * SCREEN_H];
alignas(32) static Color bottom_buffer[SCREEN_W * SCREEN_H];
static u16 *vram_top, *vram_bottom;

const Hint HINTS_ANY[] = { { "A", "Continue" }, { NULL, NULL } };
const Hint HINTS_BACK_B[] = { { "B", "Back" }, { NULL, NULL } };
const Hint HINTS_EXIT[] = { { "START", "Exit" }, { NULL, NULL } };

static void copy_rows(u16 *dst, const Color *src, int y, int h) {
    if (y < 0) {
        h += y;
        y = 0;
    }
    if (y + h > SCREEN_H) h = SCREEN_H - y;
    if (h <= 0) return;
    // 32-bit copies: VRAM ignores 8-bit writes
    const u32 *s = (const u32 *)(src + y * SCREEN_W);
    u32 *d = (u32 *)(dst + y * SCREEN_W);
    for (int n = h * SCREEN_W / 2; n > 0; n--) *d++ = *s++;
}

void ui_present_rows(const Surface *s, int y, int h) {
    copy_rows(s == &ui_top ? vram_top : vram_bottom, s->px, y, h);
}

void ui_present(const Surface *s) {
    ui_present_rows(s, 0, SCREEN_H);
}

// ---------------------------------------------------------------------------
// stdout -> activity log
// ---------------------------------------------------------------------------

static TaskView task;
static char task_status[96], task_detail[96];
static bool task_live;
static u64 task_last_draw;

static ssize_t log_write(struct _reent *r, void *fd, const char *ptr, size_t len) {
    ui_log_write(ptr, len);
    if (task_live) {
        view_task_log(&ui_bottom);
        ui_present_rows(&ui_bottom, TASK_LOG_Y, TASK_LOG_H);
    }
    return (ssize_t)len;
}

static const devoptab_t log_devoptab = {
    .name = "con",
    .write_r = log_write,
};

void ui_init(void) {
    videoSetMode(MODE_5_2D);
    videoSetModeSub(MODE_5_2D);
    vramSetBankA(VRAM_A_MAIN_BG);
    vramSetBankC(VRAM_C_SUB_BG);
    lcdMainOnTop();
    int top_bg = bgInit(3, BgType_Bmp16, BgSize_B16_256x256, 0, 0);
    int bottom_bg = bgInitSub(3, BgType_Bmp16, BgSize_B16_256x256, 0, 0);
    vram_top = bgGetGfxPtr(top_bg);
    vram_bottom = bgGetGfxPtr(bottom_bg);

    gfx_surface_init(&ui_top, top_buffer, SCREEN_W, SCREEN_H);
    gfx_surface_init(&ui_bottom, bottom_buffer, SCREEN_W, SCREEN_H);
    theme_background(&ui_top);
    theme_background(&ui_bottom);
    ui_present(&ui_top);
    ui_present(&ui_bottom);

    ui_log_clear();
    devoptab_list[STD_OUT] = &log_devoptab;
    devoptab_list[STD_ERR] = &log_devoptab;
    setvbuf(stdout, NULL, _IONBF, 0);
    setvbuf(stderr, NULL, _IONBF, 0);
}

int ui_wait(int keys) {
    while (pmMainLoop()) {
        swiWaitForVBlank();
        scanKeys();
        int k = keysDown();
        if (keys ? (k & keys) : k) return k;
    }
    return 0;
}

int ui_message(const char *toolbar, const char *title, const char *body, UiKind kind,
               const Hint *hints, int keys) {
    task_live = false;
    theme_message(&ui_bottom, toolbar, title, body, kind, hints);
    ui_present(&ui_bottom);
    return ui_wait(keys);
}

// ---------------------------------------------------------------------------
// Task screen
// ---------------------------------------------------------------------------

static void task_draw(void) {
    view_task(&ui_bottom, &task);
    ui_present(&ui_bottom);
    task_last_draw = tickGetCount();
}

void ui_task_begin(const char *title, const char *status) {
    ui_log_clear();
    memset(&task, 0, sizeof(task));
    task.title = title;
    snprintf(task_status, sizeof(task_status), "%.95s", status ? status : "");
    task_detail[0] = '\0';
    task.status = task_status;
    task.detail = task_detail;
    task.kind = KIND_INFO;
    task_live = true;
    task_draw();
}

void ui_task_status(const char *status, const char *detail) {
    snprintf(task_status, sizeof(task_status), "%.95s", status ? status : "");
    snprintf(task_detail, sizeof(task_detail), "%.95s", detail ? detail : "");
    task.done = task.total = 0;
    task_live = true;
    task_draw();
}

void ui_task_progress(const char *status, const char *detail, uint32_t done, uint32_t total) {
    if (status) snprintf(task_status, sizeof(task_status), "%.95s", status);
    snprintf(task_detail, sizeof(task_detail), "%.95s", detail ? detail : "");
    bool first = (task.total != total);
    task.done = done;
    task.total = total;
    task_live = true;
    if (first || done >= total || tickGetCount() - task_last_draw >= TICK_FREQ / 10) task_draw();
}

void ui_task_hints(const Hint *hints) {
    task.hints = hints;
    task_draw();
}

int ui_task_end(UiKind kind, const char *result, const char *detail, const Hint *hints, int keys) {
    snprintf(task_status, sizeof(task_status), "%.95s", result ? result : "");
    snprintf(task_detail, sizeof(task_detail), "%.95s", detail ? detail : "");
    task.kind = kind;
    task.finished = true;
    task.hints = hints;
    task_draw();
    task_live = false;
    return ui_wait(keys);
}

// ---------------------------------------------------------------------------
// Save dialogs
// ---------------------------------------------------------------------------

void ui_show_save_details(Title *title) {
    task_live = false;
    view_save_details(&ui_bottom, title);
    ui_present(&ui_bottom);
    ui_wait(0);
}

static void hash_prefix(const uint8_t *hash, char out[17]) {
    for (int i = 0; i < 8; i++) snprintf(out + i * 2, 3, "%02x", hash[i]);
}

bool ui_confirm_sync(Title *title, const char *server_hash, size_t server_size, bool is_upload) {
    // Ensure local hash is calculated
    if (!title->hash_calculated) {
        ui_task_status("Calculating hash", title->game_name);
        if (saves_ensure_hash(title) != 0) {
            ui_task_end(KIND_ERROR, "Failed to calculate hash", title->game_name, HINTS_ANY, 0);
            return false;
        }
    }
    task_live = false;

    bool has_server = server_hash && server_hash[0] != '\0';
    char local_hex[65];
    for (int i = 0; i < 32; i++) snprintf(&local_hex[i * 2], 3, "%02x", title->hash[i]);
    bool match = has_server && strncmp(local_hex, server_hash, 64) == 0;

    CompareView v;
    memset(&v, 0, sizeof(v));
    v.heading = is_upload ? "Upload" : "Download";
    v.game = title->game_name;
    v.has_local = true;
    v.local_size = title->save_size;
    hash_prefix(title->hash, v.local_hash);
    v.has_server = has_server;
    v.server_size = server_size;
    if (has_server) snprintf(v.server_hash, sizeof(v.server_hash), "%.16s", server_hash);
    v.action = match ? SYNC_UP_TO_DATE : (is_upload ? SYNC_UPLOAD : SYNC_DOWNLOAD);
    view_sync_compare(&ui_top, &v);
    ui_present(&ui_top);
    view_transfer_confirm(&ui_bottom, title->game_name, is_upload, has_server, match);
    ui_present(&ui_bottom);

    return (ui_wait(KEY_A | KEY_B) & KEY_A) != 0;
}

SyncAction ui_confirm_smart_sync(Title *title, SyncDecision *decision) {
    task_live = false;

    CompareView v;
    memset(&v, 0, sizeof(v));
    v.heading = "Smart Sync";
    v.game = title->game_name;
    v.has_local = title->save_size > 0;
    v.local_size = title->save_size;
    if (title->hash_calculated) hash_prefix(title->hash, v.local_hash);
    v.has_server = decision->server_hash[0] != '\0';
    v.server_size = decision->server_size;
    snprintf(v.server_hash, sizeof(v.server_hash), "%.16s", decision->server_hash);
    if (decision->has_last_synced)
        snprintf(v.last_hash, sizeof(v.last_hash), "%.16s", decision->last_synced_hash);
    v.action = decision->action;
    view_sync_compare(&ui_top, &v);
    ui_present(&ui_top);
    view_sync_action(&ui_bottom, title->game_name, decision->action, decision->has_last_synced);
    ui_present(&ui_bottom);

    switch (decision->action) {
        case SYNC_UP_TO_DATE:
            ui_wait(0);
            return SYNC_UP_TO_DATE;
        case SYNC_UPLOAD:
            return (ui_wait(KEY_A | KEY_B) & KEY_A) ? SYNC_UPLOAD : SYNC_UP_TO_DATE;
        case SYNC_DOWNLOAD:
            return (ui_wait(KEY_A | KEY_B) & KEY_A) ? SYNC_DOWNLOAD : SYNC_UP_TO_DATE;
        case SYNC_CONFLICT: {
            int k = ui_wait(KEY_R | KEY_L | KEY_B);
            if (k & KEY_R) return SYNC_UPLOAD;
            if (k & KEY_L) return SYNC_DOWNLOAD;
            return SYNC_UP_TO_DATE;
        }
    }
    return SYNC_UP_TO_DATE;
}
