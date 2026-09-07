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

kenc_result kenc_file_header_write(const kenc_file_info *info,
    uint8_t *header, size_t capacity);
kenc_result kenc_file_header_read(kenc_file_info *info,
    const uint8_t *header, size_t length);

/* The file reader copies a bounded regular file into a sealed private snapshot.
 * Source mutation after loading cannot change its validated records. Before
 * making any index entry available it scans every bounded record and checks
 * each index against the actual record offset/reset boundary. It owns no model
 * and performs no inference. Seek returns
 * the indexed position at or before the request, never an arbitrary record. */
kenc_result kenc_file_reader_create(kenc_file_reader **out, int descriptor,
    kenc_file_info *info);
kenc_result kenc_file_reader_next(kenc_file_reader *reader,
    uint8_t *record, size_t capacity, size_t *written, uint64_t *sample_position);
kenc_result kenc_file_reader_seek(kenc_file_reader *reader,
    uint64_t requested_sample, uint64_t *actual_sample);
void kenc_file_reader_free(kenc_file_reader *reader);

/* The writer duplicates an empty writable regular file. append accepts complete
 * codec-authored records; finish installs the verified index and header only
 * after the exact declared population has been written. The caller owns
 * publication/removal of the output file. No path is ever deleted here. */
kenc_result kenc_file_writer_create(kenc_file_writer **out, int descriptor,
    const kenc_file_info *info);
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
