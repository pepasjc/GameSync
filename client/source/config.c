#include "config.h"
#include <sys/stat.h>

// Generate a random 16-char hex ID using 3DS random number generator
static void generate_console_id(char *out) {
    u8 random[8];
    // Use PS service for random data
    PS_GenerateRandomBytes(random, 8);

    for (int i = 0; i < 8; i++) {
        snprintf(out + (i * 2), 3, "%02X", random[i]);
    }
    out[16] = '\0';
}

// Load or generate console ID
static void load_or_generate_console_id(AppConfig *config) {
    FILE *f = fopen(CONSOLE_ID_PATH, "r");
    if (f) {
        // Read existing ID
        if (fgets(config->console_id, sizeof(config->console_id), f)) {
            // Trim any whitespace
            char *end = config->console_id + strlen(config->console_id) - 1;
            while (end > config->console_id && (*end == '\r' || *end == '\n' || *end == ' '))
                *end-- = '\0';
        }
        fclose(f);

        // Validate it's 16 hex chars
        if (strlen(config->console_id) == 16) {
            return;  // Valid ID loaded
        }
    }

    // Generate new ID
    generate_console_id(config->console_id);

    // Ensure directory exists
    mkdir("sdmc:/3ds", 0777);
    mkdir("sdmc:/3ds/3dssync", 0777);

    // Save it
    f = fopen(CONSOLE_ID_PATH, "w");
    if (f) {
        fprintf(f, "%s\n", config->console_id);
        fclose(f);
    }
}

// Trim leading/trailing whitespace in-place
static void trim(char *str) {
    // Leading
    char *start = str;
    while (*start == ' ' || *start == '\t') start++;
    if (start != str) memmove(str, start, strlen(start) + 1);

    // Trailing
    char *end = str + strlen(str) - 1;
    while (end > str && (*end == ' ' || *end == '\t' || *end == '\r' || *end == '\n'))
        *end-- = '\0';
}

// Skip UTF-8 BOM if present at start of file
static void skip_bom(char *str) {
    if ((unsigned char)str[0] == 0xEF &&
        (unsigned char)str[1] == 0xBB &&
        (unsigned char)str[2] == 0xBF) {
        memmove(str, str + 3, strlen(str + 3) + 1);
    }
}

bool config_load(AppConfig *config, char *error_out, int error_size) {
    memset(config, 0, sizeof(AppConfig));

    FILE *f = fopen(CONFIG_PATH, "r");
    if (!f) {
        snprintf(error_out, error_size,
            "Could not open config file:\n  %s\n\n"
            "Make sure the file exists on your SD card.",
            CONFIG_PATH);
        return false;
    }

    char line[512];
    bool first_line = true;
    while (fgets(line, sizeof(line), f)) {
        if (first_line) {
            skip_bom(line);
            first_line = false;
        }

        // Skip comments and blank lines
        if (line[0] == '#' || line[0] == '\n' || line[0] == '\r')
            continue;

        char *eq = strchr(line, '=');
        if (!eq) continue;

        *eq = '\0';
        char *key = line;
        char *val = eq + 1;
        trim(key);
        trim(val);

        if (strcmp(key, "server_url") == 0) {
            strncpy(config->server_url, val, MAX_URL_LEN - 1);
        } else if (strcmp(key, "api_key") == 0) {
            strncpy(config->api_key, val, MAX_API_KEY_LEN - 1);
        } else if (strcmp(key, "nds_dir") == 0) {
            strncpy(config->nds_dir, val, MAX_PATH_LEN - 1);
        }
    }

    fclose(f);

    // Validate required fields with specific error
    if (config->server_url[0] == '\0' && config->api_key[0] == '\0') {
        snprintf(error_out, error_size,
            "Config file found but no valid keys.\n\n"
            "Expected format:\n"
            "  server_url=http://<ip>:8000\n"
            "  api_key=<your-key>");
        return false;
    }
    if (config->server_url[0] == '\0') {
        snprintf(error_out, error_size, "Config missing 'server_url' field.");
        return false;
    }
    if (config->api_key[0] == '\0') {
        snprintf(error_out, error_size, "Config missing 'api_key' field.");
        return false;
    }

    // Load or generate console ID
    load_or_generate_console_id(config);

    return true;
}

bool config_get_cached_hash(const char *title_id_hex, int file_count, u32 total_size,
                            char *hash_out) {
    FILE *f = fopen(HASH_CACHE_FILE, "r");
    if (!f) return false;

    char line[128];
    char prefix[20];
    snprintf(prefix, sizeof(prefix), "%s=", title_id_hex);
    int prefix_len = strlen(prefix);

    while (fgets(line, sizeof(line), f)) {
        if (strncmp(line, prefix, prefix_len) != 0) continue;

        int cached_fc = 0;
        u32 cached_sz = 0;
        const char *val = line + prefix_len;
        if (sscanf(val, "%d:%u:", &cached_fc, &cached_sz) == 2
                && cached_fc == file_count && cached_sz == total_size) {
            const char *p = strchr(val, ':');
            if (p) p = strchr(p + 1, ':');
            if (p) {
                strncpy(hash_out, p + 1, 64);
                hash_out[64] = '\0';
                fclose(f);
                return true;
            }
        }
        fclose(f);
        return false;
    }

    fclose(f);
    return false;
}

bool config_set_cached_hash(const char *title_id_hex, int file_count, u32 total_size,
                            const char *hash_hex) {
    // Read existing cache
    // Max entry: 16 + 1 + 10 + 1 + 10 + 1 + 64 + 2 = ~106 chars
    // For MAX_TITLES=256 entries: ~28KB
    int buf_size = MAX_TITLES * 110;
    char *buf = (char *)malloc(buf_size);
    if (!buf) return false;
    buf[0] = '\0';

    FILE *f = fopen(HASH_CACHE_FILE, "r");
    if (f) {
        int bytes = (int)fread(buf, 1, buf_size - 1, f);
        fclose(f);
        if (bytes > 0) buf[bytes] = '\0';
    }

    char *new_buf = (char *)malloc(buf_size + 110);
    if (!new_buf) { free(buf); return false; }
    new_buf[0] = '\0';

    char prefix[20];
    snprintf(prefix, sizeof(prefix), "%s=", title_id_hex);
    int prefix_len = strlen(prefix);

    // Copy all lines except the existing entry for this title
    char *line = strtok(buf, "\n");
    while (line) {
        if (line[0] != '\0' && strncmp(line, prefix, prefix_len) != 0) {
            strcat(new_buf, line);
            strcat(new_buf, "\n");
        }
        line = strtok(NULL, "\n");
    }
    free(buf);

    // Append updated entry
    char entry[110];
    snprintf(entry, sizeof(entry), "%s=%d:%u:%s\n",
             title_id_hex, file_count, (unsigned int)total_size, hash_hex);
    strcat(new_buf, entry);

    // Ensure directory exists
    mkdir("sdmc:/3ds", 0777);
    mkdir("sdmc:/3ds/3dssync", 0777);

    f = fopen(HASH_CACHE_FILE, "w");
    if (!f) { free(new_buf); return false; }
    fwrite(new_buf, 1, strlen(new_buf), f);
    fclose(f);
    free(new_buf);
    return true;
}

bool config_save(const AppConfig *config) {
    // Ensure directory exists
    mkdir("sdmc:/3ds", 0777);
    mkdir("sdmc:/3ds/3dssync", 0777);

    FILE *f = fopen(CONFIG_PATH, "w");
    if (!f) return false;

    fprintf(f, "# 3DS Save Sync Configuration\n");
    fprintf(f, "server_url=%s\n", config->server_url);
    fprintf(f, "api_key=%s\n", config->api_key);
    if (config->nds_dir[0])
        fprintf(f, "nds_dir=%s\n", config->nds_dir);

    fclose(f);
    return true;
}

// Characters available in the D-pad text editor
static const char charset[] = "abcdefghijklmnopqrstuvwxyz0123456789.:/-_ABCDEFGHIJKLMNOPQRSTUVWXYZ@?=&#%+!";
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

    while (running && aptMainLoop()) {
        // Draw editor on top screen
        consoleClear();
        printf("\x1b[1;1H\x1b[36m--- Edit Field ---\x1b[0m\n\n");
        printf(" \x1b[90m%s\x1b[0m\n\n", hint);

        // Show text with cursor
        printf(" ");
        for (int i = 0; i < len; i++) {
            if (i == cursor)
                printf("\x1b[7m%c\x1b[0m", temp[i]); // Inverted at cursor
            else
                printf("%c", temp[i]);
        }
        if (cursor == len)
            printf("\x1b[7m \x1b[0m"); // Cursor at end
        printf("\n\n");

        printf(" \x1b[90mD-Pad L/R: move cursor\x1b[0m\n");
        printf(" \x1b[90mD-Pad U/D: change character\x1b[0m\n");
        printf(" \x1b[90mA: insert | B: delete\x1b[0m\n");
        printf(" \x1b[90mY: confirm | X: cancel\x1b[0m\n");

        // Draw to both buffers
        gfxFlushBuffers();
        gfxSwapBuffers();
        gspWaitForVBlank();

        // Wait for input
        while (aptMainLoop()) {
            hidScanInput();
            u32 kDown = hidKeysDown();
            if (!kDown) {
                gfxFlushBuffers();
                gfxSwapBuffers();
                gspWaitForVBlank();
                continue;
            }

            if (kDown & KEY_LEFT) {
                if (cursor > 0) cursor--;
                break;
            }
            if (kDown & KEY_RIGHT) {
                if (cursor < len) cursor++;
                break;
            }
            if (kDown & KEY_UP) {
                // Cycle character forward
                if (cursor < len) {
                    int idx = (charset_index(temp[cursor]) + 1) % charset_len;
                    temp[cursor] = charset[idx];
                } else if (len < max_len - 1) {
                    // At end: insert 'a'
                    temp[len] = 'a';
                    len++;
                    temp[len] = '\0';
                }
                break;
            }
            if (kDown & KEY_DOWN) {
                // Cycle character backward
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
            if (kDown & KEY_A) {
                // Insert character at cursor
                if (len < max_len - 1) {
                    memmove(&temp[cursor + 1], &temp[cursor], len - cursor + 1);
                    temp[cursor] = 'a';
                    len++;
                    cursor++;
                }
                break;
            }
            if (kDown & KEY_B) {
                // Delete character before cursor
                if (cursor > 0) {
                    memmove(&temp[cursor - 1], &temp[cursor], len - cursor + 1);
                    cursor--;
                    len--;
                }
                break;
            }
            if (kDown & KEY_Y) {
                confirmed = true;
                running = false;
                break;
            }
            if (kDown & KEY_X) {
                running = false;
                break;
            }

            gfxFlushBuffers();
            gfxSwapBuffers();
            gspWaitForVBlank();
        }
    }

    if (confirmed) {
        strncpy(buffer, temp, max_len - 1);
        buffer[max_len - 1] = '\0';
        return true;
    }
    return false;
}
