"""Fail-closed identity checks for frozen capacity measurements."""

from __future__ import annotations

import hashlib
import json
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


def collect_h1_snapshot() -> dict[str, Any]:
    """Read the live machine. This is the guest when inspect_h1 runs there."""

    cpus = _cpu_records()
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
    return {
        "architecture": platform.machine(),
        "available_clocksource": clock_text.strip(),
        "cpu_records": cpus,
        "debian_version": debian_version,
        "kernel": platform.release(),
        "memory_kib": memory_kib,
        "product_name": product_name,
        "reported_vcpu_count": os.cpu_count(),
        "root_device_parts": list(root_device.parts),
        "root_device_name": root_device.name,
        "root_free_bytes": root.f_frsize * root.f_bavail,
        "root_total_bytes": root.f_frsize * root.f_blocks,
    }


def check_h1_snapshot(
    snapshot: dict[str, Any],
    runner: str,
    runner_sha256: str,
    runner_is_regular: bool,
) -> dict[str, Any]:
    """Apply the H1 identity controls to a snapshot plus the runner bytes."""

    cpus = list(snapshot.get("cpu_records") or [])
    models = sorted({record.get("model name", "") for record in cpus})
    clock_text = str(snapshot.get("available_clocksource") or "")
    checks = {
        "runner_regular_file": runner_is_regular,
        "runner_exact_sha256": runner_sha256 == H1_RUNNER_SHA256,
        "runner_h1_contract": runner_matches_h1_contract(runner),
        "architecture_amd64": snapshot.get("architecture") == "x86_64",
        "reported_vcpu_count": snapshot.get("reported_vcpu_count") == 4,
        "cpu_record_count": len(cpus) == 4,
        "qemu64_cpu_model": models == [H1_CPU_MODEL],
        "hypervisor_flag": all(
            "hypervisor" in record.get("flags", "").split() for record in cpus
        ),
        "memory_8192_mib": 7_864_320 <= int(snapshot.get("memory_kib") or 0) <= 8_388_608,
        "q35_machine": "Q35" in str(snapshot.get("product_name") or ""),
        "root_on_frozen_vda": "vda" in list(snapshot.get("root_device_parts") or []),
        "root_disk_100_gib": int(snapshot.get("root_total_bytes") or 0) >= 95 * GIB,
        "root_free_at_least_80_gib": int(snapshot.get("root_free_bytes") or 0) >= 80 * GIB,
        "debian_13_5": snapshot.get("debian_version") == "13.5",
        "kvm_clock_available": kvm_clock_present(clock_text),
    }

    passed = sum(1 for value in checks.values() if value)
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
        "debian_version": snapshot.get("debian_version"),
        "kernel": snapshot.get("kernel"),
        "machine": "q35",
        "memory_kib": snapshot.get("memory_kib"),
        "root_block_device": snapshot.get("root_device_name"),
        "root_free_bytes": snapshot.get("root_free_bytes"),
        "root_total_bytes": snapshot.get("root_total_bytes"),
        "runner_sha256": runner_sha256,
        "tier": "H1",
        "vcpus": len(cpus),
        "available_clocksource": clock_text,
    }


def inspect_h1(
    runner_path: Path,
    snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Verify the frozen four-vCPU H1 VM. Snapshot defaults to the live machine."""

    runner_is_regular = runner_path.is_file() and not runner_path.is_symlink()
    runner = runner_path.read_text(encoding="utf-8") if runner_is_regular else ""
    runner_sha256 = _sha256(runner_path) if runner_is_regular else ""
    if snapshot is None:
        snapshot = collect_h1_snapshot()
    return check_h1_snapshot(snapshot, runner, runner_sha256, runner_is_regular)


def example_h1_snapshot() -> dict[str, Any]:
    """Measured H1 guest shape from the 2026-09-07 identity record."""

    cpu = {
        "processor": "0",
        "model name": H1_CPU_MODEL,
        "flags": "fpu hypervisor",
    }
    return {
        "architecture": "x86_64",
        "available_clocksource": "tsc kvm-clock acpi_pm",
        "cpu_records": [{**cpu, "processor": str(i)} for i in range(4)],
        "debian_version": "13.5",
        "kernel": "6.12.86+deb13-amd64",
        "memory_kib": 8138024,
        "product_name": "Standard PC (Q35 + ICH9, 2009)",
        "reported_vcpu_count": 4,
        "root_device_parts": ["/", "sys", "devices", "pci0000:00", "virtio0", "block", "vda", "vda1"],
        "root_device_name": "vda1",
        "root_free_bytes": 98453762048,
        "root_total_bytes": 105088274432,
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
    runner_text = sample
    runner_digest = H1_RUNNER_SHA256
    runner_regular = True
    if runner_env:
        runner_path = Path(runner_env)
        runner_text = runner_path.read_text(encoding="utf-8")
        runner_digest = _sha256(runner_path)
        runner_regular = runner_path.is_file() and not runner_path.is_symlink()
        if runner_digest != H1_RUNNER_SHA256:
            failed.append(
                f"runner digest {runner_digest} != H1_RUNNER_SHA256 {H1_RUNNER_SHA256}"
            )
        if not runner_matches_h1_contract(runner_text):
            failed.append("durable runner failed runner_h1_contract")

    snapshot = example_h1_snapshot()
    try:
        check_h1_snapshot(snapshot, runner_text, runner_digest, runner_regular)
    except AssertionError as error:
        failed.append(f"valid snapshot rejected: {error}")

    broken = dict(snapshot)
    broken["available_clocksource"] = "tsc acpi_pm hpet"
    try:
        check_h1_snapshot(broken, runner_text, runner_digest, runner_regular)
        failed.append("TCG snapshot was accepted")
    except AssertionError:
        pass

    if failed:
        raise AssertionError("capacity_fixture self-test failed: " + "; ".join(failed))
    print("capacity_fixture self-test: PASS")


def main(argv: list[str]) -> int:
    if argv[1:] == ["--self-test"]:
        _self_test()
        return 0
    if len(argv) == 4 and argv[1] == "--snapshot":
        snapshot = json.loads(Path(argv[2]).read_text(encoding="utf-8"))
        inspect_h1(Path(argv[3]), snapshot=snapshot)
        return 0
    if len(argv) != 2:
        print(
            "usage: capacity_fixture.py --self-test | "
            "capacity_fixture.py --snapshot FILE RUNNER | "
            "capacity_fixture.py RUNNER",
            file=sys.stderr,
        )
        return 2
    inspect_h1(Path(argv[1]))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
