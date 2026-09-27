#ifndef TEST_UTIL_H
#define TEST_UTIL_H

#include <stdio.h>
#include <string.h>

static int test_failures = 0;
static int test_checks = 0;

#define CHECK(cond) do { \
    test_checks++; \
    if (!(cond)) { \
        test_failures++; \
        fprintf(stderr, "%s:%d: CHECK failed: %s\n", __FILE__, __LINE__, #cond); \
    } \
} while (0)

#define CHECK_STR(a, b) do { \
    test_checks++; \
    if (strcmp((a), (b)) != 0) { \
        test_failures++; \
        fprintf(stderr, "%s:%d: \"%s\" != \"%s\"\n", __FILE__, __LINE__, (a), (b)); \
    } \
} while (0)

#define TEST_DONE() do { \
    printf("%s: %d checks, %d failed\n", __FILE__, test_checks, test_failures); \
    return test_failures ? 1 : 0; \
} while (0)

#endif
