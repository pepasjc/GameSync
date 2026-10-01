#ifndef COMMON_H
#define COMMON_H

// Use full nds.h
#include <nds.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <stdbool.h>

#define MAX_TITLES 512
#define MAX_PATH 256
#define TITLE_ID_SIZE 8
#define HASH_SIZE 32
#define CONFIG_SIZE 256

// Console colours. The libnds 2.x text console only understands SGR 30-37
// (colour) and 39 (default colour); ESC[0m and ESC[7m are ignored, so reset
// with CON_RESET or everything printed afterwards keeps the colour.
#define CON_RED    "\x1b[31m"
#define CON_GREEN  "\x1b[32m"
#define CON_YELLOW "\x1b[33m"
#define CON_CYAN   "\x1b[36m"
#define CON_RESET  "\x1b[39m"

// Sync action
typedef enum {
    SYNC_UP_TO_DATE,
    SYNC_UPLOAD,
    SYNC_DOWNLOAD,
    SYNC_CONFLICT
} SyncAction;

// Title information
typedef struct {
    uint8_t title_id[TITLE_ID_SIZE];
    uint32_t save_size;
    uint8_t hash[HASH_SIZE];
    uint8_t last_hash[HASH_SIZE];
    uint8_t server_hash[HASH_SIZE];
    uint8_t local_hash[HASH_SIZE];
    char game_name[256];
    char save_path[MAX_PATH];
    uint32_t timestamp;
    int is_cartridge;
    int needs_sync;
    bool hash_calculated;  // True if hash has been computed
    bool on_server;        // True if save exists on server
    bool scanned;          // True if scan has been performed
    SyncAction scan_result; // Result of last scan (UP_TO_DATE, UPLOAD, DOWNLOAD, CONFLICT)
} Title;

// Sync state
typedef struct {
    char server_url[256];
    char api_key[128];
    char custom_save_dir[256];  // Optional custom save directory
    char wifi_ssid[33];          // WiFi SSID (max 32 chars + null)
    char wifi_wep_key[14];       // WEP key (13 chars + null for 104-bit WEP)
    int tcp_window;              // DSi build: TCP receive window in bytes, 0 = default
    uint32_t console_id;
    int num_titles;
    Title titles[MAX_TITLES];
} SyncState;

#endif
