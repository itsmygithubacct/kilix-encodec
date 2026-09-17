# Changelog

## Unreleased

- Add the C5-R4 epoch-start profile (owner decision OD-AL): every 24 kHz epoch,
  and the stream, starts with a four-packet repeat pre-roll on both sides, in
  the Python streaming runtime and the native runtime. Graph bytes and the
  export manifest are unchanged.
- Mark the epoch-start profile and negotiate it (owner decision OD-AT). New
  encoders and decoders use C0, which is byte-identical to 3747330. Peers
  select C5-R4 per stream only after `kenc_epoch_start_negotiate` finds it on
  both sides. RESET packets of C5-R4 streams carry `KENC_PACKET_FLAG_EPOCH_PREROLL`,
  and a decoder refuses a mismatched marker with the new `KENC_ERR_EPOCH_START`.
  New API: `kenc_epoch_start_supported`, `kenc_epoch_start_negotiate`,
  `kenc_epoch_start_from_marker`, `kenc_epoch_start_name`,
  `kenc_encoder_set_epoch_start`, `kenc_decoder_set_epoch_start`.
- Bump the packet file format to version 2: C5-R4 files carry the marker at
  header byte 42, and C0 files stay version 1 so pre-marker readers still read
  them. New file API: `kenc_file_header_write_epoch_start`,
  `kenc_file_header_read_epoch_start`, `kenc_file_writer_create_epoch_start`,
  `kenc_file_reader_epoch_start`, `kenc_file_source_epoch_start`.
  `kenc encode` gains `--epoch-start C0|C5-R4`; `kenc decode` follows the file.
- Give `tools/epoch_stream.py` explicit profiles (default C0), markers,
  advertisements, negotiation and file-header reading. Product verification and
  `tools/bench_native.py` select C5-R4 explicitly.
- Hold C0 to 3747330: a recorded C0 render reference for the 14 programme items
  (`legacy-c0`), plus native and file-command comparisons against a 3747330
  build when `C0_REFERENCE_LIBRARY` and `C0_REFERENCE_COMMAND` are given.
- Pin the C5-R4 golden's lead-in length to a literal 4 and count the product's
  lead-in runs, so a changed runtime constant fails `verify_export` on its own.
- Pin the 24 kHz converter's encodec source to facebookresearch/encodec
  `2d29d935` (MIT), not PyPI 0.1.1. The eight graphs stay byte-identical to
  the 0.1.1 export; the manifest records the new lock and reports `0.1.2a3`,
  so the population digest is re-pinned in this repository. Catalogue re-pin
  is a later wave.
- Cite the pinned commit's MIT LICENSE by sha256 in THIRD-PARTY-NOTICES and
  the converter notice. Weights stay CC BY-NC 4.0 (OD-AR). Stop calling the
  48 kHz checkpoint or the 0.1.1 package MIT.
- Make `tools/consumer_grep.sh` fail loudly on a git error such as a bad ref.
- Replace the constant-cold-start post-reset golden with a successor that holds
  each epoch to the pre-roll checkpoint definition and refuses the cold start.
- Add epoch independence, added-latency and native syn-fixture parity controls,
  and hold the runtime to the checked F101 remedy programme's render identities,
  determinism and 3/12 kb/s level tables.

## 0.1.5 - 2026-08-31

- Bind performance verification to the frozen H1 q35/qemu64 fixture and refuse
  an H1 measurement unless its CPU, memory, root disk, OS, and exact runner
  identities match.
- Turn the 24 kHz p99 and sustained-real-time requirements and the 48 kHz
  decoder real-time requirement into executable release gates.
- Embed the verifier and capacity-check source identities in canonical results.

## 0.1.4 - 2026-08-31

- Export the complete 24 kHz 3/6/12 kb/s profile family as 8 of 8
  fixed-shape stateful and RVQ graphs.
- Verify exact oracle tokens, quantized latents, decoded waveforms, nested RVQ
  prefixes, shape refusal, and timing across all 3 of 3 bandwidths.
- Record the bandwidth-to-codebook mapping in both the committed asset policy
  skeleton and each scratch-only export manifest.

## 0.1.3 - 2026-08-31

- Add a scratch-only blinded epoch-boundary trial that separates public audio
  pairs from its private answer key and verifies every file identity at score
  time.
- Add canonical multi-listener responses, exact one-sided binomial
  measurements, tamper refusal, and an explicit measured-only result boundary.

## 0.1.2 - 2026-08-31

- Add the network-free 48 kHz stereo file-profile exporter using the exact
  pinned safetensors input and fixed-shape encoder/decoder ONNX graphs.
- Add native raw-codebook RVQ, all-bandwidth oracle parity, deterministic
  three-frame overlap-add, listening-fixture, and unfrozen timing controls.
- Keep the model input, graphs, raw codebooks, verification outputs, and audio
  fixtures outside Git; their publication remains owner-reserved.

## 0.1.1 - 2026-08-31

- Add the network-free, state-explicit 24 kHz encoder, decoder, and RVQ export
  path with exact user-supplied checkpoint verification.
- Keep generated graphs, listening fixtures, checkpoints, and weights outside
  Git and mark every derived artifact non-publishable without a separate grant.
- Add executable ONNX/oracle parity, deterministic epoch-recovery,
  fixed-packet refusal, reproducibility, and unfrozen-host benchmark controls.

## 0.1.0 - 2026-08-31

- Establish the independently buildable C11 provider skeleton.
- Add the fail-closed model, encoder, and decoder API boundary.
- Add 4 of 4 initial C test binaries and a skeleton-manifest validator.
- Record the distinct 24 kHz and 48 kHz artifact-license dispositions without
  publishing either artifact.
