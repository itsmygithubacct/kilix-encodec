"""Real file conversion, exact-duration and indexed-seek regression tests."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import struct
import subprocess
import tempfile
import wave


def wav_bytes(channels=1, rate=24000, samples=97):
    pcm = b''.join(struct.pack('<h', (i * 113 % 32000) - 16000)
                   for i in range(samples * channels))
    return (b'RIFF' + struct.pack('<I', len(pcm) + 36) + b'WAVEfmt '
            + struct.pack('<IHHIIHH', 16, 1, channels, rate, rate * channels * 2, channels * 2, 16)
            + b'data' + struct.pack('<I', len(pcm)) + pcm)


def main(args):
    checks = 0
    rows = []
    binary = args.command.resolve()
    with tempfile.TemporaryDirectory(prefix='kenc-file-cli-') as temporary:
        root = Path(temporary)
        def run(arguments, success, diagnostic=None):
            nonlocal checks
            result = subprocess.run([str(binary), *map(str, arguments)],
                capture_output=True, timeout=90)
            if args.stderr_log:
                with args.stderr_log.open('ab') as log:
                    log.write(result.stderr)
            assert result.returncode == success, (arguments[0], result.returncode, result.stderr[-600:])
            if diagnostic is not None:
                assert diagnostic in result.stderr, result.stderr[-600:]
            checks += 1
            return result
        source = root / 'source.wav'
        output = root / 'output.kenc'
        original = wav_bytes()
        malformed = [original[:length] for length in range(44)]
        for offset, value in ((0, b'RIFX'), (8, b'xxxx'), (20, b'\x03\x00'),
                              (22, b'\x03\x00'), (24, struct.pack('<I', 44100)),
                              (28, b'\0\0\0\0'), (32, b'\x01\x00'), (34, b'\x20\x00'),
                              (40, b'\xff\xff\xff\xff')):
            raw = bytearray(original); raw[offset:offset + len(value)] = value
            malformed.append(bytes(raw))
        duplicate = original[:36] + original[12:36] + original[36:]
        duplicate = duplicate[:4] + struct.pack('<I', len(duplicate) - 8) + duplicate[8:]
        malformed.extend([duplicate, original + b'extra'])
        for raw in malformed:
            source.write_bytes(raw)
            run(['encode', '--model-dir', root / 'missing', source, output], 1, b'protocol')
            assert not output.exists()
            checks += 1
        source.write_bytes(original)
        for arguments in (['--bitrate', '24'], ['--threads', '3'], ['--profile', 'bad'], ['--threads', '1', '--threads', '2']):
            run(['encode', '--model-dir', root / 'missing', *arguments, source, output], 2)
        fifo = root / 'fifo'; os.mkfifo(fifo)
        link = root / 'link'; link.symlink_to(source)
        for path in (fifo, link):
            run(['encode', '--model-dir', root / 'missing', path, output], 1, b'regular file')
        for profile, assets, rates, channels, rate, samples, seek in (
                ('24k', args.mono_assets, (3, 6, 12), 1, 24000, 26003, 24999),
                ('48k', args.stereo_assets, (3, 6, 12, 24), 2, 48000, 100003, 99999)):
            if assets is None:
                continue
            source.write_bytes(wav_bytes(channels, rate, samples))
            for bitrate in rates:
                encoded = root / f'{profile}-{bitrate}.kenc'
                decoded = root / f'{profile}-{bitrate}.wav'
                sought = root / f'{profile}-{bitrate}-seek.wav'
                command = ['encode', '--model-dir', assets, '--profile', profile,
                           '--bitrate', bitrate, '--threads', '1', source, encoded]
                run(command, 0)
                retained = encoded.read_bytes()
                run(command, 1, b'existing outputs are preserved')
                assert encoded.read_bytes() == retained
                checks += 1
                run(['decode', '--model-dir', assets, encoded, decoded], 0)
                with wave.open(str(decoded), 'rb') as result:
                    assert (result.getnchannels(), result.getframerate(), result.getnframes()) == (channels, rate, samples)
                    complete = result.readframes(samples)
                assert len(complete) == samples * channels * 2
                checks += 2
                run(['decode', '--model-dir', assets, '--seek-sample', seek, encoded, sought], 0)
                boundary = 24000 if channels == 1 else 95040
                with wave.open(str(sought), 'rb') as result:
                    assert result.getnframes() == samples - boundary
                    suffix = result.readframes(samples)
                assert suffix == complete[boundary * channels * 2:]
                checks += 2
                absent = root / 'invalid-seek.wav'
                run(['decode', '--model-dir', assets, '--seek-sample', samples, encoded, absent], 1)
                assert not absent.exists()
                checks += 1
                rows.append({'profile': profile, 'bitrate_kbps': bitrate,
                             'samples': samples, 'seek_sample': boundary, 'seek_pcm_exact': True,
                             'encoded_bytes': len(retained), 'pcm_sha256': hashlib.sha256(complete).hexdigest()})
                print(f'file {profile} {bitrate} kb/s exact duration and seek PASS', flush=True)
    print(json.dumps({'checks_passed': checks, 'rows': rows,
                      'scope': 'file-functional-only', 'release_qualified': False}, sort_keys=True, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('command', type=Path)
    parser.add_argument('--mono-assets', type=Path)
    parser.add_argument('--stereo-assets', type=Path)
    parser.add_argument('--stderr-log', type=Path)
    main(parser.parse_args())
