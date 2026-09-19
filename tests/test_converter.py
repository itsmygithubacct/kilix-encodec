"""Bounded real file/process controls for the local converter build and command."""
from __future__ import annotations

import contextlib
import ctypes
import fcntl
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import signal
import stat
import struct
import subprocess
import sys
import tarfile
import tempfile
import time
import types
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import converter_build_io as build_io
import build_converter as builder


def binding():
    return json.loads((ROOT / 'tools/converter-inputs.json').read_text())


def rendered_command(profile='24khz'):
    """The command template with the real pinned authority and profile."""
    bound = binding()
    selected = builder.select_profile(bound, profile)
    authority = builder.authority_payload(bound, selected)
    source = (ROOT / 'tools/converter_runtime.py').read_text()
    for key, value in {'@BUNDLE_SHA256@': '0' * 64, '@BUNDLE_BYTES@': '0',
                       '@RUNTIME_BYTES@': '0', '@OUTPUTS_JSON@': '{}',
                       '@PROFILE_HEX@': json.dumps(builder.profile_payload(selected, profile)).encode().hex(),
                       '@AUTHORITY_HEX@': json.dumps(authority).encode().hex()}.items():
        source = source.replace(key, value)
    return source


def runtime_module(profile='24khz'):
    module = types.ModuleType('converter_test_runtime')
    module.__file__ = str(ROOT / 'tools/converter_runtime.py')
    exec(compile(rendered_command(profile), module.__file__, 'exec'), module.__dict__)
    return module


class Watch:
    """inotify over paths: records every open, read, write or entry change.

    Events are queued inside the syscall that causes them, so once a child
    process has exited its events are all readable here.
    """

    MASK = 0x1 | 0x2 | 0x4 | 0x20 | 0x40 | 0x80 | 0x100 | 0x200  # access modify attrib open moved create delete

    def __init__(self, paths):
        self.libc = ctypes.CDLL(None, use_errno=True)
        self.fd = self.libc.inotify_init1(os.O_NONBLOCK | os.O_CLOEXEC)
        if self.fd < 0:
            raise OSError(ctypes.get_errno(), 'inotify_init1')
        self.paths = {}
        for path in paths:
            descriptor = self.libc.inotify_add_watch(self.fd, os.fsencode(path), self.MASK)
            if descriptor < 0:
                error = ctypes.get_errno()
                os.close(self.fd)
                raise OSError(error, f'inotify_add_watch {path}')
            self.paths[descriptor] = str(path)

    def events(self):
        data = bytearray()
        while True:
            try:
                chunk = os.read(self.fd, 65536)
            except BlockingIOError:
                break
            if not chunk:
                break
            data += chunk
        found, offset = [], 0
        while offset < len(data):
            descriptor, mask, _cookie, length = struct.unpack_from('iIII', data, offset)
            name = bytes(data[offset + 16:offset + 16 + length]).rstrip(b'\0').decode()
            found.append((self.paths.get(descriptor, descriptor), hex(mask), name))
            offset += 16 + length
        return found

    def close(self):
        os.close(self.fd)


def vendored_authority():
    """The vendored kilix-license, imported from disk to write test receipts."""
    path = str(ROOT / 'third_party/kilix-license/src')
    sys.dont_write_bytecode = True
    if path not in sys.path:
        sys.path.insert(0, path)
    import kilix_license
    return kilix_license


class ConverterTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix='.converter-test-', dir=Path.home()))
        self.fds = len(os.listdir('/proc/self/fd'))

    def tearDown(self):
        shutil.rmtree(self.root)
        self.assertEqual(len(os.listdir('/proc/self/fd')), self.fds)

    def packaging_fixture(self, name, profile='24khz'):
        """Real frozen source/notice/authority bytes; tiny inert tool/environment fixtures."""
        area = self.root / name
        area.mkdir(mode=0o700)
        source = area / 'source'
        source.mkdir(mode=0o700)
        binding = json.loads((ROOT / 'tools/converter-inputs.json').read_text())
        selected = binding['profiles'][profile]
        notices = [*binding['uv_notices'], 'encodec-LICENSE-MIT']
        paths = [*binding['source_files'], *selected['source_files'], 'tools/converter_runtime.py',
                 *binding['licence_authority']['files'],
                 *('tools/converter-notices/' + item for item in notices)]
        for relative in paths:
            destination = source / relative
            destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            destination.write_bytes((ROOT / relative).read_bytes())
        python = area / 'python/bin/python3.12'
        uv = area / 'uv/uv'
        for label, path in (('python', python), ('uv', uv)):
            path.parent.mkdir(parents=True, mode=0o700)
            path.write_bytes((label + ' inert packaging fixture\n').encode())
            path.chmod(0o700)
            binding[label + '_binary_sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
        (area / 'python/lib').mkdir(mode=0o700)
        environment = area / 'environment'
        (environment / 'bin').mkdir(parents=True, mode=0o700)
        (environment / 'bin/python').symlink_to(python)
        (environment / 'lib/python3.12/site-packages').mkdir(parents=True, mode=0o700)
        binding['runtime_packages'] = []
        (source / 'tools/converter-inputs.json').write_text(json.dumps(binding))
        output = area / 'output'
        output.mkdir(mode=0o700)
        members = {'source/' + relative: (source / relative, expected)
                   for relative, expected in {**binding['source_files'], **selected['source_files']}.items()}
        members.update({'python/bin/python3.12': (python, binding['python_binary_sha256']),
                        'bin/uv': (uv, binding['uv_binary_sha256'])})
        members.update({'notices/' + relative: (source / 'tools/converter-notices' / relative,
                                                expected['sha256'])
                        for relative, expected in binding['uv_notices'].items()})
        members['notices/encodec-LICENSE-MIT'] = (
            source / 'tools/converter-notices/encodec-LICENSE-MIT',
            binding['encodec_source']['license_sha256'])
        members['notices/kilix-license-LICENSE'] = (
            source / 'third_party/kilix-license/LICENSE',
            binding['licence_authority']['files']['third_party/kilix-license/LICENSE'])
        return source, environment, python, uv, output, members

    def test_packaging_refuses_pypi_encodec_toolchain(self):
        source, environment, python, uv, output, _members = self.packaging_fixture('pypi-pin')
        binding = json.loads((source / 'tools/converter-inputs.json').read_text())
        binding['toolchain']['encodec'] = '0.1.1'
        (source / 'tools/converter-inputs.json').write_text(json.dumps(binding))
        with mock.patch.object(builder, 'ROOT', source), \
             contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(ValueError, 'pinned MIT encodec'):
                builder.build(environment, python, uv, output)
        self.assertEqual(list(output.iterdir()), [])

    def test_packaging_binds_actual_source_tools_and_notices(self):
        source, environment, python, uv, output, members = self.packaging_fixture('unchanged')
        with mock.patch.object(builder, 'ROOT', source), \
             contextlib.redirect_stdout(io.StringIO()):
            builder.build(environment, python, uv, output)
        receipt = json.loads((output / '.converter/receipt.json').read_text())
        with tarfile.open(output / '.converter/runtime.tar') as archive:
            for name, (path, expected) in members.items():
                payload = archive.extractfile(name).read()
                self.assertEqual(payload, path.read_bytes())
                self.assertEqual(hashlib.sha256(payload).hexdigest(), expected)
                self.assertEqual(receipt['runtime_files'][name]['sha256'], expected)
                if name.startswith('source/'):
                    self.assertEqual(receipt['source_files'][name.removeprefix('source/')], expected)
        self.assertTrue((output / 'bin/kilix-encodec-convert-24khz').is_file())

    def test_packaging_refuses_same_size_mutation_after_frozen_identity_checks(self):
        # Each actual packaging read follows the initial frozen-identity check.
        # In particular the exporter edit is the original one-byte docstring
        # mutation; no selected interpreter, exporter or model is executed.
        initial = self.packaging_fixture('population')[-1]
        for index, name in enumerate(initial):
            with self.subTest(member=name):
                source, environment, python, uv, output, members = self.packaging_fixture(str(index))
                watched, expected = members[name]
                original = watched.read_bytes()
                offset = 0
                if name == 'source/tools/export_24khz.py':
                    offset = original.index(b'"""') + 3
                    while original[offset:offset + 1] in (b'\n', b' ', b'\r'):
                        offset += 1
                replacement = b'x' if original[offset:offset + 1] != b'x' else b'y'
                changed = original[:offset] + replacement + original[offset + 1:]
                self.assertEqual(len(changed), len(original))
                self.assertNotEqual(hashlib.sha256(changed).hexdigest(), expected)
                if name == 'source/tools/export_24khz.py':
                    compile(changed, str(watched), 'exec')
                read = build_io.file_bytes
                mutated = False
                def mutate(path, check, **kwargs):
                    nonlocal mutated
                    data = read(path, check, **kwargs)
                    # The first licence-authority read follows every add().
                    if Path(path) == source / 'third_party/kilix-license.pin':
                        watched.write_bytes(changed)
                        mutated = True
                    return data
                with mock.patch.object(builder, 'ROOT', source), \
                     mock.patch.object(build_io, 'file_bytes', side_effect=mutate), \
                     contextlib.redirect_stdout(io.StringIO()):
                    with self.assertRaisesRegex(ValueError, 'digest differs'):
                        builder.build(environment, python, uv, output)
                self.assertTrue(mutated)
                self.assertEqual(watched.read_bytes(), changed)
                self.assertEqual(list(output.iterdir()), [])

    def test_packaging_uses_verified_bytes_even_if_path_changes_before_tar_write(self):
        source, environment, python, uv, output, members = self.packaging_fixture('after-read')
        name = 'source/tools/export_24khz.py'
        watched, expected = members[name]
        original = watched.read_bytes()
        addfile = tarfile.TarFile.addfile
        changed = False
        def mutate(archive, member, fileobj=None):
            nonlocal changed
            if member.name == name:
                watched.write_bytes(b'x' + original[1:])
                changed = True
            return addfile(archive, member, fileobj)
        with mock.patch.object(builder, 'ROOT', source), \
             mock.patch.object(tarfile.TarFile, 'addfile', mutate), \
             contextlib.redirect_stdout(io.StringIO()):
            builder.build(environment, python, uv, output)
        self.assertTrue(changed)
        with tarfile.open(output / '.converter/runtime.tar') as archive:
            self.assertEqual(archive.extractfile(name).read(), original)
        receipt = json.loads((output / '.converter/receipt.json').read_text())
        self.assertEqual(receipt['runtime_files'][name]['sha256'], expected)
        self.assertEqual(receipt['source_files']['tools/export_24khz.py'], expected)

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

    def populated_fixture(self, name):
        fixture = self.packaging_fixture(name)
        source, environment = fixture[:2]
        binding = json.loads((source / 'tools/converter-inputs.json').read_text())
        binding['runtime_packages'] = json.loads((ROOT / 'tools/converter-inputs.json').read_text())['runtime_packages']
        packages = environment / 'lib/python3.12/site-packages'
        metadata = []
        for package, version in binding['runtime_packages']:
            directory = packages / (package + '-' + version + '.dist-info')
            directory.mkdir(mode=0o700)
            path = directory / 'METADATA'
            path.write_text('Metadata-Version: 2.1\nName: ' + package + '\nVersion: ' + version + '\n')
            metadata.append(path)
        (source / 'tools/converter-inputs.json').write_text(json.dumps(binding))
        return fixture, metadata

    def test_packaging_refuses_late_same_size_package_version_change(self):
        fixture, metadata = self.populated_fixture('metadata-version')
        source, environment, python, uv, output, _members = fixture
        watched = metadata[0]
        original = watched.read_bytes()
        changed = original.replace(b'2026.7.22', b'2025.7.22')
        self.assertEqual(len(changed), len(original))
        self.assertNotEqual(changed, original)
        open_tar = tarfile.open
        def mutate(*args, **kwargs):
            watched.write_bytes(changed)
            return open_tar(*args, **kwargs)
        with mock.patch.object(builder, 'ROOT', source), \
             mock.patch.object(tarfile, 'open', mutate), contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(ValueError, 'digest differs'):
                builder.build(environment, python, uv, output)
        self.assertEqual(list(output.iterdir()), [])

    def test_packaging_refuses_added_or_removed_metadata_after_enumeration(self):
        for mode in ('added', 'removed'):
            with self.subTest(mode=mode):
                fixture, metadata = self.populated_fixture('metadata-' + mode)
                source, environment, python, uv, output, _members = fixture
                packages = environment / 'lib/python3.12/site-packages'
                original_rglob = Path.rglob
                def mutate(path, *args, **kwargs):
                    if path == packages:
                        if mode == 'removed':
                            metadata[0].unlink()
                        else:
                            extra = packages / 'additional-1.dist-info'
                            extra.mkdir(mode=0o700)
                            (extra / 'METADATA').write_bytes(b'Name: additional\nVersion: 1\n')
                    return original_rglob(path, *args, **kwargs)
                with mock.patch.object(builder, 'ROOT', source), \
                     mock.patch.object(Path, 'rglob', mutate), contextlib.redirect_stdout(io.StringIO()):
                    with self.assertRaisesRegex(ValueError, 'metadata population changed'):
                        builder.build(environment, python, uv, output)
                self.assertEqual(list(output.iterdir()), [])

    def test_packaging_retains_validated_metadata_after_its_read(self):
        fixture, metadata = self.populated_fixture('metadata-retained')
        source, environment, python, uv, output, _members = fixture
        watched = metadata[0]
        original = watched.read_bytes()
        name = 'python/lib/python3.12/site-packages/' + watched.relative_to(environment/'lib/python3.12/site-packages').as_posix()
        addfile = tarfile.TarFile.addfile
        def mutate(archive, member, fileobj=None):
            if member.name == name:
                watched.write_bytes(original.replace(b'2026.7.22', b'2025.7.22'))
            return addfile(archive, member, fileobj)
        with mock.patch.object(builder, 'ROOT', source), \
             mock.patch.object(tarfile.TarFile, 'addfile', mutate), contextlib.redirect_stdout(io.StringIO()):
            builder.build(environment, python, uv, output)
        with tarfile.open(output / '.converter/runtime.tar') as archive:
            self.assertEqual(archive.extractfile(name).read(), original)
        receipt = json.loads((output / '.converter/receipt.json').read_text())
        self.assertIn(['certifi', '2026.7.22'], receipt['packages'])
        self.assertEqual(receipt['runtime_files'][name]['sha256'], hashlib.sha256(original).hexdigest())

    def test_template_receipt_binds_the_exact_compiled_snapshot(self):
        source, environment, python, uv, output, _members = self.packaging_fixture('template-read')
        path = source / 'tools/converter_runtime.py'
        original = path.read_bytes()
        changed = original.replace(b'Template for the installed', b'Template for the altered__', 1)
        self.assertNotEqual(changed, original)
        read = build_io.file_bytes
        consumed = []
        def mutate(selected, check, **kwargs):
            if Path(selected) == path and not consumed:
                path.write_bytes(changed)
                data = read(selected, check, **kwargs)
                consumed.append(data)
                path.write_bytes(original)
                return data
            return read(selected, check, **kwargs)
        with mock.patch.object(builder, 'ROOT', source), \
             mock.patch.object(build_io, 'file_bytes', mutate), contextlib.redirect_stdout(io.StringIO()):
            builder.build(environment, python, uv, output)
        receipt = json.loads((output / '.converter/receipt.json').read_text())
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(consumed, [changed])
        self.assertEqual(receipt['template_sha256'], hashlib.sha256(changed).hexdigest())
        self.assertNotEqual(receipt['template_sha256'], hashlib.sha256(original).hexdigest())
        command = (output / 'bin/kilix-encodec-convert-24khz').read_bytes()
        self.assertIn(b'Template for the altered__', command)
        self.assertEqual(receipt['command_sha256'], hashlib.sha256(command).hexdigest())

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
def package(environment,python,uv,output,profile):
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
args=types.SimpleNamespace(output_root=target,cache_dir=cache,environment=None,offline=True,profile="24khz")
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

    # --- R4-067: kilix-license receipt gate, profiles, retired terms code ---

    def receipt_store(self, name):
        store = self.root / name
        store.mkdir(mode=0o700)
        return store

    def write_receipt(self, store, profile, manifest_digest, **changes):
        """A real kilix-license receipt, optionally planted with changed fields."""
        authority = vendored_authority()
        record = authority.load_determined_records().by_id(binding()['profiles'][profile]['record']['id'])
        agreement = authority.capture_agreement(record, authority.typed_agreement_line(record))
        receipt = authority.receipt_from_agreement(record, agreement, manifest_digest=manifest_digest,
                                                   release_digest='1' * 64, catalogue_digest='2' * 64)
        path = authority.ReceiptStore(store).write(receipt)
        if changes:
            raw = json.loads(path.read_text())
            raw.update(changes)
            path.write_text(json.dumps(raw, sort_keys=True) + '\n')
        return receipt, path

    def gated_run(self, runtime, store, manifest_digest):
        """Run the command body; the input and output paths do not exist."""
        with mock.patch.object(runtime, 'sealed', side_effect=AssertionError('input read')) as sealed:
            try:
                runtime.run(self.root / 'absent-input', self.root / 'absent-output', 5, store, manifest_digest)
            finally:
                self.assertEqual(sealed.call_count, 0)
                self.assertFalse((self.root / 'absent-output').exists())

    def test_binding_pins_vendored_authority_records_and_native_populations(self):
        bound = binding()
        authority = vendored_authority()
        pin = (ROOT / 'third_party/kilix-license.pin').read_text()
        self.assertEqual(pin, bound['licence_authority']['ref'] + '\n')
        for name, expected in bound['licence_authority']['files'].items():
            self.assertEqual(hashlib.sha256((ROOT / name).read_bytes()).hexdigest(), expected, name)
        records = authority.load_determined_records()
        population = {}
        exec(compile((ROOT / 'python/graph_population.py').read_text(), 'graph_population', 'exec'), population)
        for profile, number in (('24khz', 1), ('48khz', 2)):
            selected = builder.select_profile(bound, profile)
            record = records.by_id(selected['record']['id'])
            self.assertEqual(record.digest, selected['record']['digest'])
            self.assertEqual(record.licence_ids, ('CC BY-NC 4.0',))
            self.assertEqual(record.licensor, 'Meta Platforms')
            self.assertEqual(record.expected_decision, 'accept')
            files = population['PROFILES'][number][3]
            self.assertEqual({name: {'bytes': size, 'sha256': digest} for name, size, digest in files},
                             selected['outputs'])
            for pinned in selected['inputs'].values():
                self.assertTrue(pinned['url'].startswith(('https://dl.fbaipublicfiles.com/encodec/v0/',
                    'https://huggingface.co/facebook/encodec_48khz/resolve/'
                    'c3def8e7185ac8c8efdce6eb8c4a651e487a503e/')))

    def test_converter_carries_no_terms_text_of_its_own(self):
        self.assertFalse((ROOT / 'tools/NO-MODEL-GRANT-24KHZ.txt').exists())
        self.assertNotIn('notice_sha256', binding())
        for profile in builder.PROFILES:
            command = rendered_command(profile)
            self.assertNotIn('NO-MODEL-GRANT', command)
            self.assertNotIn('notices', command)
            self.assertIn('require_receipt(receipt_store, manifest_digest)', command)

    def test_gate_refuses_without_a_receipt_before_touching_any_input(self):
        for profile in builder.PROFILES:
            with self.subTest(profile=profile):
                runtime = runtime_module(profile)
                store = self.receipt_store('empty-' + profile)
                with self.assertRaisesRegex(runtime.LicenceRefused, r'CoverageRefused: .*field receipt'):
                    self.gated_run(runtime, store, '3' * 64)
                self.assertEqual(list(store.iterdir()), [])

    def test_gate_refuses_a_planted_receipt_with_a_changed_licence_text_digest(self):
        for profile in builder.PROFILES:
            with self.subTest(profile=profile):
                runtime = runtime_module(profile)
                store = self.receipt_store('text-' + profile)
                receipt, path = self.write_receipt(store, profile, '4' * 64)
                changed = ('0' if receipt.licence_text_digest[0] != '0' else '1') + receipt.licence_text_digest[1:]
                self.write_receipt(store, profile, '4' * 64, licence_text_digest=changed)
                self.assertEqual(json.loads(path.read_text())['licence_text_digest'], changed)
                with self.assertRaisesRegex(runtime.LicenceRefused, 'licence_text_digest'):
                    self.gated_run(runtime, store, '4' * 64)

    def test_gate_refuses_other_bindings_and_unsafe_stores(self):
        runtime = runtime_module('24khz')
        store = self.receipt_store('bindings')
        receipt, path = self.write_receipt(store, '24khz', '5' * 64)
        with self.assertRaisesRegex(runtime.LicenceRefused, 'field manifest_digest'):
            self.gated_run(runtime, store, '6' * 64)
        for field, value in (('decision', 'record'), ('licensor', 'Someone Else'),
                             ('binding_condition_text_digests', {}),
                             ('record_digest', '7' * 64)):
            with self.subTest(field=field):
                self.write_receipt(store, '24khz', '5' * 64, **{field: value})
                with self.assertRaisesRegex(runtime.LicenceRefused, 'no kilix-license receipt covers'):
                    self.gated_run(runtime, store, '5' * 64)
        # A receipt for the other EnCodec record does not cover this one.
        other = self.receipt_store('other-record')
        self.write_receipt(other, '48khz', '5' * 64)
        with self.assertRaisesRegex(runtime.LicenceRefused, 'field receipt'):
            self.gated_run(runtime, other, '5' * 64)
        for digest in ('5' * 63, '5' * 63 + 'A', '../' + '5' * 61):
            with self.assertRaisesRegex(runtime.LicenceRefused, 'manifest digest'):
                self.gated_run(runtime, store, digest)
        store.chmod(0o750)
        try:
            with self.assertRaisesRegex(runtime.LicenceRefused, 'private directory'):
                self.gated_run(runtime, store, '5' * 64)
        finally:
            store.chmod(0o700)
        with self.assertRaisesRegex(runtime.LicenceRefused, 'receipt store is not a readable private directory'):
            self.gated_run(runtime, self.root / 'missing-store', '5' * 64)
        self.assertFalse((self.root / 'missing-store').exists())

    def test_gate_admits_a_covering_receipt_and_leaves_the_store_unchanged(self):
        for profile in builder.PROFILES:
            with self.subTest(profile=profile):
                runtime = runtime_module(profile)
                store = self.receipt_store('covered-' + profile)
                receipt, path = self.write_receipt(store, profile, '8' * 64)
                before = {item.name: item.read_bytes() for item in store.iterdir()}
                admitted = runtime.require_receipt(store, '8' * 64)
                self.assertEqual(admitted.digest, receipt.digest)
                self.assertEqual({item.name: item.read_bytes() for item in store.iterdir()}, before)
                self.assertEqual(stat.S_IMODE(store.stat().st_mode), 0o700)
                # Past the gate, run() reaches its own output and input checks.
                output = self.root / ('occupied-' + profile)
                output.mkdir(mode=0o700)
                (output / 'member').write_bytes(b'x')
                libc = mock.MagicMock()
                libc.CDLL.return_value.prctl.return_value = 0
                with mock.patch.object(runtime, 'ctypes', libc):
                    with self.assertRaisesRegex(ValueError, 'private empty directory'):
                        runtime.run(self.root / 'absent-input', output, 5, store, '8' * 64)

    def test_gate_sets_aside_and_restores_an_imported_kilix_license(self):
        authority = vendored_authority()
        runtime = runtime_module('48khz')
        embedded = runtime.licence_authority()
        self.assertIs(sys.modules['kilix_license'], authority)
        self.assertIsNot(embedded, authority)
        self.assertNotIn(str(ROOT), getattr(embedded, '__file__', ''))
        self.assertEqual(runtime.licence_record(embedded).digest,
                         binding()['profiles']['48khz']['record']['digest'])

    def test_command_entry_refuses_without_receipt_and_on_changed_text(self):
        for profile in builder.PROFILES:
            with self.subTest(profile=profile):
                command = self.root / ('command-' + profile)
                command.write_text(rendered_command(profile))
                store = self.receipt_store('entry-' + profile)
                arguments = ['/usr/bin/python3', '-I', str(command), '--input', str(self.root / 'input'),
                             '--output', str(self.root / 'output'), '--receipt-store', str(store),
                             '--manifest-digest', '9' * 64]
                result = subprocess.run(arguments, text=True, capture_output=True, timeout=30)
                self.assertEqual(result.returncode, 1)
                self.assertIn('conversion refused: no kilix-license receipt covers', result.stderr)
                self.assertIn('field receipt', result.stderr)
                receipt, _path = self.write_receipt(store, profile, '9' * 64)
                changed = ('0' if receipt.licence_text_digest[0] != '0' else '1') + receipt.licence_text_digest[1:]
                self.write_receipt(store, profile, '9' * 64, licence_text_digest=changed)
                result = subprocess.run(arguments, text=True, capture_output=True, timeout=30)
                self.assertEqual(result.returncode, 1)
                self.assertIn('licence_text_digest', result.stderr)
                self.assertFalse((self.root / 'output').exists())

    NO_STORE = 'no kilix-license receipt store was given'

    def test_gate_refuses_an_absent_or_empty_store_before_touching_any_input(self):
        for profile in builder.PROFILES:
            runtime = runtime_module(profile)
            for store in (None, '', Path('')):
                with self.subTest(profile=profile, store=store):
                    expected = self.NO_STORE if store in (None, '') else 'receipt store is not a readable'
                    with self.assertRaisesRegex(runtime.LicenceRefused, expected):
                        self.gated_run(runtime, store, '3' * 64)
                    with self.assertRaisesRegex(runtime.LicenceRefused, expected):
                        runtime.require_receipt(store, '3' * 64)

    def test_command_entry_refuses_an_absent_or_empty_store_before_touching_any_input(self):
        # The real command, run as a program over real paths: the input, a
        # private empty output directory and a runtime directory all exist,
        # so only the gate stands between the command and a conversion.
        for profile in builder.PROFILES:
            selected = binding()['profiles'][profile]
            for case, extra in (('removed', []), ('empty', ['--receipt-store', ''])):
                with self.subTest(profile=profile, case=case):
                    area = self.root / (profile + '-' + case)
                    (area / 'bin').mkdir(parents=True, mode=0o700)
                    command = area / 'bin' / selected['command']
                    command.write_text(rendered_command(profile))
                    runtime = area / selected['directory']
                    runtime.mkdir(mode=0o700)
                    (runtime / 'runtime.tar').write_bytes(b'')
                    inputs = area / 'input'
                    inputs.mkdir(mode=0o700)
                    for name in selected['inputs']:
                        (inputs / name).write_bytes(b'upstream input stand-in')
                    given = inputs / next(iter(selected['inputs'])) if selected['input'] == 'file' else inputs
                    output = area / 'output'
                    output.mkdir(mode=0o700)
                    watched = [inputs, *(inputs / name for name in selected['inputs']),
                               output, runtime, runtime / 'runtime.tar']
                    watch = Watch(watched)
                    try:
                        result = subprocess.run(
                            ['/usr/bin/python3', '-I', str(command), '--input', str(given),
                             '--output', str(output), *extra, '--manifest-digest', '9' * 64],
                            text=True, capture_output=True, timeout=30)
                        touched = watch.events()
                        # Control: the watch does see an open of each kind of path.
                        for path in (given, output, runtime / 'runtime.tar'):
                            if path.is_dir():
                                os.close(os.open(path, os.O_RDONLY | os.O_DIRECTORY))
                            else:
                                path.read_bytes()
                        seen = {path for path, _mask, _name in watch.events()}
                    finally:
                        watch.close()
                    self.assertEqual(result.returncode, 1, result.stderr)
                    self.assertIn('conversion refused: ' + self.NO_STORE, result.stderr)
                    self.assertEqual(result.stdout, '')
                    self.assertEqual(touched, [])
                    self.assertLessEqual({str(output), str(runtime / 'runtime.tar')}, seen)
                    self.assertTrue(str(given) in seen or str(inputs) in seen)
                    self.assertEqual(list(output.iterdir()), [])

    def test_packaging_48khz_binds_frame_sources_record_and_command(self):
        source, environment, python, uv, output, members = self.packaging_fixture('frame', '48khz')
        with mock.patch.object(builder, 'ROOT', source), \
             contextlib.redirect_stdout(io.StringIO()):
            builder.build(environment, python, uv, output, '48khz')
        receipt = json.loads((output / '.converter-48khz/receipt.json').read_text())
        self.assertEqual(receipt['profile'], '48khz')
        self.assertEqual(receipt['licence_authority']['record']['id'], 'encodec-48khz-frame')
        self.assertEqual(receipt['outputs'], binding()['profiles']['48khz']['outputs'])
        with tarfile.open(output / '.converter-48khz/runtime.tar') as archive:
            names = set(archive.getnames())
            for name, (path, expected) in members.items():
                self.assertEqual(hashlib.sha256(archive.extractfile(name).read()).hexdigest(), expected)
        self.assertIn('source/tools/export_48khz.py', names)
        self.assertIn('source/tools/verify_48khz.py', names)
        self.assertFalse(any('NO-MODEL-GRANT' in name for name in names))
        command = (output / 'bin/kilix-encodec-convert-48khz').read_text()
        self.assertIn(json.dumps(builder.profile_payload(binding()['profiles']['48khz'], '48khz'),
                                 sort_keys=True, separators=(',', ':')).encode().hex(), command)
        self.assertFalse((output / '.converter').exists())

    def test_packaging_refuses_changed_vendored_authority_or_pin(self):
        for case in ('module', 'record', 'pin'):
            with self.subTest(case=case):
                source, environment, python, uv, output, _members = self.packaging_fixture('authority-' + case)
                target = {'module': 'third_party/kilix-license/src/kilix_license/coverage.py',
                          'record': 'third_party/kilix-license/src/kilix_license/data/records/'
                                    'encodec-24khz-stateful.json',
                          'pin': 'tools/converter-inputs.json'}[case]
                if case == 'pin':
                    bound = json.loads((source / target).read_text())
                    bound['licence_authority']['ref'] = '0' * 40
                    (source / target).write_text(json.dumps(bound))
                    expected = 'differs from its pin'
                else:
                    original = (source / target).read_bytes()
                    (source / target).write_bytes(original.replace(b'"', b"'", 1) if case == 'module'
                                                  else original.replace(b'Meta Platforms', b'Meta Platformz', 1))
                    expected = 'digest differs'
                with mock.patch.object(builder, 'ROOT', source), \
                     contextlib.redirect_stdout(io.StringIO()):
                    with self.assertRaisesRegex(ValueError, expected):
                        builder.build(environment, python, uv, output)
                self.assertEqual(list(output.iterdir()), [])

    def test_profiles_bind_upstream_inputs_only(self):
        for profile in builder.PROFILES:
            with self.subTest(profile=profile):
                bound = binding()
                bound['profiles'][profile]['inputs'] = {
                    name: {**pinned, 'url': 'file:///supplied/' + name}
                    for name, pinned in bound['profiles'][profile]['inputs'].items()}
                with self.assertRaisesRegex(ValueError, 'pinned upstream download'):
                    builder.select_profile(bound, profile)
        with self.assertRaisesRegex(ValueError, 'unsupported'):
            builder.select_profile(binding(), 'supplied')


if __name__ == '__main__':
    unittest.main()
