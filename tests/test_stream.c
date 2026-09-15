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

    /* Epoch-start negotiation (OD-AT): C5-R4 only when both sides advertise
     * it; an absent advertisement is C0; a malformed one is refused. */
    const uint32_t c0 = KENC_EPOCH_START_BIT(KENC_EPOCH_START_C0);
    const uint32_t c5_r4 = KENC_EPOCH_START_BIT(KENC_EPOCH_START_C5_R4);
    const uint32_t all = kenc_epoch_start_supported();
    kenc_epoch_start selected = (kenc_epoch_start)99;
    TEST_CHECK(all == (c0 | c5_r4) && all == KENC_EPOCH_START_ALL);
    TEST_CHECK(kenc_epoch_start_negotiate(all, all, &selected) == KENC_OK && selected == KENC_EPOCH_START_C5_R4);
    selected = (kenc_epoch_start)99;
    TEST_CHECK(kenc_epoch_start_negotiate(all, 0u, &selected) == KENC_OK && selected == KENC_EPOCH_START_C0);
    selected = (kenc_epoch_start)99;
    TEST_CHECK(kenc_epoch_start_negotiate(all, c0, &selected) == KENC_OK && selected == KENC_EPOCH_START_C0);
    selected = (kenc_epoch_start)99;
    TEST_CHECK(kenc_epoch_start_negotiate(c0, all, &selected) == KENC_OK && selected == KENC_EPOCH_START_C0);
    selected = (kenc_epoch_start)99;
    TEST_CHECK(kenc_epoch_start_negotiate(all, all | UINT32_C(0x80000000), &selected) == KENC_OK
        && selected == KENC_EPOCH_START_C5_R4);
    selected = (kenc_epoch_start)99;
    TEST_CHECK(kenc_epoch_start_negotiate(all, c0 | UINT32_C(4), &selected) == KENC_OK && selected == KENC_EPOCH_START_C0);
    selected = (kenc_epoch_start)99;
    TEST_CHECK(kenc_epoch_start_negotiate(all, c5_r4, &selected) == KENC_ERR_EPOCH_START && selected == (kenc_epoch_start)99);
    TEST_CHECK(kenc_epoch_start_negotiate(all, UINT32_C(4), &selected) == KENC_ERR_EPOCH_START && selected == (kenc_epoch_start)99);
    TEST_CHECK(kenc_epoch_start_negotiate(0u, all, &selected) == KENC_ERR_INVALID);
    TEST_CHECK(kenc_epoch_start_negotiate(c5_r4, all, &selected) == KENC_ERR_INVALID);
    TEST_CHECK(kenc_epoch_start_negotiate(all | UINT32_C(4), all, &selected) == KENC_ERR_INVALID);
    TEST_CHECK(kenc_epoch_start_negotiate(all, all, NULL) == KENC_ERR_INVALID && selected == (kenc_epoch_start)99);
    TEST_CHECK(kenc_epoch_start_from_marker(0u, &selected) == KENC_OK && selected == KENC_EPOCH_START_C0);
    TEST_CHECK(kenc_epoch_start_from_marker(1u, &selected) == KENC_OK && selected == KENC_EPOCH_START_C5_R4);
    TEST_CHECK(kenc_epoch_start_from_marker(2u, &selected) == KENC_ERR_EPOCH_START && selected == KENC_EPOCH_START_C5_R4);
    TEST_CHECK(kenc_epoch_start_from_marker(UINT32_MAX, &selected) == KENC_ERR_EPOCH_START);
    TEST_CHECK(kenc_epoch_start_from_marker(0u, NULL) == KENC_ERR_INVALID);
    TEST_CHECK(strcmp(kenc_epoch_start_name(KENC_EPOCH_START_C0), "C0") == 0
        && strcmp(kenc_epoch_start_name(KENC_EPOCH_START_C5_R4), "C5-R4") == 0
        && kenc_epoch_start_name((kenc_epoch_start)2) == NULL);
    TEST_CHECK(strstr(kenc_result_string(KENC_ERR_EPOCH_START), "epoch-start") != NULL);
    TEST_CHECK(kenc_encoder_set_epoch_start(NULL, KENC_EPOCH_START_C5_R4) == KENC_ERR_INVALID);
    TEST_CHECK(kenc_decoder_set_epoch_start(NULL, KENC_EPOCH_START_C5_R4) == KENC_ERR_INVALID);

    return test_summary("test_stream", passed, total);
}

