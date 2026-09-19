"""Known paraphrases that must never be stored or shown as licence text."""

from __future__ import annotations

from kilix_license.digest import sha256_hex
from kilix_license.errors import ParaphraseRefused

# kilix-ollama TERMS_SUMMARY (tts_artifacts.py). OD-AG: this paraphrase
# does not meet the Pocket prohibited-use binding.
POCKET_TERMS_SUMMARY = (
    "CC BY 4.0; credit Kyutai, preserve supplied notices, identify changes, "
    "and do not imply endorsement. Model use must be lawful and must not "
    "facilitate unauthorized voice impersonation, deception, or harmful or "
    "privacy-invasive content. Review the pinned upstream model card."
)

POCKET_TERMS_SUMMARY_BYTES = POCKET_TERMS_SUMMARY.encode("utf-8")
POCKET_TERMS_SUMMARY_SHA256 = sha256_hex(POCKET_TERMS_SUMMARY_BYTES)

_REFUSED = {
    POCKET_TERMS_SUMMARY_SHA256: "pocket-terms-summary",
}


def refuse_paraphrase(data: bytes, label: str = "text") -> None:
    digest = sha256_hex(data)
    name = _REFUSED.get(digest)
    if name is not None:
        raise ParaphraseRefused(f"{label} is refused paraphrase {name}")
    stripped = data.strip()
    if stripped and sha256_hex(stripped) in _REFUSED:
        raise ParaphraseRefused(
            f"{label} is refused paraphrase {_REFUSED[sha256_hex(stripped)]}"
        )
