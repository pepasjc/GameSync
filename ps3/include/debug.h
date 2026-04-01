#ifndef PS3SYNC_DEBUG_H
#define PS3SYNC_DEBUG_H

#include <stdbool.h>

bool debug_log_open(void);
void debug_log_close(void);
void debug_log(const char *fmt, ...);

#endif /* PS3SYNC_DEBUG_H */
