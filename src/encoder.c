#include "kilix_encodec.h"

#include <stdlib.h>

struct kenc_encoder {
    unsigned int reserved;
};

kenc_result kenc_encoder_create(
    kenc_encoder **out, kenc_model *model, const kenc_options *options)
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

    /* No encoder is constructed before the formal stateful-graph phase. */
    return KENC_ERR_RUNTIME;
}

void kenc_encoder_reset(kenc_encoder *encoder)
{
    (void)encoder;
}

kenc_result kenc_encoder_push_s16(
    kenc_encoder *encoder, const int16_t *pcm, size_t sample_count,
    uint64_t pts_ms, uint8_t *packet, size_t capacity, size_t *written)
{
    (void)pts_ms;

    if (written == NULL) {
        return KENC_ERR_INVALID;
    }
    *written = 0u;
    if (encoder == NULL || pcm == NULL || packet == NULL || capacity == 0u
        || sample_count != (size_t)KENC_PACKET_SAMPLES) {
        return KENC_ERR_INVALID;
    }
    return KENC_ERR_RUNTIME;
}

void kenc_encoder_free(kenc_encoder *encoder)
{
    free(encoder);
}

