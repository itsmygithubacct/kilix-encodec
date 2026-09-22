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

Most bound and context texts are the determinations JSON's own quoted
spans. One is not: the EnCodec `encodec-licence-history-note` advisory. The
determinations quote there is OD-AR's builder-facing description of the
screen, so `ADVISORY_TEXTS` binds the note itself instead - a text vendored
under `data/texts/` whose quoted lines are copied byte for byte from the
upstream licence history the L-ENC-R2 evidence packet pins. The generator
refuses a table entry no determinations quote uses, an entry that is not a
sha256, and a note that is missing or does not match its digest. An advisory
is not bound (OD-AQ), so the EnCodec record digests and every receipt's
coverage are unchanged by it.

The note tells the user that every line under a "quoted from" header is its
named source's bytes, so that promise is checked rather than asserted.
`ADVISORY_TEXT_SOURCES` declares, for each note, which file it quotes, that
file's sha256 and the exact lines taken; `parse_quoted_blocks` reads the
same three facts back out of the note's own headers, and the generator
refuses a note whose headers disagree with the declaration, including a
header that names one slice while quoting another. Each named source file is
pinned under `tests/data/note-sources/<sha256>`, and the suite re-derives
every quoted line from it and compares bytes, using the line numbers the
note itself claims. Adding or dropping a quoted block is a change to that
one table: nothing else names the note's sources.

The note's own lines - its preamble and each block's three header lines -
are the only text in it that upstream did not write, and they may not say
what a licence is. `states_a_licence` looks for a licence identifier or the
language of permission and relicensing, and the generator refuses a note
with such a line outside a quoted block. This is the rule the note exists
for: licence statements on a first-use screen come from upstream's bytes,
never from us. An authored line may still introduce, attribute and cite,
which is where the orientation on a quoted block comes from.

Each declared source also carries a second, independent witness where one
exists: the evidence packet's own record of the file, vendored beside it
under `tests/data/note-sources/packet-records/<source sha256>.json`. The
suite checks the declared digest and each quoted line against that record,
and checks that the record's own packet digest is the one the note shows
the user. That is a partial close, not a full one: the packet records no
copy of the audiocraft lines, so block 2 has one witness, and both
witnesses are in this tree. `tools/verify_note_sources.py --packets DIR`
re-derives both from the evidence packets themselves when they are to
hand; it takes the directory as an argument and no path is committed.

## Where receipts live

There is one receipt store root, and this authority names it (OD-AJ):
`receipt_store_root()`, which is `$GPU_TERMINAL_HOME/license-receipts` and
is overridden outright by `$KILIX_LICENSE_RECEIPTS`. Producers and
consumers reach it as `ReceiptStore.shared()`, which takes no path; a
caller that hands in a path gets it checked, and anything but the agreed
root raises `ReceiptStoreRootRefused` naming both. `ReceiptStore(root)` is
unchanged and still opens any directory, for fixtures and inspection.

With `$GPU_TERMINAL_HOME` unset the root is **`$HOME/.local/gpu_terminal/license-receipts`**.
That is the composition the rest of the stack already performs -
`voicelib.paths.gpu_terminal_home()` is `os.path.expanduser("~")`, and
`kilix/bootstrap.sh`, `kilix/build.sh` and `pleb/lib/common.sh` all read
`GPU_TERMINAL_HOME="${GPU_TERMINAL_HOME:-$HOME/.local/gpu_terminal}"` - so
the writer and the reader compose the same path with the variable set and
with it unset. It read the NSS passwd home (`pw_dir`) before, which ignores
`$HOME`; wherever the two differ - a sandbox, a service unit, `su` without
`-l` - consent was filed at one root while the gate refused at another,
which is exactly the symptom below. The suite's own live-store guard still
reads `pw_dir`, deliberately and for a different reason: the suite
redirects `$HOME` into a scratch directory, so a `$HOME`-based guard would
stop guarding the real store at the one moment it has to work. Both
spellings are refused by that guard.

This exists because the two sides each used to choose: an acceptance filed
by one component was invisible to another, and the symptom was a gate
refusing a licence the user had just accepted. A consumer that composes its
own path, or adds its own override variable, re-opens that.

Receipts written from LIC4 on also record, as context that is not bound
(OD-AQ), the statement and component-exception digests shown on the screen
and each binding's text identity; from LIC4-FIX on they also record the
licence text identity. Receipts without those keys, as written before LIC4
or LIC4-FIX, are still read and still cover; their identities are resolved
through the record their licence id names (pass `records=`).

From LIC6 a receipt also records an `acceptance` block, again as context
that is not bound. OD-BC settled its shape: exactly two fields, and no
identity. `captured_at` is the capturing machine's own UTC clock at
capture; `capture_mode` is `interactive-tty` when that process had a
terminal on both standard input and standard output, `no-tty` otherwise.
**No account name and no uid is recorded**, so nothing in a receipt
distinguishes two users of one machine - the accepted outcome of OD-BC,
not an oversight. A receipt carrying an identity field is refused, not
ignored.

### What a receipt proves, and what it does not

OD-BC: record, do not bind, in 0.2.2. A receipt is an unsigned JSON file in
the user's own store. It records that this authority captured an agreement
against a particular licence record and manifest, at a stated moment, with
or without a terminal.

**It does not prove that a human accepted anything. It does not say which
human. It does not prove that the recorded time is the real time.** Nothing
signs it, so a producer that wants to fabricate one can. Read the block as
the place to look when asking where a receipt came from, never as consent.

What it does buy: a receipt minted by a build step, an image build or an
unattended provisioning script records `no-tty`, because there is no
terminal there, and that is mechanically distinguishable from one minted at
a console - by inspection, and by
`require(..., captured_at_a_terminal=True)`. OD-BC keeps that lever
**available and off**; the default is unchanged and every earlier receipt
still covers.

### A receipt is never shipped

A receipt is **never shipped, vendored or provisioned**. The only producer
is the first-use flow, on the user's own machine, after that user has been
shown the licence and has accepted it (OD-S, OD-BB).

**Shipping a receipt is not an acceptable remedy for a gate that refuses.**
An image that carries one turns every install into consent nobody gave,
while the audit surface reads "gated" - which is the bypass OS-V-VERIFY F2
reported, wearing a compliant hat. The gate cannot tell the two apart, and
no flag in this repository can enforce this rule; it is a rule about what
is built.

The acceptable outcomes are exactly two: **acceptance at first use, or no
weights.**

If a build needs to attest something about licences, that attestation is a
**distinct schema** and never a `kilix.license.receipt/v1` (OD-BC, settling
OQ-C4).

### Compatibility

Receipts written before LIC6 carry no `acceptance` block, read exactly as
before, and cover exactly as before; they are refused only by a caller that
has opted into `captured_at_a_terminal=True`. Receipts written from LIC6 on
are **refused by pre-LIC6 readers**, which reject an unknown context key -
a fail-closed break, so every consumer pinning this authority must re-pin
before anything writes a LIC6 receipt.

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

`make check` syncs the frozen environment, runs the test suite, builds
installable artifacts, and reaches the advisory-note source check. Inside a
network sandbox run it as `make check OFFLINE=1`: `uv build` otherwise
resolves the PEP 517 build backend over the network, which uv.lock does not
pin, and the gate fails with a name-resolution error rather than a defect.

### Changing this file

This file's prose is pinned by digest in `tests/test_identity.py`
(whitespace-normalised, so re-wrapping is free and any change to a word is
not). OD-BC's shipping rule is a rule nothing here can enforce except by
being read, and such a rule is reversed just as easily by adding a sentence
elsewhere in the document as by editing the sentence itself - so the
document is constrained rather than screened for the wording of an
exception, which is the same choice the authored-note prose forced. Edit
the prose and update `README_NORMALISED_SHA256`; the failure prints the
value to put there.

### Checking an advisory note against the evidence packets

An advisory note's quoted source is pinned twice inside this tree - the
source file under `tests/data/note-sources/`, and the evidence packet's own
record of it beside it - so both can be forged together by one seat willing
to make the edits agree. Only the packets themselves settle it, and they
are not in this repository:

```sh
make notes PACKETS=<directory holding the licence-evidence-* packets>
```

**A release gate runs that, and it must be run before an advisory note's
digest is re-pinned anywhere downstream.** `make check` reaches it through
`notes-guarded`, which runs it whenever `PACKETS` is set and prints a
`notes: SKIPPED` block naming what was not checked when it is not - so a
gate that omits the second witness says so rather than passing quietly.

### Changing what this authority says on a consent screen

An advisory note has two kinds of line. Lines under a `quoted from` header
are re-derived from their named source byte for byte. Every other line -
the preamble, and each block's three header lines - is **authored**, and is
**pinned, not screened**: it must appear byte for byte in
`ADVISORY_NOTE_AUTHORED` in `src/kilix_license/generate.py`, keyed by the
note's sha256. Screening authored prose for licence vocabulary could not
work, because a false sentence can be written in plain English; the
constraint is equality with a declaration, so a new or altered authored
sentence is refused whatever it says.

The declaration is itself pinned, by a digest typed beside it in
`ADVISORY_NOTE_AUTHORED_PIN`. Without that, the declaration could be made to
derive from the note file - and then the note would be compared with itself,
the suite would stay green, and a new sentence would reach a consent screen
with nothing in the diff to read. The pin is typed and never computed, and
the suite requires the declaration to be a literal in the source, so a
computed declaration is refused rather than satisfied.

To change it deliberately, in one place:

1. edit the note under `src/kilix_license/data/texts/` and rename it to its
   new sha256;
2. copy its authored lines - in note order, blank lines omitted - into
   `ADVISORY_NOTE_AUTHORED` under the new digest, and put the declaration's
   own sha256 in `ADVISORY_NOTE_AUTHORED_PIN` beside it **and** in
   `AUTHORED_PIN` in `tests/test_records.py`, the suite's independently
   typed copy of the same figure (every refusal prints the value);
3. point `ADVISORY_TEXTS` and `ADVISORY_TEXT_SOURCES` at the new digest;
4. run `make regenerate`, which **writes** the records - `make records` is
   `--check` and refuses until they are written;
5. `git add -A`, because three tests read the committed tree and the renamed
   note is a new path, then run `make check`.

The generator's refusals state those five steps, including the two an editor
meets first: the digest mismatch before the rename, and the missing file just
after it. Widening a detector is not an alternative: a sentence absent from
that declaration never reaches a user.
