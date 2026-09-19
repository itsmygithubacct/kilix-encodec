"""Agreement capture. Informational licences need no typed line."""

from __future__ import annotations

from dataclasses import dataclass

from kilix_license.errors import AgreementRequired
from kilix_license.records import LicenseRecord


@dataclass(frozen=True)
class Agreement:
    licence_id: str
    named_binding_ids: tuple[str, ...]
    decision: str
    typed_text: str | None
    # Digests taken from the rendered screen at capture time (OD-AH/AP).
    # receipt_from_agreement refuses when they differ from the record.
    record_digest: str | None = None
    binding_condition_text_digests: dict[str, str] | None = None


def typed_agreement_line(record: LicenseRecord) -> str:
    parts = [record.id]
    parts.extend(
        condition.id
        for condition in record.binding_conditions
        if condition.agreement_required
    )
    return f"accept {' '.join(parts)} from {record.licensor}"


def capture_agreement(
    record: LicenseRecord, typed_text: str | None = None
) -> Agreement:
    required = tuple(
        condition.id
        for condition in record.binding_conditions
        if condition.agreement_required
    )
    if record.expected_decision == "record":
        if typed_text:
            raise AgreementRequired(
                "informational licence needs no typed agreement"
            )
        return Agreement(
            record.id,
            (),
            "record",
            None,
            record_digest=record.digest,
            binding_condition_text_digests=dict(record.agreement_binding_digests()),
        )
    expected = typed_agreement_line(record)
    if typed_text != expected:
        raise AgreementRequired(
            "typed agreement must name every binding text shown: "
            f"expected {expected!r}"
        )
    return Agreement(
        record.id,
        required,
        "accept",
        typed_text,
        record_digest=record.digest,
        binding_condition_text_digests=dict(record.agreement_binding_digests()),
    )
