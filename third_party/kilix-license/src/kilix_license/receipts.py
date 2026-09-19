"""Digest-bound receipts: bound fields plus context-only digests."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from kilix_license.agreement import Agreement, typed_agreement_line
from kilix_license.digest import (
    canonical_json,
    load_json_object,
    mapping,
    require_sha256,
    sha256_hex,
)
from kilix_license.errors import AgreementRequired, ReceiptShapeError
from kilix_license.records import LicenseRecord

RECEIPT_SCHEMA = "kilix.license.receipt/v1"

BOUND_FIELDS = (
    "record_digest",
    "manifest_digest",
    "licence_id",
    "licence_text_digest",
    "decision",
    "licensor",
    "binding_condition_text_digests",
)
CONTEXT_FIELDS = (
    "advisory_digests",
    "release_digest",
    "catalogue_digest",
)
_TOP_LEVEL = frozenset(("schema", *BOUND_FIELDS, "context"))
_R3_BOUND_KEYS = frozenset({"catalogue_digest", "catalog_sha256", "catalogue_sha256"})


def _digest_map(value: Any, label: str) -> dict[str, str]:
    raw = mapping(value, label)
    result: dict[str, str] = {}
    for key, item in raw.items():
        if not isinstance(key, str) or not key:
            raise ValueError(f"{label} keys must be non-empty strings")
        result[key] = require_sha256(item, f"{label}.{key}")
    return result


@dataclass(frozen=True)
class Receipt:
    record_digest: str
    manifest_digest: str
    licence_id: str
    licence_text_digest: str
    decision: str
    licensor: str
    binding_condition_text_digests: dict[str, str]
    advisory_digests: dict[str, str]
    release_digest: str
    catalogue_digest: str

    def to_jsonable(self) -> dict[str, Any]:
        return {
            "binding_condition_text_digests": dict(
                sorted(self.binding_condition_text_digests.items())
            ),
            "context": {
                "advisory_digests": dict(sorted(self.advisory_digests.items())),
                "catalogue_digest": self.catalogue_digest,
                "release_digest": self.release_digest,
            },
            "decision": self.decision,
            "licence_id": self.licence_id,
            "licence_text_digest": self.licence_text_digest,
            "licensor": self.licensor,
            "manifest_digest": self.manifest_digest,
            "record_digest": self.record_digest,
            "schema": RECEIPT_SCHEMA,
        }

    def to_bytes(self) -> bytes:
        return canonical_json(self.to_jsonable()) + b"\n"

    @property
    def digest(self) -> str:
        return sha256_hex(self.to_bytes())


def _refuse_r3_keys(raw: Mapping[str, Any], label: str) -> None:
    for key in raw:
        if key in _R3_BOUND_KEYS:
            raise ReceiptShapeError(
                key,
                f"{label} plants catalogue-digest binding (R3 shape) at {key}",
            )


def parse_receipt(raw: Mapping[str, Any]) -> Receipt:
    _refuse_r3_keys(raw, "receipt")
    unknown = tuple(key for key in raw if key not in _TOP_LEVEL)
    if unknown:
        raise ReceiptShapeError(unknown[0], f"receipt has unknown field(s): {unknown}")
    schema = raw.get("schema")
    if schema != RECEIPT_SCHEMA:
        raise ReceiptShapeError("schema", f"receipt schema must be {RECEIPT_SCHEMA}")
    missing = [field for field in BOUND_FIELDS if field not in raw]
    if missing:
        raise ReceiptShapeError(missing[0], f"receipt missing bound field {missing[0]}")
    context = mapping(raw.get("context"), "context")
    unknown_context = tuple(key for key in context if key not in CONTEXT_FIELDS)
    if unknown_context:
        raise ReceiptShapeError(
            unknown_context[0],
            f"receipt context has unknown field(s): {unknown_context}",
        )
    for field in CONTEXT_FIELDS:
        if field not in context:
            raise ReceiptShapeError(field, f"receipt context missing {field}")
    decision = raw.get("decision")
    if decision not in {"accept", "record"}:
        raise ReceiptShapeError("decision", "receipt decision must be accept or record")
    licence_id = raw.get("licence_id")
    licensor = raw.get("licensor")
    if not isinstance(licence_id, str) or not licence_id:
        raise ReceiptShapeError("licence_id")
    if not isinstance(licensor, str) or not licensor:
        raise ReceiptShapeError("licensor")
    return Receipt(
        record_digest=require_sha256(raw.get("record_digest"), "record_digest"),
        manifest_digest=require_sha256(raw.get("manifest_digest"), "manifest_digest"),
        licence_id=licence_id,
        licence_text_digest=require_sha256(
            raw.get("licence_text_digest"), "licence_text_digest"
        ),
        decision=decision,
        licensor=licensor,
        binding_condition_text_digests=_digest_map(
            raw.get("binding_condition_text_digests"),
            "binding_condition_text_digests",
        ),
        advisory_digests=_digest_map(context.get("advisory_digests"), "advisory_digests"),
        release_digest=require_sha256(context.get("release_digest"), "release_digest"),
        catalogue_digest=require_sha256(
            context.get("catalogue_digest"), "catalogue_digest"
        ),
    )


def parse_receipt_bytes(data: bytes) -> Receipt:
    return parse_receipt(load_json_object(data, "receipt"))


def receipt_from_agreement(
    record: LicenseRecord,
    agreement: Agreement,
    *,
    manifest_digest: str,
    release_digest: str,
    catalogue_digest: str,
) -> Receipt:
    if agreement.licence_id != record.id:
        raise AgreementRequired("agreement licence id does not match record")
    if agreement.decision != record.expected_decision:
        raise AgreementRequired("agreement decision does not match record class")
    expected = record.agreement_binding_digests()
    if tuple(sorted(agreement.named_binding_ids)) != tuple(sorted(expected)):
        raise AgreementRequired("agreement did not name every binding condition")
    if agreement.decision == "accept" and agreement.typed_text != typed_agreement_line(
        record
    ):
        raise AgreementRequired("accept receipt requires the typed agreement line")
    if agreement.record_digest is None and agreement.binding_condition_text_digests is None:
        raise AgreementRequired("agreement is not bound to the bytes shown")
    if agreement.record_digest is not None and agreement.record_digest != record.digest:
        raise AgreementRequired("agreement record digest does not match the record")
    if agreement.binding_condition_text_digests is not None:
        shown = dict(agreement.binding_condition_text_digests)
        if shown != expected:
            raise AgreementRequired(
                "agreement binding digests do not match the record"
            )
    return Receipt(
        record_digest=record.digest,
        manifest_digest=require_sha256(manifest_digest, "manifest_digest"),
        licence_id=record.id,
        licence_text_digest=record.text_sha256,
        decision=agreement.decision,
        licensor=record.licensor,
        binding_condition_text_digests=dict(expected),
        advisory_digests=record.advisory_digests(),
        release_digest=require_sha256(release_digest, "release_digest"),
        catalogue_digest=require_sha256(catalogue_digest, "catalogue_digest"),
    )
