"""Canonical JSON and sha256 helpers."""

from __future__ import annotations

from collections.abc import Mapping
import hashlib
import json
from typing import Any

_HEX = frozenset("0123456789abcdef")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def require_sha256(value: Any, label: str) -> str:
    if not isinstance(value, str) or len(value) != 64 or set(value) - _HEX:
        raise ValueError(f"{label} must be 64 lowercase hex characters")
    return value


def canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def load_json_object(data: bytes, label: str) -> dict[str, Any]:
    def _object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, item in pairs:
            if key in result:
                raise ValueError(f"{label} contains duplicate field {key!r}")
            result[key] = item
        return result

    try:
        parsed = json.loads(data.decode("utf-8"), object_pairs_hook=_object_pairs)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} is not UTF-8 JSON") from exc
    if not isinstance(parsed, dict):
        raise ValueError(f"{label} must be a JSON object")
    return parsed


def mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be an object")
    return value
