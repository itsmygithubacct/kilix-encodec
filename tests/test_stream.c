#include "kilix_encodec.h"
#include "test.h"

#include <string.h>

int main(void)
{
    unsigned int passed = 0u;
    unsigned int total = 0u;
    kenc_encoder *encoder = NULL;
    kenc_decoder *decoder = NULL;
    kenc_options options = kenc_options_default();
    int16_t pcm[KENC_PACKET_SAMPLES];
    uint8_t packet[64];
    size_t written = 99u;
    kenc_packet_info info = {
        .pts_ms = UINT64_MAX,
        .flags = UINT8_MAX,
        .samples = UINT16_MAX,
    };

    memset(pcm, 0, sizeof(pcm));
    memset(packet, 0, sizeof(packet));

    TEST_CHECK(kenc_encoder_create(&encoder, NULL, &options) == KENC_ERR_MODEL);
    TEST_CHECK(encoder == NULL);
    TEST_CHECK(kenc_decoder_create(&decoder, NULL, &options) == KENC_ERR_MODEL);
    TEST_CHECK(decoder == NULL);
    TEST_CHECK(kenc_encoder_push_s16(NULL, pcm, KENC_PACKET_SAMPLES,
                   UINT64_C(0), packet, sizeof(packet), &written)
        == KENC_ERR_INVALID);
    TEST_CHECK(written == 0u);
    written = 99u;
    TEST_CHECK(kenc_decoder_pull_s16(NULL, packet, sizeof(packet), pcm,
                   KENC_PACKET_SAMPLES, &written, &info)
        == KENC_ERR_INVALID);
    TEST_CHECK(written == 0u);
    TEST_CHECK(info.pts_ms == UINT64_C(0));
    TEST_CHECK(info.flags == UINT8_C(0));
    TEST_CHECK(info.samples == UINT16_C(0));
    kenc_encoder_reset(NULL);
    kenc_decoder_reset(NULL);
    kenc_encoder_free(NULL);
    kenc_decoder_free(NULL);
    TEST_CHECK(1);

    return test_summary("test_stream", passed, total);
}

