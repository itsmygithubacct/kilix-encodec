#define _GNU_SOURCE
#include "internal.h"
#include "kilix_encodec_file.h"

#include <errno.h>
#include <fcntl.h>
#include <math.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/mman.h>
#include <unistd.h>

#define INDEX_BYTES 24u
#define MAX_FILE_BYTES UINT64_C(8589934592)

typedef struct { uint64_t sample, record, offset; } file_index;

typedef struct {
    int fd;
    kenc_file_info info;
    kenc_epoch_start epoch_start;
    uint64_t records;
    size_t index_count;
    file_index *index;
    uint64_t position;
    uint64_t offset;
} file_state;

struct kenc_file_reader { file_state file; struct stat identity; };
struct kenc_file_writer { file_state file; int finished; };

void kenc_stereo_overlap_reset(kenc_stereo_overlap *state)
{
    if (state != NULL) { memset(state, 0, sizeof(*state)); }
}
static float overlap_weight(size_t sample)
{
    float position = (float)((double)(sample + 1u) / 48001.0);
    float delta = position - 0.5f;
    if (delta < 0.0f) { delta = -delta; }
    return 0.5f - delta;
}
kenc_result kenc_stereo_overlap_apply(kenc_stereo_overlap *state,
    float *frame, size_t scalar_count)
{
    if (state == NULL || frame == NULL || scalar_count != 96000u) { return KENC_ERR_INVALID; }
    if (state->primed != 0 && state->primed != 1) { return KENC_ERR_INVALID; }
    if (state->primed) {
        for (size_t i = 0u; i < sizeof(state->tail) / sizeof(state->tail[0]); ++i) {
            if (!isfinite(state->tail[i])) { return KENC_ERR_INVALID; }
        }
    }
    for (size_t i = 0u; i < scalar_count; ++i) {
        if (!isfinite(frame[i])) { return KENC_ERR_PROTOCOL; }
    }
    for (size_t sample = 0u; sample < KENC_STEREO_STRIDE_SAMPLES; ++sample) {
        float weight = overlap_weight(sample);
        float previous = state->primed && sample < 480u ? overlap_weight(47520u + sample) : 0.0f;
        for (size_t channel = 0u; channel < 2u; ++channel) {
            size_t at = sample * 2u + channel;
            float sum = previous > 0.0f ? state->tail[at] : 0.0f;
            frame[at] = (sum + weight * frame[at]) / (weight + previous);
        }
    }
    for (size_t sample = 0u; sample < 480u; ++sample) {
        float weight = overlap_weight(47520u + sample);
        for (size_t channel = 0u; channel < 2u; ++channel) {
            state->tail[sample * 2u + channel] = weight * frame[(47520u + sample) * 2u + channel];
        }
    }
    state->primed = 1;
    return KENC_OK;
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
static int stereo_books(uint8_t books)
{
    return books == 2u || books == 4u || books == 8u || books == 16u;
}
static uint64_t stride(const kenc_file_info *info)
{
    return info->profile == KENC_FILE_PROFILE_MONO ? 960u : 47520u;
}
static uint64_t records_for(const kenc_file_info *info)
{
    return info->samples / stride(info) + (info->samples % stride(info) != 0u ? 1u : 0u);
}
static size_t indexes_for(const kenc_file_info *info)
{
    uint64_t records = records_for(info);
    return (size_t)(info->profile == KENC_FILE_PROFILE_MONO ? (records + 24u) / 25u : records);
}
static kenc_result validate_info(const kenc_file_info *info)
{
    if (info == NULL || info->flags > KENC_FILE_LIVE
        || (info->profile != KENC_FILE_PROFILE_MONO && info->profile != KENC_FILE_PROFILE_STEREO)
        || !stereo_books(info->codebooks)
        || (info->profile == KENC_FILE_PROFILE_MONO && info->codebooks == 2u)) {
        return KENC_ERR_INVALID;
    }
    if (info->flags == KENC_FILE_LIVE) {
        return info->profile == KENC_FILE_PROFILE_MONO && info->samples == 0u
            ? KENC_OK : KENC_ERR_INVALID;
    }
    uint64_t rate = info->profile == KENC_FILE_PROFILE_MONO ? 24000u : 48000u;
    return info->samples > 0u && info->samples <= rate * 86400u
        ? KENC_OK : KENC_ERR_INVALID;
}

static int known_epoch_start(kenc_epoch_start epoch_start)
{
    return epoch_start == KENC_EPOCH_START_C0 || epoch_start == KENC_EPOCH_START_C5_R4;
}

/* Version 1 has no marker and is C0. Version 2 differs only in byte 4 and the
 * marker at byte 42, which must name a non-C0 profile; C0 is always written as
 * version 1 so that each state has exactly one canonical header. */
static kenc_result header_write(const kenc_file_info *info, kenc_epoch_start epoch_start,
    uint8_t *header, size_t capacity)
{
    uint8_t value[KENC_FILE_HEADER_BYTES] = {'K', 'E', 'N', 'C', 1u, 13u, 10u, 26u};
    if (header == NULL || validate_info(info) != KENC_OK) { return KENC_ERR_INVALID; }
    if (!known_epoch_start(epoch_start)) { return KENC_ERR_EPOCH_START; }
    if (epoch_start != KENC_EPOCH_START_C0 && info->profile != KENC_FILE_PROFILE_MONO) { return KENC_ERR_INVALID; }
    if (capacity < sizeof(value)) { return KENC_ERR_TRUNCATED; }
    if (epoch_start != KENC_EPOCH_START_C0) {
        value[4] = 2u;
        value[42] = (uint8_t)epoch_start;
    }
    value[8] = info->profile; value[9] = info->codebooks;
    value[10] = info->profile == KENC_FILE_PROFILE_MONO ? 1u : 2u;
    value[11] = info->flags;
    write_le(value + 12u, info->profile == KENC_FILE_PROFILE_MONO ? 24000u : 48000u, 4u);
    write_le(value + 16u, info->profile == KENC_FILE_PROFILE_MONO ? 960u : 48000u, 4u);
    write_le(value + 20u, stride(info), 4u);
    write_le(value + 24u, info->samples, 8u);
    write_le(value + 32u, records_for(info), 8u);
    write_le(value + 40u, info->profile == KENC_FILE_PROFILE_MONO ? 25u : 0u, 2u);
    write_le(value + 44u, indexes_for(info), 4u);
    write_le(value + 48u, KENC_FILE_HEADER_BYTES + indexes_for(info) * INDEX_BYTES, 8u);
    memcpy(header, value, sizeof(value));
    return KENC_OK;
}

static kenc_result header_read(kenc_file_info *info, kenc_epoch_start *epoch_start,
    const uint8_t *header, size_t length)
{
    uint8_t canonical[KENC_FILE_HEADER_BYTES];
    kenc_file_info value;
    kenc_epoch_start profile = KENC_EPOCH_START_C0;
    if (info == NULL || header == NULL) { return KENC_ERR_INVALID; }
    if (length < KENC_FILE_HEADER_BYTES) { return KENC_ERR_TRUNCATED; }
    if (length != KENC_FILE_HEADER_BYTES) { return KENC_ERR_PROTOCOL; }
    if (memcmp(header, "KENC\2\r\n\32", 8u) == 0
        && (kenc_epoch_start_from_marker(header[42], &profile) != KENC_OK
            || profile == KENC_EPOCH_START_C0)) {
        return KENC_ERR_EPOCH_START; /* unknown marker, or C0 spelled as version 2 */
    }
    value.profile = header[8]; value.codebooks = header[9]; value.flags = header[11];
    value.samples = read_le(header + 24u, 8u);
    if (header_write(&value, profile, canonical, sizeof(canonical)) != KENC_OK
        || memcmp(header, canonical, sizeof(canonical)) != 0) { return KENC_ERR_PROTOCOL; }
    *info = value;
    if (epoch_start != NULL) { *epoch_start = profile; }
    return KENC_OK;
}

kenc_result kenc_file_header_write(const kenc_file_info *info,
    uint8_t *header, size_t capacity)
{
    return header_write(info, KENC_EPOCH_START_C0, header, capacity);
}

kenc_result kenc_file_header_write_epoch_start(const kenc_file_info *info,
    kenc_epoch_start epoch_start, uint8_t *header, size_t capacity)
{
    return header_write(info, epoch_start, header, capacity);
}

kenc_result kenc_file_header_read(kenc_file_info *info,
    const uint8_t *header, size_t length)
{
    return header_read(info, NULL, header, length);
}

kenc_result kenc_file_header_read_epoch_start(kenc_file_info *info,
    kenc_epoch_start *epoch_start, const uint8_t *header, size_t length)
{
    if (epoch_start == NULL) { return KENC_ERR_INVALID; }
    return header_read(info, epoch_start, header, length);
}

kenc_result kenc_stereo_record_write(const uint16_t *codes, size_t code_count,
    uint8_t codebooks, float scale, uint8_t *record, size_t capacity, size_t *written)
{
    uint8_t value[KENC_FILE_MAX_RECORD_BYTES] = {'K', 'S', 'F', 1u};
    uint32_t bits;
    size_t count = (size_t)codebooks * KENC_STEREO_LATENT_FRAMES;
    size_t bytes = 8u + count * 10u / 8u, bit = 0u;
    if (written == NULL) { return KENC_ERR_INVALID; }
    *written = 0u;
    if (codes == NULL || record == NULL || !stereo_books(codebooks) || code_count != count
        || !isfinite(scale) || scale <= 0.0f || scale > 2.0f) { return KENC_ERR_INVALID; }
    if (capacity < bytes) { return KENC_ERR_TRUNCATED; }
    _Static_assert(sizeof(float) == sizeof(uint32_t), "stereo scale is float32");
    memcpy(&bits, &scale, sizeof(bits)); write_le(value + 4u, bits, 4u);
    for (size_t frame = 0u; frame < KENC_STEREO_LATENT_FRAMES; ++frame) {
        for (size_t book = 0u; book < codebooks; ++book) {
            uint16_t code = codes[book * KENC_STEREO_LATENT_FRAMES + frame];
            if (code >= 1024u) { return KENC_ERR_INVALID; }
            for (unsigned int shift = 10u; shift != 0u; --shift, ++bit) {
                value[8u + bit / 8u] |= (uint8_t)((((unsigned int)code >> (shift - 1u)) & 1u) << (7u - bit % 8u));
            }
        }
    }
    memcpy(record, value, bytes); *written = bytes;
    return KENC_OK;
}

kenc_result kenc_stereo_record_read(const uint8_t *record, size_t length,
    uint8_t codebooks, uint16_t *codes, size_t capacity, float *scale)
{
    uint16_t decoded[16u * KENC_STEREO_LATENT_FRAMES];
    size_t count = (size_t)codebooks * KENC_STEREO_LATENT_FRAMES, bit = 0u;
    uint32_t bits;
    float value;
    if (record == NULL || codes == NULL || scale == NULL || !stereo_books(codebooks)) { return KENC_ERR_INVALID; }
    if (length != 8u + count * 10u / 8u || memcmp(record, "KSF\1", 4u) != 0) { return KENC_ERR_PROTOCOL; }
    if (capacity < count) { return KENC_ERR_TRUNCATED; }
    bits = (uint32_t)read_le(record + 4u, 4u); memcpy(&value, &bits, sizeof(value));
    if (!isfinite(value) || value <= 0.0f || value > 2.0f) { return KENC_ERR_PROTOCOL; }
    for (size_t frame = 0u; frame < KENC_STEREO_LATENT_FRAMES; ++frame) {
        for (size_t book = 0u; book < codebooks; ++book) {
            unsigned int code = 0u;
            for (unsigned int slot = 0u; slot < 10u; ++slot, ++bit) {
                code = (code << 1u) | (((unsigned int)record[8u + bit / 8u] >> (7u - bit % 8u)) & 1u);
            }
            decoded[book * KENC_STEREO_LATENT_FRAMES + frame] = (uint16_t)code;
        }
    }
    memcpy(codes, decoded, count * sizeof(*codes)); *scale = value;
    return KENC_OK;
}

static kenc_result transfer(int fd, void *buffer, size_t count, uint64_t offset, int writing)
{
    size_t done = 0u;
    if (offset > MAX_FILE_BYTES || count > MAX_FILE_BYTES - offset) { return KENC_ERR_PROTOCOL; }
    while (done < count) {
        ssize_t got = writing ? pwrite(fd, (const uint8_t *)buffer + done, count - done, (off_t)(offset + done))
            : pread(fd, (uint8_t *)buffer + done, count - done, (off_t)(offset + done));
        if (got < 0 && errno == EINTR) { continue; }
        if (got < 0) { return KENC_ERR_RUNTIME; }
        if (got == 0) { return KENC_ERR_TRUNCATED; }
        done += (size_t)got;
    }
    return KENC_OK;
}
static kenc_result snapshot_source(file_state *file, struct stat *identity)
{
    uint8_t buffer[65536];
    uint64_t source_size = (uint64_t)identity->st_size, offset = 0u;
    uint64_t maximum_record = file->info.profile == KENC_FILE_PROFILE_MONO
        ? KENC_MAX_PACKET_BYTES : KENC_FILE_MAX_RECORD_BYTES;
    uint64_t maximum_file = KENC_FILE_HEADER_BYTES + indexes_for(&file->info) * INDEX_BYTES
        + records_for(&file->info) * (maximum_record + 4u);
    if (source_size > maximum_file) { return KENC_ERR_PROTOCOL; }
    int snapshot = memfd_create("kenc-file", MFD_CLOEXEC | MFD_ALLOW_SEALING);
    if (snapshot < 0) { return KENC_ERR_RUNTIME; }
    kenc_result result = KENC_OK;
    while (offset < source_size) {
        size_t count = source_size - offset > sizeof(buffer) ? sizeof(buffer) : (size_t)(source_size - offset);
        result = transfer(file->fd, buffer, count, offset, 0);
        if (result != KENC_OK) { goto done; }
        result = transfer(snapshot, buffer, count, offset, 1);
        if (result != KENC_OK) { goto done; }
        offset += count;
    }
    struct stat after;
    if (fstat(file->fd, &after) != 0 || after.st_size != identity->st_size
        || after.st_mtim.tv_sec != identity->st_mtim.tv_sec || after.st_mtim.tv_nsec != identity->st_mtim.tv_nsec
        || after.st_ctim.tv_sec != identity->st_ctim.tv_sec || after.st_ctim.tv_nsec != identity->st_ctim.tv_nsec
        || fcntl(snapshot, F_ADD_SEALS, F_SEAL_WRITE | F_SEAL_GROW | F_SEAL_SHRINK | F_SEAL_SEAL) != 0) {
        result = KENC_ERR_PROTOCOL; goto done;
    }
    if (fstat(snapshot, identity) != 0) { result = KENC_ERR_RUNTIME; goto done; }
    (void)close(file->fd);
    file->fd = snapshot;
    snapshot = -1;
done:
    if (snapshot >= 0) { (void)close(snapshot); }
    return result;
}
static int unchanged(kenc_file_reader *reader)
{
    struct stat now;
    const struct stat *old = &reader->identity;
    return fstat(reader->file.fd, &now) == 0 && now.st_size == old->st_size
        && now.st_mtim.tv_sec == old->st_mtim.tv_sec && now.st_mtim.tv_nsec == old->st_mtim.tv_nsec
        && now.st_ctim.tv_sec == old->st_ctim.tv_sec && now.st_ctim.tv_nsec == old->st_ctim.tv_nsec;
}
static kenc_result validate_record(const file_state *file, const uint8_t *record, size_t length)
{
    if (file->info.profile == KENC_FILE_PROFILE_MONO) {
        kenc_wire_packet packet;
        kenc_result result = kenc_packet_read(&packet, record, length, file->info.codebooks);
        if (result != KENC_OK) { return result; }
        uint8_t expected = file->position % 25u != 0u ? 0u
            : (uint8_t)(KENC_PACKET_FLAG_RESET | (file->epoch_start == KENC_EPOCH_START_C5_R4
                ? KENC_PACKET_FLAG_EPOCH_PREROLL : 0u));
        if (packet.samples != 960u || packet.epoch != file->position / 25u
            || packet.index != file->position % 25u || packet.pts_ms != file->position * 40u) { return KENC_ERR_PROTOCOL; }
        if (packet.flags != expected) {
            /* A RESET record whose only difference is the marker disagrees
             * with the header's epoch-start profile. */
            return (uint8_t)(packet.flags ^ expected) == KENC_PACKET_FLAG_EPOCH_PREROLL
                ? KENC_ERR_EPOCH_START : KENC_ERR_PROTOCOL;
        }
    } else {
        uint16_t codes[16u * KENC_STEREO_LATENT_FRAMES]; float scale;
        return kenc_stereo_record_read(record, length, file->info.codebooks,
            codes, sizeof(codes) / sizeof(codes[0]), &scale);
    }
    return KENC_OK;
}
static int indexed(const file_state *file)
{
    return file->info.profile == KENC_FILE_PROFILE_STEREO || file->position % 25u == 0u;
}
static kenc_result next_record(file_state *file, uint8_t *record, size_t capacity, size_t *written)
{
    uint8_t prefix[4];
    *written = 0u;
    if (file->position == file->records) { return KENC_OK; }
    kenc_result result = transfer(file->fd, prefix, sizeof(prefix), file->offset, 0);
    if (result != KENC_OK) { return result; }
    size_t bytes = (size_t)read_le(prefix, 4u);
    if (bytes == 0u || bytes > KENC_FILE_MAX_RECORD_BYTES) { return KENC_ERR_PROTOCOL; }
    if (capacity < bytes) { return KENC_ERR_TRUNCATED; }
    result = transfer(file->fd, record, bytes, file->offset + 4u, 0);
    if (result != KENC_OK) { return result; }
    result = validate_record(file, record, bytes);
    if (result != KENC_OK) { return result; }
    ++file->position; file->offset += bytes + 4u; *written = bytes;
    return KENC_OK;
}
static void free_state(file_state *file)
{
    if (file->fd >= 0) { (void)close(file->fd); }
    free(file->index);
}
void kenc_file_reader_free(kenc_file_reader *reader)
{
    if (reader != NULL) { free_state(&reader->file); free(reader); }
}
void kenc_file_writer_free(kenc_file_writer *writer)
{
    if (writer != NULL) { free_state(&writer->file); free(writer); }
}

kenc_result kenc_file_reader_create(kenc_file_reader **out, int descriptor, kenc_file_info *info)
{
    kenc_file_reader *reader;
    uint8_t header[KENC_FILE_HEADER_BYTES], bytes[INDEX_BYTES], record[KENC_FILE_MAX_RECORD_BYTES];
    size_t size = 0u, at = 0u;
    kenc_result result;
    if (out == NULL) { return KENC_ERR_INVALID; }
    *out = NULL;
    if (info == NULL) { return KENC_ERR_INVALID; }
    reader = calloc(1u, sizeof(*reader));
    if (reader == NULL) { return KENC_ERR_MEMORY; }
    file_state *file = &reader->file;
    file->fd = fcntl(descriptor, F_DUPFD_CLOEXEC, 3);
    if (file->fd < 0 || fstat(file->fd, &reader->identity) != 0
        || !S_ISREG(reader->identity.st_mode) || reader->identity.st_size < 0
        || (uint64_t)reader->identity.st_size > MAX_FILE_BYTES) { result = KENC_ERR_INVALID; goto done; }
    result = transfer(file->fd, header, sizeof(header), 0u, 0);
    if (result != KENC_OK) { goto done; }
    result = header_read(&file->info, &file->epoch_start, header, sizeof(header));
    if (result != KENC_OK) { goto done; }
    if (file->info.flags == KENC_FILE_LIVE) { result = KENC_ERR_PROTOCOL; goto done; }
    result = snapshot_source(file, &reader->identity);
    if (result != KENC_OK) { goto done; }
    /* Parse the copied header again: every accepted field and index must come
     * from the exact sealed bytes that will later supply playback records. */
    result = transfer(file->fd, header, sizeof(header), 0u, 0);
    if (result != KENC_OK) { goto done; }
    kenc_file_info copied_info;
    kenc_epoch_start copied_epoch_start = KENC_EPOCH_START_C0;
    result = header_read(&copied_info, &copied_epoch_start, header, sizeof(header));
    if (result != KENC_OK || copied_info.profile != file->info.profile
        || copied_info.codebooks != file->info.codebooks || copied_info.flags != file->info.flags
        || copied_info.samples != file->info.samples
        || copied_epoch_start != file->epoch_start) { result = KENC_ERR_PROTOCOL; goto done; }
    file->records = records_for(&file->info); file->index_count = indexes_for(&file->info);
    file->offset = KENC_FILE_HEADER_BYTES + file->index_count * INDEX_BYTES;
    file->index = calloc(file->index_count, sizeof(*file->index));
    if (file->index == NULL) { result = KENC_ERR_MEMORY; goto done; }
    while (file->position < file->records) {
        if (indexed(file)) {
            result = transfer(file->fd, bytes, sizeof(bytes), KENC_FILE_HEADER_BYTES + at * INDEX_BYTES, 0);
            if (result != KENC_OK) { goto done; }
            file_index entry = {read_le(bytes, 8u), read_le(bytes + 8u, 8u), read_le(bytes + 16u, 8u)};
            if (at >= file->index_count || entry.sample != file->position * stride(&file->info)
                || entry.record != file->position || entry.offset != file->offset) { result = KENC_ERR_PROTOCOL; goto done; }
            file->index[at++] = entry;
        }
        result = next_record(file, record, sizeof(record), &size);
        if (result != KENC_OK) { goto done; }
    }
    if (at != file->index_count || file->offset != (uint64_t)reader->identity.st_size || !unchanged(reader)) {
        result = KENC_ERR_PROTOCOL; goto done;
    }
    file->position = 0u; file->offset = file->index[0].offset;
    *info = file->info; *out = reader; reader = NULL;
done:
    kenc_file_reader_free(reader); return result;
}

kenc_result kenc_file_reader_epoch_start(const kenc_file_reader *reader,
    kenc_epoch_start *epoch_start)
{
    if (reader == NULL || epoch_start == NULL) { return KENC_ERR_INVALID; }
    *epoch_start = reader->file.epoch_start;
    return KENC_OK;
}

kenc_result kenc_file_reader_next(kenc_file_reader *reader,
    uint8_t *record, size_t capacity, size_t *written, uint64_t *sample_position)
{
    uint8_t buffer[KENC_FILE_MAX_RECORD_BYTES]; size_t size = 0u;
    if (written == NULL) { return KENC_ERR_INVALID; }
    *written = 0u;
    if (reader == NULL || record == NULL || sample_position == NULL) { return KENC_ERR_INVALID; }
    if (!unchanged(reader)) { return KENC_ERR_PROTOCOL; }
    file_state next = reader->file;
    kenc_result result = next_record(&next, buffer, sizeof(buffer), &size);
    if (result != KENC_OK) { return result; }
    if (capacity < size) { return KENC_ERR_TRUNCATED; }
    if (!unchanged(reader)) { return KENC_ERR_PROTOCOL; }
    *sample_position = reader->file.position * stride(&reader->file.info);
    reader->file.position = next.position; reader->file.offset = next.offset;
    memcpy(record, buffer, size); *written = size;
    return KENC_OK;
}
kenc_result kenc_file_reader_seek(kenc_file_reader *reader,
    uint64_t requested_sample, uint64_t *actual_sample)
{
    if (reader == NULL || actual_sample == NULL) { return KENC_ERR_INVALID; }
    if (!unchanged(reader)) { return KENC_ERR_PROTOCOL; }
    if (requested_sample >= reader->file.info.samples) { return KENC_ERR_INVALID; }
    uint64_t span = stride(&reader->file.info) * (reader->file.info.profile == KENC_FILE_PROFILE_MONO ? 25u : 1u);
    size_t at = (size_t)(requested_sample / span);
    const file_index *entry = &reader->file.index[at];
    reader->file.position = entry->record; reader->file.offset = entry->offset;
    *actual_sample = entry->sample; return KENC_OK;
}

kenc_result kenc_file_writer_create(kenc_file_writer **out, int descriptor, const kenc_file_info *info)
{
    return kenc_file_writer_create_epoch_start(out, descriptor, info, KENC_EPOCH_START_C0);
}

kenc_result kenc_file_writer_create_epoch_start(kenc_file_writer **out, int descriptor,
    const kenc_file_info *info, kenc_epoch_start epoch_start)
{
    struct stat status; kenc_file_writer *writer;
    if (out == NULL) { return KENC_ERR_INVALID; }
    *out = NULL;
    if (validate_info(info) != KENC_OK || info->flags == KENC_FILE_LIVE
        || fstat(descriptor, &status) != 0 || !S_ISREG(status.st_mode) || status.st_size != 0) { return KENC_ERR_INVALID; }
    if (!known_epoch_start(epoch_start)) { return KENC_ERR_EPOCH_START; }
    if (epoch_start != KENC_EPOCH_START_C0 && info->profile != KENC_FILE_PROFILE_MONO) { return KENC_ERR_INVALID; }
    int flags = fcntl(descriptor, F_GETFL);
    if (flags < 0 || (flags & O_ACCMODE) == O_RDONLY || (flags & O_APPEND) != 0) { return KENC_ERR_INVALID; }
    writer = calloc(1u, sizeof(*writer));
    if (writer == NULL) { return KENC_ERR_MEMORY; }
    file_state *file = &writer->file;
    file->fd = fcntl(descriptor, F_DUPFD_CLOEXEC, 3);
    file->epoch_start = epoch_start;
    file->info = *info; file->records = records_for(info); file->index_count = indexes_for(info);
    file->index = calloc(file->index_count, sizeof(*file->index));
    if (file->fd < 0 || file->index == NULL) {
        kenc_result result = file->fd < 0 ? KENC_ERR_RUNTIME : KENC_ERR_MEMORY;
        kenc_file_writer_free(writer); return result;
    }
    file->offset = KENC_FILE_HEADER_BYTES + file->index_count * INDEX_BYTES;
    *out = writer; return KENC_OK;
}
kenc_result kenc_file_writer_append(kenc_file_writer *writer, const uint8_t *record, size_t length)
{
    uint8_t prefix[4];
    if (writer == NULL || record == NULL || writer->finished || writer->file.position >= writer->file.records) { return KENC_ERR_INVALID; }
    file_state *file = &writer->file;
    kenc_result result = validate_record(file, record, length);
    if (result != KENC_OK) { return result; }
    if (indexed(file)) {
        size_t at = file->info.profile == KENC_FILE_PROFILE_MONO ? (size_t)(file->position / 25u) : (size_t)file->position;
        file->index[at] = (file_index){file->position * stride(&file->info), file->position, file->offset};
    }
    write_le(prefix, length, sizeof(prefix));
    result = transfer(file->fd, prefix, sizeof(prefix), file->offset, 1);
    if (result != KENC_OK) { return result; }
    result = transfer(file->fd, (void *)record, length, file->offset + 4u, 1);
    if (result != KENC_OK) { return result; }
    ++file->position; file->offset += length + 4u;
    return KENC_OK;
}
kenc_result kenc_file_writer_finish(kenc_file_writer *writer)
{
    uint8_t header[KENC_FILE_HEADER_BYTES], bytes[INDEX_BYTES];
    struct stat status;
    if (writer == NULL || writer->finished || writer->file.position != writer->file.records) { return KENC_ERR_INVALID; }
    file_state *file = &writer->file;
    if (fstat(file->fd, &status) != 0 || status.st_size < 0
        || (uint64_t)status.st_size != file->offset) { return KENC_ERR_PROTOCOL; }
    kenc_result result = header_write(&file->info, file->epoch_start, header, sizeof(header));
    if (result != KENC_OK) { return result; }
    for (size_t i = 0u; i < file->index_count; ++i) {
        write_le(bytes, file->index[i].sample, 8u); write_le(bytes + 8u, file->index[i].record, 8u); write_le(bytes + 16u, file->index[i].offset, 8u);
        result = transfer(file->fd, bytes, sizeof(bytes), KENC_FILE_HEADER_BYTES + i * INDEX_BYTES, 1);
        if (result != KENC_OK) { return result; }
    }
    result = transfer(file->fd, header, sizeof(header), 0u, 1);
    if (result == KENC_OK) { writer->finished = 1; }
    return result;
}
