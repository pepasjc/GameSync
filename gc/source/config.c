/*
 * config.c — SD-card-backed GameCube client configuration.
 *
 * Config lives on the same FAT SD volume used for ROM installs:
 *   sd:/3dssync/config.txt
 *
 * Catalog/save fetches should still work when the SD is absent — in that case
 * the in-RAM defaults are used and edits simply can't be persisted.
 */

#include "config.h"

#include <stdio.h>
#include <string.h>
#include <strings.h>
#include <stdlib.h>
#include <sys/stat.h>
#include <time.h>

#define DEFAULT_SERVER_URL "http://192.168.1.201:8000"
#define DEFAULT_API_KEY    "anything"
#define DEFAULT_STATIC_IP  "192.168.1.95"
#define DEFAULT_NETMASK    "255.255.255.0"
#define DEFAULT_GATEWAY    "192.168.1.1"

#define APP_DIR_PATH SD_ROOT APP_DATA_SUBDIR

static char g_io_buf[4096];

const char *sd_device_to_str(SdDevice dev) {
    switch (dev) {
        case SD_DEV_GECKO_A: return "geckoa";
        case SD_DEV_GECKO_B: return "geckob";
        case SD_DEV_SP2:
        default:             return "sp2";
    }
}

SdDevice config_sd_device_from_str(const char *value) {
    if (!value) return SD_DEV_SP2;
    if (strcasecmp(value, "geckoa") == 0 || strcasecmp(value, "slota") == 0)
        return SD_DEV_GECKO_A;
    if (strcasecmp(value, "geckob") == 0 || strcasecmp(value, "slotb") == 0)
        return SD_DEV_GECKO_B;
    return SD_DEV_SP2;
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
    state->use_static_ip = true;
    strncpy(state->static_ip, DEFAULT_STATIC_IP, sizeof(state->static_ip) - 1);
    strncpy(state->static_netmask, DEFAULT_NETMASK, sizeof(state->static_netmask) - 1);
    strncpy(state->static_gateway, DEFAULT_GATEWAY, sizeof(state->static_gateway) - 1);
    state->sd_device = SD_DEV_SP2;
    strncpy(state->games_folder, DEFAULT_GAMES_DIR, sizeof(state->games_folder) - 1);
    state->mmce_mode[0] = GID_ON;
    state->mmce_mode[1] = GID_ON;
    strncpy(state->sd_root, SD_ROOT, sizeof(state->sd_root) - 1);
}

static bool parse_bool_value(const char *value, bool *out) {
    if (!value || !out) return false;
    if (!strcasecmp(value, "1") || !strcasecmp(value, "true") ||
        !strcasecmp(value, "yes") || !strcasecmp(value, "on") ||
        !strcasecmp(value, "static")) { *out = true; return true; }
    if (!strcasecmp(value, "0") || !strcasecmp(value, "false") ||
        !strcasecmp(value, "no") || !strcasecmp(value, "off") ||
        !strcasecmp(value, "dhcp")) { *out = false; return true; }
    return false;
}

static int parse_gid_value(const char *value) {
    if (value && (!strcasecmp(value, "off") || !strcasecmp(value, "0") ||
                  !strcasecmp(value, "false") || !strcasecmp(value, "no")))
        return GID_OFF;
    return GID_ON;
}

static const char *gid_to_str(int mode) {
    return mode == GID_OFF ? "off" : "on";
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
                } else if (!strcmp(key, "use_static_ip") || !strcmp(key, "network")) {
                    bool en; if (parse_bool_value(val, &en)) state->use_static_ip = en;
                } else if (!strcmp(key, "dhcp")) {
                    bool en; if (parse_bool_value(val, &en)) state->use_static_ip = !en;
                } else if (!strcmp(key, "static_ip") || !strcmp(key, "ip")) {
                    strncpy(state->static_ip, val, sizeof(state->static_ip) - 1);
                    state->static_ip[sizeof(state->static_ip) - 1] = '\0';
                } else if (!strcmp(key, "static_netmask") || !strcmp(key, "netmask")) {
                    strncpy(state->static_netmask, val, sizeof(state->static_netmask) - 1);
                    state->static_netmask[sizeof(state->static_netmask) - 1] = '\0';
                } else if (!strcmp(key, "static_gateway") || !strcmp(key, "gateway")) {
                    strncpy(state->static_gateway, val, sizeof(state->static_gateway) - 1);
                    state->static_gateway[sizeof(state->static_gateway) - 1] = '\0';
                } else if (!strcmp(key, "sd_device") || !strcmp(key, "storage")) {
                    state->sd_device = config_sd_device_from_str(val);
                } else if (!strcmp(key, "games_folder") || !strcmp(key, "roms_folder")) {
                    strncpy(state->games_folder, val, sizeof(state->games_folder) - 1);
                    state->games_folder[sizeof(state->games_folder) - 1] = '\0';
                } else if (!strcmp(key, "gameid_slota")) {
                    state->mmce_mode[0] = parse_gid_value(val);
                } else if (!strcmp(key, "gameid_slotb")) {
                    state->mmce_mode[1] = parse_gid_value(val);
                }
            }
        }
        line = next;
    }
}

static void ensure_app_dir(void) {
    mkdir(APP_DIR_PATH, 0777);
}

static void format_config_text(const SyncState *state, char *out, size_t out_size) {
    snprintf(out, out_size,
             "# Save Sync GameCube client\n"
             "# Use a dotted LAN IP for server_url; hostnames are not supported.\n"
             "server_url=%s\n"
             "api_key=%s\n"
             "use_static_ip=%s\n"
             "static_ip=%s\n"
             "static_netmask=%s\n"
             "static_gateway=%s\n"
             "sd_device=%s\n"
             "games_folder=%s\n"
             "gameid_slota=%s\n"
             "gameid_slotb=%s\n"
             "%s%s%s",
             state->server_url, state->api_key,
             state->use_static_ip ? "true" : "false",
             state->static_ip, state->static_netmask, state->static_gateway,
             sd_device_to_str(state->sd_device), state->games_folder,
             gid_to_str(state->mmce_mode[0]), gid_to_str(state->mmce_mode[1]),
             state->console_id[0] ? "console_id=" : "",
             state->console_id[0] ? state->console_id : "",
             state->console_id[0] ? "\n" : "");
}

bool config_save(const SyncState *state) {
    if (!state) return false;
    ensure_app_dir();
    format_config_text(state, g_io_buf, sizeof(g_io_buf));

    FILE *fp = fopen(CONFIG_PATH, "w");
    if (!fp) return false;
    size_t len = strlen(g_io_buf);
    size_t n = fwrite(g_io_buf, 1, len, fp);
    fclose(fp);
    return n == len;
}

bool config_load(SyncState *state, char *err_out, size_t err_size) {
    if (!state) return false;
    config_apply_defaults(state);

    FILE *fp = fopen(CONFIG_PATH, "r");
    if (!fp) {
        /* No config yet — write the defaults so the user has a file to edit. */
        if (!config_save(state)) {
            if (err_out) snprintf(err_out, err_size,
                                  "No config and could not create %s", CONFIG_PATH);
            /* Not fatal: run with defaults. */
            return true;
        }
        return true;
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

    FILE *fp = fopen(CONSOLE_ID_PATH, "r");
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
    snprintf(state->console_id, sizeof(state->console_id), "gc_%08x", r);

    ensure_app_dir();
    fp = fopen(CONSOLE_ID_PATH, "w");
    if (fp) {
        fprintf(fp, "%s\n", state->console_id);
        fclose(fp);
    }
}
