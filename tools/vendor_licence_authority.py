#!/usr/bin/env python3
"""Re-vendor kilix-license and regenerate the licence-authority pin; never by hand.

`third_party/kilix-license/` is README, CHANGELOG, LICENSE, VERSION,
pyproject.toml, NOTICE and src of one kilix-license commit, read as raw Git
objects whose identities are recomputed (so archive attributes and replace refs
cannot omit or substitute a member). `third_party/kilix-license.pin` names that
commit. `tools/converter-inputs.json` `licence_authority` binds the same commit
and the sha256 of every file the converters and the admission bundle embed: the
pin, the licence, every top-level module and each profile's licence record.

    vendor_licence_authority.py                          offline: pins == vendored bytes
    vendor_licence_authority.py --repo R --ref C         also: vendored tree == commit C
    vendor_licence_authority.py --repo R --ref C --old O --write
        re-pin from O to C; refuses unless the pin reads O and the vendored tree
        is exactly commit O, so a wrong --old can never replace anything.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import build_content_bundle as objects  # noqa: E402

VENDORED = ROOT / "third_party" / "kilix-license"
PIN = ROOT / "third_party" / "kilix-license.pin"
BINDING = ROOT / "tools" / "converter-inputs.json"
INCLUDED = ("CHANGELOG.md", "LICENSE", "NOTICE", "README.md", "VERSION", "pyproject.toml", "src")
PREFIX = "third_party/kilix-license/"
MODULES = PREFIX + "src/kilix_license/"


def commit_files(repo: Path, commit: str) -> dict[str, tuple[str, bytes]]:
    """Relative path -> (mode, bytes) for the vendored subset of one commit."""
    raw = objects.git_object(repo, "commit", commit, 64 * 1024)
    first = raw.split(b"\n", 1)[0]
    if not re.fullmatch(b"tree [0-9a-f]{40}", first):
        raise ValueError("licence authority commit has no canonical tree")
    result: dict[str, tuple[str, bytes]] = {}

    def visit(oid: str, prefix: str, depth: int) -> None:
        if depth > 16:
            raise ValueError("licence authority nesting exceeds bound")
        for name, (mode, child) in objects.git_tree(repo, oid).items():
            path = prefix + name
            if not prefix and name not in INCLUDED:
                continue
            if mode == "40000":
                visit(child, path + "/", depth + 1)
            elif mode in ("100644", "100755"):
                result[path] = (mode, objects.git_object(repo, "blob", child, 4 * 1024**2))
                if len(result) > 1024:
                    raise ValueError("licence authority population exceeds bound")
            else:
                raise ValueError("licence authority has an unsupported entry: " + path)

    visit(first[5:].decode(), "", 0)
    if "LICENSE" not in result or not any(path.startswith("src/kilix_license/") for path in result):
        raise ValueError("licence authority commit is incomplete")
    return result


def vendored_files() -> dict[str, tuple[str, bytes]]:
    result = {}
    for path in sorted(VENDORED.rglob("*")):
        if path.is_symlink() or not (path.is_file() or path.is_dir()):
            raise ValueError("vendored licence authority has an unsupported entry: " + str(path))
        if path.is_file():
            mode = "100755" if path.stat().st_mode & 0o111 else "100644"
            result[str(path.relative_to(VENDORED))] = (mode, path.read_bytes())
    return result


def regenerate(binding: dict, ref: str) -> dict:
    """licence_authority for `ref`, from the vendored bytes on disk."""
    profiles = binding.get("profiles", {})
    records = sorted({profile["record"]["path"] for profile in profiles.values()})
    modules = sorted(PREFIX + str(path.relative_to(VENDORED))
                     for path in (VENDORED / "src" / "kilix_license").glob("*.py"))
    if not records or not modules or not all(path.startswith(MODULES + "data/records/") for path in records):
        raise ValueError("licence authority binding has no records or modules")
    paths = ["third_party/kilix-license.pin", PREFIX + "LICENSE", *modules, *records]
    files = {path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest() for path in paths}
    return {"files": dict(sorted(files.items())), "ref": ref}


def render(binding: dict) -> bytes:
    return (json.dumps(binding, indent=2, sort_keys=True) + "\n").encode()


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--repo", type=Path)
    parser.add_argument("--ref")
    parser.add_argument("--old")
    parser.add_argument("--write", action="store_true")
    options = parser.parse_args(argv)
    for value in (options.ref, options.old):
        if value is not None and not re.fullmatch("[0-9a-f]{40}", value):
            parser.error("--ref and --old must be full 40-character commits")
    if (options.repo is None) != (options.ref is None) or (options.write and options.old is None):
        parser.error("--repo and --ref go together; --write needs --old")
    binding_bytes = BINDING.read_bytes()
    binding = json.loads(binding_bytes)
    current = PIN.read_bytes()
    if options.write:
        if current != (options.old + "\n").encode() or binding["licence_authority"]["ref"] != options.old:
            raise SystemExit("refusing: the pin does not read --old " + options.old)
        if vendored_files() != commit_files(options.repo, options.old):
            raise SystemExit("refusing: the vendored tree is not exactly --old " + options.old)
        wanted = commit_files(options.repo, options.ref)
        before = len(vendored_files())
        shutil.rmtree(VENDORED)
        for path, (mode, data) in sorted(wanted.items()):
            target = VENDORED / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            target.chmod(0o755 if mode == "100755" else 0o644)
        PIN.write_bytes((options.ref + "\n").encode())
        binding["licence_authority"] = regenerate(binding, options.ref)
        BINDING.write_bytes(render(binding))
        print(f"re-pinned {options.old} -> {options.ref} ({before} vendored files before, {len(wanted)} after)")
        binding_bytes, current = BINDING.read_bytes(), PIN.read_bytes()
    ref = binding["licence_authority"]["ref"]
    if current != (ref + "\n").encode():
        raise SystemExit("pin and licence_authority.ref disagree")
    if render(dict(binding, licence_authority=regenerate(binding, ref))) != binding_bytes:
        raise SystemExit("licence_authority does not match the vendored bytes; regenerate it with --write")
    if options.repo is not None:
        if options.ref != ref:
            raise SystemExit(f"the pin reads {ref}, not {options.ref}")
        if vendored_files() != commit_files(options.repo, ref):
            raise SystemExit("the vendored tree is not exactly commit " + ref)
        print(f"{len(vendored_files())} vendored files equal commit {ref}")
    print(f"licence_authority matches the vendored bytes at {ref} "
          f"({len(binding['licence_authority']['files'])} pinned files)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
