// Host stand-in for <nds.h>: just the integer types the plain-C client code
// uses, so views/theme/gfx build on a PC (tests/render_screens.c).
#ifndef HOST_NDS_H
#define HOST_NDS_H
#include <stdint.h>
#include <stdbool.h>
typedef uint8_t u8;
typedef uint16_t u16;
typedef uint32_t u32;
typedef uint64_t u64;
typedef int8_t s8;
typedef int16_t s16;
typedef int32_t s32;
#endif
