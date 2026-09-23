#ifndef GCSYNC_VMCFS_H
#define GCSYNC_VMCFS_H

#include "common.h"

#include <stdint.h>

/*
 * vmcfs — GameCube memory-card *image* filesystem (Swiss / Dolphin .raw).
 *
 * Operates on a card image file on SD without loading the whole card into
 * RAM: the directory + block-allocation-map blocks (8 KB each) are held in
 * memory, save data blocks are streamed from/to the file.
 *
 * Layout (blocks of 0x2000):
 *   0      header
 *   1 / 2  directory + backup   (127 x 64-byte entries; u16 counter @0x1FFA,
 *                                checksums @0x1FFC/0x1FFE over [0,0x1FFC))
 *   3 / 4  BAM + backup         (csums @+0/+2 over [+4,0x2000), counter @+4,
 *                                free count @+6, last_alloc @+8,
 *                                u16 chain entry for block B @ 0xA+(B-5)*2)
 *   5+     data blocks
 */

#define VMCFS_BLOCK      0x2000
#define VMCFS_MAX_SAVES  127

typedef struct {
    char     gamecode[8];    /* 4-char code + NUL */
    char     company[4];     /* 2-char maker + NUL */
    char     filename[40];   /* on-card filename */
    char     title_id[16];   /* "GC_<code>" */
    char     name[64];       /* display name (filled from server list) */
    int      dir_index;      /* directory slot */
    uint16_t first_block;
    uint16_t blocks;
} VmcfsSave;

typedef struct {
    char      path[SAVE_DIR_LEN];
    char      filename[128];
    uint32_t  card_size;
    uint16_t  total_blocks;
    uint8_t   dir[VMCFS_BLOCK];   /* active directory copy */
    uint8_t   bam[VMCFS_BLOCK];   /* active BAM copy */
    VmcfsSave saves[VMCFS_MAX_SAVES];
    int       count;
    char      last_error[96];
} VmcfsCard;

/* Open a card image and parse its directory.  Returns false on error
 * (last_error is set). */
bool vmcfs_open(VmcfsCard *c, const char *path);

/* Extract save ``idx`` as a GCI (64-byte entry + data blocks, following the
 * BAM chain).  Returns the GCI length or negative on error. */
int  vmcfs_read_gci(VmcfsCard *c, int idx, uint8_t *out, uint32_t out_max);

/* Insert GCI bytes into the card image: overwrites the same game in place
 * when the block count matches, otherwise frees the old chain and allocates
 * a new one from the BAM.  Rewrites directory + BAM (both copies, bumped
 * counters, valid checksums).  Returns 0 on success. */
int  vmcfs_write_gci(VmcfsCard *c, const uint8_t *gci, uint32_t len,
                     char *msg, size_t msg_size);

#endif /* GCSYNC_VMCFS_H */
