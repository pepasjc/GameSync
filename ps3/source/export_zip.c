#include "export_zip.h"

#include "apollo.h"
#include "sha256.h"
#include "ui.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include <zlib.h>

#define ZIP_EOCD_SIG            0x06054B50UL
#define ZIP_CDIR_SIG            0x02014B50UL
#define ZIP_LOCAL_SIG           0x04034B50UL
#define ZIP_METHOD_STORE        0
#define ZIP_METHOD_DEFLATE      8
#define ZIP_FLAG_ENCRYPTED      0x0001
#define ZIP_FLAG_DATA_DESC      0x0008
#define ZIP_SEARCH_TAIL         (0x10000 + 22)
#define ZIP_IO_CHUNK            32768U

static uint16_t read_le16(const uint8_t *p) {
    return (uint16_t)p[0] | ((uint16_t)p[1] << 8);
}

static uint32_t read_le32(const uint8_t *p) {
    return (uint32_t)p[0]
         | ((uint32_t)p[1] << 8)
         | ((uint32_t)p[2] << 16)
         | ((uint32_t)p[3] << 24);
}

static int compare_entries(const void *a, const void *b) {
    const ExportZipEntry *left = (const ExportZipEntry *)a;
    const ExportZipEntry *right = (const ExportZipEntry *)b;
    return strcmp(left->path, right->path);
}

static bool normalize_zip_path(
    const char *raw_name,
    char *title_id_out,
    size_t title_id_out_size,
    char *rel_path_out,
    size_t rel_path_out_size
) {
    const char *slash;
    size_t prefix_len;
    const char *normalized = raw_name;

    if (!raw_name || !raw_name[0]) {
        return false;
    }

    if (normalized[0] == '.' && (normalized[1] == '/' || normalized[1] == '\\')) {
        normalized += 2;
    }

    slash = strpbrk(normalized, "/\\");
    if (!slash) {
        return false;
    }

    prefix_len = (size_t)(slash - normalized);
    if (prefix_len == 0 || prefix_len >= title_id_out_size) {
        return false;
    }

    memcpy(title_id_out, normalized, prefix_len);
    title_id_out[prefix_len] = '\0';

    if (*(slash + 1) == '\0') {
        return false;
    }

    snprintf(rel_path_out, rel_path_out_size, "%s", slash + 1);
    for (char *p = rel_path_out; *p; p++) {
        if (*p == '\\') {
            *p = '/';
        }
    }
    return true;
}

static bool find_eocd(FILE *fp, long *eocd_offset_out, uint8_t eocd[22]) {
    long file_size;
    long search_start;
    long search_size;
    uint8_t *buf;

    if (fseek(fp, 0, SEEK_END) != 0) {
        return false;
    }
    file_size = ftell(fp);
    if (file_size < 22) {
        return false;
    }

    search_size = file_size < ZIP_SEARCH_TAIL ? file_size : ZIP_SEARCH_TAIL;
    search_start = file_size - search_size;
    if (fseek(fp, search_start, SEEK_SET) != 0) {
        return false;
    }

    buf = (uint8_t *)malloc((size_t)search_size);
    if (!buf) {
        return false;
    }
    if (fread(buf, 1, (size_t)search_size, fp) != (size_t)search_size) {
        free(buf);
        return false;
    }

    for (long i = search_size - 22; i >= 0; i--) {
        if (read_le32(buf + i) == ZIP_EOCD_SIG) {
            memcpy(eocd, buf + i, 22);
            *eocd_offset_out = search_start + i;
            free(buf);
            return true;
        }
    }

    free(buf);
    return false;
}

static bool inflate_entry(FILE *fp, const ExportZipEntry *entry, uint8_t *buf, uint32_t buf_size) {
    z_stream zs;
    uint8_t inbuf[ZIP_IO_CHUNK];
    uint32_t remaining_in = entry->compressed_size;
    uint32_t produced = 0;
    int zr;

    memset(&zs, 0, sizeof(zs));
    if (inflateInit2(&zs, -MAX_WBITS) != Z_OK) {
        return false;
    }

    zs.next_out = buf;
    zs.avail_out = buf_size;

    while (remaining_in > 0) {
        uint32_t chunk = remaining_in > ZIP_IO_CHUNK ? ZIP_IO_CHUNK : remaining_in;
        if (fread(inbuf, 1, chunk, fp) != chunk) {
            inflateEnd(&zs);
            return false;
        }
        remaining_in -= chunk;

        zs.next_in = inbuf;
        zs.avail_in = chunk;
        while (zs.avail_in > 0) {
            zr = inflate(&zs, Z_NO_FLUSH);
            if (zr != Z_OK && zr != Z_STREAM_END) {
                inflateEnd(&zs);
                return false;
            }
            pump_callbacks();
            if (zr == Z_STREAM_END) {
                produced = (uint32_t)zs.total_out;
                inflateEnd(&zs);
                return produced == buf_size;
            }
            if (zs.avail_out == 0 && (uint32_t)zs.total_out < buf_size) {
                zs.next_out = buf + zs.total_out;
                zs.avail_out = buf_size - (uint32_t)zs.total_out;
            }
        }
    }

    zr = inflate(&zs, Z_FINISH);
    produced = (uint32_t)zs.total_out;
    inflateEnd(&zs);
    return zr == Z_STREAM_END && produced == buf_size;
}

static bool read_entry_payload(const char *zip_path, const ExportZipEntry *entry, uint8_t *buf, uint32_t buf_size) {
    FILE *fp;
    uint8_t header[30];
    uint16_t name_len;
    uint16_t extra_len;

    if (!zip_path || !entry || !buf || buf_size != entry->size) {
        return false;
    }

    fp = fopen(zip_path, "rb");
    if (!fp) {
        return false;
    }

    if (fseek(fp, (long)entry->local_header_offset, SEEK_SET) != 0) {
        fclose(fp);
        return false;
    }
    if (fread(header, 1, sizeof(header), fp) != sizeof(header)) {
        fclose(fp);
        return false;
    }
    if (read_le32(header) != ZIP_LOCAL_SIG) {
        fclose(fp);
        return false;
    }

    name_len = read_le16(header + 26);
    extra_len = read_le16(header + 28);
    if (fseek(fp, (long)name_len + (long)extra_len, SEEK_CUR) != 0) {
        fclose(fp);
        return false;
    }

    if (entry->method == ZIP_METHOD_STORE) {
        bool ok = fread(buf, 1, buf_size, fp) == buf_size;
        fclose(fp);
        return ok;
    }

    if (entry->method == ZIP_METHOD_DEFLATE) {
        bool ok = inflate_entry(fp, entry, buf, buf_size);
        fclose(fp);
        return ok;
    }

    fclose(fp);
    return false;
}

bool export_zip_parse(const char *zip_path, ExportZipInfo *info) {
    FILE *fp;
    uint8_t eocd[22];
    long eocd_offset = 0;
    uint32_t cdir_offset;
    uint16_t total_entries;
    char detected_title_id[GAME_ID_LEN] = "";

    if (!zip_path || !info) {
        return false;
    }

    memset(info, 0, sizeof(*info));
    snprintf(info->zip_path, sizeof(info->zip_path), "%s", zip_path);

    fp = fopen(zip_path, "rb");
    if (!fp) {
        return false;
    }

    if (!find_eocd(fp, &eocd_offset, eocd)) {
        fclose(fp);
        return false;
    }

    (void)eocd_offset;
    total_entries = read_le16(eocd + 10);
    cdir_offset = read_le32(eocd + 16);

    if (fseek(fp, (long)cdir_offset, SEEK_SET) != 0) {
        fclose(fp);
        return false;
    }

    for (uint16_t i = 0; i < total_entries && info->file_count < MAX_FILES; i++) {
        uint8_t header[46];
        uint16_t flags;
        uint16_t method;
        uint32_t compressed_size;
        uint32_t size;
        uint16_t name_len;
        uint16_t extra_len;
        uint16_t comment_len;
        uint32_t local_header_offset;
        char raw_name[PATH_LEN];
        char rel_path[MAX_FILE_LEN];
        char title_id[GAME_ID_LEN];

        if (fread(header, 1, sizeof(header), fp) != sizeof(header)) {
            fclose(fp);
            return false;
        }
        if (read_le32(header) != ZIP_CDIR_SIG) {
            fclose(fp);
            return false;
        }

        flags = read_le16(header + 8);
        method = read_le16(header + 10);
        compressed_size = read_le32(header + 20);
        size = read_le32(header + 24);
        name_len = read_le16(header + 28);
        extra_len = read_le16(header + 30);
        comment_len = read_le16(header + 32);
        local_header_offset = read_le32(header + 42);

        if (name_len == 0 || name_len >= sizeof(raw_name)) {
            fclose(fp);
            return false;
        }
        if (fread(raw_name, 1, name_len, fp) != name_len) {
            fclose(fp);
            return false;
        }
        raw_name[name_len] = '\0';
        if (fseek(fp, (long)extra_len + (long)comment_len, SEEK_CUR) != 0) {
            fclose(fp);
            return false;
        }

        if ((flags & ZIP_FLAG_ENCRYPTED) != 0) {
            fclose(fp);
            return false;
        }
        if (raw_name[name_len - 1] == '/') {
            continue;
        }
        if (!normalize_zip_path(raw_name, title_id, sizeof(title_id), rel_path, sizeof(rel_path))) {
            continue;
        }
        if (!apollo_is_ps3_save_dir(title_id)) {
            continue;
        }
        if (detected_title_id[0] == '\0') {
            snprintf(detected_title_id, sizeof(detected_title_id), "%s", title_id);
        } else if (strcmp(detected_title_id, title_id) != 0) {
            fclose(fp);
            return false;
        }
        if (method != ZIP_METHOD_STORE && method != ZIP_METHOD_DEFLATE) {
            fclose(fp);
            return false;
        }

        ExportZipEntry *out = &info->files[info->file_count++];
        memset(out, 0, sizeof(*out));
        snprintf(out->path, sizeof(out->path), "%s", rel_path);
        out->size = size;
        out->compressed_size = compressed_size;
        out->method = method;
        out->flags = flags;
        out->local_header_offset = local_header_offset;
        info->total_size += size;
    }

    fclose(fp);

    if (info->file_count == 0 || detected_title_id[0] == '\0') {
        return false;
    }

    qsort(info->files, (size_t)info->file_count, sizeof(info->files[0]), compare_entries);
    snprintf(info->title_id, sizeof(info->title_id), "%s", detected_title_id);
    return true;
}

int export_zip_list_files(const char *zip_path, char names[][MAX_FILE_LEN], uint32_t *sizes, int max_files) {
    ExportZipInfo *info;
    int result;

    if (!zip_path || !names || !sizes || max_files <= 0) {
        return -1;
    }
    info = (ExportZipInfo *)malloc(sizeof(*info));
    if (!info) {
        return -1;
    }
    if (!export_zip_parse(zip_path, info)) {
        free(info);
        return -1;
    }

    if (info->file_count > max_files) {
        free(info);
        return -1;
    }

    for (int i = 0; i < info->file_count; i++) {
        snprintf(names[i], MAX_FILE_LEN, "%s", info->files[i].path);
        sizes[i] = info->files[i].size;
    }
    result = info->file_count;
    free(info);
    return result;
}

bool export_zip_read_file(
    const char *zip_path,
    const char *name,
    uint8_t *buf,
    uint32_t buf_size,
    uint32_t *bytes_read_out
) {
    ExportZipInfo *info;
    bool ok = false;

    if (bytes_read_out) {
        *bytes_read_out = 0;
    }
    if (!zip_path || !name || !buf) {
        return false;
    }
    info = (ExportZipInfo *)malloc(sizeof(*info));
    if (!info) {
        return false;
    }
    if (!export_zip_parse(zip_path, info)) {
        free(info);
        return false;
    }

    for (int i = 0; i < info->file_count; i++) {
        if (strcmp(info->files[i].path, name) != 0) {
            continue;
        }
        if (buf_size != info->files[i].size) {
            break;
        }
        if (!read_entry_payload(zip_path, &info->files[i], buf, buf_size)) {
            break;
        }
        if (bytes_read_out) {
            *bytes_read_out = info->files[i].size;
        }
        ok = true;
        break;
    }

    free(info);
    return ok;
}

bool export_zip_hash_files_sha256(
    const char *zip_path,
    uint8_t hash_out[32],
    int *file_count_out,
    uint32_t *total_size_out
) {
    ExportZipInfo *info;
    SHA256_CTX ctx;
    uint8_t *buf;

    if (!zip_path || !hash_out) {
        return false;
    }
    info = (ExportZipInfo *)malloc(sizeof(*info));
    if (!info) {
        return false;
    }
    if (!export_zip_parse(zip_path, info)) {
        free(info);
        return false;
    }

    sha256_init(&ctx);
    buf = NULL;

    for (int i = 0; i < info->file_count; i++) {
        ui_status("Hashing export %d/%d: %s", i + 1, info->file_count, info->files[i].path);
        if (info->files[i].size > 0) {
            buf = (uint8_t *)malloc(info->files[i].size);
            if (!buf) {
                free(info);
                return false;
            }
            if (!read_entry_payload(zip_path, &info->files[i], buf, info->files[i].size)) {
                free(buf);
                free(info);
                return false;
            }
            sha256_update(&ctx, buf, info->files[i].size);
            free(buf);
        }
        pump_callbacks();
    }

    sha256_final(&ctx, hash_out);
    if (file_count_out) {
        *file_count_out = info->file_count;
    }
    if (total_size_out) {
        *total_size_out = info->total_size;
    }
    free(info);
    return true;
}
