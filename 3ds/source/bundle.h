#ifndef BUNDLE_H
#define BUNDLE_H

#include "common.h"
#include "archive.h"

#define BUNDLE_MAGIC "3DSS"
#define BUNDLE_VERSION 1
#define BUNDLE_VERSION_COMPRESSED 2

// Create a compressed binary bundle from archive files.
// Returns malloc'd buffer (caller must free), sets out_size.
// Returns NULL on failure.
u8 *bundle_create(u64 title_id, u32 timestamp,
                  const ArchiveFile *files, int file_count,
                  u32 *out_size);

// Parse a binary bundle into archive files.
// Supports both v1 (uncompressed) and v2 (compressed) formats.
// Returns number of files parsed, fills files array.
// If bundle is compressed, *out_decompressed is set to malloc'd buffer
// that contains decompressed data - caller must free it.
// If bundle is uncompressed, *out_decompressed is NULL and file data
// points into bundle_data.
// Returns -1 on error.
int bundle_parse(const u8 *bundle_data, u32 bundle_size,
                 u64 *out_title_id, u32 *out_timestamp,
                 ArchiveFile *files, int max_files,
                 u8 **out_decompressed);

// Compute SHA-256 hash of all save data (for sync comparison).
// Hashes the concatenation of all file contents in order.
void bundle_compute_save_hash(const ArchiveFile *files, int file_count,
                              char *hex_out); // 65 bytes: 64 hex + null

#endif // BUNDLE_H
