# kilix-encodec

`kilix-encodec` is the C11 provider boundary for Kilix EnCodec packet encoding
and decoding. The 0.1.4 tree builds the fail-closed provider skeleton plus
network-free development exporters for state-explicit 24 kHz streaming graphs
and the bounded 48 kHz stereo file profile. No model runtime, graph,
checkpoint, codebook, audio fixture, or weight payload is included.

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

## Export controls

Export dependencies are isolated in the locked `export` group. The exporter
accepts only the exact reviewed user-supplied checkpoint and refuses to write
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
identity at all 3/3 rates, decoder parity, deterministic epoch recovery, all
8/8 fixed-shape refusals and all 6/6 profile timing pipelines. The timing result
is labelled unfrozen-host measurement and receives accepted H1 credit 0/1.
Blind listening and pinned offline delivery remain 0/1 each.

### Blinded epoch-boundary trial

The verifier's `listening/` directory is input to a facilitator-operated,
paired forced-choice trial. Preparation copies randomized `A`/`B` pairs into a
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
harness. Blind listening and accepted H1 credit remain 0/1 each.

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
  non-redistributable. The 48 kHz safetensors input and every derivative remain
  scratch-only; publication is owner-reserved.

Large graphs, weights, and codebooks do not belong in Git history.
