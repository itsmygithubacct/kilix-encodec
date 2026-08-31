# Changelog

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
