/*
 * config.c — SD-card-backed Wii U client configuration.
 *
 * Config lives on the SD card mounted by WHBMountSdCard():
 *   fs:/vol/external01/3dssync/config.txt
 *
 * Catalog/save fetches should still work when the SD is absent — in that case
 * the in-RAM defaults are used and edits simply can't be persisted.
 */

#include "config.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <sys/stat.h>
#include <time.h>

#define DEFAULT_SERVER_URL "http://192.168.1.201:8000"
#define DEFAULT_API_KEY    "anything"

static char g_io_buf[4096];

const char *sdpath(const SyncState *state, const char *rel,
                   char *out, size_t out_size) {
    const char *root = (state && state->sd_root[0]) ? state->sd_root
                                                    : SD_ROOT_DEFAULT;
    snprintf(out, out_size, "%s%s", root, rel ? rel : "");
    return out;
}

static void trim(char *s) {
    if (!s) return;
    char *start = s;
    while (*start == ' ' || *start == '\t' || *start == '\r' || *start == '\n')
        start++;
    if (start != s) memmove(s, start, strlen(start) + 1);
    size_t len = strlen(s);
    while (len > 0) {
        char c = s[len - 1];
        if (c == ' ' || c == '\t' || c == '\r' || c == '\n') { s[--len] = '\0'; continue; }
        break;
    }
}

static void config_apply_defaults(SyncState *state) {
    memset(state, 0, sizeof(*state));
    strncpy(state->server_url, DEFAULT_SERVER_URL, sizeof(state->server_url) - 1);
    strncpy(state->api_key, DEFAULT_API_KEY, sizeof(state->api_key) - 1);
    strncpy(state->sd_root, SD_ROOT_DEFAULT, sizeof(state->sd_root) - 1);
    strncpy(state->nin_saves_dir, DEFAULT_NIN_SAVES_DIR, sizeof(state->nin_saves_dir) - 1);
    strncpy(state->games_dir, DEFAULT_GAMES_DIR, sizeof(state->games_dir) - 1);
    strncpy(state->wbfs_dir, DEFAULT_WBFS_DIR, sizeof(state->wbfs_dir) - 1);
    strncpy(state->install_dir, DEFAULT_INSTALL_DIR, sizeof(state->install_dir) - 1);
    strncpy(state->rom_storage, "sd", sizeof(state->rom_storage) - 1);
    strncpy(state->install_target, "usb", sizeof(state->install_target) - 1);
    state->sync_vwii = true;
    state->sync_wiiu = true;
    strncpy(state->exit_mode, "auto", sizeof(state->exit_mode) - 1);
}

static bool parse_bool_value(const char *value, bool *out) {
    if (!value || !out) return false;
    if (!strcasecmp(value, "1") || !strcasecmp(value, "true") ||
        !strcasecmp(value, "yes") || !strcasecmp(value, "on")) { *out = true; return true; }
    if (!strcasecmp(value, "0") || !strcasecmp(value, "false") ||
        !strcasecmp(value, "no") || !strcasecmp(value, "off")) { *out = false; return true; }
    return false;
}

/* Normalise a folder to leading-slash / no-trailing-slash form. */
static void copy_folder(char *dst, size_t dst_size, const char *val) {
    if (!val || !val[0]) return;
    snprintf(dst, dst_size, "%s%s", val[0] == '/' ? "" : "/", val);
    size_t n = strlen(dst);
    while (n > 1 && dst[n - 1] == '/') dst[--n] = '\0';
}

static void parse_config_text(SyncState *state, char *text) {
    char *line = text;
    while (line && *line) {
        char *next = strpbrk(line, "\r\n");
        if (next) {
            *next++ = '\0';
            while (*next == '\r' || *next == '\n') next++;
        }
        trim(line);
        if (line[0] && line[0] != '#') {
            char *eq = strchr(line, '=');
            if (eq) {
                *eq = '\0';
                char *key = line, *val = eq + 1;
                trim(key); trim(val);

                if (!strcmp(key, "server_url")) {
                    strncpy(state->server_url, val, sizeof(state->server_url) - 1);
                    state->server_url[sizeof(state->server_url) - 1] = '\0';
                } else if (!strcmp(key, "api_key")) {
                    strncpy(state->api_key, val, sizeof(state->api_key) - 1);
                    state->api_key[sizeof(state->api_key) - 1] = '\0';
                } else if (!strcmp(key, "console_id")) {
                    strncpy(state->console_id, val, sizeof(state->console_id) - 1);
                    state->console_id[sizeof(state->console_id) - 1] = '\0';
                } else if (!strcmp(key, "nintendont_saves_dir") ||
                           !strcmp(key, "nin_saves_dir")) {
                    copy_folder(state->nin_saves_dir, sizeof(state->nin_saves_dir), val);
                } else if (!strcmp(key, "games_dir") || !strcmp(key, "games_folder")) {
                    copy_folder(state->games_dir, sizeof(state->games_dir), val);
                } else if (!strcmp(key, "wbfs_dir") || !strcmp(key, "wbfs_folder")) {
                    copy_folder(state->wbfs_dir, sizeof(state->wbfs_dir), val);
                } else if (!strcmp(key, "install_dir") || !strcmp(key, "install_folder")) {
                    copy_folder(state->install_dir, sizeof(state->install_dir), val);
                } else if (!strcmp(key, "rom_storage")) {
                    if (!strcasecmp(val, "sd") || !strcasecmp(val, "usb")) {
                        strncpy(state->rom_storage, val, sizeof(state->rom_storage) - 1);
                        state->rom_storage[sizeof(state->rom_storage) - 1] = '\0';
                    }
                } else if (!strcmp(key, "install_target")) {
                    if (!strcasecmp(val, "mlc") || !strcasecmp(val, "usb")) {
                        strncpy(state->install_target, val, sizeof(state->install_target) - 1);
                        state->install_target[sizeof(state->install_target) - 1] = '\0';
                    }
                } else if (!strcmp(key, "sync_vwii")) {
                    bool en; if (parse_bool_value(val, &en)) state->sync_vwii = en;
                } else if (!strcmp(key, "sync_wiiu")) {
                    bool en; if (parse_bool_value(val, &en)) state->sync_wiiu = en;
                } else if (!strcmp(key, "exit_mode")) {
                    strncpy(state->exit_mode, val, sizeof(state->exit_mode) - 1);
                    state->exit_mode[sizeof(state->exit_mode) - 1] = '\0';
                }
            }
        }
        line = next;
    }
}

static void ensure_app_dir(const SyncState *state) {
    char dir[SAVE_DIR_LEN];
    sdpath(state, APP_DATA_SUBDIR, dir, sizeof(dir));
    mkdir(dir, 0777);
}

static void format_config_text(const SyncState *state, char *out, size_t out_size) {
    snprintf(out, out_size,
             "# Save Sync Wii U client\n"
             "# Network comes from the console's system settings.\n"
             "server_url=%s\n"
             "api_key=%s\n"
             "nintendont_saves_dir=%s\n"
             "games_dir=%s\n"
             "wbfs_dir=%s\n"
             "install_dir=%s\n"
             "# rom_storage: sd | usb  (where downloaded games are written;\n"
             "# usb means a FAT32 drive, not the console's own WFS drive)\n"
             "rom_storage=%s\n"
             "# install_target: mlc | usb  (where MCP installs a Wii U title)\n"
             "install_target=%s\n"
             "sync_vwii=%s\n"
             "sync_wiiu=%s\n"
             "# exit_mode: auto | menu | relaunch | none  (auto = let the\n"
             "# host loader restore the menu; legacy full/minimal = auto)\n"
             "exit_mode=%s\n"
             "%s%s%s",
             state->server_url, state->api_key,
             state->nin_saves_dir, state->games_dir, state->wbfs_dir,
             state->install_dir,
             state->rom_storage[0] ? state->rom_storage : "sd",
             state->install_target[0] ? state->install_target : "usb",
             state->sync_vwii ? "true" : "false",
             state->sync_wiiu ? "true" : "false",
             state->exit_mode[0] ? state->exit_mode : "auto",
             state->console_id[0] ? "console_id=" : "",
             state->console_id[0] ? state->console_id : "",
             state->console_id[0] ? "\n" : "");
}

bool config_save(const SyncState *state) {
    if (!state) return false;
    ensure_app_dir(state);
    format_config_text(state, g_io_buf, sizeof(g_io_buf));

    char path[SAVE_DIR_LEN];
    sdpath(state, APP_DATA_SUBDIR "/config.txt", path, sizeof(path));

    FILE *fp = fopen(path, "wb");
    if (!fp) return false;
    size_t len = strlen(g_io_buf);
    size_t n = fwrite(g_io_buf, 1, len, fp);
    fclose(fp);
    return n == len;
}

bool config_load(SyncState *state, char *err_out, size_t err_size) {
    if (!state) return false;
    config_apply_defaults(state);

    char path[SAVE_DIR_LEN];
    sdpath(state, APP_DATA_SUBDIR "/config.txt", path, sizeof(path));

    FILE *fp = fopen(path, "r");
    if (!fp) {
        /* No config yet — write the defaults so the user has a file to edit. */
        if (!config_save(state) && err_out)
            snprintf(err_out, err_size, "No config and could not create %s", path);
        return true;   /* not fatal: run with defaults */
    }

    size_t n = fread(g_io_buf, 1, sizeof(g_io_buf) - 1, fp);
    fclose(fp);
    g_io_buf[n] = '\0';
    parse_config_text(state, g_io_buf);

    if (strncmp(state->server_url, "http://", 7) != 0) {
        if (err_out) snprintf(err_out, err_size, "server_url must start with http://");
        return false;
    }
    return true;
}

void config_load_console_id(SyncState *state) {
    if (!state || state->console_id[0]) return;

    char path[SAVE_DIR_LEN];
    sdpath(state, APP_DATA_SUBDIR "/consoleid.txt", path, sizeof(path));

    FILE *fp = fopen(path, "r");
    if (fp) {
        size_t n = fread(g_io_buf, 1, sizeof(g_io_buf) - 1, fp);
        fclose(fp);
        g_io_buf[n] = '\0';
        trim(g_io_buf);
        if (g_io_buf[0]) {
            strncpy(state->console_id, g_io_buf, sizeof(state->console_id) - 1);
            state->console_id[sizeof(state->console_id) - 1] = '\0';
            return;
        }
    }

    unsigned r = (unsigned)time(NULL) ^ 0xC0FFEE;
    snprintf(state->console_id, sizeof(state->console_id), "wiiu_%08x", r);

    ensure_app_dir(state);
    fp = fopen(path, "w");
    if (fp) {
        fprintf(fp, "%s\n", state->console_id);
        fclose(fp);
    }
}
