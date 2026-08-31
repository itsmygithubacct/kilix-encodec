#include <kilix_encodec.h>

#include <stdio.h>

int main(void)
{
    unsigned int passed = 0u;
    const unsigned int total = 5u;
    kenc_options options = kenc_options_default();

    passed += KENC_VERSION_MAJOR == 0u ? 1u : 0u;
    passed += KENC_VERSION_MINOR == 1u ? 1u : 0u;
    passed += KENC_VERSION_PATCH == 2u ? 1u : 0u;
    passed += options.sample_rate == KENC_SAMPLE_RATE_24KHZ ? 1u : 0u;
    passed += kenc_options_validate(&options) == KENC_OK ? 1u : 0u;
    printf("installed consumer: %u/%u %s\n", passed, total,
        passed == total ? "PASS" : "FAIL");
    return passed == total ? 0 : 1;
}
