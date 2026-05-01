// 3DSS save bundle - v5 (string title_id, zlib-compressed).
//
// Wire format (matches server/app/services/bundle.py):
//
//   [4]   Magic "3DSS"
//   [4]   Version = 5 (uint32 LE)
//   [64]  Title ID (ASCII, null-padded)
//   [4]   Timestamp - unix epoch (uint32 LE)
//   [4]   File count (uint32 LE)
//   [4]   Uncompressed payload size (uint32 LE)
//   [...] zlib-compressed payload:
//           File table (file_count entries):
//             [2]  path length (uint16 LE)
//             [N]  path (UTF-8)
//             [4]  file size (uint32 LE)
//             [32] file SHA-256
//           File data (concatenated in same order):
//             [N]  raw file bytes
//
// We always emit v5 (server happily parses v4 too, but v5 keeps the wire
// format aligned with the PS3 client's longer-id-aware path and gives us
// future room without another bump).

#ifndef XBOX_BUNDLE_H
#define XBOX_BUNDLE_H

#include <stddef.h>
#include <stdint.h>

#include "saves.h"

#define BUNDLE_MAGIC               "3DSS"
#define BUNDLE_VERSION_V5          5
#define BUNDLE_HEADER_SIZE_V5      84   // 4+4+64+4+4+4

// Build a v5 bundle from a populated XboxSaveTitle (already enumerated by
// saves_scan). Reads each file from disk, hashes it, builds the payload,
// zlib-deflates, and assembles the header.
//
// Returns 0 on success and writes a malloc()'d buffer to *out_data plus its
// size to *out_size; caller frees with free(). On failure returns negative.
int bundle_create(const XboxSaveTitle *title,
                  uint32_t timestamp,
                  uint8_t **out_data,
                  uint32_t *out_size);

typedef int (*BundleWriteFn)(void *ctx, const uint8_t *data, size_t size);

// Streaming variant of bundle_create(). Emits the same v5 compressed bundle
// to ``write`` without holding the whole save, payload, or compressed bundle
// in memory. ``save_hash_hex`` may be NULL; when provided it receives the
// canonical 64-char save hash.
int bundle_stream_create(const XboxSaveTitle *title,
                         uint32_t timestamp,
                         BundleWriteFn write,
                         void *write_ctx,
                         char *save_hash_hex);

// Convenience: compute the SHA-256 of a contiguous buffer in `chunk`-sized
// passes so we never miss a chance to yield. ``chunk`` of 0 means "one
// shot". hash[] must be 32 bytes.
void bundle_hash_buffer(const uint8_t *data, uint32_t size,
                        uint32_t chunk, uint8_t hash[32]);

// Compute the canonical "save hash" for a title - SHA-256 of every save
// file's contents concatenated in the order returned by saves_scan().
// This matches the hash the server will compare against (it hashes file
// data, not the bundle envelope).
//
// Returns 0 on success and writes 32 bytes to ``hash``. ``hash_hex`` (if
// non-NULL) receives the lowercase 64-char hex digest plus NUL terminator
// (so its buffer must be >= 65 bytes).
int bundle_compute_save_hash(const XboxSaveTitle *title,
                             uint8_t hash[32],
                             char *hash_hex);

// ---------------------------------------------------------------------------
// Bundle parser (download path)
// ---------------------------------------------------------------------------

typedef struct {
    char     relative_path[XBOX_PATH_MAX];
    uint32_t size;
    uint8_t  sha256[32];
    uint8_t *data;       // owned by ParsedBundle; freed by bundle_parsed_free
} ParsedBundleFile;

typedef struct {
    char              title_id[65];   // up to 64 ASCII + NUL
    uint32_t          timestamp;
    int               file_count;
    ParsedBundleFile *files;          // file_count entries
} ParsedBundle;

// Decode a v5 bundle (matches bundle_create's output, also accepts v4 with
// a 32-byte title_id field). Returns 0 on success; on failure ``out`` is
// left zeroed and any partial allocation is freed.
int bundle_parse(const uint8_t *data, uint32_t size, ParsedBundle *out);

// Release everything owned by a ParsedBundle.
void bundle_parsed_free(ParsedBundle *pb);

// Write a parsed bundle's files back under E:\UDATA\<title_id>\<paths...>.
// Creates parent directories as needed. Files that already exist are
// overwritten (the server holds the prior version in its history dir).
//
// Returns 0 on success.
int bundle_apply_to_disk(const ParsedBundle *pb, const char *udata_title_id);

// Apply a downloaded bundle from disk without materialising the compressed
// bundle, decompressed payload, or file data in RAM. Intended for large saves.
int bundle_apply_file_to_disk(const char *bundle_path, const char *udata_title_id);

#endif // XBOX_BUNDLE_H
