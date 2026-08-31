#!/usr/bin/env python3
"""Fail-closed manifest checks for the P1 repository skeleton."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def verify_skeleton(path: Path) -> None:
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
    print(f"manifest skeleton checks: {passed}/{total} "
          f"{'PASS' if passed == total else 'FAIL'}")
    if passed != total:
        raise SystemExit(1)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skeleton", type=Path, required=True)
    args = parser.parse_args()
    verify_skeleton(args.skeleton)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

