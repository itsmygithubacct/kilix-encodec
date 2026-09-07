#include "kilix_encodec.h"
#include "test.h"

#include <math.h>
#include <stdlib.h>

int main(int argc, char **argv)
{
    unsigned int passed = 0u, total = 0u;
    kenc_stereo *codec = NULL;
    float *input = NULL, *output = NULL;
    uint16_t codes[8u * KENC_STEREO_LATENT_FRAMES];
    const size_t frames = KENC_STEREO_FRAME_SAMPLES;
    const size_t count = sizeof(codes) / sizeof(codes[0]);
    float scale = -1.0f;
    if (argc != 2) { fputs("usage: test-stereo MODEL_DIR\n", stderr); return 2; }
    TEST_CHECK(kenc_stereo_create(NULL, argv[1], 8u, 1u) == KENC_ERR_INVALID);
    TEST_CHECK(kenc_stereo_create(&codec, argv[1], 3u, 1u) == KENC_ERR_INVALID && codec == NULL);
    TEST_CHECK(kenc_stereo_create(&codec, argv[1], 8u, 0u) == KENC_ERR_INVALID && codec == NULL);
    TEST_CHECK(kenc_stereo_create(&codec, argv[1], 8u, 1u) == KENC_OK);
    if (codec == NULL) { return 1; }
    input = malloc(frames * 2u * sizeof(float));
    output = malloc(frames * 2u * sizeof(float));
    if (input == NULL || output == NULL) {
        free(input); free(output); kenc_stereo_free(codec); return 1;
    }
    for (size_t i = 0u; i < frames * 2u; ++i) {
        input[i] = (float)((int)(i % 251u) - 125) / 251.0f;
        output[i] = 12345.0f;
    }
    TEST_CHECK(kenc_stereo_encode_frame(codec, input, frames - 1u, codes,
        count, &scale) == KENC_ERR_INVALID && scale == -1.0f);
    TEST_CHECK(kenc_stereo_encode_frame(codec, input, frames, codes,
        count - 1u, &scale) == KENC_ERR_TRUNCATED && scale == -1.0f);
    TEST_CHECK(kenc_stereo_encode_frame(codec, input, frames, codes,
        count, &scale) == KENC_OK && scale > 0.0f && scale <= 2.0f);
    TEST_CHECK(kenc_stereo_decode_frame(codec, codes, count, scale,
        output, frames * 2u - 1u) == KENC_ERR_TRUNCATED && output[0] == 12345.0f);
    const float invalid[] = {NAN, INFINITY, -INFINITY, 0.0f, -1.0f, 2.01f};
    for (size_t i = 0u; i < sizeof(invalid) / sizeof(invalid[0]); ++i) {
        TEST_CHECK(kenc_stereo_decode_frame(codec, codes, count, invalid[i],
            output, frames * 2u) == KENC_ERR_PROTOCOL && output[0] == 12345.0f);
    }
    TEST_CHECK(kenc_stereo_decode_frame(codec, codes, count, scale,
        output, frames * 2u) == KENC_OK && isfinite(output[0]));
    codes[0] = KENC_CODEBOOK_CARDINALITY;
    output[0] = 12345.0f;
    TEST_CHECK(kenc_stereo_decode_frame(codec, codes, count, scale,
        output, frames * 2u) == KENC_ERR_PROTOCOL && output[0] == 12345.0f);
    input[0] = NAN;
    TEST_CHECK(kenc_stereo_encode_frame(codec, input, frames, codes,
        count, &scale) == KENC_ERR_INVALID);
    free(input);
    free(output);
    kenc_stereo_free(codec);
    kenc_stereo_free(NULL);
    return test_summary("native stereo", passed, total);
}
