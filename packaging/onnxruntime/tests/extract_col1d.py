from pathlib import Path
import hashlib
import sys
r=Path(sys.argv[3])
original=(Path(sys.argv[1])/'onnxruntime/core/util/math_cpu.cc').read_text()
candidate=(Path(sys.argv[2])/'onnxruntime/core/util/math_cpu.cc').read_text()
def function(source,signature):
    i=source.index(signature)
    return source[i:source.index('\n}\n',i)+2]
nextpos=function(original,'static inline bool NextPosition')
generic=function(original,'void Im2col<T, StorageOrder::NCHW>::operator()(\n    const T* data_im,\n    const int64_t* im_shape,')
generic=generic.replace('void Im2col<T, StorageOrder::NCHW>::operator()', 'void im2col_generic').replace('const T*','const float*').replace('T* data_col','float* data_col').replace('T padding_value','float padding_value = 0')
col2=function(original,'void Col2im<float, CPUMathUtil, StorageOrder::NCHW>')
col2=col2.replace('void Col2im<float, CPUMathUtil, StorageOrder::NCHW>','void col2im_2d')
nd=[]
for s,name in [(original,'reference'),(candidate,'actual')]:
    x=function(s,'void Col2imNd<float, CPUMathUtil, StorageOrder::NCHW>')
    x=x.replace('void Col2imNd<float, CPUMathUtil, StorageOrder::NCHW>','void '+name)
    x=x.replace('Im2col<float, StorageOrder::NCHW>()','im2col_generic').replace('Col2im<float, CPUMathUtil, StorageOrder::NCHW>','col2im_2d')
    nd.append(x)
preamble='''#include <algorithm>
#include <array>
#include <cassert>
#include <cstdint>
#include <cstddef>
#include <cstdio>
#include <cstring>
#include <functional>
#include <numeric>
#include <vector>
#define ORT_ENFORCE(x) assert(x)
struct CPUMathUtil {};
template<typename T, typename U> T narrow(U v) { return static_cast<T>(v); }
bool is_a_ge_zero_and_a_lt_b(int64_t a, int64_t b) { return a >= 0 && a < b; }
template<typename T, typename U> void Set(ptrdiff_t n, T value, T* out, U*) { std::fill_n(out,n,value); }
'''
main=r'''
int main() {
 uint32_t seed=0x6548932;
 auto value=[&]() { seed^=seed<<13;seed^=seed>>17;seed^=seed<<5;return float(int32_t(seed%20001)-10000)/4096.f; };
 size_t checked=0;
 for (int64_t channels : {1,2,3,16,32})
 for (int64_t input : {1,2,3,7,24,120})
 for (int64_t kernel : {1,2,3,4,8,10,16})
 for (int64_t stride : {1,2,4,5,8})
 for (int64_t dilation : {1,2,3})
 for (int64_t pad : {0,1,2,7})
 for (int64_t extra : {0,1,3}) {
   int64_t output=(input-1)*stride+(kernel-1)*dilation+1-2*pad+extra;
   if(output<=0) continue;
   std::vector<float> col(channels*kernel*input), ref(channels*output+16, 934.f), got(ref);
   for(auto& x:col)x=value();
   reference(col.data(),&output,&input,channels*kernel,channels*output,&kernel,&stride,&dilation,&pad,1,ref.data()+8,nullptr);
   actual(col.data(),&output,&input,channels*kernel,channels*output,&kernel,&stride,&dilation,&pad,1,got.data()+8,nullptr);
   if(std::memcmp(ref.data(),got.data(),ref.size()*sizeof(float))){
      std::fprintf(stderr,"Mismatch C=%ld I=%ld K=%ld S=%ld D=%ld P=%ld E=%ld\n",channels,input,kernel,stride,dilation,pad,extra);return 1;
   }
   ++checked;
 }
 std::printf("PASS: %zu exact one-dimensional scatter comparisons including guards and fallback cases\n",checked);
}
'''
(r/'test_col1d.cpp').write_text('// Extracted unchanged 1.21 functions vs candidate dispatch; original SHA256 '+hashlib.sha256(original.encode()).hexdigest()+'\n'+preamble+'\n'+nextpos+'\n'+generic+'\n'+col2+'\n'+'\n'.join(nd)+main)
