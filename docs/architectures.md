# Native package architecture selection

Selecting AMD64 or ARM64 for the native package and the converters. Moved from the README.

`tools/build_native_package.py --architecture arm64` requires native Debian
ARM64 and a committed `tools/debian-dependencies-arm64.json` in the selected
source. The existing default selects AMD64 and its unchanged dependency lock.
Each architecture uses its own package identities and multiarch library path;
no lock is inferred from the other architecture. Until exact patched ARM64 ORT
packages and their dependency lock are qualified, the ARM64 build refuses.
Content admission and model authorization remain required separately.

`tools/build_converter.py --architecture arm64 --profile 24khz` (or `48khz`)
selects `tools/converter-inputs-arm64.json` and requires a native ARM64 host.
The binding pins ARM64 Python and uv bytes and the frozen lock's ARM64 package
population, including `torchaudio==2.6.0`. The build receipt records the
architecture and binding digest. The profile outputs and license authority
are unchanged; building the conversion software does not authorize models.
