"""Generate licence records from the pinned determinations JSON.

Licence file bytes are never retyped: they are copied into data/texts/
named by sha256. Quote-backed texts are the JSON `text` field, which the
determinations generator (R0-DET-R2; R3 for the OD-AY PDF-engine entries) cut
from cited source spans. A hand-edited committed record fails --check.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any
import re

from kilix_license.digest import (
    canonical_json,
    load_json_object,
    require_sha256,
    sha256_hex,
)
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
@dataclass(frozen=True)
class QuotedSource:
    """One "quoted from" block of an advisory note: file, digest, lines taken."""

    path: str
    sha256: str
    lines: tuple[int, ...]


# LIC5 (C4-VERIFY F2, R4-068): the advisory the determinations quote for the two
# EnCodec records is OD-AR's builder-facing *specification* of the screen. It
# promises "a short verbatim-sourced licence-history note" and stands exactly
# where that note belongs, so the screen makes a promise it does not keep. The
# note itself is the text below. It is vendored into data/texts/ the way the
# licence files were vendored at LIC2, and every line under a "quoted from"
# header in it is copied byte for byte out of the L-ENC-R2 evidence packet
# (licence-evidence-encodec-errata-2026-09-17, sha256sum -c SHA256SUMS 33/33).
# No licence statement is retyped or paraphrased.
#
# Keyed by the determinations quote_id, so exactly the entries that carry that
# advisory show the note, and a renamed quote cannot silently keep the stale
# text: check_advisory_texts() refuses a key no determinations quote uses.
#
# Not bound (OD-AI, OD-AQ): an advisory digest is outside the record digest, so
# this changes no receipt's coverage. It is recorded as receipt context.
ADVISORY_TEXTS = {
    "encodec-licence-history-note": (
        "8cfc463c41113f776eebc60642a0e4f9841aaff247d9659d8abc466743755260"
    ),
}
# LIC5-FIX (LIC5-VERIFY F1, F2, F3, F5): what each replacement note quotes, as
# data rather than as prose in a comment and a literal in a test.
#
# F1: the note tells the user every line under a "quoted from" header is copied
# byte for byte from the source that header names. Nothing enforced that. The
# note's own headers are now parsed (parse_quoted_blocks) and must equal this
# table, so a note whose header claims one slice while quoting another is
# refused here; and tests/data/note-sources/<sha256> holds each source file, so
# the suite re-derives every quoted line from the named source and compares
# bytes. A corrupted licence sentence no longer ships green.
#
# F3: one table. A block added to or dropped from a note is a change here and
# nowhere else -- the note's sources and the tests that check them cannot
# disagree, because the tests read this.
#
# F2/F5: the second block was OWNER-DECISIONS-2026-09-12.md lines 738-740, the
# owner's internal decision text, rendered on a user-facing screen with markdown
# bold, "Owner's answer:" and backticked field names -- the pattern C4-VERIFY F4
# named -- and citing a digest that matches no file on disk. It is replaced by
# section 9 of the file the note already quoted: facebookresearch/audiocraft,
# where upstream states a licence for model weights separately from the licence
# for code, next to encodec's own statements about "this repository" and "the
# code". Every digest the note cites now resolves to a file that exists.
ADVISORY_TEXT_SOURCES = {
    "8cfc463c41113f776eebc60642a0e4f9841aaff247d9659d8abc466743755260": (
        # The 2022 CC BY-NC state and the 2023 MIT relicensing, as the upstream
        # READMEs state them (L-ENC-R2 sources/quotes.json key PACKET_HISTORY
        # records this path and this digest, and lines 14 and 18 themselves).
        QuotedSource(
            path=(
                "licence-evidence-encodec-2026-09-15/sources/"
                "upstream-licence-history.txt"
            ),
            sha256="1ce36c87223cc7a1cdd052a876440abd48e11604ffc0037a2b4afadef6da1e90",
            lines=(14, 18),
        ),
        # Section 9 of the same file: facebookresearch/audiocraft.
        QuotedSource(
            path=(
                "licence-evidence-encodec-2026-09-15/sources/"
                "upstream-licence-history.txt"
            ),
            sha256="1ce36c87223cc7a1cdd052a876440abd48e11604ffc0037a2b4afadef6da1e90",
            lines=(91, 92, 93, 94),
        ),
    ),
}


def _authored_bytes(lines: tuple[str, ...]) -> bytes:
    """The assembled authored text: each line, in order, newline-terminated."""
    return "".join(f"{line}\n" for line in lines).encode("utf-8")


# LIC6-VERIFY F3, residual m11b. This is the pin, and it replaces detection.
#
# A note has two kinds of line. Quoted lines are re-derived from their named
# source byte for byte, so they cannot be fabricated. Authored lines -- the
# preamble and each block's three header lines -- are ours, and every guard
# they had before this was a DETECTOR: states_a_licence() looks for a licence
# identifier and the language of permission. A detector over a vocabulary can
# only ever catch the sentences somebody thought of. m11b walked straight past
# it with plain English -- "Meta later allowed everyone to reuse the encodec
# weights freely." -- on a screen whose binding condition is CC BY-NC 4.0,
# non-commercial, printed directly beneath it, with the whole suite green.
# No addition to that vocabulary closes the class, because the class is
# "sentences a reader believes", not "sentences containing a licence word".
#
# So the authored text is no longer screened. It is CONSTRAINED: exactly these
# lines, byte for byte, in this order, and nothing else may appear. A new or
# altered authored sentence fails whatever its wording, because the check is
# equality with a declaration rather than a search for something suspicious.
#
# Keyed by the note's own sha256, so the note and its authored text cannot be
# re-pointed independently. The digest over the assembled text is DERIVED
# below, never typed, so the two cannot drift.
#
# **To change what this authority says on a consent screen**, edit the tuple
# below in the same commit as the note; the refusal message states the four
# steps. A sentence that is not in this declaration does not reach a user.
ADVISORY_NOTE_AUTHORED = {
    "8cfc463c41113f776eebc60642a0e4f9841aaff247d9659d8abc466743755260": (
        # The preamble: what the note is, and what it promises the reader.
        'EnCodec licence history. Every line below a "quoted from" header is copied',
        "byte for byte from the source that header names; nothing here is retyped.",
        "The source file is pinned by licence-evidence-encodec-errata-2026-09-17",
        "(L-ENC-R2) sources/quotes.json, sha256 15d17ef65ae566134fe3fa238817211192019fb066717db675192a0a545a5dca.",
        # Block 1's three header lines.
        "quoted from licence-evidence-encodec-2026-09-15/sources/upstream-licence-history.txt",
        "sha256 1ce36c87223cc7a1cdd052a876440abd48e11604ffc0037a2b4afadef6da1e90, lines 14 and 18",
        "(facebookresearch/encodec LICENSE and README at the commits named):",
        # Block 2's three header lines, including LIC5-FIX-VERIFY F2's
        # orienting clause about which repository "this repository" is.
        "quoted from licence-evidence-encodec-2026-09-15/sources/upstream-licence-history.txt",
        "sha256 1ce36c87223cc7a1cdd052a876440abd48e11604ffc0037a2b4afadef6da1e90, lines 91 to 94",
        '(section 9 of that file: facebookresearch/audiocraft, an adjacent Meta repository that states a licence for model weights separately from the licence for code; "this repository" in the lines below is audiocraft, not encodec):',
    ),
}
# Derived from the declaration above, never typed: the two cannot disagree.
# This is the figure to quote when re-pinning a note downstream.
ADVISORY_NOTE_AUTHORED_SHA256 = {
    digest: sha256_hex(_authored_bytes(tuple(lines)))
    for digest, lines in ADVISORY_NOTE_AUTHORED.items()
}
# LIC6-FIX-VERIFY V1, mutant M10 -- the same defect one level up. The
# declaration above pins the note. Nothing pinned the declaration. M10 left
# every check in place and replaced the literal with a comprehension that
# reads the authored lines straight off the note file, so equality held by
# construction: `make records` exited 0, the suite stayed green, a false
# permissive sentence rendered on both EnCodec consent screens, and **no**
# added line outside the digest-named blob contained it. The test named after
# the property had become a tautology, which is this programme's recurring
# failure mode -- two things derived from one source, compared with each other.
#
# So the declaration's own content is pinned, by a digest that is TYPED here
# and derived nowhere. A declaration computed from the note cannot satisfy it:
# whatever it computes must hash to this figure, and the moment the note
# changes it does not. check_advisory_note_authored() enforces it, so the
# GENERATOR refuses, not only a test; and the suite additionally requires the
# declaration to be a literal in this file (tests/support/source_pins.py), so
# the pin cannot be satisfied by making the pinned thing computed.
#
# Updating it is one line, it is step 2 of the deliberate path below, and
# every refusal here prints the value to put in it.
ADVISORY_NOTE_AUTHORED_PIN = {
    "8cfc463c41113f776eebc60642a0e4f9841aaff247d9659d8abc466743755260": (
        "120dc2042a48e4d793d47dd4653ad0f52e7d27d443a2b9b3776a8b40bb67f5c8"
    ),
}
# The one place an authored line is declared, named in every refusal so the
# deliberate path is the obvious one.
AUTHORED_DECLARATION = (
    "ADVISORY_NOTE_AUTHORED in src/kilix_license/generate.py"
)
# LIC6-FIX-VERIFY V3. The four steps as they stood did not land a change:
# step 4 was `make records`, which is generate_records.py --check and never
# writes, so it refused; and a `git add` of the renamed note was needed by
# three tests that read the committed tree and was stated nowhere. An editor
# who followed them exactly got a refusal and then three failures. These five
# were walked end to end, in order, on a real wording change.
_AUTHORED_UPDATE = (
    f"Authored note prose is pinned, not screened: every line of a note that "
    f"nobody upstream wrote must appear byte for byte in "
    f"{AUTHORED_DECLARATION}, keyed by the note's sha256. This is the only "
    f"way to change what this authority says on a consent screen, and it is "
    f"meant to be a visible, reviewable edit: (1) edit the note under "
    f"src/kilix_license/data/texts/ and rename it to its new sha256; "
    f"(2) copy its authored lines -- the preamble and each block's three "
    f"header lines, in note order, blank lines omitted -- into "
    f"{AUTHORED_DECLARATION} under the new digest, and put the declaration's "
    f"own sha256, which this refusal prints, in ADVISORY_NOTE_AUTHORED_PIN "
    f"beside it AND in AUTHORED_PIN in tests/test_records.py, which is the "
    f"suite's independently typed copy of it; (3) point ADVISORY_TEXTS and "
    f"ADVISORY_TEXT_SOURCES at the "
    f"new digest; (4) run `make regenerate`, which WRITES the records -- "
    f"`make records` only checks them and refuses until they are written; "
    f"(5) `git add -A`, because the suite reads the committed tree and the "
    f"renamed note is a new path, then `make check`. "
    f"Do not widen a detector instead: a sentence absent from that "
    f"declaration never reaches a user, whatever its wording."
)

_QUOTED_FROM = "quoted from "
_QUOTED_SHA = re.compile(r"^sha256 ([0-9a-f]{64}), lines? (.+)$")
# LIC5-FIX-VERIFY F1, mutant M11. The guard above covers what a note *quotes*.
# A note also has authored lines -- the preamble, and each block's three header
# lines -- and M11 put "Meta relicensed the encodec weights to Apache-2.0 in
# 2024; commercial use is permitted." into the preamble of the text a user has
# to agree to, with the whole suite green. The note stayed literally true
# (its promise is scoped to lines *below* a "quoted from" header) and the screen
# carried a false licence claim anyway.
#
# The rule that closes it is the one the note is for: **licence statements on
# this screen come from upstream's bytes, never from us.** So no authored line
# in a note may make a licence claim. This is not a copy of the preamble's
# text, so a mutant cannot make it agree by editing a declaration: it has to
# stop claiming a licence, which is the whole point. It binds the header lines
# too, which is where LIC5-FIX-VERIFY F2's orienting clause now lives.
#
# What it looks for: a licence identifier, or the language of permission and
# relicensing. Bare "LICENSE"/"licence" is a filename and a topic, not a claim,
# and is not matched -- the note is titled "EnCodec licence history". The test
# proves the detector is live by requiring every quoted block to contain a line
# it flags, so a regex that had stopped matching would fail rather than pass.
_LICENCE_CLAIM = re.compile(
    r"(?i)\b(?:"
    r"MIT|ISC|Unlicense|WTFPL|Zlib|SPDX"
    r"|BSD[- ]\d[\w.-]*"
    r"|Apache(?:[- ]2(?:\.0)?)?"
    r"|(?:GNU[ -])?[AL]?GPL[\w.-]*"
    r"|MPL[- ]?\d[\w.]*"
    r"|CC[- ]?(?:BY|0)[\w.-]*"
    r"|Creative Commons"
    r"|non-?commercial|commercial use"
    r"|relicens\w+|re-licens\w+"
    r"|permissive|permitted|permits|public domain|royalty[- ]free"
    r"|released under|licensed under|licence is|license is"
    r")\b"
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
    # LIC6-FIX-VERIFY V3. These two are the first refusals an editor changing
    # an advisory note actually meets -- the digest mismatch before the
    # rename, and the missing file just after it -- and neither said how to
    # land the change; only the third refusal, two steps later, named the
    # path. Both point at it now, for advisory texts, which are the only ones
    # a human edits.
    hint = f" {_AUTHORED_UPDATE}" if label.startswith("advisory:") else ""
    path = texts_dir / digest
    if not path.is_file():
        raise FileNotFoundError(f"missing licence text {digest} ({label}).{hint}")
    data = path.read_bytes()
    if sha256_hex(data) != digest:
        raise TextDigestMismatch(
            f"{label} {digest} does not match stored bytes.{hint}"
        )
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
    *,
    texts_dir: Path | None = None,
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
            # LIC5: the note itself replaces the determinations quote where
            # ADVISORY_TEXTS names one. The replacement must be committed in
            # data/texts/ and match its digest, exactly as a licence file does.
            replacement = ADVISORY_TEXTS.get(quote_id)
            if replacement is not None:
                require_sha256(replacement, f"ADVISORY_TEXTS[{quote_id!r}]")
                if texts_dir is None:
                    raise ValueError(
                        f"{label}[{index}] advisory {quote_id!r} has a replacement "
                        "text but no texts_dir to verify it against"
                    )
                note = load_text_file(texts_dir, replacement, f"advisory:{item_id}")
                check_advisory_note_sources(replacement, note)
                digest = replacement
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
        advisories=_quotes_as(
            Advisory, notes, f"{entry_id}.advisories", texts_dir=texts_dir
        ),
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


def _quoted_line_numbers(spec: str, label: str) -> tuple[int, ...]:
    """Expand a header's line spec: "14 and 18", "91 to 94", or "75"."""
    for separator, contiguous in ((" and ", False), (" to ", True)):
        if separator in spec:
            parts = spec.split(separator)
            if len(parts) != 2 or not all(p.isdigit() for p in parts):
                raise ValueError(f"{label} has an unreadable line spec: {spec!r}")
            first, last = (int(p) for p in parts)
            if first < 1 or last <= first:
                raise ValueError(f"{label} has an unreadable line spec: {spec!r}")
            return tuple(range(first, last + 1)) if contiguous else (first, last)
    if spec.isdigit() and int(spec) >= 1:
        return (int(spec),)
    raise ValueError(f"{label} has an unreadable line spec: {spec!r}")


def parse_quoted_blocks(data: bytes, label: str = "advisory note") -> tuple[
    tuple[QuotedSource, bytes], ...
]:
    """The note's quoted blocks. See parse_advisory_note for the whole note."""
    return parse_advisory_note(data, label)[1]


def authored_lines(
    data: bytes, label: str = "advisory note"
) -> tuple[tuple[int, str], ...]:
    """Every line of an advisory note that nobody upstream wrote.

    That is: the preamble, and each block's three header lines. Blank lines are
    left out. Everything else in the note is a quoted line, whose bytes
    parse_advisory_note already re-derives from the named source.
    """
    return parse_advisory_note(data, label)[0]


def authored_text(authored: tuple[tuple[int, str], ...]) -> bytes:
    """The authored lines assembled into one text, each newline-terminated.

    The thing that is pinned. Line numbers are dropped, so re-wrapping the
    quoted blocks around unchanged prose is not a change to the authored text,
    while any change to the prose itself is.
    """
    return _authored_bytes(tuple(line for _number, line in authored))


def authored_digest(authored: tuple[tuple[int, str], ...]) -> str:
    """sha256 of :func:`authored_text`. Never typed; always derived."""
    return sha256_hex(authored_text(authored))


def check_advisory_note_authored(
    digest: str,
    authored: tuple[tuple[int, str], ...],
    table: Mapping[str, tuple[str, ...]] | None = None,
    pins: Mapping[str, str] | None = None,
) -> None:
    """Refuse a note whose authored text is not the text declared for it.

    LIC6-VERIFY F3 (residual m11b). The predecessor of this check screened
    authored lines for licence vocabulary, which a false sentence phrased in
    plain English walks straight past -- and did, on a consent screen, with
    the suite green. This does not screen. It requires the authored text to be
    exactly what ``ADVISORY_NOTE_AUTHORED`` declares, so a new or altered
    authored sentence is refused **whatever it says**; there is no wording
    that satisfies it except the declared wording.

    The quoted blocks are untouched by this: they keep the byte-for-byte
    re-derivation from their named source, so text appended after the last
    block is still swallowed into that block's quoted bytes and still fails
    there (LIC5 mutant M13).

    LIC6-FIX-VERIFY V1 (mutant M10) added the second half: the declaration's
    own content is checked against a **typed** digest before the note is
    compared with it, so a declaration that is derived from the note file --
    which makes the comparison below a tautology -- is refused here, in the
    generator, whatever the note says.
    """
    if table is None:
        table = ADVISORY_NOTE_AUTHORED
    declared_lines = table.get(digest)
    if declared_lines is None:
        raise ValueError(
            f"advisory note {digest} declares no authored text. "
            + _AUTHORED_UPDATE
        )
    declared = tuple(declared_lines)
    # V1, before anything is compared with the note: the declaration itself.
    if pins is None:
        pins = ADVISORY_NOTE_AUTHORED_PIN
    pinned = pins.get(digest)
    declared_digest = sha256_hex(_authored_bytes(declared))
    if pinned is None:
        raise ValueError(
            f"advisory note {digest} has an authored declaration with no "
            f"pinned digest. Its declared text hashes to {declared_digest}; "
            f"that is the value to put in ADVISORY_NOTE_AUTHORED_PIN. "
            + _AUTHORED_UPDATE
        )
    if declared_digest != pinned:
        raise ValueError(
            f"advisory note {digest} authored declaration does not match its "
            f"pinned digest: the declaration hashes to {declared_digest} and "
            f"ADVISORY_NOTE_AUTHORED_PIN declares {pinned}. The pin is typed, "
            f"never derived, so a declaration computed from the note file "
            f"fails here instead of agreeing with the note by construction. "
            + _AUTHORED_UPDATE
        )
    found = tuple(line for _number, line in authored)
    if found == declared:
        return
    numbers = [number for number, _line in authored]
    for position in range(max(len(found), len(declared))):
        mine = found[position] if position < len(found) else None
        theirs = declared[position] if position < len(declared) else None
        if mine == theirs:
            continue
        if mine is None:
            what = (
                f"the note has no {_ordinal(position + 1)} authored line, and "
                f"the declaration ends with {theirs!r}"
            )
        elif theirs is None:
            what = (
                f"note line {numbers[position]} is {mine!r}, which the "
                "declaration does not have at all"
            )
        else:
            what = (
                f"at note line {numbers[position]} the note has {mine!r} "
                f"and the declaration has {theirs!r}"
            )
        raise ValueError(
            f"advisory note {digest} authored text is not the text declared "
            f"for it ({len(found)} authored lines, declared {len(declared)}): "
            f"{what}. " + _AUTHORED_UPDATE
        )
    raise ValueError(  # pragma: no cover - equality already excluded above
        f"advisory note {digest} authored text differs from its declaration. "
        + _AUTHORED_UPDATE
    )


def _ordinal(number: int) -> str:
    if 10 <= number % 100 <= 20:
        return f"{number}th"
    return f"{number}{ {1: 'st', 2: 'nd', 3: 'rd'}.get(number % 10, 'th') }"


def parse_advisory_note(data: bytes, label: str = "advisory note") -> tuple[
    tuple[tuple[int, str], ...], tuple[tuple[QuotedSource, bytes], ...]
]:
    """Read an advisory note's "quoted from" blocks: what it claims, and what it quotes.

    LIC5-VERIFY F1. The note promises the user that every line under such a
    header is its named source's bytes. That promise is only checkable if the
    headers are machine-readable, so the shape is fixed here:

        quoted from <path>
        sha256 <64 hex>, lines <spec>
        (<what the source is>):
        <blank>
        <one or more quoted lines>

    and blocks are separated by one blank line. Returns two things: every
    authored line (the preamble, and each block's three header lines) as
    (1-based line number, text), and each header as a QuotedSource with the
    block's quoted bytes, so a caller with the source file can compare them
    line for line.
    """
    text = data.decode("utf-8")
    lines = text.split("\n")
    if not lines or lines[-1] != "":
        raise ValueError(f"{label} must end with a newline")
    lines.pop()
    starts = [i for i, line in enumerate(lines) if line.startswith(_QUOTED_FROM)]
    if not starts:
        raise ValueError(f"{label} has no {_QUOTED_FROM.strip()!r} header")
    quoted_index: set[int] = set()
    blocks: list[tuple[QuotedSource, bytes]] = []
    for position, start in enumerate(starts):
        where = f"{label} block {position + 1}"
        if start + 4 > len(lines):
            raise ValueError(f"{where} is truncated")
        path = lines[start][len(_QUOTED_FROM) :]
        if not path:
            raise ValueError(f"{where} names no source file")
        matched = _QUOTED_SHA.match(lines[start + 1])
        if matched is None:
            raise ValueError(f"{where} has no 'sha256 <digest>, lines ...' line")
        if not lines[start + 2].endswith("):"):
            raise ValueError(f"{where} has no '(...):' line")
        if lines[start + 3] != "":
            raise ValueError(f"{where} has no blank line before its quoted lines")
        if position + 1 < len(starts):
            stop = starts[position + 1] - 1
            if stop <= start + 3 or lines[stop] != "":
                raise ValueError(f"{where} is not separated from the next by a blank line")
        else:
            stop = len(lines)
        quoted = lines[start + 4 : stop]
        if not quoted or any(line == "" for line in quoted):
            raise ValueError(f"{where} quotes no lines, or quotes a blank line")
        quoted_index.update(range(start + 4, stop))
        blocks.append(
            (
                QuotedSource(
                    path=path,
                    sha256=matched.group(1),
                    lines=_quoted_line_numbers(matched.group(2), where),
                ),
                ("\n".join(quoted) + "\n").encode("utf-8"),
            )
        )
    if not any(line for index, line in enumerate(lines) if index < starts[0]):
        raise ValueError(f"{label} has no preamble before its first block")
    authored = tuple(
        (index + 1, line)
        for index, line in enumerate(lines)
        if index not in quoted_index and line != ""
    )
    return authored, tuple(blocks)


def check_advisory_note_sources(
    digest: str,
    data: bytes,
    table: Mapping[str, tuple[QuotedSource, ...]] | None = None,
) -> None:
    """Refuse a note whose headers disagree with the sources declared for it.

    LIC5-VERIFY F1: the note's header claimed "lines 14 and 18" while the bytes
    under it could be any other slice, and nothing noticed. ADVISORY_TEXT_SOURCES
    is the one declaration of what each note quotes; the note's own headers must
    match it, file, digest and line numbers. The quoted bytes are compared to the
    named source itself by the suite, which pins the source file under
    tests/data/note-sources/.
    """
    if table is None:
        table = ADVISORY_TEXT_SOURCES
    declared = table.get(digest)
    if declared is None:
        raise ValueError(f"advisory replacement text {digest} declares no sources")
    authored, blocks = parse_advisory_note(data, f"advisory note {digest}")
    found = tuple(source for source, _ in blocks)
    if found != tuple(declared):
        raise ValueError(
            f"advisory note {digest} quotes {found}, "
            f"but ADVISORY_TEXT_SOURCES declares {tuple(declared)}"
        )
    # What the note QUOTES is settled above. What the note SAYS ITSELF is
    # settled by the pin, and then screened once more for a licence claim --
    # the pin is what closes m11b, and the screen is kept so that a licence
    # identifier is refused at the declaration too, rather than only being
    # unequal to it (LIC5-FIX-VERIFY F1, mutant M11).
    check_advisory_note_authored(digest, authored)
    check_advisory_note_prose(digest, authored)


def states_a_licence(line: str) -> str | None:
    """The licence claim in one line of authored note prose, or None.

    Bare "licence"/"LICENSE" is a topic and a filename, not a claim; what is
    matched is a licence identifier, or the language of permission and
    relicensing. The suite proves this is live by requiring every quoted block
    in a shipped note to contain a line it flags.
    """
    found = _LICENCE_CLAIM.search(line)
    return found.group(0) if found is not None else None


def check_advisory_note_prose(
    digest: str, authored: tuple[tuple[int, str], ...]
) -> None:
    """Refuse a note that states a licence in a line nobody upstream wrote.

    LIC5-FIX-VERIFY F1 (mutant M11). The note's quoted blocks are re-derived
    from their named sources byte for byte; its authored lines were checked by
    nothing, and a fabricated "Apache-2.0 ... commercial use is permitted"
    shipped green on the accept screen. An advisory note exists to carry what
    upstream said; if a line is ours, it may introduce, attribute and cite, and
    it may not say what the licence is.
    """
    claims = [
        (number, line, states_a_licence(line))
        for number, line in authored
        if states_a_licence(line) is not None
    ]
    if claims:
        number, line, hit = claims[0]
        raise ValueError(
            f"advisory note {digest} line {number} is not quoted from any "
            f"source and states a licence ({hit!r}): {line!r}. Only lines "
            "under a 'quoted from' header may say what a licence is."
        )


def check_advisory_texts(
    payload: Mapping[str, Any],
    table: Mapping[str, str] | None = None,
) -> None:
    """Refuse an advisory replacement the determinations no longer quote (LIC5).

    ADVISORY_TEXTS names the note a determinations advisory quote is replaced
    by. A key that matches no advisory or note quote is a stale override: the
    quote was renamed or dropped, and the screen would silently fall back to
    the text the replacement exists to keep off it. Every value must be a
    sha256. Missing keys are not refused: an advisory without a replacement is
    shown as the determinations quote it, which is the normal case.
    """
    if table is None:
        table = ADVISORY_TEXTS
    quoted: set[str] = set()
    for entry in payload.get("entries") or []:
        for key in ("advisories", "notes"):
            for quote in entry.get(key) or []:
                if isinstance(quote, dict) and isinstance(quote.get("quote_id"), str):
                    quoted.add(quote["quote_id"])
    for quote_id, digest in sorted(table.items()):
        require_sha256(digest, f"ADVISORY_TEXTS[{quote_id!r}]")
    unused = sorted(set(table) - quoted)
    if unused:
        raise ValueError(
            f"advisory replacement texts name no determinations quote: {unused}"
        )
    # LIC5-FIX (F1/F3): a note with no declared sources cannot be checked
    # against them, so it is refused before it can reach a screen.
    undeclared = sorted(set(table.values()) - set(ADVISORY_TEXT_SOURCES))
    if undeclared:
        raise ValueError(
            f"advisory replacement texts declare no quoted sources: {undeclared}"
        )
    # LIC6-FIX (LIC6-VERIFY F3): and a note whose own authored prose is
    # undeclared is unchecked prose on a consent screen, so it is refused
    # here too rather than reaching a screen as a new, unpinned note.
    # V1: a note needs both halves -- the declaration and the typed digest of
    # the declaration -- or the pin is satisfied by whatever computes it.
    pinned_notes = set(ADVISORY_NOTE_AUTHORED) & set(ADVISORY_NOTE_AUTHORED_PIN)
    unpinned = sorted(set(table.values()) - pinned_notes)
    if unpinned:
        raise ValueError(
            f"advisory replacement texts declare no authored text: {unpinned}. "
            + _AUTHORED_UPDATE
        )


def generate_records(
    payload: Mapping[str, Any],
    *,
    pin: str,
    texts_dir: Path,
) -> list[LicenseRecord]:
    check_binding_text_ids(payload)
    check_licence_text_ids(payload)
    check_advisory_texts(payload)
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


