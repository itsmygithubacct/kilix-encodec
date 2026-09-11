"""Local first-use license-review for the 24 kHz EnCodec checkpoint.

The exporter never downloads or copies Meta's checkpoint. Opening or exporting
it requires a recorded local attestation: the path and digest of the license
text the user reviewed, a timestamp, and the user-supplied checkpoint path.
The native runtime does not download and does not read this attestation.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPOSITORY = Path(__file__).resolve().parents[1]
SCHEMA = "kilix.encodec.24khz-license-review/v1"
ATTESTATION_ENV = "KENC_24KHZ_LICENSE_ATTESTATION"
TIMESTAMP_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$")
CHECKPOINT_FILE = "encodec_24khz-d7cc33bc.th"
CHECKPOINT_BYTES = 93_171_529
CHECKPOINT_SHA256 = (
    "d7cc33bcf1aad7f2dad9836f36431530744abeace3ca033005e3290ed4fa47bf"
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_json(value: dict[str, Any]) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")


def outside_repository(path: Path, label: str) -> Path:
    if path.is_symlink():
        raise ValueError(f"{label} must not be a symlink")
    resolved = path.resolve(strict=False)
    if resolved == REPOSITORY or resolved.is_relative_to(REPOSITORY):
        raise ValueError(f"{label} must be outside the Git repository")
    return resolved


def _regular_file(path: Path, label: str) -> Path:
    resolved = outside_repository(path, label)
    try:
        path_stat = resolved.lstat()
    except FileNotFoundError as error:
        raise ValueError(f"{label} does not exist: {resolved}") from error
    if stat.S_ISLNK(path_stat.st_mode) or not stat.S_ISREG(path_stat.st_mode):
        raise ValueError(f"{label} must be a non-symlink regular file")
    return resolved


def sidecar_attestation_path(checkpoint: Path) -> Path:
    return checkpoint.with_name(checkpoint.name + ".license-review.json")


def resolve_attestation_path(
    checkpoint: Path, attestation: Path | None = None
) -> Path:
    if attestation is not None:
        return outside_repository(attestation, "license-review attestation")
    env = os.environ.get(ATTESTATION_ENV)
    if env:
        return outside_repository(Path(env), "license-review attestation")
    return sidecar_attestation_path(checkpoint)


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


def record_license_review(
    checkpoint: Path,
    license_text: Path,
    attestation: Path | None = None,
    reviewed_at: str | None = None,
) -> Path:
    """Record a local review. Does not copy or fetch the checkpoint."""

    checkpoint_path = outside_repository(checkpoint, "checkpoint")
    license_path = _regular_file(license_text, "reviewed license text")
    if license_path.stat().st_size == 0:
        raise ValueError("reviewed license text must be nonempty")
    digest = sha256_file(license_path)
    timestamp = reviewed_at or utc_timestamp()
    if TIMESTAMP_RE.fullmatch(timestamp) is None:
        raise ValueError("reviewed_at must be UTC YYYY-MM-DDTHH:MM:SSZ")
    payload = {
        "checkpoint_path": str(checkpoint_path),
        "license_text_path": str(license_path),
        "license_text_sha256": digest,
        "reviewed_at": timestamp,
        "schema": SCHEMA,
    }
    output = resolve_attestation_path(checkpoint_path, attestation)
    output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(output, flags, 0o600)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(canonical_json(payload))
    return output


def require_license_review(
    checkpoint: Path, attestation: Path | None = None
) -> dict[str, Any]:
    """Refuse unless a recorded local license-review attestation is present."""

    checkpoint_path = outside_repository(checkpoint, "checkpoint")
    attestation_path = resolve_attestation_path(checkpoint_path, attestation)
    if not attestation_path.exists():
        raise ValueError(
            "license-review attestation is required before opening or "
            "exporting the 24 kHz checkpoint"
        )
    path = _regular_file(attestation_path, "license-review attestation")
    raw = path.read_bytes()
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as error:
        raise ValueError("license-review attestation is not JSON") from error
    if not isinstance(value, dict) or raw != canonical_json(value):
        raise ValueError("license-review attestation must be canonical JSON")
    if value.get("schema") != SCHEMA:
        raise ValueError("license-review attestation schema differs")
    if value.get("checkpoint_path") != str(checkpoint_path):
        raise ValueError("license-review attestation checkpoint path differs")
    timestamp = value.get("reviewed_at")
    if not isinstance(timestamp, str) or TIMESTAMP_RE.fullmatch(timestamp) is None:
        raise ValueError("license-review attestation timestamp differs")
    license_field = value.get("license_text_path")
    digest_field = value.get("license_text_sha256")
    if not isinstance(license_field, str) or not isinstance(digest_field, str):
        raise ValueError("license-review attestation is missing license identity")
    license_path = _regular_file(Path(license_field), "reviewed license text")
    if str(license_path) != license_field:
        raise ValueError("reviewed license text path is not resolved")
    actual = sha256_file(license_path)
    if actual != digest_field:
        raise ValueError("reviewed license text digest differs")
    return value


def verify_user_supplied_checkpoint(checkpoint: Path) -> None:
    """Existing user-supplied identity checks, still with no network."""

    try:
        path_stat = checkpoint.lstat()
    except FileNotFoundError as error:
        raise ValueError(f"checkpoint does not exist: {checkpoint}") from error
    if stat.S_ISLNK(path_stat.st_mode) or not stat.S_ISREG(path_stat.st_mode):
        raise ValueError("checkpoint must be a non-symlink regular file")
    if checkpoint.name != CHECKPOINT_FILE:
        raise ValueError(f"checkpoint filename must be {CHECKPOINT_FILE}")
    if path_stat.st_size != CHECKPOINT_BYTES:
        raise ValueError(
            f"checkpoint size mismatch: {path_stat.st_size} != {CHECKPOINT_BYTES}"
        )
    actual = sha256_file(checkpoint)
    if actual != CHECKPOINT_SHA256:
        raise ValueError(
            f"checkpoint digest mismatch: {actual} != {CHECKPOINT_SHA256}"
        )
