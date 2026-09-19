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
from typing import Any, Callable


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
#: Epochs in the golden post-reset stream: a cold start plus three resets.
GOLDEN_EPOCHS = 4
#: C5-R4 lead-in length, pinned here independently of tools/epoch_stream.py so
#: that the checkpoint reference cannot move with a changed runtime constant.
GOLDEN_PREROLL_PACKETS = 4
#: Latent frames counted as the head of an epoch.  Under reflect padding the
#: stock encoder diverges from a short stream start for latent frames 0-3, so
#: six frames cover that transient with margin.
POST_RESET_LATENT_FRAMES = 6
#: Decoded samples counted as the head of an epoch: 150 ms at 24 kHz.
POST_RESET_AUDIO_SAMPLES = 3_600
#: Added-latency probe packets: an epoch start and a mid-epoch packet.
LATENCY_PROBES = (25, 37)


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


#: Every key the skeleton manifest is expected to carry, with its exact value.
#: The verifier binds this as a SET as well as per-key, so an added key, a
#: removed key and a changed value are all caught.  Before this was a set, the
#: verifier checked 10 keys of a 12-key document and printed "10/10", so a
#: manifest asserting a full commercial licence grant passed at a full
#: denominator -- a control whose population was narrower than its subject.
SKELETON_EXPECTED: dict[str, object] = {
    "format": "kilix.encodec.asset/v1",
    "profile": "encodec-24khz-v1",
    "sample_rate": 24_000,
    "packet_samples": 960,
    "bandwidths_kbps": [3, 6, 12],
    "codebooks_by_bandwidth": {"3": 4, "6": 8, "12": 16},
    "codebook_cardinality": 1024,
    "license": {
        "evidence": "OD-AR-cc-by-nc-4.0-meta-platforms",
        "licensor": "Meta Platforms",
        "spdx": "CC-BY-NC-4.0",
    },
    "status": "p1-skeleton",
    "release_qualified": False,
    "artifacts": [],
    "delivery": "upstream-convert",
}


def verify_skeleton(
    path: Path, label: str = "manifest skeleton checks"
) -> tuple[int, int]:
    checks: list[bool] = []
    failures: list[str] = []

    def check(ok: bool, reason: str) -> None:
        checks.append(ok)
        if not ok:
            failures.append(reason)

    check(path.is_file() and not path.is_symlink(), f"{path} is not a regular file")
    document = json.loads(path.read_text(encoding="utf-8"))

    # Bind the key SET in both directions, so an unexpected key is a failure
    # and not merely something the loop never looks at.
    missing = sorted(set(SKELETON_EXPECTED) - set(document))
    extra = sorted(set(document) - set(SKELETON_EXPECTED))
    check(
        not missing and not extra,
        f"key set differs: missing={missing} unexpected={extra}",
    )

    for key, expected in sorted(SKELETON_EXPECTED.items()):
        actual = document.get(key)
        # `release_qualified` must be the JSON boolean false, not 0 or "".
        ok = actual is False if expected is False else actual == expected
        check(ok, f"{key}: expected {expected!r}, found {actual!r}")

    passed = sum(checks)
    total = len(checks)
    for reason in failures:
        print(f"  skeleton check failed: {reason}")
    print(
        f"{label}: {passed}/{total} "
        f"{'PASS' if passed == total else 'FAIL'}"
    )
    if passed != total:
        raise AssertionError("manifest skeleton differs")
    return passed, total


def verify_policy_and_licence(value: dict[str, Any]) -> None:
    """Assert the export manifest's artifact policy and OD-AR licence (SR-5).

    Pure, so --self-test can feed it planted manifests without a checkpoint.
    """
    expected_policy = {
        "checkpoint_delivery": "upstream-convert",
        "derived_graph_publication": "user-machine-only-never-published",
        "native_runtime_downloads": False,
        "release_qualified": False,
    }
    if value.get("artifact_policy") != expected_policy:
        raise AssertionError("artifact policy differs")
    expected_license = {
        "evidence": "OD-AR-cc-by-nc-4.0-meta-platforms",
        "licensor": "Meta Platforms",
        "spdx": "CC-BY-NC-4.0",
    }
    if value.get("license") != expected_license:
        raise AssertionError("license determination differs")


def verify_policy_and_licence_controls() -> tuple[int, int]:
    """Admit the SR-5 values; refuse each planted change to them.

    The accepted values are typed here, not read from the checker, so a
    changed expectation fails the admission control and a dropped or
    narrowed check fails a refusal control.
    """
    policy = {
        "checkpoint_delivery": "upstream-convert",
        "derived_graph_publication": "user-machine-only-never-published",
        "native_runtime_downloads": False,
        "release_qualified": False,
    }
    licence = {
        "evidence": "OD-AR-cc-by-nc-4.0-meta-platforms",
        "licensor": "Meta Platforms",
        "spdx": "CC-BY-NC-4.0",
    }

    def manifest(**changes: object) -> dict[str, Any]:
        value: dict[str, Any] = {
            "artifact_policy": dict(policy),
            "license": dict(licence),
        }
        for key, change in changes.items():
            if change is None:
                del value[key]
            else:
                value[key] = change
        return value

    licence_refused = "license determination differs"
    policy_refused = "artifact policy differs"
    refusals: list[tuple[str, dict[str, Any], str]] = [
        ("licence absent", manifest(license=None), licence_refused),
        ("licence empty", manifest(license={}), licence_refused),
        ("spdx MIT", manifest(license={**licence, "spdx": "MIT"}), licence_refused),
        ("licensor truncated", manifest(license={**licence, "licensor": "Meta"}),
         licence_refused),
        ("evidence changed",
         manifest(license={**licence, "evidence": "no-redistribution-grant-found"}),
         licence_refused),
        ("licensor removed",
         manifest(license={k: v for k, v in licence.items() if k != "licensor"}),
         licence_refused),
        ("licence key added", manifest(license={**licence, "grant": "commercial"}),
         licence_refused),
        ("policy absent", manifest(artifact_policy=None), policy_refused),
        ("delivery user-supplied",
         manifest(artifact_policy={**policy, "checkpoint_delivery": "user-supplied-only"}),
         policy_refused),
        ("publication reverted",
         manifest(artifact_policy={
             **policy,
             "derived_graph_publication": "forbidden-without-separate-model-grant",
         }),
         policy_refused),
        ("release qualified",
         manifest(artifact_policy={**policy, "release_qualified": True}), policy_refused),
        ("runtime downloads",
         manifest(artifact_policy={**policy, "native_runtime_downloads": True}),
         policy_refused),
    ]
    passed = 0
    total = 1 + len(refusals)
    try:
        verify_policy_and_licence(manifest())
    except AssertionError as error:
        print(f"  policy and licence control failed: SR-5 values refused: {error}")
    else:
        passed += 1
    for label, value, expected in refusals:
        try:
            verify_policy_and_licence(value)
        except AssertionError as error:
            if str(error) == expected:
                passed += 1
                continue
            print(f"  policy and licence control failed: {label}: refused with {error!r}")
        else:
            print(f"  policy and licence control failed: {label}: admitted")
    print(
        f"export policy and licence controls: {passed}/{total} "
        f"{'PASS' if passed == total else 'FAIL'}"
    )
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
    verify_policy_and_licence(value)

    model, identity = load_model(checkpoint)
    expected_checkpoint = {
        "bytes": identity.bytes,
        "file": identity.file,
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


def epoch_parity_rows(
    reference: object, actual: object, epoch_length: int, head_length: int
) -> list[dict[str, float]]:
    """Maximum absolute difference in each epoch's head and remainder."""

    import numpy as np

    if reference.shape != actual.shape:
        raise AssertionError(
            f"epoch parity shapes differ: {reference.shape} != {actual.shape}"
        )
    length = reference.shape[-1]
    if length == 0 or length % epoch_length or head_length >= epoch_length:
        raise AssertionError("epoch parity population differs")
    rows = []
    for start in range(0, length, epoch_length):
        difference = np.abs(
            reference[..., start : start + epoch_length]
            - actual[..., start : start + epoch_length]
        )
        rows.append(
            {
                "head_max_abs_difference": float(difference[..., :head_length].max()),
                "steady_max_abs_difference": float(
                    difference[..., head_length:].max()
                ),
            }
        )
    return rows


def epochs_within(rows: list[dict[str, float]], tolerance: float) -> int:
    return sum(
        row["head_max_abs_difference"] < tolerance
        and row["steady_max_abs_difference"] < tolerance
        for row in rows
    )


def product_encoder(
    runtimes: dict[str, object],
    records: dict[str, Any],
    bandwidth: float,
    preroll: int | None = None,
) -> object:
    """The C5-R4 epoch-start encoder (tools/epoch_stream.py) on these graphs."""

    from epoch_stream import EPOCH_START_C5_R4, StreamEncoder

    key = rvq_graph_key("encode", bandwidth)
    return StreamEncoder(
        runtimes["encoder"],
        records["encoder"],
        runtimes[key],
        records[key],
        profile=EPOCH_START_C5_R4,
        preroll=preroll,
    )


def product_decoder(
    runtimes: dict[str, object],
    records: dict[str, Any],
    bandwidth: float,
    preroll: int | None = None,
) -> object:
    from epoch_stream import EPOCH_START_C5_R4, StreamDecoder

    key = rvq_graph_key("decode", bandwidth)
    return StreamDecoder(
        runtimes[key],
        records[key],
        runtimes["decoder"],
        records["decoder"],
        profile=EPOCH_START_C5_R4,
        preroll=preroll,
    )


def product_render(
    runtimes: dict[str, object],
    records: dict[str, Any],
    bandwidth: float,
    audio: object,
    epoch_packets: int | None = EPOCH_PACKETS,
) -> tuple[object, object, object]:
    """Latent, codes and decoded audio of one product stream."""

    from epoch_stream import decode_stream, encode_stream

    latent, codes = encode_stream(
        product_encoder(runtimes, records, bandwidth), audio, epoch_packets
    )
    audio_out = decode_stream(
        product_decoder(runtimes, records, bandwidth), codes, epoch_packets
    )
    return latent, codes, audio_out


def verify_post_reset_preroll_golden(
    oracle: object, runtimes: dict[str, object], records: dict[str, Any]
) -> tuple[int, dict[str, Any]]:
    """Hold every epoch start to the repeat pre-roll checkpoint definition.

    Successor of verify_post_reset_golden, which held a zero-state reset to a
    constant-padded cold start per epoch. Owner decision OD-AL replaced that
    behaviour with a four-packet repeat pre-roll at every epoch start,
    including the stream start.

    The reference renders each epoch separately with the checkpoint's own
    modules and constant padding: the encoder over the epoch's first packet
    tiled four times followed by the epoch, without the first 12 latent
    frames, then the quantizer; the decoder over the quantized lead-in codes
    (the epoch's first 3 code frames tiled four times) followed by the epoch's
    codes, without the first 3840 samples. The product runtime renders the
    same audio as one stream. Two alternative references must be refused by
    the same comparison at every epoch head: the constant cold start the
    product used before, and a reflect-padded pre-roll.

    The lead-in length of the reference is GOLDEN_PREROLL_PACKETS, a literal 4
    pinned in this file, never the runtime's own constant. The product's
    lead-in run count at a stream start is also counted on the graph sessions
    and must equal that literal on both sides.
    """

    import numpy as np
    import torch
    from encodec.modules import SConv1d

    from stateful_graph import (
        DEFAULT_BANDWIDTH,
        PACKET_LATENT_FRAMES,
        PACKET_SAMPLES,
        constant_padding,
    )

    class CountingSession:
        def __init__(self, session: object) -> None:
            self.session = session
            self.runs = 0

        def run(self, outputs: object, feed: dict[str, object]) -> object:
            self.runs += 1
            return self.session.run(outputs, feed)

    counted = dict(runtimes)
    counted["encoder"] = CountingSession(runtimes["encoder"])
    counted["decoder"] = CountingSession(runtimes["decoder"])
    lead_in_probe = signal(1, seed=17).numpy()[..., : 2 * PACKET_SAMPLES]
    product_render(counted, records, DEFAULT_BANDWIDTH, lead_in_probe)
    lead_in_runs = (counted["encoder"].runs - 2, counted["decoder"].runs - 2)
    if lead_in_runs != (GOLDEN_PREROLL_PACKETS, GOLDEN_PREROLL_PACKETS):
        raise AssertionError(
            f"C5-R4 lead-in run count differs from the pinned {GOLDEN_PREROLL_PACKETS}: "
            f"encoder {lead_in_runs[0]}, decoder {lead_in_runs[1]}"
        )
    print(
        f"pre-roll lead-in length: encoder {lead_in_runs[0]} and decoder "
        f"{lead_in_runs[1]} runs at the stream start, pinned "
        f"{GOLDEN_PREROLL_PACKETS}: 2/2 PASS"
    )

    # Padding sites are switched and restored rather than copying the model:
    # weight-normed modules cannot be deep-copied before a no-grad forward.
    padding_sites = [
        child
        for network in (oracle.encoder, oracle.decoder)
        for child in network.modules()
        if isinstance(child, SConv1d)
    ]

    def select_padding(mode: str) -> None:
        for child in padding_sites:
            child.pad_mode = mode

    constant_padding(oracle.encoder)
    constant_padding(oracle.decoder)
    if not padding_sites or any(
        child.pad_mode != "constant" for child in padding_sites
    ):
        raise AssertionError("oracle padding sites are not all constant")

    epochs = GOLDEN_EPOCHS
    resets = {EPOCH_PACKETS * index for index in range(1, epochs)}
    epoch_samples = EPOCH_PACKETS * PACKET_SAMPLES
    epoch_frames = EPOCH_PACKETS * PACKET_LATENT_FRAMES
    source = signal(epochs, seed=13)
    segments = [
        source[..., index * epoch_samples : (index + 1) * epoch_samples]
        for index in range(epochs)
    ]

    def render_latent(preroll: int) -> object:
        pieces = []
        for segment in segments:
            joined = torch.cat(
                [segment[..., :PACKET_SAMPLES]] * preroll + [segment], dim=-1
            )
            pieces.append(
                oracle.encoder(joined)[..., preroll * PACKET_LATENT_FRAMES :]
            )
        return torch.cat(pieces, dim=-1)

    def render_audio(codes: object, preroll: int) -> object:
        pieces = []
        for index in range(epochs):
            epoch_codes = codes[..., index * epoch_frames : (index + 1) * epoch_frames]
            joined = torch.cat(
                [epoch_codes[..., :PACKET_LATENT_FRAMES]] * preroll + [epoch_codes],
                dim=-1,
            )
            start = preroll * PACKET_SAMPLES
            pieces.append(
                oracle.decoder(oracle.quantizer.decode(joined))[
                    ..., start : start + epoch_samples
                ]
            )
        return torch.cat(pieces, dim=-1)

    with torch.no_grad():
        reference_latent = render_latent(GOLDEN_PREROLL_PACKETS)
        reference_codes = oracle.quantizer.encode(
            reference_latent, oracle.frame_rate, DEFAULT_BANDWIDTH
        )
        reference_audio = render_audio(reference_codes, GOLDEN_PREROLL_PACKETS)
        cold_latent = render_latent(0)
        cold_audio = render_audio(reference_codes, 0)
        try:
            select_padding("reflect")
            reflect_latent = render_latent(GOLDEN_PREROLL_PACKETS)
            reflect_audio = render_audio(reference_codes, GOLDEN_PREROLL_PACKETS)
        finally:
            select_padding("constant")
    if any(child.pad_mode != "constant" for child in padding_sites):
        raise AssertionError("oracle padding sites were not restored")
    reference_latent = reference_latent.numpy()
    reference_codes = reference_codes.numpy()
    reference_audio = reference_audio.numpy()

    actual_latent, actual_codes, actual_audio = product_render(
        runtimes, records, DEFAULT_BANDWIDTH, source.numpy()
    )
    latent_rows = epoch_parity_rows(
        reference_latent, actual_latent, epoch_frames, POST_RESET_LATENT_FRAMES
    )
    latent_passed = epochs_within(latent_rows, LATENT_TOLERANCE)
    if latent_passed != epochs:
        raise AssertionError(
            f"pre-roll encoder latent parity exceeds tolerance: "
            f"{latent_passed}/{epochs} epochs within "
            f"{[round(row['head_max_abs_difference'], 6) for row in latent_rows]}"
        )
    print(
        f"pre-roll encoder latent parity: {latent_passed}/{epochs} epochs PASS "
        f"head{POST_RESET_LATENT_FRAMES}_max="
        f"{max(row['head_max_abs_difference'] for row in latent_rows):.3e} "
        f"steady_max="
        f"{max(row['steady_max_abs_difference'] for row in latent_rows):.3e}"
    )

    token_rows = [
        int(
            np.count_nonzero(
                reference_codes[..., index * epoch_frames : (index + 1) * epoch_frames]
                != actual_codes[..., index * epoch_frames : (index + 1) * epoch_frames]
            )
        )
        for index in range(epochs)
    ]
    tokens_passed = sum(count == 0 for count in token_rows)
    if tokens_passed != epochs:
        raise AssertionError(
            f"pre-roll token identity differs: {tokens_passed}/{epochs} epochs "
            f"mismatches={token_rows}"
        )
    print(
        f"pre-roll token identity: {tokens_passed}/{epochs} epochs PASS "
        f"token_mismatches={sum(token_rows)}/{reference_codes.size}"
    )

    audio_rows = epoch_parity_rows(
        reference_audio, actual_audio, epoch_samples, POST_RESET_AUDIO_SAMPLES
    )
    audio_passed = epochs_within(audio_rows, WAVEFORM_TOLERANCE)
    if audio_passed != epochs:
        raise AssertionError(
            f"pre-roll decoder waveform parity exceeds tolerance: "
            f"{audio_passed}/{epochs} epochs within "
            f"{[round(row['head_max_abs_difference'], 6) for row in audio_rows]}"
        )
    print(
        f"pre-roll decoder waveform parity: {audio_passed}/{epochs} epochs PASS "
        f"head150ms_max="
        f"{max(row['head_max_abs_difference'] for row in audio_rows):.3e} "
        f"steady_max="
        f"{max(row['steady_max_abs_difference'] for row in audio_rows):.3e}"
    )

    recovered = 0
    for reset in sorted(resets):
        index = reset // EPOCH_PACKETS
        _, fresh_codes, _ = product_render(
            runtimes,
            records,
            DEFAULT_BANDWIDTH,
            source.numpy()[..., index * epoch_samples : (index + 1) * epoch_samples],
        )
        stream_codes = np.ascontiguousarray(
            actual_codes[..., index * epoch_frames : (index + 1) * epoch_frames]
        )
        from epoch_stream import decode_stream

        fresh_audio = decode_stream(
            product_decoder(runtimes, records, DEFAULT_BANDWIDTH), stream_codes
        )
        recovered += int(
            np.array_equal(stream_codes, fresh_codes)
            and np.array_equal(
                actual_audio[..., index * epoch_samples : (index + 1) * epoch_samples],
                fresh_audio,
            )
        )
    if recovered != len(resets):
        raise AssertionError(
            f"epoch differs from a fresh stream over that epoch: {recovered}/{len(resets)}"
        )
    print(
        f"epoch recovery equals a fresh pre-rolled stream: "
        f"{recovered}/{len(resets)} resets PASS"
    )

    alternatives = {
        "constant_cold_start": (cold_latent.numpy(), cold_audio.numpy()),
        "reflect_pre_roll": (reflect_latent.numpy(), reflect_audio.numpy()),
    }
    refusal: dict[str, Any] = {}
    refused = 0
    for label, (latent, audio) in alternatives.items():
        latent_heads = epoch_parity_rows(
            reference_latent, latent, epoch_frames, POST_RESET_LATENT_FRAMES
        )
        audio_heads = epoch_parity_rows(
            reference_audio, audio, epoch_samples, POST_RESET_AUDIO_SAMPLES
        )
        count = sum(
            row["head_max_abs_difference"] >= LATENT_TOLERANCE for row in latent_heads
        ) + sum(
            row["head_max_abs_difference"] >= WAVEFORM_TOLERANCE for row in audio_heads
        )
        refused += count
        refusal[label] = {
            "latent": latent_heads,
            "waveform": audio_heads,
            "refused_epoch_heads": count,
        }
        print(
            f"{label.replace('_', '-')} negative control: {count}/{2 * epochs} epoch "
            f"heads refused latent_head_min="
            f"{min(row['head_max_abs_difference'] for row in latent_heads):.3e} "
            f"waveform_head_min="
            f"{min(row['head_max_abs_difference'] for row in audio_heads):.3e}"
        )
    if refused != 2 * 2 * epochs:
        raise AssertionError(
            f"an alternative epoch-start reference was not refused at every "
            f"epoch head: {refused}/{4 * epochs}"
        )

    controls = 2 + latent_passed + tokens_passed + audio_passed + recovered + refused
    return controls, {
        "epochs": epochs,
        "reset_packets": sorted(resets),
        "preroll_packets": GOLDEN_PREROLL_PACKETS,
        "lead_in_runs": {"encoder": lead_in_runs[0], "decoder": lead_in_runs[1]},
        "latent": latent_rows,
        "token_mismatches": token_rows,
        "waveform": audio_rows,
        "recovered_resets": recovered,
        "negative_controls": refusal,
    }


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
        "audio_duration_seconds": repetitions * 0.04,
        "calls_at_or_above_20ms": sum(value >= 20.0 for value in timings),
        "maximum_ms": max(timings),
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
        "audio_duration_seconds": repetitions * 0.04,
        "calls_at_or_above_20ms": sum(value >= 20.0 for value in timings),
        "maximum_ms": max(timings),
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
    fixture_tier: str,
    fixture_runner: Path | None,
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

    fixture = None
    if fixture_tier == "h1":
        if fixture_runner is None:
            raise ValueError("--fixture-runner is required for an H1 measurement")
        from capacity_fixture import inspect_h1

        fixture = inspect_h1(fixture_runner)
    elif fixture_runner is not None:
        raise ValueError("--fixture-runner requires --fixture-tier h1")

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

    from epoch_stream import EPOCH_START, added_lookahead_packets, decode_stream

    epoch_code_offset = EPOCH_PACKETS * PACKET_LATENT_FRAMES
    epoch_audio_offset = EPOCH_PACKETS * PACKET_SAMPLES
    leak_source = signal(3, seed=11).numpy()
    _, leak_codes, leak_audio = product_render(
        runtimes, records, DEFAULT_BANDWIDTH, leak_source
    )
    independence_rows = []
    for epoch in (1, 2):
        corrupted_audio = leak_source.copy()
        corrupted_audio[..., : epoch * epoch_audio_offset] = (
            np.random.default_rng(31 + epoch)
            .standard_normal(epoch * epoch_audio_offset)
            .astype(np.float32)
            * np.float32(0.1)
        )
        _, audio_codes, _ = product_render(
            runtimes, records, DEFAULT_BANDWIDTH, corrupted_audio
        )
        split = epoch * epoch_code_offset
        if np.array_equal(audio_codes[..., :split], leak_codes[..., :split]):
            raise AssertionError("audio corruption control is vacuous")
        if not np.array_equal(audio_codes[..., split:], leak_codes[..., split:]):
            raise AssertionError(
                f"corrupted audio before epoch {epoch} changed its codes: "
                "the encoder epoch start retained prior-epoch state"
            )
        corrupted_codes = leak_codes.copy()
        corrupted_codes[..., :split] = np.random.default_rng(41 + epoch).integers(
            0, 1024, size=corrupted_codes[..., :split].shape
        )
        codes_audio = decode_stream(
            product_decoder(runtimes, records, DEFAULT_BANDWIDTH), corrupted_codes
        )
        cut = epoch * epoch_audio_offset
        if np.array_equal(codes_audio[..., :cut], leak_audio[..., :cut]):
            raise AssertionError("code corruption control is vacuous")
        if not np.array_equal(codes_audio[..., cut:], leak_audio[..., cut:]):
            raise AssertionError(
                f"corrupted codes before epoch {epoch} changed its audio: "
                "the decoder epoch start retained prior-epoch state"
            )
        independence_rows.append(
            {"epoch": epoch, "audio_corruption_codes_identical": True,
             "code_corruption_audio_identical": True}
        )
    _, repeated_codes, repeated_audio = product_render(
        runtimes, records, DEFAULT_BANDWIDTH, leak_source
    )
    if not (
        np.array_equal(leak_codes, repeated_codes)
        and np.array_equal(leak_audio, repeated_audio)
    ):
        raise AssertionError("identical epoch-start stream was not deterministic")
    print("epoch-start independence and determinism controls: 5/5 PASS")

    probe_source = signal(2, seed=1).numpy()

    def probe_noise(count: int) -> object:
        return np.random.default_rng(777).standard_normal(count).astype(
            np.float32
        ) * np.float32(0.1)

    def render_probe(value: object) -> object:
        return product_render(runtimes, records, DEFAULT_BANDWIDTH, value)[2]

    def lookahead_plant(value: object) -> object:
        shifted = np.zeros_like(value)
        shifted[..., :-PACKET_SAMPLES] = value[..., PACKET_SAMPLES:]
        return render_probe(shifted)

    measured_lookahead = added_lookahead_packets(
        render_probe, probe_source, LATENCY_PROBES, probe_noise
    )
    planted_lookahead = added_lookahead_packets(
        lookahead_plant, probe_source, LATENCY_PROBES, probe_noise
    )
    if measured_lookahead != 0:
        raise AssertionError(
            f"epoch-start runtime adds lookahead: {measured_lookahead} packets"
        )
    if planted_lookahead != 1:
        raise AssertionError(
            f"latency probe missed a one-packet lookahead plant: {planted_lookahead}"
        )
    print(
        f"added algorithmic latency probe: {measured_lookahead} packets at "
        f"packets {list(LATENCY_PROBES)}; one-packet lookahead plant reads "
        f"{planted_lookahead}: 2/2 PASS"
    )
    golden_controls, golden = verify_post_reset_preroll_golden(oracle, runtimes, records)

    def require_shape_refusal(label: str, operation: Callable[[], object]) -> None:
        try:
            operation()
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
    stream_a = torch.cat((signal(1, seed=7), signal(1, seed=9)), dim=-1).numpy()
    _, _, audio_a = product_render(runtimes, records, DEFAULT_BANDWIDTH, stream_a)
    # The continuous fixture starts the same way (pre-roll at the stream start)
    # and never resets again, so the pair differs only from the epoch boundary.
    _, _, continuous_audio = product_render(
        runtimes, records, DEFAULT_BANDWIDTH, stream_a, epoch_packets=None
    )
    epoch_audio_offset = EPOCH_PACKETS * PACKET_SAMPLES
    if not np.array_equal(
        continuous_audio[..., :epoch_audio_offset], audio_a[..., :epoch_audio_offset]
    ) or np.array_equal(
        continuous_audio[..., epoch_audio_offset:], audio_a[..., epoch_audio_offset:]
    ):
        raise AssertionError("listening pair must differ only from the epoch boundary")
    write_wav(listening / "continuous.wav", continuous_audio)
    write_wav(listening / "epoch-reset.wav", audio_a)
    write_wav(listening / "synthetic-source.wav", stream_a)
    print(
        "scratch-only listening fixtures: 3/3 generated; pair identical before "
        "the epoch boundary 1/1; blind verdict 0/1"
    )

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
        for row in measurements[label].values():
            row["packet_duration_ms"] = 40.0
            row["realtime_multiple_at_p99"] = 40.0 / row["p99_ms"]

    h1_thresholds_passed = sum(
        row["p99_ms"] < 20.0
        for profile in measurements.values()
        for row in profile.values()
    )
    h1_thresholds_total = sum(
        1 for profile in measurements.values() for _ in profile.values()
    )
    h1_measured_pass = (
        fixture is not None and h1_thresholds_passed == h1_thresholds_total
    )
    result = {
        "claim": (
            "frozen-H1-measurement"
            if fixture is not None
            else "unfrozen-host-measurement-only"
        ),
        "fixture": fixture,
        "h1_gate": {
            "measured_pass": h1_measured_pass,
            "p99_below_20ms_and_realtime_at_least_2x": {
                "passed": h1_thresholds_passed,
                "total": h1_thresholds_total,
            },
        },
        "manifest_sha256": sha256(bundle / "manifest.json"),
        "measurements": measurements,
        "parity": {
            "encoder_latent_max_abs_difference": float(latent_difference.max()),
            "profiles": parity_by_profile,
            "token_mismatches": total_token_mismatches,
            "token_population": total_token_population,
        },
        "epoch_independence": independence_rows,
        "epoch_start": EPOCH_START,
        "latency_probe": {
            "measured_packets": measured_lookahead,
            "one_packet_plant_packets": planted_lookahead,
            "probe_packets": list(LATENCY_PROBES),
        },
        "post_reset_preroll_golden": golden,
        "schema": "kilix.encodec.export-verification/v3",
        "verification_sources": {
            "capacity_fixture.py": sha256(REPOSITORY / "tools/capacity_fixture.py"),
            "epoch_stream.py": sha256(REPOSITORY / "tools/epoch_stream.py"),
            "verify_export.py": sha256(Path(__file__).resolve()),
        },
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
    label = "frozen H1" if fixture is not None else "unfrozen"
    print(
        f"{label} performance harness: "
        f"{h1_thresholds_passed}/{h1_thresholds_total} profile pipelines "
        "below 20ms p99 and at least 2x real-time; "
        f"maximum_encode_p99_ms={maximum_encode_p99:.3f} "
        f"maximum_decode_p99_ms={maximum_decode_p99:.3f} "
        f"measured_H1_gate={int(h1_measured_pass)}/1"
    )
    # Controls before the golden: bundle identity 8, runtime contracts 8,
    # encoder parity 1, profile parity 9, RVQ nesting 2, epoch-start
    # independence and determinism 5, latency probe and its plant 2,
    # fixed-shape refusals 8, listening fixtures 3 and their boundary-only
    # pair 1, profile timing pipelines 6.
    base_controls = 8 + 8 + 1 + 9 + 2 + 5 + 2 + 8 + 3 + 1 + 6
    # Pre-roll golden controls: latent, token and waveform parity per epoch,
    # recovery per reset, and two refused alternative references for each
    # latent and waveform epoch head.
    golden_total = 2 + 3 * GOLDEN_EPOCHS + (GOLDEN_EPOCHS - 1) + 4 * GOLDEN_EPOCHS
    print(
        "stateful multi-rate export technical controls: "
        f"{base_controls + golden_controls}/{base_controls + golden_total} PASS"
    )
    if fixture is not None and not h1_measured_pass:
        raise AssertionError(
            f"frozen H1 performance gate failed: "
            f"{h1_thresholds_passed}/{h1_thresholds_total}"
        )


def self_test(skeleton: Path) -> None:
    passed = 0
    total = 7
    # Labelled distinctly: `make test` also runs `--skeleton` on the same
    # manifest, and two identical lines left a reader no way to tell which
    # mode a failure came from.
    verify_skeleton(skeleton, label="self-test manifest skeleton checks")
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
    controls_passed, controls_total = verify_policy_and_licence_controls()
    if controls_passed != controls_total:
        raise AssertionError("export policy and licence controls failed")


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
    parser.add_argument(
        "--fixture-tier", choices=("unfrozen", "h1"), default="unfrozen"
    )
    parser.add_argument("--fixture-runner", type=Path)
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
            if args.fixture_tier == "h1" and args.benchmark_repetitions < 1000:
                parser.error("H1 measurement requires at least 1000 repetitions")
            verify_bundle(
                args.bundle,
                args.checkpoint,
                args.seconds,
                args.threads,
                args.benchmark_repetitions,
                args.fixture_tier,
                args.fixture_runner,
            )
    except (AssertionError, OSError, RuntimeError, ValueError) as error:
        parser.exit(1, f"verification refused: {error}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
