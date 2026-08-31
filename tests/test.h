#ifndef KENC_TEST_H
#define KENC_TEST_H

#include <stdio.h>

#define TEST_CHECK(expression)                                                \
    do {                                                                      \
        total += 1u;                                                          \
        if (expression) {                                                     \
            passed += 1u;                                                     \
        } else {                                                              \
            fprintf(stderr, "%s:%d: check failed: %s\n", __FILE__, __LINE__, \
                #expression);                                                 \
        }                                                                     \
    } while (0)

static int test_summary(
    const char *name, unsigned int passed, unsigned int total)
{
    printf("%s: %u/%u %s\n", name, passed, total,
        passed == total ? "PASS" : "FAIL");
    return passed == total ? 0 : 1;
}

#endif

