"""Overlay that lets a 3747330 loader accept the OD-AS 24 kHz population.

The eight graphs stay byte-identical to the PyPI 0.1.1 export. The export
manifest records pyproject.toml, uv.lock and toolchain.encodec, so it cannot
stay 02201a5a after the MIT source pin. Reconstruct that frozen manifest by
substituting those three fields, then hard-link the current graphs beside it.
A reconstruction that does not hash to 02201a5a means the population changed
by more than the OD-AS pin.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any


LEGACY_MANIFEST_SHA256 = "02201a5a947dc0a0b9cce84d585eca35fb8a7e57aee4d404d5cb14f495f40b6c"
LEGACY_MANIFEST_BYTES = 12768
LEGACY_PYPROJECT_SHA256 = "3937fe9804948e25e3550bb6f5d576ad50d2c35b8f3e588427ba05382d16086e"
LEGACY_UV_LOCK_SHA256 = "5fcb6bcca9a8d6cf1674a3a063bef7d863485d3736beeb97dc19624f453d071c"
LEGACY_ENCODEC = "0.1.1"
GRAPH_FILES = (
    "encoder_stateful_op17.onnx",
    "decoder_stateful_op17.onnx",
    "rvq_encode_3kbps_op17.onnx",
    "rvq_decode_3kbps_op17.onnx",
    "rvq_encode_6kbps_op17.onnx",
    "rvq_decode_6kbps_op17.onnx",
    "rvq_encode_12kbps_op17.onnx",
    "rvq_decode_12kbps_op17.onnx",
)


def canonical_json(value: dict[str, Any]) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")


def reconstruct_02201a5a(manifest: dict[str, Any]) -> bytes:
    value = json.loads(json.dumps(manifest))
    sources = value.get("sources")
    toolchain = value.get("toolchain")
    if not isinstance(sources, dict) or not isinstance(toolchain, dict):
        raise AssertionError("export manifest is missing sources or toolchain")
    sources["pyproject.toml"] = LEGACY_PYPROJECT_SHA256
    sources["uv.lock"] = LEGACY_UV_LOCK_SHA256
    toolchain["encodec"] = LEGACY_ENCODEC
    raw = canonical_json(value)
    digest = hashlib.sha256(raw).hexdigest()
    if digest != LEGACY_MANIFEST_SHA256 or len(raw) != LEGACY_MANIFEST_BYTES:
        raise AssertionError(
            "OD-AS population is not a three-field successor of 02201a5a: "
            f"sha256={digest} bytes={len(raw)}"
        )
    return raw


def materialize(bundle: Path, destination: Path) -> Path:
    """Write destination/ with the 02201a5a manifest and hard-linked graphs."""
    destination.mkdir(parents=True, exist_ok=True)
    raw = reconstruct_02201a5a(json.loads((bundle / "manifest.json").read_bytes()))
    (destination / "manifest.json").write_bytes(raw)
    os.chmod(destination / "manifest.json", 0o600)
    for name in GRAPH_FILES:
        source = bundle / name
        if not source.is_file() or source.is_symlink():
            raise AssertionError(f"current population is missing graph {name}")
        target = destination / name
        try:
            os.link(source, target)
        except OSError:
            target.write_bytes(source.read_bytes())
            os.chmod(target, 0o600)
    return destination
