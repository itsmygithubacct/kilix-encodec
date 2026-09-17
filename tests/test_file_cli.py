"""Real file conversion, exact-duration and indexed-seek regression tests.

With mono assets, every 24 kHz rate is encoded twice: with the default C0
epoch start (a version 1 file) and with --epoch-start C5-R4 (a version 2 file
carrying the marker). Tampered markers must be refused. With
--c0-reference-command, the kenc command built from a git archive of 3747330
must write byte-identical C0 files, both commands must decode each other's C0
files to identical WAV bytes, and the old command must refuse a C5-R4 file.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import wave

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
sys.path.insert(0, str(Path(__file__).resolve().parent / 'fixtures'))
import epoch_stream  # noqa: E402  (NumPy-free header helpers only)
import legacy_c0_assets  # noqa: E402

REFERENCE_CONTROLS_PER_RATE = 7


def wav_bytes(channels=1, rate=24000, samples=97):
    pcm = b''.join(struct.pack('<h', (i * 113 % 32000) - 16000)
                   for i in range(samples * channels))
    return (b'RIFF' + struct.pack('<I', len(pcm) + 36) + b'WAVEfmt '
            + struct.pack('<IHHIIHH', 16, 1, channels, rate, rate * channels * 2, channels * 2, 16)
            + b'data' + struct.pack('<I', len(pcm)) + pcm)


def main(args):
    checks = 0
    skipped = 0
    rows = []
    binary = args.command.resolve()
    reference = args.c0_reference_command.resolve() if args.c0_reference_command else None
    with tempfile.TemporaryDirectory(prefix='kenc-file-cli-') as temporary:
        root = Path(temporary)
        legacy_mono = None
        if reference is not None and args.mono_assets is not None:
            legacy_mono = legacy_c0_assets.materialize(args.mono_assets, root / 'legacy-c0-24k')
        def run(arguments, success, diagnostic=None, command=None):
            nonlocal checks
            result = subprocess.run([str(command or binary), *map(str, arguments)],
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
        for arguments in (['--bitrate', '24'], ['--threads', '3'], ['--profile', 'bad'], ['--threads', '1', '--threads', '2'],
                          ['--epoch-start', 'C6'], ['--epoch-start', 'c5-r4'], ['--profile', '48k', '--epoch-start', 'C5-R4'],
                          ['--epoch-start', 'C0', '--epoch-start', 'C5-R4']):
            run(['encode', '--model-dir', root / 'missing', *arguments, source, output], 2)
        run(['decode', '--model-dir', root / 'missing', '--epoch-start', 'C0', output, root / 'refused.wav'], 2)
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
            epoch_starts = ('C0', 'C5-R4') if profile == '24k' else ('C0',)
            for bitrate in rates:
                files, complete_pcm = {}, {}
                for epoch_start in epoch_starts:
                    encoded = root / f'{profile}-{bitrate}-{epoch_start}.kenc'
                    decoded = root / f'{profile}-{bitrate}-{epoch_start}.wav'
                    sought = root / f'{profile}-{bitrate}-{epoch_start}-seek.wav'
                    command = ['encode', '--model-dir', assets, '--profile', profile,
                               '--bitrate', bitrate, '--threads', '1']
                    if epoch_start != 'C0':
                        command += ['--epoch-start', epoch_start]
                    command += [source, encoded]
                    run(command, 0)
                    retained = encoded.read_bytes()
                    run(command, 1, b'existing outputs are preserved')
                    assert encoded.read_bytes() == retained
                    checks += 1
                    # C0 files stay version 1 with no marker; C5-R4 files are version 2, marker 1.
                    assert (retained[4], retained[42]) == ((2, 1) if epoch_start == 'C5-R4' else (1, 0))
                    if profile == '24k':
                        assert epoch_stream.epoch_start_from_file_header(retained[:64]) == epoch_start
                    checks += 1
                    announced = f'kenc: epoch start {epoch_start}'.encode() if profile == '24k' else None
                    run(['decode', '--model-dir', assets, encoded, decoded], 0, announced)
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
                    if epoch_start == 'C5-R4':
                        for label, changes in (('unknown marker', {42: 7}), ('C0 spelled as version 2', {42: 0}),
                                               ('relabelled as version 1', {4: 1, 42: 0})):
                            tampered = bytearray(retained)
                            for offset, value in changes.items():
                                tampered[offset] = value
                            path = root / 'tampered.kenc'
                            path.write_bytes(bytes(tampered))
                            refused = root / 'tampered.wav'
                            run(['decode', '--model-dir', assets, path, refused], 1, b'epoch-start profile marker')
                            assert not refused.exists(), label
                            path.unlink()
                            checks += 1
                    files[epoch_start], complete_pcm[epoch_start] = encoded, complete
                    rows.append({'profile': profile, 'bitrate_kbps': bitrate, 'epoch_start': epoch_start,
                                 'samples': samples, 'seek_sample': boundary, 'seek_pcm_exact': True,
                                 'encoded_bytes': len(retained), 'pcm_sha256': hashlib.sha256(complete).hexdigest()})
                    print(f'file {profile} {bitrate} kb/s {epoch_start} exact duration and seek PASS', flush=True)
                if profile != '24k':
                    continue
                assert complete_pcm['C0'] != complete_pcm['C5-R4']
                checks += 1
                if reference is None:
                    skipped += REFERENCE_CONTROLS_PER_RATE
                    continue
                before = checks
                old_file = root / f'{profile}-{bitrate}-3747330.kenc'
                run(['encode', '--model-dir', legacy_mono, '--profile', profile, '--bitrate', bitrate,
                     '--threads', '1', source, old_file], 0, command=reference)
                assert old_file.read_bytes() == files['C0'].read_bytes()
                checks += 1
                new_of_old = root / f'{profile}-{bitrate}-new-of-3747330.wav'
                old_of_new = root / f'{profile}-{bitrate}-3747330-of-new.wav'
                run(['decode', '--model-dir', assets, old_file, new_of_old], 0, b'kenc: epoch start C0')
                run(['decode', '--model-dir', legacy_mono, files['C0'], old_of_new], 0, command=reference)
                c0_wav = (root / f'{profile}-{bitrate}-C0.wav').read_bytes()
                assert new_of_old.read_bytes() == c0_wav == old_of_new.read_bytes()
                checks += 1
                refused = root / f'{profile}-{bitrate}-3747330-of-C5-R4.wav'
                run(['decode', '--model-dir', legacy_mono, files['C5-R4'], refused], 1, b'protocol error', command=reference)
                assert not refused.exists()
                checks += 1
                assert checks - before == REFERENCE_CONTROLS_PER_RATE
                print(f'file {profile} {bitrate} kb/s 3747330 C0 reference: identical file and WAV bytes, C5-R4 refused PASS', flush=True)
    if skipped:
        message = f'SKIPPED {skipped} 3747330 C0 file reference controls: --c0-reference-command is not given'
        print(message, flush=True)
        print(message, file=sys.stderr, flush=True)
    print(json.dumps({'checks_passed': checks, 'checks_skipped': skipped, 'rows': rows,
                      'scope': 'file-functional-only', 'release_qualified': False}, sort_keys=True, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('command', type=Path)
    parser.add_argument('--mono-assets', type=Path)
    parser.add_argument('--stereo-assets', type=Path)
    parser.add_argument('--c0-reference-command', type=Path)
    parser.add_argument('--stderr-log', type=Path)
    main(parser.parse_args())
