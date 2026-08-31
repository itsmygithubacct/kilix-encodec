#!/usr/bin/env python3
"""Reserved entry point for the later stateful 24 kHz export phase."""

from __future__ import annotations

import argparse


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", action="store_true")
    args = parser.parse_args()
    if args.version:
        print("kilix-encodec export tool 0.1.0 (skeleton)")
        return 0
    parser.error("stateful export is unavailable until the P3 graph gate")


if __name__ == "__main__":
    raise SystemExit(main())

