/*
 * config.c - memory-card-backed PS2 client configuration.
 *
 * Config lives at mc0:/3DSSYNC/CONFIG.TXT:
 *
 *   server_url=http://192.168.1.201:8000
 *   api_key=anything
 *   use_static_ip=true
 *   static_ip=192.168.1.95
 *   static_netmask=255.255.255.0
 *   static_gateway=192.168.1.1
 *   storage=auto      # auto, usb, or hdd
 *
 * Storage is only the ROM/download target; catalog fetches should work
 * even when mass:/ is absent.
 */

#include "config.h"

#include <fcntl.h>
#include <libmc.h>
#include <stdarg.h>
#include <stdio.h>
#include <string.h>
#include <strings.h>
#include <stdlib.h>
#include <time.h>
#include <debug.h>

#define DEFAULT_SERVER_URL "http://192.168.1.201:8000"
#define DEFAULT_API_KEY    "anything"
#define DEFAULT_STATIC_IP  "192.168.1.95"
#define DEFAULT_NETMASK    "255.255.255.0"
#define DEFAULT_GATEWAY    "192.168.1.1"

#define MC_PORT 0
#define MC_SLOT 0

/* libmc (mcOpen) takes IOP open flags, NOT newlib POSIX flags.  newlib's
 * O_RDONLY is 0 -> mcman denies the read (sceMcResDeniedPermit), so config
 * reads silently failed and always fell back to defaults (ps2sdk issue #168). */
#define IOP_O_RDONLY 0x0001
#define IOP_O_WRONLY 0x0002
#define IOP_O_CREAT  0x0200
#define IOP_O_TRUNC  0x0400

#define MC_APP_DIR "/3DSSYNC"
#define MC_CONFIG_FILE "/3DSSYNC/CONFIG.TXT"
#define MC_CONSOLE_ID_FILE "/3DSSYNC/CONSOLEID.TXT"

static bool g_mc_ready = false;
static char g_mc_io_buf[2048] __attribute__((aligned(64)));

static void trim(char *s) {
    if (!s) return;

    char *start = s;
    while (*start == ' ' || *start == '\t' ||
           *start == '\r' || *start == '\n') {
        start++;
    }
    if (start != s) {
        memmove(s, start, strlen(start) + 1);
    }

    size_t len = strlen(s);
    while (len > 0) {
        char c = s[len - 1];
        if (c == ' ' || c == '\t' || c == '\r' || c == '\n') {
            s[--len] = '\0';
            continue;
        }
        break;
    }
}

static void config_clear(SyncState *state) {
    memset(state, 0, sizeof(*state));
    state->storage_pref = STORAGE_PREF_AUTO;
    strncpy(state->usb_root, STORAGE_DEFAULT_ROOT, sizeof(state->usb_root) - 1);
}

static void config_apply_defaults(SyncState *state) {
    strncpy(state->server_url, DEFAULT_SERVER_URL,
            sizeof(state->server_url) - 1);
    state->server_url[sizeof(state->server_url) - 1] = '\0';
    strncpy(state->api_key, DEFAULT_API_KEY, sizeof(state->api_key) - 1);
    state->api_key[sizeof(state->api_key) - 1] = '\0';
    state->use_static_ip = true;
    strncpy(state->static_ip, DEFAULT_STATIC_IP,
            sizeof(state->static_ip) - 1);
    state->static_ip[sizeof(state->static_ip) - 1] = '\0';
    strncpy(state->static_netmask, DEFAULT_NETMASK,
            sizeof(state->static_netmask) - 1);
    state->static_netmask[sizeof(state->static_netmask) - 1] = '\0';
    strncpy(state->static_gateway, DEFAULT_GATEWAY,
            sizeof(state->static_gateway) - 1);
    state->static_gateway[sizeof(state->static_gateway) - 1] = '\0';
    state->mmce_mode[0] = 1;   /* auto */
    state->mmce_mode[1] = 1;   /* auto */
}

static int parse_mmce_mode_value(const char *value) {
    if (!value) return 1;
    if (strcasecmp(value, "off")  == 0) return 0;
    if (strcasecmp(value, "auto") == 0) return 1;
    if (strcasecmp(value, "gen1") == 0) return 2;
    if (strcasecmp(value, "gen2") == 0) return 3;
    return 1;
}

static const char *mmce_mode_to_str(int mode) {
    switch (mode) {
        case 0:  return "off";
        case 2:  return "gen1";
        case 3:  return "gen2";
        default: return "auto";
    }
}

static bool parse_bool_value(const char *value, bool *out) {
    if (!value || !out) return false;
    if (strcasecmp(value, "1") == 0 ||
        strcasecmp(value, "true") == 0 ||
        strcasecmp(value, "yes") == 0 ||
        strcasecmp(value, "on") == 0 ||
        strcasecmp(value, "static") == 0)
    {
        *out = true;
        return true;
    }
    if (strcasecmp(value, "0") == 0 ||
        strcasecmp(value, "false") == 0 ||
        strcasecmp(value, "no") == 0 ||
        strcasecmp(value, "off") == 0 ||
        strcasecmp(value, "dhcp") == 0)
    {
        *out = false;
        return true;
    }
    return false;
}

static bool parse_storage_pref_value(const char *value, StoragePreference *out) {
    if (!value || !out) return false;
    if (strcasecmp(value, "auto") == 0 ||
        strcasecmp(value, "default") == 0)
    {
        *out = STORAGE_PREF_AUTO;
        return true;
    }
    if (strcasecmp(value, "usb") == 0 ||
        strcasecmp(value, "mass") == 0 ||
        strcasecmp(value, "mass0") == 0)
    {
        *out = STORAGE_PREF_USB;
        return true;
    }
    if (strcasecmp(value, "hdd") == 0 ||
        strcasecmp(value, "internal") == 0 ||
        strcasecmp(value, "ata") == 0)
    {
        *out = STORAGE_PREF_HDD;
        return true;
    }
    return false;
}

const char *config_storage_pref_to_str(StoragePreference pref) {
    switch (pref) {
        case STORAGE_PREF_USB:  return "usb";
        case STORAGE_PREF_HDD:  return "hdd";
        case STORAGE_PREF_AUTO:
        default:                return "auto";
    }
}

static void set_err(char *err_out, size_t err_size, const char *fmt, ...) {
    if (!err_out || err_size == 0) return;
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(err_out, err_size, fmt, ap);
    va_end(ap);
}

static int mc_wait_result(void) {
    int cmd = 0;
    int result = 0;
    int rc = mcSync(MC_WAIT, &cmd, &result);
    return rc < 0 ? rc : result;
}

static bool mc_ensure_ready(char *err_out, size_t err_size) {
    if (g_mc_ready) return true;

    scr_printf("  mc_ensure_ready: mcInit...\n");
    int rc = mcInit(MC_TYPE_MC);
    scr_printf("  mc_ensure_ready: mcInit -> %d\n", rc);
    if (rc < 0) {
        set_err(err_out, err_size, "mcInit failed (%d)", rc);
        return false;
    }

    int type = 0;
    int free_clusters = 0;
    int formatted = 0;
    scr_printf("  mc_ensure_ready: mcGetInfo...\n");
    rc = mcGetInfo(MC_PORT, MC_SLOT, &type, &free_clusters, &formatted);
    scr_printf("  mc_ensure_ready: mcGetInfo -> %d\n", rc);
    if (rc < 0) {
        set_err(err_out, err_size, "mcGetInfo failed to start (%d)", rc);
        return false;
    }

    scr_printf("  mc_ensure_ready: mcSync wait...\n");
    int result = mc_wait_result();
    scr_printf("  mc_ensure_ready: mcSync -> %d (type=%d)\n", result, type);
    if (result == -2) {
        set_err(err_out, err_size, "Memory card in slot 1 is not formatted");
        return false;
    }
    if (result < -2 || type == MC_TYPE_NONE) {
        set_err(err_out, err_size,
                "Memory card in slot 1 is not available (type=%d rc=%d)",
                type, result);
        return false;
    }

    g_mc_ready = true;
    return true;
}

static bool mc_try_read_text(const char *path, char *out, size_t out_size) {
    if (!out || out_size == 0) return false;
    out[0] = '\0';

    int rc = mcOpen(MC_PORT, MC_SLOT, path, IOP_O_RDONLY);
    if (rc < 0) return false;
    int fd = mc_wait_result();
    if (fd < 0) return false;

    rc = mcRead(fd, out, (int)out_size - 1);
    int n = (rc < 0) ? rc : mc_wait_result();

    mcClose(fd);
    mc_wait_result();

    if (n < 0) return false;
    out[n] = '\0';
    return true;
}

static bool mc_read_text(const char *path, char *out, size_t out_size) {
    if (mc_try_read_text(path, out, out_size)) return true;
    if (path[0] == '/' && mc_try_read_text(path + 1, out, out_size)) return true;
    return false;
}

static void mc_ensure_config_dir(void) {
    int rc = mcMkDir(MC_PORT, MC_SLOT, MC_APP_DIR);
    if (rc >= 0) mc_wait_result();
    rc = mcMkDir(MC_PORT, MC_SLOT, MC_APP_DIR + 1);
    if (rc >= 0) mc_wait_result();
}

static bool mc_try_write_text(const char *path, const char *text) {
    size_t len = strlen(text);
    if (len >= sizeof(g_mc_io_buf)) return false;
    if (text != g_mc_io_buf) {
        memcpy(g_mc_io_buf, text, len);
    }

    mcDelete(MC_PORT, MC_SLOT, path);
    mc_wait_result();

    int rc = mcOpen(MC_PORT, MC_SLOT, path, IOP_O_WRONLY | IOP_O_CREAT | IOP_O_TRUNC);
    if (rc < 0) return false;
    int fd = mc_wait_result();
    if (fd < 0) return false;

    rc = mcWrite(fd, g_mc_io_buf, (int)len);
    int written = (rc < 0) ? rc : mc_wait_result();

    if (written == (int)len) {
        rc = mcFlush(fd);
        if (rc >= 0) mc_wait_result();
    }

    mcClose(fd);
    mc_wait_result();

    return written == (int)len;
}

static bool mc_write_text(const char *path, const char *text) {
    mc_ensure_config_dir();
    if (mc_try_write_text(path, text)) return true;
    if (path[0] == '/' && mc_try_write_text(path + 1, text)) return true;
    return false;
}

static bool write_template(void) {
    const char *text =
        "# Save Sync PS2 client\n"
        "# Use a dotted LAN IP address; hostnames are not supported yet.\n"
        "server_url=" DEFAULT_SERVER_URL "\n"
        "api_key=" DEFAULT_API_KEY "\n"
        "use_static_ip=true\n"
        "static_ip=" DEFAULT_STATIC_IP "\n"
        "static_netmask=" DEFAULT_NETMASK "\n"
        "static_gateway=" DEFAULT_GATEWAY "\n"
        "storage=auto\n";
    return mc_write_text(MC_CONFIG_FILE, text);
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
        if (line[0] != '\0' && line[0] != '#') {
            char *eq = strchr(line, '=');
            if (eq) {
                *eq = '\0';
                char *key = line;
                char *val = eq + 1;
                trim(key);
                trim(val);

                if (strcmp(key, "server_url") == 0) {
                    strncpy(state->server_url, val,
                            sizeof(state->server_url) - 1);
                    state->server_url[sizeof(state->server_url) - 1] = '\0';
                } else if (strcmp(key, "api_key") == 0) {
                    strncpy(state->api_key, val, sizeof(state->api_key) - 1);
                    state->api_key[sizeof(state->api_key) - 1] = '\0';
                } else if (strcmp(key, "console_id") == 0) {
                    strncpy(state->console_id, val,
                            sizeof(state->console_id) - 1);
                    state->console_id[sizeof(state->console_id) - 1] = '\0';
                } else if (strcmp(key, "use_static_ip") == 0 ||
                           strcmp(key, "static_ip_enabled") == 0 ||
                           strcmp(key, "network") == 0)
                {
                    bool enabled = false;
                    if (parse_bool_value(val, &enabled)) {
                        state->use_static_ip = enabled;
                    }
                } else if (strcmp(key, "dhcp") == 0) {
                    bool enabled = false;
                    if (parse_bool_value(val, &enabled)) {
                        state->use_static_ip = !enabled;
                    }
                } else if (strcmp(key, "static_ip") == 0 ||
                           strcmp(key, "ip") == 0)
                {
                    strncpy(state->static_ip, val,
                            sizeof(state->static_ip) - 1);
                    state->static_ip[sizeof(state->static_ip) - 1] = '\0';
                } else if (strcmp(key, "static_netmask") == 0 ||
                           strcmp(key, "netmask") == 0)
                {
                    strncpy(state->static_netmask, val,
                            sizeof(state->static_netmask) - 1);
                    state->static_netmask[sizeof(state->static_netmask) - 1] = '\0';
                } else if (strcmp(key, "static_gateway") == 0 ||
                           strcmp(key, "gateway") == 0)
                {
                    strncpy(state->static_gateway, val,
                            sizeof(state->static_gateway) - 1);
                    state->static_gateway[sizeof(state->static_gateway) - 1] = '\0';
                } else if (strcmp(key, "storage") == 0 ||
                           strcmp(key, "storage_device") == 0 ||
                           strcmp(key, "rom_storage") == 0)
                {
                    StoragePreference pref = STORAGE_PREF_AUTO;
                    if (parse_storage_pref_value(val, &pref)) {
                        state->storage_pref = pref;
                    }
                } else if (strcmp(key, "gameid_slot1") == 0) {
                    state->mmce_mode[0] = parse_mmce_mode_value(val);
                } else if (strcmp(key, "gameid_slot2") == 0) {
                    state->mmce_mode[1] = parse_mmce_mode_value(val);
                }
            }
        }

        line = next;
    }
}

bool config_load(SyncState *state, char *err_out, size_t err_size) {
    if (!state) return false;
    config_clear(state);
    config_apply_defaults(state);

    if (!mc_ensure_ready(err_out, err_size)) return false;

    if (!mc_read_text(MC_CONFIG_FILE, g_mc_io_buf, sizeof(g_mc_io_buf))) {
        if (!write_template()) {
            set_err(
                err_out, err_size,
                "Config not found at %s.\n"
                "Could not create a default config on the memory card.",
                CONFIG_PATH
            );
            return false;
        }
        return true;
    }

    parse_config_text(state, g_mc_io_buf);

    if (state->server_url[0] == '\0') {
        set_err(err_out, err_size, "server_url not set in %s", CONFIG_PATH);
        return false;
    }
    if (state->api_key[0] == '\0') {
        set_err(err_out, err_size, "api_key not set in %s", CONFIG_PATH);
        return false;
    }
    if (strncmp(state->server_url, "http://", 7) != 0) {
        set_err(err_out, err_size, "server_url must start with http://");
        return false;
    }
    if (strstr(state->server_url + 7, "://")) {
        set_err(err_out, err_size, "server_url must be plain HTTP, not HTTPS");
        return false;
    }

    return true;
}

bool config_save(const SyncState *state) {
    if (!state) return false;

    if (!mc_ensure_ready(NULL, 0)) return false;
    snprintf(g_mc_io_buf, sizeof(g_mc_io_buf),
             "server_url=%s\n"
             "api_key=%s\n"
             "use_static_ip=%s\n"
             "static_ip=%s\n"
             "static_netmask=%s\n"
             "static_gateway=%s\n"
             "storage=%s\n"
             "gameid_slot1=%s\n"
             "gameid_slot2=%s\n"
             "%s%s%s",
             state->server_url,
             state->api_key,
             state->use_static_ip ? "true" : "false",
             state->static_ip,
             state->static_netmask,
             state->static_gateway,
             config_storage_pref_to_str(state->storage_pref),
             mmce_mode_to_str(state->mmce_mode[0]),
             mmce_mode_to_str(state->mmce_mode[1]),
             state->console_id[0] ? "console_id=" : "",
             state->console_id[0] ? state->console_id : "",
             state->console_id[0] ? "\n" : "");
    return mc_write_text(MC_CONFIG_FILE, g_mc_io_buf);
}

void config_load_console_id(SyncState *state) {
    if (!state) return;
    if (state->console_id[0] != '\0') return;

    if (mc_ensure_ready(NULL, 0) &&
        mc_read_text(MC_CONSOLE_ID_FILE, g_mc_io_buf, sizeof(g_mc_io_buf)))
    {
        trim(g_mc_io_buf);
        if (g_mc_io_buf[0] != '\0') {
            strncpy(state->console_id, g_mc_io_buf,
                    sizeof(state->console_id) - 1);
            state->console_id[sizeof(state->console_id) - 1] = '\0';
            return;
        }
    }

    unsigned r = (unsigned)time(NULL) ^ 0xC0FFEE;
    snprintf(state->console_id, sizeof(state->console_id), "ps2_%08x", r);

    snprintf(g_mc_io_buf, sizeof(g_mc_io_buf), "%s\n", state->console_id);
    mc_write_text(MC_CONSOLE_ID_FILE, g_mc_io_buf);
}
