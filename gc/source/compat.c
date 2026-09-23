/*
 * compat.c — symbol shims for prebuilt libraries.
 *
 * The installed libgxflux.a was built against an older libogc where
 * DCFlushRange() was an exported function.  Current libogc makes it a
 * static-inline (MK_INLINE) wrapper, so the symbol is gone from libogc.a and
 * the prebuilt gxflux fails to link.  Provide a real, self-contained
 * DCFlushRange so we don't depend on any particular libogc internal symbol.
 *
 * Gekko / Broadway data cache lines are 32 bytes.  dcbf flushes (and
 * invalidates) the block containing the address; the trailing sync stalls
 * until the stores have drained to main memory, matching libogc semantics.
 */

#include <gctypes.h>

void DCFlushRange(void *startaddress, u32 len) {
    u32 addr = (u32)startaddress & ~31u;
    u32 end  = ((u32)startaddress + len + 31u) & ~31u;
    for (; addr < end; addr += 32)
        __asm__ volatile ("dcbf 0,%0" :: "r"(addr) : "memory");
    __asm__ volatile ("sync" ::: "memory");
}
