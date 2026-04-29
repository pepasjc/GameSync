/*
 * zip_extract.h — minimal ZIP_STORED archive extractor.
 *
 * The server emits ZIP_STORED archives for both PS3 bundles and PS1 CHD
 * conversions (via ``?extract=cue``).  ZIP_STORED files have no
 * compression, so we can extract by walking Local File Headers and
 * copying raw bytes — no zlib, no central directory parsing.
 *
 * Scope is intentionally limited: ZIP_STORED only, no encryption, no
 * ZIP64 (server-side ``zipfile.ZipFile`` falls back to ZIP64 only when a
 * single member exceeds 4 GiB, which we don't expect for PS1 cue/bin
 * sets).  Path traversal is rejected (matches the server's safety
 * check).
 */

#ifndef PS3SYNC_ZIP_EXTRACT_H
#define PS3SYNC_ZIP_EXTRACT_H

#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>

/* Extract every ZIP_STORED member of ``zip_path`` into ``out_dir``.
 *
 * Returns true on success.  On failure, ``error_out`` (optional) is
 * filled with a human-readable reason ("Bad signature", "ZIP64 not
 * supported", "Path traversal", "Read error", etc.).
 *
 * The output directory is created if missing.  Members with relative
 * subpaths (e.g. ``DLC/Foo.bin``) get their parent dirs created on the
 * fly; a single mkdir-walk per path component, no recursion. */
bool zip_extract_stored(const char *zip_path, const char *out_dir,
                        char *error_out, size_t error_out_size);

#endif /* PS3SYNC_ZIP_EXTRACT_H */
