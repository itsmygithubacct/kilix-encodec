"""Overlay that lets a 3747330 loader accept the current 24 kHz population.

The eight graphs stay byte-identical to the PyPI 0.1.1 export. The export
manifest cannot stay 02201a5a: the OD-AS MIT source pin moved pyproject.toml,
uv.lock and toolchain.encodec, and the SR-5 correction replaced the superseded
0.2.1 delivery, publication and licence strings with the OD-AR licence
record, which moved export_24khz.py's own digest. Reconstruct that frozen
manifest by putting exactly those values back, then hard-link the current
graphs beside it. A reconstruction that does not hash to 02201a5a means the
population changed by more than those enumerated fields.
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
LEGACY_EXPORTER_SHA256 = "e95ef4ba52b3b3a65c23098757b2dfaf1eaa2b1da9004d2762ef3333902e8783"
LEGACY_CHECKPOINT_DELIVERY = "user-supplied-only"
LEGACY_DERIVED_GRAPH_PUBLICATION = "forbidden-without-separate-model-grant"
LEGACY_LICENSE_DETERMINATION = "no-redistribution-grant-found"
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
    policy = value.get("artifact_policy")
    checkpoint = value.get("checkpoint")
    if not all(isinstance(item, dict) for item in (sources, toolchain, policy, checkpoint)):
        raise AssertionError("export manifest is missing sources, toolchain, policy or checkpoint")
    # OD-AS: the MIT source pin.
    sources["pyproject.toml"] = LEGACY_PYPROJECT_SHA256
    sources["uv.lock"] = LEGACY_UV_LOCK_SHA256
    toolchain["encodec"] = LEGACY_ENCODEC
    # SR-5: the OD-AR licence record and upstream delivery, and the exporter
    # digest that edit moved.
    sources["export_24khz.py"] = LEGACY_EXPORTER_SHA256
    policy["checkpoint_delivery"] = LEGACY_CHECKPOINT_DELIVERY
    policy["derived_graph_publication"] = LEGACY_DERIVED_GRAPH_PUBLICATION
    checkpoint["license_determination"] = LEGACY_LICENSE_DETERMINATION
    if "license" not in value:
        raise AssertionError("export manifest is missing its licence record")
    del value["license"]
    raw = canonical_json(value)
    digest = hashlib.sha256(raw).hexdigest()
    if digest != LEGACY_MANIFEST_SHA256 or len(raw) != LEGACY_MANIFEST_BYTES:
        raise AssertionError(
            "current population is not an OD-AS and SR-5 successor of 02201a5a: "
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
