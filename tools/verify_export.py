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
EXPECTED_GRAPHS = {"decoder", "encoder", "rvq_decode", "rvq_encode"}
LATENT_TOLERANCE = 1e-4
WAVEFORM_TOLERANCE = 1e-4
EPOCH_PACKETS = 25


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
    if value.get("schema") != "kilix.encodec.stateful-onnx-export/v1":
        raise AssertionError("manifest schema differs")
    if value.get("profile") != "encodec-24khz-causal-mono-6kbps":
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
    print(f"bundle identity and ONNX checks: {graph_checks}/4 PASS")
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
    resets: set[int] | None = None,
) -> object:
    from stateful_graph import PACKET_LATENT_FRAMES, PACKET_SAMPLES

    latent = run_stateful_stream(
        encoder, records["encoder"], source, PACKET_SAMPLES, resets
    )
    return run_stateless_stream(
        rvq, records["rvq_encode"], latent, PACKET_LATENT_FRAMES
    )


def decode(
    rvq: object,
    decoder: object,
    records: dict[str, Any],
    codes: object,
    resets: set[int] | None = None,
) -> object:
    from stateful_graph import PACKET_LATENT_FRAMES

    quantized = run_stateless_stream(
        rvq, records["rvq_decode"], codes, PACKET_LATENT_FRAMES
    )
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
        PACKET_LATENT_FRAMES,
        PACKET_SAMPLES,
        TARGET_BANDWIDTH,
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
    print("runtime graph contracts: 4/4 PASS")

    source = signal(seconds, seed=1)
    constant_padding(oracle.encoder)
    constant_padding(oracle.decoder)
    with torch.no_grad():
        reference_latent = oracle.encoder(source)
        reference_codes = oracle.quantizer.encode(
            reference_latent, oracle.frame_rate, TARGET_BANDWIDTH
        )
        reference_quantized = oracle.quantizer.decode(reference_codes)
        reference_audio = oracle.decoder(reference_quantized)

    actual_latent = run_stateful_stream(
        runtimes["encoder"], records["encoder"], source.numpy(), PACKET_SAMPLES
    )
    latent_difference = np.abs(reference_latent.numpy() - actual_latent)
    if float(latent_difference.max()) >= LATENT_TOLERANCE:
        raise AssertionError("encoder latent parity exceeds tolerance")
    actual_codes = run_stateless_stream(
        runtimes["rvq_encode"],
        records["rvq_encode"],
        actual_latent,
        PACKET_LATENT_FRAMES,
    )
    token_mismatches = int(np.count_nonzero(reference_codes.numpy() != actual_codes))
    if token_mismatches != 0:
        raise AssertionError(
            f"token identity differs: {token_mismatches}/{reference_codes.numel()}"
        )
    actual_quantized = run_stateless_stream(
        runtimes["rvq_decode"],
        records["rvq_decode"],
        actual_codes,
        PACKET_LATENT_FRAMES,
    )
    quantized_difference = np.abs(reference_quantized.numpy() - actual_quantized)
    if float(quantized_difference.max()) >= LATENT_TOLERANCE:
        raise AssertionError("RVQ decode parity exceeds tolerance")
    actual_audio = run_stateful_stream(
        runtimes["decoder"],
        records["decoder"],
        actual_quantized,
        PACKET_LATENT_FRAMES,
    )
    expected_audio = reference_audio.numpy()[..., : actual_audio.shape[-1]]
    audio_difference = np.abs(expected_audio - actual_audio)
    if float(audio_difference.max()) >= WAVEFORM_TOLERANCE:
        raise AssertionError("decoder waveform parity exceeds tolerance")
    print(
        "ONNX/oracle parity: 4/4 PASS "
        f"latent_max={latent_difference.max():.3e} token_mismatches=0/"
        f"{reference_codes.numel()} quantized_max={quantized_difference.max():.3e} "
        f"waveform_max={audio_difference.max():.3e}"
    )

    first_a = signal(1, seed=7)
    first_b = signal(1, seed=8)
    shared = signal(1, seed=9)
    stream_a = torch.cat((first_a, shared), dim=-1).numpy()
    stream_b = torch.cat((first_b, shared), dim=-1).numpy()
    resets = {EPOCH_PACKETS}
    codes_a = encode(
        runtimes["encoder"], runtimes["rvq_encode"], records, stream_a, resets
    )
    audio_a = decode(
        runtimes["rvq_decode"], runtimes["decoder"], records, codes_a, resets
    )
    codes_b = encode(
        runtimes["encoder"], runtimes["rvq_encode"], records, stream_b, resets
    )
    audio_b = decode(
        runtimes["rvq_decode"], runtimes["decoder"], records, codes_b, resets
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
        runtimes["encoder"], runtimes["rvq_encode"], records, stream_a, resets
    )
    if not np.array_equal(codes_a, repeated_codes):
        raise AssertionError("identical reset stream was not deterministic")
    print("epoch reset and recovery controls: 3/3 PASS")

    wrong_shape_refused = 0
    try:
        run_packet(
            runtimes["encoder"],
            records["encoder"],
            source.numpy()[..., : PACKET_SAMPLES * 2],
            zero_states(records["encoder"]),
        )
    except Exception as error:  # ONNX Runtime exception types vary by build.
        if "dimension" not in str(error).lower() and "invalid" not in str(error).lower():
            raise
        wrong_shape_refused = 1
    if wrong_shape_refused != 1:
        raise AssertionError("fixed-shape encoder accepted a double packet")
    print("fixed-shape negative controls: 1/1 PASS")

    listening = bundle / "listening"
    listening.mkdir(mode=0o700, exist_ok=False)
    continuous_codes = encode(
        runtimes["encoder"], runtimes["rvq_encode"], records, stream_a
    )
    continuous_audio = decode(
        runtimes["rvq_decode"], runtimes["decoder"], records, continuous_codes
    )
    write_wav(listening / "continuous.wav", continuous_audio)
    write_wav(listening / "epoch-reset.wav", audio_a)
    write_wav(listening / "synthetic-source.wav", stream_a)
    print("scratch-only listening fixtures: 3/3 generated; blind verdict 0/1")

    encoder_packet = stream_a[..., :PACKET_SAMPLES]
    measurements = {
        "encode_pipeline": benchmark_encode_pipeline(
            runtimes["encoder"],
            records["encoder"],
            runtimes["rvq_encode"],
            records["rvq_encode"],
            encoder_packet,
            benchmark_repetitions,
        ),
        "decode_pipeline": benchmark_decode_pipeline(
            runtimes["rvq_decode"],
            records["rvq_decode"],
            runtimes["decoder"],
            records["decoder"],
            codes_a[..., :PACKET_LATENT_FRAMES],
            benchmark_repetitions,
        ),
    }
    result = {
        "claim": "unfrozen-host-measurement-only",
        "manifest_sha256": sha256(bundle / "manifest.json"),
        "measurements": measurements,
        "parity": {
            "audio_max_abs_difference": float(audio_difference.max()),
            "latent_max_abs_difference": float(latent_difference.max()),
            "quantized_max_abs_difference": float(quantized_difference.max()),
            "token_mismatches": token_mismatches,
            "token_population": reference_codes.numel(),
        },
        "schema": "kilix.encodec.export-verification/v1",
        "threads": threads,
    }
    result_path = bundle / "verification.json"
    result_path.write_bytes(canonical_json(result))
    os.chmod(result_path, 0o600)
    print(
        "unfrozen performance harness: 2/2 pipelines measured; "
        f"encode_p99_ms={measurements['encode_pipeline']['p99_ms']:.3f} "
        f"decode_p99_ms={measurements['decode_pipeline']['p99_ms']:.3f} "
        "accepted_H1_credit=0/1"
    )
    print("stateful export technical controls: 16/16 PASS")


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
