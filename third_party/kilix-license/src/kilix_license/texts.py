"""Content-addressed licence text store: files named by sha256."""

from __future__ import annotations

import os
from pathlib import Path

from kilix_license.digest import require_sha256, sha256_hex
from kilix_license.errors import TextDigestMismatch
from kilix_license.paraphrase import refuse_paraphrase


class TextStore:
    def __init__(self, root: str | os.PathLike[str]) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def put(self, data: bytes, *, label: str = "text") -> str:
        refuse_paraphrase(data, label)
        digest = sha256_hex(data)
        path = self.root / digest
        if path.is_file():
            existing = path.read_bytes()
            if existing != data:
                raise TextDigestMismatch(
                    f"{label} collides at {digest} with different bytes"
                )
            return digest
        tmp = self.root / f".{digest}.tmp"
        tmp.write_bytes(data)
        os.replace(tmp, path)
        return digest

    def get(self, digest: str, *, label: str = "text") -> bytes:
        digest = require_sha256(digest, label)
        path = self.root / digest
        data = path.read_bytes()
        if sha256_hex(data) != digest:
            raise TextDigestMismatch(f"{label} {digest} does not match stored bytes")
        refuse_paraphrase(data, label)
        return data
