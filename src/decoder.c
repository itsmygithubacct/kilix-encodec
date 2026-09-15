#include "internal.h"

#include <stdlib.h>
#include <string.h>

struct kenc_decoder {
    kenc_native_stream *native;
    kenc_options options;
    uint64_t epoch;
    uint64_t next_index;
    uint64_t last_pts;
    kenc_epoch_start epoch_start;
    int has_epoch;
    int needs_reset;
};

kenc_result kenc_decoder_set_epoch_start(kenc_decoder *decoder, kenc_epoch_start profile)
{
    kenc_epoch_start known;
    if (decoder == NULL) { return KENC_ERR_INVALID; }
    if (kenc_epoch_start_from_marker((uint32_t)profile, &known) != KENC_OK) {
        return KENC_ERR_EPOCH_START;
    }
    if (decoder->has_epoch) { return KENC_ERR_PROTOCOL; }
    decoder->epoch_start = known;
    kenc_native_set_preroll(decoder->native,
        known == KENC_EPOCH_START_C5_R4 ? KENC_PREROLL_PACKETS : 0u);
    return KENC_OK;
}

kenc_result kenc_decoder_create(kenc_decoder **out, kenc_model *model,
    const kenc_options *options)
{
    kenc_decoder *decoder;
    kenc_result result;
    if (out == NULL) { return KENC_ERR_INVALID; }
    *out = NULL;
    result = kenc_options_validate(options);
    if (result != KENC_OK) { return result; }
    if (model == NULL) { return KENC_ERR_MODEL; }
    decoder = calloc(1u, sizeof(*decoder));
    if (decoder == NULL) { return KENC_ERR_MEMORY; }
    result = kenc_native_create(&decoder->native, model, options, 0);
    if (result != KENC_OK) { free(decoder); return result; }
    decoder->options = *options;
    decoder->epoch_start = KENC_EPOCH_START_C0;
    decoder->needs_reset = 1;
    *out = decoder;
    return KENC_OK;
}

void kenc_decoder_reset(kenc_decoder *decoder)
{
    if (decoder == NULL) { return; }
    kenc_native_reset(decoder->native);
    decoder->has_epoch = 0;
    decoder->needs_reset = 1;
}

kenc_result kenc_decoder_pull_s16(kenc_decoder *decoder, const uint8_t *packet,
    size_t packet_size, int16_t *pcm, size_t pcm_capacity,
    size_t *samples_written, kenc_packet_info *info)
{
    kenc_wire_packet value;
    int16_t output[KENC_PACKET_SAMPLES];
    kenc_result result;
    int reset;
    if (samples_written == NULL) { return KENC_ERR_INVALID; }
    *samples_written = 0u;
    if (info != NULL) {
        info->pts_ms = 0u;
        info->flags = 0u;
        info->samples = 0u;
    }
    if (decoder == NULL || packet == NULL || packet_size == 0u || pcm == NULL) {
        return KENC_ERR_INVALID;
    }
    result = kenc_packet_read(&value, packet, packet_size, decoder->options.codebooks);
    if (result != KENC_OK) { return result; }
    if (pcm_capacity < value.samples) { return KENC_ERR_INVALID; }
    reset = (value.flags & KENC_PACKET_FLAG_RESET) != 0u;
    if (value.index >= decoder->options.epoch_packets
        || (value.samples != KENC_PACKET_SAMPLES && (value.flags & KENC_PACKET_FLAG_END) == 0u)) {
        return KENC_ERR_PROTOCOL;
    }
    if (reset && ((value.flags & KENC_PACKET_FLAG_EPOCH_PREROLL) != 0u)
            != (decoder->epoch_start == KENC_EPOCH_START_C5_R4)) {
        return KENC_ERR_EPOCH_START;
    }
    if (reset) {
        if (decoder->has_epoch && value.epoch <= decoder->epoch) { return KENC_ERR_PROTOCOL; }
    } else if (decoder->needs_reset || !decoder->has_epoch
        || value.epoch != decoder->epoch || value.index != decoder->next_index
        || decoder->last_pts > UINT64_MAX - 40u || value.pts_ms != decoder->last_pts + 40u) {
        decoder->needs_reset = 1;
        return KENC_ERR_PROTOCOL;
    }
    if (reset) { kenc_native_reset(decoder->native); }
    result = kenc_native_decode(decoder->native, value.codes, output);
    if (result != KENC_OK) {
        decoder->needs_reset = 1;
        return result;
    }
    memcpy(pcm, output, (size_t)value.samples * sizeof(*pcm));
    decoder->epoch = value.epoch;
    decoder->next_index = value.index + 1u;
    decoder->last_pts = value.pts_ms;
    decoder->has_epoch = 1;
    decoder->needs_reset = (value.flags & KENC_PACKET_FLAG_END) != 0u
        || decoder->next_index == decoder->options.epoch_packets;
    *samples_written = value.samples;
    if (info != NULL) {
        info->pts_ms = value.pts_ms;
        info->flags = value.flags;
        info->samples = value.samples;
    }
    return KENC_OK;
}

void kenc_decoder_free(kenc_decoder *decoder)
{
    if (decoder == NULL) { return; }
    kenc_native_free(decoder->native);
    free(decoder);
}
