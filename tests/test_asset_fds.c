#define _GNU_SOURCE
#include "kilix_encodec_file.h"
#include "internal.h"
#include "graph_contracts.h"
#include "test.h"

#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <unistd.h>

static unsigned int passed = 0u, total = 0u;
static const char *const stereo_names[4] = {
    "manifest.json", "encoder_frame_op17.onnx", "decoder_frame_op17.onnx", "rvq-codebooks.f32le"
};

static int snapshot(const char *directory, const char *name, int seals, int readonly, int corrupt)
{
    int parent = open(directory, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
    int source = parent < 0 ? -1 : openat(parent, name, O_RDONLY | O_NOFOLLOW | O_CLOEXEC);
    if (parent >= 0) { (void)close(parent); }
    int writer = memfd_create("kenc-fd-test", MFD_ALLOW_SEALING | MFD_CLOEXEC);
    if (source < 0 || writer < 0 || fchmod(writer, 0600) != 0) { perror("snapshot fixture"); exit(2); }
    unsigned char bytes[65536];
    for (;;) {
        ssize_t count = read(source, bytes, sizeof(bytes));
        if (count < 0) { perror("fixture read"); exit(2); }
        if (count == 0) { break; }
        size_t done = 0u;
        while (done < (size_t)count) {
            ssize_t wrote = write(writer, bytes + done, (size_t)count - done);
            if (wrote <= 0) { perror("fixture write"); exit(2); }
            done += (size_t)wrote;
        }
    }
    (void)close(source);
    if (corrupt && pwrite(writer, "!", 1u, 0) != 1) { exit(2); }
    if (seals != 0 && fcntl(writer, F_ADD_SEALS, seals) != 0) { perror("fixture seals"); exit(2); }
    if (!readonly) { return writer; }
    char path[64];
    (void)snprintf(path, sizeof(path), "/proc/self/fd/%d", writer);
    int result = open(path, O_RDONLY | O_CLOEXEC);
    (void)close(writer);
    if (result < 0 || lseek(result, 7, SEEK_SET) != 7) { exit(2); }
    return result;
}

static void assets(kenc_asset_fd *files, const char *directory, int stereo)
{
    size_t count = stereo ? 4u : 9u;
    for (size_t i = 0u; i < count; ++i) {
        files[i].name = stereo ? stereo_names[i] : i == 0u ? "manifest.json" : kenc_graphs[i - 1u].file;
        files[i].descriptor = snapshot(directory, files[i].name, 15, 1, 0);
    }
}

static void close_assets(kenc_asset_fd *files, size_t count)
{
    for (size_t i = 0u; i < count; ++i) {
        TEST_CHECK(fcntl(files[i].descriptor, F_GETFD) == FD_CLOEXEC);
        TEST_CHECK(lseek(files[i].descriptor, 0, SEEK_CUR) == 7);
        TEST_CHECK(close(files[i].descriptor) == 0);
        files[i].descriptor = -1;
    }
}

static void mono(const char *directory)
{
    kenc_asset_fd files[9]; assets(files, directory, 0);
    kenc_asset_set set = {files, 9u};
    kenc_model *model = NULL, *control = NULL;
    TEST_CHECK(kenc_model_load_fds(NULL, &set) == KENC_ERR_INVALID);
    TEST_CHECK(kenc_model_load_fds(&model, NULL) == KENC_ERR_INVALID && model == NULL);
    for (size_t i = 0u; i <= 10u; ++i) {
        if (i == 9u) { continue; }
        kenc_asset_set bad = {files, i};
        TEST_CHECK(kenc_model_load_fds(&model, &bad) == KENC_ERR_INVALID && model == NULL);
    }
    const char *saved_name = files[0].name;
    const char *bad_names[] = {NULL, "", "../manifest.json", "unexpected.json", files[1].name};
    for (size_t i = 0u; i < sizeof(bad_names) / sizeof(bad_names[0]); ++i) {
        files[0].name = bad_names[i];
        TEST_CHECK(kenc_model_load_fds(&model, &set) == KENC_ERR_INVALID && model == NULL);
    }
    files[0].name = saved_name;
    int original = files[0].descriptor;
    for (int missing = 0; missing <= 4; ++missing) {
        int seals = missing == 4 ? 0 : 15 & ~(1 << missing);
        files[0].descriptor = snapshot(directory, "manifest.json", seals, 1, 0);
        TEST_CHECK(kenc_model_load_fds(&model, &set) == KENC_ERR_MODEL && model == NULL);
        TEST_CHECK(lseek(files[0].descriptor, 0, SEEK_CUR) == 7);
        (void)close(files[0].descriptor);
    }
    files[0].descriptor = snapshot(directory, "manifest.json", 15, 0, 0);
    TEST_CHECK(kenc_model_load_fds(&model, &set) == KENC_ERR_MODEL && model == NULL);
    (void)close(files[0].descriptor);
    files[0].descriptor = snapshot(directory, "manifest.json", 15, 1, 1);
    TEST_CHECK(kenc_model_load_fds(&model, &set) == KENC_ERR_MODEL && model == NULL);
    (void)close(files[0].descriptor);
    files[0].descriptor = original;
    TEST_CHECK(fcntl(original, F_SETFD, 0) == 0);
    TEST_CHECK(kenc_model_load_fds(&model, &set) == KENC_ERR_MODEL && model == NULL);
    TEST_CHECK(fcntl(original, F_SETFD, FD_CLOEXEC) == 0);
    kenc_asset_fd swap = files[0]; files[0] = files[8]; files[8] = swap;
    TEST_CHECK(kenc_model_load_fds(&model, &set) == KENC_OK && model != NULL);
    TEST_CHECK(kenc_model_load(&control, directory) == KENC_OK && control != NULL);
    if (model == NULL || control == NULL) { exit(1); }
    close_assets(files, 9u);
    for (uint8_t books = 4u; books <= 16u; books = (uint8_t)(books * 2u)) {
        kenc_options options = kenc_options_default(); options.codebooks = books; options.threads = 2u;
        kenc_encoder *encoder[2] = {NULL}; kenc_decoder *decoder[2] = {NULL};
        TEST_CHECK(kenc_encoder_create(&encoder[0], model, &options) == KENC_OK);
        TEST_CHECK(kenc_encoder_create(&encoder[1], control, &options) == KENC_OK);
        TEST_CHECK(kenc_decoder_create(&decoder[0], model, &options) == KENC_OK);
        TEST_CHECK(kenc_decoder_create(&decoder[1], control, &options) == KENC_OK);
        for (size_t n = 0u; n < 3u; ++n) {
            int16_t input[960], pcm[2][960]; uint8_t wire[2][KENC_MAX_PACKET_BYTES];
            size_t written[2] = {0}, count[2] = {0};
            for (size_t i = 0u; i < 960u; ++i) { input[i] = (int16_t)((int)((i + n * 960u) % 157u) * 97 - 7600); }
            for (size_t i = 0u; i < 2u; ++i) {
                TEST_CHECK(kenc_encoder_push_s16(encoder[i], input, 960u, n * 40u, wire[i], sizeof(wire[i]), &written[i]) == KENC_OK);
                TEST_CHECK(kenc_decoder_pull_s16(decoder[i], wire[i], written[i], pcm[i], 960u, &count[i], NULL) == KENC_OK);
            }
            TEST_CHECK(written[0] == written[1] && memcmp(wire[0], wire[1], written[0]) == 0);
            TEST_CHECK(count[0] == 960u && count[1] == 960u && memcmp(pcm[0], pcm[1], sizeof(pcm[0])) == 0);
        }
        for (size_t i = 0u; i < 2u; ++i) { kenc_encoder_free(encoder[i]); kenc_decoder_free(decoder[i]); }
    }
    kenc_model_free(model); kenc_model_free(control);
}

static void file_source(const char *mono_dir, const char *stereo_dir, uint8_t profile)
{
    kenc_asset_fd files[9]; assets(files, profile == 1u ? mono_dir : stereo_dir, profile == 2u);
    kenc_asset_set set = {files, profile == 1u ? 9u : 4u};
    char path[] = "/tmp/kenc-fd-source-XXXXXX";
    int descriptor = mkstemp(path); TEST_CHECK(descriptor >= 0);
    if (descriptor < 0) { exit(2); }
    (void)unlink(path);
    kenc_file_info info = {profile, 4u, 0u, profile == 1u ? 960u : 47520u};
    kenc_file_writer *writer = NULL;
    TEST_CHECK(kenc_file_writer_create(&writer, descriptor, &info) == KENC_OK);
    uint8_t record[KENC_FILE_MAX_RECORD_BYTES]; size_t bytes = 0u;
    if (profile == 1u) {
        kenc_wire_packet packet = {0}; packet.samples = 960u; packet.codebooks = 4u; packet.flags = KENC_PACKET_FLAG_RESET;
        TEST_CHECK(kenc_packet_write(&packet, record, sizeof(record), &bytes) == KENC_OK);
    } else {
        uint16_t codes[4u * KENC_STEREO_LATENT_FRAMES] = {0};
        TEST_CHECK(kenc_stereo_record_write(codes, sizeof(codes) / sizeof(codes[0]), 4u, 0.1f, record, sizeof(record), &bytes) == KENC_OK);
    }
    TEST_CHECK(kenc_file_writer_append(writer, record, bytes) == KENC_OK);
    TEST_CHECK(kenc_file_writer_finish(writer) == KENC_OK); kenc_file_writer_free(writer);
    kenc_file_source *source = NULL, *control = NULL; kenc_file_info actual, sentinel;
    memset(&actual, 0x5a, sizeof(actual)); memcpy(&sentinel, &actual, sizeof(actual));
    TEST_CHECK(kenc_file_source_create_fds(&source, descriptor, NULL, NULL, 2u, &actual) == KENC_ERR_INVALID && source == NULL);
    TEST_CHECK(memcmp(&actual, &sentinel, sizeof(actual)) == 0);
    TEST_CHECK(kenc_file_source_create_fds(&source, descriptor, profile == 1u ? &set : NULL,
        profile == 2u ? &set : NULL, 2u, &actual) == KENC_OK && source != NULL);
    TEST_CHECK(actual.profile == info.profile && actual.samples == info.samples);
    TEST_CHECK(kenc_file_source_create(&control, descriptor, mono_dir, stereo_dir, 2u, &actual) == KENC_OK && control != NULL);
    close_assets(files, set.count); (void)close(descriptor);
    float *pcm = malloc(96000u * sizeof(float)), *expected = malloc(96000u * sizeof(float));
    if (pcm == NULL || expected == NULL) { exit(2); }
    size_t count = 0u, expected_count = 0u; uint64_t position = 0u;
    TEST_CHECK(kenc_file_source_pull_f32(source, pcm, 96000u, &count, &position) == KENC_OK);
    TEST_CHECK(kenc_file_source_pull_f32(control, expected, 96000u, &expected_count, &position) == KENC_OK);
    TEST_CHECK(count == info.samples && expected_count == count);
    TEST_CHECK(memcmp(pcm, expected, count * (profile == 1u ? 1u : 2u) * sizeof(float)) == 0);
    kenc_file_source_free(source); kenc_file_source_free(control); free(pcm); free(expected);
}

static void stereo(const char *directory)
{
    kenc_asset_fd files[4]; assets(files, directory, 1); kenc_asset_set set = {files, 4u};
    kenc_stereo *codec = NULL, *control = NULL;
    TEST_CHECK(kenc_stereo_create_fds(NULL, &set, 4u, 2u) == KENC_ERR_INVALID);
    TEST_CHECK(kenc_stereo_create_fds(&codec, NULL, 4u, 2u) == KENC_ERR_INVALID && codec == NULL);
    TEST_CHECK(kenc_stereo_create_fds(&codec, &set, 3u, 2u) == KENC_ERR_INVALID && codec == NULL);
    TEST_CHECK(kenc_stereo_create_fds(&codec, &set, 4u, 0u) == KENC_ERR_INVALID && codec == NULL);
    const char *saved = files[0].name; files[0].name = files[1].name;
    TEST_CHECK(kenc_stereo_create_fds(&codec, &set, 4u, 2u) == KENC_ERR_INVALID && codec == NULL); files[0].name = saved;
    TEST_CHECK(kenc_stereo_create_fds(&codec, &set, 4u, 2u) == KENC_OK && codec != NULL);
    TEST_CHECK(kenc_stereo_create(&control, directory, 4u, 2u) == KENC_OK && control != NULL);
    if (codec == NULL || control == NULL) { exit(1); }
    close_assets(files, 4u);
    float *pcm = calloc(96000u, sizeof(float)), *expected = calloc(96000u, sizeof(float));
    if (pcm == NULL || expected == NULL) { exit(2); }
    uint16_t codes[4u * KENC_STEREO_LATENT_FRAMES];
    for (size_t i = 0u; i < sizeof(codes) / sizeof(codes[0]); ++i) { codes[i] = (uint16_t)(i % 1024u); }
    TEST_CHECK(kenc_stereo_decode_frame(codec, codes, sizeof(codes) / sizeof(codes[0]), 0.1f, pcm, 96000u) == KENC_OK);
    TEST_CHECK(kenc_stereo_decode_frame(control, codes, sizeof(codes) / sizeof(codes[0]), 0.1f, expected, 96000u) == KENC_OK);
    TEST_CHECK(memcmp(pcm, expected, 96000u * sizeof(float)) == 0);
    free(pcm); free(expected); kenc_stereo_free(codec); kenc_stereo_free(control);
}

int main(int argc, char **argv)
{
    if (argc != 3) { return 2; }
    mono(argv[1]); stereo(argv[2]);
    file_source(argv[1], argv[2], 1u); file_source(argv[1], argv[2], 2u);
    return test_summary("sealed_asset_fds", passed, total);
}
