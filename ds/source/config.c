#include "config.h"
#include "ui.h"
#include "views.h"
#include <stdio.h>
#include <string.h>
#include <fat.h>
#include <sys/stat.h>
#include <sys/types.h>

// Try to create directory if it doesn't exist
static void ensure_directory(const char *path) {
    mkdir(path, 0777);
}

static const char *config_paths[] = {
    "sd:/dssync/config.txt",
    "fat:/dssync/config.txt",
    "/dssync/config.txt",
    "sdmc:/dssync/config.txt",
    NULL
};

// Folder of the config file in use, e.g. "sd:/dssync" (the catalog cache
// lives beside it)
static char loaded_dir[64] = "";

const char *config_dir(void) {
    return loaded_dir[0] ? loaded_dir : "sd:/dssync";
}

// Create default config file
static bool create_default_config(const char *path) {
    // Extract directory from path
    char dir[256];
    strncpy(dir, path, sizeof(dir) - 1);
    char *last_slash = strrchr(dir, '/');
    if (last_slash) {
        *last_slash = '\0';
        ensure_directory(dir);
    }
    
    FILE *f = fopen(path, "w");
    if (!f) return false;
    
    fprintf(f, "# NDS/3DS Save Sync Configuration\n");
    fprintf(f, "# Edit the values below with your server details\n\n");
    fprintf(f, "server_url=http://192.168.1.100:8000\n");
    fprintf(f, "api_key=change-this-to-your-api-key\n\n");
    fprintf(f, "# WiFi Configuration (for Nintendo DS/DS Lite)\n");
    fprintf(f, "# Leave blank to skip WiFi or use DSi firmware settings\n");
    fprintf(f, "wifi_ssid=\n");
    fprintf(f, "wifi_wep_key=\n\n");
    fprintf(f, "# Optional: Custom save directory to scan (in addition to defaults)\n");
    fprintf(f, "# Examples: /data/saves, sd:/nds/saves, fat:/saves\n");
    fprintf(f, "#save_dir=/your/custom/path\n");
    
    fclose(f);
    return true;
}

// Try to load config from multiple possible paths
bool config_load(SyncState *state, char *error, size_t error_size) {
    FILE *f = NULL;
    const char *loaded_path = NULL;
    const char *tried_create = NULL;
    
    // Try each path
    for (int i = 0; config_paths[i]; i++) {
        f = fopen(config_paths[i], "r");
        if (f) {
            loaded_path = config_paths[i];
            break;
        }
    }
    
    // If no config found, try to create one
    if (!f) {
        for (int i = 0; config_paths[i]; i++) {
            if (create_default_config(config_paths[i])) {
                tried_create = config_paths[i];
                f = fopen(config_paths[i], "r");
                if (f) {
                    loaded_path = config_paths[i];
                    break;
                }
            }
        }
    }
    
    if (!f) {
        snprintf(error, error_size,
            "Could not create config file.\n\n"
            "Please create manually at:\n"
            "sd:/dssync/config.txt\n\n"
            "With contents:\n"
            "server_url=http://<ip>:8000\n"
            "api_key=<your-key>");
        return false;
    }
    
    // Parse config file
    char line[512];
    bool has_url = false;
    bool has_key = false;
    bool is_default = false;
    
    // Initialize fields
    state->custom_save_dir[0] = '\0';
    state->wifi_ssid[0] = '\0';
    state->wifi_wep_key[0] = '\0';
    state->tcp_window = 0;
    
    while (fgets(line, sizeof(line), f)) {
        // Remove trailing newline and carriage return
        char *newline = strchr(line, '\n');
        if (newline) *newline = '\0';
        char *cr = strchr(line, '\r');
        if (cr) *cr = '\0';
        
        // Skip comments and empty lines
        if (line[0] == '#' || line[0] == '\0') continue;
        
        // Parse key=value
        char *equals = strchr(line, '=');
        if (!equals) continue;
        
        *equals = '\0';
        char *key = line;
        char *value = equals + 1;
        
        // Trim trailing whitespace from value
        char *end = value + strlen(value) - 1;
        while (end > value && (*end == ' ' || *end == '\t' || *end == '\r' || *end == '\n')) {
            *end = '\0';
            end--;
        }
        
        if (strcmp(key, "server_url") == 0) {
            strncpy(state->server_url, value, sizeof(state->server_url) - 1);
            has_url = true;
            if (strstr(value, "192.168.1.100")) {
                is_default = true;
            }
        } else if (strcmp(key, "api_key") == 0) {
            strncpy(state->api_key, value, sizeof(state->api_key) - 1);
            has_key = true;
            if (strstr(value, "change-this")) {
                is_default = true;
            }
        } else if (strcmp(key, "save_dir") == 0) {
            strncpy(state->custom_save_dir, value, sizeof(state->custom_save_dir) - 1);
        } else if (strcmp(key, "wifi_ssid") == 0) {
            strncpy(state->wifi_ssid, value, sizeof(state->wifi_ssid) - 1);
        } else if (strcmp(key, "wifi_wep_key") == 0) {
            strncpy(state->wifi_wep_key, value, sizeof(state->wifi_wep_key) - 1);
        } else if (strcmp(key, "tcp_window") == 0) {
            state->tcp_window = atoi(value);
        }
    }
    
    fclose(f);

    snprintf(loaded_dir, sizeof(loaded_dir), "%s", loaded_path);
    char *dir_end = strrchr(loaded_dir, '/');
    if (dir_end && dir_end > loaded_dir) *dir_end = '\0';
    else loaded_dir[0] = '\0';
    
    if (!has_url || !has_key) {
        snprintf(error, error_size, "Config missing server_url or api_key");
        return false;
    }
    
    if (is_default) {
        snprintf(error, error_size,
            "Config created at:\n%s\n\n"
            "Please edit it with your\n"
            "server IP and API key.",
            tried_create ? tried_create : loaded_path);
        return false;
    }
    
    // Generate console ID
    state->console_id = 0x4E445300;  // "NDS\0"
    
    return true;
}

// Save config to first available path
bool config_save(const SyncState *state) {
    const char *path = NULL;
    FILE *f = NULL;

    // Prefer an existing config path
    for (int i = 0; config_paths[i]; i++) {
        f = fopen(config_paths[i], "r");
        if (f) {
            fclose(f);
            path = config_paths[i];
            break;
        }
    }

    // Otherwise use the first path
    if (!path) {
        path = config_paths[0];
    }

    // Ensure directory exists
    char dir[256];
    strncpy(dir, path, sizeof(dir) - 1);
    char *last_slash = strrchr(dir, '/');
    if (last_slash) {
        *last_slash = '\0';
        ensure_directory(dir);
    }

    f = fopen(path, "w");
    if (!f) return false;

    fprintf(f, "# NDS/3DS Save Sync Configuration\n");
    fprintf(f, "server_url=%s\n", state->server_url);
    fprintf(f, "api_key=%s\n", state->api_key);
    if (state->custom_save_dir[0])
        fprintf(f, "save_dir=%s\n", state->custom_save_dir);
    fprintf(f, "wifi_ssid=%s\n", state->wifi_ssid);
    fprintf(f, "wifi_wep_key=%s\n", state->wifi_wep_key);
    if (state->tcp_window > 0)
        fprintf(f, "tcp_window=%d\n", state->tcp_window);

    fclose(f);
    return true;
}

// Characters available in the D-pad text editor
static const char charset[] = "abcdefghijklmnopqrstuvwxyz0123456789.:/-_ABCDEFGHIJKLMNOPQRSTUVWXYZ@?=&#%+! ";
static const int charset_len = sizeof(charset) - 1;

// Find character index in charset, or 0 if not found
static int charset_index(char c) {
    for (int i = 0; i < charset_len; i++) {
        if (charset[i] == c) return i;
    }
    return 0;
}

bool config_edit_field(const char *hint, char *buffer, int max_len) {
    char temp[256];
    strncpy(temp, buffer, sizeof(temp) - 1);
    temp[sizeof(temp) - 1] = '\0';

    int cursor = strlen(temp);
    int len = cursor;
    bool running = true;
    bool confirmed = false;

    while (running && pmMainLoop()) {
        // Draw editor screen
        view_editor(&ui_bottom, hint, temp, len, cursor, charset, charset_len);
        ui_present(&ui_bottom);

        // Wait for input
        while (pmMainLoop()) {
            swiWaitForVBlank();
            scanKeys();
            // D-pad and Delete repeat while held
            int kDown = keysDown() | (keysDownRepeat() & (KEY_UP | KEY_DOWN | KEY_LEFT | KEY_RIGHT | KEY_X));

            if (!kDown) continue;

            if (kDown & KEY_LEFT) {
                if (cursor > 0) cursor--;
                break;
            }
            if (kDown & KEY_RIGHT) {
                if (cursor < len) cursor++;
                break;
            }
            if (kDown & KEY_UP) {
                if (cursor < len) {
                    int idx = (charset_index(temp[cursor]) + 1) % charset_len;
                    temp[cursor] = charset[idx];
                } else if (len < max_len - 1) {
                    temp[len] = 'a';
                    len++;
                    temp[len] = '\0';
                }
                break;
            }
            if (kDown & KEY_DOWN) {
                if (cursor < len) {
                    int idx = (charset_index(temp[cursor]) - 1 + charset_len) % charset_len;
                    temp[cursor] = charset[idx];
                } else if (len < max_len - 1) {
                    temp[len] = 'a';
                    len++;
                    temp[len] = '\0';
                }
                break;
            }
            // Y inserts a letter at the cursor, X deletes the one before it
            if (kDown & KEY_Y) {
                if (len < max_len - 1) {
                    memmove(&temp[cursor + 1], &temp[cursor], len - cursor + 1);
                    temp[cursor] = 'a';
                    len++;
                    cursor++;
                }
                break;
            }
            if (kDown & KEY_X) {
                if (cursor > 0) {
                    memmove(&temp[cursor - 1], &temp[cursor], len - cursor + 1);
                    cursor--;
                    len--;
                }
                break;
            }
            // A saves, B cancels
            if (kDown & KEY_A) {
                confirmed = true;
                running = false;
                break;
            }
            if (kDown & KEY_B) {
                running = false;
                break;
            }
        }
    }

    if (confirmed) {
        strncpy(buffer, temp, max_len - 1);
        buffer[max_len - 1] = '\0';
        return true;
    }
    return false;
}
