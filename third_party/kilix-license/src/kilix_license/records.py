"""Licence record schema: id, licensor, text digest, components, conditions."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
import re
from typing import Any

from kilix_license.digest import (
    canonical_json,
    load_json_object,
    mapping,
    require_sha256,
    sha256_hex,
)

RECORD_SCHEMA = "kilix.license.record/v1"
# Entry ids use ollama-style colons (granite4.1:3b). Component slugs use [._:-].
_ID = re.compile(r"^[a-z0-9][a-z0-9._:-]{0,127}$")
# A text identity names the document a bound text is cut from, without its
# revision (for example bfl.ai/legal/usage-policy). It is not a fetch URL.
_TEXT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/#@+~-]{0,255}$")
_DECISION_CLASSES = frozenset({"affirmative", "informational"})


def _require_id(value: Any, label: str) -> str:
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise ValueError(f"invalid {label}: {value!r}")
    return value


def require_text_id(value: Any, label: str) -> str:
    if not isinstance(value, str) or not _TEXT_ID.fullmatch(value):
        raise ValueError(f"invalid {label}: {value!r}")
    return value


def _require_text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise ValueError(f"{label} must be a non-empty string")
    return value


@dataclass(frozen=True)
class Component:
    id: str
    role: str = ""
    exception_text_sha256: str | None = None

    def to_jsonable(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"id": self.id}
        if self.role:
            payload["role"] = self.role
        if self.exception_text_sha256 is not None:
            payload["exception_text_sha256"] = self.exception_text_sha256
        return payload

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any], label: str) -> Component:
        unknown = tuple(key for key in raw if key not in {"id", "role", "exception_text_sha256"})
        if unknown:
            raise ValueError(f"{label} has unknown field(s): {unknown}")
        role = raw.get("role", "")
        if role is None:
            role = ""
        if not isinstance(role, str):
            raise ValueError(f"{label}.role must be a string")
        exception = raw.get("exception_text_sha256")
        if exception is not None:
            exception = require_sha256(exception, f"{label}.exception_text_sha256")
        return cls(
            id=_require_id(raw.get("id"), f"{label}.id"),
            role=role,
            exception_text_sha256=exception,
        )


@dataclass(frozen=True)
class BindingCondition:
    id: str
    text_sha256: str
    agreement_required: bool = True
    # SR-4: identity of the text for "changed since your last acceptance".
    # It survives a revised text (new digest), a sibling record showing the
    # same text, and a renamed binding id. It is not bound (OD-AI): it is
    # left out of the record digest and never grants coverage.
    text_id: str | None = None

    def to_binding_jsonable(self) -> dict[str, Any]:
        """The bound fields: exactly the fbdfb546 shape, so record digests do not move."""
        return {
            "agreement_required": self.agreement_required,
            "id": self.id,
            "text_sha256": self.text_sha256,
        }

    def to_jsonable(self) -> dict[str, Any]:
        payload = self.to_binding_jsonable()
        if self.text_id is not None:
            payload["text_id"] = self.text_id
        return payload

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any], label: str) -> BindingCondition:
        unknown = tuple(
            key
            for key in raw
            if key not in {"id", "text_sha256", "agreement_required", "text_id"}
        )
        if unknown:
            raise ValueError(f"{label} has unknown field(s): {unknown}")
        required = raw.get("agreement_required", True)
        if not isinstance(required, bool):
            raise ValueError(f"{label}.agreement_required must be a boolean")
        text_id = raw.get("text_id")
        if text_id is not None:
            text_id = require_text_id(text_id, f"{label}.text_id")
        return cls(
            id=_require_id(raw.get("id"), f"{label}.id"),
            text_sha256=require_sha256(raw.get("text_sha256"), f"{label}.text_sha256"),
            agreement_required=required,
            text_id=text_id,
        )


@dataclass(frozen=True)
class Advisory:
    id: str
    text_sha256: str

    def to_jsonable(self) -> dict[str, Any]:
        return {"id": self.id, "text_sha256": self.text_sha256}

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any], label: str) -> Advisory:
        unknown = tuple(key for key in raw if key not in {"id", "text_sha256"})
        if unknown:
            raise ValueError(f"{label} has unknown field(s): {unknown}")
        return cls(
            id=_require_id(raw.get("id"), f"{label}.id"),
            text_sha256=require_sha256(raw.get("text_sha256"), f"{label}.text_sha256"),
        )


@dataclass(frozen=True)
class Statement:
    id: str
    text_sha256: str

    def to_jsonable(self) -> dict[str, Any]:
        return {"id": self.id, "text_sha256": self.text_sha256}

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any], label: str) -> Statement:
        unknown = tuple(key for key in raw if key not in {"id", "text_sha256"})
        if unknown:
            raise ValueError(f"{label} has unknown field(s): {unknown}")
        return cls(
            id=_require_id(raw.get("id"), f"{label}.id"),
            text_sha256=require_sha256(raw.get("text_sha256"), f"{label}.text_sha256"),
        )


def _tuple_of(cls: Any, value: Any, label: str) -> tuple[Any, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ValueError(f"{label} must be an array")
    items = []
    seen: set[str] = set()
    for index, item in enumerate(value):
        parsed = cls.from_mapping(mapping(item, f"{label}[{index}]"), f"{label}[{index}]")
        if parsed.id in seen:
            raise ValueError(f"{label} repeats id {parsed.id!r}")
        seen.add(parsed.id)
        items.append(parsed)
    return tuple(items)


@dataclass(frozen=True)
class LicenseRecord:
    id: str
    licensor: str
    text_sha256: str
    components: tuple[Component, ...] = ()
    binding_conditions: tuple[BindingCondition, ...] = ()
    advisories: tuple[Advisory, ...] = ()
    statements: tuple[Statement, ...] = ()
    determinations_sha256: str | None = None
    decision_class: str | None = None
    licence_ids: tuple[str, ...] = ()
    # SR-4 (LIC4-VERIFY LIC4-3): identity of the licence text, keyed by the
    # document, like BindingCondition.text_id. Sibling records that show one
    # licence text share it. Not bound (OD-AI): left out of the record digest.
    licence_text_id: str | None = None

    def to_jsonable(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "advisories": [item.to_jsonable() for item in self.advisories],
            "binding_conditions": [item.to_jsonable() for item in self.binding_conditions],
            "components": [item.to_jsonable() for item in self.components],
            "id": self.id,
            "licensor": self.licensor,
            "schema": RECORD_SCHEMA,
            "statements": [item.to_jsonable() for item in self.statements],
            "text_sha256": self.text_sha256,
        }
        if self.decision_class is not None:
            payload["decision_class"] = self.decision_class
        if self.determinations_sha256 is not None:
            payload["determinations_sha256"] = self.determinations_sha256
        if self.licence_ids:
            payload["licence_ids"] = list(self.licence_ids)
        if self.licence_text_id is not None:
            payload["licence_text_id"] = self.licence_text_id
        return payload

    def to_binding_jsonable(self) -> dict[str, Any]:
        """Licence-record payload that forms the OD-AI record digest.

        OD-AI's "record digest" names this licence record (OD-AJ), not an
        asset/v3 record. Binding fields are: this digest (sans advisories),
        manifest digest, licence id, licence text digest, decision, licensor,
        and every agreement-required binding-condition text digest.

        Advisories, statements, and component entries without exception text
        are context (OD-AQ). Their text hashes are omitted here, so a changed
        advisory keeps covers() and is recorded as receipt context the next
        time the screen is shown. A binding condition's text_id and the
        record's licence_text_id (SR-4) are omitted too: they name the text,
        they do not bind it.
        """
        payload: dict[str, Any] = {
            "binding_conditions": [
                item.to_binding_jsonable() for item in self.binding_conditions
            ],
            "components": [
                item.to_jsonable()
                for item in self.components
                if item.exception_text_sha256 is not None
            ],
            "id": self.id,
            "licensor": self.licensor,
            "schema": RECORD_SCHEMA,
            "text_sha256": self.text_sha256,
        }
        if self.decision_class is not None:
            payload["decision_class"] = self.decision_class
        if self.licence_ids:
            payload["licence_ids"] = list(self.licence_ids)
        return payload

    @property
    def digest(self) -> str:
        return sha256_hex(canonical_json(self.to_binding_jsonable()))

    @property
    def requires_typed_agreement(self) -> bool:
        return any(item.agreement_required for item in self.binding_conditions)

    @property
    def expected_decision(self) -> str:
        if self.requires_typed_agreement:
            return "accept"
        if self.decision_class == "affirmative":
            return "accept"
        return "record"

    def agreement_binding_digests(self) -> dict[str, str]:
        return {
            item.id: item.text_sha256
            for item in self.binding_conditions
            if item.agreement_required
        }

    def advisory_digests(self) -> dict[str, str]:
        return {item.id: item.text_sha256 for item in self.advisories}

    def statement_digests(self) -> dict[str, str]:
        return {item.id: item.text_sha256 for item in self.statements}

    def component_exception_digests(self) -> dict[str, str]:
        return {
            item.id: item.exception_text_sha256
            for item in self.components
            if item.exception_text_sha256 is not None
        }

    def binding_text_ids(self) -> dict[str, str]:
        """Declared text identities of the agreement-required binding conditions (SR-4)."""
        return {
            item.id: item.text_id
            for item in self.binding_conditions
            if item.agreement_required and item.text_id is not None
        }

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> LicenseRecord:
        unknown = tuple(
            key
            for key in raw
            if key
            not in {
                "schema",
                "id",
                "licensor",
                "text_sha256",
                "components",
                "binding_conditions",
                "advisories",
                "statements",
                "determinations_sha256",
                "decision_class",
                "licence_ids",
                "licence_text_id",
            }
        )
        if unknown:
            raise ValueError(f"licence record has unknown field(s): {unknown}")
        schema = raw.get("schema")
        if schema != RECORD_SCHEMA:
            raise ValueError(f"licence record schema must be {RECORD_SCHEMA}")
        determinations = raw.get("determinations_sha256")
        if determinations is not None:
            determinations = require_sha256(determinations, "determinations_sha256")
        decision_class = raw.get("decision_class")
        if decision_class is not None:
            if decision_class not in _DECISION_CLASSES:
                raise ValueError(f"invalid decision_class: {decision_class!r}")
        licence_ids_raw = raw.get("licence_ids")
        licence_ids: tuple[str, ...] = ()
        if licence_ids_raw is not None:
            if not isinstance(licence_ids_raw, list) or not all(
                isinstance(item, str) and item for item in licence_ids_raw
            ):
                raise ValueError("licence_ids must be an array of non-empty strings")
            licence_ids = tuple(licence_ids_raw)
        licence_text_id = raw.get("licence_text_id")
        if licence_text_id is not None:
            licence_text_id = require_text_id(licence_text_id, "licence_text_id")
        return cls(
            id=_require_id(raw.get("id"), "id"),
            licensor=_require_text(raw.get("licensor"), "licensor"),
            text_sha256=require_sha256(raw.get("text_sha256"), "text_sha256"),
            components=_tuple_of(Component, raw.get("components"), "components"),
            binding_conditions=_tuple_of(
                BindingCondition, raw.get("binding_conditions"), "binding_conditions"
            ),
            advisories=_tuple_of(Advisory, raw.get("advisories"), "advisories"),
            statements=_tuple_of(Statement, raw.get("statements"), "statements"),
            determinations_sha256=determinations,
            decision_class=decision_class,
            licence_ids=licence_ids,
            licence_text_id=licence_text_id,
        )

    @classmethod
    def from_bytes(cls, data: bytes) -> LicenseRecord:
        return cls.from_mapping(load_json_object(data, "licence record"))


class RecordIndex:
    def __init__(self, records: Iterable[LicenseRecord]) -> None:
        by_digest: dict[str, LicenseRecord] = {}
        by_id: dict[str, LicenseRecord] = {}
        for record in records:
            digest = record.digest
            if digest in by_digest:
                raise ValueError(f"duplicate record digest {digest}")
            if record.id in by_id:
                raise ValueError(f"duplicate record id {record.id}")
            by_digest[digest] = record
            by_id[record.id] = record
        self._by_digest = by_digest
        self._by_id = by_id

    def by_digest(self, digest: str) -> LicenseRecord:
        try:
            return self._by_digest[digest]
        except KeyError as exc:
            raise KeyError(f"no licence record with digest {digest}") from exc

    def by_id(self, record_id: str) -> LicenseRecord:
        try:
            return self._by_id[record_id]
        except KeyError as exc:
            raise KeyError(f"no licence record with id {record_id}") from exc

    def __iter__(self):
        return iter(self._by_id.values())
