# Test fixtures

P1 contains no model or audio fixtures. Later golden fixtures must carry exact
provenance, license disposition, expected identity, and bounded size before
they enter this directory.

`f101-c5r4-programme.json` (about 32 KB) holds expected figures only: sha256
identities of codes and float32 PCM renderings and dB level-table values for
the F101 remedy programme's 14 items under the repeat pre-roll epoch start. It
contains no audio, graph or weight bytes. Its `provenance` member records the
sha256 of every record it was derived from: the remedy spike's programme
manifest and item records, and the outside check's item records and summary.
The programme's synthetic items carry no third-party rights; the five recorded
excerpts are public domain or CC0, and only hashes and levels derived from
them appear here. `tools/verify_epoch_programme.py` checks the file's schema,
its 14 distinct items and its 8 programme types before use.

`f101-c0-3747330-reference.json` holds expected figures only: sha256 identities
of codes and float32 PCM for the same 14 programme items under the C0 epoch
start, at 6, 3 and 12 kb/s. `tests/fixtures/build_c0_reference.py` generated it
from a git archive of kilix-encodec `3747330` whose Git tree it recomputes.
3747330's own `tools/verify_export.py` streaming functions did the rendering, on
the pinned `02201a5a` graph population. The file records the reference commit
and tree, the programme manifest sha256, the NumPy and ONNX Runtime versions,
and a cross-check against the F101 remedy spike's own C0 arm at 6 kb/s. It
contains no audio, graph or weight bytes. `tools/verify_epoch_programme.py`
binds it to the same programme items and WAV hashes before use.
