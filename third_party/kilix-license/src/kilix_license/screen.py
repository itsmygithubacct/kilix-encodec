"""Verbatim first-use screen: each stored text is printed byte-identical."""

from __future__ import annotations

from kilix_license.digest import sha256_hex
from kilix_license.errors import TextDigestMismatch
from kilix_license.paraphrase import refuse_paraphrase
from kilix_license.records import LicenseRecord
from kilix_license.texts import TextStore


def _section(label: str, digest: str, texts: TextStore) -> bytes:
    payload = texts.get(digest, label=label)
    if sha256_hex(payload) != digest:
        raise TextDigestMismatch(f"{label} digest mismatch")
    refuse_paraphrase(payload, label)
    header = f"=== {label} ===\n".encode("utf-8")
    if payload.endswith(b"\n"):
        body = payload
    else:
        body = payload + b"\n"
    return header + body


def render_screen(record: LicenseRecord, texts: TextStore) -> bytes:
    """Return screen bytes. Every stored text appears byte-identical."""
    chunks = [_section(f"licence:{record.id}", record.text_sha256, texts)]
    for component in record.components:
        if component.exception_text_sha256 is not None:
            chunks.append(
                _section(
                    f"component:{component.id}",
                    component.exception_text_sha256,
                    texts,
                )
            )
    for condition in record.binding_conditions:
        chunks.append(
            _section(f"binding:{condition.id}", condition.text_sha256, texts)
        )
    for advisory in record.advisories:
        chunks.append(
            _section(f"advisory:{advisory.id}", advisory.text_sha256, texts)
        )
    for statement in record.statements:
        chunks.append(
            _section(f"statement:{statement.id}", statement.text_sha256, texts)
        )
    return b"".join(chunks)
