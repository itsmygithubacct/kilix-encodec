#!/usr/bin/env python3
"""Package the shared system runtime from a checksum-pinned upstream distribution."""

import argparse
import os
from pathlib import Path
import runpy
import subprocess
import tarfile
import tempfile


def package(arch, archive, output):
    installer = runpy.run_path(str(Path(__file__).resolve().parents[1] / "packaging/install-converter"))
    selected = installer["archive_for"](arch)
    expected = installer["expected_inventory"](selected, shared=True)
    with tempfile.TemporaryDirectory(dir=output.parent) as temporary:
        stage = Path(temporary)
        payload = stage / "payload"
        payload.mkdir()
        if not installer["stage_download"](stage, payload, selected, archive):
            installer["share_runtime"](payload, selected)
        if installer["inventory"](payload, owner=os.geteuid()) != expected:
            raise ValueError("shared converter differs from the pinned inventory")

        packed = stage / "shared.tar.zst"
        with packed.open("xb") as dest, subprocess.Popen(
                ["zstd", "-q", "-12", "-T1", "-c"], stdin=subprocess.PIPE, stdout=dest) as compressor:
            with compressor.stdin, tarfile.open(fileobj=compressor.stdin, mode="w|",
                                                format=tarfile.USTAR_FORMAT) as tar:
                for name, (mode, digest) in sorted(expected.items()):
                    if digest is None:
                        continue
                    member = tarfile.TarInfo(name)
                    member.size = (payload / name).stat().st_size
                    member.mode = mode
                    with (payload / name).open("rb") as source:
                        tar.addfile(member, source)
            if compressor.wait():
                raise ValueError("converter compression failed")
        compact = selected["compact"]
        installer["verify_archive"](packed, compact)
        os.link(packed, output)
    print(f"{compact['sha256']}  {output.name} ({compact['size']} bytes)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("arch", choices=("amd64", "arm64"))
    parser.add_argument("archive", type=Path, help="original pinned distribution")
    parser.add_argument("output", type=Path, help="new .tar.zst file")
    args = parser.parse_args()
    package(args.arch, args.archive, args.output)
