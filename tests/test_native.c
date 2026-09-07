#include "kilix_encodec.h"
#include "test.h"

#include <stdio.h>
#include <string.h>

/* Standalone positive test so leak checking does not include an embedding
 * Python interpreter. Assets are supplied explicitly; never downloaded. */
int main(int argc, char **argv)
{
    unsigned int passed = 0u, total = 0u;
    kenc_model *model = NULL;
    const uint8_t rates[] = {4u, 8u, 16u};
    if (argc != 2) { fputs("usage: test-native-c MODEL_DIR\n", stderr); return 2; }
    TEST_CHECK(kenc_model_load(&model, argv[1]) == KENC_OK);
    if (model == NULL) { return 1; }
    for (size_t rate = 0u; rate < sizeof(rates); ++rate) {
        kenc_options options = kenc_options_default();
        kenc_encoder *encoder = NULL;
        kenc_decoder *decoder = NULL;
        int16_t pcm[KENC_PACKET_SAMPLES];
        int16_t decoded[KENC_PACKET_SAMPLES];
        uint8_t packet[KENC_MAX_PACKET_BYTES];
        options.codebooks = rates[rate];
        TEST_CHECK(kenc_encoder_create(&encoder, model, &options) == KENC_OK);
        TEST_CHECK(kenc_decoder_create(&decoder, model, &options) == KENC_OK);
        if (encoder == NULL || decoder == NULL) {
            kenc_encoder_free(encoder); kenc_decoder_free(decoder);
            kenc_model_free(model); return 1;
        }
        for (size_t frame = 0u; frame < 27u; ++frame) {
            size_t bytes = 0u, samples = 0u;
            kenc_packet_info info;
            for (size_t i = 0u; i < KENC_PACKET_SAMPLES; ++i) {
                pcm[i] = (int16_t)((int)((frame * KENC_PACKET_SAMPLES + i) % 200u) * 120 - 12000);
            }
            TEST_CHECK(kenc_encoder_push_s16(encoder, pcm, KENC_PACKET_SAMPLES,
                frame * 40u, packet, sizeof(packet), &bytes) == KENC_OK);
            TEST_CHECK(bytes > 0u && bytes <= sizeof(packet));
            TEST_CHECK(kenc_decoder_pull_s16(decoder, packet, bytes, decoded,
                KENC_PACKET_SAMPLES, &samples, &info) == KENC_OK);
            TEST_CHECK(samples == KENC_PACKET_SAMPLES && info.pts_ms == frame * 40u);
        }
        kenc_encoder_free(encoder);
        kenc_decoder_free(decoder);
    }
    kenc_model_free(model);
    return test_summary("native positive", passed, total);
}
