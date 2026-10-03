# Debian ONNX Runtime dependency candidates

The registration patch repairs the Debian loader. The separate performance
patch below reduces SSE2 inference cost. Both target Debian
`onnxruntime 1.21.0+dfsg-1`, after its Debian patch series.

## System-ONNX registration

This dependency patch is for the Debian `onnxruntime 1.21.0+dfsg-1` source,
after its Debian patch series. It addresses duplicate built-in schema
registration when Debian links system ONNX. It is a development candidate;
the EnCodec runtime and selected dependency package remain unchanged.

Debian's [system-ONNX patch](https://sources.debian.org/patches/onnxruntime/1.21.0%2Bdfsg-1/system-onnx.patch/)
uses the distro library, whose built-in schema registry owns lazy registration.
The upstream ORT environment also registers those sets explicitly. The patch
asks the system registry for a known built-in schema instead, enforcing its
availability, while retaining ORT's custom and contributed registrations.
Apply it only to this system-ONNX build; bundled-ONNX builds require their own
registration path. Relevant sources are [ORT's environment](https://github.com/microsoft/onnxruntime/blob/v1.21.0/onnxruntime/core/session/environment.cc)
and [ONNX's schema registry](https://github.com/onnx/onnx/blob/v1.17.0/onnx/defs/schema.cc).

```sh
# Inside an unpacked, exact Debian source with its patch series already applied:
patch --fuzz=0 -p1 < /path/to/debian-system-onnx-single-registration.patch
```

A private Debian 13.5 H1 build of this exact source completed with the CPU
shared-library target, system dependencies, and Python/tests disabled.
Development inference against both receipt-backed installed EnCodec profiles
passed with zero duplicate-schema diagnostics, after verifying the corrected
library's actual process mapping and hash. The first loader experiment lacked
the SONAME symlink and loaded the original library; that failed observation is
retained separately. Library filenames alone are not loader identity evidence.

This is not a packaged dependency selection or qualification result. Before
promotion, build distinct runtime and development Debian packages with their
source/patch/build provenance; select their exact dependency identities in the
native EnCodec closure and OS delivery; then repeat ABI/oracle, strict capacity,
integration and sustained/fault gates. Do not substitute a private library via
`LD_LIBRARY_PATH` for package qualification. Full F101 acceptance remains open.
The registration patch alone changes no graph, timing population, epoch,
pre-roll, thread contract, threshold or model terms.

## SSE2 inference and one-dimensional transpose convolution

`sse2-narrow-and-col1d.patch` addresses two CPU costs without changing model
bytes or arithmetic reduction order:

- SSE2 SGEMM computes only the needed vectors for a final 1–8-column panel,
  keeping MLAS's 16-column packed layout. The four-step K loop loads each
  row once and broadcasts its lanes in the original order. Wider panels and
  AVX/FMA platforms retain their existing kernels. The single-row assembly
  path also stops computing an unused second row.
- One-dimensional `Col2imNd` uses the existing height-one `Col2im` fast path
  when zero left padding, unit dilation and the inferred column count permit
  it. Other shapes retain the generic implementation. Contribution order is
  unchanged, including overlapping windows.

Apply both patches to the exact Debian source before configuring/building:

```sh
patch --fuzz=0 -p1 < /path/to/debian-system-onnx-single-registration.patch
patch --fuzz=0 -p1 < /path/to/sse2-narrow-and-col1d.patch
```

The EnCodec change keeps per-session worker pools, enables spinning only
inside a network run, stops spinning between runs and uses dynamic work
blocks. This avoids dependence on ORT's process-wide environment singleton.
Mono callers can explicitly select **four threads, including the caller**;
the default remains one and stereo supports one or two. Four threads expand
the previous native mono CPU budget; measurements with this option do not
establish a two-thread pass. Consumers must opt into that CPU allocation.

On 2026-10-03, the finished native development build passed all six C5-R4
H1 rows with four threads, 1,000 measurements and 50 warmups per row, with
nearest-rank p99 below the unchanged 20 ms threshold:

| kb/s | Encoder p99 (ms) | Decoder p99 (ms) |
| --- | ---: | ---: |
| 3 | 17.470 | 18.158 |
| 6 | 17.469 | 17.654 |
| 12 | 19.025 | 18.333 |

The fixture retained all 14 identity checks, including qemu64's SSE2 CPU,
four vCPUs and 8 GiB RAM. All five network evaluations at each C5-R4 reset
remain inside the timed call. This is native development capacity evidence;
it does not qualify a packaged dependency, concurrent KMX workload, transport,
listening acceptance or sustained/fault operation.

The evidence directory is
`/home/pleb/research/gpu_terminal/encodec-latency-20261003/`.
`final-four-capacity.json` preserves all 6,000 integer durations, exact loaded
library hashes, fixture identity and harness hashes; `final-four-execution.json`
records the command. `final-two-capacity.json` records the separate two-thread
comparison, which still exceeds 20 ms. `runtime-final-build-provenance.json`,
`h1-cmake-cache.txt`, `h1-packages.txt` and the build logs record the dependency
source, compiler, configuration and package versions. The native build command
was `make -j2 ONNX=1 BUILD=/path/to/build`; the dependency build used Debian
GCC 14.2, `RelWithDebInfo`, `-O2 -g -DNDEBUG`, the CPU shared-library target,
and Python/unit-test targets disabled.

The model/wire checks include 1,662/1,662 locked-host oracle and ABI controls,
969/969 H1 native controls, 373/373 independent H1 four-thread oracle controls,
and 259/259 H1 file controls with no skips. A separate-process comparison to
the original native library and original schema-fixed ORT runtime covered
480 C0/C5-R4 packets over all three rates: packets and metadata were identical,
and PCM differed by at most 1 LSB. The complete golden programme oracle ran
in its locked host environment: regenerating its Torch waveform on H1 produces
a different input hash, so no H1 golden-programme pass is claimed.

To compare the actual patched SSE2 assembly/narrow kernel and scatter dispatch
against the original source, without model weights or a full ORT build:

```sh
python3 packaging/onnxruntime/tests/run.py \
  /path/to/original-debian-ort /path/to/patched-debian-ort /path/to/empty-output
```

This runs 81,600 exact SGEMM comparisons and 34,350 exact scatter comparisons
under AddressSanitizer/UndefinedBehaviorSanitizer, including output guards,
tail sizes, zero/accumulate modes, padding/dilation fallbacks and partial rows.
It requires x86-64, a C/C++ compiler and the two source trees. The test helper
extracts the original functions from the supplied source instead of replacing
them with a reference reimplementation.
