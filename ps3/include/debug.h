#ifndef PS3SYNC_DEBUG_H
#define PS3SYNC_DEBUG_H

#include <stdbool.h>

bool debug_log_open(void);
void debug_log_close(void);
void debug_log(const char *fmt, ...);
void debug_dump_path_stat(const char *label, const char *path);
void debug_dump_savedata_permissions(const char *savedata_root, const char *target_path);

#endif /* PS3SYNC_DEBUG_H */
