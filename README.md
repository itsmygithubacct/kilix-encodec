# kilix-encodec

The offline shared-library package recipe is documented in
[PACKAGING.md](PACKAGING.md). It installs one `/usr` library for Amp and KMX;
model setup and converter execution remain separate explicit operations.

`kilix-encodec` is the C11 provider for Kilix EnCodec packet encoding and
decoding. Its optional native ONNX backend implements stateful 24 kHz mono
encoding and decoding at 3, 6 and 12 kb/s, plus separate 48 kHz stereo frame
encoding and decoding at 3, 6, 12 and 24 kb/s. No runtime binary, graph, checkpoint, codebook,
audio fixture, or weight payload is included. Native functional support does
not establish release qualification, consumer integration, or listening
acceptance.

## Build and verify

```sh
make test
make sanitize
uv sync --frozen
```

`make test` builds all 3 of 3 products, runs 6 of 6 C test binaries, exercises
CLI format refusals and checks the skeleton manifest with the locked Python
tool environment. `make sanitize` repeats the C and CLI checks under AddressSanitizer and
UndefinedBehaviorSanitizer.

The default build requires no neural runtime and refuses model loading. To
build the native backend, install the selected ONNX Runtime 1.21 C API and
OpenSSL development libraries, then use an explicit local export:

```sh
make ONNX=1
make ONNX=1 test-native MODEL_DIR=/path/to/pinned/24khz-export
# Optional independent token/PCM comparison, using the locked export group:
uv run --frozen --group export make ONNX=1 test-native \
  MODEL_DIR=/path/to/pinned/24khz-export ORACLE=1
```

`ONNX_CFLAGS` and `ONNX_LIBS` can select a separately staged runtime. Its shared
libraries must also be on the executable's loader path. Native and default
builds use different directories (`build-onnx` and `build`). Static consumers
can obtain runtime, crypto and math dependencies with `pkg-config --static`.

The native loader accepts exactly the eight 24 kHz graphs and canonical
manifest emitted by exporter 0.1.5 with the MIT encodec source pin. It checks
their compiled byte counts and SHA-256 digests before initializing ORT, then
creates sessions from those same verified bytes. Asset symlinks, special files and substitutions are refused.
Tensor names, ranks, dimensions and types are checked before allocating stream
buffers. Each stream has separate recurrent state and 1 or 2 CPU threads;
inference uses caller-owned output storage and preallocated state tensors.

The separate `kenc_stereo_*` API verifies the exact 48 kHz manifest, two graphs
and raw codebooks before ORT initialization. It processes noncausal one-second
frames, preserving per-frame normalization. Input/output float PCM is
interleaved stereo; codes are codebook-major. The C quantizer is checked against
the official safetensors model on identical latents, and every corpus/rate row
must meet the existing end-to-end token and waveform tolerances. The separate
`kilix_encodec_file.h` interface adds bounded local files, verified indexes,
sealed input snapshots and shared overlap-add. [FILE-FORMAT.md](FILE-FORMAT.md)
specifies framing, limits, padding, seek pre-roll and the version 2 epoch-start
marker. Live framing and consumer
integration remain separate work. The stereo API is never a KMX profile.

```sh
uv run --frozen --group export make ONNX=1 test-stereo \
  MODEL_DIR=/path/to/pinned/48khz-export \
  CHECKPOINT_DIR=/path/to/pinned/48khz-safetensors
```

The native CLI converts PCM16 WAV files with an explicit model directory:

```sh
build-onnx/kenc encode --model-dir /path/to/pinned/24khz-export \
  --profile 24k --bitrate 6 input-mono-24000.wav output.kenc
build-onnx/kenc encode --model-dir /path/to/pinned/48khz-export \
  --profile 48k --bitrate 12 input-stereo-48000.wav output-stereo.kenc
build-onnx/kenc decode --model-dir /path/to/pinned/48khz-export \
  output-stereo.kenc decoded.wav
```

`--threads 1|2` controls native inference. Inputs must match the selected rate
and channel count; this command does not resample. The parser bounds the WAV
chunk count and every length and rejects symlinks and special input files.
Existing outputs are preserved with exclusive creation. A failed conversion
retains its newly created incomplete output for caller cleanup; no path is
deleted. The complete header is written last and synchronized on success.
Keep an input WAV unchanged during encoding. Decoding uses the library's sealed
file snapshot. Files exceeding the standard RIFF output-size limit are refused.
Offline conversion reports progress and estimated remaining time on stderr.

`kenc decode --seek-sample N` starts at the verified epoch or stereo frame
boundary at or before N and reports the actual starting sample. The shared
`kenc_file_source_*` decoder performs stereo pre-roll internally, returns
interleaved float PCM and trims the final block to the exact duration. Its
synchronous model loading/inference must run in an owned worker in interactive
consumers. Short output buffers do not advance it, and a runtime failure
requires a successful seek before further pulls. Both profiles share this
source adapter; it owns no output device or DSP.

Native source and CLI tests exercise lifetime and buffer behavior, all seven
bitrate rows, exact duration, preserving outputs and sample-exact indexed seeks:

```sh
make ONNX=1 test-source-c test-file-cli \
  MODEL_DIR=/path/to/24k-export STEREO_MODEL_DIR=/path/to/48k-export
```

These are functional checks, without a new capacity or consumer qualification
claim.

The public caller-owned PCM and PTS inputs are the deterministic test boundary.
Tests provide seeded synthetic inputs through these same APIs; no production
environment switch substitutes neural output or bypasses model verification.

`tools/bench_native.py` measures the native library directly using standard
Python and the public C ABI. It records every measured call, nearest-rank p99,
binary/manifest/runtime digests and peak RSS. The default 24 kHz population is
1,000 calls in each of six encode/decode rows. The stereo profile uses 100 calls
in each of four decoder rows. Outputs explicitly distinguish unfrozen-host
measurements from a verified H1 fixture and do not grant whole-release credit.

```sh
python tools/bench_native.py --library build-onnx/libkilix-encodec.so \
  --assets /path/to/pinned/24khz-export --output /path/to/new-result.json
```

### Packet contract

KMA2 packets start with bytes `4b 4d 41 02`, followed by canonical unsigned
LEB128 integers for profile (1), epoch, packet index and PTS in milliseconds;
one flags byte; then canonical sample-count and payload-byte-count integers.
The payload stores 10-bit tokens most-significant-bit first, ordered by time
and then codebook. Codebooks (4, 8 or 16) are selected outside the packet by
the agreed stream profile. The parser verifies the exact derived payload
length, bounds every integer and refuses nonminimal encodings, trailing bytes,
unknown flags, out-of-range values and incompatible profiles.

Every regular encoder call consumes 960 samples (40 ms). The first packet and
each configured epoch boundary carry RESET; explicit encoder reset also marks
DISCONTINUITY. At every RESET packet both sides start from zeroed state, so an
epoch never depends on earlier packets and no latency is added. A decoder
refuses dependent packets after loss or reordering until it receives a later
RESET. Replayed epochs are refused. Short output buffers and malformed packets
do not write output.

### Epoch-start profiles and negotiation

Each stream has one epoch-start profile (owner decision OD-AT):

- **C0**: the zeroed state processes the RESET packet directly. This is the
  0.2.1 behaviour and the default of every new encoder and decoder, so a
  stream, peer or file without a marker is C0. Its packets are byte-identical
  to those of 3747330.
- **C5-R4** (owner decision OD-AL): the zeroed state is first primed by running
  the RESET packet's own input through the network four times as a discarded
  lead-in, which costs five network runs for that packet. Every RESET packet
  of a C5-R4 stream carries flag `0x08` (`KENC_PACKET_FLAG_EPOCH_PREROLL`).

Peers exchange `kenc_epoch_start_supported()` in their own handshake, then call
`kenc_epoch_start_negotiate(local, peer, &profile)`. It selects C5-R4 only when
both advertise it; a peer without an advertisement (0) gets C0, and a nonzero
advertisement without C0 is refused. Both sides then call
`kenc_encoder_set_epoch_start` or `kenc_decoder_set_epoch_start` before their
first packet, or after an explicit reset. A decoder refuses a RESET whose
marker differs from its profile with `KENC_ERR_EPOCH_START`, without output or
state change. A pre-marker decoder refuses the marked flag as a protocol error.
Mixed peers therefore either decode matching PCM or refuse; they never decode
silently different audio. Files carry the profile in format version 2
([FILE-FORMAT.md](FILE-FORMAT.md)), and `kenc encode --epoch-start C5-R4`
writes one. `tools/epoch_stream.py` offers the same profiles, markers,
negotiation and file-header reading in Python.

The native tests exercise both profiles at all rates, independent streams,
reset recovery, asset substitution, epoch-start independence, negotiation,
marker refusal and profile selection rules. With `ORACLE=1` they compare exact
tokens and PCM against independent Python ORT sessions running
`tools/epoch_stream.py` with the same profile, including the programme's
syn-fixture item at 6 kb/s: tokens identical and PCM within 1 LSB, and the
Python renders equal the checked C5-R4 render and the 3747330 C0 reference.
`C0_REFERENCE_LIBRARY` names a library built from a git archive of 3747330.
C0 packets and PCM must then be byte-identical at all rates, each library must
decode the other's C0 stream with matching PCM, and 3747330 must refuse a
C5-R4 stream. Without it those 18 controls are printed and counted as SKIPPED.

## Export controls

Export dependencies are isolated in the locked `export` group. The exporter
accepts only the exact reviewed caller-supplied checkpoint and refuses to write
inside the Git repository:

```sh
make export-env
make export-test \
  CHECKPOINT=/path/to/encodec_24khz-d7cc33bc.th \
  OUTPUT_DIR=/path/to/empty/scratch-directory
```

The 24 kHz scratch bundle contains graphs 8/8 for all 3/3 required bandwidth
profiles (3/6/12 kb/s), a canonical manifest 1/1, a canonical
verification result 1/1 and synthetic listening fixtures 3/3. Verification
checks every graph contract, exact nested RVQ prefixes, continuous-oracle token
identity at all 3/3 rates, decoder parity, all 8/8 fixed-shape refusals and
all 6/6 profile timing pipelines. These graph checks use the graphs' all-zero
initial state; streams use the epoch start below.

A C5-R4 stream begins every epoch of 25 packets, and the stream start, with a
repeat pre-roll (`tools/epoch_stream.py` with `profile="C5-R4"`; the native
runtime does the same once C5-R4 is selected). Encoder and
decoder zero all state, run the epoch's first packet through the unchanged
per-packet graph 4 times as a discarded lead-in (its 960 samples, or its 3
code frames through the RVQ decoder), and then process that packet. Only the
packet itself is used, so no latency is added; an epoch start costs 5 network
runs. The lead-in run count at a stream start is counted on the graph sessions
and must equal a literal 4 pinned in `tools/verify_export.py`, independently of
the runtime's own constant (2/2). A four-epoch stream must match the checkpoint
rendering each epoch as
the lead-in followed by the epoch, with the lead-in's 12 latent frames or 3840
samples dropped: latent parity 4/4 epochs including the first 6 latent frames,
token identity 4/4, waveform parity 4/4 including the first 150 ms, and each
of 3/3 reset epochs bit-identical to a fresh stream over that epoch. The same
comparison must refuse the previous constant cold start and a reflect-padded
pre-roll at 16/16 epoch heads. Corrupting all audio, or all codes, before
epoch 1 or 2 must leave every later packet bit-identical (4/4); a repeated
stream must be identical (1/1). A perturbation probe must find 0 packets of
added lookahead, and 1 for a planted one-packet lookahead (2/2). The listening
pair shares the same stream start and is identical before its epoch boundary
(1/1). The timing result is labelled unfrozen-host measurement and receives
measured H1 gate credit 0/1. Blind listening and pinned offline delivery
remain 0/1 each.

`tools/verify_epoch_programme.py` then holds that runtime to the checked F101
remedy programme recorded in `tests/fixtures/f101-c5r4-programme.json`: the
14 programme items' codes and float32 PCM identities at 6, 3 and 12 kb/s,
fresh-encoder and fresh-decoder identity for epochs 1-11 at each rate, the
never-reset rendering's identity, and the 3 and 12 kb/s boundary level tables
within 0.01 dB. Its `legacy-c0` check renders every available item with the
C0 profile and requires codes and PCM identical, at all three rates, to
`tests/fixtures/f101-c0-3747330-reference.json`. That reference was rendered by
the streaming functions of a git archive of 3747330
(`tests/fixtures/build_c0_reference.py`). The 9 synthetic items are regenerated
and must hash to the
programme manifest. The 5 recorded excerpts are read from the directory named
by `KENC_F101_PROGRAMME_DIR`; when it is unset, every control that needs them
is reported and counted as SKIPPED, never as passed:

```sh
KENC_F101_PROGRAMME_DIR=/path/to/programme make export-test \
  CHECKPOINT=/path/to/encodec_24khz-d7cc33bc.th \
  OUTPUT_DIR=/path/to/empty/scratch-directory
```

For a frozen-fixture measurement, both verifiers accept `--fixture-tier h1`
together with the frozen `fixture.sh` path. They fail closed unless the guest
proves the exact runner digest and complete H1 identity: Debian 13.5,
q35/qemu64, 4/4 vCPUs, 8 GiB RAM, the root filesystem on the frozen 100 GiB
disk, and at least 80 GiB free. The 24 kHz gate requires at least 1,000/1,000
measured packets in every one of 6/6 encode/decode pipelines and p99 below
20 ms (therefore at least 2x real-time); the 48 kHz decoder requires at least
100/100 measured frames and must sustain its 990 ms cadence.

### Blinded epoch-boundary trial

The verifier's `listening/` directory is input to a facilitator-operated,
paired forced-choice trial. Both renderings start with the same pre-roll; the
epoch-reset rendering also restarts at 1 s, so the pair differs only from that
boundary. Preparation copies randomized `A`/`B` pairs into a
public directory while keeping the answer key in a separate private file:

```sh
python tools/listening_trial.py prepare \
  --fixtures /path/to/export-bundle/listening \
  --public-dir /path/to/empty/public-trial \
  --answer-key /path/to/private/answer-key.json \
  --trials 20
```

After each listener returns a completed copy of `response-template.json`, the
facilitator scores one or more responses without modifying the public trial:

```sh
python tools/listening_trial.py score \
  --public-dir /path/to/public-trial \
  --answer-key /path/to/private/answer-key.json \
  --response /path/to/listener-01.json \
  --result /path/to/private/measured-result.json
```

The manifest, private mapping, audio pairs, responses, and result are
digest-bound. Scoring reports exact forced-choice and binomial populations but
grants blind-listening acceptance credit 0/1; that decision remains with the
release owner.

The separate 48 kHz stereo file-profile exporter accepts only the exact pinned
official safetensors input and refuses pickle-capable model files. It exports
only to an empty scratch directory:

```sh
make export-48khz-test \
  MODEL_DIR=/path/to/pinned/encodec_48khz-safetensors \
  OUTPUT_DIR=/path/to/empty/scratch-directory
```

That scratch bundle contains graphs 2/2, raw RVQ codebooks 1/1, a canonical
manifest 1/1, a canonical verification result 1/1, and synthetic listening
fixtures 4/4. Verification covers all 5/5 fixtures at all 4/4 supported
bandwidths, bounded end-to-end token drift, decoder parity, deterministic
three-frame overlap-add, fixed-shape refusal, and an unfrozen-host timing
harness. Blind listening and the measured H1 gate remain 0/1 each until run on
the frozen fixture.

## Current boundary

- Input/output ownership follows `include/kilix_encodec.h`: callers own all
  packet and PCM storage; mutable encoder/decoder state is never shared.
- The native library performs 0 of 1 network operations and invokes 0 of 1
  Python runtimes.
- The repository contains no model artifacts. Without `ONNX=1`, model loading
  returns `KENC_ERR_MODEL`. Native builds require the exact caller-supplied
  bundle for the chosen profile; they do not claim it is release-qualified.
- The export tool performs 0 of 1 checkpoint downloads. It opens only the
  caller-supplied regular file, verifies its exact size and SHA-256, and uses
  PyTorch's restricted weights-only loader.
- Both checkpoints and every derivative are CC BY-NC 4.0, licensor Meta
  Platforms (OD-AR). They are downloaded on first use from their upstream pins
  and converted on the user's machine only under a covering kilix-license
  receipt (see "Local conversion commands"); nothing is redistributed, and
  publication is owner-reserved. Both export manifests record that licence as
  `license` (`CC-BY-NC-4.0`, `Meta Platforms`). The 24 kHz manifest records
  `checkpoint_delivery` `upstream-convert` and `derived_graph_publication`
  `user-machine-only-never-published` in place of the superseded 0.2.1
  user-supplied and no-grant strings; its graphs did not change.

Large graphs, weights, and codebooks do not belong in Git history.

The public `kenc_packet_metadata_read` parser exposes verified epoch, index,
PTS, flags and sample count without model loading. It validates the complete
opaque KMA2 packet against explicit options; only a decoder context checks
continuity against prior packets. A refusal leaves metadata unchanged. This
separate structure preserves the existing decoder-output ABI and keeps wire
parsing inside the codec library for file/live/KMX consumers.

Installed consumers can use `kenc_model_load_fds`, `kenc_stereo_create_fds`
and `kenc_file_source_create_fds` with the complete named set of read-only,
sealed Linux descriptors obtained from their receipt adapter. Each descriptor
must be caller-owned and CLOEXEC, with all four write/grow/shrink/seal seals.
The library checks the same exact manifest and graph sizes and hashes before
ORT parses any graph. It preserves borrowed offsets, never closes those FDs,
and retains its own bytes after loading. Extra, duplicate or missing names
refuse. The unused file profile's descriptor set can be null.

These native interfaces verify model bytes. The installed consumer still must
obtain current packaged catalog and license-receipt authority through
`Installer.open_asset`; a path or sealed descriptor alone is not model admission.
Existing directory loaders remain available for explicit development tools.
`make ONNX=1 test-asset-fds MODEL_DIR=... STEREO_MODEL_DIR=...` checks sealed
input refusal, byte-for-byte directory-loader parity and post-close lifetime
for both profiles and the shared file decoder. It does not qualify a consumer
or transfer timing/listening results to a new release candidate.

Installed admission is an additional explicit build option:

```sh
make ONNX=1 CONTENT=1 CONTENT_SOURCE=/path/to/kilix-content \
    CONTENT_COMMIT=FULL_40_CHARACTER_COMMIT
```

The build reads that exact content Git archive, includes its license, and
embeds a deterministic ZIP containing the complete authority package, packaged
catalog and this provider's admission helper. `content_bundle.receipt.json`
records every source hash, exact content commit and ZIP digest. No package or
catalog is taken from the process's Python path. The final source closure must
bind both this provider and the chosen content source; rebuilding against a
new catalog produces a new binary and build receipt. Model payloads are never
part of this bundle. `CONTENT=0` is development-only byte/path functionality;
it refuses the installed admission API.

The packager reads raw commit, tree and blob objects and recomputes their Git
identities. Git replacement refs, archive attributes and ambient Git routing
cannot substitute or omit package members. The version-2 build receipt records
the content tree and every selected source blob, alongside the deterministic
ZIP and file digests. Missing, oversized or unsupported objects refuse before
any output is emitted.

`kilix_encodec_content.h` exposes `kenc_installed_assets_open`. Each call runs
the embedded ZIP from a read-only sealed memory descriptor with isolated system
Python, a minimal environment and hard resource ceilings. The caller supplies
an absolute content storage root and a 1–120000 ms deadline. The helper checks
the exact F101 IDs, versions, graph population, compatibility and packaged
catalog before opening the production receipt store and `Installer.open_asset`.
All declared notices and metadata are verified too; only the native 9/4 graph
members are returned. Every returned descriptor is rehashed by the helper,
then subject to the native loader's unchanged compiled hash/ORT checks.

The C caller accepts one bounded, credential-bound descriptor record only
after the owned helper exits successfully, closes all received FDs on refusal,
and kills/reaps only its own helper on cancellation or deadline. No arbitrary
helper/interpreter path, ambient loader/Python import configuration, caller
catalog, receipt decision, download, converter or path-only admission is used.
The optional `XDG_STATE_HOME` continues to select the production receipt store
under that API's ownership and authority checks. A successful asset object owns
its FDs until `kenc_installed_assets_free`; model contexts retain their own bytes.

`make test-content-python CONTENT_SOURCE=/path/to/kilix-content` checks the
real receipt/snapshot API with clearly synthetic graph identities and catalog
data. `make test-content-ipc` checks C descriptor framing, privacy, limits,
cancellation and cleanup with an explicitly synthetic embedded peer. These
fixtures provide no actual model admission or release qualification credit.

## Local conversion commands

`tools/build_converter.py` builds a relocatable conversion command from the
unchanged export source and lock. Run it with system Python on Linux x86-64,
once per profile:

```sh
python3 tools/build_converter.py                    # 24 kHz stateful graphs
python3 tools/build_converter.py --profile 48khz    # 48 kHz stereo frame graphs
```

The default build acquires the exact UV 0.12.3 and CPython 3.12.8 tool archives
listed in `tools/converter-inputs.json`, then constructs a fresh CPU environment
from the frozen lock. The lock installs facebookresearch/encodec
`2d29d9353c2ff0ab1aeadc6a3d439854ee77da3e` (MIT), not PyPI 0.1.1. It builds
that EnCodec sdist only after the locked wheels supply setuptools. No model
input is acquired. Tool archives and dependency
artifacts use a private cache; `--offline` requires that cache to be populated.
`--cache-dir` selects another private cache, `--output-root` selects an existing
destination, and `--timeout` bounds the entire build, up to 3,600 seconds.
An explicit development environment can be selected only with all three
`--environment`, `--python` and `--uv` paths. That route records the selected
bytes and the exact package population; it does not acquire a new environment.

| profile | command | runtime directory | upstream input | output population |
| --- | --- | --- | --- | --- |
| `24khz` | `bin/kilix-encodec-convert-24khz` | `.converter` | `encodec_24khz-d7cc33bc.th` from `dl.fbaipublicfiles.com` | 8 stateful graphs and `manifest.json` (`op17-v2-bb615145`) |
| `48khz` | `bin/kilix-encodec-convert-48khz` | `.converter-48khz` | `model.safetensors`, `config.json`, `preprocessor_config.json` from `facebook/encodec_48khz` at `c3def8e7185ac8c8efdce6eb8c4a651e487a503e` | 2 frame graphs, `rvq-codebooks.f32le` and `manifest.json` (`op17-v1-433b26a1`) |

Each output consists of the command, its adjacent runtime directory holding
`runtime.tar`, and a build receipt. Keep these together when relocating them.
The receipt binds the profile, the exporter, lock, tools, package population,
every runtime member, notices, the pinned licence authority, builder and
generated command. It is build evidence;
F100 installed-asset and source-supply authority are separate. Existing output
entries are never overwritten. The dedicated build process owns and reaps its
children before removing its private staging directories, including children
that create a new session. Inputs and destination directory identities are
checked while building; symlink or shared directory chains refuse.
Generated package `RECORD` indexes are omitted because they include temporary
environment command wrappers which are not shipped. Dependency code, version
metadata and license notices are retained and bound in the runtime receipt.

### Licence receipt gate

Both checkpoints and every derivative are CC BY-NC 4.0, licensor Meta
Platforms (owner decision OD-AR). The licence authority is kilix-license
(OD-AJ): its records, first-use screen, agreement and receipts decide, and the
converter carries no terms text of its own. `third_party/kilix-license/` is a
`git archive` of kilix-license at the commit in `third_party/kilix-license.pin`.
The builder verifies every vendored file against `tools/converter-inputs.json`
and embeds the kilix-license modules and the profile's licence record in the
command, so nothing is imported from disk at run time:

| profile | licence record | record digest |
| --- | --- | --- |
| `24khz` | `encodec-24khz-stateful` | `8af1dc699df34436899f0b93dce271d2fadb119f62e136e74ad797686d880f4b` |
| `48khz` | `encodec-48khz-frame` | `9c1baee4ad48816bba2568c7e14f136b388f11722d6e422a933a1a4a24d30f3f` |

Before it reads any input, runtime or output, the command calls kilix-license
`require()` for that record and the asset manifest digest it is given. It
refuses unless the receipt store holds a receipt covering the whole binding:
record digest, manifest digest, licence id, licence text digest, decision,
licensor and the agreement-required non-commercial condition. A missing
receipt, a receipt for another manifest or record, or a planted receipt whose
licence text digest or any other bound field changed is refused. The receipt
store must be an existing private directory of the current user; the command
only reads it. An absent or empty `--receipt-store`, and an absent or empty
`--manifest-digest`, are refused by the same gate, with exit status 1, before
any input is read.

### Running a conversion

`kilix models install` is to download the pinned upstream bytes on first use,
after the licence screen has recorded a receipt, and then run the command.
That installer path is not here yet: it arrives with C4 (kilix-content) and
KX (the kilix installer). kilix-content's upstream-convert argv does not yet
pass `--receipt-store` or `--manifest-digest`. Until then the command is run
directly, as below. The input is never a user-selected file: the command
accepts only the exact pinned sizes and SHA-256 digests above.

```sh
bin/kilix-encodec-convert-24khz \
  --input /absolute/path/to/encodec_24khz-d7cc33bc.th \
  --output /absolute/path/to/empty-output \
  --receipt-store /absolute/path/to/kilix-license-receipts \
  --manifest-digest ASSET_MANIFEST_SHA256 \
  --timeout 180

bin/kilix-encodec-convert-48khz \
  --input /absolute/path/to/directory-with-the-three-model-files \
  --output /absolute/path/to/empty-output \
  --receipt-store /absolute/path/to/kilix-license-receipts \
  --manifest-digest ASSET_MANIFEST_SHA256 \
  --timeout 300
```

`--manifest-digest` is the asset record's manifest digest, the value the
licence screen bound into the receipt. The output directory must exist, be
empty, be owned by the current user and have mode 0700. The command seals
every input and its complete runtime before execution, runs the unchanged
exporter in a private process/network/mount namespace (the restricted
weights-only loader for 24 kHz, safetensors only for 48 kHz), and checks every
output file against the bound population. The bounded temporary runtime is
read-only before the selected interpreter starts; the trusted bootstrap
installs hard resource limits first. Two CPU threads are selected.
Cancellation and deadlines tear down the owned process tree. A failed attempt
can leave partial files in its output directory for the caller to inspect or
discard; a new conversion requires an empty output.

The output holds exactly the bound population and no notice: licence notices
are the installer's, taken from kilix-license texts. Neither this tool nor its
output grants redistribution, creates a source-supply decision, or admits an
installed model. Checkpoints, runtime archives and generated graphs must not be
committed to this repository. The command needs system Python, bubblewrap,
user namespaces and Linux memfd seals. The development runtime occupies
roughly 1.1 GiB on disk and additional temporary memory while converting; this
is not a fitted device profile.

`make test-converter` runs bounded file, cancellation, process ownership,
output-publication and receipt-gate controls without model payloads or network
access. Real conversion, reproducible builds and installed admission require
their separate exact inputs and evidence.

## Model weight guard

`make test` includes `tests/test_no_model_weights.py` (R4-198). It scans the
tracked tree for model suffixes, weight magic numbers (GGUF, ggml, OpenFst,
safetensors, PyTorch zip and legacy checkpoints, ONNX) and size, and for any
blob whose SHA-256 is in `tests/data/catalog_digests.txt`. That list is
generated by `make catalog-digests` from the checkpoints and converted
populations bound in `tools/converter-inputs.json` and
`python/graph_population.py`; `--catalog` folds in an accepted kilix-content
catalogue's EnCodec members. `make catalog-digests-check` and the test refuse
a hand-edited or stale list.

The guard has an inherent limit. A fragment of raw weight data below the 2 MiB
size bound, with a neutral name, no magic number and a digest of its own that
is not listed, passes. Raw float32 codebooks have no header, and one 1024x128
codebook is 512 KiB, so the first 1 MiB of `rvq-codebooks.f32le` committed as
a neutral `.dat` file is not detected. Review of what is committed covers that
case; the scan does not.
