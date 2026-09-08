"""Private directory, bounded file and dedicated build-process helpers."""
from __future__ import annotations

import ctypes
import hashlib
import os
from pathlib import Path
import selectors
import signal
import stat
import subprocess
import time

STOPPING = False
DIRECTORY = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC


def stop(_signum, _frame):
    global STOPPING
    STOPPING = True


def checkpoint(deadline, *directories):
    if STOPPING:
        raise InterruptedError('converter build interrupted')
    if time.monotonic() >= deadline:
        raise TimeoutError('converter build deadline exceeded')
    for directory in directories:
        directory.check()


class Directory:
    """Keep every directory entry and descriptor bound through a build."""

    def __init__(self, path, *, create=False, private=False):
        self.path = Path(path)
        self.fds = []
        self.entries = []
        raw = os.fspath(self.path)
        if not raw.startswith('/') or raw != os.path.normpath(raw):
            raise ValueError('build directory must be absolute and canonical')
        try:
            parent = os.open('/', DIRECTORY)
            self.fds.append(parent)
            self.safe(os.fstat(parent))
            parts = self.path.parts[1:]
            for index, name in enumerate(parts):
                if create:
                    try:
                        os.mkdir(name, 0o700, dir_fd=parent)
                    except FileExistsError:
                        pass
                child = os.open(name, DIRECTORY, dir_fd=parent)
                self.fds.append(child)
                info = os.fstat(child)
                leaf_private = private and index == len(parts) - 1
                self.safe(info, private=leaf_private)
                self.entries.append((parent, name, child, info.st_dev, info.st_ino, leaf_private))
                parent = child
            self.fd = parent
            self.check()
        except BaseException:
            self.close()
            raise

    @staticmethod
    def safe(info, *, private=False):
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid not in (0, os.geteuid())
                or (info.st_mode & 0o022 and not (info.st_uid == 0 and info.st_mode & stat.S_ISVTX))
                or (private and (info.st_uid != os.geteuid() or info.st_mode & 0o077))):
            raise ValueError('build directory ownership or mode is unsafe')

    def check(self):
        for parent, name, child, device, inode, private in self.entries:
            found = os.stat(name, dir_fd=parent, follow_symlinks=False)
            held = os.fstat(child)
            self.safe(found, private=private)
            if (found.st_dev, found.st_ino) != (device, inode) or (held.st_dev, held.st_ino) != (device, inode):
                raise ValueError('build directory entry was replaced')

    def close(self):
        for descriptor in reversed(self.fds):
            os.close(descriptor)
        self.fds.clear()

    def __enter__(self):
        return self

    def __exit__(self, *_error):
        self.close()


def file_bytes(path, check, *, maximum=1024**3, expected=None):
    """Read only a bounded regular file through its pinned parent directory."""
    path = Path(path)
    with Directory(path.parent) as parent:
        check()
        descriptor = os.open(path.name, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW | os.O_CLOEXEC,
                             dir_fd=parent.fd)
        try:
            before = os.fstat(descriptor)
            if (not stat.S_ISREG(before.st_mode) or before.st_uid not in (0, os.geteuid())
                    or before.st_mode & 0o022 or not 0 <= before.st_size <= maximum):
                raise ValueError('build input is not an owned bounded regular file')
            data = bytearray()
            while len(data) < before.st_size:
                check()
                parent.check()
                block = os.pread(descriptor, min(1024**2, before.st_size - len(data)), len(data))
                if not block:
                    raise ValueError('build input ended early')
                data.extend(block)
            after = os.fstat(descriptor)
            visible = os.stat(path.name, dir_fd=parent.fd, follow_symlinks=False)
            if (os.pread(descriptor, 1, before.st_size)
                    or any(getattr(before, name) != getattr(after, name)
                           for name in ('st_dev', 'st_ino', 'st_size', 'st_mtime_ns', 'st_ctime_ns', 'st_mode', 'st_uid'))
                    or (visible.st_dev, visible.st_ino) != (before.st_dev, before.st_ino)):
                raise ValueError('build input changed while reading')
            check()
            parent.check()
            result = bytes(data)
            if expected is not None and hashlib.sha256(result).hexdigest() != expected:
                raise ValueError('build input digest differs')
            return result
        finally:
            os.close(descriptor)


def reap_owned():
    """The CLI alone is a subreaper; no embedding process calls this helper."""
    children = Path(f'/proc/self/task/{os.getpid()}/children')
    while True:
        for text in children.read_text().split():
            try:
                os.kill(int(text), signal.SIGKILL)
            except ProcessLookupError:
                pass
        while True:
            try:
                child, _status = os.waitpid(-1, os.WNOHANG)
            except ChildProcessError:
                return
            if child == 0:
                break
        time.sleep(.005)


def run(command, *, cwd, env, check, pass_fds=()):
    """Bound output and poll the whole-build budget while the owned child runs."""
    check()
    process = None
    output = bytearray()
    try:
        process = subprocess.Popen(command, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   start_new_session=True, pass_fds=pass_fds)
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            while selector.get_map():
                check()
                # A successful leader can leave an escaped child holding the
                # pipe open. Reap that owned tree before waiting for EOF.
                if process.poll() is not None:
                    reap_owned()
                for key, _events in selector.select(.05):
                    block = os.read(key.fd, 65536)
                    if not block:
                        selector.unregister(key.fileobj)
                    else:
                        output.extend(block)
                        del output[:-65536]
            while process.poll() is None:
                check()
                time.sleep(.01)
        code = process.returncode
        reap_owned()
        check()
        if code:
            raise RuntimeError(f'converter build command failed with status {code}: '
                               + output[-4000:].decode('utf-8', 'replace'))
        return output.decode('utf-8', 'replace')
    finally:
        if process is not None:
            if process.poll() is None:
                process.kill()
            reap_owned()
            if process.stdout is not None:
                process.stdout.close()


def rename_new(source_dir, source, target_dir, target):
    """Publish one new directory without replacing any existing entry."""
    libc = ctypes.CDLL(None, use_errno=True)
    operation = libc.renameat2
    operation.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    if operation(source_dir, os.fsencode(source), target_dir, os.fsencode(target), 1):
        raise OSError(ctypes.get_errno(), 'cannot publish a new converter directory')
