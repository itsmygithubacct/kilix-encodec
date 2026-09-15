#define _POSIX_C_SOURCE 200809L
#include "file_command.h"
#include "kilix_encodec_file.h"

#include <errno.h>
#include <fcntl.h>
#include <inttypes.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>

typedef struct {
    const char *assets, *input, *output;
    uint8_t profile, books, threads;
    kenc_epoch_start epoch_start;
    uint64_t seek;
    int encoding, seek_set, epoch_start_set;
} command_options;

typedef struct {
    uint64_t samples, offset;
    uint32_t rate;
    uint16_t channels;
    struct stat identity;
} wave_input;

void kenc_file_usage(void)
{
    fprintf(stderr,
        "usage: kenc encode --model-dir DIR [--profile 24k|48k] [--bitrate 3|6|12|24]\n"
        "                   [--threads 1|2] [--epoch-start C0|C5-R4] INPUT.wav OUTPUT.kenc\n"
        "       kenc decode --model-dir DIR [--threads 1|2] [--seek-sample N]\n"
        "                   INPUT.kenc OUTPUT.wav\n"
        "Input WAV must be PCM16, 24 kHz mono or 48 kHz stereo for the selected profile.\n"
        "Encoding writes C0 unless --epoch-start C5-R4 (24k only) is selected; decoding\n"
        "follows the file's epoch-start marker.\n"
        "Existing output files are preserved. Failed new outputs remain incomplete.\n");
}

static int integer(const char *text, uint64_t *out)
{
    uint64_t value = 0u;
    if (text == NULL || text[0] == '\0') { return 0; }
    for (size_t i = 0u; text[i] != '\0'; ++i) {
        if (text[i] < '0' || text[i] > '9') { return 0; }
        uint64_t digit = (uint64_t)(text[i] - '0');
        if (value > (UINT64_MAX - digit) / 10u) { return 0; }
        value = value * 10u + digit;
    }
    *out = value;
    return 1;
}

static int parse(command_options *options, int argc, char **argv)
{
    uint64_t bitrate = 6u;
    int positional = 0, literal = 0, seen_profile = 0, seen_rate = 0, seen_threads = 0;
    memset(options, 0, sizeof(*options));
    options->encoding = strcmp(argv[1], "encode") == 0;
    options->profile = KENC_FILE_PROFILE_MONO;
    options->threads = 1u;
    for (int i = 2; i < argc; ++i) {
        const char *name = argv[i];
        if (!literal && strcmp(name, "--") == 0) { literal = 1; continue; }
        if (!literal && name[0] == '-') {
            if (i + 1 >= argc) { return 0; }
            const char *value = argv[++i];
            uint64_t number = 0u;
            if (strcmp(name, "--model-dir") == 0) {
                if (options->assets != NULL || value[0] == '\0') { return 0; }
                options->assets = value;
            } else if (strcmp(name, "--profile") == 0 && options->encoding && !seen_profile) {
                if (strcmp(value, "24k") == 0) { options->profile = KENC_FILE_PROFILE_MONO; }
                else if (strcmp(value, "48k") == 0) { options->profile = KENC_FILE_PROFILE_STEREO; }
                else { return 0; }
                seen_profile = 1;
            } else if (strcmp(name, "--bitrate") == 0 && options->encoding && !seen_rate) {
                if (!integer(value, &bitrate) || (bitrate != 3u && bitrate != 6u && bitrate != 12u && bitrate != 24u)) { return 0; }
                seen_rate = 1;
            } else if (strcmp(name, "--threads") == 0 && !seen_threads) {
                if (!integer(value, &number) || (number != 1u && number != 2u)) { return 0; }
                options->threads = (uint8_t)number;
                seen_threads = 1;
            } else if (strcmp(name, "--epoch-start") == 0 && options->encoding && !options->epoch_start_set) {
                if (strcmp(value, "C0") == 0) { options->epoch_start = KENC_EPOCH_START_C0; }
                else if (strcmp(value, "C5-R4") == 0) { options->epoch_start = KENC_EPOCH_START_C5_R4; }
                else { return 0; }
                options->epoch_start_set = 1;
            } else if (strcmp(name, "--seek-sample") == 0 && !options->encoding && !options->seek_set) {
                if (!integer(value, &options->seek)) { return 0; }
                options->seek_set = 1;
            } else { return 0; }
        } else {
            if (positional == 0) { options->input = name; }
            else if (positional == 1) { options->output = name; }
            else { return 0; }
            ++positional;
        }
    }
    if (options->profile == KENC_FILE_PROFILE_MONO && bitrate == 24u) { return 0; }
    if (options->epoch_start != KENC_EPOCH_START_C0 && options->profile != KENC_FILE_PROFILE_MONO) { return 0; }
    options->books = (uint8_t)(options->profile == KENC_FILE_PROFILE_MONO ? bitrate * 4u / 3u : bitrate * 2u / 3u);
    return positional == 2 && options->assets != NULL;
}

static uint64_t read_le(const uint8_t *bytes, size_t count)
{
    uint64_t value = 0u;
    for (size_t i = 0u; i < count; ++i) { value |= (uint64_t)bytes[i] << (8u * i); }
    return value;
}

static void write_le(uint8_t *bytes, uint64_t value, size_t count)
{
    for (size_t i = 0u; i < count; ++i) { bytes[i] = (uint8_t)(value >> (8u * i)); }
}

static int io_exact(int descriptor, void *buffer, size_t count, uint64_t offset, int writing)
{
    size_t done = 0u;
    if (offset > UINT64_C(4294967303) || count > UINT64_C(4294967303) - offset) { return 0; }
    while (done < count) {
        ssize_t got = writing ? pwrite(descriptor, (uint8_t *)buffer + done, count - done, (off_t)(offset + done))
            : pread(descriptor, (uint8_t *)buffer + done, count - done, (off_t)(offset + done));
        if (got < 0 && errno == EINTR) { continue; }
        if (got <= 0) { return 0; }
        done += (size_t)got;
    }
    return 1;
}

static int open_input(const char *path)
{
    int descriptor = open(path, O_RDONLY | O_NONBLOCK | O_CLOEXEC | O_NOFOLLOW);
    struct stat status;
    if (descriptor >= 0 && (fstat(descriptor, &status) != 0 || !S_ISREG(status.st_mode))) {
        (void)close(descriptor); descriptor = -1;
    }
    return descriptor;
}

static int read_wave(int descriptor, wave_input *wave)
{
    uint8_t header[18];
    int have_format = 0, have_data = 0;
    uint64_t data_bytes = 0u, position = 12u;
    size_t chunks = 0u;
    memset(wave, 0, sizeof(*wave));
    if (fstat(descriptor, &wave->identity) != 0 || wave->identity.st_size < 12
        || (uint64_t)wave->identity.st_size > UINT64_C(4294967303)
        || !io_exact(descriptor, header, 12u, 0u, 0)
        || memcmp(header, "RIFF", 4u) != 0 || memcmp(header + 8u, "WAVE", 4u) != 0
        || read_le(header + 4u, 4u) + 8u != (uint64_t)wave->identity.st_size) { return 0; }
    uint64_t size = (uint64_t)wave->identity.st_size;
    while (position < size) {
        if (++chunks > 4096u || size - position < 8u || !io_exact(descriptor, header, 8u, position, 0)) { return 0; }
        uint64_t bytes = read_le(header + 4u, 4u), padded = bytes + bytes % 2u;
        if (padded > size - position - 8u) { return 0; }
        if (memcmp(header, "fmt ", 4u) == 0) {
            if (have_format || (bytes != 16u && bytes != 18u)
                || !io_exact(descriptor, header, (size_t)bytes, position + 8u, 0)
                || read_le(header, 2u) != 1u || read_le(header + 14u, 2u) != 16u
                || (bytes == 18u && read_le(header + 16u, 2u) != 0u)) { return 0; }
            wave->channels = (uint16_t)read_le(header + 2u, 2u);
            wave->rate = (uint32_t)read_le(header + 4u, 4u);
            if ((wave->channels != 1u && wave->channels != 2u)
                || (wave->rate != 24000u && wave->rate != 48000u)
                || read_le(header + 12u, 2u) != (uint64_t)wave->channels * 2u
                || read_le(header + 8u, 4u) != (uint64_t)wave->rate * wave->channels * 2u) { return 0; }
            have_format = 1;
        } else if (memcmp(header, "data", 4u) == 0) {
            if (have_data || bytes == 0u) { return 0; }
            wave->offset = position + 8u;
            data_bytes = bytes;
            have_data = 1;
        }
        position += 8u + padded;
    }
    if (!have_data || !have_format || data_bytes % ((uint64_t)wave->channels * 2u) != 0u) { return 0; }
    wave->samples = data_bytes / ((uint64_t)wave->channels * 2u);
    return wave->samples <= (uint64_t)wave->rate * 86400u;
}

static int input_unchanged(int descriptor, const struct stat *before)
{
    struct stat after;
    return fstat(descriptor, &after) == 0 && before->st_size == after.st_size
        && before->st_mtim.tv_sec == after.st_mtim.tv_sec && before->st_mtim.tv_nsec == after.st_mtim.tv_nsec
        && before->st_ctim.tv_sec == after.st_ctim.tv_sec && before->st_ctim.tv_nsec == after.st_ctim.tv_nsec;
}

static double monotonic_seconds(void)
{
    struct timespec now;
    if (clock_gettime(CLOCK_MONOTONIC, &now) != 0) { return 0.0; }
    return (double)now.tv_sec + (double)now.tv_nsec / 1000000000.0;
}

static void progress(uint64_t done, uint64_t total, double began, double *last)
{
    double now = monotonic_seconds();
    if (done > 0u && now > began && (done == total || now - *last >= 1.0)) {
        double remaining = (now - began) * (double)(total - done) / (double)done;
        fprintf(stderr, "kenc: %.1f%% complete, estimated %.1f seconds remaining\n",
            (double)done * 100.0 / (double)total, remaining);
        *last = now;
    }
}

static kenc_result encode(const command_options *options, int input, int *output)
{
    wave_input wave;
    if (!read_wave(input, &wave)
        || wave.rate != (options->profile == KENC_FILE_PROFILE_MONO ? 24000u : 48000u)
        || wave.channels != (options->profile == KENC_FILE_PROFILE_MONO ? 1u : 2u)) { return KENC_ERR_PROTOCOL; }
    kenc_file_info info = {options->profile, options->books, 0u, wave.samples};
    kenc_model *model = NULL;
    kenc_encoder *mono = NULL;
    kenc_stereo *stereo = NULL;
    kenc_file_writer *writer = NULL;
    float *frame = NULL;
    uint8_t *raw = NULL;
    kenc_result result;
    size_t frame_samples = options->profile == KENC_FILE_PROFILE_MONO ? 960u : 48000u;
    size_t stride = options->profile == KENC_FILE_PROFILE_MONO ? 960u : 47520u;
    size_t scalars = frame_samples * wave.channels;
    if (options->profile == KENC_FILE_PROFILE_MONO) {
        result = kenc_model_load(&model, options->assets);
        if (result != KENC_OK) { goto done; }
        kenc_options native = kenc_options_default(); native.codebooks = options->books; native.threads = options->threads;
        result = kenc_encoder_create(&mono, model, &native);
        if (result == KENC_OK) { result = kenc_encoder_set_epoch_start(mono, options->epoch_start); }
    } else { result = kenc_stereo_create(&stereo, options->assets, options->books, options->threads); }
    if (result != KENC_OK) { goto done; }
    raw = malloc(scalars * 2u); frame = malloc(scalars * sizeof(*frame));
    if (raw == NULL || frame == NULL) { result = KENC_ERR_MEMORY; goto done; }
    *output = open(options->output, O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW | O_CLOEXEC, 0600);
    if (*output < 0) { result = KENC_ERR_RUNTIME; goto done; }
    result = kenc_file_writer_create_epoch_start(&writer, *output, &info, options->epoch_start);
    if (result != KENC_OK) { goto done; }
    double began = monotonic_seconds(), last = began;
    for (uint64_t position = 0u; position < wave.samples; position += stride) {
        size_t count = wave.samples - position < frame_samples ? (size_t)(wave.samples - position) : frame_samples;
        memset(raw, 0, scalars * 2u);
        if (!io_exact(input, raw, count * wave.channels * 2u, wave.offset + position * wave.channels * 2u, 0)) {
            result = KENC_ERR_TRUNCATED; goto done;
        }
        uint8_t record[KENC_FILE_MAX_RECORD_BYTES]; size_t bytes = 0u;
        if (mono != NULL) {
            int16_t pcm[KENC_PACKET_SAMPLES];
            for (size_t i = 0u; i < KENC_PACKET_SAMPLES; ++i) {
                uint16_t bits = (uint16_t)read_le(raw + i * 2u, 2u);
                int value = bits < 32768u ? (int)bits : (int)bits - 65536;
                pcm[i] = (int16_t)value;
            }
            result = kenc_encoder_push_s16(mono, pcm, KENC_PACKET_SAMPLES, position / 24u, record, sizeof(record), &bytes);
        } else {
            uint16_t codes[16u * KENC_STEREO_LATENT_FRAMES]; float scale;
            for (size_t i = 0u; i < scalars; ++i) {
                uint16_t bits = (uint16_t)read_le(raw + i * 2u, 2u);
                int value = bits < 32768u ? (int)bits : (int)bits - 65536;
                frame[i] = (float)value / 32768.0f;
            }
            result = kenc_stereo_encode_frame(stereo, frame, frame_samples, codes,
                sizeof(codes) / sizeof(codes[0]), &scale);
            if (result == KENC_OK) {
                result = kenc_stereo_record_write(codes, (size_t)options->books * KENC_STEREO_LATENT_FRAMES,
                    options->books, scale, record, sizeof(record), &bytes);
            }
        }
        if (result != KENC_OK) { goto done; }
        result = kenc_file_writer_append(writer, record, bytes);
        if (result != KENC_OK) { goto done; }
        uint64_t completed = wave.samples - position < stride ? wave.samples : position + stride;
        progress(completed, wave.samples, began, &last);
    }
    if (!input_unchanged(input, &wave.identity)) { result = KENC_ERR_PROTOCOL; goto done; }
    result = kenc_file_writer_finish(writer);
done:
    free(raw); free(frame);
    kenc_file_writer_free(writer); kenc_encoder_free(mono); kenc_model_free(model); kenc_stereo_free(stereo);
    return result;
}

static kenc_result decode(const command_options *options, int input, int *output)
{
    kenc_file_source *source = NULL;
    kenc_file_info info;
    float *pcm = NULL;
    uint8_t *raw = NULL;
    uint64_t start = 0u;
    kenc_result result = kenc_file_source_create(&source, input, options->assets, options->assets, options->threads, &info);
    if (result != KENC_OK) { goto done; }
    if (info.profile == KENC_FILE_PROFILE_MONO) {
        kenc_epoch_start epoch_start = KENC_EPOCH_START_C0;
        result = kenc_file_source_epoch_start(source, &epoch_start);
        if (result != KENC_OK) { goto done; }
        fprintf(stderr, "kenc: epoch start %s\n", kenc_epoch_start_name(epoch_start));
    }
    if (options->seek_set) {
        result = kenc_file_source_seek(source, options->seek, &start);
        if (result != KENC_OK) { goto done; }
        fprintf(stderr, "kenc: indexed seek starts at sample %" PRIu64 "\n", start);
    }
    size_t channels = info.profile == KENC_FILE_PROFILE_MONO ? 1u : 2u;
    uint32_t rate = info.profile == KENC_FILE_PROFILE_MONO ? 24000u : 48000u;
    uint64_t audio_bytes = (info.samples - start) * channels * 2u;
    if (audio_bytes > UINT32_MAX - 36u) { result = KENC_ERR_INVALID; goto done; }
    pcm = malloc(96000u * sizeof(*pcm)); raw = malloc(192000u);
    if (pcm == NULL || raw == NULL) { result = KENC_ERR_MEMORY; goto done; }
    *output = open(options->output, O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW | O_CLOEXEC, 0600);
    if (*output < 0) { result = KENC_ERR_RUNTIME; goto done; }
    uint64_t offset = 44u, completed = 0u;
    double began = monotonic_seconds(), last = began;
    for (;;) {
        size_t samples = 0u; uint64_t position = 0u;
        result = kenc_file_source_pull_f32(source, pcm, 96000u, &samples, &position);
        if (result != KENC_OK) { goto done; }
        if (samples == 0u) { break; }
        if (position != start + completed) { result = KENC_ERR_PROTOCOL; goto done; }
        for (size_t i = 0u; i < samples * channels; ++i) {
            if (!isfinite(pcm[i])) { result = KENC_ERR_PROTOCOL; goto done; }
            float value = pcm[i] * 32768.0f;
            int sample = value >= 32767.0f ? 32767 : value <= -32768.0f ? -32768 : (int)lrintf(value);
            write_le(raw + 2u * i, (uint16_t)(int16_t)sample, 2u);
        }
        size_t bytes = samples * channels * 2u;
        if (!io_exact(*output, raw, bytes, offset, 1)) { result = KENC_ERR_RUNTIME; goto done; }
        offset += bytes; completed += samples;
        progress(completed, info.samples - start, began, &last);
    }
    if (offset != audio_bytes + 44u) { result = KENC_ERR_PROTOCOL; goto done; }
    uint8_t header[44] = {0};
    memcpy(header, "RIFF", 4u); write_le(header + 4u, audio_bytes + 36u, 4u);
    memcpy(header + 8u, "WAVEfmt ", 8u); write_le(header + 16u, 16u, 4u);
    write_le(header + 20u, 1u, 2u); write_le(header + 22u, channels, 2u);
    write_le(header + 24u, rate, 4u); write_le(header + 28u, rate * channels * 2u, 4u);
    write_le(header + 32u, channels * 2u, 2u); write_le(header + 34u, 16u, 2u);
    memcpy(header + 36u, "data", 4u); write_le(header + 40u, audio_bytes, 4u);
    if (!io_exact(*output, header, sizeof(header), 0u, 1)) { result = KENC_ERR_RUNTIME; }
done:
    free(pcm); free(raw); kenc_file_source_free(source);
    return result;
}

int kenc_file_command(int argc, char **argv)
{
    command_options options;
    if (!parse(&options, argc, argv)) { kenc_file_usage(); return 2; }
    int input = open_input(options.input), output = -1;
    if (input < 0) { fprintf(stderr, "kenc: input must be a readable regular file\n"); return 1; }
    kenc_result result = options.encoding ? encode(&options, input, &output) : decode(&options, input, &output);
    if (result == KENC_OK && output >= 0 && fsync(output) != 0) { result = KENC_ERR_RUNTIME; }
    if (output >= 0 && close(output) != 0) { result = KENC_ERR_RUNTIME; }
    (void)close(input);
    if (result != KENC_OK) {
        fprintf(stderr, "kenc: %s; existing outputs are preserved%s\n", kenc_result_string(result),
            output >= 0 ? "; newly created output is incomplete" : "");
        return 1;
    }
    return 0;
}
