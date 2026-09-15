"""Exercise the native ABI against real, explicitly supplied pinned graphs.

Run with the locked export environment to compare independent ORT Python and
native C sessions. All PCM in this test is synthetic; no model is downloaded.
The Python oracle is the product epoch-start runtime (tools/epoch_stream.py):
every epoch start primes zeroed state with a four-packet repeat pre-roll.
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
FIXTURE = Path(__file__).resolve().parent / 'fixtures' / 'f101-c5r4-programme.json'


class Options(c.Structure):
    _fields_ = [('sample_rate', c.c_uint32), ('packet_samples', c.c_uint16),
                ('epoch_packets', c.c_uint16), ('codebooks', c.c_uint8),
                ('threads', c.c_uint8)]


class Info(c.Structure):
    _fields_ = [('pts_ms', c.c_uint64), ('flags', c.c_uint8), ('samples', c.c_uint16)]


class Native:
    def __init__(self, path):
        self.lib = c.CDLL(str(path.resolve()))
        ptr = c.c_void_p
        for name, args, result in [
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
        ]:
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

    def __init__(self, assets, books):
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
        self.encoder = epoch_stream.StreamEncoder(self.sessions['encoder'], self.records['encoder'],
            self.sessions['encode'], self.records['encode'])
        self.decoder = epoch_stream.StreamDecoder(self.sessions['decode'], self.records['decode'],
            self.sessions['decoder'], self.records['decoder'])

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


def native_stream(native, model, books, frames):
    """Encode then decode frames through fresh native contexts; returns packets and PCM."""
    options = native.options_default()
    options.codebooks = books
    options.threads = 1
    encoder, decoder = c.c_void_p(), c.c_void_p()
    try:
        if native.encoder_create(c.byref(encoder), model, c.byref(options)) != 0 or \
                native.decoder_create(c.byref(decoder), model, c.byref(options)) != 0:
            raise AssertionError('native epoch-control contexts were not created')
        packets, pcm = [], []
        for index, samples in enumerate(frames):
            result, packet, _, _ = native.encode(encoder, samples, index*40)
            if result != 0:
                raise AssertionError('native epoch-control encode failed')
            result, output, written, _ = native.decode(decoder, packet)
            if result != 0 or written != 960:
                raise AssertionError('native epoch-control decode failed')
            packets.append(packet)
            pcm.append(output)
        return packets, pcm
    finally:
        native.encoder_free(encoder)
        native.decoder_free(decoder)


def epoch_controls(native, assets, use_oracle):
    """Epoch-start independence of the native runtime, and C5-R4 parity on syn-fixture."""
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
        first_b = [[(i * 7919 + frame * 104729) % 20001 - 10000 for i in range(960)] for frame in range(25)]
        packets_a, pcm_a = native_stream(native, model, 8, first_a + shared)
        packets_b, pcm_b = native_stream(native, model, 8, first_b + shared)
        check(packets_a[:25] != packets_b[:25] and pcm_a[:25] != pcm_b[:25],
              'epoch independence control has differing first epochs')
        check(packets_a[25:] == packets_b[25:],
              'native encoder epoch start retains no prior-epoch state')
        check(pcm_a[25:] == pcm_b[25:],
              'native decoder epoch start retains no prior-epoch state')
        print(f'native epoch-start independence: 50/50 packets and PCM blocks identical after differing epoch 0: {checks}/{checks} PASS', flush=True)
        if not use_oracle:
            print('native C5-R4 syn-fixture parity: not run (requires --oracle and the locked export environment)')
            return checks
        import numpy as np
        sys.path.insert(0, str(TOOLS))
        import verify_epoch_programme as programme
        expectations = json.loads(FIXTURE.read_bytes())
        item = next(row for row in expectations['items'] if row['name'] == 'syn-fixture')
        source = programme.to_int16(programme.synthetic_float('syn-fixture'))
        check(hashlib.sha256(programme.wav_bytes(source)).hexdigest() == item['wav_sha256'],
              'regenerated syn-fixture equals the programme item')
        frames = [[int(v) for v in source[i*960:(i+1)*960]] for i in range(source.shape[0] // 960)]
        packets, pcm = native_stream(native, model, 8, frames)
        oracle = Oracle(assets, 8)
        audio = (source.astype(np.float32) / np.float32(32768.0)).reshape(1, 1, -1)
        _, codes = oracle.stream.encode_stream(oracle.encoder, audio)
        decoded = oracle.stream.decode_stream(oracle.decoder, codes)
        check(hashlib.sha256(np.ascontiguousarray(codes).tobytes()).hexdigest() == item['renders']['6']['codes']
              and hashlib.sha256(np.ascontiguousarray(decoded, dtype=np.float32).tobytes()).hexdigest()
              == item['renders']['6']['pcm_float32'],
              'Python oracle reproduces the checked C5-R4 syn-fixture render')
        native_codes = np.concatenate([np.array(parse_wire(packet, 8)[4], dtype=np.int64).reshape(8, 1, 3)
                                       for packet in packets], axis=-1)
        mismatches = int(np.count_nonzero(native_codes != codes))
        check(mismatches == 0, f'native C5-R4 tokens equal the Python ORT runtime ({mismatches}/{codes.size} differ)')
        native_pcm = np.array(pcm, dtype=np.int32).reshape(-1)
        flat = decoded.reshape(-1)
        rows = {}
        for label, expected in (('rint_x32768', np.clip(np.rint(flat * 32768.0), -32768, 32767)),
                                ('round_x32767', np.clip(np.round(flat * 32767.0), -32768, 32767))):
            difference = np.abs(native_pcm - expected.astype(np.int32))
            rows[label] = {'max_abs_lsb': int(difference.max()),
                           'identical_samples': int(np.count_nonzero(difference == 0)),
                           'compared': int(difference.size)}
            check(rows[label]['max_abs_lsb'] <= 1,
                  f'native C5-R4 PCM within 1 LSB of the Python ORT runtime ({label}: {rows[label]})')
        print(f'native C5-R4 syn-fixture parity at 6 kb/s: tokens 0/{codes.size} differ; '
              f'PCM {json.dumps(rows, sort_keys=True)}: {checks}/{checks} PASS', flush=True)
        return checks
    finally:
        native.model_free(model)


def exercise(library, assets, use_oracle):
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
            native.model_free(model); model = c.c_void_p()
            if use_oracle:
                oracle = Oracle(assets, books)
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
                twin_result,twin_packet,_,_ = native.encode(twin,pcm,frame*40)
                check(twin_result == 0 and packet == twin_packet, 'independent encoders produce exact same packets')
                epoch,index,pts,flags,codes = parse_wire(packet,books)
                check((epoch,index,pts,flags) == (frame//25,frame%25,frame*40,1 if frame%25==0 else 0),
                      'single-owner epoch schedule and timestamps')
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
                if frame != 1:
                    dropped_result,dropped_output,dropped_count,_ = native.decode(dropped,packet)
                    if frame == 0 or frame >= 25:
                        check(dropped_result == 0 and dropped_count == 960 and dropped_output == output,
                              'late/reset decoder joins with independent state')
                    else:
                        check(dropped_result == 5 and dropped_count == 0 and dropped_output == [12345]*960,
                              'loss refuses further dependent packets until next reset')
                if oracle:
                    if flags & 1:
                        oracle.reset()
                    expected_codes,expected_pcm = oracle.run(pcm)
                    check(codes == expected_codes, 'bit-exact tokens against independent ORT oracle')
                    largest = max(abs(a-b) for a,b in zip(output,expected_pcm))
                    check(largest <= 2, f'independent oracle waveform error <=2 LSB (got {largest})')
            result,_,written,_ = native.decode(decoder,packet_at_zero)
            check(result == 5 and written == 0,'old reset epoch replay rejected')
            native.encoder_reset(encoder)
            result,packet,_,_ = native.encode(encoder,[0]*960,7)
            check(result == 0 and parse_wire(packet,books)[:4] == (2,0,7,5),
                  'explicit reset advances epoch and carries discontinuity')
            result,_,written,info = native.decode(decoder,packet)
            check(result == 0 and written == 960 and info.flags == 5,
                  'explicit discontinuity resets decoder state')
            native.decoder_reset(decoder)
            result,_,written,_ = native.decode(decoder,packet_at_zero)
            check(result == 0 and written == 960,'explicit decoder reset permits a new source')
            print(f'native {books*0.75:g} kb/s: 27 packets, recovery and reset controls PASS',flush=True)
        finally:
            for mode,ptr in reversed(handles):
                getattr(native,mode+'_free')(ptr)
            native.model_free(model)
    checks += epoch_controls(native, assets, use_oracle)
    timings.sort()
    print(f'native ABI controls: {checks}/{checks} PASS')
    print(f'unfrozen functional-run timing: calls={len(timings)} p99_ms={timings[int((len(timings)-1)*.99)]/1e6:.3f}')
    print('release qualification: not claimed; frozen capacity, listening, and consumer closure remain separate')


if __name__ == '__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('library',type=Path)
    parser.add_argument('assets',type=Path)
    parser.add_argument('--oracle',action='store_true')
    args=parser.parse_args()
    exercise(args.library,args.assets,args.oracle)
