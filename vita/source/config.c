/*
 * Vita Save Sync - Configuration
 *
 * Config file at ux0:data/vitasync/config.txt:
 *   server_url=http://192.168.1.100:8000
 *   api_key=your-secret-key
 *   scan_vita=1
 *   scan_psp_emu=1
 */

#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#include <psp2/io/fcntl.h>
#include <psp2/io/stat.h>
#include <psp2/rtc.h>

#include "config.h"

static void trim(char *s) {
    int len = strlen(s);
    while (len > 0 && (s[len-1] == '\n' || s[len-1] == '\r' || s[len-1] == ' '))
        s[--len] = '\0';
}

bool config_load(SyncState *state, char *error_buf, size_t error_buf_len) {
    sceIoMkdir(SYNC_DATA_DIR, 0777);

    SceUID fd = sceIoOpen(CONFIG_PATH, SCE_O_RDONLY, 0777);
    if (fd < 0) {
        snprintf(error_buf, error_buf_len,
                 "Config not found.\n"
                 "Create %s\n\n"
                 "server_url=http://host:8000\n"
                 "api_key=key",
                 CONFIG_PATH);
        return false;
    }

    char buf[1024];
    int bytes = sceIoRead(fd, buf, sizeof(buf) - 1);
    sceIoClose(fd);

    if (bytes <= 0) {
        snprintf(error_buf, error_buf_len, "Failed to read config");
        return false;
    }
    buf[bytes] = '\0';

    /* Defaults */
    state->scan_vita_saves    = true;
    state->scan_psp_emu_saves = true;

    char *line = strtok(buf, "\n");
    while (line) {
        trim(line);
        char *eq = strchr(line, '=');
        if (eq) {
            *eq = '\0';
            char *key = line, *val = eq + 1;
            trim(key); trim(val);
            if (strcmp(key, "server_url") == 0)
                strncpy(state->server_url, val, sizeof(state->server_url) - 1);
            else if (strcmp(key, "api_key") == 0)
                strncpy(state->api_key, val, sizeof(state->api_key) - 1);
            else if (strcmp(key, "scan_vita") == 0)
                state->scan_vita_saves = atoi(val) != 0;
            else if (strcmp(key, "scan_psp_emu") == 0)
                state->scan_psp_emu_saves = atoi(val) != 0;
        }
        line = strtok(NULL, "\n");
    }

    if (state->server_url[0] == '\0') {
        snprintf(error_buf, error_buf_len, "server_url not set in config");
        return false;
    }
    if (state->api_key[0] == '\0') {
        snprintf(error_buf, error_buf_len, "api_key not set in config");
        return false;
    }
    return true;
}

bool config_save(const SyncState *state) {
    sceIoMkdir(SYNC_DATA_DIR, 0777);
    SceUID fd = sceIoOpen(CONFIG_PATH, SCE_O_WRONLY | SCE_O_CREAT | SCE_O_TRUNC, 0777);
    if (fd < 0) return false;

    char buf[512];
    int len = snprintf(buf, sizeof(buf),
        "server_url=%s\napi_key=%s\nscan_vita=%d\nscan_psp_emu=%d\n",
        state->server_url, state->api_key,
        (int)state->scan_vita_saves, (int)state->scan_psp_emu_saves);
    sceIoWrite(fd, buf, len);
    sceIoClose(fd);
    return true;
}

void config_load_console_id(SyncState *state) {
    SceUID fd = sceIoOpen(CONSOLE_ID_FILE, SCE_O_RDONLY, 0777);
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

    /* Generate console ID using RTC tick */
    SceRtcTick tick;
    sceRtcGetCurrentTick(&tick);
    snprintf(state->console_id, sizeof(state->console_id),
             "vita_%08x", (unsigned int)(tick.tick & 0xFFFFFFFF));

    fd = sceIoOpen(CONSOLE_ID_FILE, SCE_O_WRONLY | SCE_O_CREAT | SCE_O_TRUNC, 0777);
    if (fd >= 0) {
        sceIoWrite(fd, state->console_id, strlen(state->console_id));
        sceIoClose(fd);
    }
}

bool config_get_last_hash(const char *game_id, char *hash_out) {
    SceUID fd = sceIoOpen(STATE_FILE, SCE_O_RDONLY, 0777);
    if (fd < 0) return false;

    static char buf[MAX_TITLES * 128];
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
    static char buf[MAX_TITLES * 128];
    buf[0] = '\0';
    SceUID fd = sceIoOpen(STATE_FILE, SCE_O_RDONLY, 0777);
    if (fd >= 0) {
        int bytes = sceIoRead(fd, buf, sizeof(buf) - 1);
        sceIoClose(fd);
        if (bytes > 0) buf[bytes] = '\0';
    }

    static char new_buf[MAX_TITLES * 128];
    new_buf[0] = '\0';
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

    char entry[GAME_ID_LEN + 2 + 65];
    snprintf(entry, sizeof(entry), "%s=%s\n", game_id, hash_hex);
    strcat(new_buf, entry);

    fd = sceIoOpen(STATE_FILE, SCE_O_WRONLY | SCE_O_CREAT | SCE_O_TRUNC, 0777);
    if (fd < 0) return false;
    sceIoWrite(fd, new_buf, strlen(new_buf));
    sceIoClose(fd);
    return true;
}
