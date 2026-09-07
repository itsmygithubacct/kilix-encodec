#define _POSIX_C_SOURCE 200809L
#include "kilix_encodec_file.h"
#include "internal.h"
#include "test.h"

#include <fcntl.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

int main(int argc, char **argv)
{
    unsigned int passed = 0u, total = 0u;
    if (argc != 3) { return 2; }
    for (uint8_t profile = 1u; profile <= 2u; ++profile) {
        char path[] = "/tmp/kenc-source-XXXXXX";
        int fd = mkstemp(path);
        TEST_CHECK(fd >= 0);
        if (fd < 0) { return 1; }
        (void)unlink(path);
        kenc_file_info info = {profile, 4u, 0u, profile == 1u ? 26003u : 100003u}, actual_info;
        size_t channels = profile == 1u ? 1u : 2u;
        size_t stride = profile == 1u ? 960u : 47520u;
        size_t records = (size_t)((info.samples + stride - 1u) / stride);
        kenc_file_writer *writer = NULL;
        kenc_file_source *source = NULL;
        TEST_CHECK(kenc_file_writer_create(&writer, fd, &info) == KENC_OK);
        if (writer == NULL) { (void)close(fd); return 1; }
        for (size_t number = 0u; number < records; ++number) {
            uint8_t record[KENC_FILE_MAX_RECORD_BYTES]; size_t bytes = 0u;
            if (profile == 1u) {
                kenc_wire_packet packet = {0};
                packet.samples = 960u; packet.codebooks = 4u;
                packet.epoch = number / 25u; packet.index = number % 25u;
                packet.pts_ms = number * 40u;
                packet.flags = number % 25u == 0u ? KENC_PACKET_FLAG_RESET : 0u;
                for (size_t i = 0u; i < 12u; ++i) { packet.codes[i] = (uint16_t)((i + number * 31u) % 1024u); }
                TEST_CHECK(kenc_packet_write(&packet, record, sizeof(record), &bytes) == KENC_OK);
            } else {
                uint16_t codes[4u * KENC_STEREO_LATENT_FRAMES];
                for (size_t i = 0u; i < sizeof(codes) / sizeof(codes[0]); ++i) { codes[i] = (uint16_t)((i + number * 31u) % 1024u); }
                TEST_CHECK(kenc_stereo_record_write(codes, sizeof(codes) / sizeof(codes[0]), 4u, 0.1f,
                    record, sizeof(record), &bytes) == KENC_OK);
            }
            TEST_CHECK(kenc_file_writer_append(writer, record, bytes) == KENC_OK);
        }
        TEST_CHECK(kenc_file_writer_finish(writer) == KENC_OK);
        kenc_file_writer_free(writer);
        TEST_CHECK(kenc_file_source_create(&source, fd, argv[1], argv[2], 0u, &actual_info) == KENC_ERR_INVALID && source == NULL);
        TEST_CHECK(kenc_file_source_create(&source, fd, argv[1], argv[2], 1u, &actual_info) == KENC_OK);
        if (source == NULL) { (void)close(fd); return 1; }
        TEST_CHECK(actual_info.profile == info.profile && actual_info.samples == info.samples && actual_info.codebooks == 4u);
        TEST_CHECK(ftruncate(fd, 0) == 0);
        (void)close(fd); /* source owns a sealed snapshot, never this descriptor */
        float *pcm = malloc(96000u * sizeof(*pcm));
        float *complete = malloc((size_t)info.samples * channels * sizeof(*complete));
        TEST_CHECK(pcm != NULL && complete != NULL);
        if (pcm == NULL || complete == NULL) { free(pcm); free(complete); kenc_file_source_free(source); return 1; }
        for (size_t i = 0u; i < 96000u; ++i) { pcm[i] = -12345.0f; }
        size_t count = 987u; uint64_t position = 876u;
        TEST_CHECK(kenc_file_source_pull_f32(source, pcm, stride * channels - 1u, &count, &position) == KENC_ERR_TRUNCATED
            && count == 0u && position == 876u);
        int untouched = 1;
        for (size_t i = 0u; i < 96000u; ++i) { if (pcm[i] != -12345.0f) { untouched = 0; } }
        TEST_CHECK(untouched);
        uint64_t completed = 0u;
        while (completed < info.samples) {
            TEST_CHECK(kenc_file_source_pull_f32(source, pcm, 96000u, &count, &position) == KENC_OK);
            TEST_CHECK(count > 0u && count <= stride && position == completed && count <= info.samples - completed);
            if (count == 0u || count > info.samples - completed) { return 1; }
            memcpy(complete + (size_t)completed * channels, pcm, count * channels * sizeof(*pcm));
            completed += count;
        }
        TEST_CHECK(kenc_file_source_pull_f32(source, pcm, 96000u, &count, &position) == KENC_OK
            && count == 0u && position == info.samples);
        uint64_t requests[] = {0u, profile == 1u ? 23999u : 47521u, info.samples - 1u};
        for (size_t i = 0u; i < sizeof(requests) / sizeof(requests[0]); ++i) {
            uint64_t span = profile == 1u ? 24000u : 47520u;
            uint64_t wanted = requests[i] / span * span;
            TEST_CHECK(kenc_file_source_seek(source, requests[i], &position) == KENC_OK && position == wanted);
            TEST_CHECK(kenc_file_source_pull_f32(source, pcm, 96000u, &count, &position) == KENC_OK && position == wanted);
            TEST_CHECK(memcmp(pcm, complete + (size_t)wanted * channels, count * channels * sizeof(*pcm)) == 0);
        }
        position = 99u;
        TEST_CHECK(kenc_file_source_seek(source, info.samples, &position) == KENC_ERR_INVALID && position == 99u);
        free(pcm); free(complete); kenc_file_source_free(source);
    }
    return test_summary("file_source", passed, total);
}
