/*
 * PS3 Save Sync - Game keys database (games.conf parser)
 *
 * Parses the Apollo Save Tool "games.conf" INI-like format to provide
 * per-game secure_file_id values needed for save data decryption.
 *
 * Format:
 *   [TITLEID1/TITLEID2]
 *   disc_hash_key=<32 hex chars>
 *   secure_file_id:PATTERN=<32 hex chars>
 *   secure_file_id:*=<32 hex chars>
 */

#include "gamekeys.h"
#include "debug.h"
#include "common.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <ctype.h>

/* ---- Internal structures ---- */

#define GK_MAX_GAMES       4096
#define GK_MAX_KEYS        8     /* max secure_file_id entries per game */
#define GK_MAX_TITLE_IDS   8     /* max title IDs per section */
#define GK_PATTERN_LEN     128

typedef struct {
    char pattern[GK_PATTERN_LEN];   /* filename pattern (e.g. "*", "USR-DATA", "*.dat") */
    uint8_t key[16];                /* 16-byte secure_file_id */
} gk_file_key_t;

typedef struct {
    char title_ids[GK_MAX_TITLE_IDS][16];  /* game codes (e.g. "BCUS98233") */
    int  num_title_ids;
    uint8_t disc_hash_key[16];
    bool    has_disc_hash_key;
    gk_file_key_t file_keys[GK_MAX_KEYS];
    int  num_file_keys;
} gk_game_t;

static gk_game_t *g_games = NULL;
static int g_num_games = 0;
static bool g_loaded = false;

/* ---- Hex parsing ---- */

static int hex_char_val(char c) {
    if (c >= '0' && c <= '9') return c - '0';
    if (c >= 'a' && c <= 'f') return c - 'a' + 10;
    if (c >= 'A' && c <= 'F') return c - 'A' + 10;
    return -1;
}

static bool parse_hex_bytes(const char *hex, uint8_t *out, int num_bytes) {
    for (int i = 0; i < num_bytes; i++) {
        int hi = hex_char_val(hex[i * 2]);
        int lo = hex_char_val(hex[i * 2 + 1]);
        if (hi < 0 || lo < 0) return false;
        out[i] = (uint8_t)((hi << 4) | lo);
    }
    return true;
}

/* ---- Wildcard matching ---- */

/*
 * Simple wildcard match supporting only '*' (match any sequence).
 * This matches Apollo Save Tool's behavior for secure_file_id patterns.
 */
static bool wildcard_match(const char *pattern, const char *str) {
    const char *p = pattern;
    const char *s = str;
    const char *star_p = NULL;
    const char *star_s = NULL;

    while (*s) {
        if (*p == *s) {
            p++;
            s++;
        } else if (*p == '*') {
            star_p = p++;
            star_s = s;
        } else if (star_p) {
            p = star_p + 1;
            s = ++star_s;
        } else {
            return false;
        }
    }

    while (*p == '*') p++;
    return *p == '\0';
}

/* ---- Line parsing helpers ---- */

static void trim_right(char *s) {
    int len = (int)strlen(s);
    while (len > 0 && (s[len - 1] == '\r' || s[len - 1] == '\n' ||
                        s[len - 1] == ' '  || s[len - 1] == '\t')) {
        s[--len] = '\0';
    }
}

static const char *skip_whitespace(const char *s) {
    while (*s == ' ' || *s == '\t') s++;
    return s;
}

/* Parse a section header like "[BCUS98233/BLES01807]" into title IDs */
static bool parse_section(const char *line, gk_game_t *game) {
    const char *p = line;
    if (*p != '[') return false;
    p++;

    game->num_title_ids = 0;

    while (*p && *p != ']' && game->num_title_ids < GK_MAX_TITLE_IDS) {
        /* Skip whitespace and separators */
        while (*p == ' ' || *p == '/' || *p == '\t') p++;
        if (*p == ']' || *p == '\0') break;

        /* Read title ID (up to next separator, ], or whitespace) */
        int len = 0;
        while (p[len] && p[len] != '/' && p[len] != ']' &&
               p[len] != ' ' && p[len] != '\t' && len < 15) {
            len++;
        }
        if (len >= 4) { /* minimum plausible title ID length */
            int idx = game->num_title_ids;
            memcpy(game->title_ids[idx], p, len);
            game->title_ids[idx][len] = '\0';
            /* Uppercase for consistent matching */
            for (int i = 0; i < len; i++)
                game->title_ids[idx][i] = (char)toupper((unsigned char)game->title_ids[idx][i]);
            game->num_title_ids++;
        }
        p += len;
    }

    return game->num_title_ids > 0;
}

/* Parse "disc_hash_key=<32 hex>" */
static bool parse_disc_hash_key(const char *line, gk_game_t *game) {
    const char *eq = strchr(line, '=');
    if (!eq) return false;

    /* Check prefix */
    size_t plen = (size_t)(eq - line);
    if (plen < 13) return false;  /* "disc_hash_key" = 13 chars */

    char prefix[32];
    if (plen >= sizeof(prefix)) return false;
    memcpy(prefix, line, plen);
    prefix[plen] = '\0';
    trim_right(prefix);

    if (strcmp(prefix, "disc_hash_key") != 0) return false;

    const char *hex = skip_whitespace(eq + 1);
    if (strlen(hex) < 32) return false;

    if (!parse_hex_bytes(hex, game->disc_hash_key, 16)) return false;
    game->has_disc_hash_key = true;
    return true;
}

/* Parse "secure_file_id:PATTERN=<32 hex>" */
static bool parse_secure_file_id(const char *line, gk_game_t *game) {
    /* Must start with "secure_file_id:" */
    if (strncmp(line, "secure_file_id:", 15) != 0) return false;
    if (game->num_file_keys >= GK_MAX_KEYS) return false;

    const char *eq = strchr(line + 15, '=');
    if (!eq) return false;

    /* Extract pattern between ':' and '=' */
    size_t pat_len = (size_t)(eq - (line + 15));
    if (pat_len == 0 || pat_len >= GK_PATTERN_LEN) return false;

    gk_file_key_t *fk = &game->file_keys[game->num_file_keys];
    memcpy(fk->pattern, line + 15, pat_len);
    fk->pattern[pat_len] = '\0';
    trim_right(fk->pattern);

    const char *hex = skip_whitespace(eq + 1);
    if (strlen(hex) < 32) return false;

    if (!parse_hex_bytes(hex, fk->key, 16)) return false;
    game->num_file_keys++;
    return true;
}

/* ---- Public API ---- */

bool gamekeys_init(const char *data, size_t data_len) {
    if (g_loaded) {
        gamekeys_shutdown();
    }

    /* Allocate game array */
    g_games = (gk_game_t *)malloc(sizeof(gk_game_t) * GK_MAX_GAMES);
    if (!g_games) {
        debug_log("gamekeys_init: malloc failed");
        return false;
    }
    memset(g_games, 0, sizeof(gk_game_t) * GK_MAX_GAMES);
    g_num_games = 0;

    /* Parse line by line */
    const char *p = data;
    const char *end = data + data_len;
    gk_game_t *current = NULL;
    int line_num = 0;

    while (p < end) {
        /* Extract one line */
        const char *eol = p;
        while (eol < end && *eol != '\n') eol++;

        size_t line_len = (size_t)(eol - p);
        char line[512];
        if (line_len >= sizeof(line)) line_len = sizeof(line) - 1;
        memcpy(line, p, line_len);
        line[line_len] = '\0';
        trim_right(line);

        p = (eol < end) ? eol + 1 : end;
        line_num++;

        /* Skip empty lines and comments */
        const char *trimmed = skip_whitespace(line);
        if (*trimmed == '\0' || *trimmed == '#' || *trimmed == ';') continue;

        /* Section header? */
        if (*trimmed == '[') {
            if (g_num_games >= GK_MAX_GAMES) {
                debug_log("gamekeys_init: max games reached at line %d", line_num);
                break;
            }
            current = &g_games[g_num_games];
            memset(current, 0, sizeof(*current));
            if (parse_section(trimmed, current)) {
                g_num_games++;
            } else {
                current = NULL;
            }
            continue;
        }

        /* Key=value line — needs a current section */
        if (!current) continue;

        if (!parse_disc_hash_key(trimmed, current)) {
            parse_secure_file_id(trimmed, current);
        }
    }

    g_loaded = true;
    debug_log("gamekeys_init: loaded %d game entries", g_num_games);
    return true;
}

void gamekeys_shutdown(void) {
    if (g_games) {
        free(g_games);
        g_games = NULL;
    }
    g_num_games = 0;
    g_loaded = false;
}

bool gamekeys_is_loaded(void) {
    return g_loaded;
}

/* Find the game entry that contains the given game_code as one of its title IDs */
static gk_game_t *find_game(const char *game_code) {
    char upper[16];
    size_t len = strlen(game_code);
    if (len >= sizeof(upper)) len = sizeof(upper) - 1;
    for (size_t i = 0; i < len; i++)
        upper[i] = (char)toupper((unsigned char)game_code[i]);
    upper[len] = '\0';

    for (int g = 0; g < g_num_games; g++) {
        for (int t = 0; t < g_games[g].num_title_ids; t++) {
            if (strcmp(g_games[g].title_ids[t], upper) == 0) {
                return &g_games[g];
            }
        }
    }
    return NULL;
}

bool gamekeys_get_secure_file_id(const char *game_code,
                                  const char *filename,
                                  uint8_t out[16]) {
    if (!g_loaded || !game_code || !filename) {
        return false;
    }

    gk_game_t *game = find_game(game_code);
    if (!game || game->num_file_keys == 0) {
        return false;
    }

    /* Priority: exact match > prefix/wildcard > pure "*" */
    gk_file_key_t *best = NULL;
    int best_specificity = -1;

    for (int i = 0; i < game->num_file_keys; i++) {
        gk_file_key_t *fk = &game->file_keys[i];

        if (strcmp(fk->pattern, filename) == 0) {
            /* Exact match — highest priority */
            memcpy(out, fk->key, 16);
            return true;
        }

        if (wildcard_match(fk->pattern, filename)) {
            /* Score by pattern length (longer = more specific), but
             * "*" alone gets lowest score */
            int spec = (strcmp(fk->pattern, "*") == 0) ? 0 : (int)strlen(fk->pattern);
            if (spec > best_specificity) {
                best = fk;
                best_specificity = spec;
            }
        }
    }

    if (best) {
        memcpy(out, best->key, 16);
        return true;
    }

    return false;
}

bool gamekeys_get_disc_hash_key(const char *game_code, uint8_t out[16]) {
    if (!g_loaded || !game_code) return false;

    gk_game_t *game = find_game(game_code);
    if (!game || !game->has_disc_hash_key) return false;

    memcpy(out, game->disc_hash_key, 16);
    return true;
}
