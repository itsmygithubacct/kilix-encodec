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

**Status:** ships in Plebian-OS 0.2.3. Firstboot installs the native Debian
package `libkilix-encodec` (0.1.5, built from source commit `320ce65b` with
Kilix Content `d9a1335d`) from this repository's `native-deb-rc5` release
asset, named by `PLEBIAN_OS_NATIVE_DEB_URL` in `releases/0.2.3.env`.
Plebian-OS provisioning also installs the two conversion commands (no models) from the
`converters-0.2.2-test.1` release into `/usr/lib/plebian-os/kilix-encodec/`.
The EnCodec models never ship: the Kilix Content assets
`encodec-24khz-stateful` and `encodec-48khz-frame` download the upstream
checkpoints on first use, after the CC BY-NC 4.0 licence screen, and convert
them on the user's machine.

## What it does

- Encodes and decodes KMA2 packets for live 24 kHz mono audio (Kilix
  multiplexer audio) with per-stream recurrent state.
- Encodes and decodes `.kenc` files, 24 kHz mono or 48 kHz stereo, with
  verified indexes and sample-exact seeking (`kenc` CLI and
  `kilix_encodec_file.h`).
- Admits models only through the Kilix Content catalog and a current
  kilix-license receipt (`kenc_installed_assets_open`, `CONTENT=1` builds).
- Builds the local converter commands that turn the pinned upstream checkpoints
  into the exact ONNX graph populations the loader accepts.

## Quick start

```sh
make test          # default build: no neural runtime, model loading refused
make sanitize      # the same checks under ASan and UBSan
make ONNX=1        # native backend; needs ONNX Runtime 1.21 and OpenSSL dev files
build-onnx/kenc encode --model-dir /path/to/pinned/24khz-export \
  --profile 24k --bitrate 6 input-mono-24000.wav output.kenc
build-onnx/kenc decode --model-dir /path/to/pinned/24khz-export \
  output.kenc decoded.wav
```

The full build and test matrix is in [docs/build-and-cli.md](docs/build-and-cli.md).

## Usage

| Command | What it does |
|---|---|
| `kenc encode --model-dir DIR --profile 24k\|48k --bitrate N IN.wav OUT.kenc` | Encode PCM16 WAV; `--threads 1\|2\|4`, `--epoch-start C5-R4`. |
| `kenc decode --model-dir DIR [--seek-sample N] IN.kenc OUT.wav` | Decode, optionally from a verified seek point. |
| `make test` / `make sanitize` | Default-build C, CLI and manifest checks. |
| `make ONNX=1 test-native MODEL_DIR=...` | Native 24 kHz tests (`test-stereo`, `test-source-c`, `test-file-cli`, `test-asset-fds` for the rest). |
| `make export-test` / `make export-48khz-test` | Export and verify graph bundles from a caller-supplied checkpoint. |
| `make ONNX=1 CONTENT=1 CONTENT_SOURCE=... CONTENT_COMMIT=...` | Build with installed-asset admission embedded. |
| `python3 tools/build_converter.py [--profile 48khz]` | Build a converter command. |
| `python3 tools/build_native_package.py ...` | Build the `libkilix-encodec` Debian package ([PACKAGING.md](PACKAGING.md)). |
| `make catalog-digests-check` | Check the model-weight guard's digest list. |

## How it works

```
upstream checkpoint (first use, after licence receipt)
   |  bin/kilix-encodec-convert-24khz | -48khz   (sandboxed, local only)
   v
ONNX graphs + manifest in the Kilix Content store
   |  kenc_installed_assets_open: catalog entry + kilix-license receipt
   |  -> sealed read-only descriptors
   v
libkilix-encodec  (hash checks, then ONNX Runtime sessions)
   |-- KMA2 live packets (24 kHz mono)    -> KMX (kilix-multiplexer)
   |-- .kenc files (24k mono, 48k stereo) -> kenc CLI
   `-- linked through pkg-config          -> Kilix Amp
```

The library never touches the network. Every graph is checked for size and
SHA-256 before ONNX Runtime parses it, and sessions are created from those
verified bytes. Installed consumers ask an embedded helper, run from a sealed
ZIP with isolated system Python, to confirm the catalog entry, the licence
receipt and the installed tree on every call; it returns sealed descriptors that
the native loader checks again. Packets carry RESET points at epoch boundaries
so a stream recovers from loss without added latency. Depth:
[docs/installed-admission.md](docs/installed-admission.md),
[docs/packet-contract.md](docs/packet-contract.md) and
[FILE-FORMAT.md](FILE-FORMAT.md).

## Documentation

- [PACKAGING.md](PACKAGING.md): building the shared Debian package.
- [FILE-FORMAT.md](FILE-FORMAT.md): the `.kenc` file format.
- [docs/build-and-cli.md](docs/build-and-cli.md): default and native builds,
  the stereo API, the `kenc` CLI, seeking, native tests and benchmarks.
- [docs/packet-contract.md](docs/packet-contract.md): KMA2 packets and the C0
  and C5-R4 epoch-start profiles.
- [docs/export.md](docs/export.md): graph export and verification, the F101
  programme check and the blinded listening trial.
- [docs/installed-admission.md](docs/installed-admission.md): the current
  boundary, the model licence, sealed descriptors and installed admission.
- [docs/conversion.md](docs/conversion.md): building and running the local
  converters and the licence receipt gate.
- [docs/model-weight-guard.md](docs/model-weight-guard.md): the test that keeps
  weights out of the repository.
- [docs/architectures.md](docs/architectures.md): AMD64 and ARM64 package and
  converter selection.
- [packaging/onnxruntime/README.md](packaging/onnxruntime/README.md): the
  Debian ONNX Runtime patches.
- [CHANGELOG.md](CHANGELOG.md) and
  [THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md).

## Related repositories

- [kilix-multiplexer](https://github.com/itsmygithubacct/kilix-multiplexer#readme)
  (KMX): compressed audio over the semantic session stream.
- [kilix-amp](https://github.com/itsmygithubacct/kilix-amp#readme): builds with
  `ENCODEC=1` against this library.
- [kilix-content](https://github.com/itsmygithubacct/kilix-content#readme): the
  catalog records for the EnCodec assets and converters.
- [kilix-license](https://github.com/itsmygithubacct/kilix-license#readme): the
  licence authority, vendored under `third_party/kilix-license/`.
- [plebian-os](https://github.com/itsmygithubacct/plebian-os#readme): installs
  the package and the converters.

## Part of Plebian-OS

kilix-encodec is one of the repositories behind
[Plebian-OS](https://github.com/itsmygithubacct/plebian-os#readme), a Debian
install whose desktop is [Kilix](https://github.com/itsmygithubacct/kilix#readme)
running the [Kilix 95](https://github.com/itsmygithubacct/kilix-95#readme)
desktop through [Pleb](https://github.com/itsmygithubacct/pleb#readme).
Plebian-OS installs its native Debian package and converter commands from this
repository's release assets at firstboot. The map of every repository and how
it ships is on the
[components page](https://github.com/itsmygithubacct/plebian-os/blob/main/docs/components.md).

## License

The source is MIT-licensed; see [LICENSE](LICENSE). Both EnCodec checkpoints
and every derivative are CC BY-NC 4.0, licensor Meta Platforms; they are
converted on the user's machine only and never redistributed. Third-party
notices are in [THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md).
