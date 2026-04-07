#include "apollo.h"

#include <ctype.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static bool is_ps1_prefix(const char *value) {
    static const char *const prefixes[] = {
        "SCUS",
        "SLUS",
        "SCES",
        "SLES",
        "SCPS",
        "SLPS",
        "SLPM",
    };
    size_t i;

    for (i = 0; i < sizeof(prefixes) / sizeof(prefixes[0]); i++) {
        if (strncmp(value, prefixes[i], 4) == 0) {
            return true;
        }
    }
    return false;
}

static char ascii_upper(char c) {
    return (char)toupper((unsigned char)c);
}

bool apollo_is_ps3_save_dir(const char *name) {
    if (!name) {
        return false;
    }
    if (strlen(name) < 9) {
        return false;
    }
    for (int i = 0; i < 4; i++) {
        if (!isalpha((unsigned char)name[i])) {
            return false;
        }
    }
    for (int i = 4; i < 9; i++) {
        if (!isdigit((unsigned char)name[i])) {
            return false;
        }
    }
    for (size_t i = 9; name[i] != '\0'; i++) {
        unsigned char c = (unsigned char)name[i];
        if (!(isalpha(c) || isdigit(c) || c == '-' || c == '_' || c == '.')) {
            return false;
        }
    }
    return true;
}

bool apollo_is_ps1_vm1_file(const char *name) {
    size_t len;
    if (!name) {
        return false;
    }
    len = strlen(name);
    return len > 4
        && ascii_upper(name[len - 4]) == '.'
        && ascii_upper(name[len - 3]) == 'V'
        && ascii_upper(name[len - 2]) == 'M'
        && ascii_upper(name[len - 1]) == '1';
}

SaveKind apollo_detect_save_kind(const char *game_code) {
    int num;
    if (!game_code || strlen(game_code) < 9) return SAVE_KIND_PS3;

    /* PSP physical: ULUS, UCES, UCUS, UCAS, UCJS, ULJM, ULJS, etc. */
    if (game_code[0] == 'U') return SAVE_KIND_PSP;

    /* Vita: PCSA, PCSB, PCSE, PCSC, PCSF, PCSG, etc. */
    if (game_code[0] == 'P' && game_code[1] == 'C') return SAVE_KIND_PSP;

    /* PS2 others */
    if (game_code[0] == 'P' && game_code[1] == 'B') return SAVE_KIND_PS2;

    /* PS3 Blu-ray: BLUS, BLES, BCUS, BCES, BLJM, BLJS, BCKS, BCAS, etc. */
    if (game_code[0] == 'B') return SAVE_KIND_PS3;

    /* NP* PSN codes: NP + [region] + [platform char] + 5 digits
     * Platform char (index 3): B=PS3, H=PSP, G=PSP Mini, D=PS2 Classic */
    if (game_code[0] == 'N' && game_code[1] == 'P') {
        switch (game_code[3]) {
            case 'B': return SAVE_KIND_PS3;
            case 'H': return SAVE_KIND_PSP;
            case 'G': return SAVE_KIND_PSP;
            case 'D': return SAVE_KIND_PS2;
            default:  return SAVE_KIND_PS3;
        }
    }

    /* S* disc codes: PS1 or PS2 distinguished by serial number range */
    if (game_code[0] == 'S') {
        num = atoi(game_code + 4);
        if (strncmp(game_code, "SLUS", 4) == 0 && num >= 20000) return SAVE_KIND_PS2;
        if (strncmp(game_code, "SCUS", 4) == 0 && num >= 97000) return SAVE_KIND_PS2;
        if (strncmp(game_code, "SLES", 4) == 0 && num >= 50000) return SAVE_KIND_PS2;
        if (strncmp(game_code, "SCES", 4) == 0 && num >= 50000) return SAVE_KIND_PS2;
        if (strncmp(game_code, "SLPS", 4) == 0 && num >= 20000) return SAVE_KIND_PS2;
        if (strncmp(game_code, "SCPS", 4) == 0 && num >= 20000) return SAVE_KIND_PS2;
        return SAVE_KIND_PS1;
    }

    return SAVE_KIND_PS3;
}

bool apollo_extract_game_code(const char *save_dir_name, char *game_code_out, size_t out_size) {
    if (!apollo_is_ps3_save_dir(save_dir_name) || out_size < 10) {
        return false;
    }
    for (int i = 0; i < 9; i++) {
        game_code_out[i] = ascii_upper(save_dir_name[i]);
    }
    game_code_out[9] = '\0';
    return true;
}

bool apollo_extract_ps1_title_id(const char *vm1_name, char *title_id_out, size_t out_size) {
    size_t len;
    size_t pos;

    if (!vm1_name || !title_id_out || out_size < 10 || !apollo_is_ps1_vm1_file(vm1_name)) {
        return false;
    }

    len = strlen(vm1_name);
    for (pos = 0; pos + 9 <= len; pos++) {
        size_t end;
        size_t copy_len;

        if (pos > 0 && isalnum((unsigned char)vm1_name[pos - 1])) {
            continue;
        }
        if (!isupper((unsigned char)vm1_name[pos + 0])
                || !isupper((unsigned char)vm1_name[pos + 1])
                || !isupper((unsigned char)vm1_name[pos + 2])
                || !isupper((unsigned char)vm1_name[pos + 3])) {
            continue;
        }
        if (!isdigit((unsigned char)vm1_name[pos + 4])
                || !isdigit((unsigned char)vm1_name[pos + 5])
                || !isdigit((unsigned char)vm1_name[pos + 6])
                || !isdigit((unsigned char)vm1_name[pos + 7])
                || !isdigit((unsigned char)vm1_name[pos + 8])) {
            continue;
        }
        if (!is_ps1_prefix(vm1_name + pos)) {
            continue;
        }

        end = pos + 9;
        while (end < len && isalnum((unsigned char)vm1_name[end])) {
            end++;
        }
        copy_len = end - pos;
        if (copy_len >= out_size) {
            copy_len = out_size - 1;
        }
        memcpy(title_id_out, vm1_name + pos, copy_len);
        title_id_out[copy_len] = '\0';
        return true;
    }

    return false;
}

void apollo_get_ps3_savedata_root(const SyncState *state, char *out, size_t out_size) {
    snprintf(
        out,
        out_size,
        "/dev_hdd0/home/%s/savedata",
        state->ps3_user[0] ? state->ps3_user : "00000001"
    );
}

void apollo_get_ps3_export_root(int usb_index, char *out, size_t out_size) {
    snprintf(out, out_size, "/dev_usb%03d/PS3/EXPORT", usb_index);
}

void apollo_get_ps3_usb_savedata_root(int usb_index, char *out, size_t out_size) {
    snprintf(out, out_size, "/dev_usb%03d/PS3/SAVEDATA", usb_index);
}

void apollo_get_ps1_vmc_root(char *out, size_t out_size) {
    snprintf(out, out_size, "/dev_hdd0/savedata/vmc");
}

void apollo_get_ps1_usb_vmc_root(int usb_index, char *out, size_t out_size) {
    snprintf(out, out_size, "/dev_usb%03d/PS1/VMC", usb_index);
}
