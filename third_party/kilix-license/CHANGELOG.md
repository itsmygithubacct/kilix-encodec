# Changelog

All notable changes to `kilix-license` are recorded here.

## Unreleased

- Generated licence records from the pinned determinations R2 JSON
  (each record cites that sha256; the generator refuses a hand-edited
  record).
- Established the local-only repository identity with MIT licensing
  (LIC3 re-confirms the licence file).
- Pinned Python 3.12.8 and uv 0.12.5 for the empty runtime dependency
  closure.
- Added a hermetic harness: fixture records, FakeStore in a temp dir,
  and a guard that refuses the live receipt store.
- Added a tracked-tree weight guard (suffix, magic, catalog-digest list).
- Added the licence-record schema, verbatim screen, agreement capture,
  and digest-bound atomic receipts.
- Added binding-scoped covers() and require(); catalogue-digest binding
  is refused.
- Record digest omits advisory, statement, and non-exception component
  hashes (OD-AQ); covers() requires the expected manifest.
- Agreements carry the record and binding digests shown; accept receipts
  require the typed line.
- Live-store guard resolves dir_fd via /proc/self/fd and wraps truncate,
  chmod, utime, and rmdir.
- OpenFst magic is d6 fd b2 7e and .fst is a weight suffix.
- Receipt writes use a unique temp name and remove stale temps under the
  store lock so a retry after crash succeeds.
- LOCAL_ONLY remote checks allow a clone-created origin.
