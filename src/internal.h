#ifndef KENC_INTERNAL_H
#define KENC_INTERNAL_H

#include "kilix_encodec.h"

#define KENC_LATENT_FRAMES 3u
/* A C5-R4 epoch start primes zeroed stream state by running the network over
 * the epoch's first packet this many times, discarding those outputs. C0 runs
 * no lead-in. */
#define KENC_PREROLL_PACKETS 4u
#define KENC_MAX_CODEBOOKS 16u
#define KENC_MAX_TOKENS (KENC_LATENT_FRAMES * KENC_MAX_CODEBOOKS)
#define KENC_PACKET_CAPACITY KENC_MAX_PACKET_BYTES

/* Tokens use codebook-major storage, with a fixed three-frame stride. */
typedef struct {
    uint64_t epoch;
    uint64_t index;
    uint64_t pts_ms;
    uint16_t samples;
    uint8_t flags;
    uint8_t codebooks;
    uint16_t codes[KENC_MAX_TOKENS];
} kenc_wire_packet;

kenc_result kenc_packet_write(const kenc_wire_packet *value,
    uint8_t *buffer, size_t capacity, size_t *written);
kenc_result kenc_packet_read(kenc_wire_packet *value,
    const uint8_t *buffer, size_t length, uint8_t codebooks);

typedef struct kenc_native_stream kenc_native_stream;
kenc_result kenc_native_create(kenc_native_stream **out, kenc_model *model,
    const kenc_options *options, int encoding);
void kenc_native_reset(kenc_native_stream *stream);
/* Lead-in runs at every later epoch start: 0 (C0) or KENC_PREROLL_PACKETS. */
void kenc_native_set_preroll(kenc_native_stream *stream, unsigned int packets);
void kenc_native_free(kenc_native_stream *stream);
kenc_result kenc_native_encode(kenc_native_stream *stream,
    const int16_t *pcm, uint16_t *codes);
kenc_result kenc_native_decode(kenc_native_stream *stream,
    const uint16_t *codes, int16_t *pcm);

kenc_result kenc_rvq_encode_frame(const float *latent, const float *books,
    const float *norms, uint8_t count, float *residual, uint16_t *codes);
void kenc_rvq_decode_frame(const uint16_t *codes, const float *books,
    uint8_t count, float *quantized);

#endif
