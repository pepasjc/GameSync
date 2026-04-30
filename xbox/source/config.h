// Persistent client config: server URL, API key, console ID.
// Stored on disk at E:\UDATA\TDSV0000\config.txt as `key=value` lines.

#ifndef XBOX_CONFIG_H
#define XBOX_CONFIG_H

#define XBOX_CFG_URL_LEN          256
#define XBOX_CFG_API_KEY_LEN      128
#define XBOX_CFG_CONSOLE_ID_LEN   48
#define XBOX_CFG_IP_LEN           16
#define XBOX_CFG_NET_MODE_LEN     12
#define XBOX_CFG_GAME_FORMAT_LEN  12
#define XBOX_CFG_PATH_LEN         128

typedef struct {
    char server_url[XBOX_CFG_URL_LEN];
    char api_key[XBOX_CFG_API_KEY_LEN];
    char console_id[XBOX_CFG_CONSOLE_ID_LEN];
    char network_mode[XBOX_CFG_NET_MODE_LEN];  // auto, dhcp, static
    char static_ip[XBOX_CFG_IP_LEN];
    char static_netmask[XBOX_CFG_IP_LEN];
    char static_gateway[XBOX_CFG_IP_LEN];
    char static_dns1[XBOX_CFG_IP_LEN];
    char static_dns2[XBOX_CFG_IP_LEN];
    char game_format[XBOX_CFG_GAME_FORMAT_LEN];  // cci or folder
    char game_install_dir[XBOX_CFG_PATH_LEN];     // default F:\Games
} XboxConfig;

// Return codes for config_load:
//   0     loaded existing config OR wrote a sensible default and proceeded
//  -2     I/O error / parse error / required fields missing
int config_load(XboxConfig *cfg, char *err, int err_len);

// Persist config back to disk.
int config_save(const XboxConfig *cfg);

#endif // XBOX_CONFIG_H
