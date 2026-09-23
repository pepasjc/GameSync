/*
 * network.c — GameCube BBA bring-up + thin wrappers around http.c.
 *
 * libogc's net stack drives the Broadband Adapter directly; if_config()
 * does static-IP or DHCP and returns the resulting address.  No IRX,
 * NetMan or ps2ip layering like the PS2 client.
 */

#include "gcnet.h"
#include "http.h"

#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#include <unistd.h>

#include <gccore.h>
#include <network.h>

static NetProgress64Fn g_progress_cb = NULL;
static uint64_t        g_progress_base = 0;

void network_set_progress64_cb(NetProgress64Fn cb) { g_progress_cb = cb; }

static int progress_bridge(uint64_t done, uint64_t total) {
    if (g_progress_cb) {
        uint64_t abs_done  = g_progress_base + done;
        uint64_t abs_total = total > 0 ? g_progress_base + total : 0;
        return g_progress_cb(abs_done, abs_total);
    }
    return 0;
}

int network_init(SyncState *state) {
    if (!state) return -1;

    state->net_ready = false;
    state->dhcp_ok   = false;

    char ip[16] = {0}, nm[16] = {0}, gw[16] = {0};
    s32 ret;

    if (state->use_static_ip) {
        strncpy(ip, state->static_ip, sizeof(ip) - 1);
        strncpy(nm, state->static_netmask, sizeof(nm) - 1);
        strncpy(gw, state->static_gateway, sizeof(gw) - 1);
        ret = if_config(ip, nm, gw, false, 20);
    } else {
        ret = if_config(ip, nm, gw, true, 20);
    }

    if (ret < 0) {
        snprintf(state->ip, sizeof(state->ip), "net-fail");
        return -1;
    }

    state->net_ready = true;
    state->dhcp_ok   = true;
    strncpy(state->ip, ip, sizeof(state->ip) - 1);
    state->ip[sizeof(state->ip) - 1] = '\0';
    strncpy(state->netmask, nm, sizeof(state->netmask) - 1);
    state->netmask[sizeof(state->netmask) - 1] = '\0';
    strncpy(state->gateway, gw, sizeof(state->gateway) - 1);
    state->gateway[sizeof(state->gateway) - 1] = '\0';
    return 0;
}

void network_shutdown(void) {
    /* libogc keeps the interface up until reset; nothing to tear down. */
}

bool network_is_ready(const SyncState *state) {
    return state && state->net_ready && state->dhcp_ok;
}

bool network_check_server(const SyncState *state) {
    HttpRequest req = {0};
    req.server_url = state->server_url;
    req.api_key    = state->api_key;
    req.path       = "/api/v1/status";
    req.method     = "GET";

    static uint8_t buf[1024];
    int status = 0;
    int n = http_get_buf(&req, buf, sizeof(buf), &status);
    return (n >= 0 && status == 200);
}

int network_fetch_rom_catalog(const SyncState *state,
                              const char *system_code,
                              int offset, int limit,
                              char *out, uint32_t out_size,
                              int *status_out)
{
    if (!network_is_ready(state)) {
        if (status_out) *status_out = 0;
        return -100;
    }

    char path[256];
    snprintf(path, sizeof(path),
             "/api/v1/roms?system=%s&offset=%d&limit=%d",
             system_code ? system_code : "GC", offset, limit);

    HttpRequest req = {0};
    req.server_url = state->server_url;
    req.api_key    = state->api_key;
    req.path       = path;
    req.method     = "GET";

    return http_get_buf(&req, (uint8_t *)out, out_size, status_out);
}

static void build_rom_download_path(char *path, size_t path_size,
                                    const char *rom_id, const char *extract_fmt)
{
    if (extract_fmt && extract_fmt[0])
        snprintf(path, path_size, "/api/v1/roms/%s?extract=%s", rom_id, extract_fmt);
    else
        snprintf(path, path_size, "/api/v1/roms/%s", rom_id);
}

int network_download_rom_resumable(const SyncState *state,
                                   const char *rom_id,
                                   const char *extract_fmt,
                                   const char *target_path,
                                   uint64_t start_offset,
                                   uint64_t *total_out)
{
    if (total_out) *total_out = 0;

    char path[512];
    build_rom_download_path(path, sizeof(path), rom_id, extract_fmt);

    /* NOTE: parallel range download (http_download_parallel) is disabled — its
     * out-of-order positioned writes to distant file offsets thrash libfat
     * (FAT cluster-chain walk per write) and collapse to ~8 KB/s.  A
     * FAT-friendly striped writer or a libogc2 migration (bigger TCP_WND) is
     * the real fix; until then use the sequential single-stream path, which
     * batches into large sequential SD writes. */

    char part[640];
    snprintf(part, sizeof(part), "%s.part", target_path);

    FILE *fp = fopen(part, start_offset > 0 ? "ab" : "wb");
    if (!fp) return -2;

    /* Large stdio buffer so recv chunks batch into big sequential SD writes
     * (the SD/EXI path is much faster with few large writes than many small). */
    static char io_buf[256 * 1024] __attribute__((aligned(32)));
    setvbuf(fp, io_buf, _IOFBF, sizeof(io_buf));

    HttpRequest req = {0};
    req.server_url  = state->server_url;
    req.api_key     = state->api_key;
    req.path        = path;
    req.method      = "GET";
    req.range_start = start_offset;

    HttpResponseInfo info;
    g_progress_base = start_offset;
    int rc = http_get_stream(&req, fp, progress_bridge, &info);
    g_progress_base = 0;
    fclose(fp);

    if (total_out && info.content_length > 0)
        *total_out = info.content_length + start_offset;

    if (rc == 1) return 1;
    if (rc < 0) {
        if (info.status > 0 && (info.status < 200 || info.status >= 300)) return -3;
        return -1;
    }

    if (rename(part, target_path) != 0) {
        unlink(target_path);
        if (rename(part, target_path) != 0) return -2;
    }
    return 0;
}
