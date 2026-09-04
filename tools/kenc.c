#include "kilix_encodec.h"

#include <stdio.h>
#include <string.h>

static int run_selftest(void)
{
    kenc_model *model = NULL;
    kenc_options options = kenc_options_default();
    unsigned int passed = 0u;
    const unsigned int total = 6u;

    passed += options.sample_rate == KENC_SAMPLE_RATE_24KHZ ? 1u : 0u;
    passed += options.packet_samples == KENC_PACKET_SAMPLES ? 1u : 0u;
    passed += options.epoch_packets == KENC_DEFAULT_EPOCH_PACKETS ? 1u : 0u;
    passed += options.codebooks == 8u ? 1u : 0u;
    passed += kenc_options_validate(&options) == KENC_OK ? 1u : 0u;
    passed += kenc_model_load(&model, "model-not-present") == KENC_ERR_MODEL
            && model == NULL
        ? 1u
        : 0u;

    printf("kenc selftest: %u/%u %s\n", passed, total,
        passed == total ? "PASS" : "FAIL");
    return passed == total ? 0 : 1;
}

int main(int argc, char **argv)
{
    if (argc == 2 && strcmp(argv[1], "--version") == 0) {
        printf("kenc %u.%u.%u\n", KENC_VERSION_MAJOR, KENC_VERSION_MINOR,
            KENC_VERSION_PATCH);
        return 0;
    }
    if (argc == 2 && strcmp(argv[1], "--selftest") == 0) {
        return run_selftest();
    }
    if (argc == 2 && strcmp(argv[1], "--help") == 0) {
        printf("usage: kenc --version | --selftest | --help\n");
        return 0;
    }

    fprintf(stderr, "usage: kenc --version | --selftest | --help\n");
    return 2;
}
