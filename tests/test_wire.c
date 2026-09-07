#include "internal.h"
#include "test.h"

#include <string.h>

int main(void)
{
    unsigned int passed = 0u, total = 0u;
    const uint8_t rates[] = {4u, 8u, 16u};
    const uint16_t sample_counts[] = {320u, 640u, 960u};
    uint8_t buffer[KENC_PACKET_CAPACITY + 1u];
    uint8_t changed[KENC_PACKET_CAPACITY + 1u];
    kenc_wire_packet input = {0}, output;
    size_t written = 0u;
    for (size_t rate = 0u; rate < 3u; ++rate) {
        for (size_t count = 0u; count < 3u; ++count) {
            memset(&input, 0, sizeof(input));
            input.codebooks = rates[rate];
            input.samples = sample_counts[count];
            input.epoch = UINT64_MAX;
            input.pts_ms = UINT64_MAX;
            input.flags = KENC_PACKET_FLAG_RESET | KENC_PACKET_FLAG_END;
            for (size_t book = 0u; book < input.codebooks; ++book) {
                for (size_t frame = 0u; frame < (size_t)input.samples / 320u; ++frame) {
                    input.codes[book * 3u + frame] = (uint16_t)((book * 67u + frame * 31u) % 1024u);
                }
            }
            input.codes[0] = 1023u;
            TEST_CHECK(kenc_packet_write(&input, buffer, sizeof(buffer), &written) == KENC_OK);
            TEST_CHECK(kenc_packet_read(&output, buffer, written, input.codebooks) == KENC_OK);
            TEST_CHECK(output.epoch == input.epoch && output.index == 0u);
            TEST_CHECK(output.pts_ms == UINT64_MAX && output.flags == input.flags);
            TEST_CHECK(output.samples == input.samples && output.codebooks == input.codebooks);
            TEST_CHECK(memcmp(input.codes, output.codes, sizeof(input.codes)) == 0);
            for (size_t short_size = 0u; short_size < written; ++short_size) {
                TEST_CHECK(kenc_packet_read(&output, buffer, short_size, input.codebooks) != KENC_OK);
            }
            buffer[written] = 0u;
            TEST_CHECK(kenc_packet_read(&output, buffer, written + 1u, input.codebooks) == KENC_ERR_PROTOCOL);
            TEST_CHECK(kenc_packet_read(&output, buffer, written, rates[(rate + 1u) % 3u]) == KENC_ERR_PROTOCOL);
        }
    }
    memset(&input, 0, sizeof(input));
    input.samples = 320u;
    input.codebooks = 4u;
    input.flags = KENC_PACKET_FLAG_RESET | KENC_PACKET_FLAG_END;
    for (size_t book = 0u; book < 4u; ++book) { input.codes[book * 3u] = (uint16_t)book; }
    static const uint8_t golden[] = {
        'K', 'M', 'A', 2u, 1u, 0u, 0u, 0u, 3u, 0xc0u, 2u, 5u,
        0u, 0u, 0x10u, 8u, 3u
    };
    TEST_CHECK(kenc_packet_write(&input, buffer, sizeof(buffer), &written) == KENC_OK);
    TEST_CHECK(written == sizeof(golden) && memcmp(buffer, golden, sizeof(golden)) == 0);
    memset(changed, 0xa5, sizeof(changed));
    TEST_CHECK(kenc_packet_write(&input, changed, written - 1u, &written) == KENC_ERR_TRUNCATED);
    TEST_CHECK(written == 0u && changed[0] == 0xa5u && changed[sizeof(golden) - 1u] == 0xa5u);
    input.codes[0] = 1024u;
    TEST_CHECK(kenc_packet_write(&input, changed, sizeof(changed), &written) == KENC_ERR_INVALID);
    input.codes[0] = 0u;
    input.flags |= 0x80u;
    TEST_CHECK(kenc_packet_write(&input, changed, sizeof(changed), &written) == KENC_ERR_INVALID);
    memcpy(changed, golden, sizeof(golden));
    changed[8] |= 0x80u;
    TEST_CHECK(kenc_packet_read(&output, changed, sizeof(golden), 4u) == KENC_ERR_PROTOCOL);
    memcpy(changed, golden, sizeof(golden));
    changed[6] = 1u; /* RESET with a nonzero packet index */
    TEST_CHECK(kenc_packet_read(&output, changed, sizeof(golden), 4u) == KENC_ERR_PROTOCOL);
    memcpy(changed, golden, sizeof(golden));
    changed[8] = KENC_PACKET_FLAG_DISCONTINUITY;
    TEST_CHECK(kenc_packet_read(&output, changed, sizeof(golden), 4u) == KENC_ERR_PROTOCOL);
    memcpy(changed, golden, 4u);
    changed[4] = 0x81u; changed[5] = 0u; /* noncanonical profile 1 */
    memcpy(changed + 6u, golden + 5u, sizeof(golden) - 5u);
    TEST_CHECK(kenc_packet_read(&output, changed, sizeof(golden) + 1u, 4u) == KENC_ERR_PROTOCOL);
    memcpy(changed, golden, 4u);
    memset(changed + 4u, 0xff, 10u); /* overflowing 64-bit varint */
    TEST_CHECK(kenc_packet_read(&output, changed, 14u, 4u) == KENC_ERR_PROTOCOL);
    uint32_t random = 0x4193ca71u;
    for (unsigned int trial = 0u; trial < 50000u; ++trial) {
        random = random * UINT32_C(1664525) + UINT32_C(1013904223);
        size_t length = random % sizeof(buffer);
        for (size_t i = 0u; i < length; ++i) {
            random = random * UINT32_C(1664525) + UINT32_C(1013904223);
            buffer[i] = (uint8_t)(random >> 24u);
        }
        if (length >= 4u) { memcpy(buffer, "KMA\2", 4u); }
        kenc_result result = kenc_packet_read(&output, buffer, length, 8u);
        if (result == KENC_OK) {
            TEST_CHECK(kenc_packet_write(&output, changed, sizeof(changed), &written) == KENC_OK);
            TEST_CHECK(written == length && memcmp(changed, buffer, length) == 0);
        }
    }
    TEST_CHECK(1); /* bounded malformed-packet corpus completed */
    return test_summary("test_wire", passed, total);
}
