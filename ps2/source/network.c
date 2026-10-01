/*
 * network.c — PS2 network bring-up + thin wrappers around http.c.
 *
 * Lifecycle:
 *   1. main.c calls SifInitRpc/SifLoadFileInit/etc and loads embedded
 *      IRX modules (dev9, netman, smap, ps2ip, ps2ips, etc.) before
 *      calling network_init().
 *   2. network_init() applies static IP config or starts DHCP via ps2ip
 *      and stores the resulting IP in state->ip.
 *
 * The actual IRX loading lives in main.c so we can keep this file
 * dependency-free except for ps2ip headers.
 */

#include "network.h"
#include "http.h"
#include "ui.h"

#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#include <unistd.h>

#include <delaythread.h>
#include <kernel.h>
#include <netman.h>
#include <ps2ip.h>

static NetProgress64Fn g_progress_cb = NULL;
static uint64_t g_progress_base = 0;

void network_set_progress64_cb(NetProgress64Fn cb) {
    g_progress_cb = cb;
}

static int progress_bridge(uint64_t done, uint64_t total) {
    if (g_progress_cb) {
        uint64_t absolute_done  = g_progress_base + done;
        uint64_t absolute_total = total > 0 ? g_progress_base + total : 0;
        return g_progress_cb(absolute_done, absolute_total);
    }
    return 0;
}

/* ---- Init / shutdown ---- */

static int wait_for_link(int timeout_seconds) {
    /* GET_LINK_STATUS returns NETMAN_NETIF_ETH_LINK_STATE_UP when cable
     * is plugged and PHY has negotiated. ETH_GET_LINK_MODE returns the
     * negotiated speed/duplex bits, which can be non-zero even when the
     * link is actually down — wrong signal to poll. */
    int last_status = -999;
    for (int i = 0; i < timeout_seconds * 10; i++) {
        int status = NetManIoctl(NETMAN_NETIF_IOCTL_GET_LINK_STATUS, NULL, 0, NULL, 0);
        if (status != last_status) {
            ui_log("  link status=%d (target=%d) at t=%d.%ds\n",
                   status, NETMAN_NETIF_ETH_LINK_STATE_UP, i / 10, i % 10);
            last_status = status;
        }
        if (status == NETMAN_NETIF_ETH_LINK_STATE_UP) return 0;
        DelayThread(100000);
    }
    return -1;
}

static bool parse_ipv4_addr(const char *text, ip4_addr_t *out) {
    if (!text || !out) return false;
    unsigned a, b, c, d;
    char extra;
    if (sscanf(text, "%u.%u.%u.%u%c", &a, &b, &c, &d, &extra) != 4) {
        return false;
    }
    if (a > 255 || b > 255 || c > 255 || d > 255) return false;
    IP4_ADDR(out, a, b, c, d);
    return true;
}

/* SMAP registers itself with the lwIP stack as netif "sm0". The legacy
 * ps2sdk used "en0" — that name no longer exists. */
#define PS2_NETIF "sm0"

static int apply_ip_config(bool use_dhcp,
                           const ip4_addr_t *ip,
                           const ip4_addr_t *nm,
                           const ip4_addr_t *gw)
{
    t_ip_info ip_info;
    if (ps2ip_getconfig(PS2_NETIF, &ip_info) < 0) return -1;

    if (use_dhcp) {
        ip_info.dhcp_enabled = 1;
    } else {
        ip_addr_set((struct ip4_addr *)&ip_info.ipaddr,  ip);
        ip_addr_set((struct ip4_addr *)&ip_info.netmask, nm);
        ip_addr_set((struct ip4_addr *)&ip_info.gw,      gw);
        ip_info.dhcp_enabled = 0;
    }
    return ps2ip_setconfig(&ip_info);
}

int network_init(SyncState *state) {
    if (!state) return -1;

    state->net_ready = false;
    state->dhcp_ok   = false;

    ui_log("  net: NetManInit...\n");
    int rc = NetManInit();
    ui_log("  net: NetManInit -> %d\n", rc);
    if (rc < 0) {
        snprintf(state->ip, sizeof(state->ip), "netman-fail");
        return -1;
    }

    /* Auto-negotiate speed/duplex. Required before bringing up the IP
     * stack — without it the SMAP driver may stay in the default
     * disabled state. */
    rc = NetManSetLinkMode(NETMAN_NETIF_ETH_LINK_MODE_AUTO);
    ui_log("  net: NetManSetLinkMode(AUTO) -> %d\n", rc);

    ip4_addr_t ip, nm, gw;

    if (state->use_static_ip) {
        if (!parse_ipv4_addr(state->static_ip, &ip) ||
            !parse_ipv4_addr(state->static_netmask, &nm) ||
            !parse_ipv4_addr(state->static_gateway, &gw))
        {
            snprintf(state->ip, sizeof(state->ip), "bad-static");
            NetManDeinit();
            return -2;
        }
    } else {
        ip4_addr_set_zero(&ip);
        ip4_addr_set_zero(&nm);
        ip4_addr_set_zero(&gw);
    }

    /* ps2ipInit only registers the netif. DHCP is enabled separately
     * via ps2ip_setconfig(). Passing zero IP here is fine — the actual
     * address comes from the dhcp negotiation triggered below. */
    ui_log("  net: ps2ipInit...\n");
    rc = ps2ipInit(&ip, &nm, &gw);
    ui_log("  net: ps2ipInit -> %d\n", rc);
    if (rc < 0) {
        snprintf(state->ip, sizeof(state->ip), "ps2ip-fail");
        NetManDeinit();
        return -2;
    }

    rc = apply_ip_config(!state->use_static_ip, &ip, &nm, &gw);
    ui_log("  net: apply_ip_config(dhcp=%d) -> %d\n",
           !state->use_static_ip, rc);
    if (rc < 0) {
        snprintf(state->ip, sizeof(state->ip), "ipcfg-fail");
        NetManDeinit();
        return -2;
    }

    state->net_ready = true;

    ui_log("  net: waiting for link (30s)...\n");
    if (wait_for_link(30) != 0) {
        snprintf(state->ip, sizeof(state->ip), "no-link");
        return 0;
    }
    ui_log("  net: link UP\n");

    if (state->use_static_ip) {
        state->dhcp_ok = true;
        strncpy(state->ip, state->static_ip, sizeof(state->ip) - 1);
        state->ip[sizeof(state->ip) - 1] = '\0';
        strncpy(state->netmask, state->static_netmask,
                sizeof(state->netmask) - 1);
        state->netmask[sizeof(state->netmask) - 1] = '\0';
        strncpy(state->gateway, state->static_gateway,
                sizeof(state->gateway) - 1);
        state->gateway[sizeof(state->gateway) - 1] = '\0';
        return 0;
    }

    /* Wait for DHCP bound state — checking a non-zero IP isn't reliable
     * because the lease may bind briefly and then be renewed. */
    t_ip_info ip_info;
    for (int i = 0; i < 200; i++) {
        if (ps2ip_getconfig(PS2_NETIF, &ip_info) >= 0) {
            bool bound = ip_info.dhcp_enabled &&
                         (ip_info.dhcp_status == DHCP_STATE_BOUND ||
                          ip_info.dhcp_status == DHCP_STATE_OFF);
            uint32_t ip_raw = (uint32_t)ip_info.ipaddr.s_addr;
            if (bound && ip_raw != 0) {
                uint32_t nm_raw = (uint32_t)ip_info.netmask.s_addr;
                uint32_t gw_raw = (uint32_t)ip_info.gw.s_addr;
                state->dhcp_ok = true;
                snprintf(state->ip, sizeof(state->ip), "%u.%u.%u.%u",
                         (unsigned)( ip_raw        & 0xff),
                         (unsigned)((ip_raw >>  8) & 0xff),
                         (unsigned)((ip_raw >> 16) & 0xff),
                         (unsigned)((ip_raw >> 24) & 0xff));
                snprintf(state->netmask, sizeof(state->netmask), "%u.%u.%u.%u",
                         (unsigned)( nm_raw        & 0xff),
                         (unsigned)((nm_raw >>  8) & 0xff),
                         (unsigned)((nm_raw >> 16) & 0xff),
                         (unsigned)((nm_raw >> 24) & 0xff));
                snprintf(state->gateway, sizeof(state->gateway), "%u.%u.%u.%u",
                         (unsigned)( gw_raw        & 0xff),
                         (unsigned)((gw_raw >>  8) & 0xff),
                         (unsigned)((gw_raw >> 16) & 0xff),
                         (unsigned)((gw_raw >> 24) & 0xff));
                return 0;
            }
        }
        DelayThread(100000);
    }

    snprintf(state->ip, sizeof(state->ip), "no-dhcp");
    return 0;
}

void network_shutdown(void) {
    /* No public ps2ip dhcp_stop in this ps2sdk rev — NetManDeinit tears
     * down the netif which releases the lease implicitly. */
    NetManDeinit();
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

/* ---- Catalog / download wrappers ---- */

static void build_rom_download_path(char *path, size_t path_size,
                                    const char *rom_id,
                                    const char *extract_fmt)
{
    if (extract_fmt && extract_fmt[0]) {
        snprintf(path, path_size, "/api/v1/roms/%s?extract=%s",
                 rom_id, extract_fmt);
    } else {
        snprintf(path, path_size, "/api/v1/roms/%s", rom_id);
    }
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
             system_code ? system_code : "PS2", offset, limit);

    HttpRequest req = {0};
    req.server_url = state->server_url;
    req.api_key    = state->api_key;
    req.path       = path;
    req.method     = "GET";

    return http_get_buf(&req, (uint8_t *)out, out_size, status_out);
}

int network_get(const SyncState *state, const char *path,
                char *out, uint32_t out_size, int *status_out)
{
    if (status_out) *status_out = 0;
    if (!network_is_ready(state)) return -100;
    if (!out || out_size < 2) return -1;

    HttpRequest req = {0};
    req.server_url = state->server_url;
    req.api_key    = state->api_key;
    req.path       = path;
    req.method     = "GET";

    int n = http_get_buf(&req, (uint8_t *)out, out_size - 1, status_out);
    out[n > 0 ? n : 0] = '\0';
    return n;
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

    char part[640];
    snprintf(part, sizeof(part), "%s.part", target_path);

    FILE *fp = fopen(part, start_offset > 0 ? "ab" : "wb");
    if (!fp) return -2;

    /* 256 KB stdio buffer — each recv lands in the buffer; the buffer
     * flushes once full, emitting a single big write() to fileXio
     * instead of 16 separate 4 KB writes.  Direct POSIX write() per
     * recv was tried and turned out *slower* (~90 KB/s vs ~350 KB/s)
     * because libcglue's write path doesn't batch the way the stdio
     * buffer does. */
    static char io_buf[256 * 1024] __attribute__((aligned(64)));
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

    if (total_out && info.content_length > 0) {
        *total_out = info.content_length + start_offset;
    }

    if (rc == 1) return 1;       /* paused */
    if (rc < 0) {
        if (info.status > 0 && (info.status < 200 || info.status >= 300))
            return -3;
        return -1;
    }

    /* Atomic rename .part → target. */
    if (rename(part, target_path) != 0) {
        /* On Windows-mounted FAT the destination might already exist
         * from a previous failed run.  Try unlink then rename. */
        unlink(target_path);
        if (rename(part, target_path) != 0) return -2;
    }
    return 0;
}

int network_download_rom_to_sink(const SyncState *state,
                                 const char *rom_id,
                                 const char *extract_fmt,
                                 NetStreamBeginFn begin,
                                 NetWriteFn writer,
                                 void *user,
                                 uint64_t start_offset,
                                 uint64_t *total_out)
{
    if (total_out) *total_out = 0;
    if (!network_is_ready(state) || !writer) return -1;

    char path[512];
    build_rom_download_path(path, sizeof(path), rom_id, extract_fmt);

    HttpRequest req = {0};
    req.server_url  = state->server_url;
    req.api_key     = state->api_key;
    req.path        = path;
    req.method      = "GET";
    req.range_start = start_offset;

    HttpResponseInfo info;
    g_progress_base = start_offset;
    int rc = http_get_stream_cb(&req,
                                (HttpStreamBeginFn)begin,
                                (HttpWriteFn)writer,
                                user,
                                progress_bridge,
                                &info);
    g_progress_base = 0;

    if (total_out && info.content_length > 0) {
        *total_out = info.content_length + start_offset;
    }

    if (rc == 1) return 1;
    if (rc < 0) {
        if (info.status > 0 && (info.status < 200 || info.status >= 300))
            return -3;
        return -1;
    }
    return 0;
}
