# Third-party notices

This repository's source and documentation are project-authored and licensed
under the root MIT license. No third-party source, generated graph, checkpoint,
codebook, audio sample, or model weight is vendored in this repository.

The future runtime is designed to use ONNX Runtime. ONNX Runtime is not bundled
in this P1 skeleton and remains separately packaged under its own terms.

The Meta EnCodec project is reference/export input only. Its project code is
MIT-licensed upstream. That code license is not a license for every model
artifact:

- the required 24 kHz causal checkpoint has no affirmative redistribution
  grant in the reviewed source and remains fail-closed, non-redistributable,
  and user-supplied;
- the separately required 48 kHz stereo checkpoint has publisher-declared MIT
  metadata, but it is not present in this repository.

Derived graphs and codebooks are model artifacts, not project code. None may
be committed or published from this repository without its own recorded
license and provenance decision.

