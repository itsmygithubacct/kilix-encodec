#!/usr/bin/env python3
"""Prepare and score a blinded 24 kHz epoch-boundary listening trial."""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import secrets
import tempfile
import wave
from pathlib import Path
from typing import Any

from export_24khz import REPOSITORY, canonical_json, outside_repository, sha256


TOOL_VERSION = "0.1.4"
PUBLIC_SCHEMA = "kilix.encodec.epoch-listening-public/v1"
ANSWER_SCHEMA = "kilix.encodec.epoch-listening-answer-key/v1"
RESPONSE_SCHEMA = "kilix.encodec.epoch-listening-response/v1"
RESULT_SCHEMA = "kilix.encodec.epoch-listening-result/v1"
LISTENER_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
ALPHA = 0.05


def write_canonical_exclusive(path: Path, value: dict[str, Any]) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as output:
        output.write(canonical_json(value))


def load_canonical(path: Path, label: str) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"{label} must be a non-symlink regular file")
    raw = path.read_bytes()
    value = json.loads(raw)
    if not isinstance(value, dict) or raw != canonical_json(value):
        raise ValueError(f"{label} must be canonical JSON")
    return value


def fixture(path: Path, label: str) -> dict[str, Any]:
    resolved = outside_repository(path, label)
    if resolved.is_symlink() or not resolved.is_file():
        raise ValueError(f"{label} must be a non-symlink regular file")
    with wave.open(str(resolved), "rb") as source:
        contract = {
            "channels": source.getnchannels(),
            "frames": source.getnframes(),
            "sample_rate": source.getframerate(),
            "sample_width_bytes": source.getsampwidth(),
        }
        source.readframes(source.getnframes())
    expected = {
        "channels": 1,
        "frames": contract["frames"],
        "sample_rate": 24_000,
        "sample_width_bytes": 2,
    }
    if contract != expected or contract["frames"] == 0:
        raise ValueError(f"{label} must be nonempty 24 kHz mono signed-16 WAV")
    return {
        "contract": contract,
        "path": resolved,
        "sha256": sha256(resolved),
    }


def copy_exclusive(source: Path, target: Path) -> None:
    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with source.open("rb") as input_file, os.fdopen(descriptor, "wb") as output:
        for chunk in iter(lambda: input_file.read(1024 * 1024), b""):
            output.write(chunk)


def prepare_directory(path: Path, label: str) -> Path:
    resolved = outside_repository(path, label)
    if resolved.exists():
        if resolved.is_symlink() or not resolved.is_dir():
            raise ValueError(f"{label} must be a non-symlink directory")
        members = list(resolved.iterdir())
        if members:
            raise ValueError(f"{label} must be empty: {len(members)}/0 entries")
    else:
        resolved.mkdir(mode=0o700, parents=False)
    return resolved


def prepare_trial(
    fixtures: Path,
    public_directory: Path,
    answer_key_path: Path,
    trials: int,
) -> dict[str, Any]:
    if trials < 10 or trials > 100:
        raise ValueError("trial count must be between 10 and 100")
    fixture_directory = outside_repository(fixtures, "fixture directory")
    if fixture_directory.is_symlink() or not fixture_directory.is_dir():
        raise ValueError("fixture directory must be a non-symlink directory")
    continuous = fixture(fixture_directory / "continuous.wav", "continuous fixture")
    reset = fixture(fixture_directory / "epoch-reset.wav", "epoch-reset fixture")
    if continuous["contract"] != reset["contract"]:
        raise ValueError("listening fixture WAV contracts differ")
    if continuous["sha256"] == reset["sha256"]:
        raise ValueError("listening fixtures are byte-identical")

    public = prepare_directory(public_directory, "public trial directory")
    answer_key = outside_repository(answer_key_path, "answer key")
    if answer_key.exists() or answer_key.parent.is_symlink() or not answer_key.parent.is_dir():
        raise ValueError("answer key target must be absent below an existing directory")
    if answer_key == public or answer_key.is_relative_to(public):
        raise ValueError("answer key must remain outside the public trial directory")

    assignments = ["A"] * (trials // 2) + ["B"] * (trials - trials // 2)
    secrets.SystemRandom().shuffle(assignments)
    session = secrets.token_hex(16)
    public_rows = []
    private_rows = []
    for number, reset_side in enumerate(assignments, start=1):
        a_name = f"trial-{number:03d}-A.wav"
        b_name = f"trial-{number:03d}-B.wav"
        a_source = reset["path"] if reset_side == "A" else continuous["path"]
        b_source = continuous["path"] if reset_side == "A" else reset["path"]
        a_path = public / a_name
        b_path = public / b_name
        copy_exclusive(a_source, a_path)
        copy_exclusive(b_source, b_path)
        public_rows.append(
            {"a_file": a_name, "b_file": b_name, "trial": number}
        )
        private_rows.append(
            {
                "a_file": a_name,
                "a_sha256": sha256(a_path),
                "b_file": b_name,
                "b_sha256": sha256(b_path),
                "reset_side": reset_side,
                "trial": number,
            }
        )

    response_template = {
        "choices": [{"choice": "", "trial": row["trial"]} for row in public_rows],
        "listener": "replace-with-pseudonym",
        "schema": RESPONSE_SCHEMA,
        "session": session,
    }
    template_path = public / "response-template.json"
    write_canonical_exclusive(template_path, response_template)
    public_manifest = {
        "condition": "one continuous and one one-second-epoch-reset rendering per pair",
        "instructions": [
            "Use headphones at one fixed comfortable level.",
            "For every pair, choose the member with the more audible boundary near one second.",
            "Do not inspect waveforms, hashes, source fixtures, or the private answer key.",
            "Complete every trial and make no tie selection.",
        ],
        "response_schema": RESPONSE_SCHEMA,
        "schema": PUBLIC_SCHEMA,
        "session": session,
        "source": {
            "listening_trial.py": sha256(Path(__file__).resolve()),
            "tool_version": TOOL_VERSION,
        },
        "template_file": template_path.name,
        "trials": public_rows,
        "wav_contract": continuous["contract"],
    }
    manifest_path = public / "manifest.json"
    write_canonical_exclusive(manifest_path, public_manifest)
    answer = {
        "fixtures": {
            "continuous": continuous["sha256"],
            "epoch_reset": reset["sha256"],
        },
        "public_manifest_sha256": sha256(manifest_path),
        "records": private_rows,
        "schema": ANSWER_SCHEMA,
        "session": session,
        "template_sha256": sha256(template_path),
    }
    write_canonical_exclusive(answer_key, answer)
    print(
        f"blind listening trial prepared: pairs={trials}/{trials} "
        f"concealed_mappings={len(private_rows)}/{trials} manifest=1/1"
    )
    print("blind listening verdict credit: 0/1; listener and owner action required")
    return answer


def binomial_tail(correct: int, trials: int) -> float:
    numerator = sum(math.comb(trials, value) for value in range(correct, trials + 1))
    return numerator / (2**trials)


def response_choices(
    path: Path, session: str, trials: int
) -> tuple[str, dict[int, str], str]:
    response_path = outside_repository(path, "response")
    if response_path.is_symlink() or not response_path.is_file():
        raise ValueError("response must be a non-symlink regular file")
    value = json.loads(response_path.read_bytes())
    if not isinstance(value, dict) or value.get("schema") != RESPONSE_SCHEMA:
        raise ValueError("response schema differs")
    if value.get("session") != session:
        raise ValueError("response session differs")
    listener = value.get("listener")
    if not isinstance(listener, str) or not LISTENER_RE.fullmatch(listener):
        raise ValueError("response listener pseudonym is invalid")
    rows = value.get("choices")
    if not isinstance(rows, list) or len(rows) != trials:
        raise ValueError(f"response choice count differs: {len(rows) if isinstance(rows, list) else 0}/{trials}")
    choices: dict[int, str] = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("response choice row is invalid")
        number = row.get("trial")
        choice = row.get("choice")
        if (
            not isinstance(number, int)
            or number < 1
            or number > trials
            or number in choices
            or choice not in {"A", "B"}
        ):
            raise ValueError("response choice population differs")
        choices[number] = choice
    if set(choices) != set(range(1, trials + 1)):
        raise ValueError("response trial population differs")
    return listener, choices, sha256(response_path)


def score_trial(
    public_directory: Path,
    answer_key_path: Path,
    response_paths: list[Path],
    result_path: Path,
) -> dict[str, Any]:
    public = outside_repository(public_directory, "public trial directory")
    if public.is_symlink() or not public.is_dir():
        raise ValueError("public trial directory must be a non-symlink directory")
    manifest_path = public / "manifest.json"
    manifest = load_canonical(manifest_path, "public manifest")
    answer_key = outside_repository(answer_key_path, "answer key")
    if answer_key == public or answer_key.is_relative_to(public):
        raise ValueError("answer key must remain outside the public trial directory")
    answer = load_canonical(answer_key, "answer key")
    if manifest.get("schema") != PUBLIC_SCHEMA or answer.get("schema") != ANSWER_SCHEMA:
        raise ValueError("listening trial schema differs")
    if manifest.get("session") != answer.get("session"):
        raise ValueError("public/private listening session differs")
    if answer.get("public_manifest_sha256") != sha256(manifest_path):
        raise ValueError("public listening manifest identity differs")
    expected_source = {
        "listening_trial.py": sha256(Path(__file__).resolve()),
        "tool_version": TOOL_VERSION,
    }
    if manifest.get("source") != expected_source:
        raise ValueError("listening trial source identity differs")
    template_file = manifest.get("template_file")
    if not isinstance(template_file, str) or Path(template_file).name != template_file:
        raise ValueError("response template filename is unsafe")
    template_path = public / template_file
    if template_path.is_symlink() or not template_path.is_file():
        raise ValueError("response template must be a non-symlink regular file")
    if answer.get("template_sha256") != sha256(template_path):
        raise ValueError("response template identity differs")

    public_rows = manifest.get("trials")
    private_rows = answer.get("records")
    if not isinstance(public_rows, list) or not isinstance(private_rows, list):
        raise ValueError("listening trial rows are absent")
    if len(public_rows) != len(private_rows) or len(public_rows) < 10:
        raise ValueError("public/private listening trial counts differ")
    if any(not isinstance(row, dict) for row in (*public_rows, *private_rows)):
        raise ValueError("listening trial row is invalid")
    answer_by_trial = {row.get("trial"): row for row in private_rows}
    if len(answer_by_trial) != len(private_rows):
        raise ValueError("private listening trial population differs")
    expected_trials = set(range(1, len(public_rows) + 1))
    if (
        {row.get("trial") for row in public_rows} != expected_trials
        or set(answer_by_trial) != expected_trials
    ):
        raise ValueError("public/private listening trial population differs")
    audio_files: set[str] = set()
    for public_row in public_rows:
        number = public_row.get("trial")
        private_row = answer_by_trial.get(number)
        if not isinstance(private_row, dict):
            raise ValueError("private listening trial row is absent")
        for side in ("a", "b"):
            filename = public_row.get(f"{side}_file")
            if (
                not isinstance(filename, str)
                or Path(filename).name != filename
                or filename != private_row.get(f"{side}_file")
            ):
                raise ValueError("public/private listening filename differs")
            if filename in audio_files:
                raise ValueError("listening audio filename is reused")
            audio_files.add(filename)
            audio_path = public / filename
            if audio_path.is_symlink() or not audio_path.is_file():
                raise ValueError("listening audio must be a non-symlink regular file")
            if sha256(audio_path) != private_row.get(f"{side}_sha256"):
                raise ValueError("listening audio identity differs")
        if private_row.get("reset_side") not in {"A", "B"}:
            raise ValueError("private listening answer differs")

    if not response_paths:
        raise ValueError("at least one listener response is required")
    listener_rows = []
    listeners: set[str] = set()
    aggregate_correct = 0
    trial_count = len(public_rows)
    for response_path in response_paths:
        listener, choices, response_sha256 = response_choices(
            response_path, answer["session"], trial_count
        )
        if listener in listeners:
            raise ValueError("listener pseudonyms must be unique")
        listeners.add(listener)
        correct = sum(
            choices[number] == answer_by_trial[number]["reset_side"]
            for number in range(1, trial_count + 1)
        )
        probability = binomial_tail(correct, trial_count)
        listener_rows.append(
            {
                "accuracy": correct / trial_count,
                "correct": correct,
                "identifiable_at_alpha_0_05": (
                    correct > trial_count / 2 and probability < ALPHA
                ),
                "listener": listener,
                "one_sided_binomial_p": probability,
                "response_sha256": response_sha256,
                "trials": trial_count,
            }
        )
        aggregate_correct += correct

    aggregate_trials = trial_count * len(listener_rows)
    aggregate_probability = binomial_tail(aggregate_correct, aggregate_trials)
    result = {
        "acceptance": {
            "blind_listening_credit": False,
            "decision_owner": "release owner",
            "label": "measured-only",
        },
        "aggregate": {
            "accuracy": aggregate_correct / aggregate_trials,
            "correct": aggregate_correct,
            "identifiable_at_alpha_0_05": (
                aggregate_correct > aggregate_trials / 2
                and aggregate_probability < ALPHA
            ),
            "listeners": len(listener_rows),
            "one_sided_binomial_p": aggregate_probability,
            "trials": aggregate_trials,
        },
        "answer_key_sha256": sha256(answer_key),
        "listeners": listener_rows,
        "public_manifest_sha256": sha256(manifest_path),
        "schema": RESULT_SCHEMA,
        "session": answer["session"],
    }
    result_target = outside_repository(result_path, "listening result")
    if result_target.exists() or result_target.parent.is_symlink() or not result_target.parent.is_dir():
        raise ValueError("listening result target must be absent below an existing directory")
    if result_target == public or result_target.is_relative_to(public):
        raise ValueError("listening result must remain outside the public trial directory")
    write_canonical_exclusive(result_target, result)
    print(
        f"blind listening responses scored: listeners={len(listener_rows)}/"
        f"{len(response_paths)} choices={aggregate_trials}/{aggregate_trials}"
    )
    print("blind listening verdict credit: 0/1; release-owner decision required")
    return result


def write_test_wav(path: Path, sample: int) -> None:
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(24_000)
        output.writeframes(sample.to_bytes(2, "little", signed=True) * 48_000)


def policy_self_test() -> None:
    scratch = os.environ.get("TMPDIR", "/home/pleb/scratch-workers")
    passed = 0
    with tempfile.TemporaryDirectory(
        prefix="kilix-encodec-listening-policy-", dir=scratch
    ) as temporary:
        root = Path(temporary)
        fixtures = root / "fixtures"
        fixtures.mkdir()
        write_test_wav(fixtures / "continuous.wav", 101)
        write_test_wav(fixtures / "epoch-reset.wav", 202)
        public = root / "public"
        key = root / "answer.json"
        answer = prepare_trial(fixtures, public, key, 10)
        passed += len(answer["records"]) == 10
        passed += len(list(public.iterdir())) == 22
        passed += key.stat().st_mode & 0o777 == 0o600
        reset_a = sum(row["reset_side"] == "A" for row in answer["records"])
        passed += reset_a == 5

        response = {
            "choices": [
                {"choice": row["reset_side"], "trial": row["trial"]}
                for row in answer["records"]
            ],
            "listener": "self-test-listener",
            "schema": RESPONSE_SCHEMA,
            "session": answer["session"],
        }
        response_path = root / "response.json"
        write_canonical_exclusive(response_path, response)
        result_path = root / "result.json"
        result = score_trial(public, key, [response_path], result_path)
        passed += result["aggregate"]["correct"] == 10
        passed += result_path.read_bytes() == canonical_json(result)

        with (public / "trial-001-A.wav").open("ab") as output:
            output.write(b"tamper")
        try:
            score_trial(public, key, [response_path], root / "tampered-result.json")
        except ValueError:
            passed += 1

        symlink_fixtures = root / "symlink-fixtures"
        symlink_fixtures.mkdir()
        (symlink_fixtures / "continuous.wav").symlink_to(
            fixtures / "continuous.wav"
        )
        write_test_wav(symlink_fixtures / "epoch-reset.wav", 303)
        try:
            prepare_trial(
                symlink_fixtures,
                root / "symlink-public",
                root / "symlink-answer.json",
                10,
            )
        except ValueError:
            passed += 1
    if passed != 8:
        raise AssertionError(f"listening trial policy self-test failed: {passed}/8")
    print(f"listening trial policy self-test: {passed}/8 PASS")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    commands = parser.add_subparsers(dest="command")
    prepare = commands.add_parser("prepare")
    prepare.add_argument("--fixtures", type=Path, required=True)
    prepare.add_argument("--public-dir", type=Path, required=True)
    prepare.add_argument("--answer-key", type=Path, required=True)
    prepare.add_argument("--trials", type=int, default=20)
    score = commands.add_parser("score")
    score.add_argument("--public-dir", type=Path, required=True)
    score.add_argument("--answer-key", type=Path, required=True)
    score.add_argument("--response", type=Path, action="append", required=True)
    score.add_argument("--result", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.version:
            print(f"kilix-encodec listening trial tool {TOOL_VERSION}")
        elif args.self_test:
            policy_self_test()
        elif args.command == "prepare":
            prepare_trial(args.fixtures, args.public_dir, args.answer_key, args.trials)
        elif args.command == "score":
            score_trial(args.public_dir, args.answer_key, args.response, args.result)
        else:
            parser.error("a command, --version, or --self-test is required")
    except (AssertionError, OSError, ValueError) as error:
        parser.exit(1, f"listening trial refused: {error}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
