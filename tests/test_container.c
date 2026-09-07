#define _POSIX_C_SOURCE 200809L
#include "kilix_encodec_file.h"
#include "internal.h"
#include "test.h"

#include <fcntl.h>
#include <math.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

static int temporary(void)
{
    char path[] = "/tmp/kenc-container-XXXXXX";
    int fd = mkstemp(path);
    if (fd >= 0) { (void)unlink(path); }
    return fd;
}
static kenc_result mono_record(size_t number, uint8_t *record, size_t *bytes)
{
    kenc_wire_packet packet = {0};
    packet.samples = 960u; packet.codebooks = 8u;
    packet.epoch = number / 25u; packet.index = number % 25u; packet.pts_ms = number * 40u;
    packet.flags = number % 25u == 0u ? KENC_PACKET_FLAG_RESET : 0u;
    for (size_t i = 0u; i < 24u; ++i) { packet.codes[i] = (uint16_t)((i + number) % 1024u); }
    return kenc_packet_write(&packet, record, KENC_FILE_MAX_RECORD_BYTES, bytes);
}

int main(void)
{
    unsigned int passed = 0u, total = 0u;
    uint8_t header[KENC_FILE_HEADER_BYTES], altered[KENC_FILE_HEADER_BYTES];
    kenc_file_info info = {KENC_FILE_PROFILE_MONO, 8u, 0u, 27u * 960u - 17u}, read_info;
    TEST_CHECK(kenc_file_header_write(&info, header, sizeof(header)) == KENC_OK);
    TEST_CHECK(kenc_file_header_read(&read_info, header, sizeof(header)) == KENC_OK
        && read_info.samples == info.samples && read_info.profile == info.profile && read_info.codebooks == 8u);
    for (size_t length = 0u; length < sizeof(header); ++length) {
        TEST_CHECK(kenc_file_header_read(&read_info, header, length) == KENC_ERR_TRUNCATED);
    }
    const size_t fields[] = {0u, 1u, 2u, 3u, 4u, 5u, 6u, 7u, 10u, 12u, 16u, 20u, 32u, 40u, 42u, 43u, 44u, 48u, 56u, 63u};
    for (size_t i = 0u; i < sizeof(fields) / sizeof(fields[0]); ++i) {
        memcpy(altered, header, sizeof(header)); altered[fields[i]] ^= 128u;
        TEST_CHECK(kenc_file_header_read(&read_info, altered, sizeof(altered)) == KENC_ERR_PROTOCOL);
    }
    kenc_file_info bad = info; bad.samples = UINT64_MAX;
    TEST_CHECK(kenc_file_header_write(&bad, altered, sizeof(altered)) == KENC_ERR_INVALID);
    bad = info; bad.flags = KENC_FILE_LIVE; bad.samples = 0u;
    TEST_CHECK(kenc_file_header_write(&bad, altered, sizeof(altered)) == KENC_OK);
    TEST_CHECK(kenc_file_header_read(&read_info, altered, sizeof(altered)) == KENC_OK && read_info.flags == KENC_FILE_LIVE);
    bad.profile = KENC_FILE_PROFILE_STEREO;
    TEST_CHECK(kenc_file_header_write(&bad, altered, sizeof(altered)) == KENC_ERR_INVALID);

    uint8_t record[KENC_FILE_MAX_RECORD_BYTES], output[KENC_FILE_MAX_RECORD_BYTES];
    uint16_t codes[16u * KENC_STEREO_LATENT_FRAMES], decoded[16u * KENC_STEREO_LATENT_FRAMES];
    for (size_t i = 0u; i < sizeof(codes) / sizeof(codes[0]); ++i) { codes[i] = (uint16_t)((i * 31u) % 1024u); }
    for (uint8_t books = 2u; books <= 16u; books = (uint8_t)(books * 2u)) {
        size_t count = (size_t)books * KENC_STEREO_LATENT_FRAMES, bytes = 999u; float scale = -1.0f;
        TEST_CHECK(kenc_stereo_record_write(codes, count, books, 0.25f, record, sizeof(record), &bytes) == KENC_OK);
        TEST_CHECK(bytes == 8u + count * 10u / 8u);
        TEST_CHECK(kenc_stereo_record_read(record, bytes, books, decoded, count, &scale) == KENC_OK
            && scale == 0.25f && memcmp(codes, decoded, count * sizeof(codes[0])) == 0);
        for (size_t length = 0u; length < bytes; ++length) {
            TEST_CHECK(kenc_stereo_record_read(record, length, books, decoded, count, &scale) == KENC_ERR_PROTOCOL);
        }
        memset(output, 165, sizeof(output)); size_t written = 1u;
        TEST_CHECK(kenc_stereo_record_write(codes, count, books, NAN, output, sizeof(output), &written) == KENC_ERR_INVALID
            && written == 0u && output[0] == 165u);
        TEST_CHECK(kenc_stereo_record_write(codes, count, books, 0.25f, output, bytes - 1u, &written) == KENC_ERR_TRUNCATED
            && written == 0u && output[0] == 165u);
    }

    int fd = temporary(); kenc_file_writer *writer = NULL; kenc_file_reader *reader = NULL;
    size_t bytes = 0u; uint64_t position = 777u;
    TEST_CHECK(fd >= 0);
    if (fd < 0) { return 1; }
    TEST_CHECK(kenc_file_writer_create(&writer, fd, &info) == KENC_OK && writer != NULL);
    if (writer == NULL) { (void)close(fd); return 1; }
    TEST_CHECK(kenc_file_writer_finish(writer) == KENC_ERR_INVALID);
    for (size_t i = 0u; i < 27u; ++i) {
        TEST_CHECK(mono_record(i, record, &bytes) == KENC_OK);
        TEST_CHECK(kenc_file_writer_append(writer, record, bytes) == KENC_OK);
    }
    TEST_CHECK(kenc_file_writer_append(writer, record, bytes) == KENC_ERR_INVALID);
    TEST_CHECK(kenc_file_writer_finish(writer) == KENC_OK);
    TEST_CHECK(kenc_file_writer_finish(writer) == KENC_ERR_INVALID);
    kenc_file_writer_free(writer); writer = NULL;
    TEST_CHECK(fcntl(fd, F_GETFD) >= 0); /* caller's descriptor remains owned */
    TEST_CHECK(kenc_file_writer_create(&writer, fd, &info) == KENC_ERR_INVALID && writer == NULL);
    TEST_CHECK(kenc_file_reader_create(&reader, fd, &read_info) == KENC_OK && reader != NULL);
    if (reader == NULL) { (void)close(fd); return 1; }
    memset(output, 165, sizeof(output)); bytes = 999u;
    TEST_CHECK(kenc_file_reader_next(reader, output, 1u, &bytes, &position) == KENC_ERR_TRUNCATED
        && bytes == 0u && position == 777u && output[0] == 165u);
    for (size_t i = 0u; i < 27u; ++i) {
        size_t expected = 0u;
        TEST_CHECK(mono_record(i, record, &expected) == KENC_OK);
        TEST_CHECK(kenc_file_reader_next(reader, output, sizeof(output), &bytes, &position) == KENC_OK
            && bytes == expected && position == i * 960u && memcmp(record, output, bytes) == 0);
    }
    TEST_CHECK(kenc_file_reader_next(reader, output, sizeof(output), &bytes, &position) == KENC_OK && bytes == 0u);
    TEST_CHECK(kenc_file_reader_seek(reader, 25u * 960u + 1u, &position) == KENC_OK && position == 25u * 960u);
    TEST_CHECK(kenc_file_reader_next(reader, output, sizeof(output), &bytes, &position) == KENC_OK && position == 25u * 960u);
    TEST_CHECK(kenc_file_reader_seek(reader, 24u * 960u, &position) == KENC_OK && position == 0u);
    TEST_CHECK(kenc_file_reader_seek(reader, info.samples, &position) == KENC_ERR_INVALID);
    int mutation_fd = fcntl(fd, F_DUPFD_CLOEXEC, 3);
    (void)close(fd); fd = mutation_fd;
    TEST_CHECK(kenc_file_reader_next(reader, output, sizeof(output), &bytes, &position) == KENC_OK && position == 0u);
    uint8_t old = 0u, changed;
    TEST_CHECK(pread(fd, &old, 1u, 64 + 16) == 1);
    changed = (uint8_t)(old ^ 1u);
    TEST_CHECK(pwrite(fd, &changed, 1u, 64 + 16) == 1);
    TEST_CHECK(kenc_file_reader_next(reader, output, sizeof(output), &bytes, &position) == KENC_OK && position == 960u);
    kenc_file_reader_free(reader); reader = NULL;
    TEST_CHECK(kenc_file_reader_create(&reader, fd, &read_info) == KENC_ERR_PROTOCOL && reader == NULL);
    TEST_CHECK(pwrite(fd, &old, 1u, 64 + 16) == 1);
    TEST_CHECK(kenc_file_reader_create(&reader, fd, &read_info) == KENC_OK);
    kenc_file_reader_free(reader); reader = NULL;
    off_t end = lseek(fd, 0, SEEK_END);
    TEST_CHECK(write(fd, "x", 1u) == 1);
    TEST_CHECK(kenc_file_reader_create(&reader, fd, &read_info) == KENC_ERR_PROTOCOL && reader == NULL);
    TEST_CHECK(ftruncate(fd, end) == 0);
    /* Header valid but a record length is untrusted: bound it before reading. */
    const uint8_t huge[4] = {255u, 255u, 255u, 255u};
    TEST_CHECK(pwrite(fd, huge, sizeof(huge), 64 + 2 * 24) == (ssize_t)sizeof(huge));
    TEST_CHECK(kenc_file_reader_create(&reader, fd, &read_info) == KENC_ERR_PROTOCOL && reader == NULL);
    (void)close(fd);

    /* Stereo uses one verified index per independent noncausal frame. */
    fd = temporary(); info = (kenc_file_info){KENC_FILE_PROFILE_STEREO, 4u, 0u, 100003u};
    TEST_CHECK(kenc_file_writer_create(&writer, fd, &info) == KENC_OK);
    TEST_CHECK(kenc_stereo_record_write(codes, 4u * 150u, 4u, 0.25f, record, sizeof(record), &bytes) == KENC_OK);
    for (size_t i = 0u; i < 3u; ++i) { TEST_CHECK(kenc_file_writer_append(writer, record, bytes) == KENC_OK); }
    TEST_CHECK(kenc_file_writer_finish(writer) == KENC_OK);
    kenc_file_writer_free(writer); writer = NULL;
    TEST_CHECK(kenc_file_reader_create(&reader, fd, &read_info) == KENC_OK);
    TEST_CHECK(kenc_file_reader_seek(reader, 99999u, &position) == KENC_OK && position == 95040u);
    TEST_CHECK(kenc_file_reader_next(reader, output, sizeof(output), &bytes, &position) == KENC_OK && position == 95040u);
    kenc_file_reader_free(reader); (void)close(fd);

    /* Opposite-sign constant frames make the 480-sample crossfade visible;
     * samples outside the overlap remain at their original channel value. */
    float *frame = malloc(96000u * sizeof(*frame));
    TEST_CHECK(frame != NULL);
    if (frame == NULL) { return 1; }
    kenc_stereo_overlap overlap, saved;
    kenc_stereo_overlap_reset(&overlap);
    for (size_t i = 0u; i < 48000u; ++i) { frame[2u * i] = 0.75f; frame[2u * i + 1u] = -0.75f; }
    TEST_CHECK(kenc_stereo_overlap_apply(&overlap, frame, 96000u) == KENC_OK);
    for (size_t i = 0u; i < 47520u; ++i) {
        TEST_CHECK(fabsf(frame[2u * i] - 0.75f) < 0.000001f && frame[2u * i + 1u] == -frame[2u * i]);
    }
    for (size_t i = 0u; i < 48000u; ++i) { frame[2u * i] = -0.75f; frame[2u * i + 1u] = 0.75f; }
    memcpy(&saved, &overlap, sizeof(saved));
    frame[95999] = NAN;
    TEST_CHECK(kenc_stereo_overlap_apply(&overlap, frame, 96000u) == KENC_ERR_PROTOCOL
        && memcmp(&saved, &overlap, sizeof(saved)) == 0 && frame[0] == -0.75f);
    frame[95999] = 0.75f;
    TEST_CHECK(kenc_stereo_overlap_apply(&overlap, frame, 95999u) == KENC_ERR_INVALID
        && memcmp(&saved, &overlap, sizeof(saved)) == 0 && frame[0] == -0.75f);
    TEST_CHECK(kenc_stereo_overlap_apply(&overlap, frame, 96000u) == KENC_OK);
    TEST_CHECK(frame[0] > 0.74f && frame[958] < -0.74f);
    for (size_t i = 1u; i < 480u; ++i) {
        TEST_CHECK(frame[2u * i] < frame[2u * (i - 1u)] && frame[2u * i + 1u] == -frame[2u * i]);
    }
    TEST_CHECK(frame[960] == -0.75f && frame[95038] == -0.75f);
    kenc_stereo_overlap_reset(&overlap);
    TEST_CHECK(overlap.primed == 0 && overlap.tail[0] == 0.0f && overlap.tail[959] == 0.0f);
    free(frame);
    return test_summary("container", passed, total);
}
