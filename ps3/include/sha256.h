#ifndef PS3SYNC_SHA256_H
#define PS3SYNC_SHA256_H

#include <stddef.h>
#include <stdint.h>

typedef struct {
    uint32_t state[8];
    uint64_t count;
    uint8_t buffer[64];
} SHA256_CTX;

void sha256_init(SHA256_CTX *ctx);
void sha256_update(SHA256_CTX *ctx, const uint8_t *data, size_t len);
void sha256_final(SHA256_CTX *ctx, uint8_t hash[32]);
void sha256(const uint8_t *data, size_t len, uint8_t hash[32]);

#endif /* PS3SYNC_SHA256_H */
