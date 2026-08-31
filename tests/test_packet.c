#include "kilix_encodec.h"
#include "test.h"

int main(void)
{
    unsigned int passed = 0u;
    unsigned int total = 0u;
    kenc_options options = kenc_options_default();

    TEST_CHECK(KENC_PACKET_SAMPLES == 960u);
    TEST_CHECK(KENC_SAMPLE_RATE_24KHZ == 24000u);
    TEST_CHECK(KENC_DEFAULT_EPOCH_PACKETS == 25u);
    TEST_CHECK(KENC_PACKET_FLAG_RESET == UINT8_C(0x01));
    TEST_CHECK(KENC_PACKET_FLAG_END == UINT8_C(0x02));
    TEST_CHECK(KENC_PACKET_FLAG_DISCONTINUITY == UINT8_C(0x04));
    TEST_CHECK(options.packet_samples == KENC_PACKET_SAMPLES);
    TEST_CHECK(options.epoch_packets == KENC_DEFAULT_EPOCH_PACKETS);

    return test_summary("test_packet", passed, total);
}

