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
    case KENC_ERR_EPOCH_START:
        return "unsupported or mismatched epoch-start profile marker";
    default:
        return "unknown result";
    }
}

uint32_t kenc_epoch_start_supported(void)
{
    return KENC_EPOCH_START_ALL;
}

kenc_result kenc_epoch_start_negotiate(uint32_t local, uint32_t peer,
    kenc_epoch_start *selected)
{
    const uint32_t c0 = KENC_EPOCH_START_BIT(KENC_EPOCH_START_C0);
    const uint32_t c5_r4 = KENC_EPOCH_START_BIT(KENC_EPOCH_START_C5_R4);
    if (selected == NULL || (local & c0) == 0u || (local & ~KENC_EPOCH_START_ALL) != 0u) {
        return KENC_ERR_INVALID;
    }
    if (peer != 0u && (peer & c0) == 0u) {
        return KENC_ERR_EPOCH_START;
    }
    *selected = (local & peer & c5_r4) != 0u ? KENC_EPOCH_START_C5_R4 : KENC_EPOCH_START_C0;
    return KENC_OK;
}

kenc_result kenc_epoch_start_from_marker(uint32_t marker, kenc_epoch_start *profile)
{
    if (profile == NULL) {
        return KENC_ERR_INVALID;
    }
    if (marker == (uint32_t)KENC_EPOCH_START_C0) {
        *profile = KENC_EPOCH_START_C0;
    } else if (marker == (uint32_t)KENC_EPOCH_START_C5_R4) {
        *profile = KENC_EPOCH_START_C5_R4;
    } else {
        return KENC_ERR_EPOCH_START;
    }
    return KENC_OK;
}

const char *kenc_epoch_start_name(kenc_epoch_start profile)
{
    switch (profile) {
    case KENC_EPOCH_START_C0:
        return "C0";
    case KENC_EPOCH_START_C5_R4:
        return "C5-R4";
    default:
        return NULL;
    }
}

