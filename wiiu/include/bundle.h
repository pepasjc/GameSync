#ifndef WIIUSYNC_BUNDLE_H
#define WIIUSYNC_BUNDLE_H

/*
 * 3DSS save bundle — v5 (string title_id, zlib-compressed).
 *
 * Wire format (matches server/app/services/bundle.py):
 *
 *   [4]   Magic "3DSS"
 *   [4]   Version = 5 (uint32 LE)
 *   [64]  Title ID (ASCII, null-padded)
 *   [4]   Timestamp — unix epoch (uint32 LE)
 *   [4]   File count (uint32 LE)
 *   [4]   Uncompressed payload size (uint32 LE)
 *   [...] zlib-compressed payload:
 *           File table (file_count entries):
 *             [2]  path length (uint16 LE)
 *             [N]  path (UTF-8)
 *             [4]  file size (uint32 LE)
 *             [32] file SHA-256
 *           File data (concatenated in the same order)
 *
 * Ported from the Xbox client; the only structural change is that a save's
 * files hang off a caller-supplied base directory (SaveTitle.root) instead of
 * a hard-coded E:\UDATA\<id>, so the same code serves vWii NAND and Wii U MLC.
 */

#include "savetree.h"

#include <stddef.h>
#include <stdint.h>

#define BUNDLE_MAGIC          "3DSS"
#define BUNDLE_VERSION_V5     5
#define BUNDLE_HEADER_SIZE_V5 84   /* 4+4+64+4+4+4 */

typedef int (*BundleWriteFn)(void *ctx, const uint8_t *data, size_t size);

/* Streaming encoder: emits a v5 compressed bundle to ``write`` without ever
 * holding the save, payload or compressed bundle in memory.
 * ``save_hash_hex`` (>= 65 bytes, may be NULL) receives the canonical save
 * hash — SHA-256 over every file's contents in file-table order. */
int bundle_stream_create(const SaveTitle *title,
                         uint32_t timestamp,
                         BundleWriteFn write,
                         void *write_ctx,
                         char *save_hash_hex);

/* Canonical save hash for a title (same value the server compares against).
 * ``hash_hex`` (>= 65 bytes) may be NULL. */
int bundle_compute_save_hash(const SaveTitle *title,
                             uint8_t hash[32],
                             char *hash_hex);

/* Apply a downloaded bundle file under ``dest_root`` without materialising
 * the compressed bundle or the file data in RAM.  Parent directories are
 * created; existing files are overwritten (the server keeps history).
 * Each file's SHA-256 is verified as it is written. */
int bundle_apply_file_to_disk(const char *bundle_path, const char *dest_root);

#endif /* WIIUSYNC_BUNDLE_H */
