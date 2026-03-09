#include "update.h"
#include "network.h"
#include <3ds.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>

// CIA file path on SD card
#define UPDATE_CIA_PATH "sdmc:/3ds/3dssync/update.cia"

// Download buffer size
#define DOWNLOAD_CHUNK_SIZE 0x8000  // 32KB

// Helper to set error message
static void set_error(char *error_out, int error_size, const char *msg) {
    if (error_out && error_size > 0) {
        strncpy(error_out, msg, error_size - 1);
        error_out[error_size - 1] = '\0';
    }
}

// Simple JSON string extraction (no external library needed)
// Finds "key": "value" and copies value to out (up to out_size - 1 chars)
static bool json_get_string(const char *json, const char *key, char *out, int out_size) {
    char search[64];
    snprintf(search, sizeof(search), "\"%s\":", key);

    const char *pos = strstr(json, search);
    if (!pos) return false;

    pos += strlen(search);
    // Skip whitespace
    while (*pos == ' ' || *pos == '\t') pos++;

    // Handle null value
    if (strncmp(pos, "null", 4) == 0) {
        out[0] = '\0';
        return false;
    }

    // Expect opening quote
    if (*pos != '"') return false;
    pos++;

    // Copy until closing quote
    int i = 0;
    while (*pos && *pos != '"' && i < out_size - 1) {
        out[i++] = *pos++;
    }
    out[i] = '\0';
    return i > 0;
}

// Extract boolean value from JSON
static bool json_get_bool(const char *json, const char *key) {
    char search[64];
    snprintf(search, sizeof(search), "\"%s\":", key);

    const char *pos = strstr(json, search);
    if (!pos) return false;

    pos += strlen(search);
    while (*pos == ' ' || *pos == '\t') pos++;

    return strncmp(pos, "true", 4) == 0;
}

// Extract integer value from JSON
static u32 json_get_int(const char *json, const char *key) {
    char search[64];
    snprintf(search, sizeof(search), "\"%s\":", key);

    const char *pos = strstr(json, search);
    if (!pos) return 0;

    pos += strlen(search);
    while (*pos == ' ' || *pos == '\t') pos++;

    return (u32)atoi(pos);
}

bool update_check(const AppConfig *config, UpdateInfo *info) {
    memset(info, 0, sizeof(UpdateInfo));

    // Build path with current version parameter
    char path[128];
    snprintf(path, sizeof(path), "/update/check?current=%s", APP_VERSION);

    u32 resp_size = 0;
    u32 status = 0;
    u8 *resp = network_get(config, path, &resp_size, &status);

    if (!resp || status != 200) {
        if (resp) free(resp);
        return false;
    }

    // Null-terminate for JSON parsing
    char *json = (char *)malloc(resp_size + 1);
    if (!json) {
        free(resp);
        return false;
    }
    memcpy(json, resp, resp_size);
    json[resp_size] = '\0';
    free(resp);

    // Parse JSON response
    info->available = json_get_bool(json, "available");
    json_get_string(json, "latest_version", info->latest_version, sizeof(info->latest_version));
    json_get_string(json, "download_url", info->download_url, sizeof(info->download_url));
    info->file_size = json_get_int(json, "file_size");

    free(json);
    return true;
}

bool update_download(const AppConfig *config, const char *url, UpdateProgressCb progress) {
    // Build proxy download path
    // URL needs to be URL-encoded, but for simplicity we'll just use it directly
    // since it's from our own server's response
    char path[512];
    snprintf(path, sizeof(path), "/update/download?url=%s", url);

    // Report initial progress
    if (progress) progress(0);

    // Download the CIA file
    u32 resp_size = 0;
    u32 status = 0;
    u8 *data = network_get(config, path, &resp_size, &status);

    if (!data || status != 200 || resp_size == 0) {
        if (data) free(data);
        return false;
    }

    // Ensure directory exists
    mkdir("sdmc:/3ds", 0777);
    mkdir("sdmc:/3ds/3dssync", 0777);

    // Write to SD card
    FILE *f = fopen(UPDATE_CIA_PATH, "wb");
    if (!f) {
        free(data);
        return false;
    }

    // Write in chunks and report progress
    u32 written = 0;
    while (written < resp_size) {
        u32 chunk = resp_size - written;
        if (chunk > DOWNLOAD_CHUNK_SIZE) chunk = DOWNLOAD_CHUNK_SIZE;

        size_t wrote = fwrite(data + written, 1, chunk, f);
        if (wrote != chunk) {
            fclose(f);
            free(data);
            remove(UPDATE_CIA_PATH);
            return false;
        }

        written += chunk;
        if (progress) {
            int pct = (int)((written * 100) / resp_size);
            progress(pct);
        }
    }

    fclose(f);
    free(data);

    if (progress) progress(100);
    return true;
}

bool update_install(UpdateProgressCb progress, char *error_out, int error_size) {
    // Open the CIA file
    FILE *f = fopen(UPDATE_CIA_PATH, "rb");
    if (!f) {
        set_error(error_out, error_size, "Cannot open CIA file");
        return false;
    }

    // Get file size
    fseek(f, 0, SEEK_END);
    u32 file_size = ftell(f);
    fseek(f, 0, SEEK_SET);

    if (file_size == 0) {
        fclose(f);
        set_error(error_out, error_size, "CIA file is empty");
        return false;
    }

    if (progress) progress(0);

    // Start CIA installation to SD card
    Handle cia_handle;
    Result res = AM_StartCiaInstall(MEDIATYPE_SD, &cia_handle);
    if (R_FAILED(res)) {
        fclose(f);
        char msg[64];
        snprintf(msg, sizeof(msg), "AM_StartCiaInstall: %08lX", res);
        set_error(error_out, error_size, msg);
        return false;
    }

    // Read and write in chunks
    u8 *buffer = (u8 *)malloc(DOWNLOAD_CHUNK_SIZE);
    if (!buffer) {
        AM_CancelCIAInstall(cia_handle);
        fclose(f);
        set_error(error_out, error_size, "Out of memory");
        return false;
    }

    u32 total_written = 0;
    bool success = true;
    Result write_res = 0;

    while (total_written < file_size) {
        u32 to_read = file_size - total_written;
        if (to_read > DOWNLOAD_CHUNK_SIZE) to_read = DOWNLOAD_CHUNK_SIZE;

        size_t read = fread(buffer, 1, to_read, f);
        if (read != to_read) {
            set_error(error_out, error_size, "Failed to read CIA");
            success = false;
            break;
        }

        // Write to CIA handle
        u32 written = 0;
        write_res = FSFILE_Write(cia_handle, &written, total_written, buffer, to_read, FS_WRITE_FLUSH);
        if (R_FAILED(write_res) || written != to_read) {
            char msg[128];
            snprintf(msg, sizeof(msg), "FSFILE_Write: %08lX\nat offset %lu/%lu (wrote %lu/%lu)",
                write_res, total_written, file_size, written, to_read);
            set_error(error_out, error_size, msg);
            success = false;
            break;
        }

        total_written += written;

        if (progress) {
            int pct = (int)((total_written * 100) / file_size);
            progress(pct);
        }
    }

    free(buffer);
    fclose(f);

    if (!success) {
        AM_CancelCIAInstall(cia_handle);
        return false;
    }

    // Finish installation
    res = AM_FinishCiaInstall(cia_handle);
    if (R_FAILED(res)) {
        char msg[64];
        snprintf(msg, sizeof(msg), "AM_FinishCiaInstall: %08lX", res);
        set_error(error_out, error_size, msg);
        return false;
    }

    // Clean up the downloaded CIA file
    remove(UPDATE_CIA_PATH);

    if (progress) progress(100);
    return true;
}

void update_relaunch(void) {
    // Get our own title ID
    u64 title_id = 0;
    APT_GetProgramID(&title_id);

    if (title_id == 0) {
        // Can't get title ID (likely running as 3dsx)
        return;
    }

    // Prepare and execute application jump to ourselves
    // This relaunches the app with the newly installed version
    Result res = APT_PrepareToDoApplicationJump(0, title_id, MEDIATYPE_SD);
    if (R_SUCCEEDED(res)) {
        // This doesn't return on success
        APT_DoApplicationJump(NULL, 0, NULL);
    }
}
