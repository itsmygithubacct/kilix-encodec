"""Digest-bound receipts: bound fields plus context-only digests."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Any

from kilix_license.agreement import Acceptance, Agreement, typed_agreement_line
from kilix_license.digest import (
    canonical_json,
    load_json_object,
    mapping,
    require_sha256,
    sha256_hex,
)
from kilix_license.errors import AgreementRequired, ReceiptShapeError
from kilix_license.records import LicenseRecord, require_text_id

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
# Required in every receipt/v1 context, including receipts written by fbdfb546.
CONTEXT_FIELDS = (
    "advisory_digests",
    "release_digest",
    "catalogue_digest",
)
# LIC4, additive: recorded, never bound, absent from older receipts. They
# record what was on the screen (OD-AQ, C2E-VERIFY F6) and the identity of
# each bound text (SR-4). covers() never reads them. licence_text_id is
# LIC4-FIX (LIC4-VERIFY LIC4-3): absent from receipts LIC4 wrote.
#
# LIC6 adds `acceptance` (V-ACC-VERIFY F6, OD-BC): when, and at what kind of
# console, this authority captured the agreement. Also recorded, never bound,
# and it names nobody -- OD-BC records no account and no uid.
OPTIONAL_CONTEXT_FIELDS = (
    "acceptance",
    "binding_text_ids",
    "component_exception_digests",
    "licence_text_id",
    "statement_digests",
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
    # LIC4 context. None means the receipt predates LIC4 and has no such key;
    # it then serialises to exactly the bytes it was read from.
    binding_text_ids: dict[str, str] | None = None
    component_exception_digests: dict[str, str] | None = None
    statement_digests: dict[str, str] | None = None
    # LIC4-FIX context. None: the receipt names no licence text identity.
    licence_text_id: str | None = None
    # LIC6 context (V-ACC-VERIFY F6, OD-BC). None: the receipt predates LIC6
    # and says nothing about when, or at what kind of console, it was
    # captured. It then serialises to exactly the bytes it was read from.
    acceptance: Acceptance | None = None

    def to_jsonable(self) -> dict[str, Any]:
        context: dict[str, Any] = {
            "advisory_digests": dict(sorted(self.advisory_digests.items())),
            "catalogue_digest": self.catalogue_digest,
            "release_digest": self.release_digest,
        }
        for field in OPTIONAL_CONTEXT_FIELDS:
            value = getattr(self, field)
            if value is None:
                continue
            if isinstance(value, Acceptance):
                context[field] = value.to_jsonable()
            else:
                context[field] = (
                    value if isinstance(value, str) else dict(sorted(value.items()))
                )
        return {
            "binding_condition_text_digests": dict(
                sorted(self.binding_condition_text_digests.items())
            ),
            "context": context,
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
    unknown_context = tuple(
        key
        for key in context
        if key not in CONTEXT_FIELDS and key not in OPTIONAL_CONTEXT_FIELDS
    )
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
    receipt = Receipt(
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
    # LIC4 optional context, validated after every fbdfb546 field so an older
    # receipt is refused (or accepted) exactly as before.
    extra: dict[str, Any] = {}
    if "binding_text_ids" in context:
        ids = mapping(context.get("binding_text_ids"), "binding_text_ids")
        stray = sorted(
            key for key in ids if key not in receipt.binding_condition_text_digests
        )
        if stray:
            raise ReceiptShapeError(
                "binding_text_ids",
                f"binding_text_ids names unbound binding(s) {stray}",
            )
        extra["binding_text_ids"] = {
            key: require_text_id(value, f"binding_text_ids.{key}")
            for key, value in ids.items()
        }
    for field in ("component_exception_digests", "statement_digests"):
        if field in context:
            extra[field] = _digest_map(context.get(field), field)
    if "licence_text_id" in context:
        extra["licence_text_id"] = require_text_id(
            context.get("licence_text_id"), "licence_text_id"
        )
    if "acceptance" in context:
        extra["acceptance"] = Acceptance.from_mapping(
            context.get("acceptance"), "acceptance"
        )
    return replace(receipt, **extra) if extra else receipt


def parse_receipt_bytes(data: bytes) -> Receipt:
    """Parse one receipt's bytes, refusing malformed bytes by type.

    LIC6-VERIFY F7's shape, at a second field. The differential found two
    calls of 280 in which ``require()`` raised a bare ``ValueError`` --
    ``receipt is not UTF-8 JSON`` -- for a file at the exact lookup path whose
    bytes are not JSON at all: a truncated write, a half-restored backup, a
    file replaced by something else. That escapes the typed-failure contract
    exactly as a non-string ``captured_at`` did, and reaches the same
    consumer, which catches ``CoverageRefused`` only and shows a traceback
    where a refusal belongs. Present since 417b0c2d, in every version; the
    two differential lines this changes are the only intended behaviour
    change in the wave, and they move from ``RAISED ValueError`` to
    ``REFUSED ReceiptShapeError:receipt``.
    """
    try:
        payload = load_json_object(data, "receipt")
    except ReceiptShapeError:
        raise
    except ValueError as error:
        raise ReceiptShapeError("receipt", str(error)) from error
    return parse_receipt(payload)


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
    # V-ACC-VERIFY F6. An authority whose receipts optionally record nothing
    # about their own capture has not closed F6 at all: a producer would only
    # have to build the Agreement by hand to mint the timeless receipt the
    # finding is about. Every receipt this authority mints says when, and at
    # what kind of console, it was captured -- and nothing about who, which
    # OD-BC decided is not recorded. Receipts written before LIC6 are
    # unaffected: this is the write path, and reading them is unchanged.
    if agreement.acceptance is None:
        raise AgreementRequired(
            "agreement records no capture: build it with capture_agreement(), "
            "or pass an Acceptance from observe_capture() if the agreement was "
            "captured elsewhere. A receipt must say when, and at what kind of "
            "console, it was minted (V-ACC-VERIFY F6, OD-BC)."
        )
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
        # Context only (OD-AQ): what the screen showed, and each bound text's
        # identity for the changed-text marker (SR-4). Never compared by covers().
        binding_text_ids=record.binding_text_ids(),
        component_exception_digests=record.component_exception_digests(),
        statement_digests=record.statement_digests(),
        licence_text_id=record.licence_text_id,
        # V-ACC-VERIFY F6: what was observed when the agreement was captured,
        # not when this receipt was assembled. An Agreement built by hand
        # carries none, and then neither does the receipt.
        acceptance=agreement.acceptance,
    )
