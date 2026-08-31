#include "kilix_encodec.h"

#include <stdlib.h>

struct kenc_decoder {
    unsigned int reserved;
};

kenc_result kenc_decoder_create(
    kenc_decoder **out, kenc_model *model, const kenc_options *options)
{
    kenc_result valid;

    if (out == NULL) {
        return KENC_ERR_INVALID;
    }
    *out = NULL;
    valid = kenc_options_validate(options);
    if (valid != KENC_OK) {
        return valid;
    }
    if (model == NULL) {
        return KENC_ERR_MODEL;
    }

    /* No decoder is constructed before the formal stateful-graph phase. */
    return KENC_ERR_RUNTIME;
}

void kenc_decoder_reset(kenc_decoder *decoder)
{
    (void)decoder;
}

kenc_result kenc_decoder_pull_s16(
    kenc_decoder *decoder, const uint8_t *packet, size_t packet_size,
    int16_t *pcm, size_t pcm_capacity, size_t *samples_written,
    kenc_packet_info *info)
{
    if (samples_written == NULL) {
        return KENC_ERR_INVALID;
    }
    *samples_written = 0u;
    if (info != NULL) {
        info->pts_ms = UINT64_C(0);
        info->flags = UINT8_C(0);
        info->samples = UINT16_C(0);
    }
    if (decoder == NULL || packet == NULL || packet_size == 0u || pcm == NULL
        || pcm_capacity < (size_t)KENC_PACKET_SAMPLES) {
        return KENC_ERR_INVALID;
    }
    return KENC_ERR_RUNTIME;
}

void kenc_decoder_free(kenc_decoder *decoder)
{
    free(decoder);
}

