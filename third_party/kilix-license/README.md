# kilix-license

Licence: MIT (OD-AV). LIC3 re-confirms the LICENSE file; remote creation is RC-13.

`kilix-license` is the single licence authority for Kilix (OD-AJ). It
owns licence records stored by text digest, the verbatim first-use
screen, agreement capture, digest-bound receipts, and binding-scoped
`covers` / `require`.

The public coverage check is `covers(record, receipt, *, manifest_digest)`.
The expected manifest is part of the binding (OD-AI); the two-argument
form is refused. OD-AI's record digest names this licence record, not an
asset/v3 record. Advisories, statements, and component entries without
exception text are context (OD-AQ) and are omitted from that digest.

This `0.1.0` LIC2 state generates licence records from the checked
determinations JSON (never retyped). The repository licence file is MIT
(OD-AV). LIC3 re-confirms that LICENSE file; the GitHub remote remains
RC-13.

## Publication status

The repository is **local-only**. It has no remote and is not authorised
for a push or for GitHub repository creation. See `PUBLICATION.md`.

Runtime code uses the Python standard library only.

## Development

The development environment is frozen to Python 3.12.8 and uv 0.12.5.

```sh
uv sync --frozen --python 3.12.8
make check
```

`make check` syncs the frozen environment, runs the test suite, and
builds installable artifacts.
