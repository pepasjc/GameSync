#include "ra_hash.h"
#include "md5.h"
#include <stdio.h>
#include <stdint.h>
#include <string.h>

// Port of rc_hash_nintendo_ds() from rcheevos src/rhash/hash_rom.c (MIT),
// streamed in chunks instead of one buffer sized to the ARM9 binary.

#define RA_HASH_CHUNK 0x8000
#define RA_ICON_SIZE 0xA00

static uint8_t chunk_buf[RA_HASH_CHUNK];

static uint32_t read_u32le(const uint8_t *p) {
    return (uint32_t)p[0] | ((uint32_t)p[1] << 8) | ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24);
}

// Hash `size` bytes at `pos`. rcheevos hashes the full size even if the
// file is short; the tail is zero-filled here (such a ROM can't match RA).
static void hash_region(md5_state_t *md5, FILE *f, long pos, uint32_t size) {
    bool eof = (fseek(f, pos, SEEK_SET) != 0);
    while (size > 0) {
        uint32_t n = size < RA_HASH_CHUNK ? size : RA_HASH_CHUNK;
        size_t got = eof ? 0 : fread(chunk_buf, 1, n, f);
        if (got < n) {
            memset(chunk_buf + got, 0, n - got);
            eof = true;
        }
        md5_append(md5, chunk_buf, (int)n);
        size -= n;
    }
}

bool ra_hash_nds_file(const char *path, char hash_out[33]) {
    uint8_t header[512];
    long offset = 0;

    hash_out[0] = '\0';
    FILE *f = fopen(path, "rb");
    if (!f) return false;

    if (fread(header, 1, sizeof(header), f) != sizeof(header)) {
        fclose(f);
        return false;
    }

    // SuperCard wrapper: skip its 512-byte header
    if (header[0] == 0x2E && header[1] == 0x00 && header[2] == 0x00 && header[3] == 0xEA &&
        header[0xB0] == 0x44 && header[0xB1] == 0x46 && header[0xB2] == 0x96 && header[0xB3] == 0) {
        offset = 512;
        fseek(f, offset, SEEK_SET);
        if (fread(header, 1, sizeof(header), f) != sizeof(header)) {
            fclose(f);
            return false;
        }
    }

    uint32_t arm9_addr = read_u32le(header + 0x20);
    uint32_t arm9_size = read_u32le(header + 0x2C);
    uint32_t arm7_addr = read_u32le(header + 0x30);
    uint32_t arm7_size = read_u32le(header + 0x3C);
    uint32_t icon_addr = read_u32le(header + 0x68);

    // Same sanity check as rcheevos (the sum is 32-bit there too)
    if ((uint32_t)(arm9_size + arm7_size) > 16 * 1024 * 1024) {
        fclose(f);
        return false;
    }

    md5_state_t md5;
    md5_init(&md5);
    md5_append(&md5, header, 0x160);
    hash_region(&md5, f, (long)arm9_addr + offset, arm9_size);
    hash_region(&md5, f, (long)arm7_addr + offset, arm7_size);
    // Homebrew may end inside the icon block; it is zero-padded to 0xA00
    hash_region(&md5, f, (long)icon_addr + offset, RA_ICON_SIZE);
    fclose(f);

    md5_byte_t digest[16];
    md5_finish(&md5, digest);
    for (int i = 0; i < 16; i++) {
        snprintf(hash_out + i * 2, 3, "%02x", digest[i]);
    }
    hash_out[32] = '\0';
    return true;
}
