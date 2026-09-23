#ifndef WIIUSYNC_IOWRITE_H
#define WIIUSYNC_IOWRITE_H

#include "common.h"

#include <stdio.h>

#include <coreinit/messagequeue.h>
#include <coreinit/thread.h>

/*
 * Background file writer — decouples the download's recv loop from SD/USB
 * write latency.
 *
 * With recv and fwrite on one thread the transfer runs at the *serial sum* of
 * network time and storage time: while a chunk is being written the socket's
 * receive window fills and the sender stalls.  A ring of large aligned buffers
 * drained by a dedicated writer thread lets both run concurrently, so the
 * transfer runs at max(network, storage) instead.
 *
 * Design based on analysis of NUSspli's ioQueue.c
 * (https://github.com/V10lator/NUSspli, GPL-3.0-or-later, (c) V10lator):
 * fixed ring of 1 MB 0x40-aligned buffers, writer thread pinned to CPU2
 * (the main loop runs on CPU1), plain memcpy hand-off.
 */

#define IOW_BUF_SIZE (1u << 20)   /* 1 MB per buffer */
#define IOW_NBUF     8u           /* 8 MB in flight */

typedef struct {
    FILE          *fp;
    uint8_t       *mem;                       /* IOW_NBUF * IOW_BUF_SIZE */
    uint8_t       *stack;
    OSThread       thread;
    OSMessageQueue full_q, free_q;
    OSMessage      full_msgs[IOW_NBUF + 1];   /* +1 slot for the terminator */
    OSMessage      free_msgs[IOW_NBUF];
    uint8_t       *cur;                       /* buffer being filled */
    uint32_t       cur_len;
    volatile int   error;                     /* sticky fwrite failure */
    bool           started;
} IoWriter;

/* Spawn the writer thread.  fp should be unbuffered (setvbuf _IONBF) so the
 * 1 MB aligned writes pass straight through to the filesystem.
 * Returns 0 on success, -1 if allocation or thread creation failed (caller
 * falls back to synchronous writes). */
int iow_start(IoWriter *w, FILE *fp);

/* Queue bytes.  Blocks only when all buffers are full (storage is the
 * bottleneck).  Returns 0, or -1 once a write has failed. */
int iow_write(IoWriter *w, const void *data, size_t len);

/* Flush the partial buffer, stop and join the thread.  Returns 0 if every
 * byte hit the file, -1 otherwise.  Safe to call after a transfer abort —
 * whatever was queued is flushed so a .part file stays resumable. */
int iow_finish(IoWriter *w);

/* HttpWriteFn adapter: user is the IoWriter. */
int iow_stream_writer(void *user, const uint8_t *data, size_t len);

#endif /* WIIUSYNC_IOWRITE_H */
