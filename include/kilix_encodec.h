#ifndef KILIX_ENCODEC_H
#define KILIX_ENCODEC_H

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define KENC_VERSION_MAJOR 0u
#define KENC_VERSION_MINOR 1u
#define KENC_VERSION_PATCH 3u

#define KENC_SAMPLE_RATE_24KHZ 24000u
#define KENC_PACKET_SAMPLES 960u
#define KENC_DEFAULT_EPOCH_PACKETS 25u
#define KENC_CODEBOOK_CARDINALITY 1024u

#define KENC_PACKET_FLAG_RESET UINT8_C(0x01)
#define KENC_PACKET_FLAG_END UINT8_C(0x02)
#define KENC_PACKET_FLAG_DISCONTINUITY UINT8_C(0x04)

typedef struct kenc_model kenc_model;
typedef struct kenc_encoder kenc_encoder;
typedef struct kenc_decoder kenc_decoder;

typedef enum {
    KENC_OK = 0,
    KENC_ERR_INVALID,
    KENC_ERR_MODEL,
    KENC_ERR_RUNTIME,
    KENC_ERR_TRUNCATED,
    KENC_ERR_PROTOCOL,
    KENC_ERR_MEMORY
} kenc_result;

typedef struct {
    uint32_t sample_rate;
    uint16_t packet_samples;
    uint16_t epoch_packets;
    uint8_t codebooks;
    uint8_t threads;
} kenc_options;

typedef struct {
    uint64_t pts_ms;
    uint8_t flags;
    uint16_t samples;
} kenc_packet_info;

kenc_options kenc_options_default(void);
kenc_result kenc_options_validate(const kenc_options *options);
const char *kenc_result_string(kenc_result result);

kenc_result kenc_model_load(kenc_model **out, const char *asset_dir);
void kenc_model_free(kenc_model *model);

kenc_result kenc_encoder_create(
    kenc_encoder **out, kenc_model *model, const kenc_options *options);
void kenc_encoder_reset(kenc_encoder *encoder);
kenc_result kenc_encoder_push_s16(
    kenc_encoder *encoder, const int16_t *pcm, size_t sample_count,
    uint64_t pts_ms, uint8_t *packet, size_t capacity, size_t *written);
void kenc_encoder_free(kenc_encoder *encoder);

kenc_result kenc_decoder_create(
    kenc_decoder **out, kenc_model *model, const kenc_options *options);
void kenc_decoder_reset(kenc_decoder *decoder);
kenc_result kenc_decoder_pull_s16(
    kenc_decoder *decoder, const uint8_t *packet, size_t packet_size,
    int16_t *pcm, size_t pcm_capacity, size_t *samples_written,
    kenc_packet_info *info);
void kenc_decoder_free(kenc_decoder *decoder);

#ifdef __cplusplus
}
#endif

#endif
