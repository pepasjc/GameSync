#include "hash.h"

#include "common.h"
#include "sha256.h"
#include "ui.h"

#include <ctype.h>
#include <dirent.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <sys/stat.h>

typedef struct {
    char full_path[PATH_LEN];
    char rel_path[PATH_LEN];
} HashFileEntry;

#define PS1CARD_RAW_SIZE    0x20000U
#define PS1CARD_VMP_SIZE    (PS1CARD_RAW_SIZE + 0x80U)
#define PS1CARD_VMP_OFFSET  0x80U

static bool is_dot_name(const char *name) {
    return strcmp(name, ".") == 0 || strcmp(name, "..") == 0;
}

static void path_join(const char *base, const char *name, char *out, size_t out_size) {
    const size_t base_len = strlen(base);
    snprintf(
        out,
        out_size,
        "%s%s%s",
        base,
        (base_len > 0 && base[base_len - 1] == '/') ? "" : "/",
        name
    );
}

static void rel_join(const char *base, const char *name, char *out, size_t out_size) {
    if (!base || base[0] == '\0') {
        snprintf(out, out_size, "%s", name);
        return;
    }
    snprintf(out, out_size, "%s/%s", base, name);
}

static int compare_entries(const void *a, const void *b) {
    const HashFileEntry *left = (const HashFileEntry *)a;
    const HashFileEntry *right = (const HashFileEntry *)b;
    return strcmp(left->rel_path, right->rel_path);
}

bool hash_should_skip_ps3_file(const char *rel_path) {
    const char *name;
    const char *ext;

    if (!rel_path || !rel_path[0]) {
        return false;
    }

    name = strrchr(rel_path, '/');
    name = name ? (name + 1) : rel_path;

    if (strcasecmp(name, "PARAM.SFO") == 0 || strcasecmp(name, "PARAM.PFD") == 0) {
        return true;
    }

    ext = strrchr(name, '.');
    return ext && strcasecmp(ext, ".PNG") == 0;
}

static bool append_entry(
    HashFileEntry **entries,
    size_t *count,
    size_t *capacity,
    const char *full_path,
    const char *rel_path
) {
    HashFileEntry *new_entries;

    if (*count == *capacity) {
        size_t new_capacity = (*capacity == 0) ? 16 : (*capacity * 2);
        new_entries = (HashFileEntry *)realloc(*entries, new_capacity * sizeof(HashFileEntry));
        if (!new_entries) {
            return false;
        }
        *entries = new_entries;
        *capacity = new_capacity;
    }

    snprintf((*entries)[*count].full_path, sizeof((*entries)[*count].full_path), "%s", full_path);
    snprintf((*entries)[*count].rel_path, sizeof((*entries)[*count].rel_path), "%s", rel_path);
    (*count)++;
    return true;
}

static bool collect_dir_entries(
    const char *root_path,
    const char *rel_path,
    HashFileEntry **entries,
    size_t *count,
    size_t *capacity
) {
    DIR *dir;
    struct dirent *entry;

    dir = opendir(root_path);
    if (!dir) {
        return false;
    }

    while ((entry = readdir(dir)) != NULL) {
        char child_full[PATH_LEN];
        char child_rel[PATH_LEN];
        struct stat st;

        if (is_dot_name(entry->d_name)) {
            continue;
        }

        path_join(root_path, entry->d_name, child_full, sizeof(child_full));
        rel_join(rel_path, entry->d_name, child_rel, sizeof(child_rel));

        if (stat(child_full, &st) != 0) {
            continue;
        }

        if (S_ISDIR(st.st_mode)) {
            if (!collect_dir_entries(child_full, child_rel, entries, count, capacity)) {
                closedir(dir);
                return false;
            }
        } else if (S_ISREG(st.st_mode)) {
            if (hash_should_skip_ps3_file(child_rel)) {
                continue;
            }
            if (!append_entry(entries, count, capacity, child_full, child_rel)) {
                closedir(dir);
                return false;
            }
        }
    }

    closedir(dir);
    return true;
}

static bool hash_file_stream(FILE *fp, SHA256_CTX *ctx, uint32_t *size_out) {
    uint8_t buffer[8192];
    size_t bytes;
    uint32_t total = 0;

    while ((bytes = fread(buffer, 1, sizeof(buffer), fp)) > 0) {
        uint32_t chunk = (uint32_t)bytes;
        uint32_t next = total + chunk;
        if (next < total) {
            total = 0xFFFFFFFFU;
        } else {
            total = next;
        }
        sha256_update(ctx, buffer, bytes);
        pump_callbacks();
    }

    if (ferror(fp)) {
        return false;
    }

    if (size_out) {
        *size_out = total;
    }
    return true;
}

bool hash_file_sha256(const char *path, uint8_t hash_out[32], uint32_t *size_out) {
    FILE *fp = fopen(path, "rb");
    SHA256_CTX ctx;

    if (!fp) {
        return false;
    }

    ui_status("Hashing file: %s", path);
    sha256_init(&ctx);
    if (!hash_file_stream(fp, &ctx, size_out)) {
        fclose(fp);
        return false;
    }
    fclose(fp);

    sha256_final(&ctx, hash_out);
    return true;
}

bool hash_ps1_card_sha256(const char *path, uint8_t hash_out[32], uint32_t *size_out) {
    FILE *fp = fopen(path, "rb");
    uint8_t *data = NULL;
    SHA256_CTX ctx;
    long file_size;
    const uint8_t *hash_data;
    uint32_t hash_size;
    bool ok = false;

    if (!fp) {
        return false;
    }

    if (fseek(fp, 0, SEEK_END) != 0) {
        fclose(fp);
        return false;
    }
    file_size = ftell(fp);
    if (file_size < 0 || fseek(fp, 0, SEEK_SET) != 0) {
        fclose(fp);
        return false;
    }

    data = (uint8_t *)malloc((size_t)file_size);
    if (!data) {
        fclose(fp);
        return false;
    }

    if (file_size > 0 && fread(data, 1, (size_t)file_size, fp) != (size_t)file_size) {
        free(data);
        fclose(fp);
        return false;
    }
    fclose(fp);

    hash_data = data;
    hash_size = (uint32_t)file_size;

    /* Match the server's PS1 comparable-hash rule:
       - raw cards hash as-is
       - VMP cards hash only their 128 KiB raw payload */
    if ((uint32_t)file_size == PS1CARD_RAW_SIZE &&
        memcmp(data, "MC\0\0", 4) == 0) {
        hash_data = data;
        hash_size = PS1CARD_RAW_SIZE;
    } else if ((uint32_t)file_size >= PS1CARD_VMP_SIZE &&
               memcmp(data, "\0PMV", 4) == 0 &&
               memcmp(data + PS1CARD_VMP_OFFSET, "MC\0\0", 4) == 0) {
        hash_data = data + PS1CARD_VMP_OFFSET;
        hash_size = PS1CARD_RAW_SIZE;
    }

    ui_status("Hashing PS1 card: %s", path);
    sha256_init(&ctx);
    sha256_update(&ctx, hash_data, hash_size);
    pump_callbacks();
    sha256_final(&ctx, hash_out);

    if (size_out) {
        *size_out = hash_size;
    }
    ok = true;

    free(data);
    return ok;
}

bool hash_dir_files_sha256(
    const char *path,
    uint8_t hash_out[32],
    int *file_count_out,
    uint32_t *total_size_out
) {
    HashFileEntry *entries = NULL;
    size_t count = 0;
    size_t capacity = 0;
    SHA256_CTX ctx;
    uint32_t total_size = 0;
    size_t i;

    if (!collect_dir_entries(path, "", &entries, &count, &capacity)) {
        free(entries);
        return false;
    }

    ui_status("Found %d files to hash", (int)count);
    qsort(entries, count, sizeof(HashFileEntry), compare_entries);

    sha256_init(&ctx);
    for (i = 0; i < count; i++) {
        FILE *fp = fopen(entries[i].full_path, "rb");
        uint32_t file_size = 0;
        uint32_t next_total;

        if (!fp) {
            free(entries);
            return false;
        }
        ui_status("Hashing file %d/%d: %s", (int)i + 1, (int)count, entries[i].rel_path);
        if (!hash_file_stream(fp, &ctx, &file_size)) {
            fclose(fp);
            free(entries);
            return false;
        }
        fclose(fp);
        ui_status("Finished file %d/%d: %s", (int)i + 1, (int)count, entries[i].rel_path);

        next_total = total_size + file_size;
        if (next_total < total_size) {
            total_size = 0xFFFFFFFFU;
        } else {
            total_size = next_total;
        }
        pump_callbacks();
    }

    ui_status("Finalizing combined hash");
    sha256_final(&ctx, hash_out);
    if (file_count_out) {
        *file_count_out = (int)count;
    }
    if (total_size_out) {
        *total_size_out = total_size;
    }

    ui_status("Finished combined hash");
    free(entries);
    return true;
}

bool hash_from_hex(const char *hex, uint8_t hash_out[32]) {
    size_t i;

    if (!hex || strlen(hex) != 64) {
        return false;
    }

    for (i = 0; i < 32; i++) {
        char hi = (char)tolower((unsigned char)hex[i * 2]);
        char lo = (char)tolower((unsigned char)hex[i * 2 + 1]);
        int hi_val;
        int lo_val;

        if (!isxdigit((unsigned char)hi) || !isxdigit((unsigned char)lo)) {
            return false;
        }
        hi_val = (hi <= '9') ? (hi - '0') : (hi - 'a' + 10);
        lo_val = (lo <= '9') ? (lo - '0') : (lo - 'a' + 10);
        hash_out[i] = (uint8_t)((hi_val << 4) | lo_val);
    }

    return true;
}

void hash_to_hex(const uint8_t hash[32], char hash_hex_out[65]) {
    static const char HEX[] = "0123456789abcdef";
    size_t i;

    for (i = 0; i < 32; i++) {
        hash_hex_out[i * 2] = HEX[(hash[i] >> 4) & 0x0F];
        hash_hex_out[i * 2 + 1] = HEX[hash[i] & 0x0F];
    }
    hash_hex_out[64] = '\0';
}
