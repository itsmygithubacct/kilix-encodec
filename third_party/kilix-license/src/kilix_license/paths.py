"""Where receipts live, and the live store this suite must never touch.

Two separate jobs, and they must not share a definition of "home"
(LIC6-VERIFY F1):

* ``receipt_store_root()`` is the **one** answer to "where does an acceptance
  receipt go?", for every producer and every consumer in the stack (OD-AJ).
  Its fallback composes **``$HOME``** via :func:`user_home`, because that is
  what every other component in the stack composes -- ``voicelib.paths``'
  ``gpu_terminal_home()`` (``os.path.expanduser("~")``), ``kilix/bootstrap.sh``,
  ``kilix/build.sh`` and ``pleb/lib/common.sh`` (``$HOME/.local/gpu_terminal``).
  A root that followed the NSS passwd home instead disagreed with all four
  wherever ``$HOME`` and ``pw_dir`` differ, and the symptom was V-ACC-VERIFY
  F7's: a receipt filed at one root and a gate refusing at another.
* ``live_store_root()`` / ``refuse_live_store()`` are the test-harness guard
  that keeps this repository's own suite out of the invoking user's store.
  That guard must **not** follow ``$HOME``: the suite redirects ``$HOME`` into
  a scratch directory, so a ``$HOME``-based guard would stop guarding the real
  store exactly when it is running. It reads the NSS passwd home, which the
  suite cannot redirect. ``is_under_live_store()`` refuses **both** spellings,
  so broadening the receipt root narrowed nothing the guard used to catch.
"""

from __future__ import annotations

import os
from pathlib import Path
import pwd

from kilix_license.errors import LiveStoreForbidden, ReceiptStoreRootRefused

# The stack's shared writable root. kilix-voice's voicelib.paths and
# kilix-content both already read this variable; it is the stack's spelling of
# "where our state lives", and the receipt store is stack state, not the
# private state of whichever component happens to write it.
STACK_HOME_ENV = "GPU_TERMINAL_HOME"
# The one override. A deployment that must put receipts elsewhere sets this,
# and every component sees the same answer because every component asks this
# function. A component that invents its own variable re-opens V-ACC F7.
RECEIPT_STORE_ENV = "KILIX_LICENSE_RECEIPTS"
# The directory name under the stack home. Consumers never spell it.
RECEIPT_STORE_LEAF = "license-receipts"
# The stack home's path under a user's home, as bootstrap.sh, build.sh,
# common.sh and voicelib all spell it: $HOME/.local/gpu_terminal.
STACK_HOME_DIRS = (".local", "gpu_terminal")


def nss_home() -> Path:
    """The NSS passwd home: ``pw_dir``, which ``$HOME`` cannot redirect.

    Only the live-store guard reads this. No receipt path reaches it.
    """
    return Path(pwd.getpwuid(os.getuid()).pw_dir)


def user_home() -> Path:
    """``$HOME``, made absolute, exactly as the rest of the stack reads it.

    ``os.path.expanduser("~")`` is byte-for-byte what
    ``voicelib.paths.gpu_terminal_home()`` reads, and falls back to ``pw_dir``
    itself when ``$HOME`` is unset, so the two agree in every environment
    where the stack's own convention agrees with itself.

    LIC6-FIX-VERIFY V2: voicelib puts that through
    ``os.path.abspath(os.path.expanduser(value))`` and this did not, so a
    **relative** ``$HOME`` composed a relative root. A relative root resolves
    against each process's working directory, so a writer and a reader that
    agreed on every variable still filed and looked in different places, and
    the gate refused after consent -- F1's symptom again, in the one
    environment F1's own fix introduced. ``$GPU_TERMINAL_HOME`` and
    ``$KILIX_LICENSE_RECEIPTS`` already go through :func:`_absolute`; this
    was the one path that skipped it. It now matches voicelib exactly, so
    the two sides compose the same string in that environment too.

    ``expanduser`` and not ``os.environ["HOME"]``: with ``$HOME`` unset the
    stack's convention is the passwd home and with it empty it is ``/``, and
    reading the variable raw composes a root under the current directory
    instead of either.
    """
    return Path(os.path.abspath(os.path.expanduser("~")))


def live_store_root() -> Path:
    """The live store the suite must never touch: the **NSS** home's.

    Deliberately not ``$HOME``-based; see the module docstring. This is the
    guard's canonical root and the one ``live_store_guard.live_root()``
    reports. ``is_under_live_store()`` refuses the ``$HOME`` spelling too.
    """
    return nss_home().joinpath(*STACK_HOME_DIRS)


def live_store_roots() -> tuple[Path, ...]:
    """Every spelling of the live store, NSS first, without duplicates.

    Both are refused, so making the receipt root follow ``$HOME`` did not
    open a path the guard used to close: where the two homes agree this is one
    root, and where they differ it is two.
    """
    roots = [live_store_root(), user_home().joinpath(*STACK_HOME_DIRS)]
    seen: list[Path] = []
    for root in roots:
        if root not in seen:
            seen.append(root)
    return tuple(seen)


def _absolute(value: str, label: str) -> Path:
    expanded = os.path.expanduser(value)
    if not os.path.isabs(expanded):
        raise ReceiptStoreRootRefused(
            f"{label} must be an absolute path, not {value!r}"
        )
    return Path(os.path.normpath(expanded))


def stack_home() -> Path:
    """The stack's shared writable root: ``$GPU_TERMINAL_HOME``.

    Falls back to ``$HOME/.local/gpu_terminal`` (LIC6-VERIFY F1). That is the
    composition every other component in the stack already performs --
    ``voicelib.paths.gpu_terminal_home()`` is ``os.path.expanduser("~")``,
    and ``kilix/bootstrap.sh``, ``kilix/build.sh`` and ``pleb/lib/common.sh``
    all read ``GPU_TERMINAL_HOME="${GPU_TERMINAL_HOME:-$HOME/.local/gpu_terminal}"``
    -- so this authority and its consumers now compose the same path with the
    variable set and with it unset. It read the NSS passwd home before, which
    ignores ``$HOME``; in a sandbox or a service unit where the two differ,
    consent was filed at one root and the gate refused at another.
    """
    value = os.environ.get(STACK_HOME_ENV)
    if value:
        return _absolute(value, STACK_HOME_ENV)
    return user_home().joinpath(*STACK_HOME_DIRS)


def receipt_store_root() -> Path:
    """The directory every acceptance receipt is written to and read from.

    **This is the agreement V-ACC-VERIFY F7 found missing.** The default is
    ``$GPU_TERMINAL_HOME/license-receipts``; ``$KILIX_LICENSE_RECEIPTS``
    overrides it outright. Producers (kilix-content's first-use flow) and
    consumers (the kilix-voice weights gate, the kilix installer, the EnCodec
    converter) must all obtain the path from here and must not compose one of
    their own -- a receipt filed anywhere else is invisible to everyone, and
    presents to the user as a gate that refuses an acceptance they just gave.
    """
    override = os.environ.get(RECEIPT_STORE_ENV)
    if override:
        return _absolute(override, RECEIPT_STORE_ENV)
    return stack_home() / RECEIPT_STORE_LEAF


def _normalised(path: str | os.PathLike[str] | bytes) -> str:
    text = os.fsdecode(path)
    return os.path.realpath(os.path.abspath(os.path.expanduser(text)))


def is_receipt_store_root(path: str | os.PathLike[str] | bytes) -> bool:
    """True when path names the directory receipt_store_root() names."""
    return _normalised(path) == _normalised(str(receipt_store_root()))


def check_receipt_store_root(path: str | os.PathLike[str] | bytes) -> Path:
    """Return path when it is the agreed receipt store root, or refuse.

    The refusal names both paths and the override, because the symptom of
    getting this wrong (V-ACC-VERIFY F7) is a gate that refuses an acceptance
    the user gave, and nothing in that symptom points at the real cause.
    """
    if not is_receipt_store_root(path):
        agreed = receipt_store_root()
        raise ReceiptStoreRootRefused(
            f"receipt store root {os.fsdecode(path)!r} is not the root this "
            f"authority publishes receipts to ({str(agreed)!r}). Every "
            "producer and consumer must take the path from "
            "kilix_license.receipt_store_root(); a receipt written anywhere "
            "else is found by nobody, and the licence gate refuses an "
            f"acceptance the user gave. Set ${RECEIPT_STORE_ENV} to move the "
            f"store, or ${STACK_HOME_ENV} to move the whole stack home."
        )
    return Path(os.fsdecode(path))


def is_under_live_store(path: str | os.PathLike[str] | bytes) -> bool:
    """True for a path under any spelling of the live store.

    Every root in :func:`live_store_roots` is refused, not only the NSS one,
    so making the receipt root follow ``$HOME`` did not create a real store
    the suite may write to.
    """
    text = os.fsdecode(path)
    if not text:
        return False
    expanded = os.path.abspath(os.path.expanduser(text))
    try:
        candidate = os.path.realpath(expanded)
    except OSError:
        candidate = os.path.normpath(expanded)
    for root in live_store_roots():
        live = os.path.realpath(os.path.abspath(str(root)))
        if candidate == live or candidate.startswith(live + os.sep):
            return True
    return False


def refuse_live_store(path: str | os.PathLike[str] | bytes) -> None:
    if is_under_live_store(path):
        raise LiveStoreForbidden(
            f"refusing live receipt store path {os.fsdecode(path)}"
        )
