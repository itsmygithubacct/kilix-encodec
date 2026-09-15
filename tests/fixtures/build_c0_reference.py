#!/usr/bin/env python3
"""Record the C0 epoch-start reference from a git archive of kilix-encodec 3747330.

Run in the locked export environment of this checkout:

    git archive 3747330ec7236931096eefc1e84100ad027c2444 | tar -x -C REFERENCE
    python tests/fixtures/build_c0_reference.py --reference-source REFERENCE \
        --bundle EXPORTED_24KHZ_BUNDLE --programme-dir PROGRAMME_DIR \
        --output NEW_FILE.json [--spike-items SPIKE_RAW_ITEMS_DIR]

The reference source must hash to 3747330's Git tree. Each of the 14 programme
items (the 9 synthetic items regenerated, the 5 recordings read from the
programme directory, every WAV checked against the programme manifest hash
recorded in f101-c5r4-programme.json) is rendered at 6, 3 and 12 kb/s by
3747330's own ``tools/verify_export.py`` ``encode`` and ``decode``: a zero-state
reset every 25 packets, one intra-op thread per ORT session. No streaming code
of this checkout renders the reference. The optional spike directory adds a
cross-check against the F101 remedy spike's own C0 arm at 6 kb/s.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

REPOSITORY = Path(__file__).resolve().parents[2]
COMMIT = "3747330ec7236931096eefc1e84100ad027c2444"
TREE = "837dec0624e73a2709b5f34334ffdd25f99a146c"
MANIFEST_SHA256 = "02201a5a947dc0a0b9cce84d585eca35fb8a7e57aee4d404d5cb14f495f40b6c"
BITRATES = ("6", "3", "12")
EPOCH_PACKETS = 25
PACKET_SAMPLES = 960


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def git_tree(source: Path) -> str:
    """Tree id of an extracted archive, computed in a private temporary index."""

    with tempfile.TemporaryDirectory(prefix="c0-reference-tree-") as temporary:
        base = {"PATH": "/usr/bin:/bin", "HOME": temporary, "GIT_CONFIG_NOSYSTEM": "1"}
        git_dir = str(Path(temporary) / "git")
        subprocess.run(["git", "init", "-q", "--bare", git_dir], check=True, env=base)
        environment = dict(base, GIT_DIR=git_dir, GIT_WORK_TREE=str(source))
        subprocess.run(["git", "add", "-A", "-f", "."], check=True, cwd=source, env=environment)
        return subprocess.run(
            ["git", "write-tree"], check=True, cwd=source, env=environment, capture_output=True, text=True
        ).stdout.strip()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--reference-source", type=Path, required=True)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--programme-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--spike-items", type=Path)
    args = parser.parse_args()

    import numpy as np
    import onnxruntime

    tree = git_tree(args.reference_source.resolve())
    if tree != TREE:
        raise SystemExit(f"reference source tree {tree} is not 3747330's {TREE}")
    manifest_bytes = (args.bundle / "manifest.json").read_bytes()
    if hashlib.sha256(manifest_bytes).hexdigest() != MANIFEST_SHA256:
        raise SystemExit("bundle manifest is not the pinned 02201a5a export")

    programme = load("kenc_c0_programme", REPOSITORY / "tools" / "verify_epoch_programme.py")
    expectations = programme.load_expectations(programme.DEFAULT_EXPECTATIONS)
    programme_manifest = args.programme_dir / "manifest.json"
    if programme.sha256(programme_manifest.read_bytes()) != expectations["programme"]["manifest_sha256"]:
        raise SystemExit("programme manifest sha256 differs")

    reference_tools = args.reference_source.resolve() / "tools"
    sys.path.insert(0, str(reference_tools))
    reference = load("kenc_reference_3747330_verify_export", reference_tools / "verify_export.py")

    records = json.loads(manifest_bytes)["graphs"]
    sessions: dict[str, object] = {}

    def session(key: str) -> object:
        if key not in sessions:
            sessions[key] = reference.session(args.bundle / records[key]["file"], 1)
        return sessions[key]

    items: dict[str, object] = {}
    for item in expectations["items"]:
        name = item["name"]
        if item["synthetic"]:
            raw = programme.wav_bytes(programme.to_int16(programme.synthetic_float(name)))
        else:
            path = args.programme_dir / f"{name}.wav"
            if path.is_symlink() or not path.is_file():
                raise SystemExit(f"{name}: recording is missing")
            raw = path.read_bytes()
        if programme.sha256(raw) != item["wav_sha256"]:
            raise SystemExit(f"{name}: WAV sha256 differs from the programme manifest")
        pcm = np.frombuffer(np.ascontiguousarray(programme.read_wav(raw)).tobytes(), dtype="<i2")
        audio = (pcm.astype(np.float32) / np.float32(32768.0)).reshape(1, 1, -1)
        resets = set(range(0, audio.shape[-1] // PACKET_SAMPLES, EPOCH_PACKETS))
        renders = {}
        for kbps in BITRATES:
            bandwidth = float(kbps)
            codes = reference.encode(
                session("encoder"), session(f"rvq_encode_{kbps}kbps"), records, audio, bandwidth, resets
            )
            decoded = reference.decode(
                session(f"rvq_decode_{kbps}kbps"), session("decoder"), records, codes, bandwidth, resets
            )
            renders[kbps] = programme.render_hashes(codes, decoded)
        items[name] = {"wav_sha256": item["wav_sha256"], "renders": renders}
        print(f"  rendered {name}", flush=True)

    document = {
        "schema": "kilix.encodec.c0-legacy-reference/v1",
        "epoch_packets": EPOCH_PACKETS,
        "programme_manifest_sha256": expectations["programme"]["manifest_sha256"],
        "bundle_manifest_sha256": MANIFEST_SHA256,
        "reference": {
            "commit": COMMIT,
            "tree": TREE,
            "source": "git archive of the reference commit; tree id recomputed from the extracted files",
            "renderer": "tools/verify_export.py encode() and decode() with resets at every 25th packet; session(path, 1)",
            "generator": "tests/fixtures/build_c0_reference.py",
            "numpy": np.__version__,
            "onnxruntime": onnxruntime.__version__,
        },
        "hash_fields": "codes: sha256 of int64 [n_q, 1, frames]; pcm_float32: sha256 of float32 [1, 1, samples]",
        "items": items,
    }
    if args.spike_items is not None:
        equal = 0
        for name, row in items.items():
            arm = json.loads((args.spike_items / f"{name}.json").read_bytes())["arms"]["C0"]
            equal += int(arm["epoch_packets"] == EPOCH_PACKETS and arm["render_sha256"] == row["renders"]["6"])
        document["spike_c0_cross_check"] = {
            "source": "F101 remedy spike raw/items/<item>.json arms.C0.render_sha256 at 6 kb/s",
            "compared": len(items),
            "equal": equal,
        }
        print(f"spike C0 arm cross-check at 6 kb/s: {equal}/{len(items)} equal", flush=True)
        if equal != len(items):
            raise SystemExit("the 3747330 C0 reference differs from the spike's C0 arm")
    descriptor = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    with os.fdopen(descriptor, "w") as output:
        json.dump(document, output, indent=1, sort_keys=True)
        output.write("\n")
    print(f"C0 reference: {len(items)} items x {len(BITRATES)} rates written", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
