/*
 * iowrite.c — background file writer for downloads.
 *
 * Design based on analysis of NUSspli's ioQueue.c
 * (https://github.com/V10lator/NUSspli, GPL-3.0-or-later, (c) V10lator).
 * See iowrite.h for the rationale.
 */

#include "iowrite.h"

#include <malloc.h>
#include <string.h>

#include <coreinit/memory.h>

#define IOW_STACK_SIZE (128 * 1024)

/* full_q message: message = buffer pointer (NULL = stop), args[0] = length. */

static int writer_thread(int argc, const char **argv) {
    (void)argc;
    IoWriter *w = (IoWriter *)argv;

    for (;;) {
        OSMessage msg;
        OSReceiveMessage(&w->full_q, &msg, OS_MESSAGE_FLAGS_BLOCKING);
        uint8_t *buf = (uint8_t *)msg.message;
        if (!buf) break;

        uint32_t len = msg.args[0];
        if (!w->error && fwrite(buf, 1, len, w->fp) != len)
            w->error = 1;

        msg.args[0] = 0;
        OSSendMessage(&w->free_q, &msg, OS_MESSAGE_FLAGS_BLOCKING);
    }
    return 0;
}

int iow_start(IoWriter *w, FILE *fp) {
    memset(w, 0, sizeof(*w));
    w->fp = fp;

    w->mem   = (uint8_t *)memalign(0x40, IOW_NBUF * IOW_BUF_SIZE);
    w->stack = (uint8_t *)memalign(16, IOW_STACK_SIZE);
    if (!w->mem || !w->stack) {
        free(w->mem);  free(w->stack);
        return -1;
    }

    OSInitMessageQueue(&w->full_q, w->full_msgs, IOW_NBUF + 1);
    OSInitMessageQueue(&w->free_q, w->free_msgs, IOW_NBUF);

    /* All buffers start free; the first fill buffer is taken up front. */
    for (uint32_t i = 1; i < IOW_NBUF; i++) {
        OSMessage msg = { .message = w->mem + i * IOW_BUF_SIZE };
        OSSendMessage(&w->free_q, &msg, OS_MESSAGE_FLAGS_NONE);
    }
    w->cur     = w->mem;
    w->cur_len = 0;

    /* The main loop lives on CPU1 (WHB default); pin the writer to CPU2 so
     * FSA wait time never steals cycles from the recv loop. */
    if (!OSCreateThread(&w->thread, writer_thread, 0, (char *)w,
                        w->stack + IOW_STACK_SIZE, IOW_STACK_SIZE,
                        14, OS_THREAD_ATTRIB_AFFINITY_CPU2)) {
        free(w->mem);  free(w->stack);
        w->mem = w->stack = NULL;
        return -1;
    }
    OSSetThreadName(&w->thread, "wiiusync iow");
    OSResumeThread(&w->thread);
    w->started = true;
    return 0;
}

static void iow_submit_cur(IoWriter *w) {
    OSMessage msg = { .message = w->cur, .args = { w->cur_len, 0, 0 } };
    OSSendMessage(&w->full_q, &msg, OS_MESSAGE_FLAGS_BLOCKING);
    w->cur     = NULL;
    w->cur_len = 0;
}

int iow_write(IoWriter *w, const void *data, size_t len) {
    if (!w->started || w->error) return -1;

    const uint8_t *p = (const uint8_t *)data;
    while (len) {
        if (!w->cur) {
            OSMessage msg;
            OSReceiveMessage(&w->free_q, &msg, OS_MESSAGE_FLAGS_BLOCKING);
            w->cur = (uint8_t *)msg.message;
            if (w->error) return -1;   /* checked after the potential block */
        }
        size_t space = IOW_BUF_SIZE - w->cur_len;
        size_t take  = len < space ? len : space;
        memcpy(w->cur + w->cur_len, p, take);
        w->cur_len += (uint32_t)take;
        p          += take;
        len        -= take;
        if (w->cur_len == IOW_BUF_SIZE) iow_submit_cur(w);
    }
    return 0;
}

int iow_finish(IoWriter *w) {
    if (!w->started) return -1;

    if (w->cur && w->cur_len > 0) iow_submit_cur(w);

    OSMessage stop = { .message = NULL };
    OSSendMessage(&w->full_q, &stop, OS_MESSAGE_FLAGS_BLOCKING);

    int rval;
    OSJoinThread(&w->thread, &rval);

    free(w->mem);
    free(w->stack);
    w->mem = w->stack = NULL;
    w->started = false;
    return w->error ? -1 : 0;
}

int iow_stream_writer(void *user, const uint8_t *data, size_t len) {
    return iow_write((IoWriter *)user, data, len);
}
