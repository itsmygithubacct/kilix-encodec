#include "kilix_encodec.h"

kenc_options kenc_options_default(void)
{
    kenc_options options = {
        .sample_rate = KENC_SAMPLE_RATE_24KHZ,
        .packet_samples = KENC_PACKET_SAMPLES,
        .epoch_packets = KENC_DEFAULT_EPOCH_PACKETS,
        .codebooks = UINT8_C(8),
        .threads = UINT8_C(1),
    };
    return options;
}

kenc_result kenc_options_validate(const kenc_options *options)
{
    if (options == NULL) {
        return KENC_ERR_INVALID;
    }
    if (options->sample_rate != KENC_SAMPLE_RATE_24KHZ
        || options->packet_samples != KENC_PACKET_SAMPLES
        || options->epoch_packets == 0u
        || options->threads == 0u
        || options->threads > 2u) {
        return KENC_ERR_INVALID;
    }
    if (options->codebooks != 4u && options->codebooks != 8u
        && options->codebooks != 16u) {
        return KENC_ERR_INVALID;
    }
    return KENC_OK;
}

const char *kenc_result_string(kenc_result result)
{
    switch (result) {
    case KENC_OK:
        return "ok";
    case KENC_ERR_INVALID:
        return "invalid argument";
    case KENC_ERR_MODEL:
        return "model unavailable or invalid";
    case KENC_ERR_RUNTIME:
        return "runtime failure";
    case KENC_ERR_TRUNCATED:
        return "truncated input";
    case KENC_ERR_PROTOCOL:
        return "protocol error";
    case KENC_ERR_MEMORY:
        return "memory allocation failed";
    default:
        return "unknown result";
    }
}

