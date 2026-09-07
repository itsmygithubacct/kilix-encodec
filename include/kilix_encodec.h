#ifndef KILIX_ENCODEC_H
#define KILIX_ENCODEC_H

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define KENC_VERSION_MAJOR 0u
#define KENC_VERSION_MINOR 1u
#define KENC_VERSION_PATCH 5u

#define KENC_SAMPLE_RATE_24KHZ 24000u
#define KENC_PACKET_SAMPLES 960u
#define KENC_DEFAULT_EPOCH_PACKETS 25u
#define KENC_CODEBOOK_CARDINALITY 1024u
#define KENC_MAX_PACKET_BYTES 160u
#define KENC_STEREO_FRAME_SAMPLES 48000u
#define KENC_STEREO_STRIDE_SAMPLES 47520u
#define KENC_STEREO_LATENT_FRAMES 150u

#define KENC_PACKET_FLAG_RESET UINT8_C(0x01)
#define KENC_PACKET_FLAG_END UINT8_C(0x02)
#define KENC_PACKET_FLAG_DISCONTINUITY UINT8_C(0x04)

typedef struct kenc_model kenc_model;
typedef struct kenc_encoder kenc_encoder;
typedef struct kenc_decoder kenc_decoder;
typedef struct kenc_stereo kenc_stereo;

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

/* Load the exact pinned 24 kHz user-supplied export. Symlinks, special files,
 * changed manifests and changed graph bytes are refused before ORT parses
 * anything. Builds without ONNX support return KENC_ERR_MODEL. No network or
 * Python is used. Successful streams retain the model until they are freed. */
kenc_result kenc_model_load(kenc_model **out, const char *asset_dir);
void kenc_model_free(kenc_model *model);

/* A context belongs to one stream and must not be used concurrently. The model
 * can be shared by independent contexts. PCM is signed native-endian mono at
 * 24 kHz; each push consumes exactly 960 samples. PTS advances by 40 ms until an
 * explicit reset. The encoder alone owns RESET and the configured epoch cadence.
 * A buffer of KENC_MAX_PACKET_BYTES always holds a supported packet. A short
 * output buffer produces no bytes and does not advance the stream. */
kenc_result kenc_encoder_create(
    kenc_encoder **out, kenc_model *model, const kenc_options *options);
void kenc_encoder_reset(kenc_encoder *encoder);
kenc_result kenc_encoder_push_s16(
    kenc_encoder *encoder, const int16_t *pcm, size_t sample_count,
    uint64_t pts_ms, uint8_t *packet, size_t capacity, size_t *written);
void kenc_encoder_free(kenc_encoder *encoder);

/* Loss or reordering requires a later RESET packet. A newly created/reset
 * decoder joins at any RESET; a running decoder refuses replayed epochs.
 * Malformed packets and short output buffers do not write PCM. The decoder
 * returns only verified packet metadata and at most KENC_PACKET_SAMPLES. */
kenc_result kenc_decoder_create(
    kenc_decoder **out, kenc_model *model, const kenc_options *options);
void kenc_decoder_reset(kenc_decoder *decoder);
kenc_result kenc_decoder_pull_s16(
    kenc_decoder *decoder, const uint8_t *packet, size_t packet_size,
    int16_t *pcm, size_t pcm_capacity, size_t *samples_written,
    kenc_packet_info *info);
void kenc_decoder_free(kenc_decoder *decoder);

/* Separate noncausal 48 kHz stereo frame profile, for local files only. It
 * cannot be passed to the 24 kHz streaming/KMX API. Input/output floats are
 * interleaved stereo; each frame contains exactly 48000 samples per channel.
 * Callers own all buffers and overlap-add adjacent decoded frames at the
 * 47520-sample stride. Codes are codebook-major with a 150-frame stride.
 * Only 2/4/8/16 codebooks and 1/2 threads are supported. No network/Python or
 * model publication occurs. The context must not be used concurrently. */
kenc_result kenc_stereo_create(kenc_stereo **out, const char *asset_dir,
    uint8_t codebooks, uint8_t threads);
kenc_result kenc_stereo_encode_frame(kenc_stereo *codec,
    const float *pcm, size_t sample_count, uint16_t *codes,
    size_t code_capacity, float *scale);
kenc_result kenc_stereo_decode_frame(kenc_stereo *codec,
    const uint16_t *codes, size_t code_count, float scale,
    float *pcm, size_t pcm_capacity);
void kenc_stereo_free(kenc_stereo *codec);

#ifdef __cplusplus
}
#endif

#endif
