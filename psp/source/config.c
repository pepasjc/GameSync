/*
 * PSP Save Sync - Configuration
 *
 * Config file format (ms0:/PSP/GAME/pspsync/config.txt):
 *   server_url=http://192.168.1.100:8000
 *   api_key=your-secret-key
 *   wifi_ap=0
 */

#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#include <pspiofilemgr.h>
#include <psputils.h>
#include <psprtc.h>

#include "config.h"

static void trim(char *s) {
    int len = strlen(s);
    while (len > 0 && (s[len-1] == '\n' || s[len-1] == '\r' || s[len-1] == ' '))
        s[--len] = '\0';
}

bool config_load(SyncState *state, char *error_buf, size_t error_buf_size) {
    /* Ensure config directory exists */
    sceIoMkdir(SYNC_STATE_DIR, 0777);

    SceUID fd = sceIoOpen(CONFIG_PATH, PSP_O_RDONLY, 0777);
    if (fd < 0) {
        snprintf(error_buf, error_buf_size,
                 "Config not found.\n"
                 "Create:\n%s\n\n"
                 "With:\n"
                 "server_url=http://host:8000\n"
                 "api_key=key\n"
                 "wifi_ap=0",
                 CONFIG_PATH);
        return false;
    }

    char buf[1024];
    int bytes = sceIoRead(fd, buf, sizeof(buf) - 1);
    sceIoClose(fd);

    if (bytes <= 0) {
        snprintf(error_buf, error_buf_size, "Failed to read config");
        return false;
    }
    buf[bytes] = '\0';

    /* Default values */
    state->wifi_ap_index = 0;

    char *line = strtok(buf, "\n");
    while (line) {
        trim(line);
        char *eq = strchr(line, '=');
        if (eq) {
            *eq = '\0';
            char *key = line;
            char *val = eq + 1;
            trim(key);
            trim(val);

            if (strcmp(key, "server_url") == 0)
                strncpy(state->server_url, val, sizeof(state->server_url) - 1);
            else if (strcmp(key, "api_key") == 0)
                strncpy(state->api_key, val, sizeof(state->api_key) - 1);
            else if (strcmp(key, "wifi_ap") == 0)
                state->wifi_ap_index = atoi(val);
        }
        line = strtok(NULL, "\n");
    }

    if (state->server_url[0] == '\0') {
        snprintf(error_buf, error_buf_size, "server_url not set in config");
        return false;
    }
    if (state->api_key[0] == '\0') {
        snprintf(error_buf, error_buf_size, "api_key not set in config");
        return false;
    }

    return true;
}

bool config_save(const SyncState *state) {
    sceIoMkdir(SYNC_STATE_DIR, 0777);
    SceUID fd = sceIoOpen(CONFIG_PATH, PSP_O_WRONLY | PSP_O_CREAT | PSP_O_TRUNC, 0777);
    if (fd < 0) return false;

    char buf[512];
    int len = snprintf(buf, sizeof(buf),
        "server_url=%s\napi_key=%s\nwifi_ap=%d\n",
        state->server_url, state->api_key, state->wifi_ap_index);
    sceIoWrite(fd, buf, len);
    sceIoClose(fd);
    return true;
}

void config_load_console_id(SyncState *state) {
    SceUID fd = sceIoOpen(CONSOLE_ID_FILE, PSP_O_RDONLY, 0777);
    if (fd >= 0) {
        char buf[64];
        int r = sceIoRead(fd, buf, sizeof(buf) - 1);
        sceIoClose(fd);
        if (r > 0) {
            buf[r] = '\0';
            trim(buf);
            if (buf[0] != '\0') {
                strncpy(state->console_id, buf, sizeof(state->console_id) - 1);
                return;
            }
        }
    }

    /* Generate new console ID using PSP UID */
    /* TODO: use sceKernelGetChipId or hardware-based ID for better uniqueness */
    u64 tick;
    sceRtcGetCurrentTick(&tick);
    unsigned int rand_val = (unsigned int)tick ^ 0xDEADBEEF;
    snprintf(state->console_id, sizeof(state->console_id), "psp_%08x", rand_val);

    fd = sceIoOpen(CONSOLE_ID_FILE, PSP_O_WRONLY | PSP_O_CREAT | PSP_O_TRUNC, 0777);
    if (fd >= 0) {
        sceIoWrite(fd, state->console_id, strlen(state->console_id));
        sceIoClose(fd);
    }
}

/* Simple key-value store for per-game sync state.
 * Format: "GAMEID=hash64hex\n" (up to MAX_TITLES entries)
 */
bool config_get_last_hash(const char *game_id, char *hash_out) {
    SceUID fd = sceIoOpen(STATE_FILE, PSP_O_RDONLY, 0777);
    if (fd < 0) return false;

    char buf[MAX_TITLES * 80];
    int bytes = sceIoRead(fd, buf, sizeof(buf) - 1);
    sceIoClose(fd);
    if (bytes <= 0) return false;
    buf[bytes] = '\0';

    char prefix[GAME_ID_LEN + 2];
    snprintf(prefix, sizeof(prefix), "%s=", game_id);
    int prefix_len = strlen(prefix);

    char *line = strtok(buf, "\n");
    while (line) {
        if (strncmp(line, prefix, prefix_len) == 0) {
            strncpy(hash_out, line + prefix_len, 64);
            hash_out[64] = '\0';
            return true;
        }
        line = strtok(NULL, "\n");
    }
    return false;
}

bool config_set_last_hash(const char *game_id, const char *hash_hex) {
    /* Read existing state */
    char buf[MAX_TITLES * 80] = "";
    SceUID fd = sceIoOpen(STATE_FILE, PSP_O_RDONLY, 0777);
    if (fd >= 0) {
        int bytes = sceIoRead(fd, buf, sizeof(buf) - 1);
        sceIoClose(fd);
        if (bytes > 0) buf[bytes] = '\0';
    }

    /* Build new state: keep all lines except the one for game_id */
    char new_buf[MAX_TITLES * 80] = "";
    char prefix[GAME_ID_LEN + 2];
    snprintf(prefix, sizeof(prefix), "%s=", game_id);
    int prefix_len = strlen(prefix);

    char *line = strtok(buf, "\n");
    while (line) {
        if (line[0] != '\0' && strncmp(line, prefix, prefix_len) != 0) {
            strcat(new_buf, line);
            strcat(new_buf, "\n");
        }
        line = strtok(NULL, "\n");
    }

    /* Append updated entry */
    char entry[GAME_ID_LEN + 2 + 65];
    snprintf(entry, sizeof(entry), "%s=%s\n", game_id, hash_hex);
    strcat(new_buf, entry);

    /* Write back */
    fd = sceIoOpen(STATE_FILE, PSP_O_WRONLY | PSP_O_CREAT | PSP_O_TRUNC, 0777);
    if (fd < 0) return false;
    sceIoWrite(fd, new_buf, strlen(new_buf));
    sceIoClose(fd);
    return true;
}
