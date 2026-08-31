"""Fail-closed identity checks for frozen capacity measurements."""

from __future__ import annotations

import hashlib
import os
import platform
from pathlib import Path
from typing import Any


GIB = 1024**3
H1_CPU_MODEL = "QEMU Virtual CPU version 2.5+"
H1_RUNNER_SHA256 = "46d5e2dc182001fe0e150dc8ff297d369c55b6baa9b048c360090c797b9833b0"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _cpu_records() -> list[dict[str, str]]:
    records = []
    for block in Path("/proc/cpuinfo").read_text(encoding="utf-8").split("\n\n"):
        fields = {}
        for line in block.splitlines():
            if ":" in line:
                name, value = line.split(":", 1)
                fields[name.strip()] = value.strip()
        if "processor" in fields:
            records.append(fields)
    return records


def inspect_h1(runner_path: Path) -> dict[str, Any]:
    """Verify and describe the frozen four-vCPU H1 VM from inside the guest."""

    runner_is_regular = runner_path.is_file() and not runner_path.is_symlink()
    runner = runner_path.read_text(encoding="utf-8") if runner_is_regular else ""
    runner_sha256 = _sha256(runner_path) if runner_is_regular else ""

    cpus = _cpu_records()
    models = sorted({record.get("model name", "") for record in cpus})

    memory_kib = int(
        next(
            line.split()[1]
            for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines()
            if line.startswith("MemTotal:")
        )
    )
    product_name = Path("/sys/class/dmi/id/product_name").read_text(
        encoding="utf-8"
    ).strip()

    root = os.statvfs("/")
    root_total_bytes = root.f_frsize * root.f_blocks
    root_free_bytes = root.f_frsize * root.f_bavail
    root_stat = os.stat("/")
    root_device = Path(
        f"/sys/dev/block/{os.major(root_stat.st_dev)}:{os.minor(root_stat.st_dev)}"
    ).resolve()

    debian_version = Path("/etc/debian_version").read_text(
        encoding="utf-8"
    ).strip()
    checks = {
        "runner_regular_file": runner_is_regular,
        "runner_exact_sha256": runner_sha256 == H1_RUNNER_SHA256,
        "runner_h1_contract": (
            "h1) SMP=4; MEM=8192;  DISK=100G" in runner
            and '-machine q35 -cpu qemu64 -smp "$SMP" -m "$MEM"' in runner
        ),
        "architecture_amd64": platform.machine() == "x86_64",
        "reported_vcpu_count": os.cpu_count() == 4,
        "cpu_record_count": len(cpus) == 4,
        "qemu64_cpu_model": models == [H1_CPU_MODEL],
        "hypervisor_flag": all(
            "hypervisor" in record.get("flags", "").split() for record in cpus
        ),
        "memory_8192_mib": 7_864_320 <= memory_kib <= 8_388_608,
        "q35_machine": "Q35" in product_name,
        "root_on_frozen_vda": "vda" in root_device.parts,
        "root_disk_100_gib": root_total_bytes >= 95 * GIB,
        "root_free_at_least_80_gib": root_free_bytes >= 80 * GIB,
        "debian_13_5": debian_version == "13.5",
    }

    passed = sum(checks.values())
    total = len(checks)
    outcome = "PASS" if passed == total else "FAIL"
    print(f"frozen H1 fixture identity: {passed}/{total} {outcome}")
    if passed != total:
        failed = sorted(name for name, value in checks.items() if not value)
        raise AssertionError(
            f"frozen H1 fixture identity differs: {passed}/{total}; "
            f"failed={','.join(failed)}"
        )

    return {
        "controls": {"passed": passed, "total": total},
        "cpu_model_names": models,
        "debian_version": debian_version,
        "kernel": platform.release(),
        "machine": "q35",
        "memory_kib": memory_kib,
        "root_block_device": root_device.name,
        "root_free_bytes": root_free_bytes,
        "root_total_bytes": root_total_bytes,
        "runner_sha256": runner_sha256,
        "tier": "H1",
        "vcpus": len(cpus),
    }
