"""Generate licence records from the pinned determinations JSON.

Licence file bytes are never retyped: they are copied into data/texts/
named by sha256. Quote-backed texts are the JSON `text` field, which the
determinations generator (R0-DET-R2; R3 for the OD-AY PDF-engine entries) cut
from cited source spans. A hand-edited committed record fails --check.
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
    require_text_id,
)

DATA_DIR = Path(__file__).resolve().parent / "data"
DETERMINATIONS_NAME = "determinations.json"
PIN_NAME = "determinations.sha256"
TEXTS_DIRNAME = "texts"
RECORDS_DIRNAME = "records"
CONVERTER_ID = "encodec-converter-runtime-code"
CONVERTER_TEXT_ROLE = "code-relicensing-notice"
FORBIDDEN_PREFIXES = ("kilix-llm",)
# Same id set the determinations generator (R0-DET-R2, R3) used so records equal the JSON.
CONDITIONING_IDS = frozenset(
    {
        "Apache-2.0",
        "MIT",
        "CC-BY-4.0",
        "CC-BY-NC-4.0",
        "LicenseRef-Meta-Llama-3-Community-License",
    }
)
# OD-AY "Granite only": P1, N1 and N2 get records. The Datalab/Surya models
# (P2-P6), the F1 font, N3 and N4 do not.
PDF_ENGINE_RECORD_IDS = (
    "granite-docling-258m",
    "documentfigureclassifier-v2.5",
    "granite-vision-4.1-4b",
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
    # OD-AY (R4-047): the kilix-pdf-conversion [granite] engine models only.
    *PDF_ENGINE_RECORD_IDS,
)
# SR-4 (C2E-VERIFY F2): the identity of each agreement-required binding text,
# keyed by the determinations quote_id. The value names the document the text
# is cut from, without a revision. It is an identifier, not a fetch URL. The
# key is the text itself, not the record or binding id, so:
#   - sibling records showing one text share it (both Bonsai Image variants);
#   - a revised text keeps it while its digest changes (the changed marker);
#   - a renamed binding keeps it: map the new quote_id to the same value.
# Every binding quote must be listed; the generator refuses one that is not,
# so a rename cannot silently mint a new identity. check_binding_text_ids()
# refuses a table that merges two source documents or splits one quoted span.
# Not bound (OD-AI): records carry it outside the record digest.
BINDING_TEXT_IDS = {
    # OD-AH. https://bfl.ai/legal/usage-policy (ADDENDUM-BFL-POLICY.md line 7).
    "bfl-usage-policy": "bfl.ai/legal/usage-policy",
    "bfl-usage-policy-binary": "bfl.ai/legal/usage-policy",
    # OD-AG. The "## Prohibited use" section of the Pocket TTS model card README.
    "pocket-prohibited-use": (
        "huggingface.co/kyutai/pocket-tts-without-voice-cloning/README.md#prohibited-use"
    ),
    # OD-AP. The Meta Llama 3 Community License (meta-llama/llama3 LICENSE), in full.
    "llama3-community-licence-full-text": "github.com/meta-llama/llama3/LICENSE",
    # OD-AR. The owner's non-commercial binding sentence, shown for both checkpoints.
    "encodec-noncommercial-binding": "kilix/owner-decisions/OD-AR#non-commercial-binding",
}
# SR-4 (LIC4-VERIFY LIC4-3): the identity of each record's licence text,
# chosen the way BINDING_TEXT_IDS is: keyed by the determinations entry_id, the
# value names the document the licence text is (without a revision). It is an
# identifier, not a fetch URL. Records that show one licence text share it, so
# a licence text revised through a sibling is marked changed. Every record must
# be listed; the generator refuses one that is not, so a renamed entry cannot
# silently mint a new identity. check_licence_text_ids() refuses a table that
# gives two licence texts one identity, or one licence text two identities.
# Each comment cites the determinations licence_texts label (or quote) the
# value is taken from. Not bound (OD-AI): records carry it outside the digest.
LICENCE_TEXT_IDS = {
    # "Apache-2.0 text (Debian common-licenses copy, cfc7749b)": the Vosk models,
    # and the card-only Apache-2.0 grants (builder reading R3 item 1).
    "small-en-us": "debian/common-licenses/Apache-2.0",
    "lgraph-en-us": "debian/common-licenses/Apache-2.0",
    "granite-docling-258m": "debian/common-licenses/Apache-2.0",
    "granite-vision-4.1-4b": "debian/common-licenses/Apache-2.0",
    # Licence label quoted from OWNER-DETERMINATION-piper-kristin.md line 6.
    "piper-en-us-kristin-medium": "kilix/owner-determinations/piper-kristin#licence",
    # "MIT LICENSE of microsoft/VibeVoice".
    "vibevoice-asr-bitnet": "github.com/microsoft/VibeVoice/LICENSE",
    # "YOLOX LICENSE at tag 0.1.1rc0" (Megvii-BaseDetection/YOLOX).
    "yolox_s": "github.com/Megvii-BaseDetection/YOLOX/LICENSE",
    "yolox_tiny": "github.com/Megvii-BaseDetection/YOLOX/LICENSE",
    "yolox_nano": "github.com/Megvii-BaseDetection/YOLOX/LICENSE",
    # "Apache-2.0 LICENSE of tensorflow/models".
    "yamnet": "github.com/tensorflow/models/LICENSE",
    # "Prism ML Apache-2.0 LICENSE": one file, byte-identical in all four
    # prism-ml repositories, so the identity names the publisher's licence.
    "bonsai-8b": "huggingface.co/prism-ml#LICENSE",
    "bonsai-27b": "huggingface.co/prism-ml#LICENSE",
    "bonsai-image-4b:ternary-gemlite": "huggingface.co/prism-ml#LICENSE",
    "bonsai-image-4b:binary-gemlite": "huggingface.co/prism-ml#LICENSE",
    # "Ollama registry licence layer" of each library model.
    "granite4.1:3b": "registry.ollama.ai/library/granite4.1:3b#license",
    "granite3.2-vision:2b": "registry.ollama.ai/library/granite3.2-vision:2b#license",
    "nomic-embed-text:v1.5": "registry.ollama.ai/library/nomic-embed-text:v1.5#license",
    "qwen3.5:4b": "registry.ollama.ai/library/qwen3.5:4b#license",
    # "BitNet MIT LICENSE at microsoft/bitnet-b1.58-2B-4T".
    "bitnet-b1.58-2b4t": "huggingface.co/microsoft/bitnet-b1.58-2B-4T/LICENSE",
    # "CC BY 4.0 legal code".
    "pocket-tts-english-q8_0": "creativecommons.org/licenses/by/4.0/legalcode",
    # "kilix config/model_notices a44a6081 (Apache-2.0 with Alibaba Cloud copyright)".
    "qwen3-tts-0.6b-base": "kilix/config/model_notices#qwen3-tts",
    "qwen3-tts-0.6b-customvoice": "kilix/config/model_notices#qwen3-tts",
    "qwen3-tts-1.7b-voicedesign": "kilix/config/model_notices#qwen3-tts",
    # "kilix config/model_notices b5d65a59 (OpenAI Whisper MIT LICENSE)".
    "whisper-tiny-ggml": "kilix/config/model_notices#whisper",
    # "CC BY-NC 4.0 legal code (creativecommons.org)", both checkpoints (OD-AR).
    "encodec-24khz-stateful": "creativecommons.org/licenses/by-nc/4.0/legalcode",
    "encodec-48khz-frame": "creativecommons.org/licenses/by-nc/4.0/legalcode",
    # "MIT License canonical text, SPDX license-list-data ... text/MIT.txt".
    "documentfigureclassifier-v2.5": "github.com/spdx/license-list-data/text/MIT.txt",
    # "facebookresearch/encodec MIT LICENSE" (OD-AS code relicensing notice).
    CONVERTER_ID: "github.com/facebookresearch/encodec/LICENSE",
}
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
    """The one sha256 pin line. A second pin line is refused (LIC3-2): a stale
    pin appended after the real one must not pass unnoticed."""
    pin_path = directory / PIN_NAME
    text = pin_path.read_text(encoding="utf-8")
    pins = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        pins.append(stripped.split()[0])
    if len(pins) != 1:
        raise ValueError(f"{pin_path} must hold exactly one sha256 pin line, found {len(pins)}")
    digest = pins[0]
    if len(digest) != 64 or set(digest) - set("0123456789abcdef"):
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
            text_id = BINDING_TEXT_IDS.get(quote_id)
            if text_id is None:
                raise ValueError(
                    f"{label}[{index}] binding quote {quote_id!r} has no text identity "
                    "in BINDING_TEXT_IDS (SR-4); a renamed binding maps to its old identity"
                )
            require_text_id(text_id, f"BINDING_TEXT_IDS[{quote_id!r}]")
            items.append(
                BindingCondition(
                    id=item_id,
                    text_sha256=digest,
                    agreement_required=True,
                    text_id=text_id,
                )
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


def licence_text_id_for(record_id: str) -> str:
    """The declared licence text identity of record_id; a missing one is refused."""
    text_id = LICENCE_TEXT_IDS.get(record_id)
    if text_id is None:
        raise ValueError(
            f"record {record_id!r} has no licence text identity in LICENCE_TEXT_IDS "
            "(SR-4); a renamed entry maps to its old identity"
        )
    return require_text_id(text_id, f"LICENCE_TEXT_IDS[{record_id!r}]")


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
        licence_text_id=licence_text_id_for(entry_id),
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
        licence_text_id=licence_text_id_for(CONVERTER_ID),
    )


def check_binding_text_ids(
    payload: Mapping[str, Any],
    table: Mapping[str, str] | None = None,
) -> None:
    """Refuse an identity table that disagrees with the determinations' sources.

    One identity must not cover two source documents, and one quoted span of
    one source document must not carry two identities (a sibling left
    without its alias). Missing entries are refused where records are built.
    """
    if table is None:
        table = BINDING_TEXT_IDS
    sources: dict[str, set[str]] = {}
    spans: dict[tuple[Any, ...], set[str]] = {}
    for entry in payload.get("entries") or []:
        for quote in entry.get("binding_conditions") or []:
            text_id = table.get(quote.get("quote_id"))
            if text_id is None:
                continue
            source = quote.get("source") or {}
            sources.setdefault(text_id, set()).add(str(source.get("sha256")))
            span = (
                source.get("sha256"),
                quote.get("line_start"),
                quote.get("line_end"),
                quote.get("byte_start_in_first_line"),
                quote.get("byte_end_in_last_line"),
            )
            spans.setdefault(span, set()).add(text_id)
    merged = sorted(text_id for text_id, found in sources.items() if len(found) > 1)
    if merged:
        raise ValueError(f"text identities cover more than one source document: {merged}")
    split = sorted(sorted(found) for found in spans.values() if len(found) > 1)
    if split:
        raise ValueError(f"one quoted source span carries several text identities: {split}")


def check_licence_text_ids(
    payload: Mapping[str, Any],
    table: Mapping[str, str] | None = None,
) -> None:
    """Refuse a licence text identity table that disagrees with the determinations.

    Two different licence texts must not share one identity (that would mark
    one "changed" against the other with nothing revised), and one licence
    text must not carry two identities (a sibling left without its alias, the
    LIC4-3 defect). A licence text identity must not also name a binding text.
    Missing entries are refused where records are built.
    """
    if table is None:
        table = LICENCE_TEXT_IDS
    shown: list[tuple[str, str]] = []
    for entry in payload.get("entries") or []:
        shown.append((str(entry.get("entry_id")), licence_text_digest(entry)))
    shown.append((CONVERTER_ID, _converter_mit_digest(payload)))
    digests: dict[str, set[str]] = {}
    identities: dict[str, set[str]] = {}
    for record_id, digest in shown:
        text_id = table.get(record_id)
        if text_id is None:
            continue
        digests.setdefault(text_id, set()).add(digest)
        identities.setdefault(digest, set()).add(text_id)
    merged = sorted(text_id for text_id, found in digests.items() if len(found) > 1)
    if merged:
        raise ValueError(f"licence text identities cover more than one licence text: {merged}")
    split = sorted(sorted(found) for found in identities.values() if len(found) > 1)
    if split:
        raise ValueError(f"one licence text carries several text identities: {split}")
    shared = sorted(set(digests) & set(BINDING_TEXT_IDS.values()))
    if shared:
        raise ValueError(f"licence text identities also name binding texts: {shared}")


def generate_records(
    payload: Mapping[str, Any],
    *,
    pin: str,
    texts_dir: Path,
) -> list[LicenseRecord]:
    check_binding_text_ids(payload)
    check_licence_text_ids(payload)
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


def check_quote_texts(payload: Mapping[str, Any], texts_dir: Path) -> None:
    """Read-only counterpart of write_quote_texts (LIC3-3): every quote blob the
    records need must already be committed, byte-exact. Nothing is written."""
    problems = []
    for digest, data in sorted(quote_blobs(payload).items()):
        path = texts_dir / digest
        if not path.is_file():
            problems.append(f"missing {digest}")
        elif path.read_bytes() != data:
            problems.append(f"differs {digest}")
    if problems:
        raise HandEditedRecord(
            "committed quote texts are missing or differ: " + ", ".join(problems)
        )


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


