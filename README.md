# kilix-encodec

`kilix-encodec` is the C11 provider boundary for Kilix EnCodec packet encoding
and decoding. The 0.1.1 tree builds the fail-closed provider skeleton and the
network-free development exporter for state-explicit 24 kHz ONNX graphs. No
model runtime, graph, checkpoint, codebook, or weight payload is included.

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

## Stateful export controls

Export dependencies are isolated in the locked `export` group. The exporter
accepts only the exact reviewed user-supplied checkpoint and refuses to write
inside the Git repository:

```sh
make export-env
make export-test \
  CHECKPOINT=/path/to/encodec_24khz-d7cc33bc.th \
  OUTPUT_DIR=/path/to/empty/scratch-directory
```

The scratch bundle contains graphs 4/4, a canonical manifest 1/1, a canonical
verification result 1/1 and synthetic listening fixtures 3/3. Verification
checks ONNX contracts, continuous-oracle parity, token identity, deterministic
epoch recovery, fixed-shape refusal and an H1-ready timing harness. The timing
result is labelled unfrozen-host measurement and receives accepted H1 credit
0/1. Blind listening, the 48 kHz profile and pinned offline delivery remain
0/1 each.

## Current boundary

- Input/output ownership follows `include/kilix_encodec.h`: callers own all
  packet and PCM storage; mutable encoder/decoder state is never shared.
- The native library performs 0 of 1 network operations and invokes 0 of 1
  Python runtimes.
- The repository contains 0 of 2 required model artifacts. Model loading
  returns `KENC_ERR_MODEL` until the later stateful-ONNX phase lands.
- The export tool performs 0 of 1 checkpoint downloads. It opens only the
  caller-supplied regular file, verifies its exact size and SHA-256, and uses
  PyTorch's restricted weights-only loader.
- The 24 kHz checkpoint and every derivative remain user-supplied and
  non-redistributable. The 48 kHz profile remains a separate artifact.

Large graphs, weights, and codebooks do not belong in Git history.
