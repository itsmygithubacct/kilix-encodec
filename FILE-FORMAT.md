# EnCodec packet file, version 2

`kilix_encodec_file.h` defines the library interface. This format holds only
codec metadata, verified seek indexes and audio tokens. It has no embedded
paths, tags, executable data, URLs or model-selection overrides. Model graph
identity remains the native codec's pinned contract.

Version 2 adds the epoch-start profile marker (owner decision OD-AT). A
version 1 file carries no marker and is a C0 file; it remains valid. C0 files
are always written as version 1, byte-identical to the version 1 format, so
readers that predate the marker still read them. A C5-R4 file is version 2,
which such readers refuse instead of decoding it with the wrong epoch start.

All integers are unsigned little-endian. The header is exactly 64 bytes.
Reserved bytes must be zero and all derived fields must match the declared
profile and duration. The reader compares the entire header to its canonical
serialization.

| Offset | Bytes | Value |
| --- | --- | --- |
| 0 | 8 | `4b 45 4e 43 VV 0d 0a 1a`; `VV` is the version: `01` (C0) or `02` (marked) |
| 8 | 1 | Profile: 1 = 24 kHz mono, 2 = 48 kHz stereo |
| 9 | 1 | Codebooks: mono 4/8/16; stereo 2/4/8/16 |
| 10 | 1 | Channels: mono 1, stereo 2 |
| 11 | 1 | Flags: 0 = local file; 1 = reserved live framing |
| 12 | 4 | Sample rate: 24000 or 48000 |
| 16 | 4 | Samples per frame: 960 or 48000 |
| 20 | 4 | Frame stride: 960 or 47520 |
| 24 | 8 | Logical samples per channel, excluding final padding |
| 32 | 8 | Record count: ceiling of logical samples / stride |
| 40 | 2 | Epoch length: mono 25; stereo 0 |
| 42 | 1 | Version 1: reserved zero. Version 2: epoch-start marker, 1 = C5-R4 |
| 43 | 1 | Reserved |
| 44 | 4 | Index count: mono ceiling of records / 25; stereo records |
| 48 | 8 | First record offset: 64 + index count * 24 |
| 56 | 8 | Reserved |

Local duration is positive and at most 24 hours. A header with live flag 1
requires mono and zero duration, record count and index count. The header
parser recognizes that reserved framing, but the local reader and writer
explicitly reject it. No live source consumer is supplied by this interface.

## Epoch-start profile

Every mono epoch of 25 records begins with a RESET record. The profile says how
both the encoder and the decoder start each epoch:

- **C0**: all streaming state is zeroed and the RESET packet is processed from
  there. This is the 0.2.1 behaviour, and the profile of every stream, peer or
  file without a marker.
- **C5-R4** (owner decision OD-AL): after zeroing, the RESET packet's own input
  is run through the network four times as a discarded lead-in (its 960
  samples on the encoder, its 3 code frames through the RVQ decoder on the
  decoder), and then the packet itself is processed. No latency is added.

The marker value is the `kenc_epoch_start` value. Version 2 must name a non-C0
profile: marker 0 in a version 2 header is not canonical and is refused, as is
any unknown marker. Both refusals return `KENC_ERR_EPOCH_START`, which reads
"unsupported or mismatched epoch-start profile marker". Only mono headers,
local or live, can carry a marker; a stereo header is always version 1.

In a C5-R4 file every RESET record also carries packet flag `0x08`
(`KENC_PACKET_FLAG_EPOCH_PREROLL`); in a C0 file no record does. The reader and
writer refuse any RESET record whose marker disagrees with the header, with
`KENC_ERR_EPOCH_START`, so relabelling a file's header cannot change how its
records decode. `kenc_file_source_*` selects the file's profile on its decoder.
A consumer that decodes records itself must read the marker with
`kenc_file_header_read_epoch_start` or `kenc_file_reader_epoch_start` and select
it with `kenc_decoder_set_epoch_start`; a C0 decoder refuses a marked RESET.
`kenc_file_header_read` still accepts both versions but does not report the
marker.

Live peers carry no file header, so they negotiate instead:
`kenc_epoch_start_supported` gives the advertisement and
`kenc_epoch_start_negotiate` selects C5-R4 only when both sides advertise it.
A peer without an advertisement gets C0 on both sides. The handshake that
carries the advertisement belongs to the consumer.

## Index and records

The header is followed by 24-byte index entries, each containing sample
position, record number and byte offset as three 64-bit integers. There is an
entry for every mono RESET epoch and every independent stereo frame. The
reader scans all actual records and checks every index against its actual
offset and boundary before making any seek operation available. It rejects
missing, extra, reordered, inconsistent and out-of-bounds indexes.

Each record has a four-byte length followed by that many bytes. A mono record
is a complete canonical `KMA2` packet with 960 samples, epoch `record / 25`,
index `record % 25`, PTS `record * 40` milliseconds and RESET only when the
index is zero, marked as the header's profile requires. Local files do not use
END or DISCONTINUITY; the exact duration in the header trims a zero-padded last
frame. Packet parsing is shared with the streaming codec and permits no extra
bytes.

A stereo record starts with `4b 53 46 01`, then a four-byte IEEE binary32
normalization scale encoded little-endian. The scale must be finite and
greater than zero and no greater than two. The remaining data are 10-bit
tokens, most-significant bit first, time-major and then codebook-major, for
exactly 150 latent frames. Its total length is `8 + codebooks * 150 * 10 / 8`,
at most 3008 bytes. Stereo records are separate from `KMA2` and cannot be used
as a live or KMX profile.

The reader accepts a regular descriptor and duplicates it without taking
ownership of the caller's descriptor. It derives a strict maximum file size
from the bounded header before copying the source into a sealed private
memfd. The copied header, index and all records are validated again from those
sealed bytes. Growth, early EOF and observable concurrent mutation during
copy are refused. Subsequent changes to, or removal of, the original file do
not change accepted playback bytes. This is structural validation; the file
format does not authenticate who authored the audio. No allocation is based
on an unvalidated record length. The largest valid index is about 2.1 MiB;
the sealed snapshot is bounded by the profile's maximum encoded file size.

The writer requires an empty writable regular descriptor without append mode.
It writes validated records, then the index, then the header after the exact
declared record count has arrived. It never overwrites a nonempty file or
deletes a path. Its caller owns publication, synchronization and failed-output
cleanup. Opening with `O_EXCL` avoids replacing an existing destination.

`kenc_stereo_overlap_apply` uses the retained reference's triangular float32
weights. It emits the first 47520 samples of each decoded one-second frame
and retains the final 480 weighted samples for the next overlap. Reset state
before a new source. A later indexed stereo seek must decode one preceding
frame without emitting it to prime this state; then the requested frame's
overlap matches continuous playback exactly. The reader exposes frame
boundaries so a consumer can perform this pre-roll. Mono seeks instead reset
the decoder and begin at the returned verified RESET epoch. The decoder starts
that epoch with the file's own epoch-start profile (for C5-R4, the RESET
packet's own four-packet lead-in), so the sought epoch decodes exactly as in
continuous playback. File duration always trims padding; a caller must never
emit unbounded tail samples.

The default C suite covers malformed headers and lengths, version 2 markers
and marker/record disagreement, indexes, all stereo rates, every truncated
stereo record length, descriptor ownership, mutation after load, short-buffer
atomicity and overlap transitions. `make test-file-cli` with assets encodes
and decodes C0 and C5-R4 files and refuses tampered markers; with
`C0_REFERENCE_COMMAND` it also requires byte-identical C0 files and WAV output
against a 3747330 build. The optional `make test-container-oracle` uses the
locked export environment's NumPy oracle to compare three complete three-frame
waveforms and exact indexed pre-roll. These tests do not qualify Amp, KMX,
timing or human listening.
