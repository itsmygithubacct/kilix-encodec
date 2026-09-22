"""Kilix licence authority: records, screen, receipts, and coverage."""

from __future__ import annotations

from kilix_license.agreement import (
    Acceptance,
    Agreement,
    capture_agreement,
    observe_capture,
    typed_agreement_line,
)
from kilix_license.catalog import load_determined_records, load_determined_texts
from kilix_license.changed import ChangedText, ReceiptScan, changed_texts, scan_receipts
from kilix_license.coverage import AssetRef, covers, require
from kilix_license.errors import (
    AgreementRequired,
    CoverageRefused,
    HandEditedRecord,
    LicenseError,
    LiveStoreForbidden,
    ParaphraseRefused,
    ReceiptShapeError,
    ReceiptStoreRootRefused,
    TextDigestMismatch,
)
from kilix_license.paths import (
    check_receipt_store_root,
    live_store_root,
    live_store_roots,
    receipt_store_root,
    refuse_live_store,
    stack_home,
    user_home,
)
from kilix_license.receipts import Receipt, receipt_from_agreement
from kilix_license.records import LicenseRecord, RecordIndex
from kilix_license.screen import render_screen
from kilix_license.store import ReceiptStore
from kilix_license.texts import TextStore

__version__ = "0.1.0"

__all__ = [
    "Acceptance",
    "Agreement",
    "AgreementRequired",
    "AssetRef",
    "ChangedText",
    "CoverageRefused",
    "HandEditedRecord",
    "LicenseError",
    "LicenseRecord",
    "LiveStoreForbidden",
    "ParaphraseRefused",
    "Receipt",
    "ReceiptScan",
    "ReceiptShapeError",
    "ReceiptStore",
    "ReceiptStoreRootRefused",
    "RecordIndex",
    "TextDigestMismatch",
    "TextStore",
    "__version__",
    "capture_agreement",
    "changed_texts",
    "check_receipt_store_root",
    "covers",
    "load_determined_records",
    "load_determined_texts",
    "live_store_root",
    "live_store_roots",
    "observe_capture",
    "receipt_from_agreement",
    "receipt_store_root",
    "refuse_live_store",
    "render_screen",
    "require",
    "scan_receipts",
    "stack_home",
    "typed_agreement_line",
    "user_home",
]
