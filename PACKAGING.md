# Shared native package

Run `python3 tools/build_native_package.py --source /absolute/native-source
--commit FULL_NATIVE_SHA --content-source /absolute/content-source
--content-commit FULL_CONTENT_SHA --output /private/new-package` with the
arguments on one command line. The output's existing parent must be private
and owned; no existing output entry is replaced. The maximum deadline is
300 seconds. Failures retain a separately named `.native-package-*` attempt.

The recipe verifies raw Git commit/tree/blob identities and snapshots the
source before building. It uses the existing `make ONNX=1 CONTENT=1` and
`make PREFIX=/usr DESTDIR=... install`, records compiler bytes/flags, verifies
the installed library's embedded Content identity, and creates
`libkilix-encodec.deb`. It never installs that package or runs `ldconfig` on the
build host. The package's maintainer scripts update the loader cache only when
an operator installs/removes it through Debian package management. Amp and KMX
link this one library through `pkg-config kilix-encodec`.

Provision the exact Debian amd64 dependencies in `tools/debian-dependencies.json`
from snapshot `20260727T000000Z` (including its security archive). The package
does not fetch dependencies. An existing directory of the ten recorded ORT
`.deb` files can be supplied with `--deb-directory /absolute/cached-packages`:
every archive's bytes and package identity are checked before extraction into
the private attempt. System compiler/crypto/resampler/base packages are still
checked against their selected installed versions. This development build
mode changes no host package and copies no ORT payload into the output package.

The build is exact; the installed package is not. `Depends:` names
`libonnxruntime1.21`, `libssl3t64` and `libc6` at the versions built against as
**minimums**, because Debian delivers their security fixes as new versions: an
exact pin would make dpkg refuse the package on an already patched machine and
would hold libc6 and OpenSSL security updates back where it is installed. The
record (`kilix.encodec.native-package/v2`) names every library the loader
resolves by the Debian package that ships it and the version built against, and
keeps the build's bytes only as provenance; an installed system checks each
library against its owner's own dpkg checksums at an owner version no older
than that.

The package carries `native-package.json`, the embedded Content build receipt,
native and third-party notices, the Content license, dependency identities and
a deterministic archive of the exact native source. No checkpoint, graph,
converter environment or model receipt is included. The final release supplies
both commits; an older Content catalog does not receive newer admission credit.
The output package directory is a staging image with a `/usr` prefix, not a
relocated production installation. For private build tests, use
`pkg-config --define-prefix` and an explicit private loader path; verify both
consumers resolve the same `.so.0`. Root-owned runtime package installation and
final release source selection remain separate steps.

## Raw source capture

The original package deadline and SIGINT/SIGTERM cancellation also cover the
first commit, every tree and every blob read. Git output is polled and byte
bounded; EOF alone is not treated as process exit. Refusal kills and reaps the
Git process, and the dedicated native CLI reaps its owned escaped descendants
before returning. Raw object identity, offline selection and archive modes
remain verified. The reusable bundle helper does not acquire process-wide
reaping authority when called without the dedicated CLI cleanup callback.
