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
import zipfile


def git_bytes(source: Path, arguments: list[str], maximum: int) -> bytes:
    # Neither the calling shell nor archive attributes may select different
    # bytes under the requested commit's name. Missing objects stay offline.
    environment = {"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C",
                   "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null",
                   "GIT_CONFIG_SYSTEM": "/dev/null", "GIT_NO_REPLACE_OBJECTS": "1",
                   "GIT_NO_LAZY_FETCH": "1", "GIT_TERMINAL_PROMPT": "0"}
    command = ["/usr/bin/git", "--no-replace-objects", "--literal-pathspecs",
               "-C", str(source.resolve()), *arguments]
    with subprocess.Popen(command, env=environment, stdout=subprocess.PIPE,
                          stderr=subprocess.DEVNULL) as child:
        try:
            payload = child.stdout.read(maximum + 1)
            if len(payload) > maximum:
                raise ValueError("content Git object exceeds bound")
            if child.wait() != 0:
                raise ValueError("content Git object is unavailable")
            return payload
        finally:
            if child.poll() is None:
                child.kill()
                child.wait()


def git_object(source: Path, kind: str, oid: str, maximum: int) -> bytes:
    if not re.fullmatch("[0-9a-f]{40}", oid):
        raise ValueError("invalid content Git identity")
    payload = git_bytes(source, ["cat-file", kind, oid], maximum)
    identity = kind.encode() + b" " + str(len(payload)).encode() + b"\0" + payload
    if hashlib.sha1(identity).hexdigest() != oid:
        raise ValueError("content Git object identity mismatch")
    return payload


def git_tree(source: Path, oid: str) -> dict[str, tuple[str, str]]:
    payload = git_object(source, "tree", oid, 2 * 1024**2)
    result = {}
    while payload:
        metadata, separator, remaining = payload.partition(b"\0")
        if not separator or len(remaining) < 20:
            raise ValueError("invalid content Git tree")
        mode, separator, raw_name = metadata.partition(b" ")
        name = raw_name.decode("utf-8")
        if (not separator or not name or name in (".", "..") or "/" in name
                or name in result or mode not in (b"40000", b"100644", b"100755", b"120000", b"160000")):
            raise ValueError("unsupported content Git tree entry")
        result[name] = (mode.decode(), remaining[:20].hex())
        payload = remaining[20:]
    return result


def content_members(source: Path, commit: str):
    raw_commit = git_object(source, "commit", commit, 64 * 1024)
    first_line = raw_commit.split(b"\n", 1)[0]
    if not re.fullmatch(b"tree [0-9a-f]{40}", first_line):
        raise ValueError("content commit has no canonical tree")
    tree = first_line[5:].decode()
    root = git_tree(source, tree)
    if root.get("src", (None,))[0] != "40000" or root.get("LICENSE", (None,))[0] not in ("100644", "100755"):
        raise ValueError("content package or license is missing")
    src = git_tree(source, root["src"][1])
    if src.get("kilix_content", (None,))[0] != "40000":
        raise ValueError("content package is missing")
    members, objects = {}, {}
    total = 0
    entries = 0

    def add(path: str, name: str, mode: str, oid: str):
        nonlocal total
        if mode not in ("100644", "100755"):
            raise ValueError("content authority contains unsupported entries")
        value = git_object(source, "blob", oid, 2 * 1024**2)
        total += len(value)
        if total > 4 * 1024**2 or len(members) >= 128:
            raise ValueError("content authority population exceeds bound")
        members[name] = value
        objects[path] = dict(mode=mode, oid=oid, bytes=len(value))

    def visit(oid: str, prefix: str, depth: int):
        nonlocal entries
        if depth > 16:
            raise ValueError("content package nesting exceeds bound")
        for name, (mode, child) in git_tree(source, oid).items():
            entries += 1
            if entries > 256:
                raise ValueError("content package entries exceed bound")
            path = prefix + "/" + name
            if mode == "40000":
                visit(child, path, depth + 1)
            elif (Path(name).suffix in (".py", ".json") or name == "py.typed") and "__pycache__" not in path.split("/"):
                add("src/" + path, path, mode, child)
            else:
                raise ValueError("unexpected content package member")

    add("LICENSE", "licenses/kilix-content.txt", *root["LICENSE"])
    visit(src["kilix_content"][1], "kilix_content", 0)
    return members, tree, objects


def write_changed(path: Path, payload: bytes):
    if path.exists() and path.read_bytes() == payload:
        return
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(payload)
    temporary.replace(path)


def build(source: Path, commit: str, output: Path):
    if not re.fullmatch("[0-9a-f]{40}", commit):
        raise ValueError("an exact content commit is required")
    members, tree, objects = content_members(source, commit)
    if not 5 <= len(members) <= 128 or "kilix_content/__init__.py" not in members or "licenses/kilix-content.txt" not in members:
        raise ValueError("incomplete content authority population")
    root = Path(__file__).resolve().parents[1]
    for path in ("installed_assets.py", "graph_population.py", "content_worker.py"):
        members["__main__.py" if path == "content_worker.py" else path] = (root / "python" / path).read_bytes()
    record = dict(schema="kilix.encodec.content-build/v2", content_commit=commit,
        content_tree=tree, content_objects=objects,
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
