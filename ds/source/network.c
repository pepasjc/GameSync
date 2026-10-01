#include "network.h"
#include "http.h"
#include "saves.h"
#include <dswifi9.h>
#include <wfc.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <netinet/in.h>

// WiFi connection state
static bool wifi_connected = false;

// WiFi event handler
void wifi_event_handler(int event) {
    // switch (event) {
    //     case WIFI_EVENT_CONNECT:
    //         printf("Connected to WiFi\n");
    //         wifi_connected = true;
    //         break;
    //     case WIFI_EVENT_DISCONNECT:
    //         printf("WiFi disconnected\n");
    //         wifi_connected = false;
    //         break;
    //     case WIFI_EVENT_ERROR:
    //         printf("WiFi error\n");
    //         wifi_connected = false;
    //         break;
    // }
}

static void wifi_retry_delay(int attempt) {
    int frames = 60 * attempt; // 1s, 2s, 3s
    for (int i = 0; i < frames; i++) {
        swiWaitForVBlank();
    }
}

static int network_init_once(SyncState *state) {
    // Try DSi/3DS firmware settings first (works on DSi/3DS, not on DS Lite)
    if (Wifi_InitDefault(WFC_CONNECT)) {
        wifi_connected = true;
        iprintf("WFC connected!\n");
        
        struct in_addr ip, gateway, mask, dns1, dns2;
        ip = Wifi_GetIPInfo(&gateway, &mask, &dns1, &dns2);
        iprintf("IP: %s\n", inet_ntoa(ip));
        
        return 0;
    }
    
    // If no firmware settings and SSID is configured, try manual connection
    if (state && state->wifi_ssid[0] != '\0') {
        iprintf("Using manual config\n");
        iprintf("SSID: %s\n", state->wifi_ssid);
        
        // Initialize WiFi hardware without connecting
        if (!Wifi_InitDefault(INIT_ONLY)) {
            iprintf("WiFi init failed\n");
            return -1;
        }
        
        // Set up the WFC scan filter to find our specific AP
        static const WlanBssScanFilter filter = {
            .channel_mask = UINT32_MAX,  // Scan all channels
            .target_bssid = { 0xff, 0xff, 0xff, 0xff, 0xff, 0xff },  // Any BSSID
        };
        
        // Begin scan
        if (!wfcBeginScan(&filter)) {
            iprintf("Scan failed to start\n");
            return -1;
        }
        
        iprintf("Scanning...\n");
        
        // Wait for scan to complete
        unsigned count = 0;
        WlanBssDesc* aplist = NULL;
        
        for (int timeout = 0; timeout < 600 && !aplist; timeout++) {  // 60 seconds
            aplist = wfcGetScanBssList(&count);
            if (!aplist) swiWaitForVBlank();
        }
        
        if (!aplist || !count) {
            iprintf("No APs found at all!\n");
            iprintf("Check:\n");
            iprintf("- WiFi on channel 1-11\n");
            iprintf("- Broadcasting SSID\n");
            iprintf("Press START to continue\n");
            return -1;
        }
        
        iprintf("Found %u AP(s)\n", count);
        iprintf("Looking for '%s' (len %u)\n", 
            state->wifi_ssid, strlen(state->wifi_ssid));
        
        // Find our AP by SSID
        WlanBssDesc* target_ap = NULL;
        for (unsigned i = 0; i < count; i++) {
            if (aplist[i].ssid_len == strlen(state->wifi_ssid) &&
                memcmp(aplist[i].ssid, state->wifi_ssid, aplist[i].ssid_len) == 0) {
                target_ap = &aplist[i];
                iprintf("Found '%s' on ch %u\n", 
                    state->wifi_ssid, aplist[i].channel);
                break;
            }
        }
        
        if (!target_ap) {
            iprintf("'%s' not found\n", state->wifi_ssid);
            iprintf("Available APs:\n");
            for (unsigned i = 0; i < count && i < 5; i++) {
                iprintf("  %.*s (ch %u)\n", 
                    aplist[i].ssid_len, aplist[i].ssid,
                    aplist[i].channel);
            }
            if (count > 5) iprintf("  ...and %u more\n", count - 5);
            iprintf("Press START to continue\n");
            return -1;
        }
        
        iprintf("AP found! Connecting...\n");
        
        // Set up auth data
        static WlanAuthData auth;
        memset(&auth, 0, sizeof(auth));
        
        size_t key_len = strlen(state->wifi_wep_key);
        
        if (key_len == 0) {
            // Open network (no encryption)
            iprintf("Open network\n");
            target_ap->auth_type = WlanBssAuthType_Open;
        } else {
            // WEP encrypted network - determine type based on key length
            if (key_len == WLAN_WEP_40_LEN) {
                target_ap->auth_type = WlanBssAuthType_WEP_40;
            } else if (key_len == WLAN_WEP_104_LEN) {
                target_ap->auth_type = WlanBssAuthType_WEP_104;
            } else if (key_len == WLAN_WEP_128_LEN) {
                target_ap->auth_type = WlanBssAuthType_WEP_128;
            } else {
                iprintf("Invalid WEP key length\n");
                iprintf("Need 5, 13, or 16 chars\n");
                iprintf("or leave blank for open\n");
                return -1;
            }
            
            memcpy(auth.wep_key, state->wifi_wep_key, key_len);
        }
        
        // Begin connection
        if (!wfcBeginConnect(target_ap, &auth)) {
            iprintf("Connect failed\n");
            return -1;
        }
        
        // Wait for connection
        bool is_connected = false;
        for (int i = 0; i < 600; i++) {  // 60 seconds max
            int status = Wifi_AssocStatus();
            
            if (status == ASSOCSTATUS_ASSOCIATED) {
                is_connected = true;
                wifi_connected = true;
                break;
            }
            
            if (status == ASSOCSTATUS_DISCONNECTED) {
                iprintf("Connection failed\n");
                return -1;
            }
            
            swiWaitForVBlank();
        }
        
        if (!is_connected) {
            iprintf("Connection timeout\n");
            return -1;
        }
        
        iprintf("Connected!\n");
        
        // Show IP info
        unsigned ip = Wifi_GetIP();
        iprintf("IP: %u.%u.%u.%u\n", 
            ip & 0xff, (ip >> 8) & 0xff, 
            (ip >> 16) & 0xff, (ip >> 24) & 0xff);
        
        return 0;
    }
    
    iprintf("WiFi unavailable\n");
    iprintf("Configure in DS System\n");
    iprintf("or add to config.txt:\n");
    iprintf("wifi_ssid=YourSSID\n");
    iprintf("wifi_wep_key=YourKey\n");
    return -1;
}

#ifdef __DSI__
// DSi build: links a patched copy of dswifi (third_party/dswifi) whose TCP
// receive window is set here. Stock sgIP advertises at most 1400 bytes, so
// only one segment is ever in flight; the DSi's Atheros WiFi and 16 MB of RAM
// can take far more.
#define DSI_TCP_WINDOW_DEFAULT (32 * 1024)
#define DSI_TCP_WINDOW_MAX 65535   // no window scaling in sgIP
#define DSI_PACKET_BUFFERS 48      // extra 2 KB WiFi packet buffers

int sgip_tcp_max_window = 1400;

alignas(ARM_CACHE_LINE_SZ)
static u8 dsi_packet_heap[WLMGR_MIN_PACKET_MEM_SZ + DSI_PACKET_BUFFERS * (sizeof(NetBuf) + 2048)];

static void dsi_wifi_setup(const SyncState *state) {
    static bool done = false;
    if (done) return;
    done = true;

    if (!isDSiMode()) {
        iprintf(CON_YELLOW "DS mode: no DSi WiFi speedup" CON_RESET "\n");
        return;
    }
    setCpuClock(true);  // 134 MHz: the ARM9 runs the TCP/IP stack

    int window = state && state->tcp_window > 0 ? state->tcp_window : DSI_TCP_WINDOW_DEFAULT;
    if (window > DSI_TCP_WINDOW_MAX) window = DSI_TCP_WINDOW_MAX;
    if (window < 1400) window = 1400;
    sgip_tcp_max_window = window;

    // More packet buffers than calico's default so a full window of segments
    // has somewhere to land. Bits set in allocmap go to TX: every fourth
    // buffer, the rest RX. Must run before dswifi's own wlmgrInitDefault(),
    // which is then a no-op.
    static const WlMgrInitConfig config = {
        .pktmem = dsi_packet_heap,
        .pktmem_sz = sizeof(dsi_packet_heap),
        .pktmem_allocmap = 0x11111111,
    };
    if (!wlmgrInit(&config, WLMGR_DEFAULT_THREAD_PRIO))
        iprintf(CON_RED "WiFi buffer setup failed" CON_RESET "\n");
}
#endif

int network_init(SyncState *state) {
    iprintf("Connecting WiFi...\n");
    wifi_connected = false;
#ifdef __DSI__
    dsi_wifi_setup(state);
#endif

    const int max_attempts = 3;
    for (int attempt = 1; attempt <= max_attempts; attempt++) {
        iprintf("Attempt %d/%d\n", attempt, max_attempts);
        if (network_init_once(state) == 0) {
            return 0;
        }

        wifi_connected = false;
        if (attempt < max_attempts) {
            iprintf("Retrying...\n");
            wifi_retry_delay(attempt);
        }
    }

    return -1;
}

// Helper: build sync request payload
static uint8_t* build_sync_payload(SyncState *state, size_t *payload_size) {
    // TODO: Create JSON or binary payload with title metadata
    // Format: array of {title_id, local_hash, last_synced_hash, size}
    *payload_size = 0;
    return NULL;
}

int network_sync(SyncState *state) {
    if (!wifi_connected) {
        printf("Not connected to WiFi\n");
        return -1;
    }
    
    // Build sync request
    size_t payload_size = 0;
    uint8_t *payload = build_sync_payload(state, &payload_size);
    
    // Build URL
    char url[512];
    snprintf(url, sizeof(url), "%s/api/v1/sync", state->server_url);
    
    printf("Syncing to %s...\n", url);
    
    // POST to sync endpoint
    HttpResponse response = http_request(url, HTTP_POST, state->api_key, payload, payload_size);
    
    if (!response.success) {
        printf("Sync request failed (HTTP %d)\n", response.status_code);
        http_response_free(&response);
        if (payload) free(payload);
        return -1;
    }
    
    printf("Sync complete\n");
    
    // TODO: Parse response and execute sync plan
    // Response contains list of {title_id, action: UPLOAD|DOWNLOAD|CONFLICT}
    
    http_response_free(&response);
    if (payload) free(payload);
    
    return 0;
}

int network_upload(SyncState *state, int title_idx) {
    if (!wifi_connected) {
        iprintf("Not connected to WiFi\n");
        return -1;
    }
    
    if (title_idx >= state->num_titles) {
        return -1;
    }
    
    Title *title = &state->titles[title_idx];
    
    // Ensure hash is calculated
    if (!title->hash_calculated) {
        iprintf("Calculating hash...\n");
        if (saves_compute_hash(title->save_path, title->hash) != 0) {
            iprintf("Failed to read save!\n");
            return -1;
        }
        title->hash_calculated = true;
    }
    
    // Read save file
    FILE *f = fopen(title->save_path, "rb");
    if (!f) {
        iprintf("Failed to open save file!\n");
        return -1;
    }
    
    fseek(f, 0, SEEK_END);
    long file_size = ftell(f);
    fseek(f, 0, SEEK_SET);
    
    uint8_t *file_data = (uint8_t*)malloc(file_size);
    if (!file_data) {
        fclose(f);
        iprintf("Out of memory!\n");
        return -1;
    }
    
    size_t read_bytes = fread(file_data, 1, file_size, f);
    fclose(f);
    
    if (read_bytes != file_size) {
        free(file_data);
        iprintf("Failed to read save!\n");
        return -1;
    }
    
    // Strip trailing slash from server_url if present
    char server_url[256];
    strncpy(server_url, state->server_url, sizeof(server_url) - 1);
    size_t len = strlen(server_url);
    if (len > 0 && server_url[len - 1] == '/') {
        server_url[len - 1] = '\0';
    }
    
    // Build URL - use title_id as identifier with /raw endpoint
    char title_id_hex[17];
    snprintf(title_id_hex, sizeof(title_id_hex), "%02X%02X%02X%02X%02X%02X%02X%02X",
        title->title_id[0], title->title_id[1], title->title_id[2], title->title_id[3],
        title->title_id[4], title->title_id[5], title->title_id[6], title->title_id[7]);
    
    char url[512];
    snprintf(url, sizeof(url), "%s/api/v1/saves/%s/raw",
        server_url,
        title_id_hex);
    
    iprintf("=== Upload Debug ===\n");
    iprintf("Server: %s\n", state->server_url);
    iprintf("Game: %s\n", title->game_name);
    iprintf("URL: %s\n", url);
    iprintf("Size: %ld bytes\n", file_size);
    iprintf("API Key: %.10s...\n\n", state->api_key);
    
    iprintf("Sending POST...\n");
    
    // POST save data
    HttpResponse response = http_request(url, HTTP_POST, state->api_key, file_data, file_size);
    
    free(file_data);
    
    if (!response.success) {
        iprintf("HTTP %d\n", response.status_code);
        http_response_free(&response);
        return -1;
    }
    
    http_response_free(&response);
    return 0;
}

int network_download(SyncState *state, int title_idx) {
    if (!wifi_connected) {
        iprintf("Not connected to WiFi\n");
        return -1;
    }
    
    if (title_idx >= state->num_titles) {
        return -1;
    }
    
    Title *title = &state->titles[title_idx];
    
    // Strip trailing slash from server_url if present
    char server_url[256];
    strncpy(server_url, state->server_url, sizeof(server_url) - 1);
    size_t len = strlen(server_url);
    if (len > 0 && server_url[len - 1] == '/') {
        server_url[len - 1] = '\0';
    }
    
    // Build URL - use title_id as identifier with /raw endpoint for direct binary
    char title_id_hex[17];
    snprintf(title_id_hex, sizeof(title_id_hex), "%02X%02X%02X%02X%02X%02X%02X%02X",
        title->title_id[0], title->title_id[1], title->title_id[2], title->title_id[3],
        title->title_id[4], title->title_id[5], title->title_id[6], title->title_id[7]);
    
    char url[512];
    snprintf(url, sizeof(url), "%s/api/v1/saves/%s/raw",
        server_url,
        title_id_hex);
    
    iprintf("GET %s\n", url);
    
    // GET save data
    HttpResponse response = http_request(url, HTTP_GET, state->api_key, NULL, 0);
    
    if (!response.success) {
        iprintf("HTTP %d\n", response.status_code);
        http_response_free(&response);
        return -1;
    }
    
    // Write to file
    FILE *f = fopen(title->save_path, "wb");
    if (!f) {
        iprintf("Failed to open file!\n");
        http_response_free(&response);
        return -1;
    }
    
    size_t written = fwrite(response.body, 1, response.body_size, f);
    fclose(f);
    
    if (written != response.body_size) {
        iprintf("Write failed!\n");
        http_response_free(&response);
        return -1;
    }
    
    iprintf("Wrote %zu bytes\n", written);
    
    // Update save size
    title->save_size = response.body_size;
    
    // Recalculate hash for downloaded file
    if (saves_compute_hash(title->save_path, title->hash) == 0) {
        title->hash_calculated = true;
    }
    
    http_response_free(&response);
    return 0;
}

// Fetch list of saves from server
int network_fetch_saves(SyncState *state) {
    if (!wifi_connected) {
        iprintf("No WiFi connection\n");
        return -1;
    }
    
    // Strip trailing slash from server_url if present
    char server_url[256];
    strncpy(server_url, state->server_url, sizeof(server_url) - 1);
    size_t len = strlen(server_url);
    if (len > 0 && server_url[len - 1] == '/') {
        server_url[len - 1] = '\0';
    }
    
    // Build URL: GET /api/v1/titles
    char url[512];
    snprintf(url, sizeof(url), "%s/api/v1/titles", server_url);
    
    iprintf("Fetching save list...\n");
    
    HttpResponse response = http_request(url, HTTP_GET, state->api_key, NULL, 0);
    
    iprintf("Status: %d\n", response.status_code);
    iprintf("Body size: %zu bytes\n", response.body_size);
    
    if (!response.success) {
        iprintf("Failed to fetch saves\n");
        if (response.body && response.body_size > 0) {
            iprintf("Error: %.50s\n", (char*)response.body);
        }
        http_response_free(&response);
        return -1;
    }
    
    // Parse JSON response - look for game names in simple format
    // Expected format: ["game1.sav", "game2.sav", ...]
    // For now, mark all matching games as on_server
    
    if (response.body && response.body_size > 0) {
        iprintf("Parsing response...\n");
        int found = 0;
        for (int i = 0; i < state->num_titles; i++) {
            // Check if game name appears in response
            if (strstr((char*)response.body, state->titles[i].game_name)) {
                state->titles[i].on_server = true;
                found++;
            }
        }
        iprintf("Checked %d saves\n", state->num_titles);
        iprintf("Found %d on server\n", found);
    }
    
    http_response_free(&response);
    return 0;
}

// Get save info from server
int network_get_save_info(SyncState *state, const char *title_id_hex, char *hash_out, size_t *size_out) {
    if (!wifi_connected) {
        return -1;
    }
    
    // Strip trailing slash from server_url
    char server_url[256];
    strncpy(server_url, state->server_url, sizeof(server_url) - 1);
    size_t len = strlen(server_url);
    if (len > 0 && server_url[len - 1] == '/') {
        server_url[len - 1] = '\0';
    }
    
    // Build URL: GET /api/v1/saves/{title_id}/meta
    char url[512];
    snprintf(url, sizeof(url), "%s/api/v1/saves/%s/meta", server_url, title_id_hex);
    
    HttpResponse response = http_request(url, HTTP_GET, state->api_key, NULL, 0);
    
    // If not found, return -1 (save doesn't exist on server)
    if (response.status_code == 404) {
        http_response_free(&response);
        return -1;
    }
    
    if (!response.success || !response.body) {
        http_response_free(&response);
        return -1;
    }
    
    // Parse JSON: {"save_hash": "...", "save_size": 123, ...}
    // Simple string search for hash and size
    char *body = (char*)response.body;
    char *hash_ptr = strstr(body, "\"save_hash\":\"");
    char *size_ptr = strstr(body, "\"save_size\":");
    
    if (hash_ptr && hash_out) {
        hash_ptr += 13; // Skip "save_hash":"
        int i = 0;
        while (i < 64 && hash_ptr[i] != '"') {
            hash_out[i] = hash_ptr[i];
            i++;
        }
        hash_out[i] = '\0';
    }
    
    if (size_ptr && size_out) {
        sscanf(size_ptr + 12, "%zu", size_out);
    }
    
    http_response_free(&response);
    return 0;
}

// Extended version: also parses client_timestamp from /meta response
int network_get_save_info_ext(SyncState *state, const char *title_id_hex,
                               char *hash_out, size_t *size_out, uint32_t *timestamp_out) {
    if (!wifi_connected) {
        return -1;
    }

    // Strip trailing slash from server_url
    char server_url[256];
    strncpy(server_url, state->server_url, sizeof(server_url) - 1);
    size_t len = strlen(server_url);
    if (len > 0 && server_url[len - 1] == '/') {
        server_url[len - 1] = '\0';
    }

    // Build URL: GET /api/v1/saves/{title_id}/meta
    char url[512];
    snprintf(url, sizeof(url), "%s/api/v1/saves/%s/meta", server_url, title_id_hex);

    HttpResponse response = http_request(url, HTTP_GET, state->api_key, NULL, 0);

    if (response.status_code == 404) {
        http_response_free(&response);
        return -1;
    }

    if (!response.success || !response.body) {
        http_response_free(&response);
        return -1;
    }

    char *body = (char*)response.body;

    // Parse save_hash
    char *hash_ptr = strstr(body, "\"save_hash\":\"");
    if (hash_ptr && hash_out) {
        hash_ptr += 13;
        int i = 0;
        while (i < 64 && hash_ptr[i] != '"') {
            hash_out[i] = hash_ptr[i];
            i++;
        }
        hash_out[i] = '\0';
    }

    // Parse save_size
    if (size_out) {
        char *size_ptr = strstr(body, "\"save_size\":");
        if (size_ptr) {
            sscanf(size_ptr + 12, "%zu", size_out);
        }
    }

    // Parse client_timestamp
    if (timestamp_out) {
        *timestamp_out = 0;
        char *ts_ptr = strstr(body, "\"client_timestamp\":");
        if (ts_ptr) {
            *timestamp_out = (uint32_t)strtoul(ts_ptr + 19, NULL, 10);
        }
    }

    http_response_free(&response);
    return 0;
}

void network_cleanup(void) {
    http_cleanup();
    if (wifi_connected) {
        Wifi_DisconnectAP();
        // Wait for WiFi to fully disconnect (about 1 second)
        // This helps other apps initialize WiFi cleanly after we exit
        for (int i = 0; i < 60; i++) {
            swiWaitForVBlank();
        }
    }
    wifi_connected = false;
}
