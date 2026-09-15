#!/usr/bin/env python3
"""Hold the product epoch-start runtime to the checked F101 remedy programme.

The expectations file records, for all 14 programme items of the F101 remedy
spike, the render identities of the repeat pre-roll arm (C5-R4) at 6, 3 and
12 kb/s, the never-reset continuous rendering at the same rates, and the
outside check's 3 and 12 kb/s level tables. Every figure carries the sha256
of the record it came from.

The nine synthetic items are regenerated here from their pre-registered
constructions and must hash to the programme manifest before they are used,
so this check runs without any programme file. The five recorded excerpts
are read from the directory named by ``KENC_F101_PROGRAMME_DIR``; when it is
unset every control that needs a recording is reported as SKIPPED and
counted, never passed.

Checks, all through ``tools/epoch_stream.py`` on the exported graphs:

- identity: C5-R4 codes and float32 PCM sha256 at 6, 3 and 12 kb/s.
- determinism: for epochs 1..11, a fresh encoder given only that epoch's audio
  and a fresh decoder given only that epoch's codes reproduce the full stream
  bit for bit (D-tx, D-rx), at 6, 3 and 12 kb/s.
- levels: the continuous rendering's identity, and the 3 and 12 kb/s
  boundary level tables (0-50 and 50-100 ms against continuous and source,
  deepest 5 ms, silence dBFS) within 0.01 dB.

No audio or graph is written.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import multiprocessing
import os
import sys
import wave
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Any

REPOSITORY = Path(__file__).resolve().parents[1]
DEFAULT_EXPECTATIONS = REPOSITORY / "tests" / "fixtures" / "f101-c5r4-programme.json"
PROGRAMME_ENVIRONMENT = "KENC_F101_PROGRAMME_DIR"
SCHEMA = "kilix.encodec.epoch-programme-expectations/v1"
SAMPLE_RATE = 24_000
ITEM_SAMPLES = 12 * SAMPLE_RATE
EPOCH_SAMPLES = SAMPLE_RATE
EPOCH_FRAMES = 75
EPOCHS = 12
BOUNDARIES = tuple(k * SAMPLE_RATE for k in range(1, EPOCHS))
WINDOW = 1_200
BLOCK = 120
BLOCKS = 30
BITRATES = ("6", "3", "12")
LEVEL_BITRATES = ("3", "12")
TYPES = ("fixture", "tones", "noise", "silence", "impulses", "clipping", "speech", "music")
WINDOWS = ("0-50", "50-100")
TYPE_FIELDS = ("mean_dC", "worst_dC", "mean_dS", "worst_dS", "mean_dS_R")
CHECKS = ("identity", "determinism", "levels")


# ---------------------------------------------------------------- programme audio

def synthetic_float(name: str) -> Any:
    """Float64 signal of a synthetic item, exactly as pre-registered."""

    import numpy as np

    n = ITEM_SAMPLES
    t = np.arange(n) / SAMPLE_RATE
    if name == "syn-fixture":
        import torch

        timeline = torch.arange(n, dtype=torch.float32) / SAMPLE_RATE
        value = (
            0.35 * torch.sin(2 * math.pi * (120 + 80 * timeline) * timeline)
            + 0.25 * torch.sin(2 * math.pi * 440 * timeline)
            + 0.10 * torch.randn(n, generator=torch.Generator().manual_seed(13))
        )
        return (value / value.abs().max() * 0.9).numpy().astype(np.float64)
    if name == "syn-tone440":
        return 0.25 * np.sin(2 * np.pi * 440 * t)
    if name == "syn-tone100":
        return 0.25 * np.sin(2 * np.pi * 100 * t)
    if name == "syn-sweep":
        f0, f1, duration = 40.0, 11000.0, 12.0
        k = math.log(f1 / f0)
        return 0.25 * np.sin(2 * np.pi * f0 * duration / k * (np.exp(t / duration * k) - 1.0))
    if name == "syn-white":
        w = np.random.default_rng(101).standard_normal(n)
        return w / np.sqrt(np.mean(w ** 2)) * 0.1
    if name == "syn-pink":
        g = np.random.default_rng(102).standard_normal(n)
        spectrum = np.fft.rfft(g)
        frequency = np.fft.rfftfreq(n, 1.0 / SAMPLE_RATE)
        shape = np.zeros_like(frequency)
        shape[1:] = 1.0 / np.sqrt(frequency[1:])
        p = np.fft.irfft(spectrum * shape, n=n)
        return p / np.sqrt(np.mean(p ** 2)) * 0.1
    if name == "syn-silence":
        return np.zeros(n)
    if name == "syn-impulses":
        value = np.zeros(n)
        value[500::1117] = 0.9
        return value
    if name == "syn-clipped":
        return np.clip(np.sin(2 * np.pi * 220 * t) + np.sin(2 * np.pi * 331 * t), -0.95, 0.95)
    raise ValueError(f"no synthetic construction: {name}")


def to_int16(value: Any) -> Any:
    import numpy as np

    return np.clip(
        np.round(np.asarray(value, dtype=np.float64) * 32767.0), -32768, 32767
    ).astype("<i2")


def wav_bytes(pcm: Any) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(SAMPLE_RATE)
        output.writeframes(pcm.tobytes())
    return buffer.getvalue()


def read_wav(raw: bytes) -> Any:
    import numpy as np

    with wave.open(io.BytesIO(raw), "rb") as handle:
        if (handle.getframerate(), handle.getnchannels(), handle.getsampwidth()) != (
            SAMPLE_RATE,
            1,
            2,
        ):
            raise ValueError("programme WAV is not 24 kHz mono 16-bit")
        frames = handle.readframes(handle.getnframes())
    pcm = np.frombuffer(frames, dtype="<i2")
    if pcm.shape != (ITEM_SAMPLES,):
        raise ValueError(f"programme WAV length differs: {pcm.shape}")
    return pcm


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ---------------------------------------------------------------- measurement

def qdomain(value: Any) -> Any:
    """The listening-file domain: float32 clip, x32767, truncate to int16, /32768."""

    import numpy as np

    samples = np.asarray(value, dtype=np.float32).reshape(-1).clip(-1.0, 1.0)
    return (samples * np.float32(32767.0)).astype("<i2").astype(np.float64) / 32768.0


def boundary_levels(candidate: Any, continuous: Any, source: Any, boundary: int) -> dict[str, Any]:
    import numpy as np

    def mean_square(value: Any, offset: int) -> float:
        window = value[boundary + offset : boundary + offset + WINDOW]
        return float(np.mean(window * window))

    def block_levels(value: Any) -> Any:
        blocks = value[boundary : boundary + BLOCKS * BLOCK].reshape(BLOCKS, BLOCK)
        return 20 * np.log10(np.maximum(np.sqrt((blocks * blocks).mean(1)), 1e-5))

    return {
        "ms_Y": [mean_square(candidate, 0), mean_square(candidate, WINDOW)],
        "ms_R": [mean_square(continuous, 0), mean_square(continuous, WINDOW)],
        "ms_S": [mean_square(source, 0), mean_square(source, WINDOW)],
        "d5": float((block_levels(candidate) - block_levels(continuous)).min()),
    }


def render_hashes(codes: Any, pcm: Any) -> dict[str, str]:
    import numpy as np

    return {
        "codes": sha256(np.ascontiguousarray(codes.astype(np.int64)).tobytes()),
        "pcm_float32": sha256(np.ascontiguousarray(pcm, dtype=np.float32).tobytes()),
    }


def measure_item(bundle: str, name: str, pcm_bytes: bytes, checks: tuple[str, ...]) -> dict[str, Any]:
    """Render one item at every bitrate. Runs in a worker process."""

    import numpy as np
    import onnxruntime as ort

    sys.path.insert(0, str(REPOSITORY / "tools"))
    import epoch_stream as stream

    records = json.loads((Path(bundle) / "manifest.json").read_bytes())["graphs"]
    sessions: dict[str, Any] = {}

    def session(key: str) -> Any:
        if key not in sessions:
            options = ort.SessionOptions()
            options.intra_op_num_threads = 1
            options.inter_op_num_threads = 1
            options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
            sessions[key] = ort.InferenceSession(
                str(Path(bundle) / records[key]["file"]), options, providers=["CPUExecutionProvider"]
            )
        return sessions[key]

    pcm = np.frombuffer(pcm_bytes, dtype="<i2")
    audio = (pcm.astype(np.float32) / np.float32(32768.0)).reshape(1, 1, -1)
    source = pcm.astype(np.float64) / 32768.0
    result: dict[str, Any] = {"name": name, "bitrates": {}}
    for kbps in BITRATES:
        encode_key, decode_key = f"rvq_encode_{kbps}kbps", f"rvq_decode_{kbps}kbps"

        def encoder(preroll: int = stream.PREROLL_PACKETS) -> Any:
            return stream.StreamEncoder(
                session("encoder"), records["encoder"], session(encode_key), records[encode_key], preroll
            )

        def decoder(preroll: int = stream.PREROLL_PACKETS) -> Any:
            return stream.StreamDecoder(
                session(decode_key), records[decode_key], session("decoder"), records["decoder"], preroll
            )

        _, codes = stream.encode_stream(encoder(), audio)
        rendered = stream.decode_stream(decoder(), codes)
        row: dict[str, Any] = {"hashes": render_hashes(codes, rendered)}
        if "determinism" in checks:
            tx, rx = [], []
            for epoch in range(1, EPOCHS):
                epoch_audio = np.ascontiguousarray(
                    audio[..., epoch * EPOCH_SAMPLES : (epoch + 1) * EPOCH_SAMPLES]
                )
                epoch_codes = np.ascontiguousarray(
                    codes[..., epoch * EPOCH_FRAMES : (epoch + 1) * EPOCH_FRAMES]
                )
                _, fresh_codes = stream.encode_stream(encoder(), epoch_audio)
                tx.append(bool(np.array_equal(fresh_codes, epoch_codes)))
                fresh_pcm = stream.decode_stream(decoder(), epoch_codes)
                rx.append(
                    bool(
                        np.array_equal(
                            fresh_pcm,
                            rendered[..., epoch * EPOCH_SAMPLES : (epoch + 1) * EPOCH_SAMPLES],
                        )
                    )
                )
            row["D_tx"], row["D_rx"] = tx, rx
        if "levels" in checks and kbps in LEVEL_BITRATES:
            _, continuous_codes = stream.encode_stream(encoder(0), audio, None)
            continuous = stream.decode_stream(decoder(0), continuous_codes, None)
            row["continuous_hashes"] = render_hashes(continuous_codes, continuous)
            candidate_q, continuous_q = qdomain(rendered), qdomain(continuous)
            row["boundaries"] = [
                boundary_levels(candidate_q, continuous_q, source, boundary)
                for boundary in BOUNDARIES
            ]
        result["bitrates"][kbps] = row
    return result


# ---------------------------------------------------------------- level tables

def level(mean_square: float) -> float:
    return 10.0 * math.log10(mean_square + 1e-10)


def item_levels(boundaries: list[dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for index, key in enumerate(WINDOWS):
        ys = [row["ms_Y"][index] for row in boundaries]
        rs = [row["ms_R"][index] for row in boundaries]
        ss = [row["ms_S"][index] for row in boundaries]
        mean_y, mean_r, mean_s = (math.fsum(v) / len(v) for v in (ys, rs, ss))
        out[key] = {
            "pooled_dC": level(mean_y) - level(mean_r),
            "pooled_dS": level(mean_y) - level(mean_s),
            "pooled_dS_R": level(mean_r) - level(mean_s),
            "worst_dC": min(level(y) - level(r) for y, r in zip(ys, rs)),
            "worst_dS": min(level(y) - level(s) for y, s in zip(ys, ss)),
            "abs_Y": level(mean_y),
            "abs_R": level(mean_r),
        }
    out["D5"] = min(row["d5"] for row in boundaries)
    return out


def type_cell(levels: list[dict[str, Any]]) -> dict[str, Any]:
    cell: dict[str, Any] = {}
    for key in WINDOWS:
        cell[key] = {
            "mean_dC": math.fsum(v[key]["pooled_dC"] for v in levels) / len(levels),
            "worst_dC": min(v[key]["worst_dC"] for v in levels),
            "mean_dS": math.fsum(v[key]["pooled_dS"] for v in levels) / len(levels),
            "worst_dS": min(v[key]["worst_dS"] for v in levels),
            "mean_dS_R": math.fsum(v[key]["pooled_dS_R"] for v in levels) / len(levels),
        }
    cell["D5"] = min(v["D5"] for v in levels)
    return cell


# ---------------------------------------------------------------- accounting

class Tally:
    def __init__(self) -> None:
        self.passed = 0
        self.failed: list[str] = []
        self.skipped = 0
        self.skip_reasons: list[str] = []

    def check(self, ok: bool, label: str) -> None:
        if ok:
            self.passed += 1
        else:
            self.failed.append(label)

    def skip(self, count: int, reason: str) -> None:
        self.skipped += count
        self.skip_reasons.append(f"{count} {reason}")

    @property
    def total(self) -> int:
        return self.passed + len(self.failed)


def load_expectations(path: Path) -> dict[str, Any]:
    document = json.loads(path.read_bytes())
    if document.get("schema") != SCHEMA or document.get("arm") != "C5-R4":
        raise ValueError("expectations schema or arm differs")
    names = [item["name"] for item in document["items"]]
    if len(names) != 14 or len(set(names)) != 14:
        raise ValueError("expectations must list 14 distinct programme items")
    if sorted({item["kind"] for item in document["items"]}) != sorted(TYPES):
        raise ValueError("expectations do not cover every programme type")
    return document


def gather_programme(
    expectations: dict[str, Any], directory: str | None, tally: Tally
) -> dict[str, bytes]:
    """Regenerate synthetic items and read recordings; verify every WAV hash."""

    import numpy as np

    available: dict[str, bytes] = {}
    if directory:
        manifest = Path(directory) / "manifest.json"
        tally.check(
            manifest.is_file()
            and not manifest.is_symlink()
            and sha256(manifest.read_bytes()) == expectations["programme"]["manifest_sha256"],
            "programme manifest sha256",
        )
    for item in expectations["items"]:
        name = item["name"]
        if item["synthetic"]:
            raw = wav_bytes(to_int16(synthetic_float(name)))
            origin = "regenerated"
        elif directory:
            path = Path(directory) / f"{name}.wav"
            if path.is_symlink() or not path.is_file():
                tally.check(False, f"{name}: recording missing from {PROGRAMME_ENVIRONMENT}")
                continue
            raw = path.read_bytes()
            origin = "read"
        else:
            continue
        matched = sha256(raw) == item["wav_sha256"]
        tally.check(matched, f"{name}: {origin} WAV sha256 differs from the programme manifest")
        if matched:
            available[name] = np.ascontiguousarray(read_wav(raw)).tobytes()
    return available


def evaluate(
    expectations: dict[str, Any],
    measured: dict[str, dict[str, Any]],
    checks: tuple[str, ...],
    tally: Tally,
) -> dict[str, Any]:
    items = {item["name"]: item for item in expectations["items"]}
    missing = [name for name in items if name not in measured]
    report: dict[str, Any] = {"determinism": {}, "levels": {}}

    if "identity" in checks:
        for name, row in measured.items():
            for kbps in BITRATES:
                for field in ("codes", "pcm_float32"):
                    tally.check(
                        row["bitrates"][kbps]["hashes"][field] == items[name]["renders"][kbps][field],
                        f"{name} {kbps} kb/s C5-R4 {field} identity",
                    )
        if missing:
            tally.skip(len(missing) * len(BITRATES) * 2, f"identity controls for absent items {missing}")

    if "determinism" in checks:
        for kbps in BITRATES:
            tx = sum(sum(row["bitrates"][kbps]["D_tx"]) for row in measured.values())
            rx = sum(sum(row["bitrates"][kbps]["D_rx"]) for row in measured.values())
            total = len(measured) * (EPOCHS - 1)
            for name, row in measured.items():
                for epoch, (a, b) in enumerate(zip(row["bitrates"][kbps]["D_tx"], row["bitrates"][kbps]["D_rx"]), start=1):
                    tally.check(a, f"{name} {kbps} kb/s D-tx epoch {epoch}")
                    tally.check(b, f"{name} {kbps} kb/s D-rx epoch {epoch}")
            report["determinism"][kbps] = {"D_tx": tx, "D_rx": rx, "total": total}
            if not missing:
                tally.check(
                    {"D_rx": rx, "D_tx": tx, "total": total} == expectations["determinism_totals"][kbps],
                    f"{kbps} kb/s determinism totals equal the checked 154/154",
                )
        if missing:
            tally.skip(len(missing) * len(BITRATES) * 2 * (EPOCHS - 1) + len(BITRATES), f"determinism controls for absent items {missing}")

    if "levels" in checks:
        tolerance = expectations["level_tolerance_db"]
        for kbps in LEVEL_BITRATES:
            for name, row in measured.items():
                for field in ("codes", "pcm_float32"):
                    tally.check(
                        row["bitrates"][kbps]["continuous_hashes"][field] == items[name]["continuous"][kbps][field],
                        f"{name} {kbps} kb/s continuous {field} identity",
                    )
            per_item = {name: item_levels(row["bitrates"][kbps]["boundaries"]) for name, row in measured.items()}
            expected = expectations["levels"][kbps]
            table: dict[str, Any] = {}
            for kind in TYPES:
                names = [name for name, item in items.items() if item["kind"] == kind]
                if any(name not in per_item for name in names):
                    tally.skip(2 * len(TYPE_FIELDS) + 1, f"{kbps} kb/s {kind} level cells (absent items)")
                    continue
                cell = type_cell([per_item[name] for name in names])
                table[kind] = cell
                for key in WINDOWS:
                    for field in TYPE_FIELDS:
                        delta = cell[key][field] - expected["types"][kind][key][field]
                        tally.check(abs(delta) <= tolerance, f"{kbps} kb/s {kind} {key} {field} delta {delta:+.4f} dB")
                delta = cell["D5"] - expected["types"][kind]["D5"]
                tally.check(abs(delta) <= tolerance, f"{kbps} kb/s {kind} D5 delta {delta:+.4f} dB")
            if "syn-silence" in per_item:
                silence = per_item["syn-silence"]
                for key in WINDOWS:
                    for index, field in enumerate(("abs_Y", "abs_R")):
                        delta = silence[key][field] - expected["silence_abs"][key][index]
                        tally.check(abs(delta) <= tolerance, f"{kbps} kb/s silence {key} {field} delta {delta:+.4f} dB")
            report["levels"][kbps] = table
        if missing:
            tally.skip(len(missing) * len(LEVEL_BITRATES) * 2, f"continuous identity controls for absent items {missing}")
    return report


def verify(bundle: Path, expectations_path: Path, jobs: int, checks: tuple[str, ...]) -> int:
    expectations = load_expectations(expectations_path)
    directory = os.environ.get(PROGRAMME_ENVIRONMENT) or None
    tally = Tally()
    programme = gather_programme(expectations, directory, tally)
    recordings = [item["name"] for item in expectations["items"] if not item["synthetic"]]
    if directory is None:
        print(
            f"SKIPPED: {PROGRAMME_ENVIRONMENT} is unset; {len(recordings)}/14 recorded programme items "
            f"are not measured: {', '.join(recordings)}",
            file=sys.stderr,
            flush=True,
        )
    print(
        f"epoch programme inputs: {len(programme)}/14 items verified against the programme manifest "
        f"({sum(item['synthetic'] for item in expectations['items'])} regenerated synthetic, "
        f"{len(programme) - sum(1 for item in expectations['items'] if item['synthetic'] and item['name'] in programme)} recorded)",
        flush=True,
    )
    measured: dict[str, dict[str, Any]] = {}
    context = multiprocessing.get_context("spawn")
    with ProcessPoolExecutor(max_workers=max(1, jobs), mp_context=context) as pool:
        futures = {
            name: pool.submit(measure_item, str(bundle), name, pcm, checks)
            for name, pcm in programme.items()
        }
        for name, future in futures.items():
            measured[name] = future.result()
            print(f"  measured {name}", flush=True)
    # Preserve the programme order for reporting.
    measured = {item["name"]: measured[item["name"]] for item in expectations["items"] if item["name"] in measured}
    report = evaluate(expectations, measured, checks, tally)
    for kbps, row in report["determinism"].items():
        print(f"  {kbps} kb/s D-rx {row['D_rx']}/{row['total']} D-tx {row['D_tx']}/{row['total']}")
    for label in tally.failed[:40]:
        print(f"  FAILED: {label}")
    for reason in tally.skip_reasons:
        print(f"  SKIPPED: {reason}")
    status = "PASS" if not tally.failed else "FAIL"
    print(
        f"epoch programme controls ({','.join(checks)}): {tally.passed}/{tally.total} {status}; "
        f"SKIPPED {tally.skipped}"
    )
    if tally.skipped:
        print(
            f"SKIPPED {tally.skipped} epoch programme controls: set {PROGRAMME_ENVIRONMENT} to the programme directory",
            file=sys.stderr,
        )
    return 0 if not tally.failed else 1


def self_test(expectations_path: Path) -> int:
    """Model-free controls: synthetic regeneration, level metric plants, skip accounting."""

    import numpy as np

    expectations = load_expectations(expectations_path)
    passed = total = 0

    def check(ok: bool, reason: str) -> None:
        nonlocal passed, total
        total += 1
        passed += int(ok)
        if not ok:
            print(f"  self-test failed: {reason}")

    synthetic = [item for item in expectations["items"] if item["synthetic"]]
    regenerated = sum(
        sha256(wav_bytes(to_int16(synthetic_float(item["name"])))) == item["wav_sha256"]
        for item in synthetic
    )
    check(regenerated == len(synthetic) == 9, f"synthetic regeneration {regenerated}/{len(synthetic)}")

    rng = np.random.default_rng(3)
    reference = rng.standard_normal(ITEM_SAMPLES) * 0.1
    boundaries = [boundary_levels(reference, reference, reference, b) for b in BOUNDARIES]
    identity = item_levels(boundaries)
    check(identity["0-50"]["pooled_dC"] == 0.0 and identity["D5"] == 0.0, "identity reads 0 dB")
    planted = reference.copy()
    for boundary in BOUNDARIES:
        planted[boundary : boundary + WINDOW] *= 0.5
    dip = item_levels([boundary_levels(planted, reference, reference, b) for b in BOUNDARIES])
    check(abs(dip["0-50"]["pooled_dC"] + 6.0206) < 0.01, f"x0.5 plant reads -6.02 dB ({dip['0-50']['pooled_dC']:.4f})")
    check(abs(dip["50-100"]["pooled_dC"]) < 0.01, "x0.5 plant leaves 50-100 ms at 0 dB")
    check(abs(dip["D5"] + 6.0206) < 0.01, "x0.5 plant reads -6.02 dB in the deepest 5 ms block")

    tally = Tally()
    evaluate(expectations, {}, CHECKS, tally)
    check(tally.passed == 0 and not tally.failed and tally.skipped > 0, "absent items are counted as skipped, never passed")
    print(f"epoch programme self-test: {passed}/{total} {'PASS' if passed == total else 'FAIL'}")
    return 0 if passed == total else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--bundle", type=Path)
    modes.add_argument("--self-test", action="store_true")
    parser.add_argument("--expectations", type=Path, default=DEFAULT_EXPECTATIONS)
    parser.add_argument("--jobs", type=int, default=4)
    parser.add_argument("--checks", default=",".join(CHECKS))
    args = parser.parse_args()
    checks = tuple(part for part in args.checks.split(",") if part)
    if not checks or any(part not in CHECKS for part in checks):
        parser.error(f"--checks must be a subset of {','.join(CHECKS)}")
    try:
        if args.self_test:
            return self_test(args.expectations)
        if args.bundle.is_symlink() or not (args.bundle / "manifest.json").is_file():
            parser.error("--bundle must be an exported 24 kHz bundle directory")
        return verify(args.bundle, args.expectations, args.jobs, checks)
    except (OSError, ValueError, KeyError) as error:
        parser.exit(1, f"epoch programme verification refused: {error}\n")


if __name__ == "__main__":
    raise SystemExit(main())
