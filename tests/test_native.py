"""Exercise the native ABI against real, explicitly supplied pinned graphs.

Run with the locked export environment to compare independent ORT Python and
native C sessions. All PCM in this test is synthetic; no model is downloaded.

Both epoch-start profiles (owner decision OD-AT) are exercised:
- C0, the default of new contexts: unmarked RESET packets, zero-state epoch
  starts;
- C5-R4, selected on both sides: every RESET packet carries the epoch-start
  marker and every epoch start runs a four-packet repeat pre-roll.

With --oracle, independent Python ORT sessions running tools/epoch_stream.py
with the same profile must produce identical tokens and PCM within 2 LSB. The
programme's syn-fixture item must reproduce the checked C5-R4 render and the
3747330 C0 reference. With --c0-reference-library, a library built from a git
archive of 3747330 must emit byte-identical C0 packets and PCM, decode new C0
streams with matching PCM, and refuse marked C5-R4 streams. That library still
pins manifest 02201a5a, so it loads the same graphs beside a reconstructed
copy of that manifest (the OD-AS pin changes only lock and encodec version
fields).
"""
from __future__ import annotations

import argparse
import ctypes as c
import hashlib
import json
import math
import os
from pathlib import Path
import sys
import tempfile
import time

TOOLS = Path(__file__).resolve().parents[1] / 'tools'
FIXTURES = Path(__file__).resolve().parent / 'fixtures'
FIXTURE = FIXTURES / 'f101-c5r4-programme.json'
C0_REFERENCE = FIXTURES / 'f101-c0-3747330-reference.json'
sys.path.insert(0, str(FIXTURES))
import legacy_c0_assets  # noqa: E402

OK, INVALID, MODEL, RUNTIME, TRUNCATED, PROTOCOL, MEMORY, EPOCH_START = range(8)
C0, C5_R4 = 0, 1
PROFILE_NAMES = {C0: 'C0', C5_R4: 'C5-R4'}
RESET, END, DISCONTINUITY, EPOCH_PREROLL = 1, 2, 4, 8
REFERENCE_CONTROLS = 18


class Options(c.Structure):
    _fields_ = [('sample_rate', c.c_uint32), ('packet_samples', c.c_uint16),
                ('epoch_packets', c.c_uint16), ('codebooks', c.c_uint8),
                ('threads', c.c_uint8)]


class Info(c.Structure):
    _fields_ = [('pts_ms', c.c_uint64), ('flags', c.c_uint8), ('samples', c.c_uint16)]


class Native:
    def __init__(self, path, legacy=False):
        self.lib = c.CDLL(str(path.resolve()))
        self.legacy = legacy
        ptr = c.c_void_p
        functions = [
            ('model_load', [c.POINTER(ptr), c.c_char_p], c.c_int),
            ('model_free', [ptr], None),
            ('options_default', [], Options),
            ('encoder_create', [c.POINTER(ptr), ptr, c.POINTER(Options)], c.c_int),
            ('decoder_create', [c.POINTER(ptr), ptr, c.POINTER(Options)], c.c_int),
            ('encoder_free', [ptr], None), ('decoder_free', [ptr], None),
            ('encoder_reset', [ptr], None), ('decoder_reset', [ptr], None),
            ('encoder_push_s16', [ptr, c.POINTER(c.c_int16), c.c_size_t,
                c.c_uint64, c.POINTER(c.c_uint8), c.c_size_t, c.POINTER(c.c_size_t)], c.c_int),
            ('decoder_pull_s16', [ptr, c.POINTER(c.c_uint8), c.c_size_t,
                c.POINTER(c.c_int16), c.c_size_t, c.POINTER(c.c_size_t), c.POINTER(Info)], c.c_int),
        ]
        if not legacy:
            functions += [
                ('encoder_set_epoch_start', [ptr, c.c_int], c.c_int),
                ('decoder_set_epoch_start', [ptr, c.c_int], c.c_int),
                ('epoch_start_supported', [], c.c_uint32),
                ('epoch_start_negotiate', [c.c_uint32, c.c_uint32, c.POINTER(c.c_int)], c.c_int),
                ('epoch_start_from_marker', [c.c_uint32, c.POINTER(c.c_int)], c.c_int),
                ('epoch_start_name', [c.c_int], c.c_char_p),
            ]
        for name, args, result in functions:
            fn = getattr(self.lib, 'kenc_' + name)
            fn.argtypes, fn.restype = args, result
            setattr(self, name, fn)

    def encode(self, encoder, pcm, pts, capacity=160):
        output = (c.c_uint8 * 160)(*[0xA5] * 160)
        written = c.c_size_t(999)
        result = self.encoder_push_s16(encoder, (c.c_int16 * len(pcm))(*pcm),
            len(pcm), pts, output, capacity, c.byref(written))
        return result, bytes(output[:written.value]), written.value, bytes(output)

    def decode(self, decoder, packet, capacity=960):
        output = (c.c_int16 * 960)(*[12345] * 960)
        written, info = c.c_size_t(999), Info()
        result = self.decoder_pull_s16(decoder,
            (c.c_uint8 * len(packet)).from_buffer_copy(packet), len(packet),
            output, capacity, c.byref(written), c.byref(info))
        return result, list(output), written.value, info


def parse_wire(packet, books):
    """Independent integer-based parser for comparing tokens with the oracle."""
    assert packet[:4] == b'KMA\2'
    offset = 4
    def integer():
        nonlocal offset
        value, shift = 0, 0
        while True:
            byte = packet[offset]; offset += 1
            value += (byte & 127) * (2 ** shift)
            if byte < 128:
                return value
            shift += 7
    profile, epoch, index, pts = [integer() for _ in range(4)]
    flags = packet[offset]; offset += 1
    samples, payload_size = integer(), integer()
    assert profile == 1 and samples == 960 and payload_size == books * 30 // 8
    payload = packet[offset:]
    assert len(payload) == payload_size
    bits = int.from_bytes(payload, 'big')
    mask = (1 << 10) - 1
    time_major = [(bits >> (10 * (books * 3 - i - 1))) & mask for i in range(books * 3)]
    codes = [[time_major[frame * books + book] for frame in range(3)] for book in range(books)]
    return epoch, index, pts, flags, codes


class Oracle:
    """Independent ORT Python sessions driven by the product epoch-start runtime."""

    def __init__(self, assets, books, profile):
        import numpy as np
        import onnxruntime as ort
        sys.path.insert(0, str(TOOLS))
        import epoch_stream
        self.np = np
        self.stream = epoch_stream
        manifest = json.loads((assets / 'manifest.json').read_text())
        rate = {4:3, 8:6, 16:12}[books]
        self.records = {name:manifest['graphs'][key] for name,key in [
            ('encoder','encoder'), ('decoder','decoder'),
            ('encode',f'rvq_encode_{rate}kbps'), ('decode',f'rvq_decode_{rate}kbps')]}
        options = ort.SessionOptions()
        options.intra_op_num_threads = 1
        options.inter_op_num_threads = 1
        options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        self.sessions = {name:ort.InferenceSession(str(assets / record['file']),
            sess_options=options, providers=['CPUExecutionProvider'])
            for name,record in self.records.items()}
        name = PROFILE_NAMES[profile]
        self.encoder = epoch_stream.StreamEncoder(self.sessions['encoder'], self.records['encoder'],
            self.sessions['encode'], self.records['encode'], profile=name)
        self.decoder = epoch_stream.StreamDecoder(self.sessions['decode'], self.records['decode'],
            self.sessions['decoder'], self.records['decoder'], profile=name)

    def reset(self):
        self.encoder.reset()
        self.decoder.reset()

    def run(self, pcm):
        np = self.np
        audio = np.asarray(pcm, dtype='float32').reshape(1,1,960) / np.float32(32768)
        _, codes = self.encoder.push(audio)
        decoded = self.decoder.pull(codes)
        samples = np.clip(np.rint(decoded.reshape(-1)*32768), -32768, 32767).astype('int16')
        return codes[:,0,:].tolist(), samples.tolist()


def tone(frame, first, second):
    return [round(12000*math.sin(2*math.pi*first*(frame*960+i)/24000)
                  + 3000*math.sin(2*math.pi*second*(frame*960+i)/24000)) for i in range(960)]


def noise(frame):
    return [(i * 7919 + frame * 104729) % 20001 - 10000 for i in range(960)]


def stream_options(native, books):
    options = native.options_default()
    options.codebooks = books
    options.threads = 1
    return options


def native_stream(native, model, books, frames, profile=None, reset_at=None):
    """Encode then decode frames through fresh native contexts; returns packets and PCM.

    profile None keeps the contexts' default. At reset_at the encoder is reset
    explicitly (a DISCONTINUITY epoch start) and the decoder follows the stream.
    """
    options = stream_options(native, books)
    encoder, decoder = c.c_void_p(), c.c_void_p()
    try:
        if native.encoder_create(c.byref(encoder), model, c.byref(options)) != OK or \
                native.decoder_create(c.byref(decoder), model, c.byref(options)) != OK:
            raise AssertionError('native stream contexts were not created')
        if profile is not None and (native.encoder_set_epoch_start(encoder, profile) != OK
                                    or native.decoder_set_epoch_start(decoder, profile) != OK):
            raise AssertionError('native epoch-start profile was not selected')
        packets, pcm = [], []
        for index, samples in enumerate(frames):
            pts = index * 40
            if reset_at is not None and index >= reset_at:
                if index == reset_at:
                    native.encoder_reset(encoder)
                pts += 7
            result, packet, _, _ = native.encode(encoder, samples, pts)
            if result != OK:
                raise AssertionError(f'native stream encode failed with {result}')
            result, output, written, _ = native.decode(decoder, packet)
            if result != OK or written != 960:
                raise AssertionError(f'native stream decode failed with {result}')
            packets.append(packet)
            pcm.append(output)
        return packets, pcm
    finally:
        native.encoder_free(encoder)
        native.decoder_free(decoder)


def decode_packets(native, model, books, packets, profile=None):
    """Decode packets through a fresh decoder; stops at the first refusal.

    Returns (result, pcm blocks decoded so far, refused output buffer, written).
    """
    options = stream_options(native, books)
    decoder = c.c_void_p()
    try:
        if native.decoder_create(c.byref(decoder), model, c.byref(options)) != OK:
            raise AssertionError('native decoder was not created')
        if profile is not None and native.decoder_set_epoch_start(decoder, profile) != OK:
            raise AssertionError('native decoder profile was not selected')
        pcm = []
        for packet in packets:
            result, output, written, _ = native.decode(decoder, packet)
            if result != OK:
                return result, pcm, output, written
            pcm.append(output)
        return OK, pcm, None, 0
    finally:
        native.decoder_free(decoder)


def negotiation_controls(native):
    """Model-free epoch-start advertisement, negotiation and marker controls."""
    checks = 0
    def check(value, description):
        nonlocal checks
        if not value:
            raise AssertionError(description)
        checks += 1
    supported = native.epoch_start_supported()
    selected = c.c_int(99)
    def negotiate(local, peer):
        selected.value = 99
        return native.epoch_start_negotiate(local, peer, c.byref(selected)), selected.value
    check(supported == 3, 'the library advertises C0 and C5-R4')
    check(negotiate(supported, supported) == (OK, C5_R4), 'new+new negotiates C5-R4')
    check(negotiate(supported, 0) == (OK, C0), 'new+old (no advertisement) negotiates C0')
    check(negotiate(supported, 1) == (OK, C0), 'a C0-only peer negotiates C0')
    check(negotiate(1, supported) == (OK, C0), 'a C0-only local side negotiates C0')
    check(negotiate(supported, 2) == (EPOCH_START, 99), 'a peer advertisement without C0 is refused')
    marker = c.c_int(99)
    check(native.epoch_start_from_marker(7, c.byref(marker)) == EPOCH_START and marker.value == 99,
          'an unknown profile marker is refused')
    check(native.epoch_start_name(C0) == b'C0' and native.epoch_start_name(C5_R4) == b'C5-R4',
          'profile names')
    sys.path.insert(0, str(TOOLS))
    import epoch_stream
    pairs = [(local, peer) for local in (1, 3) for peer in (0, 1, 2, 3, 5, 7, 0x80000003)]
    agree = 0
    for local, peer in pairs:
        native_result = negotiate(local, peer)
        try:
            python_result = (OK, epoch_stream.EPOCH_START_MARKERS[epoch_stream.negotiate_epoch_start(local, peer)])
        except ValueError:
            python_result = (EPOCH_START, 99)
        agree += int(python_result == native_result)
    check(agree == len(pairs), f'Python and native negotiation agree on {agree}/{len(pairs)} advertisement pairs')
    print(f'native epoch-start negotiation controls: {checks}/{checks} PASS', flush=True)
    return checks


def mixed_peer_controls(native, assets, reference_library):
    """Marked and unmarked peers: matching PCM or a refusal, never silent drift."""
    checks = 0
    def check(value, description):
        nonlocal checks
        if not value:
            raise AssertionError(description)
        checks += 1
    frames = [tone(frame, 437, 113) for frame in range(27)] + [tone(frame, 211, 59) for frame in range(27, 30)]
    resets = (0, 25, 27)
    model = c.c_void_p()
    check(native.model_load(c.byref(model), os.fsencode(assets)) == OK and model.value,
          'validated model loaded for mixed-peer controls')
    try:
        c5_packets, c5_pcm = native_stream(native, model, 8, frames, C5_R4, reset_at=27)
        c0_packets, c0_pcm = native_stream(native, model, 8, frames, None, reset_at=27)
        check([parse_wire(c5_packets[i], 8)[3] for i in resets]
              == [RESET | EPOCH_PREROLL, RESET | EPOCH_PREROLL, RESET | DISCONTINUITY | EPOCH_PREROLL]
              and all(parse_wire(packet, 8)[3] & EPOCH_PREROLL == 0
                      for index, packet in enumerate(c5_packets) if index not in resets),
              'C5-R4 marks every RESET packet, including an explicit discontinuity, and no other packet')
        check([parse_wire(c0_packets[i], 8)[3] for i in resets] == [RESET, RESET, RESET | DISCONTINUITY]
              and all(parse_wire(packet, 8)[3] & EPOCH_PREROLL == 0 for packet in c0_packets),
              'C0 leaves every packet unmarked')
        check(native_stream(native, model, 8, frames, C0, reset_at=27) == (c0_packets, c0_pcm),
              'explicitly selected C0 equals the default contexts')
        check(c5_pcm[0] != c0_pcm[0] and c5_pcm[25] != c0_pcm[25] and c5_pcm[27] != c0_pcm[27],
              'C0 and C5-R4 decode different PCM at every epoch start')
        supported = native.epoch_start_supported()
        selected = c.c_int(99)
        check(native.epoch_start_negotiate(supported, supported, c.byref(selected)) == OK
              and selected.value == C5_R4
              and native_stream(native, model, 8, frames, selected.value, reset_at=27) == (c5_packets, c5_pcm),
              'new+new: both sides use the negotiated C5-R4 and decode matching PCM')
        check(native.epoch_start_negotiate(supported, 0, c.byref(selected)) == OK and selected.value == C0
              and native_stream(native, model, 8, frames, selected.value, reset_at=27) == (c0_packets, c0_pcm),
              'new+old: both sides fall back to C0 and decode matching PCM')
        result, pcm, output, written = decode_packets(native, model, 8, c5_packets, C0)
        check(result == EPOCH_START and not pcm and written == 0 and output == [12345] * 960,
              'a C0 decoder refuses a marked C5-R4 stream at its first RESET without output')
        result, pcm, output, written = decode_packets(native, model, 8, c0_packets, C5_R4)
        check(result == EPOCH_START and not pcm and written == 0 and output == [12345] * 960,
              'a C5-R4 decoder refuses an unmarked C0 stream at its first RESET without output')
        options = stream_options(native, 8)
        encoder, decoder = c.c_void_p(), c.c_void_p()
        try:
            check(native.decoder_create(c.byref(decoder), model, c.byref(options)) == OK
                  and native.encoder_create(c.byref(encoder), model, c.byref(options)) == OK,
                  'selection-rule contexts created')
            refused = native.decode(decoder, c5_packets[0])[0]
            joined = [native.decode(decoder, packet) for packet in c0_packets[:25]]
            check(refused == EPOCH_START and all(row[0] == OK for row in joined)
                  and [row[1] for row in joined] == c0_pcm[:25],
                  'a refused marker leaves the decoder able to join its own profile')
            check(native.decoder_set_epoch_start(decoder, C5_R4) == PROTOCOL,
                  'a running decoder cannot change profile')
            native.decoder_reset(decoder)
            check(native.decoder_set_epoch_start(decoder, C5_R4) == OK
                  and native.decode(decoder, c5_packets[0])[:3:2] == (OK, 960),
                  'an explicitly reset decoder selects a new profile')
            check(native.encode(encoder, frames[0], 0)[0] == OK
                  and native.encoder_set_epoch_start(encoder, C5_R4) == PROTOCOL,
                  'a running encoder cannot change profile')
            native.encoder_reset(encoder)
            result, packet, _, _ = native.encode(encoder, frames[1], 3)
            check(native.encoder_set_epoch_start(encoder, 2) == EPOCH_START, 'an unknown profile is refused')
            check(result == OK and parse_wire(packet, 8)[3] == RESET | DISCONTINUITY,
                  'the discontinuity after a reset stays C0 until a profile is selected')
            native.encoder_reset(encoder)
            check(native.encoder_set_epoch_start(encoder, C5_R4) == OK
                  and parse_wire(native.encode(encoder, frames[2], 5)[1], 8)[3]
                  == RESET | DISCONTINUITY | EPOCH_PREROLL,
                  'after an explicit reset the encoder marks the next discontinuity with its new profile')
        finally:
            native.encoder_free(encoder)
            native.decoder_free(decoder)
        print(f'native mixed-peer epoch-start controls: {checks}/{checks} PASS', flush=True)
        if reference_library is None:
            message = (f'SKIPPED {REFERENCE_CONTROLS} 3747330 C0 reference controls: '
                       '--c0-reference-library is not given')
            print(message, flush=True)
            print(message, file=sys.stderr, flush=True)
            return checks, REFERENCE_CONTROLS
        before = checks
        old = Native(reference_library, legacy=True)
        check(not hasattr(old.lib, 'kenc_epoch_start_negotiate') and hasattr(native.lib, 'kenc_epoch_start_negotiate'),
              'the reference library is a separate pre-marker build')
        old_model = c.c_void_p()
        with tempfile.TemporaryDirectory(prefix='kenc-c0-ref-assets-') as legacy_root:
            legacy = legacy_c0_assets.materialize(Path(assets), Path(legacy_root) / 'bundle')
            check(old.model_load(c.byref(old_model), os.fsencode(legacy)) == OK and old_model.value,
                  'the reference library loads the same graphs under the reconstructed 02201a5a manifest')
            try:
                for books in (4, 8, 16):
                    old_packets, old_pcm = native_stream(old, old_model, books, frames, reset_at=27)
                    new_packets, new_pcm = native_stream(native, model, books, frames, reset_at=27)
                    check(new_packets == old_packets and new_pcm == old_pcm,
                          f'{books} codebooks: C0 packets and PCM byte-identical to 3747330')
                    result, pcm, _, _ = decode_packets(native, model, books, old_packets, C0)
                    check(result == OK and pcm == old_pcm,
                          f'{books} codebooks: a 3747330 stream decodes as C0 with matching PCM')
                    result, pcm, _, _ = decode_packets(old, old_model, books, new_packets)
                    check(result == OK and pcm == new_pcm,
                          f'{books} codebooks: a new C0 stream decodes on 3747330 with matching PCM')
                    marked, _ = native_stream(native, model, books, frames, C5_R4, reset_at=27)
                    result, pcm, output, written = decode_packets(old, old_model, books, marked)
                    check(result == PROTOCOL and not pcm and written == 0 and output == [12345] * 960,
                          f'{books} codebooks: 3747330 refuses a marked C5-R4 stream instead of decoding it')
                    result, pcm, _, _ = decode_packets(native, model, books, old_packets, C5_R4)
                    check(result == EPOCH_START and not pcm,
                          f'{books} codebooks: a C5-R4 decoder refuses a 3747330 stream')
                long_frames = [tone(frame, 437, 113) if frame % 50 < 25 else noise(frame) for frame in range(100)]
                old_packets, old_pcm = native_stream(old, old_model, 8, long_frames)
                check(native_stream(native, model, 8, long_frames) == (old_packets, old_pcm),
                      '100-packet mixed tone and noise stream: C0 packets and PCM byte-identical to 3747330')
            finally:
                old.model_free(old_model)
        if checks - before != REFERENCE_CONTROLS:
            raise AssertionError('reference control count differs from its declared skip count')
        print(f'native 3747330 C0 reference controls: {checks - before}/{REFERENCE_CONTROLS} PASS', flush=True)
        return checks, 0
    finally:
        native.model_free(model)


def epoch_controls(native, assets, use_oracle):
    """Epoch-start independence per profile, and syn-fixture parity for C0 and C5-R4."""
    checks = 0
    def check(value, description):
        nonlocal checks
        if not value:
            raise AssertionError(description)
        checks += 1
    model = c.c_void_p()
    check(native.model_load(c.byref(model), os.fsencode(assets)) == 0 and model.value,
          'validated model loaded for epoch controls')
    try:
        # Two streams whose second and third epochs carry the same audio but
        # whose first epochs differ: every packet and PCM sample from epoch 1
        # on must be identical, so no state crosses an epoch start.
        shared = [tone(frame, 437, 113) for frame in range(25, 75)]
        first_a = [tone(frame, 211, 59) for frame in range(25)]
        first_b = [noise(frame) for frame in range(25)]
        for profile in (C0, C5_R4):
            name = PROFILE_NAMES[profile]
            packets_a, pcm_a = native_stream(native, model, 8, first_a + shared, profile)
            packets_b, pcm_b = native_stream(native, model, 8, first_b + shared, profile)
            check(packets_a[:25] != packets_b[:25] and pcm_a[:25] != pcm_b[:25],
                  f'{name} epoch independence control has differing first epochs')
            check(packets_a[25:] == packets_b[25:],
                  f'native {name} encoder epoch start retains no prior-epoch state')
            check(pcm_a[25:] == pcm_b[25:],
                  f'native {name} decoder epoch start retains no prior-epoch state')
            print(f'native {name} epoch-start independence: 50/50 packets and PCM blocks identical after differing epoch 0: {checks}/{checks} PASS', flush=True)
        if not use_oracle:
            print('native C0 and C5-R4 syn-fixture parity: not run (requires --oracle and the locked export environment)')
            return checks
        import numpy as np
        sys.path.insert(0, str(TOOLS))
        import verify_epoch_programme as programme
        expectations = json.loads(FIXTURE.read_bytes())
        item = next(row for row in expectations['items'] if row['name'] == 'syn-fixture')
        c0_item = json.loads(C0_REFERENCE.read_bytes())['items']['syn-fixture']
        source = programme.to_int16(programme.synthetic_float('syn-fixture'))
        check(hashlib.sha256(programme.wav_bytes(source)).hexdigest() == item['wav_sha256'] == c0_item['wav_sha256'],
              'regenerated syn-fixture equals the programme item')
        frames = [[int(v) for v in source[i*960:(i+1)*960]] for i in range(source.shape[0] // 960)]
        audio = (source.astype(np.float32) / np.float32(32768.0)).reshape(1, 1, -1)
        for profile, expected, label in ((C5_R4, item['renders']['6'], 'checked C5-R4 syn-fixture render'),
                                         (C0, c0_item['renders']['6'], '3747330 C0 syn-fixture reference')):
            name = PROFILE_NAMES[profile]
            packets, pcm = native_stream(native, model, 8, frames, profile)
            oracle = Oracle(assets, 8, profile)
            _, codes = oracle.stream.encode_stream(oracle.encoder, audio)
            decoded = oracle.stream.decode_stream(oracle.decoder, codes)
            check(hashlib.sha256(np.ascontiguousarray(codes).tobytes()).hexdigest() == expected['codes']
                  and hashlib.sha256(np.ascontiguousarray(decoded, dtype=np.float32).tobytes()).hexdigest()
                  == expected['pcm_float32'],
                  f'Python {name} oracle reproduces the {label}')
            native_codes = np.concatenate([np.array(parse_wire(packet, 8)[4], dtype=np.int64).reshape(8, 1, 3)
                                           for packet in packets], axis=-1)
            mismatches = int(np.count_nonzero(native_codes != codes))
            check(mismatches == 0, f'native {name} tokens equal the Python ORT runtime ({mismatches}/{codes.size} differ)')
            native_pcm = np.array(pcm, dtype=np.int32).reshape(-1)
            flat = decoded.reshape(-1)
            rows = {}
            for convention, reference in (('rint_x32768', np.clip(np.rint(flat * 32768.0), -32768, 32767)),
                                          ('round_x32767', np.clip(np.round(flat * 32767.0), -32768, 32767))):
                difference = np.abs(native_pcm - reference.astype(np.int32))
                rows[convention] = {'max_abs_lsb': int(difference.max()),
                                    'identical_samples': int(np.count_nonzero(difference == 0)),
                                    'compared': int(difference.size)}
                check(rows[convention]['max_abs_lsb'] <= 1,
                      f'native {name} PCM within 1 LSB of the Python ORT runtime ({convention}: {rows[convention]})')
            print(f'native {name} syn-fixture parity at 6 kb/s: tokens 0/{codes.size} differ; '
                  f'PCM {json.dumps(rows, sort_keys=True)}: {checks}/{checks} PASS', flush=True)
        return checks
    finally:
        native.model_free(model)


def exercise(library, assets, use_oracle, reference_library):
    native = Native(library)
    checks = 0
    timings = []
    def check(value, description):
        nonlocal checks
        if not value:
            raise AssertionError(description)
        checks += 1
    # Reject substituted manifests, special files, graph symlinks and wrong
    # graph bytes before any graph can enter the model parser.
    with tempfile.TemporaryDirectory(prefix='kenc-assets-') as temporary:
        bad = Path(temporary)
        model = c.c_void_p()
        check(native.model_load(c.byref(model), os.fsencode(bad)) == 2 and not model.value,
              'absent manifest rejected')
        os.symlink(assets/'manifest.json', bad/'manifest.json')
        check(native.model_load(c.byref(model), os.fsencode(bad)) == 2 and not model.value,
              'manifest symlink rejected')
        (bad/'manifest.json').unlink()
        (bad/'manifest.json').write_bytes((assets/'manifest.json').read_bytes())
        os.symlink(assets/'encoder_stateful_op17.onnx',bad/'encoder_stateful_op17.onnx')
        check(native.model_load(c.byref(model), os.fsencode(bad)) == 2 and not model.value,
              'graph symlink rejected')
        (bad/'encoder_stateful_op17.onnx').unlink()
        with (bad/'encoder_stateful_op17.onnx').open('wb') as handle:
            handle.truncate(29720617)
        check(native.model_load(c.byref(model), os.fsencode(bad)) == 2 and not model.value,
              'equal-sized graph substitution rejected')
        (bad/'encoder_stateful_op17.onnx').unlink()
        os.mkfifo(bad/'encoder_stateful_op17.onnx')
        check(native.model_load(c.byref(model), os.fsencode(bad)) == 2 and not model.value,
              'FIFO rejected without blocking')
    checks += negotiation_controls(native)
    for profile in (C0, C5_R4):
        name = PROFILE_NAMES[profile]
        marker = EPOCH_PREROLL if profile == C5_R4 else 0
        for books in (4,8,16):
            model, encoder, twin, decoder, dropped = [c.c_void_p() for _ in range(5)]
            handles = []
            oracle = None
            try:
                check(native.model_load(c.byref(model), os.fsencode(assets)) == 0 and model.value,
                      f'validated model loaded for {books} codebooks')
                options = native.options_default()
                options.codebooks = books
                options.threads = 2 if books == 16 else 1
                for mode,ptr in [('encoder',encoder),('encoder',twin),('decoder',decoder),('decoder',dropped)]:
                    check(getattr(native,mode+'_create')(c.byref(ptr),model,c.byref(options)) == 0 and ptr.value,
                          f'{mode} created')
                    handles.append((mode,ptr))
                    if profile == C5_R4:
                        check(getattr(native, mode+'_set_epoch_start')(ptr, C5_R4) == OK,
                              f'{mode} selects C5-R4 before its first packet')
                native.model_free(model); model = c.c_void_p()
                if use_oracle:
                    oracle = Oracle(assets, books, profile)
                packet_at_zero = None
                for frame in range(27):
                    pcm = [round(12000*math.sin(2*math.pi*437*(frame*960+i)/24000)
                                 + 3000*math.sin(2*math.pi*113*(frame*960+i)/24000)) for i in range(960)]
                    if frame == 0:
                        result,_,written,raw = native.encode(encoder,pcm,0,capacity=1)
                        check(result == 4 and written == 0 and raw == bytes([0xA5])*160,
                              'short encode buffer rejected before output/state mutation')
                    start = time.perf_counter_ns()
                    result,packet,written,_ = native.encode(encoder,pcm,frame*40)
                    timings.append(time.perf_counter_ns()-start)
                    check(result == 0 and written == len(packet), 'native encoder runs')
                    if frame == 0:
                        check(native.encoder_set_epoch_start(encoder, profile) == PROTOCOL,
                              'encoder epoch-start profile cannot change inside a running stream')
                    twin_result,twin_packet,_,_ = native.encode(twin,pcm,frame*40)
                    check(twin_result == 0 and packet == twin_packet, 'independent encoders produce exact same packets')
                    epoch,index,pts,flags,codes = parse_wire(packet,books)
                    # Successor of 'single-owner epoch schedule and timestamps':
                    # RESET packets now also carry the stream's epoch-start marker.
                    check((epoch,index,pts,flags) == (frame//25,frame%25,frame*40,(RESET|marker) if frame%25==0 else 0),
                          f'single-owner epoch schedule, timestamps and {name} epoch-start marker')
                    if frame == 0:
                        packet_at_zero = packet
                        result,output,written,_ = native.decode(decoder,packet,capacity=959)
                        check(result == 1 and written == 0 and output == [12345]*960,
                              'short decode buffer rejected before output/state mutation')
                        result,output,written,_ = native.decode(decoder,packet+b'\x00')
                        check(result == 5 and written == 0 and output == [12345]*960,
                              'trailing data rejected without output/state mutation')
                    start = time.perf_counter_ns()
                    result,output,written,info = native.decode(decoder,packet)
                    timings.append(time.perf_counter_ns()-start)
                    check(result == 0 and written == 960 and (info.pts_ms,info.flags,info.samples)==(pts,flags,960),
                          'native decoder runs and reports exact metadata')
                    if frame == 0:
                        check(native.decoder_set_epoch_start(decoder, profile) == PROTOCOL,
                              'decoder epoch-start profile cannot change after its first packet')
                    if frame != 1:
                        dropped_result,dropped_output,dropped_count,_ = native.decode(dropped,packet)
                        if frame == 0 or frame >= 25:
                            check(dropped_result == 0 and dropped_count == 960 and dropped_output == output,
                                  'late/reset decoder joins with independent state')
                        else:
                            check(dropped_result == 5 and dropped_count == 0 and dropped_output == [12345]*960,
                                  'loss refuses further dependent packets until next reset')
                    if oracle:
                        if flags & RESET:
                            oracle.reset()
                        expected_codes,expected_pcm = oracle.run(pcm)
                        check(codes == expected_codes, f'bit-exact {name} tokens against independent ORT oracle')
                        largest = max(abs(a-b) for a,b in zip(output,expected_pcm))
                        check(largest <= 2, f'independent {name} oracle waveform error <=2 LSB (got {largest})')
                result,_,written,_ = native.decode(decoder,packet_at_zero)
                check(result == 5 and written == 0,'old reset epoch replay rejected')
                native.encoder_reset(encoder)
                result,packet,_,_ = native.encode(encoder,[0]*960,7)
                check(result == 0 and parse_wire(packet,books)[:4] == (2,0,7,RESET|DISCONTINUITY|marker),
                      f'explicit reset advances epoch and carries discontinuity and the {name} marker')
                result,_,written,info = native.decode(decoder,packet)
                check(result == 0 and written == 960 and info.flags == RESET|DISCONTINUITY|marker,
                      'explicit discontinuity resets decoder state')
                native.decoder_reset(decoder)
                result,_,written,_ = native.decode(decoder,packet_at_zero)
                check(result == 0 and written == 960,'explicit decoder reset permits a new source')
                print(f'native {name} {books*0.75:g} kb/s: 27 packets, recovery and reset controls PASS',flush=True)
            finally:
                for mode,ptr in reversed(handles):
                    getattr(native,mode+'_free')(ptr)
                native.model_free(model)
    checks += epoch_controls(native, assets, use_oracle)
    mixed, skipped = mixed_peer_controls(native, assets, reference_library)
    checks += mixed
    timings.sort()
    print(f'native ABI controls: {checks}/{checks} PASS' + (f'; SKIPPED {skipped}' if skipped else ''))
    print(f'unfrozen functional-run timing: calls={len(timings)} p99_ms={timings[int((len(timings)-1)*.99)]/1e6:.3f}')
    print('release qualification: not claimed; frozen capacity, listening, and consumer closure remain separate')


if __name__ == '__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('library',type=Path)
    parser.add_argument('assets',type=Path)
    parser.add_argument('--oracle',action='store_true')
    parser.add_argument('--c0-reference-library',type=Path)
    args=parser.parse_args()
    exercise(args.library,args.assets,args.oracle,args.c0_reference_library)
