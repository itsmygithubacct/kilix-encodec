"""Changed-text detection: "changed since your last acceptance" (SR-4, OD-AH).

A bound text is a record's licence text, a component exception text, or an
agreement-required binding-condition text. Each has a text identity:

  text:<licence_text_id>            a licence text whose record declares one
  licence:<record id>               a licence text whose record declares none
  component:<component id>          that component's exception text
  text:<text_id>                    a binding condition that declares text_id
  binding:<record id>/<binding id>  a binding condition that declares none

text_id and licence_text_id are keyed by the text, not by record id or
binding id, so sibling records that show one text share it (both Bonsai Image
variants show one policy and one licence), and a renamed binding keeps it
(F2, LIC4-VERIFY LIC4-3).

A text shown now is marked changed when an earlier valid accept receipt
accepted a text of the same identity and no accepted digest for that identity
equals the digest shown now. Receipts carry no time, so "last acceptance"
means any earlier acceptance: a text the user has accepted anywhere is not
new to them.

The marker is presentation only. It never grants or withdraws coverage.
covers() and require() never read it and decide coverage exactly as before.
Receipt files that cannot be used for the marker are skipped, never fatal
(C2E-VERIFY F1): not a regular file, unreadable, too large, not UTF-8 JSON,
an unknown schema, or a foreign shape. A non-regular target (a character
device behind a symlink, a FIFO) is never opened. An entry swapped for one
between the check and the open cannot block the scan, is never read, and
cannot become the controlling terminal (C2E-FIX2-VERIFY T1, T2; LIC4-VERIFY
LIC4-1, LIC4-6). No entry is read past MAX_RECEIPT_BYTES + 1 bytes, whatever
its size claims (LIC4-2).
"""

from __future__ import annotations

from dataclasses import dataclass
import os

from kilix_license.receipts import Receipt, parse_receipt_bytes
from kilix_license.records import BindingCondition, LicenseRecord, RecordIndex
# MAX_RECEIPT_BYTES stays importable from here (LIC4); the reader lives in store.
from kilix_license.store import MAX_RECEIPT_BYTES, ReceiptStore, read_receipt_file  # noqa: F401

CHANGED_HEADER = "=== changed since your last acceptance ==="


@dataclass(frozen=True)
class ChangedText:
    """One bound text shown now whose identity was accepted with other bytes."""

    section: str
    identity: str
    shown_sha256: str
    accepted_sha256: tuple[str, ...]
    accepted_under: tuple[str, ...]


@dataclass(frozen=True)
class ReceiptScan:
    """Receipts usable for the marker, and the file names that were skipped."""

    receipts: tuple[Receipt, ...]
    skipped: tuple[str, ...]


def binding_identity(record_id: str, condition: BindingCondition) -> str:
    if condition.text_id is not None:
        return f"text:{condition.text_id}"
    return f"binding:{record_id}/{condition.id}"


def licence_identity(record: LicenseRecord) -> str:
    if record.licence_text_id is not None:
        return f"text:{record.licence_text_id}"
    return f"licence:{record.id}"


def bound_texts(record: LicenseRecord) -> dict[str, tuple[str, str]]:
    """Screen section label -> (text identity, sha256) for every bound text of record."""
    found = {f"licence:{record.id}": (licence_identity(record), record.text_sha256)}
    for component in record.components:
        if component.exception_text_sha256 is not None:
            found[f"component:{component.id}"] = (
                f"component:{component.id}",
                component.exception_text_sha256,
            )
    for condition in record.binding_conditions:
        if condition.agreement_required:
            found[f"binding:{condition.id}"] = (
                binding_identity(record.id, condition),
                condition.text_sha256,
            )
    return found


def _read_small_regular_file(path: os.PathLike[str] | str) -> bytes | None:
    """File bytes, or None for anything the marker must not read or wait on.

    store.read_receipt_file does the reading, with the same discipline
    require() uses (T1, T2, LIC4-7): stat before the open, O_NONBLOCK |
    O_NOCTTY, the descriptor must fstat as a regular file before one byte is
    read, and at most MAX_RECEIPT_BYTES + 1 bytes are read. None means "skip".
    """
    return read_receipt_file(path)


def scan_receipts(store: ReceiptStore) -> ReceiptScan:
    """Every parseable receipt in store; every other *.json is skipped, never raised."""
    receipts: list[Receipt] = []
    skipped: list[str] = []
    try:
        paths = sorted(store.root.glob("*.json"))
    except OSError:
        return ReceiptScan((), (str(store.root),))
    for path in paths:
        if path.name.startswith("."):
            continue
        data = _read_small_regular_file(path)
        if data is None:
            skipped.append(path.name)
            continue
        try:
            receipts.append(parse_receipt_bytes(data))
        except Exception:  # noqa: BLE001 - any unusable file is skipped for the marker (F1)
            skipped.append(path.name)
    return ReceiptScan(tuple(receipts), tuple(skipped))


def _record_for(
    record_id: str,
    presented: LicenseRecord,
    records: RecordIndex | None,
) -> LicenseRecord | None:
    if presented.id == record_id:
        return presented
    if records is None:
        return None
    try:
        return records.by_id(record_id)
    except KeyError:
        return None


def _record_by_digest(
    digest: str,
    presented: LicenseRecord,
    records: RecordIndex | None,
) -> LicenseRecord | None:
    if presented.digest == digest:
        return presented
    if records is None:
        return None
    try:
        return records.by_digest(digest)
    except KeyError:
        return None


def accepted_texts(
    receipt: Receipt,
    *,
    presented: LicenseRecord,
    records: RecordIndex | None = None,
) -> set[tuple[str, str]]:
    """(text identity, sha256) pairs an accept receipt accepted."""
    licence_record = _record_for(receipt.licence_id, presented, records)
    if receipt.licence_text_id is not None:
        licence = f"text:{receipt.licence_text_id}"
    elif licence_record is not None:
        # A receipt written before LIC4-FIX names no licence text identity:
        # resolve it through the record that receipt's licence id names today.
        licence = licence_identity(licence_record)
    else:
        licence = f"licence:{receipt.licence_id}"
    accepted = {(licence, receipt.licence_text_digest)}
    for component_id, digest in (receipt.component_exception_digests or {}).items():
        accepted.add((f"component:{component_id}", digest))
    declared = receipt.binding_text_ids or {}
    for binding_id, digest in receipt.binding_condition_text_digests.items():
        if binding_id in declared:
            identity = f"text:{declared[binding_id]}"
        else:
            # A receipt written before LIC4 names no identity: resolve it
            # through the record that receipt's licence id names today.
            identity = f"binding:{receipt.licence_id}/{binding_id}"
            if licence_record is not None:
                for condition in licence_record.binding_conditions:
                    if condition.id == binding_id:
                        identity = binding_identity(licence_record.id, condition)
                        break
        accepted.add((identity, digest))
    # The record digest binds every bound text (OD-AI). A receipt for a record
    # still in view accepted exactly that record's bound texts.
    exact = _record_by_digest(receipt.record_digest, presented, records)
    if exact is not None:
        accepted.update(bound_texts(exact).values())
    return accepted


def changed_texts(
    record: LicenseRecord,
    receipts: ReceiptStore,
    *,
    records: RecordIndex | None = None,
) -> dict[str, ChangedText]:
    """Section label -> ChangedText for each bound text changed since an acceptance.

    records resolves the text identities of receipts written before LIC4 (no
    binding identity) or before LIC4-FIX (no licence text identity), and
    recognises receipts for other records in view. Without it, only the
    presented record is used for that.
    """
    if not isinstance(receipts, ReceiptStore):
        raise TypeError("changed_texts needs the ReceiptStore that holds earlier receipts")
    by_identity: dict[str, dict[str, set[str]]] = {}
    for receipt in scan_receipts(receipts).receipts:
        if receipt.decision != "accept":
            continue
        for identity, digest in accepted_texts(receipt, presented=record, records=records):
            by_identity.setdefault(identity, {}).setdefault(digest, set()).add(
                receipt.licence_id
            )
    changed: dict[str, ChangedText] = {}
    for section, (identity, shown) in bound_texts(record).items():
        earlier = by_identity.get(identity)
        if not earlier or shown in earlier:
            continue
        changed[section] = ChangedText(
            section=section,
            identity=identity,
            shown_sha256=shown,
            accepted_sha256=tuple(sorted(earlier)),
            accepted_under=tuple(sorted(set().union(*earlier.values()))),
        )
    return changed


def changed_block(item: ChangedText) -> bytes:
    """The marker printed immediately before the changed text's own section."""
    lines = [
        CHANGED_HEADER,
        f"changed: {item.section}",
        f"text identity: {item.identity}",
    ]
    lines.extend(f"accepted sha256: {digest}" for digest in item.accepted_sha256)
    lines.append(f"accepted under: {', '.join(item.accepted_under)}")
    lines.append(f"shown sha256: {item.shown_sha256}")
    lines.append(
        "The text below differs from the text you accepted before. "
        "Your earlier acceptance does not cover it."
    )
    return ("\n".join(lines) + "\n").encode("utf-8")
