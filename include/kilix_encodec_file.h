#ifndef KILIX_ENCODEC_FILE_H
#define KILIX_ENCODEC_FILE_H

#include "kilix_encodec.h"

#ifdef __cplusplus
extern "C" {
#endif

#define KENC_FILE_HEADER_BYTES 64u
#define KENC_FILE_MAX_RECORD_BYTES 3008u
#define KENC_FILE_PROFILE_MONO 1u
#define KENC_FILE_PROFILE_STEREO 2u
#define KENC_FILE_LIVE 1u

/* All sample counts/positions are per channel. A local file has a known,
 * nonzero duration of at most 24 hours. Reserved live headers are mono-only
 * with zero totals/index entries; the local reader/writer reject them. The header carries
 * no paths, executable metadata or text. Every integer is little-endian. */
typedef struct {
    uint8_t profile;
    uint8_t codebooks;
    uint8_t flags;
    uint64_t samples;
} kenc_file_info;

typedef struct kenc_file_reader kenc_file_reader;
typedef struct kenc_file_writer kenc_file_writer;
typedef struct kenc_file_source kenc_file_source;

/* Caller-owned overlap state, reset on source changes and indexed seeks. The
 * frame is interleaved stereo float; after apply, its first 47520 samples per
 * channel can be emitted. The final file duration trims any padded samples. */
typedef struct {
    float tail[960];
    int primed;
} kenc_stereo_overlap;
void kenc_stereo_overlap_reset(kenc_stereo_overlap *state);
kenc_result kenc_stereo_overlap_apply(kenc_stereo_overlap *state,
    float *frame, size_t scalar_count);

/* File format version 2 (FILE-FORMAT.md) adds the epoch-start profile marker.
 * A C0 file or header is always version 1, byte-identical to the pre-marker
 * format, so pre-marker readers still accept it. A C5-R4 header (mono only,
 * local or live) is version 2 with marker 1, which pre-marker readers refuse.
 * The plain functions write C0 and read both versions without reporting the
 * marker. Anyone decoding records itself must read the marker and select that
 * profile on its decoder: a C0 decoder refuses a marked RESET record with
 * KENC_ERR_EPOCH_START. An unknown marker, or a version 2 header naming C0,
 * is refused with KENC_ERR_EPOCH_START. */
#define KENC_FILE_FORMAT_VERSION 2u
kenc_result kenc_file_header_write(const kenc_file_info *info,
    uint8_t *header, size_t capacity);
kenc_result kenc_file_header_write_epoch_start(const kenc_file_info *info,
    kenc_epoch_start epoch_start, uint8_t *header, size_t capacity);
kenc_result kenc_file_header_read(kenc_file_info *info,
    const uint8_t *header, size_t length);
kenc_result kenc_file_header_read_epoch_start(kenc_file_info *info,
    kenc_epoch_start *epoch_start, const uint8_t *header, size_t length);

/* The file reader copies a bounded regular file into a sealed private snapshot.
 * Source mutation after loading cannot change its validated records. Before
 * making any index entry available it scans every bounded record and checks
 * each index against the actual record offset/reset boundary. It owns no model
 * and performs no inference. Seek returns
 * the indexed position at or before the request, never an arbitrary record. */
kenc_result kenc_file_reader_create(kenc_file_reader **out, int descriptor,
    kenc_file_info *info);
/* The validated header's epoch-start profile; every RESET record matches it. */
kenc_result kenc_file_reader_epoch_start(const kenc_file_reader *reader,
    kenc_epoch_start *epoch_start);
kenc_result kenc_file_reader_next(kenc_file_reader *reader,
    uint8_t *record, size_t capacity, size_t *written, uint64_t *sample_position);
kenc_result kenc_file_reader_seek(kenc_file_reader *reader,
    uint64_t requested_sample, uint64_t *actual_sample);
void kenc_file_reader_free(kenc_file_reader *reader);

/* One local-file decoder for CLI and player adapters. Model loading and pull
 * are synchronous inference operations: interactive consumers must call them
 * from an owned worker, never a UI/audio callback. Asset directories are
 * explicit; only the directory for the validated file profile is used.
 * Pull writes interleaved float PCM with at most 960 mono / 47520 stereo
 * samples per channel. EOF succeeds with zero samples. Short capacity does
 * not advance the source or write PCM. Seek returns an indexed boundary and
 * performs stereo pre-roll internally, preserving continuous-playback overlap.
 * A runtime failure requires a successful seek before pulling again. */
kenc_result kenc_file_source_create(kenc_file_source **out, int descriptor,
    const char *mono_assets, const char *stereo_assets, uint8_t threads,
    kenc_file_info *info);
/* Equivalent decoder using admitted sealed asset FDs. Only the validated file
 * profile's set is read; the other may be NULL. No paths are reopened, and
 * borrowed asset/input descriptors remain owned by the caller. */
kenc_result kenc_file_source_create_fds(kenc_file_source **out, int descriptor,
    const kenc_asset_set *mono_assets, const kenc_asset_set *stereo_assets,
    uint8_t threads, kenc_file_info *info);
/* The source decodes with the file's own epoch-start profile. */
kenc_result kenc_file_source_epoch_start(const kenc_file_source *source,
    kenc_epoch_start *epoch_start);
kenc_result kenc_file_source_pull_f32(kenc_file_source *source, float *pcm,
    size_t scalar_capacity, size_t *samples_written, uint64_t *sample_position);
kenc_result kenc_file_source_seek(kenc_file_source *source,
    uint64_t requested_sample, uint64_t *actual_sample);
void kenc_file_source_free(kenc_file_source *source);

/* The writer duplicates an empty writable regular file. append accepts complete
 * codec-authored records; finish installs the verified index and header only
 * after the exact declared population has been written. The caller owns
 * publication/removal of the output file. No path is ever deleted here. */
kenc_result kenc_file_writer_create(kenc_file_writer **out, int descriptor,
    const kenc_file_info *info);
/* As above for a selected epoch-start profile; C5-R4 requires mono. Every
 * appended RESET record must carry exactly that profile's marker. */
kenc_result kenc_file_writer_create_epoch_start(kenc_file_writer **out, int descriptor,
    const kenc_file_info *info, kenc_epoch_start epoch_start);
kenc_result kenc_file_writer_append(kenc_file_writer *writer,
    const uint8_t *record, size_t length);
kenc_result kenc_file_writer_finish(kenc_file_writer *writer);
void kenc_file_writer_free(kenc_file_writer *writer);

/* Stereo file records contain a finite normalization scale and 10-bit tokens,
 * ordered by time and then codebook. They are never KMA2/network packets. */
kenc_result kenc_stereo_record_write(const uint16_t *codes, size_t code_count,
    uint8_t codebooks, float scale, uint8_t *record, size_t capacity, size_t *written);
kenc_result kenc_stereo_record_read(const uint8_t *record, size_t length,
    uint8_t codebooks, uint16_t *codes, size_t capacity, float *scale);

#ifdef __cplusplus
}
#endif
#endif
