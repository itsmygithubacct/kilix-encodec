"""Verbatim first-use screen: each stored text is printed byte-identical.

SR-4: the screen also marks every bound text that is "changed since your
last acceptance", so every consumer that shows a record shows the marker.
The marker is a block printed immediately before the changed section; the
section's own bytes are unchanged, and it never grants coverage.
"""

from __future__ import annotations

from collections.abc import Mapping

from kilix_license.changed import ChangedText, changed_block, changed_texts
from kilix_license.digest import sha256_hex
from kilix_license.errors import TextDigestMismatch
from kilix_license.paraphrase import refuse_paraphrase
from kilix_license.records import LicenseRecord, RecordIndex
from kilix_license.store import ReceiptStore
from kilix_license.texts import TextStore


def _section(
    label: str,
    digest: str,
    texts: TextStore,
    changed: Mapping[str, ChangedText],
) -> bytes:
    payload = texts.get(digest, label=label)
    if sha256_hex(payload) != digest:
        raise TextDigestMismatch(f"{label} digest mismatch")
    refuse_paraphrase(payload, label)
    header = f"=== {label} ===\n".encode("utf-8")
    if payload.endswith(b"\n"):
        body = payload
    else:
        body = payload + b"\n"
    marker = changed_block(changed[label]) if label in changed else b""
    return marker + header + body


def render_screen(
    record: LicenseRecord,
    texts: TextStore,
    *,
    receipts: ReceiptStore,
    records: RecordIndex | None = None,
) -> bytes:
    """Return screen bytes. Every stored text appears byte-identical.

    receipts is the store that holds earlier receipts; it is required, so no
    consumer can render a screen that omits the changed-text marker (the
    two-argument form is refused). records resolves receipts written before
    LIC4 and receipts for sibling records; see kilix_license.changed.
    """
    changed = changed_texts(record, receipts, records=records)
    chunks = [_section(f"licence:{record.id}", record.text_sha256, texts, changed)]
    for component in record.components:
        if component.exception_text_sha256 is not None:
            chunks.append(
                _section(
                    f"component:{component.id}",
                    component.exception_text_sha256,
                    texts,
                    changed,
                )
            )
    for condition in record.binding_conditions:
        chunks.append(
            _section(f"binding:{condition.id}", condition.text_sha256, texts, changed)
        )
    for advisory in record.advisories:
        chunks.append(
            _section(f"advisory:{advisory.id}", advisory.text_sha256, texts, changed)
        )
    for statement in record.statements:
        chunks.append(
            _section(f"statement:{statement.id}", statement.text_sha256, texts, changed)
        )
    return b"".join(chunks)
