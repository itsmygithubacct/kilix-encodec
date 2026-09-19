#!/usr/bin/env python3
"""Build a relocatable local converter using pinned tools and the export lock.

By default this creates a fresh CPU environment. An explicit development
environment may be selected with all three path options. Both routes bind
actual runtime bytes; neither includes or acquires a checkpoint or graph.
Each build makes one conversion profile's command (24 kHz stateful graphs or
48 kHz frame graphs), which refuses to run without a kilix-license receipt.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
import ctypes
import email.parser
import hashlib
import io
import json
import math
import os
from pathlib import Path
import shutil
import signal
import stat
import sys
import tarfile
import time
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parent))
import converter_build_io as build_io

ROOT = Path(__file__).resolve().parents[1]
MAXIMUM_BYTES = 2 * 1024**3
MAXIMUM_FILES = 50000
PROFILES = ('24khz', '48khz')
BINDING_SCHEMA = 'kilix.encodec.converter-inputs/v2'
AUTHORITY_PACKAGE = 'third_party/kilix-license/src/kilix_license/'
AUTHORITY_RECORDS = AUTHORITY_PACKAGE + 'data/records/'
AUTHORITY_LICENSE = 'third_party/kilix-license/LICENSE'
AUTHORITY_PIN = 'third_party/kilix-license.pin'


CHECK = lambda: None


def digest(path):
    return hashlib.sha256(build_io.file_bytes(path, CHECK, maximum=MAXIMUM_BYTES + 64 * 1024**2)).hexdigest()


def owned(path):
    value = path.absolute()
    if value != value.resolve():
        raise ValueError('build input must have a canonical directory chain')
    for parent in [value, *value.parents]:
        info = parent.stat()
        if info.st_uid not in (0, os.geteuid()) or info.st_mode & 0o022:
            raise ValueError('build input or ancestor is shared')
    return value


def profile_names(profile):
    """Converter directory and command name; 24 kHz keeps its 0.2.1 names."""
    if profile not in PROFILES:
        raise ValueError('conversion profile is unsupported')
    return ('.converter' if profile == '24khz' else '.converter-' + profile,
            'kilix-encodec-convert-' + profile)


def select_profile(binding, profile):
    """Return one profile's bound command, inputs, outputs and licence record."""
    if binding.get('schema') != BINDING_SCHEMA:
        raise ValueError('converter binding schema differs')
    directory, command = profile_names(profile)
    selected = binding.get('profiles', {}).get(profile)
    if not isinstance(selected, dict):
        raise ValueError('conversion profile is not bound')
    if (selected.get('command') != command or selected.get('directory') != directory
            or selected.get('input') not in ('file', 'directory')
            or not selected.get('inputs') or not selected.get('outputs')):
        raise ValueError('conversion profile binding is incomplete')
    for name, pinned in selected['inputs'].items():
        if '/' in name or not str(pinned.get('url', '')).startswith('https://'):
            raise ValueError('conversion input must be one pinned upstream download')
    record = selected.get('record', {})
    if record.get('path') != AUTHORITY_RECORDS + record.get('id', '') + '.json':
        raise ValueError('conversion profile licence record is not the pinned authority record')
    return selected


def authority_payload(binding, selected):
    """Embed the pinned kilix-license modules and the profile's licence record."""
    authority = binding.get('licence_authority', {})
    files = authority.get('files', {})
    for required in (AUTHORITY_PIN, AUTHORITY_LICENSE, selected['record']['path']):
        if required not in files:
            raise ValueError('licence authority binding is incomplete')
    payload = {}
    for name, expected in files.items():
        payload[name] = build_io.file_bytes(ROOT / name, CHECK, maximum=1024**2, expected=expected)
    if payload[AUTHORITY_PIN] != (authority.get('ref', '') + '\n').encode():
        raise ValueError('vendored licence authority differs from its pin')
    modules = {}
    for name, data in sorted(payload.items()):
        if name.startswith(AUTHORITY_PACKAGE) and name.endswith('.py') and name.count('/') == 4:
            module = 'kilix_license' + ('' if name.endswith('/__init__.py')
                                        else '.' + Path(name).stem)
            modules[module] = ['/kilix-license/src/kilix_license/' + Path(name).name, data.decode()]
    if 'kilix_license' not in modules or 'kilix_license.coverage' not in modules:
        raise ValueError('licence authority modules are incomplete')
    return {'modules': modules, 'record': payload[selected['record']['path']].decode(),
            'ref': authority['ref']}


def profile_payload(selected, profile):
    """The profile facts the generated command needs, nothing else."""
    return {'directory': selected['directory'], 'entry': selected['entry'],
            'input': selected['input'], 'inputs': selected['inputs'],
            'label': selected['label'], 'profile': profile,
            'record': {'digest': selected['record']['digest'], 'id': selected['record']['id']}}


def build(environment, python, uv, output_root, profile='24khz'):
    environment = owned(environment)
    python = owned(python)
    uv = owned(uv)
    output_root = owned(output_root)
    binding = json.loads(build_io.file_bytes(ROOT / 'tools/converter-inputs.json', CHECK, maximum=262144))
    selected = select_profile(binding, profile)
    pin = binding.get('encodec_source')
    expected_commit = '2d29d9353c2ff0ab1aeadc6a3d439854ee77da3e'
    expected_url = ('https://github.com/facebookresearch/encodec/archive/'
                    '2d29d9353c2ff0ab1aeadc6a3d439854ee77da3e.tar.gz')
    expected_archive = '47d071d2d90f3d107ed83c930c4c189a066f92d299371ff04b6ede573c7cf434'
    expected_license = 'cf9b17822d1fcd4ff32ccbe14183386fb3adf6f2ff92dc184130823f7fc28173'
    if (not isinstance(pin, dict) or pin.get('commit') != expected_commit
            or pin.get('url') != expected_url or pin.get('sha256') != expected_archive
            or pin.get('license_sha256') != expected_license or pin.get('version') != '0.1.2a3'):
        raise ValueError('converter encodec pin differs from the MIT source')
    if binding.get('toolchain', {}).get('encodec') != '0.1.2a3':
        raise ValueError('converter toolchain is not the pinned MIT encodec')
    if ['encodec', '0.1.1'] in binding.get('runtime_packages', []):
        raise ValueError('converter still selects PyPI encodec 0.1.1')
    for name, expected in {**binding['source_files'], **selected['source_files']}.items():
        if digest(ROOT / name) != expected:
            raise ValueError('export source or lock differs from the native population')
    pyproject = build_io.file_bytes(ROOT / 'pyproject.toml', CHECK, maximum=2 * 1024**2)
    lock = build_io.file_bytes(ROOT / 'uv.lock', CHECK, maximum=2 * 1024**2)
    if expected_url.encode() not in pyproject or b'encodec==0.1.1' in pyproject:
        raise ValueError('export project still selects PyPI encodec 0.1.1')
    if expected_url.encode() not in lock or expected_archive.encode() not in lock:
        raise ValueError('frozen lock is not the pinned MIT encodec source')
    if b'encodec-0.1.1.tar.gz' in lock:
        raise ValueError('frozen lock still selects PyPI encodec 0.1.1')
    if digest(python) != binding['python_binary_sha256'] or digest(uv) != binding['uv_binary_sha256']:
        raise ValueError('export toolchain executable identity differs')
    python_root = python.parent.parent
    interpreter = environment / 'bin/python'
    if interpreter.resolve(strict=True) != python:
        raise ValueError('environment uses a different interpreter')
    # Read metadata without executing caller-selected interpreter/site code.
    distributions = []
    packages = environment / 'lib/python3.12/site-packages'
    metadata_hashes = {}
    for metadata_path in packages.glob('*.dist-info/METADATA'):
        payload = build_io.file_bytes(metadata_path, CHECK, maximum=2 * 1024**2)
        metadata = email.parser.BytesParser().parsebytes(payload)
        if len(metadata.get_all('Name', [])) != 1 or len(metadata.get_all('Version', [])) != 1:
            raise ValueError('dependency identity metadata is ambiguous')
        distributions.append([metadata['Name'].lower().replace('_', '-'), metadata['Version']])
        metadata_hashes[metadata_path.relative_to(packages).as_posix()] = hashlib.sha256(payload).hexdigest()
    if sorted(distributions) != binding['runtime_packages']:
        raise ValueError('export environment package population differs from the frozen lock')
    if ['encodec', '0.1.1'] in distributions:
        raise ValueError('export environment installed PyPI encodec 0.1.1')
    evidence = {'packages': sorted(distributions), 'toolchain': binding['toolchain']}
    converter = output_root / selected['directory']
    executable = output_root / 'bin' / selected['command']
    if os.path.lexists(converter) or os.path.lexists(executable):
        raise ValueError('refusing any existing converter output')
    entries = {}

    def add(name, path, boundary, mode=None, *, expected=None):
        CHECK()
        target = path.resolve(strict=True)
        if not target.is_relative_to(boundary):
            raise ValueError('runtime file escapes its selected source')
        info = target.stat()
        if (not stat.S_ISREG(info.st_mode) or info.st_uid not in (0, os.geteuid())
                or info.st_mode & 0o022 or info.st_size > 1024**3 or name in entries):
            raise ValueError('unsafe or duplicate runtime file')
        entries[name] = (target, info.st_size,
                         mode or (0o700 if info.st_mode & 0o111 else 0o600), expected)
        if len(entries) > MAXIMUM_FILES:
            raise ValueError('runtime file count exceeds bound')

    add('python/bin/python3.12', python, python_root, 0o700,
        expected=binding['python_binary_sha256'])
    traversed = 0
    for path in (python_root / 'lib').rglob('*'):
        CHECK()
        traversed += 1
        if traversed > 2 * MAXIMUM_FILES:
            raise ValueError('runtime traversal exceeds bound')
        relative = path.relative_to(python_root)
        if any(part in ('__pycache__', 'site-packages') for part in relative.parts) or path.suffix == '.pyc':
            continue
        if path.is_file():
            add('python/' + relative.as_posix(), path, python_root)
        elif path.is_symlink():
            raise ValueError('runtime directory symlink is unsupported')
    archived_metadata = set()
    for path in packages.rglob('*'):
        CHECK()
        traversed += 1
        if traversed > 2 * MAXIMUM_FILES:
            raise ValueError('runtime traversal exceeds bound')
        relative = path.relative_to(packages)
        if '__pycache__' in relative.parts or path.suffix == '.pyc' or relative.as_posix() in ('_virtualenv.py', '_virtualenv.pth'):
            continue
        # UV records hashes of environment-specific console wrappers that
        # this runtime does not ship. The build receipt independently binds
        # every shipped member; do not ship that stale installation index.
        if len(relative.parts) == 2 and relative.parts[0].endswith('.dist-info') and relative.name == 'RECORD':
            continue
        if path.is_file():
            expected = None
            if len(relative.parts) == 2 and relative.parts[0].endswith('.dist-info') and relative.name == 'METADATA':
                expected = metadata_hashes.get(relative.as_posix())
                if expected is None:
                    raise ValueError('dependency metadata population changed during packaging')
                archived_metadata.add(relative.as_posix())
            add('python/lib/python3.12/site-packages/' + relative.as_posix(), path, packages, expected=expected)
        elif path.is_symlink():
            raise ValueError('dependency directory symlink is unsupported')
    if archived_metadata != set(metadata_hashes):
        raise ValueError('dependency metadata population changed during packaging')
    for name, expected in {**binding['source_files'], **selected['source_files']}.items():
        add('source/' + name, ROOT / name, ROOT, expected=expected)
    add('bin/uv', uv, uv.parent, 0o700, expected=binding['uv_binary_sha256'])
    for name, expected in binding['uv_notices'].items():
        path = ROOT / 'tools/converter-notices' / name
        build_io.file_bytes(path, CHECK, maximum=65536, expected=expected['sha256'])
        add('notices/' + name, path, ROOT, expected=expected['sha256'])
    license_path = ROOT / 'tools/converter-notices/encodec-LICENSE-MIT'
    build_io.file_bytes(license_path, CHECK, maximum=65536, expected=pin['license_sha256'])
    add('notices/encodec-LICENSE-MIT', license_path, ROOT, expected=pin['license_sha256'])
    add('notices/kilix-license-LICENSE', ROOT / AUTHORITY_LICENSE, ROOT,
        expected=binding['licence_authority']['files'][AUTHORITY_LICENSE])
    total = sum(row[1] for row in entries.values())
    if not 1 <= len(entries) <= MAXIMUM_FILES or not 1 <= total <= MAXIMUM_BYTES:
        raise ValueError('converter runtime exceeds its declared resource bound')
    # The licence decision is kilix-license's (OD-AJ): the command embeds the
    # pinned authority and a record; it carries no terms text of its own.
    authority = authority_payload(binding, selected)
    runtime_profile = profile_payload(selected, profile)
    output_guard = build_io.Directory(output_root)
    stage_name = '.converter-build-' + uuid.uuid4().hex
    os.mkdir(stage_name, 0o700, dir_fd=output_guard.fd)
    stage = output_root / stage_name
    stage_identity = os.stat(stage_name, dir_fd=output_guard.fd, follow_symlinks=False)
    published = False
    try:
        rows = {}
        with tarfile.open(stage / 'runtime.tar', 'w', format=tarfile.PAX_FORMAT) as archive:
            for name, (path, size, mode, expected) in sorted(entries.items()):
                # Verify the actual immutable bytes handed to tarfile, not
                # just an earlier read of this mutable source pathname.
                payload = build_io.file_bytes(path, CHECK, maximum=size, expected=expected)
                if len(payload) != size:
                    raise ValueError('runtime input changed during packaging')
                item = tarfile.TarInfo(name)
                item.size = size
                item.mode = mode
                item.uid = item.gid = item.mtime = 0
                item.uname = item.gname = ''
                archive.addfile(item, io.BytesIO(payload))
                rows[name] = {'bytes': size, 'mode': mode, 'sha256': hashlib.sha256(payload).hexdigest()}
        archive_size = (stage / 'runtime.tar').stat().st_size
        archive_hash = digest(stage / 'runtime.tar')
        if archive_size > MAXIMUM_BYTES + 64 * 1024**2:
            raise ValueError('runtime archive exceeds bound')
        template_bytes = build_io.file_bytes(ROOT / 'tools/converter_runtime.py', CHECK, maximum=65536)
        template = template_bytes.decode()
        for old, new in {
            '@BUNDLE_SHA256@': archive_hash, '@BUNDLE_BYTES@': str(archive_size),
            '@RUNTIME_BYTES@': str(total),
            '@OUTPUTS_JSON@': json.dumps(selected['outputs'], sort_keys=True, separators=(',', ':')),
            '@PROFILE_HEX@': json.dumps(runtime_profile, sort_keys=True, separators=(',', ':')).encode().hex(),
            '@AUTHORITY_HEX@': json.dumps(authority, sort_keys=True, separators=(',', ':')).encode().hex(),
        }.items():
            if template.count(old) != 1:
                raise ValueError('converter template does not match the builder')
            template = template.replace(old, new)
        compile(template, selected['command'], 'exec')
        (stage / 'command').write_text(template)
        (stage / 'command').chmod(0o700)
        (stage / 'runtime.tar').chmod(0o400)
        receipt = {'schema': 'kilix.encodec.converter-build/v3', 'toolchain': evidence['toolchain'],
            'profile': profile, 'command': selected['command'],
            'inputs': selected['inputs'], 'outputs': selected['outputs'],
            'licence_authority': {'ref': authority['ref'], 'record': runtime_profile['record'],
                                  'files': binding['licence_authority']['files']},
            'packages': evidence['packages'],
            'source_files': {**binding['source_files'], **selected['source_files']},
            'runtime_tar_sha256': archive_hash, 'runtime_tar_bytes': archive_size,
            'runtime_bytes': total, 'runtime_files': rows,
            'command_sha256': digest(stage / 'command'),
            'template_sha256': hashlib.sha256(template_bytes).hexdigest(),
            'builder_sha256': digest(Path(__file__)), 'model_payloads_included': False}
        receipt['build_io_sha256'] = digest(Path(build_io.__file__))
        receipt['tool_archives'] = binding['tool_archives']
        receipt['omitted_installation_metadata'] = ['*.dist-info/RECORD']
        (stage / 'receipt.json').write_text(json.dumps(receipt, indent=2, sort_keys=True) + '\n')
        executable.parent.mkdir(mode=0o700, exist_ok=True)
        CHECK()
        if not executable.parent.is_dir() or executable.parent.is_symlink() or os.path.lexists(executable):
            raise ValueError('unsafe converter executable destination')
        output_guard.check()
        build_io.rename_new(output_guard.fd, stage_name, output_guard.fd, selected['directory'])
        published = True
        os.link(converter / 'command', executable)
        (converter / 'command').unlink()
        print(json.dumps({key: receipt[key] for key in ('schema', 'profile', 'command', 'runtime_tar_sha256',
            'runtime_tar_bytes', 'runtime_bytes', 'command_sha256', 'model_payloads_included')}, indent=2))
    finally:
        try:
            if not published:
                try:
                    current = os.stat(stage_name, dir_fd=output_guard.fd, follow_symlinks=False)
                except FileNotFoundError:
                    current = None
                if current is not None and (current.st_dev, current.st_ino) == (stage_identity.st_dev, stage_identity.st_ino):
                    shutil.rmtree(stage_name, dir_fd=output_guard.fd)
        finally:
            output_guard.close()


def prepare_environment(binding, cache, stage, offline):
    """Acquire only exact upstream tool archives, then apply the frozen lock."""
    env = {'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8', 'LC_ALL': 'C.UTF-8',
           'HOME': str(stage.path / 'home'), 'SOURCE_DATE_EPOCH': '0',
           'PYTHONDONTWRITEBYTECODE': '1', 'OMP_NUM_THREADS': '2',
           'MKL_NUM_THREADS': '2', 'OPENBLAS_NUM_THREADS': '2',
           'UV_PROJECT_ENVIRONMENT': str(stage.path / 'environment')}
    (stage.path / 'home').mkdir(mode=0o700)
    tools = {}
    for label in ('uv', 'python'):
        CHECK()
        record = binding['tool_archives'][label]
        filename = record['sha256'] + '.archive'
        path = cache.path / filename
        if not os.path.lexists(path):
            if offline:
                raise ValueError('pinned tool archive is absent from the offline cache')
            temporary = 'download-' + uuid.uuid4().hex
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                         0o600, dir_fd=stage.fd)
            try:
                build_io.run(['/usr/bin/curl', '--fail', '--location', '--silent', '--show-error',
                    '--proto', '=https', '--proto-redir', '=https', '--max-time', '180',
                    '--max-filesize', str(record['bytes']), '--output', f'/proc/self/fd/{fd}', record['url']],
                    cwd=stage.path, env=env, check=CHECK, pass_fds=(fd,))
            finally:
                os.close(fd)
            data = build_io.file_bytes(stage.path / temporary, CHECK,
                                       maximum=record['bytes'], expected=record['sha256'])
            if len(data) != record['bytes']:
                raise ValueError('tool archive size differs')
            # Exclusive publication; an existing cache entry is never replaced.
            fd = os.open(filename, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                         0o400, dir_fd=cache.fd)
            created = os.fstat(fd)
            complete = False
            try:
                for offset in range(0, len(data), 1024**2):
                    CHECK()
                    block = memoryview(data)[offset:offset + 1024**2]
                    while block:
                        count = os.write(fd, block)
                        if count <= 0:
                            raise OSError('tool archive cache write failed')
                        block = block[count:]
                os.fsync(fd)
                complete = True
            finally:
                os.close(fd)
                if not complete:
                    try:
                        current = os.stat(filename, dir_fd=cache.fd, follow_symlinks=False)
                    except FileNotFoundError:
                        current = None
                    if current is not None and (current.st_dev, current.st_ino) == (created.st_dev, created.st_ino):
                        os.unlink(filename, dir_fd=cache.fd)
            os.unlink(temporary, dir_fd=stage.fd)
        data = build_io.file_bytes(path, CHECK, maximum=record['bytes'], expected=record['sha256'])
        if len(data) != record['bytes']:
            raise ValueError('cached tool archive size differs')
        destination = stage.path / (label + '-distribution')
        destination.mkdir(mode=0o700)
        # tarfile sees immutable bytes only after the exact upstream hash;
        # per-member checks and the data filter also bound extraction.
        with tarfile.open(fileobj=io.BytesIO(data), mode='r:gz') as archive:
            total = count = 0
            for entry in archive:
                CHECK()
                total += entry.size
                count += 1
                if count > 10000 or not 0 <= entry.size <= 128 * 1024**2 or total > 512 * 1024**2:
                    raise ValueError('tool extraction exceeds bounds')
                archive.extract(entry, path=destination, filter='data')
        tools[label] = destination / record['binary_member']
        build_io.file_bytes(tools[label], CHECK, maximum=128 * 1024**2, expected=record['binary_sha256'])
    project = stage.path / 'project'
    project.mkdir(mode=0o700)
    for name, expected in binding['source_files'].items():
        data = build_io.file_bytes(ROOT / name, CHECK, maximum=2 * 1024**2, expected=expected)
        destination = project / name
        destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        destination.write_bytes(data)
    command = [str(tools['uv']), 'sync', '--frozen', '--group', 'export', '--no-config', '--no-python-downloads',
               '--no-progress', '--python', str(tools['python']), '--link-mode', 'copy',
               '--cache-dir', str(cache.path / 'uv')]
    if offline:
        command.append('--offline')
    # Build only the EnCodec sdist after the frozen wheels provide setuptools.
    for extra in (['--no-install-package', 'encodec', '--no-build'], ['--no-build-isolation']):
        print(build_io.run(command + extra, cwd=project, env=env, check=CHECK), end='', flush=True)
    return stage.path / 'environment', tools['python'], tools['uv']


def supervised_build(args, deadline):
    """Pin the whole build destination and tear down descendants before cleanup."""
    global CHECK
    with ExitStack() as stack:
        source = stack.enter_context(build_io.Directory(ROOT))
        target = stack.enter_context(build_io.Directory(args.output_root))
        cache = stack.enter_context(build_io.Directory(args.cache_dir, create=True, private=True))
        CHECK = lambda: build_io.checkpoint(deadline, source, target, cache)
        CHECK()
        profile = getattr(args, 'profile', '24khz')
        directory, command = profile_names(profile)
        for name in (directory, 'bin/' + command):
            try:
                os.stat(name, dir_fd=target.fd, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                raise ValueError('refusing any existing converter output')
        name = '.converter-install-' + uuid.uuid4().hex
        os.mkdir(name, 0o700, dir_fd=target.fd)
        identity = os.stat(name, dir_fd=target.fd, follow_symlinks=False)
        published = False
        command_inode = None
        try:
            stage = stack.enter_context(build_io.Directory(target.path / name, private=True))
            if (os.fstat(stage.fd).st_dev, os.fstat(stage.fd).st_ino) != (identity.st_dev, identity.st_ino):
                raise ValueError('new build directory identity differs')
            CHECK = lambda: build_io.checkpoint(deadline, source, target, cache, stage)
            binding = json.loads(build_io.file_bytes(ROOT / 'tools/converter-inputs.json', CHECK, maximum=262144))
            profiled = select_profile(binding, profile)
            for relative, expected in {**binding['source_files'], **profiled['source_files']}.items():
                build_io.file_bytes(ROOT / relative, CHECK, maximum=2 * 1024**2, expected=expected)
            if args.environment is None:
                environment, python, uv = prepare_environment(binding, cache, stage, args.offline)
            else:
                environment, python, uv = args.environment, args.python, args.uv
            selected = [stack.enter_context(build_io.Directory(path)) for path in
                        (environment, python.parent.parent, uv.parent)]
            CHECK = lambda: build_io.checkpoint(deadline, source, target, cache, stage, *selected)
            package = stage.path / 'package'
            package.mkdir(mode=0o700)
            build(environment, python, uv, package, profile)
            CHECK()
            binary = stack.enter_context(build_io.Directory(target.path / 'bin', create=True))
            converter = stack.enter_context(build_io.Directory(package / directory, private=True))
            package_directory = stack.enter_context(build_io.Directory(package))
            packaged_binary = stack.enter_context(build_io.Directory(package / 'bin'))
            info = os.stat(command, dir_fd=packaged_binary.fd, follow_symlinks=False)
            command_inode = (info.st_dev, info.st_ino)
            build_io.rename_new(package_directory.fd, directory, target.fd, directory)
            published = True
            os.link(command, command,
                    src_dir_fd=packaged_binary.fd, dst_dir_fd=binary.fd, follow_symlinks=False)
            target.check()
            binary.check()
            visible = os.stat(directory, dir_fd=target.fd, follow_symlinks=False)
            held = os.fstat(converter.fd)
            if (visible.st_dev, visible.st_ino) != (held.st_dev, held.st_ino):
                raise ValueError('published converter directory was replaced')
        except BaseException:
            build_io.reap_owned()
            # Never unlink a substitute at the final command name.
            if command_inode is not None and 'binary' in locals():
                try:
                    info = os.stat(command, dir_fd=binary.fd, follow_symlinks=False)
                except FileNotFoundError:
                    pass
                else:
                    if (info.st_dev, info.st_ino) == command_inode:
                        os.unlink(command, dir_fd=binary.fd)
            if published:
                try:
                    found = os.stat(directory, dir_fd=target.fd, follow_symlinks=False)
                except FileNotFoundError:
                    found = None
                held = os.fstat(converter.fd)
                if found is not None and (found.st_dev, found.st_ino) == (held.st_dev, held.st_ino):
                    shutil.rmtree(directory, dir_fd=target.fd)
            raise
        finally:
            build_io.reap_owned()
            try:
                found = os.stat(name, dir_fd=target.fd, follow_symlinks=False)
            except FileNotFoundError:
                found = None
            if found is not None and (found.st_dev, found.st_ino) == (identity.st_dev, identity.st_ino):
                shutil.rmtree(name, dir_fd=target.fd)
            CHECK = lambda: None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile', choices=PROFILES, default='24khz',
                        help='24khz: stateful mono graphs; 48khz: stereo frame graphs')
    parser.add_argument('--environment', type=Path)
    parser.add_argument('--python', type=Path)
    parser.add_argument('--uv', type=Path)
    parser.add_argument('--output-root', type=Path, default=ROOT)
    parser.add_argument('--cache-dir', type=Path)
    parser.add_argument('--offline', action='store_true')
    parser.add_argument('--timeout', type=float, default=900)
    args = parser.parse_args()
    if not math.isfinite(args.timeout) or not 1 <= args.timeout <= 3600:
        parser.error('timeout must be between 1 and 3600 seconds')
    if sum(path is not None for path in (args.environment, args.python, args.uv)) not in (0, 3):
        parser.error('select all three development environment paths or none')
    if args.cache_dir is None:
        args.cache_dir = Path(os.environ.get('XDG_CACHE_HOME', str(Path.home() / '.cache'))) / 'kilix-encodec-converter-v1'
    for key in ('environment', 'python', 'uv', 'output_root', 'cache_dir'):
        value = getattr(args, key)
        if value is not None:
            setattr(args, key, value.absolute())
    os.umask(0o077)
    if ctypes.CDLL(None, use_errno=True).prctl(36, 1, 0, 0, 0):
        raise OSError('cannot establish dedicated build supervision')
    for number in (signal.SIGTERM, signal.SIGINT):
        signal.signal(number, build_io.stop)
    try:
        supervised_build(args, time.monotonic() + args.timeout)
    finally:
        build_io.reap_owned()


if __name__ == '__main__':
    main()
