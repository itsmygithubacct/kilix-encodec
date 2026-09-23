"""Per-load installed admission for native EnCodec, against asset/v3 and kilix-license.

Three authorities answer "is this model installed, verified and licence-accepted?",
and each is asked on every load. There is no readiness cache.

* **The catalogue** is `kilix_content.verified_packaged_catalog()`: the packaged
  catalogue parsed from the one read of its bytes that matched the content
  component's pin. A caller path selects storage; it never supplies catalogue
  authority. The selected record must still equal this consumer's own compiled
  identity (`graph_population.PROFILES`) before anything else is opened.
* **The licence** is kilix-license: `kilix_license.coverage.require()` over the
  one shared store `kilix_license.receipt_store_root()` names, for the binding
  asset/v3 installs under -- the catalogue's `licenses[0]` record digest and the
  manifest digest. The record itself is this bundle's kilix-license record for
  the asset, so a catalogue that names another record is refused. The store is
  only read: this module never creates, repairs or writes a receipt.
* **The installed tree** is the directory `Installer.asset_destination()` names,
  and it must equal the manifest exactly: every declared file at its size and
  digest, nothing undeclared. asset/v3 has no installed-snapshot API (the F100
  `installed.py` was removed), so the snapshot is taken here, with the same
  discipline: directories are walked by descriptor with `O_NOFOLLOW`, each member
  is copied once into a sealed memory file while it is hashed, and the tree, the
  member identities and the covering receipt are all checked again afterwards.

No network, converter, receipt decision, download or alternative graph directory
is used. Only the native population's sealed descriptors leave this module; the
native loader then repeats its own compiled graph hashes.
"""
from __future__ import annotations

from contextlib import contextmanager
import errno
import fcntl
import hashlib
import os
from pathlib import Path, PurePosixPath
import stat
import time

from graph_population import PROFILES


class AdmissionError(RuntimeError):
    """A bounded, content-free model admission refusal."""


_DIRECTORY = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
_MEMBER = os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW | os.O_NOCTTY | os.O_CLOEXEC
_F_ADD_SEALS = getattr(fcntl, "F_ADD_SEALS", 1033)
_F_GET_SEALS = getattr(fcntl, "F_GET_SEALS", 1034)
_SEALS = 0x000F  # SEAL | SHRINK | GROW | WRITE
_MAX_MEMBERS = 256
_MAX_DEPTH = 32


def _check(deadline, cancelled):
    if cancelled is not None and cancelled():
        raise AdmissionError("model admission canceled")
    if time.monotonic() >= deadline:
        raise AdmissionError("model admission deadline exceeded")


def _descriptor(descriptor, item, deadline, cancelled):
    """Re-verify one sealed snapshot through the descriptor that will be returned."""
    _name, size, digest = item
    _check(deadline, cancelled)
    info = os.fstat(descriptor)
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
            or info.st_size != size or info.st_mode & 0o133
            or fcntl.fcntl(descriptor, fcntl.F_GETFL) & os.O_ACCMODE != os.O_RDONLY
            or not fcntl.fcntl(descriptor, fcntl.F_GETFD) & fcntl.FD_CLOEXEC
            or fcntl.fcntl(descriptor, _F_GET_SEALS) & _SEALS != _SEALS):
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


def _identity(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_uid,
            info.st_nlink, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _owned_directory(info):
    """The content root and everything below it belong to this user alone."""
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid()
            or info.st_mode & 0o022):
        raise AdmissionError("installed content has an unsafe directory")


class _Tree:
    """Directory descriptors from the content root down, and their links."""

    def __init__(self):
        self.descriptors = []
        self.links = []

    def root(self, path, check):
        """Walk to the content root by descriptor; the root itself is never a symlink.

        Ancestors of the root may belong to anyone (a user namespace shows root's
        directories as the overflow uid) but must not be writable by others unless
        sticky. A symlinked ancestor, such as a `~/.local` that points at another
        disk, is followed the way the installer followed it when it wrote the
        tree, but by this walk and not the kernel's: every directory the walk
        passes through, on either side of a link, must meet the same rule, a link
        in an other-writable (sticky) directory must belong to this user or to
        that directory's owner, and at most 40 links are followed. The root
        itself, and everything below it, is opened without following symlinks
        and must be this user's, private to its owner.
        """
        parts = PurePosixPath(path).parts
        if not parts or parts[0] != "/" or ".." in parts or len(parts) > 129:
            raise AdmissionError("installed content root is not canonical")
        pending = list(parts[1:-1])
        links = 0
        current = os.open("/", _DIRECTORY)
        try:
            while True:
                check()
                info = os.fstat(current)
                if info.st_mode & 0o022 and not info.st_mode & stat.S_ISVTX:
                    raise AdmissionError("installed content root has a shared ancestor")
                if not pending:
                    break
                name = pending.pop(0)
                if name == ".":
                    continue
                try:
                    child = os.open(name, _DIRECTORY, dir_fd=current)
                except OSError as error:
                    if error.errno not in (errno.ELOOP, errno.ENOTDIR):
                        raise
                    link = os.stat(name, dir_fd=current, follow_symlinks=False)
                    if not stat.S_ISLNK(link.st_mode):
                        raise
                    links += 1
                    if links > 40:
                        raise AdmissionError("installed content root has too many symbolic links") from None
                    if info.st_mode & 0o022 and link.st_uid not in (os.geteuid(), info.st_uid):
                        raise AdmissionError("installed content root has a foreign symbolic link") from None
                    target = PurePosixPath(os.readlink(name, dir_fd=current))
                    if target.is_absolute():
                        os.close(current)
                        current = -1
                        current = os.open("/", _DIRECTORY)
                        pending[:0] = target.parts[1:]
                    else:
                        pending[:0] = target.parts
                    continue
                os.close(current)
                current = child
            if len(parts) > 1:
                child = os.open(parts[-1], _DIRECTORY, dir_fd=current)
                os.close(current)
                current = child
        except BaseException:
            if current >= 0:
                os.close(current)
            raise
        self.descriptors.append(current)
        _owned_directory(os.fstat(current))
        return current

    def open(self, parent, name):
        descriptor = os.open(name, _DIRECTORY, dir_fd=parent)
        self.descriptors.append(descriptor)
        info = os.fstat(descriptor)
        _owned_directory(info)
        self.links.append((parent, name, descriptor, info.st_dev, info.st_ino))
        return descriptor

    def recheck(self):
        for parent, name, descriptor, device, inode in self.links:
            linked = os.stat(name, dir_fd=parent, follow_symlinks=False)
            pinned = os.fstat(descriptor)
            _owned_directory(linked)
            _owned_directory(pinned)
            if ((linked.st_dev, linked.st_ino) != (device, inode)
                    or (pinned.st_dev, pinned.st_ino) != (device, inode)):
                raise AdmissionError("installed asset directory identity changed")

    def close(self):
        for descriptor in reversed(self.descriptors):
            os.close(descriptor)
        self.descriptors = []


def _snapshot(parent, item, check, keep):
    """Hash one member once; return (sealed read-only copy or None, identity)."""
    source = os.open(PurePosixPath(item.path).name, _MEMBER, dir_fd=parent)
    writer = reader = -1
    try:
        before = os.fstat(source)
        if (not stat.S_ISREG(before.st_mode) or before.st_uid != os.geteuid()
                or before.st_nlink != 1 or before.st_mode & 0o133
                or before.st_size != item.bytes):
            raise AdmissionError("installed asset member metadata differs")
        if keep:
            writer = os.memfd_create("kilix-encodec-admitted", os.MFD_CLOEXEC | os.MFD_ALLOW_SEALING)
            os.fchmod(writer, 0o600)
        digest = hashlib.sha256()
        remaining = item.bytes
        while remaining:
            check()
            data = os.read(source, min(remaining, 1024 * 1024))
            if not data:
                raise AdmissionError("installed asset member ended early")
            remaining -= len(data)
            digest.update(data)
            pending = memoryview(data)
            while keep and pending:
                written = os.write(writer, pending)
                if written <= 0:
                    raise AdmissionError("installed asset member could not be copied")
                pending = pending[written:]
        check()
        if os.read(source, 1) or _identity(os.fstat(source)) != _identity(before):
            raise AdmissionError("installed asset member changed while it was read")
        if digest.hexdigest() != item.sha256:
            raise AdmissionError("installed asset member digest differs")
        if not keep:
            return None, _identity(before)
        fcntl.fcntl(writer, _F_ADD_SEALS, _SEALS)
        reader = os.open(f"/proc/self/fd/{writer}", os.O_RDONLY | os.O_CLOEXEC)
        if ((os.fstat(reader).st_dev, os.fstat(reader).st_ino)
                != (os.fstat(writer).st_dev, os.fstat(writer).st_ino)):
            raise AdmissionError("installed asset snapshot identity differs")
        result, reader = reader, -1
        return result, _identity(before)
    finally:
        os.close(source)
        for descriptor in (writer, reader):
            if descriptor >= 0:
                os.close(descriptor)


def _layout(spec):
    """Directory -> {entry: is_directory} for exactly the manifest's files."""
    expected = {"": {}}
    for item in spec.files:
        parts = PurePosixPath(item.path).parts
        if not parts or len(parts) > _MAX_DEPTH or ".." in parts:
            raise AdmissionError("installed asset manifest path is unsafe")
        for index, name in enumerate(parts):
            parent = "/".join(parts[:index])
            directory = index != len(parts) - 1
            members = expected.setdefault(parent, {})
            if members.get(name, directory) != directory:
                raise AdmissionError("installed asset manifest paths conflict")
            members[name] = directory
    return expected


def _population_matches(tree_dirs, expected, check):
    for relative, descriptor in tree_dirs.items():
        seen = set()
        with os.scandir(descriptor) as entries:
            for entry in entries:
                check()
                if entry.name not in expected[relative]:
                    raise AdmissionError("installed asset has an undeclared member")
                info = entry.stat(follow_symlinks=False)
                directory = expected[relative][entry.name]
                if (directory and not stat.S_ISDIR(info.st_mode)) or (
                        not directory and not stat.S_ISREG(info.st_mode)):
                    raise AdmissionError("installed asset member has the wrong type")
                seen.add(entry.name)
        if seen != expected[relative].keys():
            raise AdmissionError("installed asset is missing a declared member")


class _Authority:
    """The asset/v3 and kilix-license names this admission uses, and nothing else."""

    def __init__(self):
        try:
            # kilix_license first: kilix_content resolves it through its own
            # package path, so the bundle's copy is the one it gets.
            import kilix_license
            from kilix_license import coverage, errors, records, store
            import kilix_content
        except ImportError as error:
            raise AdmissionError("packaged content API is unavailable") from error
        for owner, names in ((kilix_content, ("verified_packaged_catalog", "Installer",
                                               "CatalogError", "InstallError")),
                             (kilix_license, ("receipt_store_root",)),
                             (coverage, ("AssetRef", "require")),
                             (records, ("LicenseRecord", "RecordIndex")),
                             (store, ("ReceiptStore",)),
                             (errors, ("LicenseError",))):
            if not all(hasattr(owner, name) for name in names):
                raise AdmissionError("packaged content API is unavailable")
        if not hasattr(kilix_content.Installer, "asset_destination"):
            raise AdmissionError("packaged content API is unavailable")
        self.content, self.license = kilix_content, kilix_license
        self.coverage, self.records, self.store = coverage, records, store
        # The catalogue check raises RuntimeError, a missing record KeyError.
        self.errors = (kilix_content.CatalogError, kilix_content.InstallError,
                       errors.LicenseError, KeyError, ValueError, RuntimeError)

        class ReadOnlyStore(store.ReceiptStore):
            # kilix-license's own lookup over a pinned directory. Admission
            # never creates or changes the store; only a licence screen writes it.
            def __init__(self, root):
                self.root = Path(root)

            def write(self, *_arguments, **_options):
                raise AdmissionError("admission never writes a receipt")

        self.ReadOnlyStore = ReadOnlyStore

    def record(self, asset_id):
        from importlib.resources import files
        data = files("kilix_license").joinpath("data", "records", asset_id + ".json").read_bytes()
        return self.records.LicenseRecord.from_bytes(data)

    def receipts(self):
        """The shared receipt store, opened once, read-only, private to this user.

        The path is resolved by the kernel, a symlinked store directory included,
        as kilix-license resolves it when it files a receipt. What binds is the
        directory actually opened: it must be this user's and private to them,
        and every later read goes through that descriptor.
        """
        root = os.fspath(self.license.receipt_store_root())
        descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
        try:
            info = os.fstat(descriptor)
            if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & 0o077:
                raise AdmissionError("licence receipt store is not private to this user")
        except BaseException:
            os.close(descriptor)
            raise
        return descriptor


@contextmanager
def admitted_assets(profile: int, root: str, *, timeout_ms: int = 120000, cancelled=None):
    """Yield complete borrowed immutable FDs, closed when this context exits.

    The selected catalogue population, a covering receipt and the installed
    bytes must already exist. Every call repeats all three checks.
    """
    if (type(profile) is not int or profile not in PROFILES
            or type(root) is not str or not root.startswith("/")
            or root != os.path.normpath(root) or "\0" in root
            or len(os.fsencode(root)) > 4095
            or type(timeout_ms) is not int or not 1 <= timeout_ms <= 120000
            or cancelled is not None and not callable(cancelled)):
        raise AdmissionError("invalid model admission request")
    deadline = time.monotonic() + timeout_ms/1000

    def check():
        _check(deadline, cancelled)

    check()
    authority = _Authority()
    asset_id, version, budget, population = PROFILES[profile]
    descriptors = {}
    tree = _Tree()
    held_store = -1
    try:
        spec = authority.content.verified_packaged_catalog().require_asset(asset_id)
        expected = {name: (size, digest) for name, size, digest in population}
        actual = {item.path: (item.bytes, item.sha256) for item in spec.files
                  if not item.path.startswith("notices/")}
        if (spec.asset_id != asset_id or spec.version != version
                or spec.stream != "F101" or spec.provider != "kilix-encodec"
                or spec.consumer_schema != "kilix.encodec.graphs/v1"
                or not spec.compatibility_minimum <= 1 <= spec.compatibility_maximum
                or actual != expected or spec.installed_bytes > budget
                or len(spec.files) > _MAX_MEMBERS):
            raise AdmissionError("installed graph identity differs from native consumer")
        # asset/v3 binds licenses[0] alone. A second row would be shown and never
        # covered by any receipt, so this consumer admits exactly one.
        record = authority.record(asset_id)
        if (len(spec.licenses) != 1 or record.id != asset_id
                or spec.licenses[0].record_digest != record.digest):
            raise AdmissionError("installed graph licence record differs from native consumer")
        records = authority.records.RecordIndex([record])
        binding = authority.coverage.AssetRef(id=asset_id, record_digest=record.digest,
                                              manifest_digest=spec.manifest_digest)
        check()
        held_store = authority.receipts()
        store = authority.ReadOnlyStore(f"/proc/self/fd/{held_store}")
        covering = authority.coverage.require(binding, records=records, store=store)
        check()
        opened = {"": tree.root(root, check)}
        destination = authority.content.Installer(root).asset_destination(spec)
        relative = PurePosixPath(os.path.relpath(destination, root))
        if (relative.is_absolute() or not relative.parts or ".." in relative.parts
                or os.path.join(root, *relative.parts) != destination):
            raise AdmissionError("installed asset destination is outside its root")
        parent = opened[""]
        for name in relative.parts:
            check()
            parent = tree.open(parent, name)
        layout = _layout(spec)
        asset_dirs = {"": parent}
        for directory in sorted(layout, key=lambda value: (value.count("/"), value)):
            check()
            if directory:
                path = PurePosixPath(directory)
                above = "" if str(path.parent) == "." else str(path.parent)
                asset_dirs[directory] = tree.open(asset_dirs[above], path.name)
        tree.recheck()
        _population_matches(asset_dirs, layout, check)
        wanted = {name for name, _size, _digest in population}
        identities = {}
        for item in spec.files:
            above = str(PurePosixPath(item.path).parent)
            directory = asset_dirs["" if above == "." else above]
            snapshot, identity = _snapshot(directory, item, check, item.path in wanted)
            if snapshot is not None:
                descriptors[item.path] = snapshot
            identities[item.path] = (directory, identity)
        tree.recheck()
        _population_matches(asset_dirs, layout, check)
        for path, (directory, identity) in identities.items():
            check()
            if _identity(os.stat(PurePosixPath(path).name, dir_fd=directory,
                                 follow_symlinks=False)) != identity:
                raise AdmissionError("installed asset changed during admission")
        # The receipt is required again after the bytes were read: a receipt
        # withdrawn while the snapshot was taken admits nothing.
        if authority.coverage.require(binding, records=records, store=store) != covering:
            raise AdmissionError("licence receipt changed during admission")
        ordered = []
        for item in population:
            _descriptor(descriptors[item[0]], item, deadline, cancelled)
            ordered.append((item[0], descriptors[item[0]]))
        check()
    except BaseException as error:
        for descriptor in descriptors.values():
            os.close(descriptor)
        descriptors.clear()
        if isinstance(error, AdmissionError) or not isinstance(error, (*authority.errors, OSError)):
            raise
        check()
        raise AdmissionError("installed graph authorization or bytes failed") from error
    finally:
        tree.close()
        if held_store >= 0:
            os.close(held_store)
    try:
        yield tuple(ordered)
    finally:
        for descriptor in descriptors.values():
            os.close(descriptor)
