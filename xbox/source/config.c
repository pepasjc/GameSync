// Config file parser/writer for the Xbox client.

#include "config.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#include <hal/debug.h>
#include <windows.h>

// Use a directory under E:\UDATA - the root data partition's only
// guaranteed-writable subtree on a stock FATX layout. Picking a fake Xbox
// Title ID prefix "TDSV" (= "TitleData SaveSync") keeps us from colliding
// with real game saves. The xbox enumerator skips this entry because it
// only matches strict 8-hex names, which "TDSV0000" is not.
//
// (Earlier we tried E:\Apps\SaveSync but mkdir at E: root returns
//  ERROR_PATH_NOT_FOUND on at least xemu's default HDD image - the FATX
//  driver only advertises pre-existing top-level directories.)
static const char *CONFIG_DIR  = "E:\\UDATA\\TDSV0000";
static const char *CONFIG_PATH = "E:\\UDATA\\TDSV0000\\config.txt";

static const char *DEFAULT_SERVER = "http://192.168.1.201:8000";
static const char *DEFAULT_API_KEY = "anything";

// Best-effort directory creation. Idempotent: returns 0 even if dir exists.
static int ensure_dir_one(const char *path)
{
    if (CreateDirectoryA(path, NULL)) return 0;
    DWORD err = GetLastError();
    if (err == ERROR_ALREADY_EXISTS) return 0;
    debugPrint("cfg: mkdir %s err=%lu\n", path, (unsigned long)err);
    return -1;
}

// Walk the path component by component and create each parent directory
// if missing. Win32 CreateDirectoryA only creates one level at a time, so
// "E:\Apps\SaveSync" fails outright when "E:\Apps" doesn't exist yet.
static int ensure_dir(const char *path)
{
    char tmp[260];
    int  len = (int)strlen(path);
    if (len <= 0 || len >= (int)sizeof(tmp)) return -1;
    memcpy(tmp, path, len + 1);

    // Skip the drive letter prefix ("E:\") so we don't try to create "E:".
    int start = 0;
    if (len >= 3 && tmp[1] == ':' && (tmp[2] == '\\' || tmp[2] == '/')) {
        start = 3;
    }

    for (int i = start; i <= len; i++) {
        if (tmp[i] == '\\' || tmp[i] == '/' || tmp[i] == '\0') {
            char saved = tmp[i];
            tmp[i] = '\0';
            if (i > start) {
                if (ensure_dir_one(tmp) != 0) {
                    return -1;
                }
            }
            tmp[i] = saved;
            if (saved == '\0') break;
        }
    }
    return 0;
}

// Pick a stable-ish console ID. Xbox doesn't expose the real serial easily
// from user mode, so we synthesize one based on uptime + process tick on
// first run and persist it. Caller writes the result back via config_save.
static void mint_console_id(char *out, int out_len)
{
    snprintf(out, out_len, "XBOX-%08X-%08X",
             (unsigned)GetTickCount(),
             (unsigned)((uintptr_t)out & 0xFFFFFFFFu));
}

// Write a fresh default config so the user has a template to edit.
static int write_default(const XboxConfig *seed)
{
    int dir_rc = ensure_dir(CONFIG_DIR);
    if (dir_rc != 0) {
        debugPrint("cfg: ensure_dir(%s) failed\n", CONFIG_DIR);
    }

    HANDLE h = CreateFileA(CONFIG_PATH, GENERIC_WRITE, 0, NULL,
                           CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);
    if (h == INVALID_HANDLE_VALUE) {
        debugPrint("cfg: CreateFile(%s) err=%lu\n",
                   CONFIG_PATH, (unsigned long)GetLastError());
        return -1;
    }

    char buf[1024];
    int n = snprintf(buf, sizeof(buf),
        "# Save Sync - Xbox client configuration\r\n"
        "# Edit server_url and api_key, then re-launch the app.\r\n"
        "# Network: auto uses the Xbox dashboard config; dhcp forces DHCP.\r\n"
        "# If DHCP times out, use network_mode=static and fill the static_* values.\r\n"
        "\r\n"
        "server_url=%s\r\n"
        "api_key=%s\r\n"
        "console_id=%s\r\n"
        "network_mode=%s\r\n"
        "static_ip=\r\n"
        "static_netmask=255.255.255.0\r\n"
        "static_gateway=\r\n"
        "static_dns1=\r\n"
        "static_dns2=\r\n",
        seed->server_url[0] ? seed->server_url : DEFAULT_SERVER,
        seed->api_key[0]    ? seed->api_key    : DEFAULT_API_KEY,
        seed->console_id,
        seed->network_mode[0] ? seed->network_mode : "auto");

    DWORD written = 0;
    BOOL ok = WriteFile(h, buf, (DWORD)n, &written, NULL);
    CloseHandle(h);
    return (ok && written == (DWORD)n) ? 0 : -1;
}

// Strip trailing CR/LF/whitespace.
static void rstrip(char *s)
{
    int len = (int)strlen(s);
    while (len > 0) {
        char c = s[len - 1];
        if (c == ' ' || c == '\t' || c == '\r' || c == '\n') {
            s[--len] = '\0';
        } else {
            break;
        }
    }
}

int config_load(XboxConfig *cfg, char *err, int err_len)
{
    if (!cfg) return -2;

    memset(cfg, 0, sizeof(*cfg));

    // Read whole file via Win32 (avoids fopen text-mode \r\n quirks).
    HANDLE h = CreateFileA(CONFIG_PATH, GENERIC_READ, FILE_SHARE_READ, NULL,
                           OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, NULL);
    if (h == INVALID_HANDLE_VALUE) {
        // Synthesize a console_id once and write a stub config.
        mint_console_id(cfg->console_id, sizeof(cfg->console_id));
        if (write_default(cfg) != 0) {
            if (err) snprintf(err, err_len,
                              "Could not create config at %s", CONFIG_PATH);
            return -2;
        }
        // Populate with defaults for the in-memory copy. The defaults
        // already match the typical local-LAN deployment (server on
        // 192.168.1.201, api_key="anything"), so we proceed straight away
        // without forcing the user to relaunch.
        snprintf(cfg->server_url, sizeof(cfg->server_url), "%s", DEFAULT_SERVER);
        snprintf(cfg->api_key,    sizeof(cfg->api_key),    "%s", DEFAULT_API_KEY);
        if (err) snprintf(err, err_len,
                          "Default config written to %s.", CONFIG_PATH);
        return 0;
    }

    char buf[2048];
    DWORD got = 0;
    BOOL ok = ReadFile(h, buf, sizeof(buf) - 1, &got, NULL);
    CloseHandle(h);
    if (!ok) {
        if (err) snprintf(err, err_len, "Read failed on %s", CONFIG_PATH);
        return -2;
    }
    buf[got] = '\0';

    // Parse `key=value` lines. PDCLib in nxdk lacks strtok_r, so walk the
    // buffer manually.
    int has_url = 0, has_key = 0, has_cid = 0;
    snprintf(cfg->network_mode, sizeof(cfg->network_mode), "auto");
    char *line = buf;
    while (*line) {
        char *nl = strchr(line, '\n');
        if (nl) *nl = '\0';

        rstrip(line);
        if (line[0] && line[0] != '#') {
            char *eq = strchr(line, '=');
            if (eq) {
                *eq = '\0';
                const char *k = line;
                const char *v = eq + 1;
                if (strcmp(k, "server_url") == 0) {
                    snprintf(cfg->server_url, sizeof(cfg->server_url), "%s", v);
                    has_url = 1;
                } else if (strcmp(k, "api_key") == 0) {
                    snprintf(cfg->api_key, sizeof(cfg->api_key), "%s", v);
                    has_key = 1;
                } else if (strcmp(k, "console_id") == 0) {
                    snprintf(cfg->console_id, sizeof(cfg->console_id), "%s", v);
                    has_cid = 1;
                } else if (strcmp(k, "network_mode") == 0) {
                    snprintf(cfg->network_mode, sizeof(cfg->network_mode), "%s", v);
                } else if (strcmp(k, "static_ip") == 0) {
                    snprintf(cfg->static_ip, sizeof(cfg->static_ip), "%s", v);
                } else if (strcmp(k, "static_netmask") == 0) {
                    snprintf(cfg->static_netmask, sizeof(cfg->static_netmask), "%s", v);
                } else if (strcmp(k, "static_gateway") == 0) {
                    snprintf(cfg->static_gateway, sizeof(cfg->static_gateway), "%s", v);
                } else if (strcmp(k, "static_dns1") == 0) {
                    snprintf(cfg->static_dns1, sizeof(cfg->static_dns1), "%s", v);
                } else if (strcmp(k, "static_dns2") == 0) {
                    snprintf(cfg->static_dns2, sizeof(cfg->static_dns2), "%s", v);
                }
            }
        }
        if (!nl) break;
        line = nl + 1;
    }

    // If console_id missing (older config), mint + persist.
    if (!has_cid || cfg->console_id[0] == '\0') {
        mint_console_id(cfg->console_id, sizeof(cfg->console_id));
        config_save(cfg);
    }

    if (!has_url || !has_key) {
        if (err) snprintf(err, err_len,
                          "Config missing server_url or api_key");
        return -2;
    }
    return 0;
}

int config_save(const XboxConfig *cfg)
{
    if (!cfg) return -1;
    ensure_dir(CONFIG_DIR);

    HANDLE h = CreateFileA(CONFIG_PATH, GENERIC_WRITE, 0, NULL,
                           CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);
    if (h == INVALID_HANDLE_VALUE) return -1;

    char buf[1024];
    int n = snprintf(buf, sizeof(buf),
        "# Save Sync - Xbox client configuration\r\n"
        "# network_mode: auto, dhcp, or static\r\n"
        "server_url=%s\r\n"
        "api_key=%s\r\n"
        "console_id=%s\r\n"
        "network_mode=%s\r\n"
        "static_ip=%s\r\n"
        "static_netmask=%s\r\n"
        "static_gateway=%s\r\n"
        "static_dns1=%s\r\n"
        "static_dns2=%s\r\n",
        cfg->server_url, cfg->api_key, cfg->console_id,
        cfg->network_mode[0] ? cfg->network_mode : "auto",
        cfg->static_ip,
        cfg->static_netmask,
        cfg->static_gateway,
        cfg->static_dns1,
        cfg->static_dns2);

    DWORD written = 0;
    BOOL ok = WriteFile(h, buf, (DWORD)n, &written, NULL);
    CloseHandle(h);
    return (ok && written == (DWORD)n) ? 0 : -1;
}
