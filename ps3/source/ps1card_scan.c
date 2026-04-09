#include "ps1card_scan.h"

#include "apollo.h"
#include "debug.h"

#include <ctype.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define PS1CARD_MAX_SLOTS_LOCAL   15
#define PS1CARD_HEADER_SIZE       128
#define PS1CARD_BLOCK_SIZE        8192
#define PS1CARD_SIZE              131072

static bool detect_card_offset(const uint8_t *data, size_t size, size_t *offset_out) {
    if (!data || !offset_out) {
        return false;
    }

    if (size >= PS1CARD_SIZE && memcmp(data, "MC", 2) == 0) {
        *offset_out = 0;
        return true;
    }
    if (size == PS1CARD_SIZE) {
        *offset_out = 0;
        return true;
    }
    if (size >= 0x20F40 && memcmp(data, "123-456-STD", 11) == 0) {
        *offset_out = 3904;
        return true;
    }
    if (size >= 0x20040 && memcmp(data, "VgsM", 4) == 0) {
        *offset_out = 64;
        return true;
    }
    if (size >= 0x20080 && memcmp(data, "\0PMV", 4) == 0) {
        *offset_out = 128;
        return true;
    }

    return false;
}

static void sanitize_ascii_name(
    const uint8_t *src,
    size_t src_len,
    char *out,
    size_t out_size
) {
    size_t j = 0;

    if (!out || out_size == 0) {
        return;
    }

    for (size_t i = 0; i < src_len && j + 1 < out_size; i++) {
        unsigned char c = src[i];
        if (c == '\0') {
            break;
        }
        if (c < 0x20 || c > 0x7E) {
            continue;
        }
        out[j++] = (char)c;
    }

    while (j > 0 && out[j - 1] == ' ') {
        j--;
    }
    out[j] = '\0';
}

static bool normalize_title_id(const uint8_t *src, char *out, size_t out_size) {
    size_t j = 0;

    if (!src || !out || out_size < 10) {
        return false;
    }

    for (size_t i = 0; i < 10 && j + 1 < out_size; i++) {
        unsigned char c = src[i];
        if (c == '\0' || c == ' ') {
            continue;
        }
        c = (unsigned char)toupper(c);
        if (c == '-') {
            continue;
        }
        if (!isalnum(c)) {
            continue;
        }
        out[j++] = (char)c;
    }
    out[j] = '\0';

    if (j < 9) {
        return false;
    }

    return apollo_detect_save_kind(out) == SAVE_KIND_PS1;
}

static void format_prod_bytes(const uint8_t *src, char *out, size_t out_size) {
    size_t pos = 0;
    if (!src || !out || out_size == 0) {
        return;
    }
    out[0] = '\0';
    for (size_t i = 0; i < 10 && pos + 4 < out_size; i++) {
        unsigned char c = src[i];
        if (c >= 0x20 && c <= 0x7E) {
            pos += (size_t)snprintf(out + pos, out_size - pos, "%c", c);
        } else {
            pos += (size_t)snprintf(out + pos, out_size - pos, "\\x%02X", c);
        }
    }
}

int ps1card_scan_file(const char *path, Ps1CardEntry *entries, int max_entries) {
    uint8_t *data = NULL;
    long file_size = 0;
    size_t offset = 0;
    const uint8_t *raw;
    int count = 0;
    FILE *fp;

    if (!path || !entries || max_entries <= 0) {
        return -1;
    }

    fp = fopen(path, "rb");
    if (!fp) {
        return -1;
    }
    if (fseek(fp, 0, SEEK_END) != 0) {
        fclose(fp);
        return -1;
    }
    file_size = ftell(fp);
    if (file_size <= 0) {
        fclose(fp);
        return -1;
    }
    if (fseek(fp, 0, SEEK_SET) != 0) {
        fclose(fp);
        return -1;
    }

    data = (uint8_t *)malloc((size_t)file_size);
    if (!data) {
        fclose(fp);
        return -1;
    }
    if (fread(data, 1, (size_t)file_size, fp) != (size_t)file_size) {
        free(data);
        fclose(fp);
        return -1;
    }
    fclose(fp);

    if (!detect_card_offset(data, (size_t)file_size, &offset) ||
        ((size_t)file_size < offset + PS1CARD_SIZE)) {
        debug_log("scan ps1 parser: unsupported card path=%s size=%ld", path, file_size);
        free(data);
        return 0;
    }

    debug_log("scan ps1 parser: path=%s size=%ld offset=%u",
              path, file_size, (unsigned)offset);

    raw = data + offset;
    for (int slot = 0; slot < PS1CARD_MAX_SLOTS_LOCAL && count < max_entries; slot++) {
        const uint8_t *header = raw + (PS1CARD_HEADER_SIZE * (slot + 1));
        char title_id[GAME_ID_LEN];
        char save_name[32];
        char prod_bytes[64];

        format_prod_bytes(header + 12, prod_bytes, sizeof(prod_bytes));
        debug_log("scan ps1 parser: slot=%d type=0x%02X next=0x%02X prod=%s",
                  slot,
                  header[0],
                  header[8],
                  prod_bytes);

        if (header[0] != 0x51) {
            continue;
        }
        if (!normalize_title_id(header + 12, title_id, sizeof(title_id))) {
            debug_log("scan ps1 parser: slot=%d rejected_product prod=%s", slot, prod_bytes);
            continue;
        }

        memset(save_name, 0, sizeof(save_name));
        sanitize_ascii_name(header + 10, 20, save_name, sizeof(save_name));

        memset(&entries[count], 0, sizeof(entries[count]));
        strncpy(entries[count].title_id, title_id, sizeof(entries[count].title_id) - 1);
        strncpy(entries[count].save_name,
                save_name[0] ? save_name : title_id,
                sizeof(entries[count].save_name) - 1);
        entries[count].slot_index = slot;
        debug_log("scan ps1 parser: slot=%d accepted title_id=%s save_name=%s",
                  slot, entries[count].title_id, entries[count].save_name);
        count++;
    }

    free(data);
    return count;
}
