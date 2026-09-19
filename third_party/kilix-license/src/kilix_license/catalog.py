"""Load generated determination records and their text blobs."""

from __future__ import annotations

from pathlib import Path

from kilix_license.generate import (
    DATA_DIR,
    RECORDS_DIRNAME,
    TEXTS_DIRNAME,
    data_dir,
    record_filename,
)
from kilix_license.records import LicenseRecord, RecordIndex
from kilix_license.texts import TextStore


def records_dir(root: Path | None = None) -> Path:
    return data_dir(root) / RECORDS_DIRNAME


def texts_dir(root: Path | None = None) -> Path:
    return data_dir(root) / TEXTS_DIRNAME


def load_determined_records(root: Path | None = None) -> RecordIndex:
    directory = records_dir(root)
    records = []
    for path in sorted(directory.glob("*.json")):
        records.append(LicenseRecord.from_bytes(path.read_bytes()))
    return RecordIndex(records)


def load_determined_texts(store_root: Path, *, package_root: Path | None = None) -> TextStore:
    store = TextStore(store_root)
    directory = texts_dir(package_root)
    for path in sorted(directory.iterdir()):
        if not path.is_file() or path.name.startswith("."):
            continue
        store.put(path.read_bytes(), label=path.name)
    return store


def record_path(record_id: str, root: Path | None = None) -> Path:
    return records_dir(root) / record_filename(record_id)


# Default package data location, used by tools when cwd is the repository root.
PACKAGE_DATA = DATA_DIR
