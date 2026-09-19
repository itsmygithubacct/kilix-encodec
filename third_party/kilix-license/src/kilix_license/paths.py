"""Live receipt-store location (NSS home, not redirected HOME)."""

from __future__ import annotations

import os
from pathlib import Path
import pwd

from kilix_license.errors import LiveStoreForbidden


def nss_home() -> Path:
    return Path(pwd.getpwuid(os.getuid()).pw_dir)


def live_store_root() -> Path:
    return nss_home() / ".local" / "gpu_terminal"


def is_under_live_store(path: str | os.PathLike[str] | bytes) -> bool:
    text = os.fsdecode(path)
    if not text:
        return False
    expanded = os.path.abspath(os.path.expanduser(text))
    try:
        candidate = os.path.realpath(expanded)
    except OSError:
        candidate = os.path.normpath(expanded)
    live = os.path.realpath(os.path.abspath(str(live_store_root())))
    return candidate == live or candidate.startswith(live + os.sep)


def refuse_live_store(path: str | os.PathLike[str] | bytes) -> None:
    if is_under_live_store(path):
        raise LiveStoreForbidden(
            f"refusing live receipt store path {os.fsdecode(path)}"
        )
