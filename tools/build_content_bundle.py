#!/usr/bin/env python3
"""Embed an exact committed content authority with the native admission helper.

The final closure supplies the content commit. No model payload, runtime path,
local catalog override or network request is accepted by this build step.

asset/v3 keeps licence receipts in kilix-license, which the content commit
vendors at `third_party/kilix-license`. The bundle carries that vendored copy
too, and only when it is the licence authority this repository pins: the
content commit's `third_party/kilix-license.pin` must equal ours, and every
embedded kilix-license module, record and licence must hash to the digest
`tools/converter-inputs.json` binds under `licence_authority.files`.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import re
import selectors
import subprocess
import time
import zipfile


def git_bytes(source: Path, arguments: list[str], maximum: int, *, check=None, cleanup=None) -> bytes:
    """Poll the caller's unchanged budget; cleanup belongs to its dedicated CLI.

    Ordinary bundle callers need no process-wide reaper. A dedicated subreaper
    may supply cleanup to prove its escaped descendants are gone as well.
    """
    if check is None:
        check = lambda: None
    check()
    # Neither the calling shell nor archive attributes may select different
    # bytes under the requested commit's name. Missing objects stay offline.
    environment = {"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C",
                   "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null",
                   "GIT_CONFIG_SYSTEM": "/dev/null", "GIT_NO_REPLACE_OBJECTS": "1",
                   "GIT_NO_LAZY_FETCH": "1", "GIT_TERMINAL_PROMPT": "0"}
    command = ["/usr/bin/git", "--no-replace-objects", "--literal-pathspecs",
               "-C", str(source.resolve()), *arguments]
    with subprocess.Popen(command, env=environment, stdin=subprocess.DEVNULL,
                          stdout=subprocess.PIPE, stderr=subprocess.DEVNULL) as child:
        try:
            payload = bytearray()
            os.set_blocking(child.stdout.fileno(), False)
            with selectors.DefaultSelector() as selector:
                selector.register(child.stdout, selectors.EVENT_READ)
                while selector.get_map():
                    check()
                    # An escaped descendant can retain the output pipe after
                    # Git exits. The native CLI owns that complete subtree.
                    if child.poll() is not None and cleanup is not None:
                        cleanup()
                    for key, _events in selector.select(.05):
                        check()
                        try:
                            block = os.read(key.fd, min(65536, maximum + 1 - len(payload)))
                        except BlockingIOError:
                            continue
                        if not block:
                            selector.unregister(key.fileobj)
                        else:
                            payload.extend(block)
                            if len(payload) > maximum:
                                raise ValueError("content Git object exceeds bound")
                # EOF does not prove process exit. Keep the same cancellation
                # and deadline checks while an output-less Git child remains.
                while child.poll() is None:
                    check()
                    time.sleep(.01)
            check()
            if child.returncode != 0:
                raise ValueError("content Git object is unavailable")
            return bytes(payload)
        finally:
            if child.poll() is None:
                child.kill()
            child.wait()
            if cleanup is not None:
                cleanup()


def git_object(source: Path, kind: str, oid: str, maximum: int, *, check=None, cleanup=None) -> bytes:
    if not re.fullmatch("[0-9a-f]{40}", oid):
        raise ValueError("invalid content Git identity")
    payload = git_bytes(source, ["cat-file", kind, oid], maximum, check=check, cleanup=cleanup)
    identity = kind.encode() + b" " + str(len(payload)).encode() + b"\0" + payload
    if hashlib.sha1(identity).hexdigest() != oid:
        raise ValueError("content Git object identity mismatch")
    if check is not None:
        check()
    return payload


def git_tree(source: Path, oid: str, *, check=None, cleanup=None) -> dict[str, tuple[str, str]]:
    payload = git_object(source, "tree", oid, 2 * 1024**2, check=check, cleanup=cleanup)
    result = {}
    while payload:
        if check is not None:
            check()
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
    licence = licence_authority()

    def entry(path: str) -> tuple[str, str]:
        tree_oid, parts = tree, path.split("/")
        for index, name in enumerate(parts):
            found = git_tree(source, tree_oid).get(name)
            if found is None or (index < len(parts) - 1 and found[0] != "40000"):
                raise ValueError("content commit does not vendor the pinned licence authority")
            mode, tree_oid = found
        return mode, tree_oid

    def pinned(path: str, name: str):
        mode, oid = entry(path)
        add(path, name, mode, oid)
        if hashlib.sha256(members[name]).hexdigest() != licence["files"][path]:
            raise ValueError("content commit's licence authority differs from the pinned one")

    pin_mode, pin_oid = entry(LICENCE_PIN)
    if (pin_mode not in ("100644", "100755")
            or git_object(source, "blob", pin_oid, 4096) != (licence["ref"] + "\n").encode()):
        raise ValueError("content commit pins a different licence authority")
    package = LICENCE_PACKAGE.rstrip("/")
    listed = {path for path in licence["files"] if path.startswith(LICENCE_PACKAGE)}
    modules = {path for path in listed if path.count("/") == 4 and path.endswith(".py")}
    records = listed - modules
    present = {package + "/" + name for name, (mode, _oid) in git_tree(source, entry(package)[1]).items()
               if mode != "40000" and name.endswith(".py")}
    if present != modules or package + "/__init__.py" not in modules or not records:
        raise ValueError("content commit's licence authority modules differ from the pinned set")
    for path in sorted(listed):
        pinned(path, path[len("third_party/kilix-license/src/"):])
    pinned(LICENCE_LICENSE, "licenses/kilix-license.txt")
    return members, tree, objects


LICENCE_PIN = "third_party/kilix-license.pin"
LICENCE_LICENSE = "third_party/kilix-license/LICENSE"
LICENCE_PACKAGE = "third_party/kilix-license/src/kilix_license/"


def licence_authority() -> dict:
    """This repository's own licence-authority pin, which the bundle must match."""
    root = Path(__file__).resolve().parents[1]
    binding = json.loads((root / "tools" / "converter-inputs.json").read_bytes())
    authority = binding.get("licence_authority", {})
    ref, files = authority.get("ref"), authority.get("files")
    if (not isinstance(ref, str) or not re.fullmatch("[0-9a-f]{40}", ref)
            or not isinstance(files, dict) or LICENCE_LICENSE not in files
            or (root / LICENCE_PIN).read_bytes() != (ref + "\n").encode()):
        raise ValueError("this repository's licence authority pin is incomplete")
    records = {profile.get("record", {}).get("path") for profile in binding.get("profiles", {}).values()}
    if not records or not records <= set(files) or not all(
            isinstance(digest, str) and re.fullmatch("[0-9a-f]{64}", digest) for digest in files.values()):
        raise ValueError("this repository's licence authority pin is incomplete")
    return dict(ref=ref, files={path: digest for path, digest in files.items()
                                if path.startswith(LICENCE_PACKAGE) and (path.endswith(".py") or path in records)
                                or path == LICENCE_LICENSE})


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
    if (not 5 <= len(members) <= 128 or "kilix_content/__init__.py" not in members
            or "licenses/kilix-content.txt" not in members or "kilix_license/__init__.py" not in members
            or "licenses/kilix-license.txt" not in members):
        raise ValueError("incomplete content authority population")
    root = Path(__file__).resolve().parents[1]
    for path in ("installed_assets.py", "graph_population.py", "content_worker.py"):
        members["__main__.py" if path == "content_worker.py" else path] = (root / "python" / path).read_bytes()
    record = dict(schema="kilix.encodec.content-build/v2", content_commit=commit,
        content_tree=tree, content_objects=objects, licence_authority_ref=licence_authority()["ref"],
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
