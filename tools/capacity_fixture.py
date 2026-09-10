"""Fail-closed identity checks for frozen capacity measurements."""

from __future__ import annotations

import hashlib
import os
import platform
import sys
from pathlib import Path
from typing import Any


GIB = 1024**3
H1_CPU_MODEL = "QEMU Virtual CPU version 2.5+"
# Digest of 021-capacity-fixtures/fixture.sh after C52-5 A2 (-accel kvm).
# Must move in the same change as the runner (C52-5 C4).
H1_RUNNER_SHA256 = "51703cc2fadb52cddd75ccbb12cd0f8f5bd7e8c5f227f9f4d2db6af21883eec3"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _accel_is_kvm(runner: str) -> bool:
    """True when the runner passes exactly ``-accel kvm``, never a fallback."""

    found = False
    for raw in runner.splitlines():
        line = raw.strip().rstrip("\\").strip()
        parts = line.split()
        if "-accel" not in parts:
            continue
        value = parts[parts.index("-accel") + 1] if parts.index("-accel") + 1 < len(parts) else ""
        if value != "kvm":
            return False
        found = True
    return found


def runner_matches_h1_contract(runner: str) -> bool:
    """True when the runner text is the H1 machine plus required KVM accel."""

    return (
        "h1) SMP=4; MEM=8192;  DISK=100G" in runner
        and '-machine q35 -cpu qemu64 -smp "$SMP" -m "$MEM"' in runner
        and _accel_is_kvm(runner)
    )


def kvm_clock_present(available_clocksource: str) -> bool:
    """True when the guest advertises the KVM paravirtual clock."""

    return "kvm-clock" in available_clocksource.split()


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
    clock_path = Path(
        "/sys/devices/system/clocksource/clocksource0/available_clocksource"
    )
    clock_text = clock_path.read_text(encoding="utf-8") if clock_path.is_file() else ""
    checks = {
        "runner_regular_file": runner_is_regular,
        "runner_exact_sha256": runner_sha256 == H1_RUNNER_SHA256,
        "runner_h1_contract": runner_matches_h1_contract(runner),
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
        "kvm_clock_available": kvm_clock_present(clock_text),
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
        "available_clocksource": clock_text.strip(),
    }


def _self_test() -> None:
    """Host-side checks that do not inspect the live machine as H1."""

    failed: list[str] = []
    if not kvm_clock_present("tsc kvm-clock acpi_pm"):
        failed.append("kvm_clock_present missed kvm-clock")
    if kvm_clock_present("tsc acpi_pm hpet"):
        failed.append("kvm_clock_present accepted a TCG clock list")
    sample = (
        '  h1) SMP=4; MEM=8192;  DISK=100G ;;\n'
        '  -machine q35 -cpu qemu64 -smp "$SMP" -m "$MEM" \\\n'
        "  -accel kvm \\\n"
    )
    if not runner_matches_h1_contract(sample):
        failed.append("runner_matches_h1_contract rejected the A2 runner")
    tcg = sample.replace("-accel kvm", "-accel kvm:tcg")
    if runner_matches_h1_contract(tcg):
        failed.append("runner_matches_h1_contract accepted colon fallback")
    old = (
        '  h1) SMP=4; MEM=8192;  DISK=100G ;;\n'
        '  -machine q35 -cpu qemu64 -smp "$SMP" -m "$MEM" \\\n'
        "  -drive file=\"$IMG\",format=qcow2,if=virtio \\\n"
    )
    if runner_matches_h1_contract(old):
        failed.append("runner_matches_h1_contract accepted the pre-A2 runner")
    runner_env = os.environ.get("H1_FIXTURE_RUNNER")
    if runner_env:
        runner_path = Path(runner_env)
        text = runner_path.read_text(encoding="utf-8")
        digest = _sha256(runner_path)
        if digest != H1_RUNNER_SHA256:
            failed.append(
                f"runner digest {digest} != H1_RUNNER_SHA256 {H1_RUNNER_SHA256}"
            )
        if not runner_matches_h1_contract(text):
            failed.append("durable runner failed runner_h1_contract")
    if failed:
        raise AssertionError("capacity_fixture self-test failed: " + "; ".join(failed))
    print("capacity_fixture self-test: PASS")


def main(argv: list[str]) -> int:
    if argv[1:] == ["--self-test"]:
        _self_test()
        return 0
    if len(argv) != 2:
        print(
            "usage: capacity_fixture.py --self-test | capacity_fixture.py RUNNER",
            file=sys.stderr,
        )
        return 2
    inspect_h1(Path(argv[1]))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
