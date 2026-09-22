"""Typed failures for the licence authority."""

from __future__ import annotations


class LicenseError(Exception):
    """Base class for kilix-license failures."""


class LiveStoreForbidden(LicenseError):
    """A path under the live receipt store was refused."""


class ReceiptStoreRootRefused(LicenseError):
    """A receipt store root that is not the one the authority publishes.

    V-ACC-VERIFY F7: kilix-content wrote a receipt at a root it chose and
    kilix-voice looked for it at a root it chose, so an acceptance the user
    really gave left the gate refusing. There is one root, and it comes from
    receipt_store_root(); a caller that means the shared store and names
    something else is refused here rather than silently filing out of sight.
    """


class TextDigestMismatch(LicenseError):
    """Stored bytes do not match their sha256 name."""


class ParaphraseRefused(LicenseError):
    """A known paraphrase (Pocket TERMS_SUMMARY) was offered as licence text."""


class AgreementRequired(LicenseError):
    """Typed agreement was missing, extra, or did not name the binding texts."""


class ReceiptShapeError(LicenseError):
    """Receipt JSON is not the OD-AI/OD-AQ shape (for example R3 catalogue binding)."""

    def __init__(self, field: str, message: str | None = None) -> None:
        self.field = field
        super().__init__(message or f"receipt shape refuses field {field}")


class CoverageRefused(LicenseError):
    """A receipt does not cover a binding field."""

    def __init__(self, field: str, message: str | None = None) -> None:
        self.field = field
        super().__init__(message or f"receipt does not cover binding field {field}")


class AtomicWriteCrashed(LicenseError):
    """A planted crash during receipt publication; no covering receipt exists."""


class HandEditedRecord(LicenseError):
    """A committed licence record does not match generator output."""
