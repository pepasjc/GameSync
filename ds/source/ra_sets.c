#include "ra_sets.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

size_t ra_sets_request(char *out, size_t cap, const char (*md5s)[33], int count) {
    size_t len = 0;
    int n = snprintf(out, cap, "{\"md5s\":[");
    if (n < 0 || (size_t)n >= cap) return 0;
    len = (size_t)n;
    for (int i = 0; i < count; i++) {
        n = snprintf(out + len, cap - len, "%s\"%.32s\"", i ? "," : "", md5s[i]);
        if (n < 0 || (size_t)n >= cap - len) return 0;
        len += (size_t)n;
    }
    n = snprintf(out + len, cap - len, "]}");
    if (n < 0 || (size_t)n >= cap - len) return 0;
    return len + (size_t)n;
}

static bool is_md5(const char *s, size_t len) {
    if (len != 32) return false;
    for (size_t i = 0; i < 32; i++) {
        char c = s[i];
        if (!((c >= '0' && c <= '9') || (c >= 'a' && c <= 'f'))) return false;
    }
    return true;
}

bool ra_sets_parse(const char *body, size_t len, RaBatchFn fn, void *user) {
    static const char magic[] = "RASETS\t1\n";
    if (len < sizeof(magic) - 1 || memcmp(body, magic, sizeof(magic) - 1) != 0) return false;
    size_t pos = sizeof(magic) - 1;

    while (pos < len) {
        const char *line = body + pos;
        const char *nl = memchr(line, '\n', len - pos);
        if (!nl) return false;
        size_t line_len = (size_t)(nl - line);
        pos += line_len + 1;

        if (line_len == 3 && memcmp(line, "END", 3) == 0) return true;
        if (line_len < 4 + 32 + 2) return false;

        char md5[33];
        memcpy(md5, line + 4, 32);
        md5[32] = '\0';
        if (!is_md5(md5, 32) || line[36] != ' ') return false;
        const char *rest = line + 37;
        size_t rest_len = line_len - 37;

        if (memcmp(line, "=== ", 4) == 0) {
            char num[16];
            if (rest_len == 0 || rest_len >= sizeof(num)) return false;
            memcpy(num, rest, rest_len);
            num[rest_len] = '\0';
            char *end;
            unsigned long n = strtoul(num, &end, 10);
            if (*end != '\0' || n > len - pos) return false;
            fn(RA_BATCH_SET, md5, body + pos, (size_t)n, user);
            pos += n;
        } else if (memcmp(line, "--- ", 4) == 0) {
            if (rest_len == 7 && memcmp(rest, "unknown", 7) == 0) {
                fn(RA_BATCH_UNKNOWN, md5, NULL, 0, user);
            } else if (rest_len >= 5 && memcmp(rest, "error", 5) == 0) {
                char reason[96];
                size_t r = rest_len > 6 ? rest_len - 6 : 0;
                if (r >= sizeof(reason)) r = sizeof(reason) - 1;
                if (r) memcpy(reason, rest + 6, r);
                reason[r] = '\0';
                fn(RA_BATCH_ERROR, md5, reason, r, user);
            } else {
                return false;
            }
        } else {
            return false;
        }
    }
    return false;  // no END: cut off
}
