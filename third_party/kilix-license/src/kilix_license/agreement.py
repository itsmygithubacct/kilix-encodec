"""Agreement capture. Informational licences need no typed line."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import datetime as _datetime
import re
import sys
from typing import Any

from kilix_license.errors import AgreementRequired, ReceiptShapeError
from kilix_license.records import LicenseRecord

# V-ACC-VERIFY F6, settled by OD-BC. A receipt named no person, no moment and
# no circumstance, so one well-formed receipt satisfied every gate, on every
# machine, for every user, for ever -- and a receipt placed in an image would
# have read exactly like consent a user gave. What follows does not fix that.
# It records the two facts this authority can observe for itself at the moment
# the agreement is captured, and nothing else: OD-BC answered "record neither"
# on the identity fields, so no account name and no uid is written.
# Read what each field is called literally. Neither is an identity claim, and
# neither is attested by anything.
INTERACTIVE_TTY = "interactive-tty"
NO_TTY = "no-tty"
CAPTURE_MODES = (INTERACTIVE_TTY, NO_TTY)

# \Z, not $: `$` also matches before a final newline, so "...Z\n" parsed and
# covered (LIC6-VERIFY F9). It carried no identity -- nothing fits after the
# newline -- but captured_at was not strictly canonical, and this authority's
# own writer never produces one.
_CAPTURED_AT = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z\Z")


@dataclass(frozen=True)
class Acceptance:
    """What the authority observed about the process that captured agreement.

    Two fields, and only two. OD-BC answered "record neither" on identity, so
    a receipt carries no account name and no uid: **nothing in a receipt
    distinguishes two users of one machine**, and that is the accepted
    outcome, not an oversight.

    * ``captured_at`` is the capturing machine's own UTC clock at capture. It
      is not attested, not signed and not compared with anything; a machine
      with a wrong or adjusted clock writes a wrong value and nothing notices.
    * ``capture_mode`` is ``interactive-tty`` when that process had a terminal
      on both its standard input and its standard output at capture time, and
      ``no-tty`` otherwise. A terminal is evidence that something could have
      typed; it is not evidence that a person did.

    **What a receipt proves, and what it does not** (OD-BC: record, do not
    bind, in 0.2.2). A receipt is an unsigned JSON file on the user's own
    disk. It records that an agreement was captured by this authority, against
    a particular licence record and manifest, at a stated moment, with or
    without a terminal. It does **not** prove that a human accepted anything;
    it does **not** say which human, on a shared or automated account; and it
    does **not** prove that the recorded time is the real time. Nothing signs
    it, so a producer that wants to fabricate one can. Treat the block as the
    place to look when asking where a receipt came from, never as consent.

    Nothing here is bound (OD-AI): ``covers()`` never reads it, so recording it
    changes no receipt's coverage and voids no acceptance. It is audit context,
    and, for a caller that opts in, a check a consumer can demand.

    See the shipping rule in ``README.md``: a receipt is never shipped,
    vendored or provisioned, and shipping one is not a remedy for a refusing
    gate.
    """

    captured_at: str
    capture_mode: str

    def __post_init__(self) -> None:
        # LIC6-VERIFY F7. Without this, a receipt whose captured_at is a
        # number, a bool or bytes raised TypeError out of the regex rather
        # than ReceiptShapeError -- the first receipt field in this module to
        # leave the typed-failure contract. It reached a consumer:
        # kilix_content.first_use.needs_agreement() catches CoverageRefused
        # only, so one malformed file in the store turned the first-use flow
        # into a traceback instead of a refusal anyone could act on.
        if not isinstance(self.captured_at, str):
            raise ReceiptShapeError(
                "acceptance.captured_at",
                "captured_at must be a string, not "
                f"{type(self.captured_at).__name__} ({self.captured_at!r})",
            )
        if not isinstance(self.capture_mode, str):
            raise ReceiptShapeError(
                "acceptance.capture_mode",
                "capture_mode must be a string, not "
                f"{type(self.capture_mode).__name__} ({self.capture_mode!r})",
            )
        if not _CAPTURED_AT.match(self.captured_at or ""):
            raise ReceiptShapeError(
                "acceptance.captured_at",
                "captured_at must be RFC 3339 UTC seconds, e.g. "
                f"2026-09-21T20:41:00Z, not {self.captured_at!r}",
            )
        if self.capture_mode not in CAPTURE_MODES:
            raise ReceiptShapeError(
                "acceptance.capture_mode",
                f"capture_mode must be one of {CAPTURE_MODES}, "
                f"not {self.capture_mode!r}",
            )

    @property
    def was_captured_at_a_terminal(self) -> bool:
        """True only for ``interactive-tty``. Says nothing about who typed."""
        return self.capture_mode == INTERACTIVE_TTY

    def to_jsonable(self) -> dict[str, Any]:
        return {
            "capture_mode": self.capture_mode,
            "captured_at": self.captured_at,
        }

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any], label: str) -> Acceptance:
        if not isinstance(raw, Mapping):
            raise ReceiptShapeError(label, f"{label} must be an object")
        expected = {"capture_mode", "captured_at"}
        unknown = sorted(set(raw) - expected)
        if unknown:
            raise ReceiptShapeError(
                f"{label}.{unknown[0]}",
                f"{label} has unknown field(s): {unknown}. OD-BC records only "
                f"{sorted(expected)}; an identity field is refused, not ignored.",
            )
        missing = sorted(expected - set(raw))
        if missing:
            raise ReceiptShapeError(
                f"{label}.{missing[0]}", f"{label} is missing {missing[0]}"
            )
        return cls(
            captured_at=raw["captured_at"],
            capture_mode=raw["capture_mode"],
        )


def observe_capture(
    *, now: _datetime.datetime | None = None, interactive: bool | None = None
) -> Acceptance:
    """Observe the capturing process, here, now.

    Both values are taken by this authority from its own process rather than
    accepted from a caller, so a producer cannot assert a nicer story by
    passing one in. ``now`` and ``interactive`` exist for tests and for a
    producer that genuinely knows better about its own terminal (a TUI that
    owns the tty on a different descriptor, say).

    Nothing about the user is read: no account name, no uid, no home, no
    hostname (OD-BC).
    """
    moment = (now or _datetime.datetime.now(_datetime.timezone.utc)).astimezone(
        _datetime.timezone.utc
    )
    if interactive is None:
        interactive = _has_terminal()
    return Acceptance(
        captured_at=moment.strftime("%Y-%m-%dT%H:%M:%SZ"),
        capture_mode=INTERACTIVE_TTY if interactive else NO_TTY,
    )


def _has_terminal() -> bool:
    for stream in (sys.stdin, sys.stdout):
        try:
            if stream is None or not stream.isatty():
                return False
        except (OSError, ValueError):
            return False
    return True


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
    # What was observed about this capture (V-ACC-VERIFY F6). None only for an
    # Agreement a caller built by hand, as every caller did before LIC6.
    acceptance: Acceptance | None = None


def typed_agreement_line(record: LicenseRecord) -> str:
    parts = [record.id]
    parts.extend(
        condition.id
        for condition in record.binding_conditions
        if condition.agreement_required
    )
    return f"accept {' '.join(parts)} from {record.licensor}"


def capture_agreement(
    record: LicenseRecord,
    typed_text: str | None = None,
    *,
    acceptance: Acceptance | None = None,
) -> Agreement:
    """Capture agreement, and record what this process observes about itself.

    ``acceptance`` defaults to :func:`observe_capture` called here, at the
    moment the agreement is taken, so the recorded moment is the acceptance's
    and not the receipt writer's. A caller may pass its own -- a first-use flow
    that captured the agreement earlier, say -- but cannot suppress it.
    """
    observed = acceptance or observe_capture()
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
            acceptance=observed,
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
        acceptance=observed,
    )
