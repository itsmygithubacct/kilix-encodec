"""Atomic receipt store. Construction takes an explicit root only."""

from __future__ import annotations

import fcntl
import os
from pathlib import Path
import time

from kilix_license.errors import AtomicWriteCrashed
from kilix_license.receipts import Receipt, parse_receipt_bytes


class ReceiptStore:
    """JSON receipts under an explicit directory, published by replace."""

    def __init__(self, root: str | os.PathLike[str]) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        os.chmod(self.root, 0o700)

    def path_for(self, record_digest: str, manifest_digest: str) -> Path:
        return self.root / f"{record_digest}-{manifest_digest}.json"

    def lookup(self, record_digest: str, manifest_digest: str) -> Receipt | None:
        path = self.path_for(record_digest, manifest_digest)
        if not path.is_file() or path.name.startswith("."):
            return None
        return parse_receipt_bytes(path.read_bytes())

    def for_record(self, record_digest: str) -> list[Receipt]:
        found: list[Receipt] = []
        for path in self.root.glob(f"{record_digest}-*.json"):
            if path.name.startswith("."):
                continue
            found.append(parse_receipt_bytes(path.read_bytes()))
        return found

    def write(self, receipt: Receipt, *, crash_before_replace: bool = False) -> Path:
        dest = self.path_for(receipt.record_digest, receipt.manifest_digest)
        payload = receipt.to_bytes()
        lock_fd = os.open(self.root / ".store.lock", os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
            stale_prefix = f".{dest.name}"
            for entry in self.root.iterdir():
                name = entry.name
                if name.startswith(stale_prefix) and name.endswith(".tmp"):
                    try:
                        os.unlink(entry)
                    except FileNotFoundError:
                        pass
            tmp = self.root / f".{dest.name}.{os.getpid()}.{time.time_ns()}.tmp"
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
            fd = os.open(tmp, flags, 0o600)
            try:
                os.write(fd, payload)
                os.fsync(fd)
            finally:
                os.close(fd)
            if crash_before_replace:
                # Bytes exist in tmp; dest is unpublished. Retry removes stale
                # tmp under this lock, or uses a unique name per attempt.
                raise AtomicWriteCrashed(f"planted crash before replace {dest.name}")
            os.replace(tmp, dest)
            dir_fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
            os.chmod(dest, 0o600)
            return dest
        finally:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            os.close(lock_fd)
