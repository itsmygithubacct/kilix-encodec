# Current boundary and installed admission

What the library does and does not do, the model licence, sealed-descriptor loading, and admission of models installed through Kilix Content (`CONTENT=1`). Moved from the README.

## Current boundary

- Input/output ownership follows `include/kilix_encodec.h`: callers own all
  packet and PCM storage; mutable encoder/decoder state is never shared.
- The native library performs 0 of 1 network operations and invokes 0 of 1
  Python runtimes.
- The repository contains no model artifacts. Without `ONNX=1`, model loading
  returns `KENC_ERR_MODEL`. Native builds require the exact caller-supplied
  bundle for the chosen profile; they do not claim it is release-qualified.
- The export tool performs 0 of 1 checkpoint downloads. It opens only the
  caller-supplied regular file, verifies its exact size and SHA-256, and uses
  PyTorch's restricted weights-only loader.
- Both checkpoints and every derivative are CC BY-NC 4.0, licensor Meta
  Platforms (OD-AR). Their delivery is upstream-convert: the exact bytes of
  their upstream pins are converted on the user's machine only, under a
  covering kilix-license receipt (see "Local conversion commands"). Fetching
  those bytes on first use is left to the installer path, which is not here
  yet (see "Running a conversion"); until then the caller supplies the pinned
  download. Derived graphs are produced only on the user's machine and
  never published (OD-AR, OD-S); nothing is redistributed. Both export
  manifests record that licence as `license` (`CC-BY-NC-4.0`,
  `Meta Platforms`). The 24 kHz manifest records `checkpoint_delivery`
  `upstream-convert` and `derived_graph_publication`
  `user-machine-only-never-published` in place of the superseded 0.2.1
  user-supplied and no-grant strings; its graphs did not change. The 48 kHz
  manifest keeps its earlier wording of the same policy,
  `derived_artifact_publication` `owner-reserved`: the owner has decided, and
  the decision is that derived graphs are never published.

Large graphs, weights, and codebooks do not belong in Git history.

The public `kenc_packet_metadata_read` parser exposes verified epoch, index,
PTS, flags and sample count without model loading. It validates the complete
opaque KMA2 packet against explicit options; only a decoder context checks
continuity against prior packets. A refusal leaves metadata unchanged. This
separate structure preserves the existing decoder-output ABI and keeps wire
parsing inside the codec library for file/live/KMX consumers.

Installed consumers can use `kenc_model_load_fds`, `kenc_stereo_create_fds`
and `kenc_file_source_create_fds` with the complete named set of read-only,
sealed Linux descriptors obtained from their receipt adapter. Each descriptor
must be caller-owned and CLOEXEC, with all four write/grow/shrink/seal seals.
The library checks the same exact manifest and graph sizes and hashes before
ORT parses any graph. It preserves borrowed offsets, never closes those FDs,
and retains its own bytes after loading. Extra, duplicate or missing names
refuse. The unused file profile's descriptor set can be null.

These native interfaces verify model bytes. The installed consumer still must
obtain current packaged catalog and license-receipt authority through
`kenc_installed_assets_open`; a path or sealed descriptor alone is not model admission.
Existing directory loaders remain available for explicit development tools.
`make ONNX=1 test-asset-fds MODEL_DIR=... STEREO_MODEL_DIR=...` checks sealed
input refusal, byte-for-byte directory-loader parity and post-close lifetime
for both profiles and the shared file decoder. It does not qualify a consumer
or transfer timing/listening results to a new release candidate.

Installed admission is an additional explicit build option:

```sh
make ONNX=1 CONTENT=1 CONTENT_SOURCE=/path/to/kilix-content \
    CONTENT_COMMIT=FULL_40_CHARACTER_COMMIT
```

The build reads that exact content Git archive, includes its license, and
embeds a deterministic ZIP containing the complete authority package, packaged
catalog, the content commit's vendored kilix-license (modules, the two EnCodec
licence records and its licence) and this provider's admission helper. The
content commit must pin the same kilix-license commit as
`third_party/kilix-license.pin`, and every embedded kilix-license file must
hash to its `licence_authority.files` digest, or the build refuses. `content_bundle.receipt.json`
records every source hash, exact content commit and ZIP digest. No package or
catalog is taken from the process's Python path. The final source closure must
bind both this provider and the chosen content source; rebuilding against a
new catalog produces a new binary and build receipt. Model payloads are never
part of this bundle. `CONTENT=0` is development-only byte/path functionality;
it refuses the installed admission API.

The packager reads raw commit, tree and blob objects and recomputes their Git
identities. Git replacement refs, archive attributes and ambient Git routing
cannot substitute or omit package members. The version-2 build receipt records
the content tree and every selected source blob, alongside the deterministic
ZIP and file digests. Missing, oversized or unsupported objects refuse before
any output is emitted.

`kilix_encodec_content.h` exposes `kenc_installed_assets_open`. Each call runs
the embedded ZIP from a read-only sealed memory descriptor with isolated system
Python, a minimal environment and hard resource ceilings. The caller supplies
an absolute content storage root and a 1–120000 ms deadline. The helper
(`python/installed_assets.py`) asks the asset/v3 and kilix-license authorities
on every call, with no readiness cache:

- the catalogue is `kilix_content.verified_packaged_catalog()`, parsed from the
  one read of its bytes that matched the content component's pin, and its entry
  must equal the exact F101 IDs, versions, graph population and compatibility
  compiled here, name exactly one licence, and name this bundle's own
  kilix-license record for the asset;
- the licence is `kilix_license.coverage.require()` over the store
  `kilix_license.receipt_store_root()` names, for the record digest and the
  manifest digest. The store is only read; admission never writes a receipt.
  A symlinked store directory is followed, as the receipt writer follows it;
  the directory reached must be the caller's and private to them;
- the installed tree is the directory `Installer.asset_destination()` names and
  must equal the manifest exactly. It is walked by descriptor. The content root
  and everything below it are opened without following symlinks and must be
  the caller's and not writable by others. A symlinked ancestor of the root,
  such as a `~/.local` that points at another disk, is followed, as the
  installer followed it: every directory the walk passes through, on both
  sides of a link, must not be writable by others unless sticky, a link in a
  sticky directory must be the caller's or that directory's owner's, and at
  most 40 links are followed. Each member is copied once into a sealed memory
  file while it is hashed, and the tree, the members and the covering receipt
  are checked again afterwards. asset/v3 has no installed-snapshot API, so this
  snapshot is the helper's own.

All declared notices are verified too; only the native 9/4 graph members are
returned. Every returned descriptor is rehashed by the helper, then subject to
the native loader's unchanged compiled hash/ORT checks. The helper also refuses
if any authority module was imported from anywhere but its sealed ZIP.

The C caller accepts one bounded, credential-bound descriptor record only
after the owned helper exits successfully, closes all received FDs on refusal,
and kills/reaps only its own helper on cancellation or deadline. No arbitrary
helper/interpreter path, ambient loader/Python import configuration, caller
catalog, receipt decision, download, converter or path-only admission is used.
Only `KILIX_LICENSE_RECEIPTS`, `GPU_TERMINAL_HOME` and `HOME` cross into the
helper besides a fixed `PATH` and locale: they are exactly what
`receipt_store_root()` reads, so the helper looks for receipts where the licence
screen filed them. Each crosses exactly as the caller has it, set or unset and
empty included: an empty `HOME` means `/` to kilix-license, so the helper then
looks under `/.local/gpu_terminal/license-receipts`, as the writer does.
`XDG_STATE_HOME` no longer selects anything.

A caller that is PID 1 of its PID namespace (a container entrypoint without an
init, or `unshare -pf app`) is never admitted, because the helper does not
accept PID 1 as its parent. `kenc_installed_assets_open` then returns
`KENC_ERR_RUNTIME` at once, without starting the helper, as it does when the
deadline passes or the call is cancelled: admission did not run, and no model
or licence was judged. `KENC_ERR_MODEL` stays the answer for a refused
catalogue entry, receipt or installed tree, and for a helper that failed. Run
such a consumer under an init, or call admission from a child process. A successful asset object owns
its FDs until `kenc_installed_assets_free`; model contexts retain their own bytes.

`make test-content-python CONTENT_SOURCE=/path/to/kilix-content` checks the
real asset/v3 catalogue and kilix-license receipt code with clearly synthetic
graph identities and catalog data, plants each refusal (no receipt, a wrong
receipt, a tampered tree, a tampered catalogue), and runs
`kenc_installed_assets_open` end to end from a fixture content commit bundled
by `tools/build_content_bundle.py`. `make test-content-ipc` checks C descriptor framing, privacy, limits,
cancellation and cleanup with an explicitly synthetic embedded peer. These
fixtures provide no actual model admission or release qualification credit.
