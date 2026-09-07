#include "internal.h"

#include <stdlib.h>

struct kenc_encoder {
    kenc_native_stream *native;
    kenc_options options;
    uint64_t epoch;
    uint64_t index;
    uint64_t last_pts;
    uint8_t flags;
    int emitted;
    int exhausted;
};

kenc_result kenc_encoder_create(kenc_encoder **out, kenc_model *model,
    const kenc_options *options)
{
    kenc_encoder *encoder;
    kenc_result result;
    if (out == NULL) { return KENC_ERR_INVALID; }
    *out = NULL;
    result = kenc_options_validate(options);
    if (result != KENC_OK) { return result; }
    if (model == NULL) { return KENC_ERR_MODEL; }
    encoder = calloc(1u, sizeof(*encoder));
    if (encoder == NULL) { return KENC_ERR_MEMORY; }
    result = kenc_native_create(&encoder->native, model, options, 1);
    if (result != KENC_OK) { free(encoder); return result; }
    encoder->options = *options;
    encoder->flags = KENC_PACKET_FLAG_RESET;
    *out = encoder;
    return KENC_OK;
}

void kenc_encoder_reset(kenc_encoder *encoder)
{
    if (encoder == NULL) { return; }
    if (encoder->emitted && (encoder->flags & KENC_PACKET_FLAG_DISCONTINUITY) == 0u) {
        if (encoder->epoch == UINT64_MAX) { encoder->exhausted = 1; }
        else { ++encoder->epoch; }
    }
    encoder->index = 0u;
    encoder->flags = KENC_PACKET_FLAG_RESET | KENC_PACKET_FLAG_DISCONTINUITY;
    kenc_native_reset(encoder->native);
}

kenc_result kenc_encoder_push_s16(kenc_encoder *encoder, const int16_t *pcm,
    size_t sample_count, uint64_t pts_ms, uint8_t *packet, size_t capacity,
    size_t *written)
{
    kenc_wire_packet value = {0};
    uint8_t scratch[KENC_PACKET_CAPACITY];
    size_t required = 0u;
    kenc_result result;
    int boundary;
    if (written == NULL) { return KENC_ERR_INVALID; }
    *written = 0u;
    if (encoder == NULL || pcm == NULL || packet == NULL || capacity == 0u
        || sample_count != (size_t)KENC_PACKET_SAMPLES) { return KENC_ERR_INVALID; }
    if (encoder->exhausted) { return KENC_ERR_PROTOCOL; }
    if (encoder->emitted && (encoder->flags & KENC_PACKET_FLAG_DISCONTINUITY) == 0u
        && (encoder->last_pts > UINT64_MAX - 40u || pts_ms != encoder->last_pts + 40u)) {
        return KENC_ERR_PROTOCOL;
    }
    boundary = encoder->index == encoder->options.epoch_packets;
    if (boundary && encoder->epoch == UINT64_MAX) { return KENC_ERR_PROTOCOL; }
    value.epoch = encoder->epoch + (boundary ? 1u : 0u);
    value.index = boundary ? 0u : encoder->index;
    value.pts_ms = pts_ms;
    value.flags = boundary ? KENC_PACKET_FLAG_RESET : encoder->flags;
    value.samples = KENC_PACKET_SAMPLES;
    value.codebooks = encoder->options.codebooks;
    /* Check the complete variable-width header before touching stream state. */
    result = kenc_packet_write(&value, scratch, sizeof(scratch), &required);
    if (result != KENC_OK) { return result; }
    if (capacity < required) { return KENC_ERR_TRUNCATED; }
    if (boundary) { kenc_native_reset(encoder->native); }
    result = kenc_native_encode(encoder->native, pcm, value.codes);
    if (result != KENC_OK) {
        kenc_encoder_reset(encoder);
        return result;
    }
    result = kenc_packet_write(&value, packet, capacity, written);
    if (result != KENC_OK) {
        kenc_encoder_reset(encoder);
        return result;
    }
    encoder->epoch = value.epoch;
    encoder->index = value.index + 1u;
    encoder->flags = 0u;
    encoder->last_pts = pts_ms;
    encoder->emitted = 1;
    return KENC_OK;
}

void kenc_encoder_free(kenc_encoder *encoder)
{
    if (encoder == NULL) { return; }
    kenc_native_free(encoder->native);
    free(encoder);
}
