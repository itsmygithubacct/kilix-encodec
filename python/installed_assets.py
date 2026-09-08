"""Per-load packaged catalog and durable receipt admission for native EnCodec.

The native consumer still verifies every manifest/graph against its own compiled
hashes. A caller path selects storage; it never supplies catalog authority.
"""
from __future__ import annotations

from contextlib import contextmanager
import fcntl
import hashlib
import os
import stat
import time

from graph_population import PROFILES


class AdmissionError(RuntimeError):
    """A bounded, content-free model admission refusal."""


def _check(deadline, cancelled):
    if cancelled is not None and cancelled():
        raise AdmissionError("model admission canceled")
    if time.monotonic() >= deadline:
        raise AdmissionError("model admission deadline exceeded")


def _descriptor(asset, item, deadline, cancelled):
    name, size, digest = item
    descriptor = asset.duplicate(name)
    try:
        _check(deadline, cancelled)
        info = os.fstat(descriptor)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                or info.st_size != size or info.st_mode & 0o133
                or fcntl.fcntl(descriptor, fcntl.F_GETFL) & os.O_ACCMODE != os.O_RDONLY
                or not fcntl.fcntl(descriptor, fcntl.F_GETFD) & fcntl.FD_CLOEXEC
                or fcntl.fcntl(descriptor, 1034) & 15 != 15):
            raise AdmissionError("unsafe model snapshot descriptor")
        actual = hashlib.sha256()
        offset = 0
        while offset < size:
            _check(deadline, cancelled)
            data = os.pread(descriptor, min(1024*1024, size-offset), offset)
            if not data:
                raise AdmissionError("model snapshot ended early")
            actual.update(data)
            offset += len(data)
        if os.pread(descriptor, 1, offset) or actual.hexdigest() != digest:
            raise AdmissionError("model snapshot digest differs")
        _check(deadline, cancelled)
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


@contextmanager
def admitted_assets(profile: int, root: str, *, timeout_ms: int = 120000, cancelled=None):
    """Yield complete borrowed immutable FDs, closed when this context exits.

    No network, converter, receipt decision or alternative graph directory is
    used. The selected catalog population, receipts and installed bytes must
    already exist. Every call repeats current packaged authority and snapshot
    checks; there is no readiness cache.
    """
    if (type(profile) is not int or profile not in PROFILES
            or type(root) is not str or not root.startswith("/")
            or root != os.path.normpath(root) or "\0" in root
            or len(os.fsencode(root)) > 4095
            or type(timeout_ms) is not int or not 1 <= timeout_ms <= 120000
            or cancelled is not None and not callable(cancelled)):
        raise AdmissionError("invalid model admission request")
    deadline = time.monotonic() + timeout_ms/1000
    _check(deadline, cancelled)
    try:
        import kilix_content as content
        from kilix_content.receipt import ArtifactBinding
    except ImportError as error:
        raise AdmissionError("packaged content API is unavailable") from error
    if not hasattr(content.Installer, "open_asset"):
        raise AdmissionError("packaged content API is unavailable")
    asset_id, version, budget, population = PROFILES[profile]
    errors = (content.CatalogError, content.ReceiptError, content.InstallError)
    descriptors = []
    try:
        spec = content.verified_packaged_catalog().require_asset(asset_id)
        release = content.ReleaseContext.packaged()
        expected = {name: (size, digest) for name, size, digest in population}
        actual = {item.path: (item.bytes, item.sha256) for item in spec.files
                  if not item.path.startswith("notices/")}
        if (spec.asset_id != asset_id or spec.version != version
                or spec.stream != "F101" or spec.provider != "kilix-encodec"
                or spec.consumer_schema != "kilix.encodec.graphs/v1"
                or not spec.compatibility_minimum <= 1 <= spec.compatibility_maximum
                or actual != expected or spec.installed_bytes > budget):
            raise AdmissionError("installed graph identity differs from native consumer")
        binding = ArtifactBinding.from_spec(spec)
        _check(deadline, cancelled)
        with content.ReceiptStore.open_default() as store:
            installer = content.Installer(root)
            _check(deadline, cancelled)
            def interrupted():
                _check(deadline, cancelled)
                return False
            with installer.open_asset(spec, store, release, maximum_bytes=budget,
                    timeout=max(.001, deadline-time.monotonic()), cancelled=interrupted) as asset:
                if (type(asset) is not content.InstalledAsset or asset.binding != binding
                        or asset.release != release or asset.files != spec.files or not asset.receipts):
                    raise AdmissionError("unbound installed graph snapshot")
                for item in population:
                    descriptors.append((item[0], _descriptor(asset, item, deadline, cancelled)))
                _check(deadline, cancelled)
                yield tuple(descriptors)
    except errors as error:
        _check(deadline, cancelled)
        raise AdmissionError("installed graph authorization or bytes failed") from error
    finally:
        for _name, descriptor in descriptors:
            os.close(descriptor)
