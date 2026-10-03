"""Compare patched kernels with untouched Debian ORT 1.21 source under sanitizers.

Usage: python3 run.py ORIGINAL_ORT_SOURCE PATCHED_ORT_SOURCE EMPTY_OUTPUT_DIR
No model files, network access, or ORT library build are needed.
"""
import argparse
from pathlib import Path
import shutil
import subprocess
import sys

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('original', type=Path)
p.add_argument('patched', type=Path)
p.add_argument('output', type=Path)
a = p.parse_args()
a.original = a.original.resolve()
a.patched = a.patched.resolve()
a.output = a.output.resolve()
a.output.mkdir(exist_ok=True)
if any(a.output.iterdir()):
    p.error('output directory must be empty')
here = Path(__file__).resolve().parent

def run(args):
    subprocess.run(list(map(str, args)), cwd=a.output, check=True)

source = (a.patched / 'onnxruntime/core/mlas/lib/sgemm.cpp').read_text()
start = source.index('#if defined(MLAS_TARGET_AMD64) && !defined(FORCE_GENERIC_ALGORITHMS)\ntemplate<size_t Rows')
end = source.index('\n#endif', start) + len('\n#endif')
(a.output / 'narrow-kernel.inc').write_text(source[start:end] + '\n')
shutil.copyfile(here / 'test_sse2_tail.cpp', a.output / 'test_sse2_tail.cpp')
asm_dir = a.original / 'onnxruntime/core/mlas/lib/x86_64'
run(['cc', '-c', '-I' + str(asm_dir), asm_dir / 'SgemmKernelSse2.S', '-o', 'reference-sse2.o'])
run(['cc', '-c', '-I' + str(asm_dir),
     '-DMlasGemmFloatKernelSse=MlasGemmFloatKernelSsePatched',
     a.patched / 'onnxruntime/core/mlas/lib/x86_64/SgemmKernelSse2.S', '-o', 'patched-sse2.o'])
flags = ['c++', '-O2', '-g', '-std=c++17', '-Wall', '-Wextra', '-Werror',
         '-fsanitize=address,undefined', '-fno-omit-frame-pointer']
run(flags + ['test_sse2_tail.cpp', 'reference-sse2.o', 'patched-sse2.o', '-o', 'test-sse2'])
run([a.output / 'test-sse2'])
run([sys.executable, here / 'extract_col1d.py', a.original, a.patched, a.output])
run(flags + ['test_col1d.cpp', '-o', 'test-col1d'])
run([a.output / 'test-col1d'])
