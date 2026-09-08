#include "internal.h"

#include <string.h>

static int valid_codebooks(uint8_t count)
{
    return count == 4u || count == 8u || count == 16u;
}

kenc_result kenc_packet_metadata_read(kenc_packet_metadata *metadata,
    const uint8_t *packet, size_t packet_size, const kenc_options *options)
{
    kenc_wire_packet value;
    if (metadata == NULL || kenc_options_validate(options) != KENC_OK) {
        return KENC_ERR_INVALID;
    }
    kenc_result result = kenc_packet_read(&value, packet, packet_size, options->codebooks);
    if (result != KENC_OK) { return result; }
    if (value.index >= options->epoch_packets
        || (value.samples != KENC_PACKET_SAMPLES && (value.flags & KENC_PACKET_FLAG_END) == 0u)) {
        return KENC_ERR_PROTOCOL;
    }
    kenc_packet_metadata verified = {0};
    verified.epoch = value.epoch;
    verified.index = value.index;
    verified.packet.pts_ms = value.pts_ms;
    verified.packet.flags = value.flags;
    verified.packet.samples = value.samples;
    *metadata = verified;
    return KENC_OK;
}

static void put_varint(uint8_t *buffer, size_t *position, uint64_t value)
{
    do {
        uint8_t octet = (uint8_t)(value & UINT64_C(127));
        value >>= 7u;
        buffer[(*position)++] = (uint8_t)(octet | (value ? 128u : 0u));
    } while (value != 0u);
}

static kenc_result get_varint(const uint8_t *buffer, size_t length,
    size_t *position, uint64_t *value)
{
    uint64_t decoded = 0u;
    for (unsigned int slot = 0u; slot < 10u; ++slot) {
        uint8_t octet;
        if (*position == length) {
            return KENC_ERR_TRUNCATED;
        }
        octet = buffer[(*position)++];
        if (slot == 9u && (octet & UINT8_C(254)) != 0u) {
            return KENC_ERR_PROTOCOL;
        }
        decoded |= (uint64_t)(octet & UINT8_C(127)) << (slot * 7u);
        if ((octet & UINT8_C(128)) == 0u) {
            if (slot != 0u && octet == 0u) {
                return KENC_ERR_PROTOCOL; /* non-minimal encoding */
            }
            *value = decoded;
            return KENC_OK;
        }
    }
    return KENC_ERR_PROTOCOL;
}

kenc_result kenc_packet_write(const kenc_wire_packet *value,
    uint8_t *buffer, size_t capacity, size_t *written)
{
    uint8_t result[KENC_PACKET_CAPACITY] = { 'K', 'M', 'A', 2u };
    size_t position = 4u;
    size_t bit = 0u;
    size_t frames;
    size_t payload_size;
    if (written == NULL) {
        return KENC_ERR_INVALID;
    }
    *written = 0u;
    if (value == NULL || buffer == NULL || !valid_codebooks(value->codebooks)
        || (value->samples != 320u && value->samples != 640u
            && value->samples != 960u)
        || (value->flags & UINT8_C(248)) != 0u
        || ((value->flags & KENC_PACKET_FLAG_DISCONTINUITY) != 0u
            && (value->flags & KENC_PACKET_FLAG_RESET) == 0u)
        || ((value->flags & KENC_PACKET_FLAG_RESET) != 0u && value->index != 0u)) {
        return KENC_ERR_INVALID;
    }
    frames = (size_t)value->samples / 320u;
    payload_size = (frames * value->codebooks * 10u + 7u) / 8u;
    put_varint(result, &position, 1u); /* encodec-24khz-v1 */
    put_varint(result, &position, value->epoch);
    put_varint(result, &position, value->index);
    put_varint(result, &position, value->pts_ms);
    result[position++] = value->flags;
    put_varint(result, &position, value->samples);
    put_varint(result, &position, payload_size);
    for (size_t frame = 0u; frame < frames; ++frame) {
        for (size_t book = 0u; book < value->codebooks; ++book) {
            uint16_t code = value->codes[book * KENC_LATENT_FRAMES + frame];
            if (code >= KENC_CODEBOOK_CARDINALITY) {
                return KENC_ERR_INVALID;
            }
            /* Tokens are packed most-significant-bit first. */
            for (unsigned int shift = 10u; shift != 0u; --shift, ++bit) {
                unsigned int one = ((unsigned int)code >> (shift - 1u)) & 1u;
                result[position + bit / 8u] |= (uint8_t)(one << (7u - bit % 8u));
            }
        }
    }
    position += payload_size;
    if (position > capacity) {
        return KENC_ERR_TRUNCATED;
    }
    memcpy(buffer, result, position);
    *written = position;
    return KENC_OK;
}

kenc_result kenc_packet_read(kenc_wire_packet *value,
    const uint8_t *buffer, size_t length, uint8_t codebooks)
{
    kenc_wire_packet result = {0};
    uint64_t profile = 0u, samples = 0u, payload_size = 0u;
    size_t position = 4u, bit = 0u;
    kenc_result status;
    if (value == NULL || buffer == NULL || !valid_codebooks(codebooks)) {
        return KENC_ERR_INVALID;
    }
    if (length < 4u) {
        return KENC_ERR_TRUNCATED;
    }
    if (length > KENC_PACKET_CAPACITY || memcmp(buffer, "KMA\2", 4u) != 0) {
        return KENC_ERR_PROTOCOL;
    }
#define READ_FIELD(target) do { \
    status = get_varint(buffer, length, &position, &(target)); \
    if (status != KENC_OK) { return status; } \
} while (0)
    READ_FIELD(profile);
    READ_FIELD(result.epoch);
    READ_FIELD(result.index);
    READ_FIELD(result.pts_ms);
    if (position == length) {
        return KENC_ERR_TRUNCATED;
    }
    result.flags = buffer[position++];
    READ_FIELD(samples);
    READ_FIELD(payload_size);
#undef READ_FIELD
    if (profile != 1u || (samples != 320u && samples != 640u && samples != 960u)
        || (result.flags & UINT8_C(248)) != 0u
        || ((result.flags & KENC_PACKET_FLAG_DISCONTINUITY) != 0u
            && (result.flags & KENC_PACKET_FLAG_RESET) == 0u)
        || ((result.flags & KENC_PACKET_FLAG_RESET) != 0u && result.index != 0u)) {
        return KENC_ERR_PROTOCOL;
    }
    size_t frames = (size_t)samples / 320u;
    size_t expected = (frames * codebooks * 10u + 7u) / 8u;
    if (payload_size != expected) {
        return KENC_ERR_PROTOCOL;
    }
    if (length - position < expected) {
        return KENC_ERR_TRUNCATED;
    }
    if (length - position != expected) {
        return KENC_ERR_PROTOCOL;
    }
    result.samples = (uint16_t)samples;
    result.codebooks = codebooks;
    for (size_t frame = 0u; frame < frames; ++frame) {
        for (size_t book = 0u; book < codebooks; ++book) {
            uint16_t code = 0u;
            for (unsigned int slot = 0u; slot < 10u; ++slot, ++bit) {
                unsigned int one = ((unsigned int)buffer[position + bit / 8u]
                    >> (7u - bit % 8u)) & 1u;
                code = (uint16_t)(((unsigned int)code << 1u) | one);
            }
            result.codes[book * KENC_LATENT_FRAMES + frame] = code;
        }
    }
    if (bit % 8u != 0u
        && (buffer[length - 1u] & (uint8_t)((1u << (8u - bit % 8u)) - 1u)) != 0u) {
        return KENC_ERR_PROTOCOL;
    }
    *value = result;
    return KENC_OK;
}
