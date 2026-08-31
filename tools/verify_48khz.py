#!/usr/bin/env python3
"""Verify the scratch-only 48 kHz stereo file-profile export."""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import math
import os
import platform
import time
import wave
from pathlib import Path
from typing import Any, Callable

from export_24khz import REPOSITORY, canonical_json, sha256, uv_version
from export_48khz import (
    CHANNELS,
    CODEBOOKS,
    CODEBOOK_SIZE,
    FRAME_SAMPLES,
    LATENT_DIMENSION,
    LATENT_FRAMES,
    MODEL_FILES,
    MODEL_REVISION,
    OVERLAP_SAMPLES,
    SAMPLE_RATE,
    STRIDE_SAMPLES,
    decoder_frame_type,
    encoder_frame_type,
    load_model,
)


LATENT_TOLERANCE = 5e-3
WAVEFORM_TOLERANCE = 2e-4
TOKEN_FLIP_RATE_LIMIT_PERCENT = 0.1
BANDWIDTH_TO_CODEBOOKS = {3.0: 2, 6.0: 4, 12.0: 8, 24.0: 16}


def load_manifest(
    bundle: Path, model_directory: Path
) -> tuple[dict[str, Any], object]:
    import numpy as np
    import onnx
    import torch

    if bundle.is_symlink() or not bundle.is_dir():
        raise AssertionError("48 kHz bundle must be a non-symlink directory")
    resolved = bundle.resolve()
    if resolved == REPOSITORY or resolved.is_relative_to(REPOSITORY):
        raise AssertionError("48 kHz bundle must remain outside the Git repository")
    manifest_path = bundle / "manifest.json"
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise AssertionError("48 kHz manifest must be a regular file")
    raw = manifest_path.read_bytes()
    manifest = json.loads(raw)
    if raw != canonical_json(manifest):
        raise AssertionError("48 kHz manifest is not canonical JSON")
    if manifest.get("schema") != "kilix.encodec.48khz-export/v1":
        raise AssertionError("48 kHz manifest schema differs")
    expected_policy = {
        "derived_artifact_publication": "owner-reserved",
        "model_delivery": "local-safetensors-only",
        "network_access": False,
        "release_qualified": False,
        "unsafe_pickle_loaded": False,
    }
    if manifest.get("artifact_policy") != expected_policy:
        raise AssertionError("48 kHz artifact policy differs")
    if manifest.get("model", {}).get("revision") != MODEL_REVISION:
        raise AssertionError("48 kHz model revision differs")
    if manifest.get("model", {}).get("format") != "safetensors":
        raise AssertionError("48 kHz model format differs")
    expected_model_files = {
        name: {"bytes": size, "sha256": digest}
        for name, (size, digest) in MODEL_FILES.items()
    }
    if manifest.get("model", {}).get("files") != expected_model_files:
        raise AssertionError("48 kHz model file identity differs")
    expected_sources = {
        "export_24khz.py": sha256(REPOSITORY / "tools/export_24khz.py"),
        "export_48khz.py": sha256(REPOSITORY / "tools/export_48khz.py"),
        "pyproject.toml": sha256(REPOSITORY / "pyproject.toml"),
        "uv.lock": sha256(REPOSITORY / "uv.lock"),
        "verify_48khz.py": sha256(Path(__file__).resolve()),
    }
    if manifest.get("sources") != expected_sources:
        raise AssertionError("48 kHz source identity differs")
    expected_toolchain = {
        "numpy": np.__version__,
        "onnx": onnx.__version__,
        "python": platform.python_version(),
        "safetensors": importlib.metadata.version("safetensors"),
        "torch": torch.__version__,
        "transformers": importlib.metadata.version("transformers"),
        "uv": uv_version(),
    }
    if manifest.get("toolchain") != expected_toolchain:
        raise AssertionError("48 kHz toolchain identity differs")

    model, _ = load_model(model_directory)
    if set(manifest.get("graphs", {})) != {"encoder", "decoder"}:
        raise AssertionError("48 kHz graph inventory differs")
    for name, record in manifest["graphs"].items():
        filename = record.get("file")
        if not isinstance(filename, str) or Path(filename).name != filename:
            raise AssertionError(f"unsafe 48 kHz graph filename: {name}")
        path = bundle / filename
        if path.is_symlink() or not path.is_file():
            raise AssertionError(f"48 kHz graph is not a regular file: {name}")
        if path.stat().st_size != record.get("bytes"):
            raise AssertionError(f"48 kHz graph size differs: {name}")
        if sha256(path) != record.get("sha256"):
            raise AssertionError(f"48 kHz graph digest differs: {name}")
        onnx.checker.check_model(onnx.load(path), full_check=True)
    codebook_record = manifest["codebooks"]
    codebook_path = bundle / codebook_record["file"]
    if codebook_path.is_symlink() or not codebook_path.is_file():
        raise AssertionError("48 kHz codebooks are not a regular file")
    if (
        codebook_path.stat().st_size != codebook_record["bytes"]
        or sha256(codebook_path) != codebook_record["sha256"]
    ):
        raise AssertionError("48 kHz codebook identity differs")
    print("48 kHz bundle identities: 4/4 PASS")
    return manifest, model


def session(path: Path, threads: int) -> object:
    import onnxruntime as ort

    options = ort.SessionOptions()
    options.intra_op_num_threads = threads
    options.inter_op_num_threads = 1
    options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
    return ort.InferenceSession(
        str(path), options, providers=["CPUExecutionProvider"]
    )


def verify_graph_contract(runtime: object, record: dict[str, Any]) -> None:
    types = {"float32": "tensor(float)"}
    actual_inputs = [
        (item.name, item.type, item.shape) for item in runtime.get_inputs()
    ]
    actual_outputs = [
        (item.name, item.type, item.shape) for item in runtime.get_outputs()
    ]
    expected_inputs = [
        (item["name"], types[item["dtype"]], item["shape"])
        for item in record["inputs"]
    ]
    expected_outputs = [
        (item["name"], types[item["dtype"]], item["shape"])
        for item in record["outputs"]
    ]
    if actual_inputs != expected_inputs or actual_outputs != expected_outputs:
        raise AssertionError("48 kHz runtime graph contract differs")


def verify_fixed_shape_refusal(encoder: object) -> None:
    import numpy as np

    refused = 0
    invalid = (
        np.zeros((1, CHANNELS, FRAME_SAMPLES - 1), dtype=np.float32),
        np.zeros((1, 1, FRAME_SAMPLES), dtype=np.float32),
    )
    for audio in invalid:
        try:
            encoder.run(None, {"audio": audio})
        except Exception:
            refused += 1
    if refused != len(invalid):
        raise AssertionError(f"48 kHz fixed-shape refusals differ: {refused}/2")
    print(f"48 kHz fixed-shape refusals: {refused}/2 PASS")


def load_codebooks(
    bundle: Path, model: object, record: dict[str, Any]
) -> object:
    import numpy as np

    codebooks = np.fromfile(bundle / record["file"], dtype="<f4").reshape(
        record["shape"]
    )
    oracle = np.stack(
        [layer.codebook.embed.detach().cpu().numpy() for layer in model.quantizer.layers]
    ).astype("<f4", copy=False)
    if not np.array_equal(codebooks, oracle):
        raise AssertionError("48 kHz raw codebooks differ from safetensors buffers")
    return codebooks


def native_rvq_encode(latent: object, codebooks: object, count: int) -> object:
    import numpy as np

    residual = np.array(latent, dtype=np.float32, copy=True, order="C")
    rows = []
    for index in range(count):
        vectors = np.ascontiguousarray(
            residual.transpose(0, 2, 1).reshape(-1, LATENT_DIMENSION)
        )
        book = np.ascontiguousarray(codebooks[index])
        scaled = np.sum(np.square(vectors), axis=1, keepdims=True, dtype=np.float32)
        cross = np.matmul(vectors, book.T)
        book_scaled = np.sum(
            np.square(book), axis=1, keepdims=False, dtype=np.float32
        )[None, :]
        scores = -(scaled - np.float32(2.0) * cross + book_scaled)
        indices = np.argmax(scores, axis=-1).astype(np.int64).reshape(
            latent.shape[0], latent.shape[2]
        )
        decoded = book[indices].transpose(0, 2, 1)
        residual = np.subtract(residual, decoded, dtype=np.float32)
        rows.append(indices)
    return np.stack(rows)


def native_rvq_decode(codes: object, codebooks: object) -> object:
    import numpy as np

    quantized = np.zeros(
        (codes.shape[1], LATENT_DIMENSION, codes.shape[2]), dtype=np.float32
    )
    for index, indices in enumerate(codes):
        decoded = codebooks[index][indices].transpose(0, 2, 1)
        quantized = np.add(quantized, decoded, dtype=np.float32)
    return quantized


def corpus() -> dict[str, object]:
    import numpy as np

    timeline = np.arange(FRAME_SAMPLES, dtype=np.float32) / np.float32(SAMPLE_RATE)
    tones = np.stack(
        [
            np.float32(0.42)
            * np.sin(
                np.float32(2 * math.pi)
                * (np.float32(110) + np.float32(75) * timeline)
                * timeline
            )
            + np.float32(0.17)
            * np.sin(np.float32(2 * math.pi * 440) * timeline),
            np.float32(0.38)
            * np.sin(
                np.float32(2 * math.pi)
                * (np.float32(165) + np.float32(55) * timeline)
                * timeline
            )
            + np.float32(0.13)
            * np.sin(np.float32(2 * math.pi * 660) * timeline),
        ]
    )[None, ...].astype(np.float32)
    impulse = np.zeros((1, CHANNELS, FRAME_SAMPLES), dtype=np.float32)
    impulse[0, 0, [0, 1, 23_999, 47_999]] = [0.9, -0.7, 0.6, -0.5]
    impulse[0, 1, [0, 9_599, 24_000, 47_998]] = [-0.8, 0.5, -0.6, 0.7]
    generator = np.random.default_rng(101)
    noise = generator.normal(
        0.0, 0.18, size=(1, CHANNELS, FRAME_SAMPLES)
    ).astype(np.float32)
    antiphase = np.stack(
        [
            np.float32(0.5)
            * np.sin(np.float32(2 * math.pi * 997) * timeline),
            np.float32(-0.5)
            * np.sin(np.float32(2 * math.pi * 997) * timeline),
        ]
    )[None, ...].astype(np.float32)
    return {
        "antiphase": antiphase,
        "impulse": impulse,
        "noise": noise,
        "silence": np.zeros((1, CHANNELS, FRAME_SAMPLES), dtype=np.float32),
        "tones": tones,
    }


def max_abs(reference: object, actual: object) -> float:
    import numpy as np

    return float(
        np.max(
            np.abs(
                reference.astype(np.float64) - actual.astype(np.float64)
            ),
            initial=0.0,
        )
    )


def frame_checks(
    model: object,
    codebooks: object,
    encoder: object,
    decoder: object,
) -> dict[str, Any]:
    import numpy as np
    import torch

    EncoderFrame = encoder_frame_type()
    DecoderFrame = decoder_frame_type()
    oracle_encoder = EncoderFrame(model).eval()
    oracle_decoder = DecoderFrame(model).eval()
    rows = 0
    identical_population = 0
    identical_mismatches = 0
    end_to_end_population = 0
    end_to_end_mismatches = 0
    worst_latent = 0.0
    worst_waveform = 0.0
    with torch.no_grad():
        for fixture, audio in corpus().items():
            tensor = torch.from_numpy(audio)
            oracle_latent_tensor, oracle_scale_tensor = oracle_encoder(tensor)
            oracle_latent = oracle_latent_tensor.numpy()
            oracle_scale = oracle_scale_tensor.numpy()
            candidate_latent, candidate_scale = encoder.run(None, {"audio": audio})
            latent_error = max_abs(oracle_latent, candidate_latent)
            scale_error = max_abs(oracle_scale, candidate_scale)
            if latent_error > LATENT_TOLERANCE or scale_error > 1e-7:
                raise AssertionError(f"48 kHz encoder parity failed: {fixture}")
            worst_latent = max(worst_latent, latent_error)
            for bandwidth, count in BANDWIDTH_TO_CODEBOOKS.items():
                candidate_codes = native_rvq_encode(
                    candidate_latent, codebooks, count
                )
                identical_oracle = model.quantizer.encode(
                    torch.from_numpy(candidate_latent), bandwidth
                ).numpy()
                identical_mismatches += int(
                    np.count_nonzero(identical_oracle != candidate_codes)
                )
                identical_population += int(candidate_codes.size)
                official_codes_bqt, official_scale_tensor = model._encode_frame(
                    tensor, bandwidth
                )
                official_codes = official_codes_bqt.transpose(0, 1).numpy()
                row_mismatches = int(
                    np.count_nonzero(official_codes != candidate_codes)
                )
                row_population = int(candidate_codes.size)
                row_rate = 100.0 * row_mismatches / row_population
                if row_rate > TOKEN_FLIP_RATE_LIMIT_PERCENT:
                    raise AssertionError(
                        f"48 kHz token drift exceeds bound: {fixture}:{bandwidth}"
                    )
                end_to_end_mismatches += row_mismatches
                end_to_end_population += row_population
                candidate_quantized = native_rvq_decode(candidate_codes, codebooks)
                oracle_quantized = model.quantizer.decode(
                    torch.from_numpy(candidate_codes)
                ).numpy()
                if max_abs(oracle_quantized, candidate_quantized) > 1e-6:
                    raise AssertionError(
                        f"48 kHz native RVQ decode differs: {fixture}:{bandwidth}"
                    )
                candidate_audio = decoder.run(
                    None,
                    {
                        "quantized": candidate_quantized,
                        "scale": candidate_scale,
                    },
                )[0]
                graph_oracle = oracle_decoder(
                    torch.from_numpy(candidate_quantized),
                    torch.from_numpy(candidate_scale),
                ).numpy()
                official_audio = model._decode_frame(
                    official_codes_bqt, official_scale_tensor
                ).numpy()
                graph_error = max_abs(graph_oracle, candidate_audio)
                full_error = max_abs(official_audio, candidate_audio)
                if (
                    graph_error > WAVEFORM_TOLERANCE
                    or full_error > WAVEFORM_TOLERANCE
                ):
                    raise AssertionError(
                        f"48 kHz decoder parity failed: {fixture}:{bandwidth}"
                    )
                worst_waveform = max(worst_waveform, graph_error, full_error)
                rows += 1
    if identical_mismatches != 0:
        raise AssertionError(
            "48 kHz identical-latent RVQ differs: "
            f"{identical_mismatches}/{identical_population}"
        )
    print(
        f"48 kHz corpus rows: {rows}/20 PASS "
        f"identical_token_mismatches=0/{identical_population} "
        f"end_to_end_token_mismatches={end_to_end_mismatches}/"
        f"{end_to_end_population}"
    )
    return {
        "end_to_end_token_mismatches": end_to_end_mismatches,
        "end_to_end_token_population": end_to_end_population,
        "identical_token_mismatches": identical_mismatches,
        "identical_token_population": identical_population,
        "rows": rows,
        "worst_latent_max_abs": worst_latent,
        "worst_waveform_max_abs": worst_waveform,
    }


def linear_overlap_add(frames: list[object]) -> object:
    import numpy as np

    total = STRIDE_SAMPLES * (len(frames) - 1) + frames[-1].shape[-1]
    timeline = np.linspace(0, 1, FRAME_SAMPLES + 2, dtype=np.float32)[1:-1]
    weight = np.float32(0.5) - np.abs(timeline - np.float32(0.5))
    output = np.zeros((*frames[0].shape[:-1], total), dtype=np.float32)
    weight_sum = np.zeros(total, dtype=np.float32)
    for index, frame in enumerate(frames):
        offset = index * STRIDE_SAMPLES
        output[..., offset : offset + FRAME_SAMPLES] += weight * frame
        weight_sum[offset : offset + FRAME_SAMPLES] += weight
    if float(weight_sum.min()) <= 0.0:
        raise AssertionError("48 kHz overlap-add weight reached zero")
    return np.divide(output, weight_sum, dtype=np.float32)


def file_source(logical_samples: int) -> tuple[object, object]:
    import numpy as np

    frames = math.ceil(logical_samples / STRIDE_SAMPLES)
    padded = (frames - 1) * STRIDE_SAMPLES + FRAME_SAMPLES
    timeline = np.arange(logical_samples, dtype=np.float32) / np.float32(
        SAMPLE_RATE
    )
    left = np.float32(0.38) * np.sin(
        np.float32(2 * math.pi * 220) * timeline
    )
    right = np.float32(0.35) * np.sin(
        np.float32(2 * math.pi * 277.18) * timeline
    )
    value = np.zeros((1, CHANNELS, padded), dtype=np.float32)
    value[0, :, :logical_samples] = np.stack((left, right))
    mask = np.zeros_like(value, dtype=np.bool_)
    mask[..., :logical_samples] = True
    return value, mask


def file_pipeline(
    value: object,
    mask: object,
    codebooks: object,
    encoder: object,
    decoder: object,
) -> tuple[object, object, object]:
    import numpy as np

    outputs = []
    codes = []
    latents = []
    final_offset = value.shape[-1] - FRAME_SAMPLES
    for offset in range(0, final_offset + 1, STRIDE_SAMPLES):
        frame = value[..., offset : offset + FRAME_SAMPLES]
        frame_mask = mask[..., offset : offset + FRAME_SAMPLES]
        latent, scale = encoder.run(None, {"audio": frame * frame_mask})
        frame_codes = native_rvq_encode(latent, codebooks, 4)
        quantized = native_rvq_decode(frame_codes, codebooks)
        outputs.append(
            decoder.run(None, {"quantized": quantized, "scale": scale})[0]
        )
        codes.append(frame_codes)
        latents.append(latent)
    return linear_overlap_add(outputs), np.stack(codes), np.stack(latents)


def write_wav(path: Path, value: object) -> None:
    import numpy as np

    pcm = np.rint(
        np.clip(value[0].T, -1.0, 1.0) * np.float32(32767.0)
    ).astype("<i2")
    with wave.open(str(path), "wb") as output:
        output.setnchannels(CHANNELS)
        output.setsampwidth(2)
        output.setframerate(SAMPLE_RATE)
        output.writeframes(pcm.tobytes())
    os.chmod(path, 0o600)


def file_checks(
    bundle: Path,
    model: object,
    codebooks: object,
    encoder: object,
    decoder: object,
) -> dict[str, Any]:
    import numpy as np
    import torch

    logical_samples = 100_003
    value, mask = file_source(logical_samples)
    candidate, codes, latents = file_pipeline(
        value, mask, codebooks, encoder, decoder
    )
    repeated, repeated_codes, repeated_latents = file_pipeline(
        value, mask, codebooks, encoder, decoder
    )
    if not (
        np.array_equal(candidate, repeated)
        and np.array_equal(codes, repeated_codes)
        and np.array_equal(latents, repeated_latents)
    ):
        raise AssertionError("48 kHz file pipeline is not deterministic")

    EncoderFrame = encoder_frame_type()
    DecoderFrame = decoder_frame_type()
    oracle_encoder = EncoderFrame(model).eval()
    oracle_decoder = DecoderFrame(model).eval()
    oracle_outputs = []
    oracle_codes = []
    with torch.no_grad():
        final_offset = value.shape[-1] - FRAME_SAMPLES
        for offset in range(0, final_offset + 1, STRIDE_SAMPLES):
            frame = (
                value[..., offset : offset + FRAME_SAMPLES]
                * mask[..., offset : offset + FRAME_SAMPLES]
            )
            latent, scale = oracle_encoder(torch.from_numpy(frame))
            frame_codes = model.quantizer.encode(latent, 6.0)
            quantized = model.quantizer.decode(frame_codes)
            oracle_outputs.append(oracle_decoder(quantized, scale).numpy())
            oracle_codes.append(frame_codes.numpy())
    oracle = linear_overlap_add(oracle_outputs)
    oracle_code_array = np.stack(oracle_codes)
    token_mismatches = int(np.count_nonzero(oracle_code_array != codes))
    token_population = int(codes.size)
    waveform_error = max_abs(oracle, candidate)
    if (
        100.0 * token_mismatches / token_population
        > TOKEN_FLIP_RATE_LIMIT_PERCENT
        or waveform_error > WAVEFORM_TOLERANCE
    ):
        raise AssertionError("48 kHz file-profile parity exceeds bounds")
    if codes.shape[0] != 3:
        raise AssertionError(f"48 kHz complete-frame count differs: {codes.shape[0]}/3")

    listening = bundle / "listening"
    listening.mkdir(mode=0o700, exist_ok=False)
    source = value[..., :logical_samples]
    oracle_trimmed = oracle[..., :logical_samples]
    candidate_trimmed = candidate[..., :logical_samples]
    difference = oracle_trimmed - candidate_trimmed
    peak = float(np.max(np.abs(difference)))
    normalized = difference if peak == 0.0 else difference * np.float32(0.8 / peak)
    fixtures = {
        "candidate.wav": candidate_trimmed,
        "difference-normalized.wav": normalized,
        "oracle.wav": oracle_trimmed,
        "synthetic-source.wav": source,
    }
    for name, content in fixtures.items():
        write_wav(listening / name, content)
    print(
        "48 kHz file-profile controls: 5/5 PASS "
        f"frames={codes.shape[0]}/3 token_mismatches={token_mismatches}/"
        f"{token_population} listening_fixtures=4/4 blind_verdict=0/1"
    )
    return {
        "code_frames": int(codes.shape[0]),
        "deterministic_repeat": True,
        "listening_fixtures": {
            name: sha256(listening / name) for name in sorted(fixtures)
        },
        "logical_samples": logical_samples,
        "padded_samples": int(value.shape[-1]),
        "token_mismatches": token_mismatches,
        "token_population": token_population,
        "waveform_max_abs": waveform_error,
    }


def timed(function: Callable[[], object], repetitions: int) -> dict[str, Any]:
    import numpy as np

    for _ in range(3):
        function()
    values = []
    for _ in range(repetitions):
        started = time.perf_counter_ns()
        function()
        values.append((time.perf_counter_ns() - started) / 1_000_000)
    array = np.asarray(values)
    return {
        "maximum_ms": float(array.max()),
        "p50_ms": float(np.percentile(array, 50)),
        "p95_ms": float(np.percentile(array, 95)),
        "p99_ms": float(np.percentile(array, 99)),
        "repetitions": repetitions,
    }


def benchmark(
    encoder: object, decoder: object, repetitions: int
) -> dict[str, Any]:
    import numpy as np

    audio = corpus()["tones"]
    latent, scale = encoder.run(None, {"audio": audio})
    quantized = np.zeros((1, LATENT_DIMENSION, LATENT_FRAMES), dtype=np.float32)
    rows = {
        "encoder": timed(lambda: encoder.run(None, {"audio": audio}), repetitions),
        "decoder": timed(
            lambda: decoder.run(
                None, {"quantized": quantized, "scale": scale}
            ),
            repetitions,
        ),
    }
    frame_budget_ms = 1000.0 * STRIDE_SAMPLES / SAMPLE_RATE
    for row in rows.values():
        row["frame_budget_ms"] = frame_budget_ms
        row["realtime_multiple_at_p99"] = frame_budget_ms / row["p99_ms"]
    return rows


def verify_bundle(
    bundle: Path,
    model_directory: Path,
    threads: int,
    benchmark_repetitions: int,
    fixture_tier: str,
    fixture_runner: Path | None,
) -> None:
    fixture = None
    if fixture_tier == "h1":
        if fixture_runner is None:
            raise ValueError("--fixture-runner is required for an H1 measurement")
        from capacity_fixture import inspect_h1

        fixture = inspect_h1(fixture_runner)
    elif fixture_runner is not None:
        raise ValueError("--fixture-runner requires --fixture-tier h1")

    manifest, model = load_manifest(bundle, model_directory)
    encoder = session(bundle / manifest["graphs"]["encoder"]["file"], threads)
    decoder = session(bundle / manifest["graphs"]["decoder"]["file"], threads)
    verify_graph_contract(encoder, manifest["graphs"]["encoder"])
    verify_graph_contract(decoder, manifest["graphs"]["decoder"])
    print("48 kHz runtime graph contracts: 2/2 PASS")
    verify_fixed_shape_refusal(encoder)
    codebooks = load_codebooks(bundle, model, manifest["codebooks"])
    frame_result = frame_checks(model, codebooks, encoder, decoder)
    file_result = file_checks(bundle, model, codebooks, encoder, decoder)
    benchmark_result = benchmark(encoder, decoder, benchmark_repetitions)
    h1_decode_passed = benchmark_result["decoder"]["p99_ms"] < (
        1000.0 * STRIDE_SAMPLES / SAMPLE_RATE
    )
    h1_measured_pass = fixture is not None and h1_decode_passed
    result = {
        "claim": (
            "technical-controls-on-frozen-H1"
            if fixture is not None
            else "technical-controls-on-unfrozen-host"
        ),
        "file_profile": file_result,
        "fixture": fixture,
        "frame_corpus": frame_result,
        "h1_gate": {
            "measured_pass": h1_measured_pass,
            "decoder_realtime": h1_decode_passed,
        },
        "manifest_sha256": sha256(bundle / "manifest.json"),
        "performance": benchmark_result,
        "schema": "kilix.encodec.48khz-verification/v2",
        "threads": threads,
        "verification_sources": {
            "capacity_fixture.py": sha256(REPOSITORY / "tools/capacity_fixture.py"),
            "verify_48khz.py": sha256(Path(__file__).resolve()),
        },
    }
    result_path = bundle / "verification.json"
    result_path.write_bytes(canonical_json(result))
    os.chmod(result_path, 0o600)
    label = "frozen H1" if fixture is not None else "unfrozen"
    print(
        f"48 kHz {label} performance harness: 2/2 graphs measured "
        f"encoder_p99_ms={benchmark_result['encoder']['p99_ms']:.3f} "
        f"decoder_p99_ms={benchmark_result['decoder']['p99_ms']:.3f} "
        f"decoder_realtime={int(h1_decode_passed)}/1 "
        f"measured_H1_gate={int(h1_measured_pass)}/1"
    )
    print("48 kHz technical controls: 35/35 PASS; formal_P3_credit=0/1")
    if fixture is not None and not h1_measured_pass:
        raise AssertionError("48 kHz frozen H1 decoder did not sustain real time")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--model-dir", type=Path, required=True)
    parser.add_argument("--threads", type=int, choices=(1, 2, 4), default=2)
    parser.add_argument("--benchmark-repetitions", type=int, default=20)
    parser.add_argument(
        "--fixture-tier", choices=("unfrozen", "h1"), default="unfrozen"
    )
    parser.add_argument("--fixture-runner", type=Path)
    args = parser.parse_args()
    if args.benchmark_repetitions < 10:
        parser.error("--benchmark-repetitions must be at least 10")
    if args.fixture_tier == "h1" and args.benchmark_repetitions < 100:
        parser.error("H1 measurement requires at least 100 repetitions")
    try:
        verify_bundle(
            args.bundle,
            args.model_dir,
            args.threads,
            args.benchmark_repetitions,
            args.fixture_tier,
            args.fixture_runner,
        )
    except (AssertionError, OSError, RuntimeError, ValueError) as error:
        parser.exit(1, f"48 kHz verification refused: {error}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
