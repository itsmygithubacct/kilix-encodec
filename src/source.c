#include "kilix_encodec_file.h"

#include <stdlib.h>
#include <string.h>

struct kenc_file_source {
    kenc_file_reader *reader;
    kenc_file_info info;
    kenc_model *model;
    kenc_decoder *mono;
    kenc_stereo *stereo;
    kenc_stereo_overlap overlap;
    float *frame;
    uint64_t position;
    int faulted;
};

void kenc_file_source_free(kenc_file_source *source)
{
    if (source == NULL) { return; }
    kenc_file_reader_free(source->reader);
    kenc_decoder_free(source->mono);
    kenc_model_free(source->model);
    kenc_stereo_free(source->stereo);
    free(source->frame);
    free(source);
}

kenc_result kenc_file_source_create(kenc_file_source **out, int descriptor,
    const char *mono_assets, const char *stereo_assets, uint8_t threads,
    kenc_file_info *info)
{
    if (out == NULL) { return KENC_ERR_INVALID; }
    *out = NULL;
    if (info == NULL || (threads != 1u && threads != 2u)) { return KENC_ERR_INVALID; }
    kenc_file_source *source = calloc(1u, sizeof(*source));
    if (source == NULL) { return KENC_ERR_MEMORY; }
    kenc_result result = kenc_file_reader_create(&source->reader, descriptor, &source->info);
    if (result != KENC_OK) { goto done; }
    if (source->info.profile == KENC_FILE_PROFILE_MONO) {
        result = kenc_model_load(&source->model, mono_assets);
        if (result != KENC_OK) { goto done; }
        kenc_options options = kenc_options_default();
        options.codebooks = source->info.codebooks;
        options.threads = threads;
        result = kenc_decoder_create(&source->mono, source->model, &options);
    } else {
        result = kenc_stereo_create(&source->stereo, stereo_assets, source->info.codebooks, threads);
    }
    if (result != KENC_OK) { goto done; }
    source->frame = calloc(source->info.profile == KENC_FILE_PROFILE_MONO ? 960u : 96000u,
        sizeof(*source->frame));
    if (source->frame == NULL) { result = KENC_ERR_MEMORY; goto done; }
    *info = source->info;
    *out = source;
    source = NULL;
done:
    kenc_file_source_free(source);
    return result;
}

static kenc_result decode_next(kenc_file_source *source)
{
    uint8_t record[KENC_FILE_MAX_RECORD_BYTES];
    size_t bytes = 0u;
    uint64_t position = 0u;
    kenc_result result = kenc_file_reader_next(source->reader, record, sizeof(record), &bytes, &position);
    if (result != KENC_OK) { return result; }
    if (bytes == 0u || position != source->position) { return KENC_ERR_PROTOCOL; }
    if (source->info.profile == KENC_FILE_PROFILE_MONO) {
        int16_t pcm[KENC_PACKET_SAMPLES];
        size_t count = 0u;
        kenc_packet_info packet_info;
        result = kenc_decoder_pull_s16(source->mono, record, bytes, pcm,
            KENC_PACKET_SAMPLES, &count, &packet_info);
        if (result != KENC_OK) { return result; }
        if (count != KENC_PACKET_SAMPLES) { return KENC_ERR_PROTOCOL; }
        for (size_t i = 0u; i < count; ++i) { source->frame[i] = (float)pcm[i] / 32768.0f; }
    } else {
        uint16_t codes[16u * KENC_STEREO_LATENT_FRAMES];
        float scale = 0.0f;
        result = kenc_stereo_record_read(record, bytes, source->info.codebooks,
            codes, sizeof(codes) / sizeof(codes[0]), &scale);
        if (result != KENC_OK) { return result; }
        result = kenc_stereo_decode_frame(source->stereo, codes,
            (size_t)source->info.codebooks * KENC_STEREO_LATENT_FRAMES,
            scale, source->frame, 96000u);
        if (result != KENC_OK) { return result; }
        result = kenc_stereo_overlap_apply(&source->overlap, source->frame, 96000u);
    }
    return result;
}

kenc_result kenc_file_source_pull_f32(kenc_file_source *source, float *pcm,
    size_t scalar_capacity, size_t *samples_written, uint64_t *sample_position)
{
    if (samples_written == NULL) { return KENC_ERR_INVALID; }
    *samples_written = 0u;
    if (source == NULL || pcm == NULL || sample_position == NULL) { return KENC_ERR_INVALID; }
    if (source->faulted) { return KENC_ERR_RUNTIME; }
    if (source->position >= source->info.samples) {
        *sample_position = source->info.samples;
        return KENC_OK;
    }
    size_t stride = source->info.profile == KENC_FILE_PROFILE_MONO ? 960u : 47520u;
    size_t channels = source->info.profile == KENC_FILE_PROFILE_MONO ? 1u : 2u;
    uint64_t remaining = source->info.samples - source->position;
    size_t count = remaining < stride ? (size_t)remaining : stride;
    if (scalar_capacity < count * channels) { return KENC_ERR_TRUNCATED; }
    kenc_result result = decode_next(source);
    if (result != KENC_OK) { source->faulted = 1; return result; }
    memcpy(pcm, source->frame, count * channels * sizeof(*pcm));
    *sample_position = source->position;
    *samples_written = count;
    source->position += count;
    return KENC_OK;
}

kenc_result kenc_file_source_seek(kenc_file_source *source,
    uint64_t requested_sample, uint64_t *actual_sample)
{
    if (source == NULL || actual_sample == NULL || requested_sample >= source->info.samples) {
        return KENC_ERR_INVALID;
    }
    uint64_t span = source->info.profile == KENC_FILE_PROFILE_MONO ? 24000u : 47520u;
    uint64_t boundary = requested_sample / span * span;
    uint64_t start = source->info.profile == KENC_FILE_PROFILE_STEREO && boundary > 0u
        ? boundary - span : boundary;
    uint64_t reached = 0u;
    kenc_result result = kenc_file_reader_seek(source->reader, start, &reached);
    if (result != KENC_OK) { return result; }
    source->faulted = 1;
    if (reached != start) { return KENC_ERR_PROTOCOL; }
    source->position = start;
    kenc_decoder_reset(source->mono);
    kenc_stereo_overlap_reset(&source->overlap);
    if (start != boundary) {
        result = decode_next(source);
        if (result != KENC_OK) { return result; }
        source->position = boundary;
    }
    source->faulted = 0;
    *actual_sample = boundary;
    return KENC_OK;
}
