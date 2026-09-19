#!/usr/bin/env python3
"""Regenerate tests/data/catalog_source.json and tests/data/catalog_digests.txt.

The digest list names every model input and converted payload this
repository knows by sha256: the upstream checkpoints and converter outputs in
tools/converter-inputs.json and the installed graph populations in
python/graph_population.py. CHG-kilix-encodec folds in the accepted catalog
with --catalog; its EnCodec members are kept in catalog_source.json so later
runs reproduce them. Neither output file is ever hand-edited.

  regenerate_catalog_digests.py                  rewrite both files
  regenerate_catalog_digests.py --catalog FILE   also replace the catalog members
  regenerate_catalog_digests.py --stdout         print the digest list
  regenerate_catalog_digests.py --check          exit 1 unless both files match
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
BINDING = ROOT / "tools" / "converter-inputs.json"
POPULATION = ROOT / "python" / "graph_population.py"
SOURCE = ROOT / "tests" / "data" / "catalog_source.json"
OUTPUT = ROOT / "tests" / "data" / "catalog_digests.txt"
SCHEMA = "kilix.encodec.catalog-digest-source/v1"
_HEX = frozenset("0123456789abcdef")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _digest(value: object, label: str) -> str:
    if not isinstance(value, str) or len(value) != 64 or set(value) - _HEX:
        raise SystemExit(f"{label} is not 64 lowercase hex")
    return value


def _is_notice_or_licence_path(path: str) -> bool:
    """Notices and licence texts are not model weights (as in kilix-content)."""
    normalized = path.replace("\\", "/").lower()
    name = normalized.rsplit("/", 1)[-1]
    if normalized.startswith("notices/") or "/notices/" in f"/{normalized}":
        return True
    return name.startswith(("license", "licence", "copying", "notice"))


def repository_members(root: Path = ROOT) -> list[str]:
    binding = json.loads((root / "tools" / "converter-inputs.json").read_text(encoding="utf-8"))
    digests: list[str] = []
    for name, profile in sorted(binding["profiles"].items()):
        for kind in ("inputs", "outputs"):
            for file, pinned in sorted(profile[kind].items()):
                if not _is_notice_or_licence_path(file):
                    digests.append(_digest(pinned["sha256"], f"{name}.{kind}.{file}"))
    tree = ast.parse((root / "python" / "graph_population.py").read_text(encoding="utf-8"))
    profiles = None
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "PROFILES" for target in node.targets
        ):
            profiles = ast.literal_eval(node.value)
    if not isinstance(profiles, dict) or not profiles:
        raise SystemExit("graph_population.py has no PROFILES literal")
    for number, (_asset, _version, _limit, files) in sorted(profiles.items()):
        for file, _size, digest in files:
            if not _is_notice_or_licence_path(file):
                digests.append(_digest(digest, f"population {number}.{file}"))
    return sorted(set(digests))


def catalog_members(catalog: dict) -> list[str]:
    """EnCodec asset digests from a kilix-content catalog (v1 or v3 records)."""
    digests: list[str] = []
    for asset in catalog.get("assets", []):
        if asset.get("provider") != "kilix-encodec" and not str(asset.get("id", "")).startswith("encodec-"):
            continue
        source = asset.get("source") or {}
        for key in ("archive_sha256", "input_sha256"):
            if source.get(key):
                digests.append(_digest(source[key], f"{asset.get('id')}.source.{key}"))
        convert_input = source.get("input") or {}
        if isinstance(convert_input, dict) and convert_input.get("sha256"):
            digests.append(_digest(convert_input["sha256"], f"{asset.get('id')}.source.input"))
        for item in asset.get("files") or []:
            path = item.get("path") or ""
            if item.get("sha256") and not _is_notice_or_licence_path(path):
                digests.append(_digest(item["sha256"], f"{asset.get('id')}.files.{path}"))
    return sorted(set(digests))


def committed_catalog(path: Path = SOURCE) -> dict:
    if not path.exists():
        return {"members": [], "source": None}
    parsed = json.loads(path.read_text(encoding="utf-8"))
    if parsed.get("schema") != SCHEMA:
        raise SystemExit(f"catalog source schema must be {SCHEMA}")
    catalog = parsed.get("catalog")
    if not isinstance(catalog, dict) or not isinstance(catalog.get("members"), list):
        raise SystemExit("catalog source has no catalog members")
    return {"members": [_digest(item, "catalog member") for item in catalog["members"]],
            "source": catalog.get("source")}


def render(catalog: dict, root: Path = ROOT) -> tuple[bytes, str]:
    members = sorted(set(repository_members(root)) | set(catalog["members"]))
    source = {
        "catalog": {"members": sorted(set(catalog["members"])), "source": catalog["source"]},
        "members": members,
        "schema": SCHEMA,
    }
    source_bytes = (json.dumps(source, indent=2, sort_keys=True) + "\n").encode("utf-8")
    lines = [
        "# kilix-encodec catalog digest list",
        "# Generated by tools/regenerate_catalog_digests.py. Do not hand-edit.",
        "# source: tests/data/catalog_source.json",
        f"# source_sha256: {sha256_hex(source_bytes)}",
        f"# count: {len(members)}",
    ]
    lines.extend(members)
    lines.append("")
    return source_bytes, "\n".join(lines)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--stdout", action="store_true")
    mode.add_argument("--check", action="store_true")
    parser.add_argument("--catalog", type=Path)
    args = parser.parse_args(argv[1:])
    catalog = committed_catalog()
    if args.catalog is not None:
        if args.check or args.stdout:
            parser.error("--catalog rewrites the committed source")
        data = args.catalog.read_bytes()
        catalog = {"members": catalog_members(json.loads(data.decode("utf-8"))),
                   "source": f"{args.catalog.name} sha256 {sha256_hex(data)}"}
    source_bytes, text = render(catalog)
    if args.stdout:
        sys.stdout.write(text)
        return 0
    if args.check:
        matches = (SOURCE.exists() and SOURCE.read_bytes() == source_bytes
                   and OUTPUT.exists() and OUTPUT.read_text(encoding="utf-8") == text)
        print("catalog digest list: " + ("matches the generator" if matches else "DIFFERS from the generator"))
        return 0 if matches else 1
    SOURCE.parent.mkdir(parents=True, exist_ok=True)
    SOURCE.write_bytes(source_bytes)
    OUTPUT.write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
