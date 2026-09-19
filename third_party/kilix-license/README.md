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

The screen is `render_screen(record, texts, *, receipts, records=None)`.
It marks every bound text that is "changed since your last acceptance"
(SR-4): the licence text, a component exception, or a binding condition
whose text identity an earlier accept receipt accepted with other bytes.
`receipts` (the `ReceiptStore`) is required, so no consumer can render a
screen without the marker; the two-argument form is refused. The same
detection is `changed_texts(record, receipts, *, records=None)`, and
`scan_receipts(store)` lists the receipt files it used and skipped. A
binding condition's identity is its generated `text_id`, and a licence
text's identity is its record's generated `licence_text_id`. Both are keyed
by the document, not by record or binding id, so they hold across sibling
records (both Bonsai Image variants, both EnCodec checkpoints) and a
renamed binding or entry. The generator refuses an identity table that
gives two texts one identity or one text two identities. The marker never
grants coverage: `covers` and `require` do not read it. A receipt file that
cannot be parsed, or has an unknown schema, is skipped for the marker,
never fatal.

The scan and `require` read receipt files the same way. Each entry is
stat'ed before it is opened, so a FIFO, device or directory is never
opened. The open is `O_NONBLOCK | O_NOCTTY`, and the descriptor must be a
regular file of at most 1 MiB before one byte is read. Reads stop at
1 MiB + 1 byte whatever the file's size claims. An entry named like a
receipt that fails this is not a receipt: `require` refuses with
`CoverageRefused` and never waits on it.

Receipts written from LIC4 on also record, as context that is not bound
(OD-AQ), the statement and component-exception digests shown on the screen
and each binding's text identity; from LIC4-FIX on they also record the
licence text identity. Receipts without those keys, as written before LIC4
or LIC4-FIX, are still read and still cover; their identities are resolved
through the record their licence id names (pass `records=`).

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
