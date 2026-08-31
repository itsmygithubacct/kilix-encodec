#!/usr/bin/env python3
"""Export the bounded 48 kHz stereo file profile into scratch storage."""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import os
import platform
import tempfile
from pathlib import Path
from typing import Any, Callable

from export_24khz import (
    REPOSITORY,
    canonical_json,
    outside_repository,
    prepare_output_directory,
    sha256,
    uv_version,
)


TOOL_VERSION = "0.1.2"
MODEL_REVISION = "c3def8e7185ac8c8efdce6eb8c4a651e487a503e"
MODEL_FILES = {
    "config.json": (803, "4a914ed15ed5a69e19932d05b0c51f2d22c68ffac70e959a757594cb0cd6e2a7"),
    "model.safetensors": (
        76_291_152,
        "47a15ffbaf7bb76176d0833e10590de0a8988a7848748608cefc36a1c88adfdc",
    ),
    "preprocessor_config.json": (
        231,
        "df1268bac588b486545baa1d0b6c9c32366c817efbd113158b4c2ef74f6eefeb",
    ),
}
SAMPLE_RATE = 48_000
CHANNELS = 2
FRAME_SAMPLES = 48_000
STRIDE_SAMPLES = 47_520
OVERLAP_SAMPLES = 480
LATENT_FRAMES = 150
LATENT_DIMENSION = 128
CODEBOOKS = 16
CODEBOOK_SIZE = 1_024
OPSET = 17


def verify_model_directory(path: Path) -> tuple[Path, dict[str, Any]]:
    directory = outside_repository(path, "model directory")
    if directory.is_symlink() or not directory.is_dir():
        raise ValueError("model directory must be a non-symlink directory")
    unsafe = sorted(
        child.name
        for child in directory.iterdir()
        if child.suffix.lower() in {".bin", ".ckpt", ".pickle", ".pkl", ".pt", ".pth"}
    )
    if unsafe:
        raise ValueError(f"unsafe pickle-capable model files present: {len(unsafe)}/0")
    records: dict[str, Any] = {}
    for name, (expected_bytes, expected_sha256) in MODEL_FILES.items():
        file_path = directory / name
        if file_path.is_symlink() or not file_path.is_file():
            raise ValueError(f"model input must be a non-symlink regular file: {name}")
        actual_bytes = file_path.stat().st_size
        actual_sha256 = sha256(file_path)
        if actual_bytes != expected_bytes or actual_sha256 != expected_sha256:
            raise ValueError(f"model input identity differs: {name}")
        records[name] = {"bytes": actual_bytes, "sha256": actual_sha256}
    return directory, records


def refused(action: Callable[[], object]) -> bool:
    try:
        action()
    except ValueError:
        return True
    return False


def policy_self_test() -> None:
    scratch = os.environ.get("TMPDIR", "/home/pleb/scratch-workers")
    with tempfile.TemporaryDirectory(
        prefix="kilix-encodec-48khz-policy-", dir=scratch
    ) as temporary:
        root = Path(temporary)
        symlink_target = root / "model-target"
        symlink_target.mkdir()
        model_symlink = root / "model-symlink"
        model_symlink.symlink_to(symlink_target, target_is_directory=True)

        unsafe_model = root / "unsafe-model"
        unsafe_model.mkdir()
        (unsafe_model / "model.bin").write_bytes(b"pickle-capable")

        mismatched_model = root / "mismatched-model"
        mismatched_model.mkdir()
        (mismatched_model / "config.json").write_bytes(b"{}")

        nonempty_output = root / "nonempty-output"
        nonempty_output.mkdir()
        (nonempty_output / "member").write_bytes(b"occupied")

        output_target = root / "output-target"
        output_target.mkdir()
        output_symlink = root / "output-symlink"
        output_symlink.symlink_to(output_target, target_is_directory=True)

        cases = (
            lambda: verify_model_directory(REPOSITORY),
            lambda: verify_model_directory(root / "missing-model"),
            lambda: verify_model_directory(model_symlink),
            lambda: verify_model_directory(unsafe_model),
            lambda: verify_model_directory(mismatched_model),
            lambda: prepare_output_directory(REPOSITORY / "generated"),
            lambda: prepare_output_directory(nonempty_output),
            lambda: prepare_output_directory(output_symlink),
        )
        passed = sum(refused(case) for case in cases)
        if passed != len(cases):
            raise RuntimeError(
                f"48 kHz policy self-test failed: {passed}/{len(cases)}"
            )
        print(f"48 kHz policy refusals: {passed}/{len(cases)} PASS")


def load_model(model_directory: Path) -> tuple[object, dict[str, Any]]:
    from transformers import EncodecModel

    directory, records = verify_model_directory(model_directory)
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    model = EncodecModel.from_pretrained(
        directory,
        local_files_only=True,
        use_safetensors=True,
    )
    model.eval()
    expected = {
        "audio_channels": CHANNELS,
        "chunk_length": FRAME_SAMPLES,
        "chunk_stride": STRIDE_SAMPLES,
        "codebook_size": CODEBOOK_SIZE,
        "frame_rate": LATENT_FRAMES,
        "hidden_size": LATENT_DIMENSION,
        "normalize": True,
        "sampling_rate": SAMPLE_RATE,
        "use_causal_conv": False,
    }
    actual = {name: getattr(model.config, name) for name in expected}
    if actual != expected:
        raise ValueError(f"48 kHz model contract differs: {actual!r}")
    if model.config.target_bandwidths != [3.0, 6.0, 12.0, 24.0]:
        raise ValueError("48 kHz bandwidth set differs")
    if model.config.num_quantizers != CODEBOOKS:
        raise ValueError("48 kHz codebook count differs")
    return model, records


def encoder_frame_type() -> type:
    import torch
    from torch import Tensor, nn

    class EncoderFrame(nn.Module):
        def __init__(self, model: object):
            super().__init__()
            self.encoder = model.encoder

        def forward(self, audio: Tensor) -> tuple[Tensor, Tensor]:
            mono = torch.sum(audio, 1, keepdim=True) / 2
            scale = mono.pow(2).mean(dim=-1, keepdim=True).sqrt() + 1e-8
            latent = self.encoder(audio / scale)
            return latent, scale.reshape(-1, 1)

    return EncoderFrame


def decoder_frame_type() -> type:
    from torch import Tensor, nn

    class DecoderFrame(nn.Module):
        def __init__(self, model: object):
            super().__init__()
            self.decoder = model.decoder

        def forward(self, quantized: Tensor, scale: Tensor) -> Tensor:
            return self.decoder(quantized) * scale.reshape(-1, 1, 1)

    return DecoderFrame


def graph_record(
    path: Path,
    inputs: list[dict[str, Any]],
    outputs: list[dict[str, Any]],
) -> dict[str, Any]:
    import onnx

    onnx.checker.check_model(onnx.load(path), full_check=True)
    os.chmod(path, 0o600)
    return {
        "bytes": path.stat().st_size,
        "file": path.name,
        "inputs": inputs,
        "opset": OPSET,
        "outputs": outputs,
        "sha256": sha256(path),
    }


def export_graphs(model: object, output: Path) -> dict[str, Any]:
    import torch

    encoder_path = output / f"encoder_frame_op{OPSET}.onnx"
    decoder_path = output / f"decoder_frame_op{OPSET}.onnx"
    audio = torch.zeros(1, CHANNELS, FRAME_SAMPLES, dtype=torch.float32)
    quantized = torch.zeros(
        1, LATENT_DIMENSION, LATENT_FRAMES, dtype=torch.float32
    )
    scale = torch.ones(1, 1, dtype=torch.float32)
    torch.onnx.export(
        encoder_frame_type()(model).eval(),
        (audio,),
        encoder_path,
        input_names=["audio"],
        output_names=["latent", "scale"],
        opset_version=OPSET,
        do_constant_folding=True,
        dynamo=False,
    )
    torch.onnx.export(
        decoder_frame_type()(model).eval(),
        (quantized, scale),
        decoder_path,
        input_names=["quantized", "scale"],
        output_names=["audio"],
        opset_version=OPSET,
        do_constant_folding=True,
        dynamo=False,
    )
    return {
        "encoder": graph_record(
            encoder_path,
            [{"dtype": "float32", "name": "audio", "shape": [1, 2, 48_000]}],
            [
                {
                    "dtype": "float32",
                    "name": "latent",
                    "shape": [1, 128, 150],
                },
                {"dtype": "float32", "name": "scale", "shape": [1, 1]},
            ],
        ),
        "decoder": graph_record(
            decoder_path,
            [
                {
                    "dtype": "float32",
                    "name": "quantized",
                    "shape": [1, 128, 150],
                },
                {"dtype": "float32", "name": "scale", "shape": [1, 1]},
            ],
            [{"dtype": "float32", "name": "audio", "shape": [1, 2, 48_000]}],
        ),
    }


def export_codebooks(model: object, output: Path) -> dict[str, Any]:
    import numpy as np

    arrays = [
        layer.codebook.embed.detach().cpu().numpy()
        for layer in model.quantizer.layers
    ]
    codebooks = np.stack(arrays).astype("<f4", copy=False)
    expected_shape = (CODEBOOKS, CODEBOOK_SIZE, LATENT_DIMENSION)
    if codebooks.shape != expected_shape:
        raise ValueError(f"codebook shape differs: {codebooks.shape}")
    path = output / "rvq-codebooks.f32le"
    codebooks.tofile(path)
    os.chmod(path, 0o600)
    return {
        "bytes": path.stat().st_size,
        "dtype": "float32",
        "endianness": "little",
        "file": path.name,
        "layout": "quantizer,entry,dimension",
        "sha256": sha256(path),
        "shape": list(expected_shape),
    }


def export_bundle(model_directory: Path, output_path: Path) -> Path:
    import numpy as np
    import onnx
    import torch

    output = prepare_output_directory(output_path)
    model, model_files = load_model(model_directory)
    torch.manual_seed(0)
    torch.set_num_threads(1)
    graphs = export_graphs(model, output)
    codebooks = export_codebooks(model, output)
    manifest = {
        "artifact_policy": {
            "derived_artifact_publication": "owner-reserved",
            "model_delivery": "local-safetensors-only",
            "network_access": False,
            "release_qualified": False,
            "unsafe_pickle_loaded": False,
        },
        "codebooks": codebooks,
        "graphs": graphs,
        "license": {
            "evidence": "reviewed-official-model-card-at-pinned-revision",
            "spdx": "MIT",
        },
        "model": {
            "files": model_files,
            "format": "safetensors",
            "repository": "facebook/encodec_48khz",
            "revision": MODEL_REVISION,
        },
        "profile": {
            "bandwidth_kbps": [3.0, 6.0, 12.0, 24.0],
            "channels": CHANNELS,
            "codebook_cardinality": CODEBOOK_SIZE,
            "codebook_counts": [2, 4, 8, 16],
            "frame_samples": FRAME_SAMPLES,
            "latent_dimension": LATENT_DIMENSION,
            "latent_frames": LATENT_FRAMES,
            "normalization": "per-frame-mono-rms-plus-1e-8",
            "overlap_samples": OVERLAP_SAMPLES,
            "sample_rate": SAMPLE_RATE,
            "state": "none-noncausal-bounded-file-frame",
            "stride_samples": STRIDE_SAMPLES,
        },
        "schema": "kilix.encodec.48khz-export/v1",
        "sources": {
            "export_24khz.py": sha256(REPOSITORY / "tools/export_24khz.py"),
            "export_48khz.py": sha256(Path(__file__).resolve()),
            "pyproject.toml": sha256(REPOSITORY / "pyproject.toml"),
            "uv.lock": sha256(REPOSITORY / "uv.lock"),
            "verify_48khz.py": sha256(REPOSITORY / "tools/verify_48khz.py"),
        },
        "toolchain": {
            "numpy": np.__version__,
            "onnx": onnx.__version__,
            "python": platform.python_version(),
            "safetensors": importlib.metadata.version("safetensors"),
            "torch": torch.__version__,
            "transformers": importlib.metadata.version("transformers"),
            "uv": uv_version(),
        },
    }
    manifest_path = output / "manifest.json"
    manifest_path.write_bytes(canonical_json(manifest))
    os.chmod(manifest_path, 0o600)
    print(
        "48 kHz export: graphs=2/2 codebooks=1/1 manifest=1/1 "
        f"sha256={sha256(manifest_path)}"
    )
    print("model and derived-artifact publication: 0/2 authorized")
    return manifest_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--model-dir", type=Path)
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    if args.version:
        print(f"kilix-encodec 48 kHz export tool {TOOL_VERSION}")
        return 0
    if args.self_test:
        policy_self_test()
        return 0
    if args.model_dir is None or args.output_dir is None:
        parser.error("--model-dir and --output-dir are required")
    try:
        export_bundle(args.model_dir, args.output_dir)
    except (OSError, RuntimeError, ValueError) as error:
        parser.exit(1, f"48 kHz export refused: {error}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
