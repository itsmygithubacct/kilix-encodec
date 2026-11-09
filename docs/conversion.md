# Local conversion commands

Building the two converter commands, the kilix-license receipt gate and running a conversion. Moved from the README. Since this text was written, the Kilix Content catalog has gained the `encodec-24khz-stateful` and `encodec-48khz-frame` records, whose upstream-convert step runs the converter installed under `/usr/lib/plebian-os/kilix-encodec/` with `--receipt-store` and `--manifest-digest`; see the README's status line.

`tools/build_converter.py` builds a relocatable conversion command from the
unchanged export source and lock. Run it with system Python on Linux x86-64,
once per profile:

```sh
python3 tools/build_converter.py                    # 24 kHz stateful graphs
python3 tools/build_converter.py --profile 48khz    # 48 kHz stereo frame graphs
```

The default build acquires the exact UV 0.12.3 and CPython 3.12.8 tool archives
listed in `tools/converter-inputs.json`, then constructs a fresh CPU environment
from the frozen lock. The lock installs facebookresearch/encodec
`2d29d9353c2ff0ab1aeadc6a3d439854ee77da3e` (MIT), not PyPI 0.1.1. It builds
that EnCodec sdist only after the locked wheels supply setuptools. No model
input is acquired. Tool archives and dependency
artifacts use a private cache; `--offline` requires that cache to be populated.
`--cache-dir` selects another private cache, `--output-root` selects an existing
destination, and `--timeout` bounds the entire build, up to 3,600 seconds.
An explicit development environment can be selected only with all three
`--environment`, `--python` and `--uv` paths. That route records the selected
bytes and the exact package population; it does not acquire a new environment.

| profile | command | runtime directory | upstream input | output population |
| --- | --- | --- | --- | --- |
| `24khz` | `bin/kilix-encodec-convert-24khz` | `.converter` | `encodec_24khz-d7cc33bc.th` from `dl.fbaipublicfiles.com` | 8 stateful graphs and `manifest.json` (`op17-v2-bb615145`) |
| `48khz` | `bin/kilix-encodec-convert-48khz` | `.converter-48khz` | `model.safetensors`, `config.json`, `preprocessor_config.json` from `facebook/encodec_48khz` at `c3def8e7185ac8c8efdce6eb8c4a651e487a503e` | 2 frame graphs, `rvq-codebooks.f32le` and `manifest.json` (`op17-v1-2ce5225d`) |

Each output consists of the command, its adjacent runtime directory holding
`runtime.tar`, and a build receipt. Keep these together when relocating them.
The receipt binds the profile, the exporter, lock, tools, package population,
every runtime member, notices, the pinned licence authority, builder and
generated command. It is build evidence;
F100 installed-asset and source-supply authority are separate. Existing output
entries are never overwritten. The dedicated build process owns and reaps its
children before removing its private staging directories, including children
that create a new session. Inputs and destination directory identities are
checked while building; symlink or shared directory chains refuse.
Generated package `RECORD` indexes are omitted because they include temporary
environment command wrappers which are not shipped. Dependency code, version
metadata and license notices are retained and bound in the runtime receipt.

## Licence receipt gate

Both checkpoints and every derivative are CC BY-NC 4.0, licensor Meta
Platforms (owner decision OD-AR). The licence authority is kilix-license
(OD-AJ): its records, first-use screen, agreement and receipts decide, and the
converter carries no terms text of its own. `third_party/kilix-license/` is a
`git archive` of kilix-license at the commit in `third_party/kilix-license.pin`.
The builder verifies every vendored file against `tools/converter-inputs.json`
and embeds the kilix-license modules and the profile's licence record in the
command, so nothing is imported from disk at run time:

| profile | licence record | record digest |
| --- | --- | --- |
| `24khz` | `encodec-24khz-stateful` | `8af1dc699df34436899f0b93dce271d2fadb119f62e136e74ad797686d880f4b` |
| `48khz` | `encodec-48khz-frame` | `9c1baee4ad48816bba2568c7e14f136b388f11722d6e422a933a1a4a24d30f3f` |

Before it reads any input, runtime or output, the command calls kilix-license
`require()` for that record and the asset manifest digest it is given. It
refuses unless the receipt store holds a receipt covering the whole binding:
record digest, manifest digest, licence id, licence text digest, decision,
licensor and the agreement-required non-commercial condition. A missing
receipt, a receipt for another manifest or record, or a planted receipt whose
licence text digest or any other bound field changed is refused. The receipt
store must be an existing private directory of the current user; the command
only reads it. An absent or empty `--receipt-store`, and an absent or empty
`--manifest-digest`, are refused by the same gate, with exit status 1, before
any input is read.

Whatever stands at a receipt's file name is decided by the same gate. A FIFO,
device, directory, oversized or unreadable entry is not a receipt: the vendored
kilix-license refuses it without opening or waiting on it. A readable but
malformed one is refused as well, whatever its parse raises. Every failure of
the licence authority is this gate's refusal, so no conversion begins on one,
and a covering receipt beside such an entry still converts.

## Running a conversion

`kilix models install` is to download the pinned upstream bytes on first use,
after the licence screen has recorded a receipt, and then run the command.
That installer path is not here yet: it arrives with C4 (kilix-content) and
KX (the kilix installer). kilix-content's upstream-convert argv does not yet
pass `--receipt-store` or `--manifest-digest`. Until then the command is run
directly, as below. The input is never a user-selected file: the command
accepts only the exact pinned sizes and SHA-256 digests above.

```sh
bin/kilix-encodec-convert-24khz \
  --input /absolute/path/to/encodec_24khz-d7cc33bc.th \
  --output /absolute/path/to/empty-output \
  --receipt-store /absolute/path/to/kilix-license-receipts \
  --manifest-digest ASSET_MANIFEST_SHA256 \
  --timeout 180

bin/kilix-encodec-convert-48khz \
  --input /absolute/path/to/directory-with-the-three-model-files \
  --output /absolute/path/to/empty-output \
  --receipt-store /absolute/path/to/kilix-license-receipts \
  --manifest-digest ASSET_MANIFEST_SHA256 \
  --timeout 300
```

`--manifest-digest` is the asset record's manifest digest, the value the
licence screen bound into the receipt. The output directory must exist, be
empty, be owned by the current user and have mode 0700. The command seals
every input and its complete runtime before execution, runs the unchanged
exporter in a private process/network/mount namespace (the restricted
weights-only loader for 24 kHz, safetensors only for 48 kHz), and checks every
output file against the bound population. The bounded temporary runtime is
read-only before the selected interpreter starts; the trusted bootstrap
installs hard resource limits first. Two CPU threads are selected.
Cancellation and deadlines tear down the owned process tree. A failed attempt
can leave partial files in its output directory for the caller to inspect or
discard; a new conversion requires an empty output.

The output holds exactly the bound population and no notice. The licence
texts are kilix-license's; presenting them with an installed model is left to
the installer path (C4 and KX above), which is not here yet. Neither this tool
nor its output grants redistribution, creates a source-supply decision, or
admits an installed model. Checkpoints, runtime archives and generated graphs
must not be committed to this repository. The command needs system Python,
bubblewrap, user namespaces and Linux memfd seals. The development runtime
occupies roughly 1.1 GiB on disk and additional temporary memory while
converting; this is not a fitted device profile.

`make test-converter` runs bounded file, cancellation, process ownership,
output-publication and receipt-gate controls without model payloads or network
access. Real conversion, reproducible builds and installed admission require
their separate exact inputs and evidence.
