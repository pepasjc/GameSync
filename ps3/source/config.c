#include "config.h"

#include "debug.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static void trim(char *s) {
    size_t len = strlen(s);
    while (len > 0) {
        char c = s[len - 1];
        if (c == '\n' || c == '\r' || c == ' ' || c == '\t') {
            s[--len] = '\0';
            continue;
        }
        break;
    }
}

static void config_apply_defaults(SyncState *state) {
    memset(state, 0, sizeof(*state));
    strncpy(state->server_url, "http://192.168.1.201:8000", sizeof(state->server_url) - 1);
    strncpy(state->api_key, "anything", sizeof(state->api_key) - 1);
    strncpy(state->ps3_user, "00000001", sizeof(state->ps3_user) - 1);
    state->scan_ps3 = true;
    state->scan_ps1 = true;
}

bool config_load(
    SyncState *state,
    bool *created_out,
    char *error_buf,
    size_t error_buf_size
) {
    FILE *fp = fopen(CONFIG_PATH, "rb");
    if (created_out) {
        *created_out = false;
    }

    if (!fp) {
        config_apply_defaults(state);
        if (config_save(state)) {
            if (created_out) {
                *created_out = true;
            }
            snprintf(
                error_buf,
                error_buf_size,
                "Config was missing. Created debug config at %s.",
                CONFIG_PATH
            );
            debug_log("Created debug config at %s", CONFIG_PATH);
        } else {
            snprintf(
                error_buf,
                error_buf_size,
                "Config missing at %s. Using in-memory debug defaults.",
                CONFIG_PATH
            );
            debug_log("Using in-memory debug defaults because %s could not be written", CONFIG_PATH);
        }
        return true;
    }

    config_apply_defaults(state);

    char line[512];
    while (fgets(line, sizeof(line), fp) != NULL) {
        trim(line);
        if (line[0] == '\0' || line[0] == '#') {
            continue;
        }
        char *eq = strchr(line, '=');
        if (!eq) {
            continue;
        }
        *eq = '\0';
        char *key = line;
        char *value = eq + 1;
        trim(key);
        trim(value);

        if (strcmp(key, "server_url") == 0) {
            strncpy(state->server_url, value, sizeof(state->server_url) - 1);
        } else if (strcmp(key, "api_key") == 0) {
            strncpy(state->api_key, value, sizeof(state->api_key) - 1);
        } else if (strcmp(key, "ps3_user") == 0) {
            strncpy(state->ps3_user, value, sizeof(state->ps3_user) - 1);
        } else if (strcmp(key, "scan_ps3") == 0) {
            state->scan_ps3 = atoi(value) != 0;
        } else if (strcmp(key, "scan_ps1") == 0) {
            state->scan_ps1 = atoi(value) != 0;
        }
    }
    fclose(fp);

    if (state->server_url[0] == '\0') {
        snprintf(error_buf, error_buf_size, "server_url not set in %s", CONFIG_PATH);
        return false;
    }
    if (state->api_key[0] == '\0') {
        snprintf(error_buf, error_buf_size, "api_key not set in %s", CONFIG_PATH);
        return false;
    }
    if (state->ps3_user[0] == '\0') {
        snprintf(error_buf, error_buf_size, "ps3_user not set in %s", CONFIG_PATH);
        return false;
    }

    snprintf(error_buf, error_buf_size, "Loaded config from %s", CONFIG_PATH);
    return true;
}

bool config_save(const SyncState *state) {
    FILE *fp = fopen(CONFIG_PATH, "wb");
    if (!fp) {
        return false;
    }
    fprintf(fp, "server_url=%s\n", state->server_url);
    fprintf(fp, "api_key=%s\n", state->api_key);
    fprintf(fp, "ps3_user=%s\n", state->ps3_user);
    fprintf(fp, "scan_ps3=%d\n", state->scan_ps3 ? 1 : 0);
    fprintf(fp, "scan_ps1=%d\n", state->scan_ps1 ? 1 : 0);
    fclose(fp);
    return true;
}

void config_load_console_id(SyncState *state) {
    snprintf(
        state->console_id,
        sizeof(state->console_id),
        "ps3_%s",
        state->ps3_user[0] ? state->ps3_user : "00000001"
    );
}
