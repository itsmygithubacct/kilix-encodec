# Third-party notices

This repository's source and documentation are project-authored and licensed
under the root MIT license. No third-party source, generated graph, checkpoint,
codebook, audio sample, or model weight is vendored in this repository.

The development export group uses ONNX, ONNX Runtime, PyTorch, torchaudio,
NumPy, EnCodec, einops, Transformers, and safetensors under their respective
terms. They are locked build tools and are not bundled into the native library.
The optional native backend links separately installed ONNX Runtime and OpenSSL
libraries under their respective terms. Their binaries and source are not
vendored here. Graph-contract metadata lists identities and tensor interfaces;
it contains no learned model parameters.

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

An optional `CONTENT=1` build embeds the explicitly selected kilix-content
source package under that package's MIT license. The sealed ZIP contains its
complete `LICENSE` as `licenses/kilix-content.txt`; the generated build receipt
records the exact source commit and every bundled file hash. This optional
runtime uses the separately installed system Python and standard library.
Neither that interpreter nor any model payload is embedded by this build.
