"""Atomic receipt store. Construction takes an explicit root only."""

from __future__ import annotations

import fcntl
import os
from pathlib import Path
import stat
import time

from kilix_license.errors import AtomicWriteCrashed
from kilix_license.receipts import Receipt, parse_receipt_bytes

# A receipt is well under 4 KiB. No entry larger than this is read.
MAX_RECEIPT_BYTES = 1 << 20
# O_NONBLOCK: a FIFO swapped in after the stat cannot block the open or a read.
# O_NOCTTY: a terminal swapped in cannot become this process's controlling terminal.
_RECEIPT_OPEN_FLAGS = os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC | os.O_NOCTTY


def read_receipt_file(path: str | os.PathLike[str]) -> bytes | None:
    """The bytes of one receipt entry, or None when it is not a receipt file.

    One discipline for the changed-text scan and for require() (T1, T2,
    LIC4-VERIFY LIC4-7). The target is stat'ed first, following symlinks as
    lookup() always did, so a character device, FIFO, socket or directory is
    never opened. An entry swapped between that stat and the open is still
    harmless: the open is O_NONBLOCK | O_NOCTTY, and the descriptor must fstat
    as a regular file of at most MAX_RECEIPT_BYTES before one byte is read.
    The read stops at MAX_RECEIPT_BYTES + 1 bytes, whatever st_size says, and
    a larger entry is None. Every OSError (missing, unreadable, vanished, or a
    pseudo-file that refuses the read) is None too. The bytes of a regular
    file are returned unchanged, so a receipt parses exactly as before.
    """
    try:
        if not stat.S_ISREG(os.stat(path).st_mode):
            return None
        fd = os.open(path, _RECEIPT_OPEN_FLAGS)
    except OSError:
        return None
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_RECEIPT_BYTES:
            return None
        chunks = []
        budget = MAX_RECEIPT_BYTES + 1
        while budget > 0:
            chunk = os.read(fd, min(65536, budget))
            if not chunk:
                break
            chunks.append(chunk)
            budget -= len(chunk)
        data = b"".join(chunks)
        return None if len(data) > MAX_RECEIPT_BYTES else data
    except OSError:
        return None
    finally:
        os.close(fd)


class ReceiptStore:
    """JSON receipts under an explicit directory, published by replace."""

    def __init__(self, root: str | os.PathLike[str]) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        os.chmod(self.root, 0o700)

    def path_for(self, record_digest: str, manifest_digest: str) -> Path:
        return self.root / f"{record_digest}-{manifest_digest}.json"

    def lookup(self, record_digest: str, manifest_digest: str) -> Receipt | None:
        """The receipt at the exact path, or None (LIC4-VERIFY LIC4-7).

        An entry that read_receipt_file() refuses (not a regular file at the
        stat or at the open, larger than MAX_RECEIPT_BYTES, or unreadable)
        holds no receipt; it is never waited on. A readable regular file is
        parsed exactly as before, and a malformed one still raises.
        """
        path = self.path_for(record_digest, manifest_digest)
        if path.name.startswith("."):
            return None
        data = read_receipt_file(path)
        if data is None:
            return None
        return parse_receipt_bytes(data)

    def for_record(self, record_digest: str) -> list[Receipt]:
        """Receipts for record_digest under any manifest.

        An entry that read_receipt_file() refuses (a FIFO, a device, a
        directory, an oversized or unreadable file) is left out and never
        waited on. A readable regular file is parsed exactly as before.
        """
        found: list[Receipt] = []
        for path in self.root.glob(f"{record_digest}-*.json"):
            if path.name.startswith("."):
                continue
            data = read_receipt_file(path)
            if data is None:
                continue
            found.append(parse_receipt_bytes(data))
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
