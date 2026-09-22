"""Binding-scoped receipt coverage (OD-AI, OD-AQ)."""

from __future__ import annotations

from dataclasses import dataclass

from kilix_license.errors import CoverageRefused, ReceiptShapeError
from kilix_license.receipts import Receipt, parse_receipt
from kilix_license.records import LicenseRecord, RecordIndex
from kilix_license.store import ReceiptStore


@dataclass(frozen=True)
class AssetRef:
    id: str
    record_digest: str
    manifest_digest: str


def _compare(field: str, expected: str, actual: str) -> None:
    if expected != actual:
        raise CoverageRefused(field)


def covers(
    record: LicenseRecord,
    receipt: Receipt,
    *,
    manifest_digest: str,
) -> bool:
    """True when receipt covers the OD-AI binding, including the manifest.

    The expected manifest is required so consumers cannot call a form that
    ignores it. A one-byte change to any bound field raises CoverageRefused
    naming the field. Catalogue-digest binding (R3 shape) raises
    ReceiptShapeError. Advisory/statement/non-exception component digests
    are context (OD-AQ) and are not compared.
    """
    parse_receipt(receipt.to_jsonable())
    _compare("manifest_digest", manifest_digest, receipt.manifest_digest)
    _compare("licence_id", record.id, receipt.licence_id)
    _compare("licensor", record.licensor, receipt.licensor)
    _compare("licence_text_digest", record.text_sha256, receipt.licence_text_digest)
    expected_binding = record.agreement_binding_digests()
    actual_binding = dict(receipt.binding_condition_text_digests)
    expected_keys = set(expected_binding)
    actual_keys = set(actual_binding)
    if expected_keys != actual_keys:
        missing = expected_keys - actual_keys
        extra = actual_keys - expected_keys
        field = sorted(missing | extra)[0]
        raise CoverageRefused(f"binding_condition_text_digests:{field}")
    for key, digest in expected_binding.items():
        actual = actual_binding.get(key)
        if actual is None or actual != digest:
            raise CoverageRefused(f"binding_condition_text_digests:{key}")
    _compare("decision", record.expected_decision, receipt.decision)
    _compare("record_digest", record.digest, receipt.record_digest)
    return True


def require(
    asset: AssetRef,
    *,
    records: RecordIndex,
    store: ReceiptStore,
    captured_at_a_terminal: bool = False,
) -> Receipt:
    """Return the covering receipt for asset, scoped to the binding.

    ``captured_at_a_terminal`` is off by default and changes nothing when it
    is: coverage is the OD-AI binding and nothing else, so every receipt any
    earlier kilix-license wrote is accepted exactly as before.

    Switched on, it additionally demands that the receipt record a capture at
    a terminal (V-ACC-VERIFY F6's ``acceptance.capture_mode``). That refuses a
    receipt written with no console -- a build step, an image, a provisioning
    script -- and refuses every receipt written before LIC6, which record no
    capture at all. It is not proof a person accepted anything, and it says
    nothing about which person: see
    :class:`kilix_license.agreement.Acceptance` for exactly what a receipt
    proves and does not. It is the weakest mechanical check that distinguishes
    a receipt minted at a console from one that was shipped.

    OD-BC keeps this lever **available and off**, and pairs it with a rule
    that no flag can enforce: a receipt is never shipped, vendored or
    provisioned, and shipping one is not an acceptable remedy for a refusing
    gate. The acceptable outcomes are acceptance at first use, or no weights.
    See ``README.md``.
    """
    record = records.by_digest(asset.record_digest)
    # LIC6-FIX-VERIFY V4. A malformed file in the store used to leave this
    # function by raising, and the consumer that asks the question --
    # kilix_content.first_use.needs_agreement() -- catches CoverageRefused
    # only, so one truncated write or half-restored backup turned the
    # first-use flow into a traceback rather than a refusal. LIC6 typed the
    # failure (ReceiptShapeError instead of TypeError or ValueError) but that
    # class is not a CoverageRefused either, so the symptom was unchanged.
    #
    # A file this authority cannot read as a receipt covers nothing, so that
    # is what require() says. Fail-closed and unchanged in effect: no weights
    # are fetched, the caller shows the licence and asks for consent. The
    # typed contract inside the authority is untouched -- parse_receipt_bytes
    # and parse_receipt still raise ReceiptShapeError, and the original is
    # chained here and named in the message, so a corrupt store is still
    # diagnosable.
    #
    # What this does NOT close, said plainly: the pre-existing family of bare
    # `ValueError` escapes out of the LIC4/LIC5 digest-map and text-id helpers
    # (advisory_digests, statement_digests, binding_text_ids,
    # component_exception_digests, catalogue_digest, release_digest,
    # licence_text_id) still leave this function untyped. They are identical
    # at 417b0c2d, c002be26 and 1a3bc477, so nothing regressed; closing them
    # changes the failure contract of seven fields at once and is its own
    # wave, with its own require() differential.
    try:
        receipt = store.lookup(asset.record_digest, asset.manifest_digest)
        others = () if receipt is not None else store.for_record(asset.record_digest)
    except ReceiptShapeError as error:
        raise CoverageRefused(
            error.field,
            "a file in the receipt store is not a receipt this authority can "
            f"read, so nothing there covers this asset: {error}",
        ) from error
    if receipt is None:
        if others:
            raise CoverageRefused("manifest_digest")
        raise CoverageRefused("receipt")
    covers(record, receipt, manifest_digest=asset.manifest_digest)
    if receipt.manifest_digest != asset.manifest_digest:
        raise CoverageRefused("manifest_digest")
    if receipt.record_digest != asset.record_digest:
        raise CoverageRefused("record_digest")
    if captured_at_a_terminal and not (
        receipt.acceptance is not None
        and receipt.acceptance.was_captured_at_a_terminal
    ):
        raise CoverageRefused(
            "acceptance.capture_mode",
            "this caller demands a receipt captured at a terminal; this one "
            + (
                "records no capture at all (it predates LIC6)"
                if receipt.acceptance is None
                else f"records capture_mode {receipt.acceptance.capture_mode!r}"
            ),
        )
    return receipt


def refuse_catalogue_binding(raw: dict[str, object]) -> None:
    """Refuse a planted R3 receipt that binds catalogue digest."""
    for field in ("catalogue_digest", "catalog_sha256", "catalogue_sha256"):
        if field in raw:
            raise ReceiptShapeError(field)
    parse_receipt(raw)
