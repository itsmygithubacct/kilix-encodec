"""Bounded real file/process controls for the local converter build and command."""
from __future__ import annotations

import ctypes
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import types
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import converter_build_io as build_io


def runtime_module():
    source = (ROOT / 'tools/converter_runtime.py').read_text()
    for key, value in {'@BUNDLE_SHA256@': '0' * 64, '@BUNDLE_BYTES@': '0',
                       '@RUNTIME_BYTES@': '0', '@OUTPUTS_JSON@': '{}',
                       '@NOTICE_HEX@': ''}.items():
        source = source.replace(key, value)
    module = types.ModuleType('converter_test_runtime')
    module.__file__ = str(ROOT / 'tools/converter_runtime.py')
    exec(compile(source, module.__file__, 'exec'), module.__dict__)
    return module


class ConverterTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix='.converter-test-', dir=Path.home()))
        self.fds = len(os.listdir('/proc/self/fd'))

    def tearDown(self):
        shutil.rmtree(self.root)
        self.assertEqual(len(os.listdir('/proc/self/fd')), self.fds)

    def test_directory_refuses_shared_and_symlink_ancestors(self):
        real = self.root / 'real'
        real.mkdir(mode=0o700)
        (self.root / 'alias').symlink_to(real, target_is_directory=True)
        with self.assertRaises(OSError):
            build_io.Directory(self.root / 'alias')
        real.chmod(0o770)
        with self.assertRaises(ValueError):
            build_io.Directory(real)
        real.chmod(0o700)
        with build_io.Directory(real, private=True) as held:
            real.rename(self.root / 'moved')
            real.mkdir(mode=0o700)
            with self.assertRaises(ValueError):
                held.check()

    def test_file_fifo_oversize_and_cancel_are_bounded(self):
        fifo = self.root / 'fifo'
        os.mkfifo(fifo, 0o600)
        started = time.monotonic()
        with self.assertRaises(ValueError):
            build_io.file_bytes(fifo, lambda: None)
        self.assertLess(time.monotonic() - started, .5)
        regular = self.root / 'regular'
        regular.write_bytes(b'abcd')
        with self.assertRaises(ValueError):
            build_io.file_bytes(regular, lambda: None, maximum=3)
        def canceled():
            raise InterruptedError('test cancellation')
        with mock.patch.object(os, 'open', wraps=os.open) as opened:
            with self.assertRaises(InterruptedError):
                build_io.file_bytes(regular, canceled)
            self.assertFalse(any(call.args[0] == 'regular' for call in opened.call_args_list))

    def test_file_growth_replacement_and_ancestor_move_refuse(self):
        for kind in ('growth', 'replacement', 'ancestor'):
            with self.subTest(kind=kind):
                directory = self.root / kind
                directory.mkdir(mode=0o700)
                source = directory / 'source'
                source.write_bytes(b'a' * (1024**2 + 7))
                original = os.pread
                changed = False
                def read(fd, size, offset):
                    nonlocal changed
                    data = original(fd, size, offset)
                    if not changed:
                        changed = True
                        if kind == 'growth':
                            with source.open('ab') as stream:
                                stream.write(b'x')
                        elif kind == 'replacement':
                            source.rename(directory / 'previous')
                            source.write_bytes(b'b')
                        else:
                            directory.rename(self.root / 'relocated')
                            directory.mkdir(mode=0o700)
                            source.write_bytes(b'outside sentinel')
                    return data
                with mock.patch.object(os, 'pread', side_effect=read):
                    with self.assertRaises(ValueError):
                        build_io.file_bytes(source, lambda: None)
                if kind == 'ancestor':
                    self.assertEqual(source.read_bytes(), b'outside sentinel')

    def test_runtime_snapshot_is_readonly_sealed_and_independent(self):
        runtime = runtime_module()
        source = self.root / 'input'
        data = b'original snapshot'
        source.write_bytes(data)
        fd = runtime.sealed(source, len(data), hashlib.sha256(data).hexdigest(), time.monotonic() + 2)
        try:
            self.assertEqual(fcntl.fcntl(fd, fcntl.F_GETFL) & os.O_ACCMODE, os.O_RDONLY)
            self.assertTrue(fcntl.fcntl(fd, fcntl.F_GETFD) & fcntl.FD_CLOEXEC)
            self.assertEqual(fcntl.fcntl(fd, fcntl.F_GET_SEALS) & 15, 15)
            source.write_bytes(b'changed source')
            self.assertEqual(os.read(fd, 1024), data)
            with self.assertRaises(OSError):
                os.pwrite(fd, b'x', 0)
        finally:
            os.close(fd)

    def test_runtime_snapshot_failure_closes_inputs(self):
        runtime = runtime_module()
        source = self.root / 'input'
        source.write_bytes(b'a')
        expected = hashlib.sha256(b'a').hexdigest()
        with mock.patch.object(os, 'memfd_create', side_effect=OSError('allocation refused')):
            with self.assertRaises(OSError):
                runtime.sealed(source, 1, expected, time.monotonic() + 2)
        source.unlink()
        os.mkfifo(source, 0o600)
        started = time.monotonic()
        with self.assertRaises(ValueError):
            runtime.sealed(source, 1, expected, time.monotonic() + 2)
        self.assertLess(time.monotonic() - started, .5)

    def test_runtime_cancel_during_snapshot_refuses(self):
        runtime = runtime_module()
        source = self.root / 'input'
        data = b'x' * (1024**2 + 1)
        source.write_bytes(data)
        original = os.pread
        def read(fd, size, offset):
            value = original(fd, size, offset)
            runtime.STOPPING = True
            return value
        with mock.patch.object(os, 'pread', side_effect=read):
            with self.assertRaises(InterruptedError):
                runtime.sealed(source, len(data), hashlib.sha256(data).hexdigest(), time.monotonic() + 2)

    def test_dedicated_build_reaps_escaped_children_on_all_endings(self):
        child = self.root / 'child.py'
        child.write_text('''import os,signal,sys,time
pid=os.fork()
if pid==0:
 os.setsid();signal.signal(signal.SIGTERM,signal.SIG_IGN)
 with open(sys.argv[1],"w") as stream:stream.write(str(os.getpid()))
 while True:time.sleep(.01)
while not os.path.exists(sys.argv[1]):time.sleep(.005)
if sys.argv[2]=="success":sys.exit(0)
while True:time.sleep(.01)
''')
        helper = self.root / 'helper.py'
        helper.write_text('''import ctypes,json,os,pathlib,signal,sys,time
sys.path.insert(0,sys.argv[1]);import converter_build_io as io
assert ctypes.CDLL(None).prctl(36,1,0,0,0)==0
signal.signal(signal.SIGTERM,io.stop)
ending=sys.argv[2];root=pathlib.Path(sys.argv[3]);started=time.monotonic()
deadline=started+(1.5 if ending=="success" else .3)
def check():
 if ending=="cancel" and time.monotonic()-started>=.15:io.STOPPING=True
 io.checkpoint(deadline)
error=None
try:io.run(["/usr/bin/python3",str(root/"child.py"),str(root/(ending+".pid")),ending],cwd=root,env={"PATH":"/usr/bin:/bin"},check=check)
except BaseException as caught:error=type(caught).__name__
print(json.dumps({"error":error,"seconds":time.monotonic()-started,"children":pathlib.Path(f"/proc/self/task/{os.getpid()}/children").read_text().split()}))
''')
        unrelated = subprocess.Popen(['/usr/bin/sleep', '15'])
        try:
            for ending in ('success', 'cancel', 'deadline'):
                with self.subTest(ending=ending):
                    result = subprocess.run(['/usr/bin/python3', str(helper), str(ROOT / 'tools'), ending, str(self.root)],
                                            text=True, capture_output=True, timeout=5)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    row = json.loads(result.stdout)
                    self.assertEqual(row['children'], [])
                    self.assertIsNone(unrelated.poll())
                    pid = int((self.root / (ending + '.pid')).read_text())
                    self.assertFalse(Path(f'/proc/{pid}').exists())
                    self.assertEqual(row['error'], {'success': None, 'cancel': 'InterruptedError', 'deadline': 'TimeoutError'}[ending])
                    self.assertLess(row['seconds'], 1)
        finally:
            unrelated.terminate()
            unrelated.wait()

    def test_build_offline_and_existing_output_refusals_preserve_entries(self):
        cache = self.root / 'cache'
        cache.mkdir(mode=0o700)
        for kind in ('offline', 'directory', 'dangling'):
            with self.subTest(kind=kind):
                target = self.root / kind
                target.mkdir(mode=0o700)
                if kind == 'directory':
                    (target / '.converter').mkdir(mode=0o700)
                    (target / '.converter/sentinel').write_bytes(b'preserved')
                elif kind == 'dangling':
                    (target / '.converter').symlink_to(self.root / 'never-created')
                result = subprocess.run(['/usr/bin/python3', str(ROOT / 'tools/build_converter.py'),
                    '--output-root', str(target), '--cache-dir', str(cache), '--offline', '--timeout', '2'],
                    text=True, capture_output=True, timeout=5)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(any(p.name.startswith('.converter-install-') for p in target.iterdir()))
                if kind == 'directory':
                    self.assertEqual((target / '.converter/sentinel').read_bytes(), b'preserved')
                elif kind == 'dangling':
                    self.assertTrue((target / '.converter').is_symlink())
                    self.assertFalse((self.root / 'never-created').exists())
                else:
                    self.assertEqual(list(target.iterdir()), [])

    def test_build_publication_detects_replacement_and_preserves_foreign_entries(self):
        helper = self.root / 'publication.py'
        helper.write_text('''import ctypes,json,os,pathlib,sys,time,types
sys.path.insert(0,sys.argv[1]);import build_converter as builder
assert ctypes.CDLL(None).prctl(36,1,0,0,0)==0
area=pathlib.Path(sys.argv[2]);kind=sys.argv[3];container=area/"container"
container.mkdir(mode=0o700);target=container/"target";target.mkdir(mode=0o700)
cache=area/"cache";cache.mkdir(mode=0o700)
def prepare(binding,cache,stage,offline):
 environment=stage.path/"environment";environment.mkdir(mode=0o700)
 python=stage.path/"python";python.mkdir(mode=0o700);(python/"bin").mkdir(mode=0o700)
 uv=stage.path/"uv";uv.mkdir(mode=0o700)
 return environment,python/"bin/python3.12",uv/"uv"
def package(environment,python,uv,output):
 (output/".converter").mkdir(mode=0o700);(output/".converter/runtime.tar").write_bytes(b"runtime")
 (output/"bin").mkdir(mode=0o700);(output/"bin/kilix-encodec-convert-24khz").write_bytes(b"command")
 if kind=="ancestor":
  container.rename(area/"relocated");container.mkdir(mode=0o700);target.mkdir(mode=0o700)
  (target/"sentinel").write_bytes(b"outside")
builder.prepare_environment=prepare;builder.build=package
rename=builder.build_io.rename_new
def publish(source_dir,source,target_dir,name):
 rename(source_dir,source,target_dir,name)
 if kind=="entry":
  os.rename(name,"original",src_dir_fd=target_dir,dst_dir_fd=target_dir)
  os.mkdir(name,0o700,dir_fd=target_dir)
  (target/name/"sentinel").write_bytes(b"outside")
builder.build_io.rename_new=publish
args=types.SimpleNamespace(output_root=target,cache_dir=cache,environment=None,offline=True)
error=None
try:builder.supervised_build(args,time.monotonic()+2)
except BaseException as caught:error=type(caught).__name__
print(json.dumps({"error":error,"children":pathlib.Path(f"/proc/self/task/{os.getpid()}/children").read_text().split()}))
''')
        for kind in ('success', 'ancestor', 'entry'):
            with self.subTest(kind=kind):
                area = self.root / kind
                area.mkdir(mode=0o700)
                result = subprocess.run(['/usr/bin/python3', str(helper), str(ROOT / 'tools'), str(area), kind],
                                        text=True, capture_output=True, timeout=5)
                self.assertEqual(result.returncode, 0, result.stderr)
                row = json.loads(result.stdout)
                self.assertEqual(row['children'], [])
                target = area / 'container/target'
                if kind == 'success':
                    self.assertIsNone(row['error'])
                    self.assertEqual((target / '.converter/runtime.tar').read_bytes(), b'runtime')
                    self.assertEqual((target / 'bin/kilix-encodec-convert-24khz').read_bytes(), b'command')
                else:
                    self.assertEqual(row['error'], 'ValueError')
                    sentinel = target / ('sentinel' if kind == 'ancestor' else '.converter/sentinel')
                    self.assertEqual(sentinel.read_bytes(), b'outside')
                    self.assertFalse((target / 'bin/kilix-encodec-convert-24khz').exists())
                original_target = area / 'relocated/target' if kind == 'ancestor' else target
                self.assertFalse(any(p.name.startswith('.converter-install-') for p in original_target.iterdir()))


if __name__ == '__main__':
    unittest.main()
