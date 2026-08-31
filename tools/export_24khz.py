#!/usr/bin/env python3
"""Export the state-explicit 24 kHz EnCodec bundle into scratch storage."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import platform
import subprocess
from pathlib import Path
from typing import Any


TOOL_VERSION = "0.1.2"
REPOSITORY = Path(__file__).resolve().parents[1]
GRAPH_FILES = {
    "encoder": "encoder_stateful_op17.onnx",
    "decoder": "decoder_stateful_op17.onnx",
    "rvq_encode": "rvq_encode_op17.onnx",
    "rvq_decode": "rvq_decode_op17.onnx",
}


def sha256(path: Path) -> str:
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


def prepare_output_directory(path: Path) -> Path:
    output = outside_repository(path, "output directory")
    if output.exists():
        if output.is_symlink() or not output.is_dir():
            raise ValueError("output path must be a non-symlink directory")
        members = list(output.iterdir())
        if members:
            raise ValueError(f"output directory must be empty: {len(members)}/0 entries")
    else:
        output.mkdir(mode=0o700, parents=False)
    return output


def graph_record(path: Path, details: dict[str, Any]) -> dict[str, Any]:
    details.update(
        {
            "bytes": path.stat().st_size,
            "file": path.name,
            "sha256": sha256(path),
        }
    )
    os.chmod(path, 0o600)
    return details


def export_stateful(model: object, side: str, output: Path) -> dict[str, Any]:
    import onnx
    import torch

    from stateful_graph import (
        OPSET,
        PACKET_LATENT_FRAMES,
        PACKET_SAMPLES,
        iter_state_rows,
        stateful_network,
    )

    network = stateful_network(model, side)
    initial = network.initial_state()
    if side == "encoder":
        sample = torch.zeros(1, 1, PACKET_SAMPLES)
        value_name, output_name = "audio", "latent"
        expected_output = [1, 128, PACKET_LATENT_FRAMES]
    else:
        sample = torch.zeros(1, 128, PACKET_LATENT_FRAMES)
        value_name, output_name = "quantized", "audio"
        expected_output = [1, 1, PACKET_SAMPLES]
    path = output / GRAPH_FILES[side]
    torch.onnx.export(
        network,
        (sample, *initial),
        path,
        input_names=[value_name, *network.state_input_names],
        output_names=[output_name, *network.state_output_names],
        opset_version=OPSET,
        do_constant_folding=True,
        dynamo=False,
    )
    onnx.checker.check_model(onnx.load(path), full_check=True)
    print(f"{side}: states={len(network.state_specs)}/{len(network.state_specs)}")
    for row in iter_state_rows(network):
        print(f"  {row}")
    return graph_record(
        path,
        {
            "input_name": value_name,
            "input_shape": list(sample.shape),
            "opset": OPSET,
            "output_name": output_name,
            "output_shape": expected_output,
            "state_inputs": [
                {"name": name, "shape": list(spec.shape)}
                for name, spec in zip(network.state_input_names, network.state_specs)
            ],
            "state_outputs": [
                {"name": name, "shape": list(spec.shape)}
                for name, spec in zip(network.state_output_names, network.state_specs)
            ],
        },
    )


def export_rvq(model: object, direction: str, output: Path) -> dict[str, Any]:
    import onnx
    import torch

    from stateful_graph import OPSET, PACKET_LATENT_FRAMES

    class RVQEncode(torch.nn.Module):
        def __init__(self, quantizer: object):
            super().__init__()
            self.quantizer = quantizer

        def forward(self, latent: torch.Tensor) -> torch.Tensor:
            return self.quantizer.encode(latent, n_q=8)

    class RVQDecode(torch.nn.Module):
        def __init__(self, quantizer: object):
            super().__init__()
            self.quantizer = quantizer

        def forward(self, codes: torch.Tensor) -> torch.Tensor:
            return self.quantizer.decode(codes)

    if direction == "encode":
        network = RVQEncode(model.quantizer.vq).eval()
        sample = torch.zeros(1, 128, PACKET_LATENT_FRAMES)
        input_name, output_name = "latent", "codes"
        output_shape = [8, 1, PACKET_LATENT_FRAMES]
        output_dtype = "int64"
    elif direction == "decode":
        network = RVQDecode(model.quantizer.vq).eval()
        sample = torch.zeros(8, 1, PACKET_LATENT_FRAMES, dtype=torch.int64)
        input_name, output_name = "codes", "quantized"
        output_shape = [1, 128, PACKET_LATENT_FRAMES]
        output_dtype = "float32"
    else:
        raise ValueError(f"unsupported RVQ direction: {direction}")

    key = f"rvq_{direction}"
    path = output / GRAPH_FILES[key]
    torch.onnx.export(
        network,
        (sample,),
        path,
        input_names=[input_name],
        output_names=[output_name],
        opset_version=OPSET,
        do_constant_folding=True,
        dynamo=False,
    )
    onnx.checker.check_model(onnx.load(path), full_check=True)
    return graph_record(
        path,
        {
            "input_name": input_name,
            "input_shape": list(sample.shape),
            "opset": OPSET,
            "output_dtype": output_dtype,
            "output_name": output_name,
            "output_shape": output_shape,
            "quantizers": 8,
        },
    )


def uv_version() -> str:
    completed = subprocess.run(
        ["uv", "--version"], check=True, capture_output=True, text=True
    )
    return completed.stdout.strip()


def export_bundle(checkpoint: Path, output_path: Path) -> Path:
    import onnx
    import torch

    from stateful_graph import (
        PACKET_LATENT_FRAMES,
        PACKET_SAMPLES,
        SAMPLE_RATE,
        TARGET_BANDWIDTH,
        load_model,
    )

    checkpoint = outside_repository(checkpoint, "checkpoint")
    output = prepare_output_directory(output_path)
    model, identity = load_model(checkpoint)
    records = {
        "encoder": export_stateful(model, "encoder", output),
        "decoder": export_stateful(model, "decoder", output),
        "rvq_encode": export_rvq(model, "encode", output),
        "rvq_decode": export_rvq(model, "decode", output),
    }
    manifest = {
        "artifact_policy": {
            "checkpoint_delivery": "user-supplied-only",
            "derived_graph_publication": "forbidden-without-separate-model-grant",
            "native_runtime_downloads": False,
            "release_qualified": False,
        },
        "checkpoint": {
            "bytes": identity.bytes,
            "file": identity.file,
            "license_determination": "no-redistribution-grant-found",
            "sha256": identity.sha256,
        },
        "graphs": records,
        "initial_state": "all-zero",
        "packet": {
            "latent_frames": PACKET_LATENT_FRAMES,
            "sample_rate": SAMPLE_RATE,
            "samples": PACKET_SAMPLES,
            "target_bandwidth_kbps": TARGET_BANDWIDTH,
        },
        "padding_mode": "constant",
        "profile": "encodec-24khz-causal-mono-6kbps",
        "schema": "kilix.encodec.stateful-onnx-export/v1",
        "sources": {
            "export_24khz.py": sha256(Path(__file__).resolve()),
            "pyproject.toml": sha256(REPOSITORY / "pyproject.toml"),
            "stateful_graph.py": sha256(REPOSITORY / "tools/stateful_graph.py"),
            "uv.lock": sha256(REPOSITORY / "uv.lock"),
        },
        "toolchain": {
            "encodec": importlib.metadata.version("encodec"),
            "onnx": onnx.__version__,
            "onnxruntime": importlib.metadata.version("onnxruntime"),
            "python": platform.python_version(),
            "torch": torch.__version__,
            "uv": uv_version(),
        },
    }
    manifest_path = output / "manifest.json"
    manifest_path.write_bytes(canonical_json(manifest))
    os.chmod(manifest_path, 0o600)
    print(
        f"export bundle: graphs={len(records)}/4 manifest=1/1 "
        f"path={manifest_path} sha256={sha256(manifest_path)}"
    )
    print("publication: 0/1 authorized; derived artifacts remain local-only")
    return manifest_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", action="store_true")
    parser.add_argument("--checkpoint", type=Path)
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    if args.version:
        print(f"kilix-encodec export tool {TOOL_VERSION}")
        return 0
    if args.checkpoint is None or args.output_dir is None:
        parser.error("--checkpoint and --output-dir are required")
    try:
        export_bundle(args.checkpoint, args.output_dir)
    except (OSError, RuntimeError, ValueError) as error:
        parser.exit(1, f"export refused: {error}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
