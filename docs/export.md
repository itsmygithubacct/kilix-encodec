# Export controls

Exporting and verifying the 24 kHz and 48 kHz graph bundles from caller-supplied checkpoints, the F101 programme check and the blinded listening trial. Moved from the README.

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

## Blinded epoch-boundary trial

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
