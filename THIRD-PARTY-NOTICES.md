# Third-party notices

This repository's source and documentation are project-authored and licensed
under the root MIT license. Apart from the kilix-license source described
below, no third-party source, generated graph, checkpoint, codebook, audio
sample, or model weight is vendored in this repository.

kilix-license: `third_party/kilix-license/` is a `git archive` of the
kilix-license repository at the commit in `third_party/kilix-license.pin`,
under its MIT license (`third_party/kilix-license/LICENSE`, Copyright (c) 2026
Kilix License contributors). It is the licence authority (owner decision
OD-AJ): the conversion commands embed its modules and one licence record, and
ship its LICENSE as `notices/kilix-license-LICENSE` in their runtime archive.
Its licence texts are stored by SHA-256 under `data/texts/`.

The development export group uses ONNX, ONNX Runtime, PyTorch, torchaudio,
NumPy, EnCodec, einops, Transformers, and safetensors under their respective
terms. They are locked build tools and are not bundled into the native library.
The optional native backend links separately installed ONNX Runtime and OpenSSL
libraries under their respective terms. Their binaries and source are not
vendored here. Graph-contract metadata lists identities and tensor interfaces;
it contains no learned model parameters.

The Meta EnCodec project is reference/export input only.

Code: the 24 kHz and 48 kHz converters install facebookresearch/encodec at commit
2d29d9353c2ff0ab1aeadc6a3d439854ee77da3e (MIT). That commit is after the
relicensing 349b72939f57cb3bc7b60906c0ee8228c849485d and is the earliest
usable post-relicensing revision (349b7293's setup.py never closes
classifiers). Source archive sha256
47d071d2d90f3d107ed83c930c4c189a066f92d299371ff04b6ede573c7cf434.
The pinned commit's LICENSE sha256 is
cf9b17822d1fcd4ff32ccbe14183386fb3adf6f2ff92dc184130823f7fc28173.
PyPI encodec 0.1.1 is not used. That release's METADATA declared CC BY-NC 4.0
and predated the MIT relicensing. Verbatim MIT LICENSE of the pinned commit:

MIT License

Copyright (c) Meta Platforms, Inc. and affiliates.

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

Weights: both the required 24 kHz causal checkpoint
(encodec_24khz-d7cc33bc.th) and the 48 kHz stereo checkpoint
(facebook/encodec_48khz at c3def8e7185ac8c8efdce6eb8c4a651e487a503e) are
CC BY-NC 4.0, licensor Meta Platforms (owner decision OD-AR). A
publisher-declared MIT tag on a third-party 48 kHz model card is not the
determination. Derived graphs and codebooks inherit CC BY-NC 4.0. None may
be committed or published from this repository without its own recorded
license and provenance decision. The converters download nothing, redistribute
no weights, and run only under a kilix-license receipt that covers the
checkpoint's licence record binding; the licence screen and notices are
kilix-license's, not the converters'.

An optional `CONTENT=1` build embeds the explicitly selected kilix-content
source package under that package's MIT license. The sealed ZIP contains its
complete `LICENSE` as `licenses/kilix-content.txt`; the generated build receipt
records the exact source commit and every bundled file hash. This optional
runtime uses the separately installed system Python and standard library.
Neither that interpreter nor any model payload is embedded by this build.
