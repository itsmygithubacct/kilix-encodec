"""Private one-request admission helper, bundled with its exact content package."""
from __future__ import annotations

import array
import ctypes
import os
import resource
import select
import signal
import socket
import struct
import sys

HEADER = struct.Struct("<4sBBHI")


def main():
    # Parent PID, ZIP fd3 and seqpacket fd4 are supplied only by the native
    # launcher. Resource ceilings precede importing the packaged authority.
    if len(sys.argv) != 2 or not sys.argv[1].isascii() or not sys.argv[1].isdigit():
        return 69
    parent = int(sys.argv[1])
    if parent <= 1 or os.getppid() != parent:
        return 69
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(1, signal.SIGKILL, 0, 0, 0) != 0 or os.getppid() != parent:
        return 69
    if libc.prctl(38, 1, 0, 0, 0) != 0:
        return 69
    os.set_inheritable(3, False)
    os.set_inheritable(4, False)
    for kind, ceiling in ((resource.RLIMIT_CORE, 0), (resource.RLIMIT_AS, 512*1024**2),
            (resource.RLIMIT_FSIZE, 256*1024**2), (resource.RLIMIT_NOFILE, 64),
            (resource.RLIMIT_CPU, 30)):
        _soft, hard = resource.getrlimit(kind)
        bound = ceiling if hard == resource.RLIM_INFINITY else min(ceiling, hard)
        resource.setrlimit(kind, (bound, bound))
    os.umask(0o077)
    channel = socket.socket(fileno=4)
    channel.settimeout(5)
    if channel.getsockopt(socket.SOL_SOCKET, socket.SO_TYPE) != socket.SOCK_SEQPACKET:
        return 69
    profile = 0
    try:
        payload, control, flags, _address = channel.recvmsg(HEADER.size+4096, socket.CMSG_SPACE(64*4))
        # Refuse all incoming FDs and close any delivered descriptors first.
        for level, kind, data in control:
            if level == socket.SOL_SOCKET and kind == socket.SCM_RIGHTS:
                incoming = array.array("i")
                incoming.frombytes(data[:len(data)-len(data)%incoming.itemsize])
                for fd in incoming: os.close(fd)
        if control or flags or len(payload) <= HEADER.size:
            return 69
        magic, profile, reserved, zero, timeout_ms = HEADER.unpack_from(payload)
        if magic != b"KCI1" or profile not in (1,2) or reserved or zero or not 1 <= timeout_ms <= 120000:
            return 69
        root = payload[HEADER.size:].decode("utf-8")
        if "\0" in root:
            return 69
        channel.settimeout(timeout_ms/1000)
        def cancelled():
            # EOF or any extra request invalidates this single-request exchange.
            return os.getppid() != parent or bool(select.select([channel], [], [], 0)[0])
        from installed_assets import admitted_assets
        with admitted_assets(profile, root, timeout_ms=timeout_ms, cancelled=cancelled) as files:
            fds = array.array("i", [fd for _name, fd in files])
            response = HEADER.pack(b"KCO1", profile, len(files), 0, 0)
            if channel.sendmsg([response], [(socket.SOL_SOCKET, socket.SCM_RIGHTS, fds)]) != len(response):
                return 69
        return 0
    except (OSError, ValueError, RuntimeError, MemoryError):
        # No paths, model/receipt metadata or exception text cross this boundary.
        try:
            channel.settimeout(.2)
            channel.send(HEADER.pack(b"KCO1", profile, 0, 0, 2))
        except OSError:
            pass
        return 69
    finally:
        channel.close()


if __name__ == "__main__":
    raise SystemExit(main())
