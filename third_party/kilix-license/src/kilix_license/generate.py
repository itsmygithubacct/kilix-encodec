"""Generate licence records from the pinned determinations JSON.

Licence file bytes are never retyped: they are copied into data/texts/
named by sha256. Quote-backed texts are the JSON `text` field, which R0-DET-R2
cut from cited source spans. A hand-edited committed record fails --check.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any
import re

from kilix_license.digest import canonical_json, load_json_object, sha256_hex
from kilix_license.errors import HandEditedRecord, TextDigestMismatch
from kilix_license.records import (
    Advisory,
    BindingCondition,
    Component,
    LicenseRecord,
    Statement,
)

DATA_DIR = Path(__file__).resolve().parent / "data"
DETERMINATIONS_NAME = "determinations.json"
PIN_NAME = "determinations.sha256"
TEXTS_DIRNAME = "texts"
RECORDS_DIRNAME = "records"
CONVERTER_ID = "encodec-converter-runtime-code"
CONVERTER_TEXT_ROLE = "code-relicensing-notice"
FORBIDDEN_PREFIXES = ("kilix-llm",)
# Same id set generate.py (R0-DET-R2) used so records equal the JSON.
CONDITIONING_IDS = frozenset(
    {
        "Apache-2.0",
        "MIT",
        "CC-BY-4.0",
        "CC-BY-NC-4.0",
        "LicenseRef-Meta-Llama-3-Community-License",
    }
)
REQUIRED_RECORD_IDS = (
    "small-en-us",
    "lgraph-en-us",
    "qwen3-tts-0.6b-base",
    "qwen3-tts-0.6b-customvoice",
    "qwen3-tts-1.7b-voicedesign",
    "whisper-tiny-ggml",
    "piper-en-us-kristin-medium",
    "vibevoice-asr-bitnet",
    "yolox_s",
    "yolox_tiny",
    "yolox_nano",
    "yamnet",
    "bonsai-8b",
    "bonsai-27b",
    "bitnet-b1.58-2b4t",
    "granite4.1:3b",
    "granite3.2-vision:2b",
    "nomic-embed-text:v1.5",
    "qwen3.5:4b",
    "pocket-tts-english-q8_0",
    "bonsai-image-4b:ternary-gemlite",
    "bonsai-image-4b:binary-gemlite",
    "encodec-24khz-stateful",
    "encodec-48khz-frame",
    CONVERTER_ID,
)
_NON_ID = re.compile(r"[^a-z0-9._:-]+")
_QUOTE_KEYS = (
    "binding_conditions",
    "advisories",
    "statements",
    "notes",
    "attribution",
    "accepted_gaps",
)


def data_dir(root: Path | None = None) -> Path:
    if root is None:
        return DATA_DIR
    return Path(root) / "src" / "kilix_license" / "data"


def record_filename(record_id: str) -> str:
    return record_id.replace(":", "_") + ".json"


def load_pin(directory: Path) -> str:
    pin_path = directory / PIN_NAME
    text = pin_path.read_text(encoding="utf-8")
    digest = None
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        parts = stripped.split()
        if len(parts) < 1:
            continue
        digest = parts[0]
        break
    if digest is None or len(digest) != 64:
        raise ValueError(f"{pin_path} does not contain a sha256 pin")
    return digest


def load_determinations(directory: Path) -> tuple[bytes, dict[str, Any], str]:
    path = directory / DETERMINATIONS_NAME
    data = path.read_bytes()
    digest = sha256_hex(data)
    pinned = load_pin(directory)
    if digest != pinned:
        raise TextDigestMismatch(
            f"{DETERMINATIONS_NAME} sha256 {digest} != pin {pinned}"
        )
    payload = load_json_object(data, "determinations")
    return data, payload, digest


def slug_id(value: str) -> str:
    slug = _NON_ID.sub("-", value.lower()).strip("-")
    slug = re.sub(r"-{2,}", "-", slug)
    if not slug:
        raise ValueError(f"cannot slug {value!r}")
    return slug


def quote_bytes(quote: Mapping[str, Any], label: str) -> tuple[str, bytes]:
    text = quote.get("text")
    digest = quote.get("text_sha256")
    if not isinstance(text, str) or not text:
        raise ValueError(f"{label} has no text")
    if not isinstance(digest, str):
        raise ValueError(f"{label} has no text_sha256")
    data = text.encode("utf-8")
    if sha256_hex(data) != digest:
        raise TextDigestMismatch(f"{label} text does not match text_sha256")
    return digest, data


def file_text(entry: Mapping[str, Any], role: str) -> tuple[str, str] | None:
    for item in entry.get("licence_texts") or []:
        if item.get("role") == role:
            digest = item.get("sha256")
            label = item.get("label") or role
            if not isinstance(digest, str):
                raise ValueError(f"{entry.get('entry_id')}:{role} missing sha256")
            return digest, str(label)
    return None


def licence_text_digest(entry: Mapping[str, Any]) -> str:
    found = file_text(entry, "licence")
    if found is not None:
        return found[0]
    licence = entry.get("licence") or {}
    label = licence.get("label")
    if isinstance(label, dict) and label.get("text_sha256"):
        digest, _data = quote_bytes(label, f"{entry.get('entry_id')}.licence.label")
        return digest
    raise ValueError(f"{entry.get('entry_id')} has no licence text")


def licence_ids_of(entry: Mapping[str, Any]) -> tuple[str, ...]:
    licence = entry.get("licence") or {}
    spdx = licence.get("spdx") or []
    if spdx:
        if not isinstance(spdx, list) or not all(isinstance(item, str) for item in spdx):
            raise ValueError(f"{entry.get('entry_id')} licence.spdx is not a string list")
        return tuple(spdx)
    label = licence.get("label")
    if isinstance(label, dict) and isinstance(label.get("text"), str) and label["text"]:
        return (label["text"],)
    return ()


def decision_class_for_entry(entry: Mapping[str, Any]) -> str:
    """PLAN-R3 / R4-042: affirmative iff a binding condition or restrictive licence."""
    ids = set(entry.get("licence", {}).get("spdx") or [])
    for component in entry.get("components") or []:
        ids.update(component.get("licence_ids") or [])
    if ids & CONDITIONING_IDS or entry.get("binding_conditions"):
        return "affirmative"
    return "informational"


def licensors_of(entry: Mapping[str, Any]) -> str:
    names = []
    for item in entry.get("licensors") or []:
        name = item.get("name")
        if not isinstance(name, str) or not name:
            raise ValueError(f"{entry.get('entry_id')} licensor missing name")
        names.append(name)
    if not names:
        raise ValueError(f"{entry.get('entry_id')} has no licensors")
    return "; ".join(names)


def load_text_file(texts_dir: Path, digest: str, label: str) -> bytes:
    path = texts_dir / digest
    if not path.is_file():
        raise FileNotFoundError(f"missing licence text {digest} ({label})")
    data = path.read_bytes()
    if sha256_hex(data) != digest:
        raise TextDigestMismatch(f"{label} {digest} does not match stored bytes")
    return data


def collect_quote_texts(entry: Mapping[str, Any]) -> dict[str, bytes]:
    blobs: dict[str, bytes] = {}
    entry_id = str(entry.get("entry_id"))
    licence = entry.get("licence") or {}
    label = licence.get("label")
    if isinstance(label, dict) and label.get("text"):
        digest, data = quote_bytes(label, f"{entry_id}.licence.label")
        blobs[digest] = data
    for key in _QUOTE_KEYS:
        for index, quote in enumerate(entry.get(key) or []):
            if not isinstance(quote, dict) or "text" not in quote:
                continue
            digest, data = quote_bytes(quote, f"{entry_id}.{key}[{index}]")
            blobs[digest] = data
    for index, component in enumerate(entry.get("components") or []):
        for field in ("record_quote", "attribution"):
            quote = component.get(field)
            if isinstance(quote, dict) and quote.get("text"):
                digest, data = quote_bytes(
                    quote, f"{entry_id}.components[{index}].{field}"
                )
                blobs[digest] = data
    return blobs


def _quotes_as(
    cls: type,
    quotes: list[Any] | None,
    label: str,
) -> tuple[Any, ...]:
    items = []
    seen: set[str] = set()
    for index, quote in enumerate(quotes or []):
        if not isinstance(quote, dict):
            raise ValueError(f"{label}[{index}] is not an object")
        quote_id = quote.get("quote_id")
        if not isinstance(quote_id, str) or not quote_id:
            raise ValueError(f"{label}[{index}] missing quote_id")
        item_id = slug_id(quote_id)
        if item_id in seen:
            raise ValueError(f"{label} repeats id {item_id!r}")
        seen.add(item_id)
        digest, _data = quote_bytes(quote, f"{label}[{index}]")
        if cls is BindingCondition:
            items.append(
                BindingCondition(id=item_id, text_sha256=digest, agreement_required=True)
            )
        elif cls is Advisory:
            items.append(Advisory(id=item_id, text_sha256=digest))
        else:
            items.append(Statement(id=item_id, text_sha256=digest))
    return tuple(items)


def components_of(entry: Mapping[str, Any], texts_dir: Path) -> tuple[Component, ...]:
    items = []
    seen: set[str] = set()
    for index, raw in enumerate(entry.get("components") or []):
        name = raw.get("name")
        if not isinstance(name, str) or not name:
            raise ValueError(f"{entry.get('entry_id')}.components[{index}] missing name")
        item_id = slug_id(name)
        if item_id in seen:
            raise ValueError(f"duplicate component id {item_id}")
        seen.add(item_id)
        exception_digest = None
        if raw.get("exception"):
            text = raw.get("text") or {}
            digest = text.get("sha256")
            if not isinstance(digest, str):
                raise ValueError(f"{item_id} exception is missing text sha256")
            load_text_file(texts_dir, digest, f"component:{item_id}")
            exception_digest = digest
        items.append(
            Component(
                id=item_id,
                role="exception" if raw.get("exception") else "component",
                exception_text_sha256=exception_digest,
            )
        )
    return tuple(items)


def record_from_entry(
    entry: Mapping[str, Any],
    *,
    pin: str,
    texts_dir: Path,
) -> LicenseRecord:
    entry_id = entry.get("entry_id")
    if not isinstance(entry_id, str) or not entry_id:
        raise ValueError("entry missing entry_id")
    if any(entry_id.startswith(prefix) for prefix in FORBIDDEN_PREFIXES):
        raise ValueError(f"refusing out-of-closure entry {entry_id}")
    text_digest = licence_text_digest(entry)
    load_text_file(texts_dir, text_digest, f"licence:{entry_id}")
    notes = list(entry.get("advisories") or []) + list(entry.get("notes") or [])
    statements = list(entry.get("statements") or []) + list(
        entry.get("attribution") or []
    )
    generated = decision_class_for_entry(entry)
    recorded = (entry.get("agreement") or {}).get("decision_class")
    if recorded is not None and recorded != generated:
        raise ValueError(
            f"{entry_id} generated decision_class {generated} != JSON {recorded}"
        )
    return LicenseRecord(
        id=entry_id,
        licensor=licensors_of(entry),
        text_sha256=text_digest,
        components=components_of(entry, texts_dir),
        binding_conditions=_quotes_as(
            BindingCondition,
            entry.get("binding_conditions"),
            f"{entry_id}.binding_conditions",
        ),
        advisories=_quotes_as(Advisory, notes, f"{entry_id}.advisories"),
        statements=_quotes_as(Statement, statements, f"{entry_id}.statements"),
        determinations_sha256=pin,
        decision_class=generated,
        licence_ids=licence_ids_of(entry),
    )


def _converter_mit_digest(payload: Mapping[str, Any]) -> str:
    for entry in payload.get("entries") or []:
        found = file_text(entry, CONVERTER_TEXT_ROLE)
        if found is not None:
            return found[0]
    raise ValueError("no converter MIT notice text in determinations licence_texts")


def _mit_copyright_holder(data: bytes) -> str:
    for line in data.decode("utf-8").splitlines():
        if line.startswith("Copyright (c) "):
            return line[len("Copyright (c) ") :].rstrip(".")
    raise ValueError("converter MIT text has no Copyright (c) line")


def converter_record(
    payload: Mapping[str, Any],
    *,
    pin: str,
    texts_dir: Path,
) -> LicenseRecord:
    not_entries = payload.get("not_entries") or []
    raw = None
    for item in not_entries:
        if item.get("entry_id") == CONVERTER_ID:
            raw = item
            break
    if raw is None:
        raise ValueError(f"determinations not_entries missing {CONVERTER_ID}")
    digest = _converter_mit_digest(payload)
    mit_bytes = load_text_file(texts_dir, digest, "converter-mit")
    quote = raw.get("quote") or {}
    statement_digest, _data = quote_bytes(quote, "converter.od-as")
    return LicenseRecord(
        id=CONVERTER_ID,
        licensor=_mit_copyright_holder(mit_bytes),
        text_sha256=digest,
        components=(),
        binding_conditions=(),
        advisories=(),
        statements=(
            Statement(id=slug_id(str(quote.get("quote_id"))), text_sha256=statement_digest),
        ),
        determinations_sha256=pin,
        decision_class="informational",
        licence_ids=("MIT",),
    )


def generate_records(
    payload: Mapping[str, Any],
    *,
    pin: str,
    texts_dir: Path,
) -> list[LicenseRecord]:
    records: list[LicenseRecord] = []
    seen: set[str] = set()
    for entry in payload.get("entries") or []:
        entry_id = entry.get("entry_id")
        if not isinstance(entry_id, str):
            raise ValueError("entry missing entry_id")
        if any(entry_id.startswith(prefix) for prefix in FORBIDDEN_PREFIXES):
            raise ValueError(f"kilix-llm entry {entry_id} must not become a record")
        record = record_from_entry(entry, pin=pin, texts_dir=texts_dir)
        if record.id in seen:
            raise ValueError(f"duplicate record id {record.id}")
        seen.add(record.id)
        records.append(record)
    converter = converter_record(payload, pin=pin, texts_dir=texts_dir)
    if converter.id in seen:
        raise ValueError("converter id collides with a model entry")
    records.append(converter)
    missing = [item for item in REQUIRED_RECORD_IDS if item not in {r.id for r in records}]
    if missing:
        raise ValueError(f"missing required records: {missing}")
    extra_llm = [item.id for item in records if item.id.startswith("kilix-llm")]
    if extra_llm:
        raise ValueError(f"kilix-llm records are out of closure: {extra_llm}")
    return records


def quote_blobs(payload: Mapping[str, Any]) -> dict[str, bytes]:
    blobs: dict[str, bytes] = {}
    for entry in payload.get("entries") or []:
        blobs.update(collect_quote_texts(entry))
    for item in payload.get("not_entries") or []:
        if item.get("entry_id") == CONVERTER_ID and item.get("quote"):
            digest, data = quote_bytes(item["quote"], "converter.od-as")
            blobs[digest] = data
    return blobs


def write_quote_texts(payload: Mapping[str, Any], texts_dir: Path) -> None:
    texts_dir.mkdir(parents=True, exist_ok=True)
    for digest, data in quote_blobs(payload).items():
        path = texts_dir / digest
        if path.is_file():
            existing = path.read_bytes()
            if existing != data:
                raise TextDigestMismatch(f"text {digest} collides with different bytes")
            continue
        path.write_bytes(data)


def render_record_bytes(record: LicenseRecord) -> bytes:
    return canonical_json(record.to_jsonable()) + b"\n"


def write_records(records: list[LicenseRecord], records_dir: Path) -> None:
    records_dir.mkdir(parents=True, exist_ok=True)
    wanted = {record_filename(record.id) for record in records}
    for record in records:
        path = records_dir / record_filename(record.id)
        path.write_bytes(render_record_bytes(record))
    for path in records_dir.glob("*.json"):
        if path.name not in wanted:
            path.unlink()


def committed_record_bytes(records_dir: Path, record_id: str) -> bytes | None:
    path = records_dir / record_filename(record_id)
    if not path.is_file():
        return None
    return path.read_bytes()


def check_records(records: list[LicenseRecord], records_dir: Path) -> None:
    mismatches: list[str] = []
    wanted = {record_filename(record.id) for record in records}
    for record in records:
        name = record_filename(record.id)
        expected = render_record_bytes(record)
        actual = committed_record_bytes(records_dir, record.id)
        if actual is None:
            mismatches.append(f"missing {name}")
            continue
        if actual != expected:
            mismatches.append(name)
    for path in sorted(records_dir.glob("*.json")):
        if path.name not in wanted:
            mismatches.append(f"extra {path.name}")
    if mismatches:
        raise HandEditedRecord(
            "generator refuses hand-edited or missing records: "
            + ", ".join(mismatches)
        )


