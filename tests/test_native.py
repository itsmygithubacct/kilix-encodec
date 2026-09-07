"""Exercise the native ABI against real, explicitly supplied pinned graphs.

Run with the locked export environment to compare independent ORT Python and
native C sessions. All PCM in this test is synthetic; no model is downloaded.
"""
from __future__ import annotations

import argparse
import ctypes as c
import json
import math
import os
from pathlib import Path
import tempfile
import time


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
    def __init__(self, assets, books):
        import numpy as np
        import onnxruntime as ort
        self.np = np
        manifest = json.loads((assets / 'manifest.json').read_text())
        rate = {4:3, 8:6, 16:12}[books]
        self.records = {name:manifest['graphs'][key] for name,key in [
            ('encoder','encoder'), ('decoder','decoder'),
            ('encode',f'rvq_encode_{rate}kbps'), ('decode',f'rvq_decode_{rate}kbps')]}
        options = ort.SessionOptions()
        options.intra_op_num_threads = 1
        options.inter_op_num_threads = 1
        self.sessions = {name:ort.InferenceSession(str(assets / record['file']),
            sess_options=options, providers=['CPUExecutionProvider'])
            for name,record in self.records.items()}
        self.reset()

    def reset(self):
        self.states = {name:[self.np.zeros(row['shape'], dtype='float32')
            for row in self.records[name]['state_inputs']] for name in ('encoder','decoder')}

    def stateful(self, name, value):
        record = self.records[name]
        inputs = {record['input_name']: value}
        inputs.update({row['name']:state for row,state in zip(record['state_inputs'],self.states[name])})
        result = self.sessions[name].run(None, inputs)
        self.states[name] = result[1:]
        return result[0]

    def run(self, pcm):
        np = self.np
        audio = np.asarray(pcm, dtype='float32').reshape(1,1,960) / 32768
        latent = self.stateful('encoder', audio)
        codes = self.sessions['encode'].run(None, {'latent':latent})[0]
        quantized = self.sessions['decode'].run(None, {'codes':codes})[0]
        decoded = self.stateful('decoder', quantized)
        samples = np.clip(np.rint(decoded.reshape(-1)*32768), -32768, 32767).astype('int16')
        return codes[:,0,:].tolist(), samples.tolist()


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
