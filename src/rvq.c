#include "internal.h"

#include <float.h>
#include <math.h>
#include <string.h>

_Static_assert(KENC_CODEBOOK_CARDINALITY == 1024u,
    "10-bit token packing requires 1,024-entry codebooks");
_Static_assert((1u << 10u) == KENC_CODEBOOK_CARDINALITY,
    "codebook cardinality must fit exactly in 10 bits");

/* The file-profile RVQ keeps the same float32 distance and residual-update
 * order as the reference. Equal scores choose the first codebook entry. */
kenc_result kenc_rvq_encode_frame(const float *latent, const float *books,
    const float *norms, uint8_t count, float *residual, uint16_t *codes)
{
    const size_t frames = KENC_STEREO_LATENT_FRAMES;
    memcpy(residual, latent, 128u * frames * sizeof(float));
    for (size_t book = 0u; book < count; ++book) {
        for (size_t frame = 0u; frame < frames; ++frame) {
            float norm = 0.0f, best = FLT_MAX;
            uint16_t selected = 0u;
            for (size_t dim = 0u; dim < 128u; ++dim) {
                float value = residual[dim * frames + frame];
                norm += value * value;
            }
            for (size_t entry = 0u; entry < KENC_CODEBOOK_CARDINALITY; ++entry) {
                const float *vector = books + (book * KENC_CODEBOOK_CARDINALITY + entry) * 128u;
                float cross = 0.0f;
                for (size_t dim = 0u; dim < 128u; ++dim) {
                    cross += residual[dim * frames + frame] * vector[dim];
                }
                float distance = norm - 2.0f * cross + norms[book * KENC_CODEBOOK_CARDINALITY + entry];
                if (!isfinite(distance)) { return KENC_ERR_RUNTIME; }
                if (distance < best) { best = distance; selected = (uint16_t)entry; }
            }
            codes[book * frames + frame] = selected;
            const float *vector = books + (book * KENC_CODEBOOK_CARDINALITY + selected) * 128u;
            for (size_t dim = 0u; dim < 128u; ++dim) {
                residual[dim * frames + frame] -= vector[dim];
            }
        }
    }
    return KENC_OK;
}

void kenc_rvq_decode_frame(const uint16_t *codes, const float *books,
    uint8_t count, float *quantized)
{
    const size_t frames = KENC_STEREO_LATENT_FRAMES;
    memset(quantized, 0, 128u * frames * sizeof(float));
    for (size_t book = 0u; book < count; ++book) {
        for (size_t frame = 0u; frame < frames; ++frame) {
            const float *vector = books
                + (book * KENC_CODEBOOK_CARDINALITY + codes[book * frames + frame]) * 128u;
            for (size_t dim = 0u; dim < 128u; ++dim) {
                quantized[dim * frames + frame] += vector[dim];
            }
        }
    }
}
