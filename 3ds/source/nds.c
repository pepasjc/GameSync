#include "nds.h"
#include "card_spi.h"
#include "title.h"

#include <dirent.h>
#include <sys/stat.h>

#define NDS_GAMECODE_OFFSET 0x0C
#define NDS_TITLE_ID_PREFIX 0x00048000ULL

// Read the 4-char game code from an NDS ROM header on the SD card.
// Returns true and fills code_out (5 bytes) on success.
static bool nds_read_gamecode(const char *rom_path, char *code_out) {
    FILE *f = fopen(rom_path, "rb");
    if (!f) return false;

    fseek(f, NDS_GAMECODE_OFFSET, SEEK_SET);
    char code[4];
    size_t rd = fread(code, 1, 4, f);
    fclose(f);

    if (rd != 4) return false;

    // Validate printable ASCII
    for (int i = 0; i < 4; i++) {
        if (code[i] < 0x20 || code[i] > 0x7E)
            return false;
    }

    memcpy(code_out, code, 4);
    code_out[4] = '\0';
    return true;
}

// Convert a 4-char NDS game code to a u64 title ID.
// Format: 00048000 (high 32) + ASCII hex of game code (low 32)
// Example: "A2DE" -> 0x0004800041324445
static u64 nds_gamecode_to_title_id(const char *code) {
    u64 tid = NDS_TITLE_ID_PREFIX << 32;
    tid |= ((u64)(u8)code[0]) << 24;
    tid |= ((u64)(u8)code[1]) << 16;
    tid |= ((u64)(u8)code[2]) << 8;
    tid |= ((u64)(u8)code[3]);
    return tid;
}

// Check if a string ends with a suffix (case-insensitive)
static bool ends_with_ci(const char *str, const char *suffix) {
    int slen = strlen(str);
    int suflen = strlen(suffix);
    if (suflen > slen) return false;
    for (int i = 0; i < suflen; i++) {
        char a = str[slen - suflen + i];
        char b = suffix[i];
        if (a >= 'A' && a <= 'Z') a += 32;
        if (b >= 'A' && b <= 'Z') b += 32;
        if (a != b) return false;
    }
    return true;
}

// Find the .sav file for an NDS ROM. Checks:
// 1. Same directory: <dir>/<stem>.sav
// 2. saves/ subfolder: <dir>/saves/<stem>.sav
// Returns true and fills sav_path_out on success.
static bool find_sav_for_rom(const char *rom_path, char *sav_path_out, int out_size) {
    // Extract directory and stem from rom_path
    char dir[MAX_PATH_LEN];
    char stem[MAX_PATH_LEN];
    strncpy(dir, rom_path, MAX_PATH_LEN - 1);
    dir[MAX_PATH_LEN - 1] = '\0';

    // Find last / to split dir and filename
    char *last_slash = strrchr(dir, '/');
    if (!last_slash) return false;

    char *filename = last_slash + 1;
    strncpy(stem, filename, MAX_PATH_LEN - 1);
    stem[MAX_PATH_LEN - 1] = '\0';
    *last_slash = '\0';  // dir is now just the directory part

    // Remove .nds extension from stem
    char *dot = strrchr(stem, '.');
    if (dot) *dot = '\0';

    // Check same directory
    snprintf(sav_path_out, out_size, "%s/%s.sav", dir, stem);
    struct stat st;
    if (stat(sav_path_out, &st) == 0 && S_ISREG(st.st_mode))
        return true;

    // Check saves/ subfolder
    snprintf(sav_path_out, out_size, "%s/saves/%s.sav", dir, stem);
    if (stat(sav_path_out, &st) == 0 && S_ISREG(st.st_mode))
        return true;

    return false;
}

int nds_scan(const char *nds_dir, TitleInfo *titles, int offset, int max_titles) {
    if (!nds_dir || !nds_dir[0])
        return 0;

    DIR *dp = opendir(nds_dir);
    if (!dp) return 0;

    int added = 0;
    struct dirent *entry;

    while ((entry = readdir(dp)) != NULL && (offset + added) < max_titles) {
        // Skip . and ..
        if (entry->d_name[0] == '.') continue;

        // Only process .nds files
        if (!ends_with_ci(entry->d_name, ".nds")) continue;

        // Build full ROM path
        char rom_path[MAX_PATH_LEN];
        snprintf(rom_path, sizeof(rom_path), "%s/%s", nds_dir, entry->d_name);

        // Read game code from ROM header
        char code[5];
        if (!nds_read_gamecode(rom_path, code))
            continue;

        // Check for duplicate game codes (already in title list)
        bool dup = false;
        for (int j = 0; j < offset + added; j++) {
            if (titles[j].is_nds && strcmp(titles[j].product_code, code) == 0) {
                dup = true;
                break;
            }
        }
        if (dup) continue;

        // Find matching .sav file (or set default path for downloads)
        char sav_path[MAX_PATH_LEN];
        bool has_save = find_sav_for_rom(rom_path, sav_path, sizeof(sav_path));
        if (!has_save) {
            // Build default sav_path: prefer saves/ subfolder if it exists
            char dir[MAX_PATH_LEN];
            strncpy(dir, rom_path, MAX_PATH_LEN - 1);
            dir[MAX_PATH_LEN - 1] = '\0';
            char *sl = strrchr(dir, '/');
            char stem[MAX_PATH_LEN];
            if (sl) {
                strncpy(stem, sl + 1, MAX_PATH_LEN - 1);
                stem[MAX_PATH_LEN - 1] = '\0';
                *sl = '\0';
            } else {
                strncpy(stem, rom_path, MAX_PATH_LEN - 1);
                stem[MAX_PATH_LEN - 1] = '\0';
                dir[0] = '\0';
            }
            char *dot = strrchr(stem, '.');
            if (dot) *dot = '\0';

            // Check if saves/ subfolder exists
            char saves_sub[MAX_PATH_LEN];
            snprintf(saves_sub, sizeof(saves_sub), "%s/saves", dir);
            struct stat st;
            if (stat(saves_sub, &st) == 0) {
                snprintf(sav_path, sizeof(sav_path), "%s/saves/%s.sav", dir, stem);
            } else {
                snprintf(sav_path, sizeof(sav_path), "%s/%s.sav", dir, stem);
            }
        }

        // Build TitleInfo
        TitleInfo *t = &titles[offset + added];
        memset(t, 0, sizeof(TitleInfo));

        t->title_id = nds_gamecode_to_title_id(code);
        t->media_type = MEDIATYPE_SD;  // NDS ROMs are on SD card
        t->is_nds = true;
        t->has_save_data = has_save;
        t->in_conflict = false;

        title_id_to_hex(t->title_id, t->title_id_hex);
        strncpy(t->product_code, code, sizeof(t->product_code) - 1);
        strncpy(t->sav_path, sav_path, MAX_PATH_LEN - 1);

        // Set initial name to ROM filename (will be updated by server lookup)
        // Strip .nds extension for display
        char display_name[64];
        strncpy(display_name, entry->d_name, sizeof(display_name) - 1);
        display_name[sizeof(display_name) - 1] = '\0';
        char *ext = strrchr(display_name, '.');
        if (ext) *ext = '\0';
        snprintf(t->name, sizeof(t->name), "%s", display_name);

        added++;
    }

    closedir(dp);
    return added;
}

int nds_read_save(const char *sav_path, ArchiveFile *files, int max_files) {
    if (max_files < 1) return -1;

    FILE *f = fopen(sav_path, "rb");
    if (!f) return -1;

    // Get file size
    fseek(f, 0, SEEK_END);
    long size = ftell(f);

    u8 *data = (u8 *)malloc((size_t)size);
    if (!data) {
        fclose(f);
        return -1;
    }

    size_t rd = fread(data, 1, (size_t)size, f);
    fclose(f);

    if ((long)rd != size) {
        free(data);
        return -1;
    }

    // Store as single file "save.dat" (matches ds_sync.py bundle format)
    strncpy(files[0].path, "save.dat", MAX_PATH_LEN - 1);
    files[0].path[MAX_PATH_LEN - 1] = '\0';
    files[0].size = (u32)size;
    files[0].data = data;

    return 1;
}

bool nds_write_save(const char *sav_path, const ArchiveFile *files, int file_count) {
    if (file_count < 1) return false;

    // Use the first file's data (bundles from ds_sync.py have one "save.dat" file)
    const ArchiveFile *sav = &files[0];

    // Ensure parent directory exists (for first-time downloads to saves/ subfolder)
    char dir[MAX_PATH_LEN];
    strncpy(dir, sav_path, MAX_PATH_LEN - 1);
    dir[MAX_PATH_LEN - 1] = '\0';
    char *last_slash = strrchr(dir, '/');
    if (last_slash) {
        *last_slash = '\0';
        mkdir(dir, 0777);
    }

    FILE *f = fopen(sav_path, "wb");
    if (!f) return false;

    size_t written = fwrite(sav->data, 1, sav->size, f);
    fclose(f);

    return written == sav->size;
}

int nds_cart_read_save(ArchiveFile *files, int max_files) {
    if (max_files < 1) return -1;

    CardSaveType type = card_spi_detect();
    if (type == SAVE_TYPE_UNKNOWN) return -1;

    u32 save_size = card_spi_get_size(type);
    if (save_size == 0) return -1;

    u8 *data = (u8 *)malloc(save_size);
    if (!data) return -1;

    if (!card_spi_read_save(type, data, save_size)) {
        free(data);
        return -1;
    }

    strncpy(files[0].path, "save.dat", MAX_PATH_LEN - 1);
    files[0].path[MAX_PATH_LEN - 1] = '\0';
    files[0].size = save_size;
    files[0].data = data;

    return 1;
}

bool nds_cart_write_save(const ArchiveFile *files, int file_count) {
    if (file_count < 1) return false;

    CardSaveType type = card_spi_detect();
    if (type == SAVE_TYPE_UNKNOWN) return false;

    const ArchiveFile *sav = &files[0];
    u32 save_size = card_spi_get_size(type);

    // If download is smaller than cart, pad with 0xFF
    // If download is larger, write only what fits
    u32 write_size = sav->size < save_size ? save_size : sav->size;
    if (write_size > save_size) write_size = save_size;

    u8 *buf;
    if (sav->size < save_size) {
        // Pad with 0xFF
        buf = (u8 *)malloc(save_size);
        if (!buf) return false;
        memset(buf, 0xFF, save_size);
        memcpy(buf, sav->data, sav->size);
        bool ok = card_spi_write_save(type, buf, save_size);
        free(buf);
        return ok;
    }

    return card_spi_write_save(type, sav->data, write_size);
}
