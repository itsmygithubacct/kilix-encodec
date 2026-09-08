#!/usr/bin/python3 -I
"""Template for the installed, local-only 24 kHz conversion command.

build_converter.py binds a complete runtime archive and the exact output
population into this executable. No download or license decision occurs here.
"""
from __future__ import annotations

import argparse
import ctypes
import fcntl
import hashlib
import json
import os
from pathlib import Path
import selectors
import signal
import stat
import subprocess
import sys
import time

BUNDLE_SHA256 = "@BUNDLE_SHA256@"
BUNDLE_BYTES = int("@BUNDLE_BYTES@")
RUNTIME_BYTES = int("@RUNTIME_BYTES@")
CHECKPOINT_BYTES = 93171529
CHECKPOINT_SHA256 = "d7cc33bcf1aad7f2dad9836f36431530744abeace3ca033005e3290ed4fa47bf"
OUTPUTS = json.loads('@OUTPUTS_JSON@')
NOTICE = bytes.fromhex('@NOTICE_HEX@')
STOPPING = False


def stop(_number, _frame):
    global STOPPING
    STOPPING = True


def check(deadline):
    if STOPPING:
        raise InterruptedError('conversion interrupted')
    if time.monotonic() >= deadline:
        raise TimeoutError('conversion deadline exceeded')


def directory(path):
    """Pin a no-follow, non-shared directory chain."""
    text = os.fspath(path)
    if not text.startswith('/') or text != os.path.normpath(text):
        raise ValueError('directory must be an absolute canonical path')
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        for part in text.split('/')[1:]:
            if not part:
                continue
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                            dir_fd=fd)
            os.close(fd)
            fd = child
            info = os.fstat(fd)
            if info.st_uid not in (0, os.geteuid()) or info.st_mode & 0o022:
                raise ValueError('directory chain is not private to its owners')
        return fd
    except BaseException:
        os.close(fd)
        raise


def sealed(path, size, expected, deadline):
    parent = directory(path.parent)
    source = writer = -1
    try:
        source = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
                         dir_fd=parent)
        info = os.fstat(source)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                or info.st_size != size or info.st_mode & 0o022):
            raise ValueError('input metadata differs from the pinned population')
        check(deadline)
        writer = os.memfd_create('encodec-converter-input', os.MFD_CLOEXEC | os.MFD_ALLOW_SEALING)
        os.fchmod(writer, 0o400)
        value = hashlib.sha256()
        offset = 0
        while offset < size:
            check(deadline)
            block = os.pread(source, min(1024**2, size - offset), offset)
            if not block:
                raise ValueError('input ended early')
            value.update(block)
            written = 0
            while written < len(block):
                count = os.pwrite(writer, block[written:], offset + written)
                if count <= 0:
                    raise OSError('snapshot write failed')
                written += count
            offset += len(block)
        if os.pread(source, 1, size) or value.hexdigest() != expected:
            raise ValueError('input bytes differ from the pinned population')
        fcntl.fcntl(writer, fcntl.F_ADD_SEALS, fcntl.F_SEAL_SEAL | fcntl.F_SEAL_SHRINK
                    | fcntl.F_SEAL_GROW | fcntl.F_SEAL_WRITE)
        check(deadline)
        return os.open(f'/proc/self/fd/{writer}', os.O_RDONLY | os.O_CLOEXEC)
    finally:
        for fd in (source, writer, parent):
            if fd >= 0:
                os.close(fd)


BOOTSTRAP = r'''
import ctypes,os,pathlib,resource,sys,tarfile
maximum=int(sys.argv[1])
for key,value in ((resource.RLIMIT_AS,6*1024**3),(resource.RLIMIT_CPU,600),
                  (resource.RLIMIT_FSIZE,2*1024**3),(resource.RLIMIT_NOFILE,128),
                  (resource.RLIMIT_CORE,0)):
 resource.setrlimit(key,(value,value))
libc=ctypes.CDLL(None,use_errno=True)
libc.mount.argtypes=[ctypes.c_char_p,ctypes.c_char_p,ctypes.c_char_p,ctypes.c_ulong,ctypes.c_void_p]
if libc.unshare(0x20000):raise OSError(ctypes.get_errno(),'runtime mount namespace')
if libc.mount(b'tmpfs',b'/runtime',b'tmpfs',6,f'size={maximum+256*1024**2},mode=0700'.encode()):
 raise OSError(ctypes.get_errno(),'runtime filesystem')
total=count=0
with tarfile.open('/bundle.tar','r:') as archive:
 for entry in archive:
  path=pathlib.PurePosixPath(entry.name)
  if (not entry.isfile() or path.is_absolute() or '..' in path.parts
      or not path.parts or entry.size<0 or entry.size>1024**3):
   raise ValueError('invalid runtime bundle member')
  total+=entry.size;count+=1
  if total>maximum or count>50000:raise ValueError('runtime bundle exceeds bound')
  destination=pathlib.Path('/runtime').joinpath(*path.parts)
  destination.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
  descriptor=os.open(destination,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
  try:
   with archive.extractfile(entry) as source,os.fdopen(descriptor,'wb',closefd=False) as output:
    left=entry.size
    while left:
     data=source.read(min(left,1024**2))
     if not data:raise ValueError('runtime bundle ended early')
     output.write(data);left-=len(data)
   os.fchmod(descriptor,0o700 if entry.mode&0o111 else 0o600)
  finally:os.close(descriptor)
if total!=maximum:raise ValueError('runtime bundle total differs')
if libc.mount(None,b'/runtime',None,4096|32|1|2|4|(1<<21),None):raise OSError(ctypes.get_errno(),'readonly runtime mount')
for capability in range(64):libc.prctl(24,capability,0,0,0)
class Header(ctypes.Structure):_fields_=[('version',ctypes.c_uint32),('pid',ctypes.c_int)]
class Data(ctypes.Structure):_fields_=[('effective',ctypes.c_uint32),('permitted',ctypes.c_uint32),('inheritable',ctypes.c_uint32)]
header=Header(0x20080522,0);data=(Data*2)()
if libc.capset(ctypes.byref(header),data):raise OSError(ctypes.get_errno(),'capability drop')
if libc.prctl(38,1,0,0,0):raise OSError(ctypes.get_errno(),'no-new-privileges')
command="import sys,runpy;sys.path.insert(0,'/runtime/source/tools');sys.argv=['/runtime/source/tools/export_24khz.py','--checkpoint','/input/encodec_24khz-d7cc33bc.th','--output-dir','/output'];runpy.run_path(sys.argv[0],run_name='__main__')"
os.execve('/runtime/python/bin/python3.12',['/runtime/python/bin/python3.12','-I','-B','-c',command],
 {'PATH':'/runtime/bin:/usr/bin:/bin','HOME':'/tmp','LANG':'C.UTF-8','LC_ALL':'C.UTF-8',
  'PYTHONDONTWRITEBYTECODE':'1','OMP_NUM_THREADS':'2','MKL_NUM_THREADS':'2',
  'OPENBLAS_NUM_THREADS':'2','HF_HUB_OFFLINE':'1','TRANSFORMERS_OFFLINE':'1'})
'''


def reap_owned():
    """This dedicated command, never an embedding process, is the subreaper."""
    children = Path(f'/proc/self/task/{os.getpid()}/children')
    while True:
        for value in children.read_text().split():
            try:
                os.kill(int(value), signal.SIGKILL)
            except ProcessLookupError:
                pass
        while True:
            try:
                pid, _status = os.waitpid(-1, os.WNOHANG)
            except ChildProcessError:
                return
            if pid == 0:
                break
        time.sleep(.005)


def run(input_path, output_path, timeout):
    if not 1 <= timeout <= 900:
        raise ValueError('timeout must be between 1 and 900 seconds')
    deadline = time.monotonic() + timeout
    if ctypes.CDLL(None, use_errno=True).prctl(36, 1, 0, 0, 0):
        raise OSError('could not establish owned process supervision')
    output = directory(output_path)
    descriptors = [output]
    child = None
    try:
        info = os.fstat(output)
        if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700 or os.listdir(output):
            raise ValueError('output must be an existing, private empty directory')
        bundle_path = Path(__file__).resolve().parent.parent / '.converter/runtime.tar'
        bundle = sealed(bundle_path, BUNDLE_BYTES, BUNDLE_SHA256, deadline)
        descriptors.append(bundle)
        checkpoint = sealed(input_path, CHECKPOINT_BYTES, CHECKPOINT_SHA256, deadline)
        descriptors.append(checkpoint)
        command = ['/usr/bin/bwrap', '--die-with-parent', '--unshare-all', '--new-session',
            '--ro-bind', '/usr', '/usr', '--ro-bind', '/lib', '/lib',
            '--ro-bind', '/lib64', '/lib64', '--ro-bind', '/etc/ld.so.cache', '/etc/ld.so.cache',
            '--proc', '/proc', '--dev', '/dev', '--tmpfs', '/tmp', '--dir', '/runtime',
            '--dir', '/input', '--dir', '/output', '--ro-bind-data', str(bundle), '/bundle.tar',
            '--ro-bind-data', str(checkpoint), '/input/encodec_24khz-d7cc33bc.th',
            '--bind', f'/proc/self/fd/{output}', '/output',
            '--cap-add', 'CAP_SYS_ADMIN', '--cap-add', 'CAP_SETPCAP',
            '--', '/usr/bin/python3', '-I', '-S', '-c', BOOTSTRAP, str(RUNTIME_BYTES)]
        child = subprocess.Popen(command, pass_fds=tuple(descriptors),
            env={'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8'}, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True)
        for fd in (bundle, checkpoint):
            os.close(fd)
            descriptors.remove(fd)
        tail = bytearray()
        with selectors.DefaultSelector() as selector:
            selector.register(child.stdout, selectors.EVENT_READ)
            while selector.get_map():
                check(deadline)
                for key, _events in selector.select(min(.05, max(0, deadline - time.monotonic()))):
                    data = os.read(key.fd, 65536)
                    if data:
                        tail.extend(data)
                        del tail[:-65536]
                    else:
                        selector.unregister(key.fileobj)
        status = child.wait(timeout=max(.001, deadline - time.monotonic()))
        reap_owned()
        check(deadline)
        if status:
            raise RuntimeError('runtime failed: ' + tail[-2000:].decode('utf-8', 'replace'))
        if set(os.listdir(output)) != set(OUTPUTS):
            raise ValueError('conversion output population differs')
        for name, expected in OUTPUTS.items():
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=output)
            try:
                info = os.fstat(fd)
                if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.geteuid()
                        or info.st_size != expected['bytes'] or stat.S_IMODE(info.st_mode) != 0o600):
                    raise ValueError('unsafe conversion output')
                value = hashlib.sha256()
                offset = 0
                while offset < info.st_size:
                    check(deadline)
                    block = os.pread(fd, min(1024**2, info.st_size - offset), offset)
                    if not block:
                        raise ValueError('conversion output ended early')
                    value.update(block)
                    offset += len(block)
                if value.hexdigest() != expected['sha256'] or os.pread(fd, 1, offset):
                    raise ValueError('conversion output bytes differ')
            finally:
                os.close(fd)
        os.mkdir('notices', 0o700, dir_fd=output)
        notices = os.open('notices', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=output)
        try:
            fd = os.open('NO-MODEL-GRANT-24KHZ.txt', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC,
                         0o600, dir_fd=notices)
            with os.fdopen(fd, 'wb') as target:
                target.write(NOTICE)
        finally:
            os.close(notices)
        check(deadline)
    finally:
        if child is not None:
            if child.poll() is None:
                child.kill()
            reap_owned()
            child.wait()
            child.stdout.close()
        for fd in descriptors:
            os.close(fd)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--timeout', type=int, default=900)
    arguments = parser.parse_args()
    os.umask(0o077)
    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    try:
        run(arguments.input, arguments.output, arguments.timeout)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        parser.exit(1, f'conversion refused: {error}\n')
    print('converted exact local 24 kHz graph population; no publication authorized')


if __name__ == '__main__':
    main()
