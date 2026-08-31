#!/usr/bin/env python3
"""Verify skeleton policy or a scratch-only stateful EnCodec export bundle."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import math
import os
import platform
import subprocess
import tempfile
import time
import wave
from pathlib import Path
from typing import Any


REPOSITORY = Path(__file__).resolve().parents[1]
EXPECTED_GRAPHS = {
    "decoder",
    "encoder",
    *{
        f"rvq_{direction}_{bandwidth}kbps"
        for bandwidth in (3, 6, 12)
        for direction in ("encode", "decode")
    },
}
LATENT_TOLERANCE = 1e-4
WAVEFORM_TOLERANCE = 1e-4
EPOCH_PACKETS = 25


def rvq_graph_key(direction: str, bandwidth: float) -> str:
    return f"rvq_{direction}_{int(bandwidth)}kbps"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_json(value: dict[str, Any]) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")


def verify_skeleton(path: Path) -> tuple[int, int]:
    checks: list[bool] = []
    checks.append(path.is_file() and not path.is_symlink())
    document = json.loads(path.read_text(encoding="utf-8"))
    checks.append(document.get("format") == "kilix.encodec.asset/v1")
    checks.append(document.get("profile") == "encodec-24khz-v1")
    checks.append(document.get("sample_rate") == 24_000)
    checks.append(document.get("packet_samples") == 960)
    checks.append(document.get("bandwidths_kbps") == [3, 6, 12])
    checks.append(
        document.get("codebooks_by_bandwidth") == {"3": 4, "6": 8, "12": 16}
    )
    checks.append(document.get("release_qualified") is False)
    checks.append(document.get("artifacts") == [])
    checks.append(document.get("delivery") == "user-supplied")
    passed = sum(checks)
    total = len(checks)
    print(
        f"manifest skeleton checks: {passed}/{total} "
        f"{'PASS' if passed == total else 'FAIL'}"
    )
    if passed != total:
        raise AssertionError("manifest skeleton differs")
    return passed, total


def uv_version() -> str:
    return subprocess.run(
        ["uv", "--version"], check=True, capture_output=True, text=True
    ).stdout.strip()


def load_manifest(bundle: Path, checkpoint: Path) -> tuple[dict[str, Any], object]:
    import onnx
    import torch

    from stateful_graph import load_model

    if bundle.is_symlink() or not bundle.is_dir():
        raise AssertionError("bundle must be a non-symlink directory")
    if bundle.resolve() == REPOSITORY or bundle.resolve().is_relative_to(REPOSITORY):
        raise AssertionError("bundle must remain outside the Git repository")
    manifest_path = bundle / "manifest.json"
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise AssertionError("manifest must be a non-symlink regular file")
    raw = manifest_path.read_bytes()
    value = json.loads(raw)
    if raw != canonical_json(value):
        raise AssertionError("manifest is not canonical JSON")
    if value.get("schema") != "kilix.encodec.stateful-onnx-export/v2":
        raise AssertionError("manifest schema differs")
    if value.get("profile") != "encodec-24khz-causal-mono-3-6-12kbps":
        raise AssertionError("manifest profile differs")
    if value.get("initial_state") != "all-zero":
        raise AssertionError("initial state contract differs")
    if value.get("padding_mode") != "constant":
        raise AssertionError("padding policy differs")
    expected_policy = {
        "checkpoint_delivery": "user-supplied-only",
        "derived_graph_publication": "forbidden-without-separate-model-grant",
        "native_runtime_downloads": False,
        "release_qualified": False,
    }
    if value.get("artifact_policy") != expected_policy:
        raise AssertionError("artifact policy differs")

    model, identity = load_model(checkpoint)
    expected_checkpoint = {
        "bytes": identity.bytes,
        "file": identity.file,
        "license_determination": "no-redistribution-grant-found",
        "sha256": identity.sha256,
    }
    if value.get("checkpoint") != expected_checkpoint:
        raise AssertionError("checkpoint identity differs")

    expected_sources = {
        "export_24khz.py": sha256(REPOSITORY / "tools/export_24khz.py"),
        "pyproject.toml": sha256(REPOSITORY / "pyproject.toml"),
        "stateful_graph.py": sha256(REPOSITORY / "tools/stateful_graph.py"),
        "uv.lock": sha256(REPOSITORY / "uv.lock"),
    }
    if value.get("sources") != expected_sources:
        raise AssertionError("export source identity differs")
    expected_toolchain = {
        "encodec": importlib.metadata.version("encodec"),
        "onnx": onnx.__version__,
        "onnxruntime": importlib.metadata.version("onnxruntime"),
        "python": platform.python_version(),
        "torch": torch.__version__,
        "uv": uv_version(),
    }
    if value.get("toolchain") != expected_toolchain:
        raise AssertionError("toolchain identity differs")
    if set(value.get("graphs", {})) != EXPECTED_GRAPHS:
        raise AssertionError("graph inventory differs")
    expected_packet = {
        "bandwidths_kbps": [3.0, 6.0, 12.0],
        "codebooks_by_bandwidth": {"3": 4, "6": 8, "12": 16},
        "latent_frames": 3,
        "sample_rate": 24_000,
        "samples": 960,
    }
    if value.get("packet") != expected_packet:
        raise AssertionError("packet profile contract differs")
    stateful_shapes = {
        "encoder": ([1, 1, 960], [1, 128, 3]),
        "decoder": ([1, 128, 3], [1, 1, 960]),
    }
    for key, (expected_input, expected_output) in stateful_shapes.items():
        record = value["graphs"][key]
        if record.get("input_shape") != expected_input:
            raise AssertionError(f"graph input shape differs: {key}")
        if record.get("output_shape") != expected_output:
            raise AssertionError(f"graph output shape differs: {key}")
    for bandwidth, quantizers in ((3.0, 4), (6.0, 8), (12.0, 16)):
        for direction in ("encode", "decode"):
            key = rvq_graph_key(direction, bandwidth)
            record = value["graphs"][key]
            if record.get("bandwidth_kbps") != bandwidth:
                raise AssertionError(f"graph bandwidth differs: {key}")
            if record.get("quantizers") != quantizers:
                raise AssertionError(f"graph codebook count differs: {key}")
            if direction == "encode":
                expected_input = [1, 128, 3]
                expected_output = [quantizers, 1, 3]
                expected_dtype = "int64"
            else:
                expected_input = [quantizers, 1, 3]
                expected_output = [1, 128, 3]
                expected_dtype = "float32"
            if record.get("input_shape") != expected_input:
                raise AssertionError(f"graph input shape differs: {key}")
            if record.get("output_shape") != expected_output:
                raise AssertionError(f"graph output shape differs: {key}")
            if record.get("output_dtype") != expected_dtype:
                raise AssertionError(f"graph output dtype differs: {key}")
    if any(record.get("opset") != 17 for record in value["graphs"].values()):
        raise AssertionError("graph opset differs")

    graph_checks = 0
    for key, record in value["graphs"].items():
        filename = record.get("file")
        if not isinstance(filename, str) or Path(filename).name != filename:
            raise AssertionError(f"unsafe graph filename: {key}")
        graph = bundle / filename
        if graph.is_symlink() or not graph.is_file():
            raise AssertionError(f"graph is not a regular file: {key}")
        if graph.stat().st_size != record.get("bytes"):
            raise AssertionError(f"graph size differs: {key}")
        if sha256(graph) != record.get("sha256"):
            raise AssertionError(f"graph digest differs: {key}")
        onnx.checker.check_model(onnx.load(graph), full_check=True)
        graph_checks += 1
    print(
        f"bundle identity and ONNX checks: "
        f"{graph_checks}/{len(EXPECTED_GRAPHS)} PASS"
    )
    return value, model


def session(path: Path, threads: int) -> object:
    import onnxruntime as ort

    options = ort.SessionOptions()
    options.intra_op_num_threads = threads
    options.inter_op_num_threads = 1
    options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
    return ort.InferenceSession(
        str(path), options, providers=["CPUExecutionProvider"]
    )


def zero_states(record: dict[str, Any]) -> list[object]:
    import numpy as np

    return [
        np.zeros(tuple(item["shape"]), dtype=np.float32)
        for item in record["state_inputs"]
    ]


def verify_contract(runtime: object, record: dict[str, Any]) -> None:
    expected_inputs = [record["input_name"]] + [
        item["name"] for item in record.get("state_inputs", [])
    ]
    expected_outputs = [record["output_name"]] + [
        item["name"] for item in record.get("state_outputs", [])
    ]
    if [item.name for item in runtime.get_inputs()] != expected_inputs:
        raise AssertionError("runtime input contract differs")
    if [item.name for item in runtime.get_outputs()] != expected_outputs:
        raise AssertionError("runtime output contract differs")
    if runtime.get_inputs()[0].shape != record["input_shape"]:
        raise AssertionError("runtime input shape differs")
    if runtime.get_outputs()[0].shape != record["output_shape"]:
        raise AssertionError("runtime output shape differs")


def run_packet(
    runtime: object,
    record: dict[str, Any],
    value: object,
    states: list[object],
) -> tuple[object, list[object]]:
    feed = {record["input_name"]: value}
    feed.update(
        {
            item["name"]: state
            for item, state in zip(record.get("state_inputs", []), states)
        }
    )
    outputs = runtime.run(None, feed)
    return outputs[0], outputs[1:]


def run_stateful_stream(
    runtime: object,
    record: dict[str, Any],
    value: object,
    packet_size: int,
    reset_packets: set[int] | None = None,
) -> object:
    import numpy as np

    initial = zero_states(record)
    states = [item.copy() for item in initial]
    pieces = []
    reset_packets = reset_packets or set()
    for index, offset in enumerate(range(0, value.shape[-1], packet_size)):
        packet = value[..., offset : offset + packet_size]
        if packet.shape[-1] != packet_size:
            break
        if index in reset_packets:
            states = [item.copy() for item in initial]
        result, states = run_packet(runtime, record, packet, states)
        pieces.append(result)
    return np.concatenate(pieces, axis=-1)


def run_stateless_stream(
    runtime: object, record: dict[str, Any], value: object, packet_size: int
) -> object:
    import numpy as np

    pieces = []
    for offset in range(0, value.shape[-1], packet_size):
        packet = value[..., offset : offset + packet_size]
        if packet.shape[-1] != packet_size:
            break
        pieces.append(runtime.run(None, {record["input_name"]: packet})[0])
    return np.concatenate(pieces, axis=-1)


def signal(seconds: int, seed: int) -> object:
    import torch

    from stateful_graph import SAMPLE_RATE

    count = SAMPLE_RATE * seconds
    timeline = torch.arange(count, dtype=torch.float32) / SAMPLE_RATE
    value = (
        0.35 * torch.sin(2 * math.pi * (120 + 80 * timeline) * timeline)
        + 0.25 * torch.sin(2 * math.pi * 440 * timeline)
        + 0.10
        * torch.randn(count, generator=torch.Generator().manual_seed(seed))
    )
    return (value / value.abs().max() * 0.9).reshape(1, 1, -1)


def encode(
    encoder: object,
    rvq: object,
    records: dict[str, Any],
    source: object,
    bandwidth: float,
    resets: set[int] | None = None,
) -> object:
    from stateful_graph import PACKET_LATENT_FRAMES, PACKET_SAMPLES

    latent = run_stateful_stream(
        encoder, records["encoder"], source, PACKET_SAMPLES, resets
    )
    key = rvq_graph_key("encode", bandwidth)
    return run_stateless_stream(
        rvq, records[key], latent, PACKET_LATENT_FRAMES
    )


def decode(
    rvq: object,
    decoder: object,
    records: dict[str, Any],
    codes: object,
    bandwidth: float,
    resets: set[int] | None = None,
) -> object:
    from stateful_graph import PACKET_LATENT_FRAMES

    key = rvq_graph_key("decode", bandwidth)
    quantized = run_stateless_stream(rvq, records[key], codes, PACKET_LATENT_FRAMES)
    return run_stateful_stream(
        decoder,
        records["decoder"],
        quantized,
        PACKET_LATENT_FRAMES,
        resets,
    )


def write_wav(path: Path, value: object) -> None:
    import numpy as np

    from stateful_graph import SAMPLE_RATE

    samples = np.asarray(value).reshape(-1).clip(-1.0, 1.0)
    pcm = (samples * 32767.0).astype("<i2").tobytes()
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(SAMPLE_RATE)
        output.writeframes(pcm)
    os.chmod(path, 0o600)


def percentile(values: list[float], point: float) -> float:
    import numpy as np

    return float(np.percentile(np.asarray(values), point))


def benchmark_encode_pipeline(
    first: object,
    first_record: dict[str, Any],
    second: object,
    second_record: dict[str, Any],
    packet: object,
    repetitions: int,
) -> dict[str, Any]:
    states = zero_states(first_record)

    def invoke() -> None:
        nonlocal states
        intermediate, states = run_packet(first, first_record, packet, states)
        second.run(None, {second_record["input_name"]: intermediate})

    for _ in range(10):
        invoke()
    timings = []
    for _ in range(repetitions):
        started = time.perf_counter_ns()
        invoke()
        timings.append((time.perf_counter_ns() - started) / 1_000_000)
    return {
        "p50_ms": percentile(timings, 50),
        "p95_ms": percentile(timings, 95),
        "p99_ms": percentile(timings, 99),
        "repetitions": repetitions,
    }


def benchmark_decode_pipeline(
    rvq: object,
    rvq_record: dict[str, Any],
    decoder: object,
    decoder_record: dict[str, Any],
    codes: object,
    repetitions: int,
) -> dict[str, Any]:
    states = zero_states(decoder_record)

    def invoke() -> None:
        nonlocal states
        quantized = rvq.run(None, {rvq_record["input_name"]: codes})[0]
        _, states = run_packet(decoder, decoder_record, quantized, states)

    for _ in range(10):
        invoke()
    timings = []
    for _ in range(repetitions):
        started = time.perf_counter_ns()
        invoke()
        timings.append((time.perf_counter_ns() - started) / 1_000_000)
    return {
        "p50_ms": percentile(timings, 50),
        "p95_ms": percentile(timings, 95),
        "p99_ms": percentile(timings, 99),
        "repetitions": repetitions,
    }


def verify_bundle(
    bundle: Path,
    checkpoint: Path,
    seconds: int,
    threads: int,
    benchmark_repetitions: int,
) -> None:
    import numpy as np
    import torch

    from stateful_graph import (
        BANDWIDTH_PROFILES,
        DEFAULT_BANDWIDTH,
        PACKET_LATENT_FRAMES,
        PACKET_SAMPLES,
        constant_padding,
    )

    manifest, oracle = load_manifest(bundle, checkpoint)
    records = manifest["graphs"]
    runtimes = {
        key: session(bundle / record["file"], threads)
        for key, record in records.items()
    }
    for key in EXPECTED_GRAPHS:
        verify_contract(runtimes[key], records[key])
    print(
        f"runtime graph contracts: "
        f"{len(EXPECTED_GRAPHS)}/{len(EXPECTED_GRAPHS)} PASS"
    )

    source = signal(seconds, seed=1)
    constant_padding(oracle.encoder)
    constant_padding(oracle.decoder)
    with torch.no_grad():
        reference_latent = oracle.encoder(source)

    actual_latent = run_stateful_stream(
        runtimes["encoder"], records["encoder"], source.numpy(), PACKET_SAMPLES
    )
    latent_difference = np.abs(reference_latent.numpy() - actual_latent)
    if float(latent_difference.max()) >= LATENT_TOLERANCE:
        raise AssertionError("encoder latent parity exceeds tolerance")
    print(
        "shared encoder ONNX/oracle parity: 1/1 PASS "
        f"latent_max={latent_difference.max():.3e}"
    )

    codes_by_profile: dict[str, object] = {}
    parity_by_profile: dict[str, dict[str, Any]] = {}
    total_token_mismatches = 0
    total_token_population = 0
    for bandwidth, quantizers in BANDWIDTH_PROFILES:
        label = f"{int(bandwidth)}kbps"
        encode_key = rvq_graph_key("encode", bandwidth)
        decode_key = rvq_graph_key("decode", bandwidth)
        with torch.no_grad():
            reference_codes = oracle.quantizer.encode(
                reference_latent, oracle.frame_rate, bandwidth
            )
            reference_quantized = oracle.quantizer.decode(reference_codes)
            reference_audio = oracle.decoder(reference_quantized)
        if reference_codes.shape[0] != quantizers:
            raise AssertionError(f"oracle codebook count differs: {label}")

        actual_codes = run_stateless_stream(
            runtimes[encode_key],
            records[encode_key],
            actual_latent,
            PACKET_LATENT_FRAMES,
        )
        token_mismatches = int(
            np.count_nonzero(reference_codes.numpy() != actual_codes)
        )
        token_population = int(reference_codes.numel())
        if token_mismatches != 0:
            raise AssertionError(
                f"token identity differs for {label}: "
                f"{token_mismatches}/{token_population}"
            )
        actual_quantized = run_stateless_stream(
            runtimes[decode_key],
            records[decode_key],
            actual_codes,
            PACKET_LATENT_FRAMES,
        )
        quantized_difference = np.abs(
            reference_quantized.numpy() - actual_quantized
        )
        if float(quantized_difference.max()) >= LATENT_TOLERANCE:
            raise AssertionError(f"RVQ decode parity exceeds tolerance: {label}")
        actual_audio = run_stateful_stream(
            runtimes["decoder"],
            records["decoder"],
            actual_quantized,
            PACKET_LATENT_FRAMES,
        )
        expected_audio = reference_audio.numpy()[..., : actual_audio.shape[-1]]
        audio_difference = np.abs(expected_audio - actual_audio)
        if float(audio_difference.max()) >= WAVEFORM_TOLERANCE:
            raise AssertionError(
                f"decoder waveform parity exceeds tolerance: {label}"
            )
        codes_by_profile[label] = actual_codes
        parity_by_profile[label] = {
            "audio_max_abs_difference": float(audio_difference.max()),
            "quantized_max_abs_difference": float(quantized_difference.max()),
            "token_mismatches": token_mismatches,
            "token_population": token_population,
        }
        total_token_mismatches += token_mismatches
        total_token_population += token_population
        print(
            f"{label} ONNX/oracle parity: 3/3 PASS "
            f"token_mismatches={token_mismatches}/{token_population} "
            f"quantized_max={quantized_difference.max():.3e} "
            f"waveform_max={audio_difference.max():.3e}"
        )
    print(
        "profile ONNX/oracle parity: 9/9 PASS "
        f"token_mismatches={total_token_mismatches}/{total_token_population}"
    )

    prefix_checks = (
        np.array_equal(codes_by_profile["3kbps"], codes_by_profile["6kbps"][:4]),
        np.array_equal(codes_by_profile["6kbps"], codes_by_profile["12kbps"][:8]),
    )
    if not all(prefix_checks):
        raise AssertionError("RVQ profile nesting differs")
    print("RVQ nested-profile controls: 2/2 PASS")

    first_a = signal(1, seed=7)
    first_b = signal(1, seed=8)
    shared = signal(1, seed=9)
    stream_a = torch.cat((first_a, shared), dim=-1).numpy()
    stream_b = torch.cat((first_b, shared), dim=-1).numpy()
    resets = {EPOCH_PACKETS}
    default_encode_key = rvq_graph_key("encode", DEFAULT_BANDWIDTH)
    default_decode_key = rvq_graph_key("decode", DEFAULT_BANDWIDTH)
    codes_a = encode(
        runtimes["encoder"],
        runtimes[default_encode_key],
        records,
        stream_a,
        DEFAULT_BANDWIDTH,
        resets,
    )
    audio_a = decode(
        runtimes[default_decode_key],
        runtimes["decoder"],
        records,
        codes_a,
        DEFAULT_BANDWIDTH,
        resets,
    )
    codes_b = encode(
        runtimes["encoder"],
        runtimes[default_encode_key],
        records,
        stream_b,
        DEFAULT_BANDWIDTH,
        resets,
    )
    audio_b = decode(
        runtimes[default_decode_key],
        runtimes["decoder"],
        records,
        codes_b,
        DEFAULT_BANDWIDTH,
        resets,
    )
    epoch_code_offset = EPOCH_PACKETS * PACKET_LATENT_FRAMES
    epoch_audio_offset = EPOCH_PACKETS * PACKET_SAMPLES
    if not np.array_equal(
        codes_a[..., epoch_code_offset:], codes_b[..., epoch_code_offset:]
    ):
        raise AssertionError("reset encoder retained prior-epoch state")
    if not np.array_equal(
        audio_a[..., epoch_audio_offset:], audio_b[..., epoch_audio_offset:]
    ):
        raise AssertionError("reset decoder retained prior-epoch state")
    repeated_codes = encode(
        runtimes["encoder"],
        runtimes[default_encode_key],
        records,
        stream_a,
        DEFAULT_BANDWIDTH,
        resets,
    )
    if not np.array_equal(codes_a, repeated_codes):
        raise AssertionError("identical reset stream was not deterministic")
    print("epoch reset and recovery controls: 3/3 PASS")

    def require_shape_refusal(label: str, operation: object) -> None:
        try:
            operation()  # type: ignore[operator]
        except Exception as error:  # Runtime exception types vary by build.
            message = str(error).lower()
            if "dimension" not in message and "invalid" not in message:
                raise
            return
        raise AssertionError(f"fixed-shape graph accepted a double packet: {label}")

    require_shape_refusal(
        "encoder",
        lambda: run_packet(
            runtimes["encoder"],
            records["encoder"],
            source.numpy()[..., : PACKET_SAMPLES * 2],
            zero_states(records["encoder"]),
        ),
    )
    require_shape_refusal(
        "decoder",
        lambda: run_packet(
            runtimes["decoder"],
            records["decoder"],
            np.zeros((1, 128, PACKET_LATENT_FRAMES * 2), dtype=np.float32),
            zero_states(records["decoder"]),
        ),
    )
    for bandwidth, quantizers in BANDWIDTH_PROFILES:
        encode_key = rvq_graph_key("encode", bandwidth)
        decode_key = rvq_graph_key("decode", bandwidth)
        require_shape_refusal(
            encode_key,
            lambda key=encode_key: runtimes[key].run(
                None,
                {
                    records[key]["input_name"]: np.zeros(
                        (1, 128, PACKET_LATENT_FRAMES * 2), dtype=np.float32
                    )
                },
            ),
        )
        require_shape_refusal(
            decode_key,
            lambda key=decode_key, count=quantizers: runtimes[key].run(
                None,
                {
                    records[key]["input_name"]: np.zeros(
                        (count, 1, PACKET_LATENT_FRAMES * 2), dtype=np.int64
                    )
                },
            ),
        )
    print("fixed-shape negative controls: 8/8 PASS")

    listening = bundle / "listening"
    listening.mkdir(mode=0o700, exist_ok=False)
    continuous_codes = encode(
        runtimes["encoder"],
        runtimes[default_encode_key],
        records,
        stream_a,
        DEFAULT_BANDWIDTH,
    )
    continuous_audio = decode(
        runtimes[default_decode_key],
        runtimes["decoder"],
        records,
        continuous_codes,
        DEFAULT_BANDWIDTH,
    )
    write_wav(listening / "continuous.wav", continuous_audio)
    write_wav(listening / "epoch-reset.wav", audio_a)
    write_wav(listening / "synthetic-source.wav", stream_a)
    print("scratch-only listening fixtures: 3/3 generated; blind verdict 0/1")

    encoder_packet = stream_a[..., :PACKET_SAMPLES]
    measurements: dict[str, dict[str, Any]] = {}
    for bandwidth, _ in BANDWIDTH_PROFILES:
        label = f"{int(bandwidth)}kbps"
        encode_key = rvq_graph_key("encode", bandwidth)
        decode_key = rvq_graph_key("decode", bandwidth)
        measurements[label] = {
            "encode_pipeline": benchmark_encode_pipeline(
                runtimes["encoder"],
                records["encoder"],
                runtimes[encode_key],
                records[encode_key],
                encoder_packet,
                benchmark_repetitions,
            ),
            "decode_pipeline": benchmark_decode_pipeline(
                runtimes[decode_key],
                records[decode_key],
                runtimes["decoder"],
                records["decoder"],
                codes_by_profile[label][..., :PACKET_LATENT_FRAMES],
                benchmark_repetitions,
            ),
        }
    result = {
        "claim": "unfrozen-host-measurement-only",
        "manifest_sha256": sha256(bundle / "manifest.json"),
        "measurements": measurements,
        "parity": {
            "encoder_latent_max_abs_difference": float(latent_difference.max()),
            "profiles": parity_by_profile,
            "token_mismatches": total_token_mismatches,
            "token_population": total_token_population,
        },
        "schema": "kilix.encodec.export-verification/v2",
        "threads": threads,
    }
    result_path = bundle / "verification.json"
    result_path.write_bytes(canonical_json(result))
    os.chmod(result_path, 0o600)
    maximum_encode_p99 = max(
        profile["encode_pipeline"]["p99_ms"] for profile in measurements.values()
    )
    maximum_decode_p99 = max(
        profile["decode_pipeline"]["p99_ms"] for profile in measurements.values()
    )
    print(
        "unfrozen performance harness: 6/6 profile pipelines measured; "
        f"maximum_encode_p99_ms={maximum_encode_p99:.3f} "
        f"maximum_decode_p99_ms={maximum_decode_p99:.3f} "
        "accepted_H1_credit=0/1"
    )
    print("stateful multi-rate export technical controls: 48/48 PASS")


def self_test(skeleton: Path) -> None:
    passed = 0
    total = 7
    verify_skeleton(skeleton)
    passed += 1
    sample = {"b": 2, "a": 1}
    if canonical_json(sample) == b'{\n  "a": 1,\n  "b": 2\n}\n':
        passed += 1
    with tempfile.TemporaryDirectory(dir=os.environ.get("TMPDIR")) as directory:
        root = Path(directory)
        path = root / "sample.json"
        path.write_bytes(canonical_json(sample))
        if json.loads(path.read_bytes()) == sample:
            passed += 1
        target = root / "target"
        target.mkdir()
        link = root / "link"
        link.symlink_to(target, target_is_directory=True)
        try:
            from export_24khz import outside_repository

            outside_repository(link, "self-test symlink")
        except ValueError:
            passed += 1
        nonempty = root / "nonempty"
        nonempty.mkdir()
        (nonempty / "member").write_bytes(b"x")
        try:
            from export_24khz import prepare_output_directory

            prepare_output_directory(nonempty)
        except ValueError:
            passed += 1
    try:
        from export_24khz import outside_repository

        outside_repository(REPOSITORY / "forbidden", "self-test")
    except ValueError:
        passed += 1
    try:
        from export_24khz import prepare_output_directory

        prepare_output_directory(REPOSITORY / "forbidden-output")
    except ValueError:
        passed += 1
    print(f"export policy self-test: {passed}/{total} PASS")
    if passed != total:
        raise AssertionError("export policy self-test failed")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--skeleton", type=Path)
    modes.add_argument("--bundle", type=Path)
    modes.add_argument("--self-test", action="store_true")
    parser.add_argument("--checkpoint", type=Path)
    parser.add_argument("--seconds", type=int, default=1)
    parser.add_argument("--threads", type=int, choices=(1, 2, 4), default=1)
    parser.add_argument("--benchmark-repetitions", type=int, default=100)
    args = parser.parse_args()
    skeleton = REPOSITORY / "models/encodec-24khz-v1/manifest.json"
    try:
        if args.skeleton is not None:
            verify_skeleton(args.skeleton)
        elif args.self_test:
            self_test(skeleton)
        else:
            if args.checkpoint is None:
                parser.error("--checkpoint is required with --bundle")
            if args.seconds < 1 or args.seconds > 8:
                parser.error("--seconds must be between 1 and 8")
            if args.benchmark_repetitions < 20:
                parser.error("--benchmark-repetitions must be at least 20")
            verify_bundle(
                args.bundle,
                args.checkpoint,
                args.seconds,
                args.threads,
                args.benchmark_repetitions,
            )
    except (AssertionError, OSError, RuntimeError, ValueError) as error:
        parser.exit(1, f"verification refused: {error}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
