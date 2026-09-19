"""Kilix licence authority: records, screen, receipts, and coverage."""

from __future__ import annotations

from kilix_license.agreement import Agreement, capture_agreement, typed_agreement_line
from kilix_license.catalog import load_determined_records, load_determined_texts
from kilix_license.coverage import AssetRef, covers, require
from kilix_license.errors import (
    AgreementRequired,
    CoverageRefused,
    HandEditedRecord,
    LicenseError,
    LiveStoreForbidden,
    ParaphraseRefused,
    ReceiptShapeError,
    TextDigestMismatch,
)
from kilix_license.paths import live_store_root, refuse_live_store
from kilix_license.receipts import Receipt, receipt_from_agreement
from kilix_license.records import LicenseRecord, RecordIndex
from kilix_license.screen import render_screen
from kilix_license.store import ReceiptStore
from kilix_license.texts import TextStore

__version__ = "0.1.0"

__all__ = [
    "Agreement",
    "AgreementRequired",
    "AssetRef",
    "CoverageRefused",
    "HandEditedRecord",
    "LicenseError",
    "LicenseRecord",
    "LiveStoreForbidden",
    "ParaphraseRefused",
    "Receipt",
    "ReceiptShapeError",
    "ReceiptStore",
    "RecordIndex",
    "TextDigestMismatch",
    "TextStore",
    "__version__",
    "capture_agreement",
    "covers",
    "load_determined_records",
    "load_determined_texts",
    "live_store_root",
    "receipt_from_agreement",
    "refuse_live_store",
    "render_screen",
    "require",
    "typed_agreement_line",
]
