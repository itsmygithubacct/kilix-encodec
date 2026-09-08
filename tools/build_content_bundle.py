#!/usr/bin/env python3
"""Embed an exact committed content authority with the native admission helper.

The final closure supplies the content commit. No model payload, runtime path,
local catalog override or network request is accepted by this build step.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
from pathlib import Path
import re
import subprocess
import tarfile
import zipfile


def write_changed(path: Path, payload: bytes):
    if path.exists() and path.read_bytes() == payload:
        return
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(payload)
    temporary.replace(path)


def build(source: Path, commit: str, output: Path):
    if not re.fullmatch("[0-9a-f]{40}", commit):
        raise ValueError("an exact content commit is required")
    actual = subprocess.check_output(["git", "rev-parse", "--verify", commit + "^{commit}"], cwd=source, text=True).strip()
    if actual != commit:
        raise ValueError("content commit did not resolve exactly")
    archive = subprocess.check_output(["git", "archive", "--format=tar", commit,
        "src/kilix_content", "LICENSE"], cwd=source)
    if len(archive) > 4*1024**2:
        raise ValueError("content authority archive exceeds bound")
    members = {}
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as tar:
        for entry in tar.getmembers():
            if entry.isdir():
                continue
            if not entry.isfile() or entry.size > 2*1024**2:
                raise ValueError("content authority contains unsupported entries")
            path = Path(entry.name)
            if entry.name == "LICENSE":
                name = "licenses/kilix-content.txt"
            elif (entry.name.startswith("src/kilix_content/") and (path.suffix in (".py", ".json") or path.name == "py.typed")
                    and "__pycache__" not in path.parts):
                name = entry.name.removeprefix("src/")
            else:
                raise ValueError("unexpected content package member")
            if name in members:
                raise ValueError("duplicate content member")
            members[name] = tar.extractfile(entry).read()
    if not 5 <= len(members) <= 128 or "kilix_content/__init__.py" not in members or "licenses/kilix-content.txt" not in members:
        raise ValueError("incomplete content authority population")
    root = Path(__file__).resolve().parents[1]
    for path in ("installed_assets.py", "graph_population.py", "content_worker.py"):
        members["__main__.py" if path == "content_worker.py" else path] = (root / "python" / path).read_bytes()
    record = dict(schema="kilix.encodec.content-build/v1", content_commit=commit,
        content_archive_sha256=hashlib.sha256(archive).hexdigest(),
        files={name:dict(bytes=len(data),sha256=hashlib.sha256(data).hexdigest()) for name,data in sorted(members.items())})
    bundle = io.BytesIO()
    with zipfile.ZipFile(bundle, "w", compression=zipfile.ZIP_STORED) as target:
        for name, data in sorted(members.items()):
            entry = zipfile.ZipInfo(name, date_time=(1980,1,1,0,0,0))
            entry.create_system = 3
            entry.external_attr = 0o100644 << 16
            target.writestr(entry, data)
    payload = bundle.getvalue()
    if len(payload) > 2*1024**2:
        raise ValueError("content ZIP exceeds bound")
    record.update(bundle_bytes=len(payload), bundle_sha256=hashlib.sha256(payload).hexdigest())
    lines=["/* Generated from an exact content commit and this helper source. */",
           "#ifndef KENC_CONTENT_BUNDLE_H", "#define KENC_CONTENT_BUNDLE_H",
           f'#define KENC_CONTENT_COMMIT "{commit}"',
           f'#define KENC_CONTENT_BUNDLE_SHA256 "{record["bundle_sha256"]}"',
           "static const unsigned char kenc_content_bundle[] = {"]
    for start in range(0,len(payload),16):
        lines.append("    " + ",".join(f"0x{v:02x}" for v in payload[start:start+16]) + ",")
    lines += ["};", "#endif", ""]
    output.mkdir(parents=True,exist_ok=True)
    write_changed(output / "content_bundle.h", "\n".join(lines).encode())
    write_changed(output / "content_bundle.zip", payload)
    write_changed(output / "CONTENT-LICENSE.txt", members["licenses/kilix-content.txt"])
    write_changed(output / "content_bundle.receipt.json", (json.dumps(record,indent=2,sort_keys=True)+"\n").encode())
    print(f'content authority {commit}: {len(members)} files, ZIP {record["bundle_sha256"]}')


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source",type=Path,required=True)
    parser.add_argument("--commit",required=True)
    parser.add_argument("--output",type=Path,required=True)
    options = parser.parse_args()
    build(options.source,options.commit,options.output)
