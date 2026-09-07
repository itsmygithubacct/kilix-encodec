"""Streaming overlap-add against the retained complete-file NumPy oracle."""
from __future__ import annotations

import ctypes as c
import json
from pathlib import Path
import sys

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from verify_48khz import linear_overlap_add


class Overlap(c.Structure):
    _fields_ = [('tail', c.c_float * 960), ('primed', c.c_int)]


def main(library):
    lib = c.CDLL(str(library.resolve()))
    lib.kenc_stereo_overlap_reset.argtypes = [c.POINTER(Overlap)]
    lib.kenc_stereo_overlap_reset.restype = None
    lib.kenc_stereo_overlap_apply.argtypes = [c.POINTER(Overlap), c.POINTER(c.c_float), c.c_size_t]
    lib.kenc_stereo_overlap_apply.restype = c.c_int
    rng = np.random.default_rng(112305)
    fixtures = {
        'silence': np.zeros((3, 2, 48000), dtype=np.float32),
        'noise': rng.uniform(-1, 1, (3, 2, 48000)).astype(np.float32),
        'opposite_frames': np.array([1, -1, 1], dtype=np.float32)[:, None, None]
            * np.ones((3, 2, 48000), dtype=np.float32),
    }
    rows = []
    for name, frames in fixtures.items():
        reference = linear_overlap_add(list(frames))
        state = Overlap()
        lib.kenc_stereo_overlap_reset(c.byref(state))
        actual = []
        for frame in frames:
            pcm = frame.T.copy()
            assert lib.kenc_stereo_overlap_apply(c.byref(state), pcm.ctypes.data_as(c.POINTER(c.c_float)), pcm.size) == 0
            actual.append(pcm[:47520].T)
        output = np.concatenate(actual, axis=1)
        maximum = float(np.max(np.abs(output - reference[:, :output.shape[1]])))
        assert maximum <= 0.000001, (name, maximum)
        # Seeking to a later frame requires one preceding frame of pre-roll.
        lib.kenc_stereo_overlap_reset(c.byref(state))
        for frame in frames[1:]:
            pcm = frame.T.copy()
            assert lib.kenc_stereo_overlap_apply(c.byref(state), pcm.ctypes.data_as(c.POINTER(c.c_float)), pcm.size) == 0
        assert np.array_equal(pcm[:47520].T, output[:, 2 * 47520:])
        rows.append({'fixture': name, 'compared_scalars': int(output.size),
                     'maximum_absolute_error': maximum, 'seek_preroll_exact': True})
    print(json.dumps({'scope': 'overlap-and-preroll-only', 'rows': rows}, sort_keys=True, indent=2))


if __name__ == '__main__':
    main(Path(sys.argv[1]))
