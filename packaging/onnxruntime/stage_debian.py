#!/usr/bin/env python3
"""Stage pinned development ORT runtime/dev packages offline; install nothing.

This packages an already built, fingerprinted candidate. It is deliberately
separate from compilation and from performance/release qualification.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess

HERE = Path(__file__).resolve().parent
MAINTAINER = 'itsmygithubacct <itsmygithubacct@users.noreply.github.com>'


def fingerprint(path):
    result = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(block)
    return {'bytes': path.stat().st_size, 'sha256': result.hexdigest()}


def verify(path, expected):
    if fingerprint(path)['sha256'] != expected:
        raise ValueError('staging input differs: ' + str(path))


def stage(args):
    lock = json.loads((HERE / 'stage-inputs.json').read_text())
    for name, expected in lock['patches'].items():
        verify(HERE / name, expected)
    def verify_install_inputs():
        for category, root in (('source', args.source), ('build', args.build)):
            for name, expected in lock['install_inputs'][category].items():
                path = root / name
                if path.is_symlink():
                    raise ValueError('install input is a symlink: ' + name)
                verify(path, expected)

    verify_install_inputs()
    for name, expected in lock['source_files'].items():
        verify(args.source / name, expected)
    verify(args.source_dsc, lock['debian_source_dsc_sha256'])
    verify(args.build / 'CMakeCache.txt', lock['cmake_cache_sha256'])
    verify(args.build / 'libonnxruntime.so.1.21.0', lock['runtime_sha256'])
    if args.output.exists():
        raise ValueError('output must be a new directory')
    args.output.mkdir(mode=0o700)
    commands = []
    environment = {'PATH': '/usr/bin:/bin', 'LANG': 'C', 'LC_ALL': 'C',
                   'HOME': str(args.output), 'SOURCE_DATE_EPOCH': '0'}

    def run(command, *, extra_environment=None):
        commands.append(command)
        result = subprocess.run(command, check=True, text=True,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                env=environment | (extra_environment or {}),
                                timeout=300)
        (args.output / ('command-%02d.log' % len(commands))).write_text(result.stdout)
        return result.stdout

    if run(['dpkg', '--print-architecture']).strip() != 'amd64':
        raise ValueError('this staged input requires Debian amd64')
    installed = args.output / 'cmake-install'
    # Build the provider targets before staging so development metadata and
    # the runtime package retain Debian's DNNL/shared-provider population.
    run(['cmake', '--install', str(args.build), '--prefix', '/usr', '--strip'],
        extra_environment={'DESTDIR': str(installed)})
    verify_install_inputs()
    runtime_name = 'libonnxruntime1.21'
    dev_name = 'libonnxruntime-dev'
    roots = {name: args.output / name for name in (runtime_name, dev_name)}
    for root in roots.values():
        (root / 'usr/lib/x86_64-linux-gnu').mkdir(parents=True)
    runtime = roots[runtime_name]
    development = roots[dev_name]
    libraries = runtime / 'usr/lib/x86_64-linux-gnu'
    before = {}
    remove_rpath = args.output / 'remove-rpath.cmake'
    remove_rpath.write_text('file(RPATH_REMOVE FILE "${library}")\n')
    for name in ('libonnxruntime.so.1.21.0',
                 'libonnxruntime_providers_shared.so',
                 'libonnxruntime_providers_dnnl.so'):
        origin = args.build / name
        destination = libraries / name
        before[name] = fingerprint(origin)
        shutil.copyfile(installed / 'usr/lib' / name, destination)
        destination.chmod(0o644)
        run(['cmake', '-Dlibrary=' + str(destination), '-P', str(remove_rpath)])
        elf = run(['readelf', '-d', str(destination)])
        if 'RPATH' in elf or 'RUNPATH' in elf:
            raise ValueError('staged runtime has a loader path override')
        # Preserve all allocated instructions/constants/data. Only removing
        # the explicitly pinned search paths may change loader metadata.
        def allocated_sections(path):
            sections = run(['readelf', '-SW', str(path)])
            pattern = re.compile(r'^\s*\[\s*\d+\]\s+(\S+)\s+(\S+)\s+'
                                 r'([0-9a-fA-F]+)\s+([0-9a-fA-F]+)\s+'
                                 r'([0-9a-fA-F]+)\s+[0-9a-fA-F]+\s+(\S+)')
            result = {}
            with path.open('rb') as stream:
                for line in sections.splitlines():
                    match = pattern.match(line)
                    if match is None:
                        continue
                    section, kind, address, offset, size, flags = match.groups()
                    if 'A' not in flags or section == '.dynamic':
                        continue
                    length = int(size, 16)
                    if length > 64 * 1024**2 or section in result:
                        raise ValueError('invalid allocated section: ' + section)
                    if kind == 'NOBITS':
                        data = b''
                    else:
                        stream.seek(int(offset, 16))
                        data = stream.read(length)
                        if len(data) != length:
                            raise ValueError('truncated allocated section')
                    if section == '.dynstr':
                        for value in lock['library_rpaths'][name]:
                            raw = value.encode() + b'\x00'
                            data = data.replace(raw, b'\x00' * len(raw))
                    result[section] = (kind, int(address, 16), length, flags,
                                       hashlib.sha256(data).hexdigest())
            if '.text' not in result:
                raise ValueError('runtime has no allocated executable .text section')
            return result

        if allocated_sections(origin) != allocated_sections(destination):
            raise ValueError('staging changed allocated runtime sections: ' + name)
        def dynamic_entries(path):
            entries = []
            for line in run(['readelf', '-dW', str(path)]).splitlines():
                if not line.strip().startswith('0x'):
                    continue
                if '(RPATH)' in line or '(RUNPATH)' in line:
                    match = re.search(r'\[(.*?)\]', line)
                    if match is None or match.group(1) not in lock['library_rpaths'][name]:
                        raise ValueError('unexpected original runtime loader path')
                    continue
                if '(NULL)' not in line:
                    entries.append(line.strip())
            return entries
        if dynamic_entries(origin) != dynamic_entries(destination):
            raise ValueError('staging changed runtime loader metadata: ' + name)
        if fingerprint(origin) != before[name]:
            raise ValueError('staging changed an input library: ' + name)
    (libraries / 'libonnxruntime.so.1.21').symlink_to('libonnxruntime.so.1.21.0')
    dev_libraries = development / 'usr/lib/x86_64-linux-gnu'
    (dev_libraries / 'libonnxruntime.so').symlink_to('libonnxruntime.so.1.21')
    shutil.copytree(installed / 'usr/include', development / 'usr/include')
    cmake_metadata = development / 'usr/lib/cmake'
    shutil.copytree(installed / 'usr/lib/cmake', cmake_metadata)
    for path in cmake_metadata.rglob('*.cmake'):
        path.write_text(path.read_text().replace('/lib/', '/lib/x86_64-linux-gnu/'))
    pkgconfig = dev_libraries / 'pkgconfig'
    pkgconfig.mkdir()
    pc = (installed / 'usr/lib/pkgconfig/libonnxruntime.pc').read_text()
    pc = pc.replace('prefix=/usr/local', 'prefix=/usr').replace(
        'libdir=${prefix}/lib', 'libdir=${prefix}/lib/x86_64-linux-gnu')
    (pkgconfig / 'libonnxruntime.pc').write_text(pc)
    version = lock['package_version']
    dependencies = run(['dpkg-query', '-W', '-f=${Depends}', runtime_name]).strip()
    provenance = {'schema': 'kilix.encodec.ort-debian-stage/v1',
                  'release_qualified': False, 'inputs': lock,
                  'original_libraries': before,
                  'install_inputs': lock['install_inputs'],
                  'staged_libraries': {name: fingerprint(libraries / name) for name in before},
                  'stager_sha256': fingerprint(Path(__file__))['sha256'],
                  'compiler': run(['c++', '--version']),
                  'system_packages': run(['dpkg-query', '-W']),
                  'commands': commands}
    for name, root in roots.items():
        doc = root / 'usr/share/doc' / name
        doc.mkdir(parents=True)
        shutil.copyfile(args.source / 'debian/copyright', doc / 'copyright')
        shutil.copyfile(args.source / 'LICENSE', doc / 'upstream-LICENSE')
        shutil.copyfile(args.source_dsc, doc / args.source_dsc.name)
        for patch_name in lock['patches']:
            shutil.copyfile(HERE / patch_name, doc / patch_name)
        (doc / 'kilix-stage-provenance.json').write_text(json.dumps(provenance, indent=2) + '\n')
        control = root / 'DEBIAN'
        control.mkdir()
        size = sum(path.stat().st_size for path in root.rglob('*')
                   if path.is_file() and not path.is_symlink())
        depends = dependencies if name == runtime_name else runtime_name + ' (= ' + version + ')'
        (control / 'control').write_text(
            f'Package: {name}\nVersion: {version}\nArchitecture: amd64\n'
            f'Maintainer: {MAINTAINER}\nDepends: {depends}\n'
            f'Installed-Size: {(size + 1023) // 1024}\nSection: libs\nPriority: optional\n'
            'Multi-Arch: same\nDescription: pinned Kilix ONNX Runtime development candidate\n'
            ' Debian system-ONNX registration and SSE2 inference fixes.\n')
        if name == runtime_name:
            (control / 'triggers').write_text('activate-noawait ldconfig\n')
        rows = []
        for path in sorted((root / 'usr').rglob('*')):
            if path.is_file() and not path.is_symlink():
                rows.append(hashlib.md5(path.read_bytes()).hexdigest() + '  ' + str(path.relative_to(root)))
        (control / 'md5sums').write_text('\n'.join(rows) + '\n')
        for path in root.rglob('*'):
            if not path.is_symlink():
                path.chmod(0o755 if path.is_dir() else 0o644)
            os.utime(path, (0, 0), follow_symlinks=False)
        artifact = args.output / f'{name}_{version}_amd64.deb'
        run(['dpkg-deb', '--root-owner-group', '--build', str(root), str(artifact)])
    provenance['commands'] = commands
    provenance['packages'] = {path.name: fingerprint(path) for path in args.output.glob('*.deb')}
    (args.output / 'staging.json').write_text(json.dumps(provenance, indent=2) + '\n')
    print(json.dumps(provenance['packages'], indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('source', 'build', 'source-dsc', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    stage(parser.parse_args())


if __name__ == '__main__':
    main()
