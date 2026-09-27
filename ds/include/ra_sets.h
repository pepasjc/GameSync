#ifndef RA_SETS_H
#define RA_SETS_H

// POST /api/v1/ra/sets: request body and response parsing. Plain C, no
// libnds, so it also builds on a PC.
//
// Response:
//   RASETS<TAB>1
//   === <md5> <byte length>      followed by exactly that many bytes (a set)
//   --- <md5> unknown            RetroAchievements doesn't know the hash
//   --- <md5> error <reason>     couldn't be fetched this time
//   END

#include <stdbool.h>
#include <stddef.h>

typedef enum {
    RA_BATCH_SET,
    RA_BATCH_UNKNOWN,
    RA_BATCH_ERROR,
} RaBatchKind;

// For RA_BATCH_SET `data`/`len` is the set text; for RA_BATCH_ERROR the
// reason (NUL-terminated copy, len = strlen); NULL/0 for RA_BATCH_UNKNOWN.
typedef void (*RaBatchFn)(RaBatchKind kind, const char *md5, const char *data, size_t len, void *user);

// Writes {"md5s":["...","..."]} into out. Returns its length, or 0 if it
// doesn't fit.
size_t ra_sets_request(char *out, size_t cap, const char (*md5s)[33], int count);

// Calls fn for every item in body. Returns true if the body was well formed
// up to END; items before a problem are still reported.
bool ra_sets_parse(const char *body, size_t len, RaBatchFn fn, void *user);

#endif
