// SHA-256 (FIPS 180-4). Pure C, little-endian-safe — same code we ship on the
// 3DS, NDS, and PS3 clients. Streaming API (init / update / final) plus a
// one-shot helper.

#ifndef XBOX_SHA256_H
#define XBOX_SHA256_H

#include <stddef.h>
#include <stdint.h>

typedef struct {
    uint32_t state[8];
    uint64_t count;       // total bytes consumed so far
    uint8_t  buffer[64];  // partial block accumulator
} SHA256_CTX;

void sha256_init(SHA256_CTX *ctx);
void sha256_update(SHA256_CTX *ctx, const uint8_t *data, size_t len);
void sha256_final(SHA256_CTX *ctx, uint8_t hash[32]);

// Convenience: hash a buffer in one call.
void sha256(const uint8_t *data, size_t len, uint8_t hash[32]);

#endif // XBOX_SHA256_H
