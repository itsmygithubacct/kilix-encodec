#!/usr/bin/env python3
"""Stage one offline, source-bound Debian native package without installing it."""
from __future__ import annotations

import argparse
from contextlib import ExitStack
import ctypes
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import re
import signal
import stat
import sys
import tarfile
import time
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_content_bundle as objects
import converter_build_io as process_io

ROOT = Path(__file__).resolve().parents[1]
SHA = re.compile(r'[0-9a-f]{40}\Z')
MAX_SOURCE = 16 * 1024**2


def digest(data):
    return hashlib.sha256(data).hexdigest()


def canonical(value):
    return (json.dumps(value, sort_keys=True, separators=(',', ':')) + '\n').encode()


def source_files(source, commit, check, *, cleanup=None):
    """Ignore worktree/archive attributes and verify every consumed Git object."""
    raw = objects.git_object(source, 'commit', commit, 65536, check=check, cleanup=cleanup)
    tree = raw.split(b'\n', 1)[0].removeprefix(b'tree ').decode()
    if not SHA.fullmatch(tree):
        raise ValueError('source commit has no canonical tree')
    result = {}
    total = 0

    def visit(oid, prefix='', depth=0):
        nonlocal total
        check()
        if depth > 12:
            raise ValueError('native source tree is too deep')
        for name, (mode, child) in objects.git_tree(source, oid, check=check, cleanup=cleanup).items():
            check()
            path = prefix + name
            if mode == '40000':
                visit(child, path + '/', depth + 1)
                continue
            if mode not in ('100644', '100755') or len(result) >= 256:
                raise ValueError('native source population is unsupported')
            if Path(name).suffix.lower() in ('.onnx', '.pt', '.th', '.safetensors', '.deb'):
                raise ValueError('model or binary payload is not native source')
            data = objects.git_object(source, 'blob', child, 2 * 1024**2, check=check, cleanup=cleanup)
            total += len(data)
            if total > MAX_SOURCE:
                raise ValueError('native source exceeds the byte bound')
            result[path] = (int(mode, 8) & 0o777, data)
    visit(tree)
    for required in ('Makefile', 'LICENSE', 'THIRD-PARTY-NOTICES.md',
                     'tools/debian-dependencies.json', 'tools/build_native_package.py'):
        if required not in result:
            raise ValueError('native package source is incomplete')
    return tree, result


def materialize(files, root):
    root.mkdir(mode=0o700)
    for name, (mode, data) in files.items():
        path = root/name
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        path.write_bytes(data)
        path.chmod(mode)


def inventory(root, check):
    result = {}
    for path in sorted(root.rglob('*')):
        check()
        info = path.lstat()
        name = path.relative_to(root).as_posix()
        if stat.S_ISDIR(info.st_mode):
            continue
        if path.is_symlink():
            target = os.readlink(path)
            if name != 'usr/lib/libkilix-encodec.so' or target != 'libkilix-encodec.so.0':
                raise ValueError('unexpected installed symlink')
            result[name] = {'link': target}
        elif stat.S_ISREG(info.st_mode) and info.st_nlink == 1:
            data = process_io.file_bytes(path, check, maximum=32*1024**2)
            result[name] = {'bytes': len(data), 'sha256': digest(data), 'mode': info.st_mode & 0o777}
        else:
            raise ValueError('unexpected installed file type')
    return result


def write_source_archive(files, destination):
    # A compact exact source offer, including native and embedded helper notices;
    # generated binaries, models and the converter environment are absent.
    with destination.open('wb') as raw, gzip.GzipFile(filename='', mode='wb', fileobj=raw, mtime=0) as compressed:
        # Receipt filenames in the source tree exceed ustar's 100-byte name
        # field. PAX records the complete path without changing the payload.
        with tarfile.open(fileobj=compressed, mode='w', format=tarfile.PAX_FORMAT) as archive:
            for name, (mode, data) in sorted(files.items()):
                entry = tarfile.TarInfo(name)
                entry.size, entry.mode, entry.mtime = len(data), mode, 0
                entry.uid = entry.gid = 0
                archive.addfile(entry, io.BytesIO(data))


# The three packages the library links directly. Debian ships security fixes
# for them as new versions, so the package states the version it was built
# against as a minimum: an exact pin would make dpkg refuse the package on a
# machine that is already patched, and would hold libc6 and OpenSSL security
# updates back on one where it is installed.
DIRECT_DEPENDENCIES = ('libonnxruntime1.21', 'libssl3t64', 'libc6')
PACKAGE_NAME = re.compile(r'[a-z0-9][a-z0-9+.-]{1,127}')
PACKAGE_VERSION = re.compile(r'[A-Za-z0-9.+:~_-]{1,100}')


def debian_depends(lock, runtime):
    """Every package a loaded library comes from, at the version built against.

    The three direct dependencies come first at their lock versions; the other
    owners follow in name order, so dpkg refuses an older library up front
    instead of the installed-system check refusing it after the fact.
    """
    floors = {name: lock['packages'][name]['version'] for name in DIRECT_DEPENDENCIES}
    for row in runtime.values():
        if floors.setdefault(row['package'], row['version']) != row['version']:
            raise ValueError('one package recorded at two versions: ' + row['package'])
    order = list(DIRECT_DEPENDENCIES) + sorted(set(floors) - set(DIRECT_DEPENDENCIES))
    return ', '.join(name + ' (>= ' + floors[name] + ')' for name in order)


def debian_owner(path, dependency_root, private_owner, query):
    """(package, version) that ships path, or ('', '') when there is not exactly one.

    query(argv) returns a command's output and raises RuntimeError on failure.
    Debian 13 ships libraries under /usr; dpkg may still record /lib.
    """
    if path.is_relative_to(dependency_root):
        return private_owner.get(str(path.relative_to(dependency_root)), ('', ''))
    for candidate in (str(path), str(path).replace('/usr/lib/', '/lib/', 1)):
        try:
            listing = query(['/usr/bin/dpkg-query', '-S', candidate])
        except RuntimeError:
            continue
        owners = [line[:-len(': ' + candidate)] for line in listing.splitlines()
                  if line.endswith(': ' + candidate) and not line.startswith('diversion ')]
        if len(owners) != 1 or ',' in owners[0]:
            return '', ''
        package = owners[0].split(':', 1)[0]
        # Qualified, so an i386 co-installation cannot concatenate two versions.
        versions = query(['/usr/bin/dpkg-query', '-W', '-f=${Version}\\n', package + ':amd64']).splitlines()
        return (package, versions[0]) if len(versions) == 1 else ('', '')
    return '', ''


def runtime_libraries(paths, owner, fingerprint):
    """Name each loaded library by the Debian package that ships it.

    The installed system verifies a library against its owner's own dpkg
    checksums at an owner version no older than the one built against, so a
    security update to that owner is accepted and a stray or replaced file is
    not. The build's own bytes are kept as provenance only.
    """
    rows = {}
    for path in paths:
        name = path.name
        if name in rows:
            raise ValueError('duplicate runtime library name: ' + name)
        package, version = owner(path)
        if not PACKAGE_NAME.fullmatch(package) or not PACKAGE_VERSION.fullmatch(version):
            raise ValueError('runtime library has no Debian owner: ' + name)
        rows[name] = {'package': package, 'version': version, 'built': fingerprint(path)}
    return rows


def build(args):
    deadline = time.monotonic() + args.timeout
    with ExitStack() as stack:
        parent = stack.enter_context(process_io.Directory(args.output.parent, private=True))
        source = stack.enter_context(process_io.Directory(args.source))
        content = stack.enter_context(process_io.Directory(args.content_source))
        check = lambda: process_io.checkpoint(deadline, parent, source, content)
        if os.path.lexists(args.output):
            raise ValueError('package output must be a new entry')
        staging_name = '.native-package-' + uuid.uuid4().hex
        os.mkdir(staging_name, 0o700, dir_fd=parent.fd)
        stage = args.output.parent/staging_name
        held = stack.enter_context(process_io.Directory(stage, private=True))
        check = lambda: process_io.checkpoint(deadline, parent, source, content, held)
        tree, files = source_files(source.path, args.commit, check, cleanup=process_io.reap_owned)
        # The launcher and reused helpers must be precisely this source candidate.
        for name in ('build_native_package.py', 'build_content_bundle.py', 'converter_build_io.py'):
            if process_io.file_bytes(ROOT/'tools'/name, check, maximum=2*1024**2) != files['tools/'+name][1]:
                raise ValueError('package recipe differs from the selected source')
        materialize(files, stage/'source')
        lock = json.loads(files['tools/debian-dependencies.json'][1])
        if lock['schema'] != 'kilix.encodec.debian-dependencies/v1' or lock['architecture'] != 'amd64':
            raise ValueError('unsupported dependency closure')
        environment = {'PATH': '/usr/bin:/bin', 'LANG': 'C', 'LC_ALL': 'C',
                       'HOME': str(stage), 'SOURCE_DATE_EPOCH': '0',
                       'PYTHONDONTWRITEBYTECODE': '1', 'GIT_NO_LAZY_FETCH': '1'}
        logs = stage/'logs'
        logs.mkdir(mode=0o700)
        commands = []

        def run(command):
            check()
            commands.append(command)
            value = process_io.run(command, cwd=stage, env=environment, check=check)
            (logs/(str(len(commands))+'.log')).write_text(value)
            return value.strip()

        if run(['/usr/bin/dpkg', '--print-architecture']) != 'amd64':
            raise ValueError('native package requires Debian amd64')
        dependency_root = stage/'dependencies'
        dependency_root.mkdir(mode=0o700)
        packages = {}
        private_owner = {}
        for name, record in sorted(lock['packages'].items()):
            if args.deb_directory is not None and record['provision'] == 'ort':
                origin = args.deb_directory/Path(record['filename']).name
                payload = process_io.file_bytes(origin, check, maximum=16*1024**2, expected=record['sha256'])
                if len(payload) != record['bytes']:
                    raise ValueError('dependency package size differs')
                copied = stage/(name+'.deb')
                copied.write_bytes(payload)
                value = run(['/usr/bin/dpkg-deb', '-f', str(copied), 'Package', 'Version', 'Architecture'])
                expected = f'Package: {name}\nVersion: {record["version"]}\nArchitecture: amd64'
                if value != expected:
                    raise ValueError('dependency package metadata differs')
                for line in run(['/usr/bin/dpkg-deb', '-c', str(copied)]).splitlines():
                    member = line.split()[-1] if line.split() else ''
                    if member.startswith('./') and not line.startswith('d'):
                        private_owner[member[2:]] = (name, record['version'])
                run(['/usr/bin/dpkg-deb', '-x', str(copied), str(dependency_root)])
                copied.unlink()
                packages[name] = {'version': record['version'], 'sha256': record['sha256'], 'source': 'verified-private-deb'}
            else:
                value = run(['/usr/bin/dpkg-query', '-W', '-f=${db:Status-Status}\t${Version}\t${Architecture}', name])
                if value != f'installed\t{record["version"]}\tamd64':
                    raise ValueError(f'provision {name}={record["version"]} from the selected Debian snapshot before building')
                packages[name] = {'version': record['version'], 'source': 'installed-dpkg'}
        compiler = Path(args.cc).resolve(strict=True)
        if not compiler.is_relative_to('/usr/bin') or compiler.stat().st_uid != 0:
            raise ValueError('select a provisioned system C compiler')
        compiler_bytes = process_io.file_bytes(compiler, check, maximum=64*1024**2)
        compiler_version = run([str(compiler), '--version'])
        cflags = '-O2 -g -std=c11 -fPIC -Wall -Wextra -Wpedantic -Werror -ffile-prefix-map='+str(stage)+'=/usr/src/kilix-encodec'
        libdir = dependency_root/'usr/lib/x86_64-linux-gnu'
        include = dependency_root/'usr/include/onnxruntime'
        if args.deb_directory is None:
            libdir, include = Path('/usr/lib/x86_64-linux-gnu'), Path('/usr/include/onnxruntime')
        environment['LD_LIBRARY_PATH'] = str(libdir)
        build_dir = stage/'build'
        dest = stage/'package'
        make = ['/usr/bin/make', '-j2', '-C', str(stage/'source'),
                'ONNX=1', 'CONTENT=1', 'PREFIX=/usr', 'BUILD='+str(build_dir),
                'CONTENT_SOURCE='+str(content.path), 'CONTENT_COMMIT='+args.content_commit,
                'CC='+str(compiler), 'CFLAGS='+cflags,
                'ONNX_CFLAGS=-I'+str(include), 'ONNX_LIBS=-L'+str(libdir)+' -lonnxruntime',
                'LDFLAGS=-Wl,-rpath-link,'+str(libdir)]
        run(make+['all'])
        run(make+['DESTDIR='+str(dest), 'install'])
        content_receipt = json.loads((build_dir/'content_bundle.receipt.json').read_bytes())
        if content_receipt['content_commit'] != args.content_commit:
            raise ValueError('embedded Content commit differs')
        probe = ('import ctypes,json; p=ctypes.CDLL('+repr(str(dest/'usr/lib/libkilix-encodec.so.0'))+'); '
                 'p.kenc_installed_content_commit.restype=ctypes.c_char_p; '
                 'p.kenc_installed_bundle_sha256.restype=ctypes.c_char_p; '
                 'print(json.dumps([p.kenc_installed_content_commit().decode(),p.kenc_installed_bundle_sha256().decode()]))')
        actual = json.loads(run(['/usr/bin/python3', '-I', '-c', probe]))
        if actual != [args.content_commit, content_receipt['bundle_sha256']]:
            raise ValueError('installed library does not embed the selected Content bundle')
        elf = run(['/usr/bin/readelf', '-d', str(dest/'usr/lib/libkilix-encodec.so.0')])
        if 'RPATH' in elf or 'RUNPATH' in elf or not all(x in elf for x in ('libonnxruntime.so.1.21', 'libcrypto.so.3')):
            raise ValueError('shared library has an unexpected loader closure')
        linked = run(['/usr/bin/ldd', str(dest/'usr/lib/libkilix-encodec.so.0')])
        if 'not found' in linked:
            raise ValueError('native runtime dependency is absent')
        resolved = []
        for line in linked.splitlines():
            found = re.search(r'(?:=>\s+)?(/\S+)', line)
            if found:
                resolved.append(Path(found[1]).resolve(strict=True))

        def owner(path):
            return debian_owner(path, dependency_root, private_owner, run)

        def fingerprint(path):
            data = process_io.file_bytes(path, check, maximum=96*1024**2)
            return {'bytes': len(data), 'sha256': digest(data)}

        runtime = runtime_libraries(resolved, owner, fingerprint)
        doc = dest/'usr/share/doc/kilix-encodec'
        write_source_archive(files, doc/'source.tar.gz')
        (doc/'debian-dependencies.json').write_bytes(files['tools/debian-dependencies.json'][1])
        for name in ('source.tar.gz', 'debian-dependencies.json'):
            (doc/name).chmod(0o644)
        record = {'schema': 'kilix.encodec.native-package/v2', 'source_commit': args.commit,
                  'source_tree': tree, 'content_commit': args.content_commit,
                  'content_bundle_sha256': content_receipt['bundle_sha256'],
                  'compiler': {'name': compiler.name, 'sha256': digest(compiler_bytes), 'version': compiler_version},
                  'build': {'ONNX': 1, 'CONTENT': 1, 'PREFIX': '/usr', 'CFLAGS': cflags.replace(str(stage), '$BUILD'),
                            'ONNX_CFLAGS': '-I$ORT/include', 'ONNX_LIBS': '-L$ORT/lib -lonnxruntime',
                            'LDFLAGS': '-Wl,-rpath-link,$ORT/lib'},
                  'snapshot': lock['snapshot'], 'packages': packages, 'runtime_libraries': runtime,
                  'files': inventory(dest, check)}
        (doc/'native-package.json').write_bytes(canonical(record))
        (doc/'native-package.json').chmod(0o644)
        control = dest/'DEBIAN'
        control.mkdir(mode=0o755)
        control.chmod(0o755)
        version = files['VERSION'][1].decode().strip()+'+git'+args.commit[:12]+'.'+args.content_commit[:12]
        depends = debian_depends(lock, runtime)
        (control/'control').write_text(f'Package: libkilix-encodec\nVersion: {version}\nArchitecture: amd64\n'
            'Maintainer: itsmygithubacct <itsmygithubacct@users.noreply.github.com>\n'
            f'Depends: {depends}\nSection: libs\nPriority: optional\n'
            'Description: Shared Kilix codec and installed-content admission\n No model payload or model authorization is included.\n')
        (control/'triggers').write_text('activate-noawait ldconfig\n')
        for name in ('control', 'triggers'):
            (control/name).chmod(0o644)
        for operation in ('postinst', 'postrm'):
            script = control/operation
            script.write_text('#!/bin/sh\nset -e\nif [ "$1" = configure ] || [ "$1" = remove ]; then\n  ldconfig\nfi\n')
            script.chmod(0o755)
        package = stage/'libkilix-encodec.deb'
        run(['/usr/bin/dpkg-deb', '--root-owner-group', '--threads-max=2', '-Zxz', '--build', str(dest), str(package)])
        # No consumer-local codec or ORT payload is installed in the package.
        check()
        if process_io.file_bytes(compiler, check, maximum=64*1024**2) != compiler_bytes:
            raise ValueError('compiler changed during the build')
        (stage/'commands.json').write_bytes(canonical(commands))
        (stage/'result.json').write_bytes(canonical({'package_sha256': digest(package.read_bytes()),
            'package_bytes': package.stat().st_size, 'record_sha256': digest(canonical(record)),
            'source_commit': args.commit, 'content_commit': args.content_commit}))
        process_io.rename_new(parent.fd, staging_name, parent.fd, args.output.name)
        return args.output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=ROOT)
    parser.add_argument('--commit', required=True)
    parser.add_argument('--content-source', required=True, type=Path)
    parser.add_argument('--content-commit', required=True)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--deb-directory', type=Path, help='existing verified ORT packages; no download or host install')
    parser.add_argument('--cc', default='/usr/bin/cc')
    parser.add_argument('--timeout', type=int, default=300)
    args = parser.parse_args()
    if not SHA.fullmatch(args.commit) or not SHA.fullmatch(args.content_commit) or not 1 <= args.timeout <= 300:
        parser.error('exact commits and timeout 1..300 are required')
    if not args.output.is_absolute() or args.output.name in ('', '.', '..'):
        parser.error('output must be a new absolute directory')
    os.umask(0o077)
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(36, 1, 0, 0, 0):
        raise OSError(ctypes.get_errno(), 'cannot supervise native build')
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, process_io.stop)
    try:
        print(build(args))
        return 0
    except (OSError, ValueError, RuntimeError, TimeoutError) as error:
        print('native package: '+str(error), file=sys.stderr)
        return 1
    finally:
        process_io.reap_owned()


if __name__ == '__main__':
    sys.exit(main())
