# kilix-encodec

`kilix-encodec` is the C11 provider boundary for Kilix EnCodec packet encoding
and decoding. The 0.1.0 tree is the P1 buildable repository skeleton: it fixes
the public ownership and buffer contracts, builds static/shared libraries and a
diagnostic command, and fails closed because no model runtime or model artifact
is included yet.

## Build and verify

```sh
make test
make sanitize
uv sync --frozen
```

`make test` builds all 3 of 3 products, runs 4 of 4 C test binaries, and checks
the skeleton manifest with the locked Python tool environment. `make sanitize`
repeats the same 4 of 4 C binaries under AddressSanitizer and
UndefinedBehaviorSanitizer.

## Current boundary

- Input/output ownership follows `include/kilix_encodec.h`: callers own all
  packet and PCM storage; mutable encoder/decoder state is never shared.
- The native library performs 0 of 1 network operations and invokes 0 of 1
  Python runtimes.
- The repository contains 0 of 2 required model artifacts. Model loading
  returns `KENC_ERR_MODEL` until the later stateful-ONNX phase lands.
- The 24 kHz checkpoint and every derivative remain user-supplied and
  non-redistributable. The 48 kHz profile remains a separate artifact.

Large graphs, weights, and codebooks do not belong in Git history.

