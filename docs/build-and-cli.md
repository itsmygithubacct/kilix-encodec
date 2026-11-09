# Build, native backend and the kenc CLI

The default and native (`ONNX=1`) builds, the 24 kHz and 48 kHz APIs, the `kenc` file CLI, seeking, native tests and the benchmark tool. Moved from the README.

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
buffers. Each mono stream has separate recurrent state and 1, 2 or 4 CPU threads;
inference uses caller-owned output storage and preallocated state tensors.

The separate `kenc_stereo_*` API verifies the exact 48 kHz manifest, two graphs
and raw codebooks before ORT initialization. It processes noncausal one-second
frames, preserving per-frame normalization. Input/output float PCM is
interleaved stereo; codes are codebook-major. The C quantizer is checked against
the official safetensors model on identical latents, and every corpus/rate row
must meet the existing end-to-end token and waveform tolerances. The separate
`kilix_encodec_file.h` interface adds bounded local files, verified indexes,
sealed input snapshots and shared overlap-add. [FILE-FORMAT.md](../FILE-FORMAT.md)
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

`--threads 1|2|4` controls native inference; four threads are mono-only.
The count includes the calling thread and defaults to one. Inputs must match the selected rate
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
The [four-thread H1 result and required ORT dependency patches](../packaging/onnxruntime/README.md#sse2-inference-and-one-dimensional-transpose-convolution)
document the CPU allocation and separate it from the previous two-thread budget.

```sh
python tools/bench_native.py --library build-onnx/libkilix-encodec.so \
  --assets /path/to/pinned/24khz-export --output /path/to/new-result.json
```
