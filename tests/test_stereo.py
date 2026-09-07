"""Real native stereo-frame parity with the pinned export and safetensors oracle."""
from __future__ import annotations
import argparse,ctypes as c,math,os,sys,time
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from export_48khz import load_model
from verify_48khz import corpus,native_rvq_decode


def exercise(library,assets,model_dir):
    import numpy as np
    import onnxruntime as ort
    import torch
    torch.set_num_threads(1)
    torch.set_num_interop_threads(1)
    model,_=load_model(model_dir)
    lib=c.CDLL(str(library.resolve()))
    ptr=c.c_void_p
    fp=c.POINTER(c.c_float)
    up=c.POINTER(c.c_uint16)
    lib.kenc_stereo_create.argtypes=[c.POINTER(ptr),c.c_char_p,c.c_uint8,c.c_uint8]
    lib.kenc_stereo_create.restype=c.c_int
    lib.kenc_stereo_free.argtypes=[ptr];lib.kenc_stereo_free.restype=None
    lib.kenc_stereo_encode_frame.argtypes=[ptr,fp,c.c_size_t,up,c.c_size_t,fp]
    lib.kenc_stereo_encode_frame.restype=c.c_int
    lib.kenc_stereo_decode_frame.argtypes=[ptr,up,c.c_size_t,c.c_float,fp,c.c_size_t]
    lib.kenc_stereo_decode_frame.restype=c.c_int
    lib.kenc_rvq_encode_frame.argtypes=[fp,fp,fp,c.c_uint8,fp,up]
    lib.kenc_rvq_encode_frame.restype=c.c_int
    books=np.fromfile(assets/'rvq-codebooks.f32le',dtype='<f4').reshape(16,1024,128)
    # Use the native loader's float32 norm convention. The compared selection
    # oracle remains the unmodified model's quantizer on identical latents.
    norms=np.zeros((16,1024),dtype='float32')
    for dim in range(128):norms+=books[:,:,dim]*books[:,:,dim]
    options=ort.SessionOptions();options.intra_op_num_threads=1;options.inter_op_num_threads=1
    enc=ort.InferenceSession(str(assets/'encoder_frame_op17.onnx'),sess_options=options,providers=['CPUExecutionProvider'])
    dec=ort.InferenceSession(str(assets/'decoder_frame_op17.onnx'),sess_options=options,providers=['CPUExecutionProvider'])
    checks=population=mismatches=identical_population=identical_mismatches=0
    worst=0.;elapsed=[]
    def check(ok,why):
        nonlocal checks
        assert ok,why
        checks+=1
    for count in (2,4,8,16):
        codec=ptr()
        check(lib.kenc_stereo_create(c.byref(codec),os.fsencode(assets),count,1)==0 and codec.value,'stereo context loads exact pinned bundle')
        try:
            for name,audio in corpus().items():
                pcm=np.ascontiguousarray(audio[0].T)
                tokens=np.full(count*150,65535,dtype='uint16')
                scale=c.c_float(-1)
                check(lib.kenc_stereo_encode_frame(codec,pcm.ctypes.data_as(fp),48000,tokens.ctypes.data_as(up),tokens.size-1,c.byref(scale))==4 and np.all(tokens==65535) and scale.value==-1,'short code output has no mutation')
                started=time.perf_counter_ns()
                check(lib.kenc_stereo_encode_frame(codec,pcm.ctypes.data_as(fp),48000,tokens.ctypes.data_as(up),tokens.size,c.byref(scale))==0,'native stereo encoder runs')
                encode_ms=(time.perf_counter_ns()-started)/1e6
                check(np.all(tokens<1024) and math.isfinite(scale.value) and scale.value>0,'bounded native tokens and scale')
                candidate=tokens.astype('int64').reshape(count,1,150)
                latent,reference_scale=enc.run(None,{'audio':audio})
                scratch=np.empty_like(latent)
                selected=np.empty(count*150,dtype='uint16')
                check(lib.kenc_rvq_encode_frame(latent.ctypes.data_as(fp),books.ctypes.data_as(fp),norms.ctypes.data_as(fp),count,scratch.ctypes.data_as(fp),selected.ctypes.data_as(up))==0,'native identical-latent RVQ runs')
                with torch.no_grad():
                    identical=model.quantizer.encode(torch.from_numpy(latent),float(count*1.5)).numpy().reshape(-1)
                    full_codes,full_scale=model._encode_frame(torch.from_numpy(audio),float(count*1.5))
                identical_mismatches+=int(np.count_nonzero(identical!=selected))
                identical_population+=tokens.size
                row_mismatches=int(np.count_nonzero(full_codes.numpy().transpose(1,0,2)!=candidate))
                mismatches+=row_mismatches;population+=tokens.size
                check(row_mismatches*100/tokens.size<=.1,'per-row end-to-end token drift at most 0.1 percent')
                output=np.full((48000,2),12345,dtype='float32')
                check(lib.kenc_stereo_decode_frame(codec,tokens.ctypes.data_as(up),tokens.size,float('nan'),output.ctypes.data_as(fp),output.size)==5 and np.all(output==12345),'nonfinite scale refuses atomically')
                check(lib.kenc_stereo_decode_frame(codec,tokens.ctypes.data_as(up),tokens.size,scale.value,output.ctypes.data_as(fp),output.size-1)==4 and np.all(output==12345),'short PCM refuses atomically')
                started=time.perf_counter_ns()
                check(lib.kenc_stereo_decode_frame(codec,tokens.ctypes.data_as(up),tokens.size,scale.value,output.ctypes.data_as(fp),output.size)==0,'native stereo decoder runs')
                decode_ms=(time.perf_counter_ns()-started)/1e6
                reference=dec.run(None,{'quantized':native_rvq_decode(candidate,books),'scale':np.asarray([[scale.value]],dtype='float32')})[0]
                error=float(np.max(np.abs(reference[0].T-output)))
                worst=max(worst,error)
                check(error<=2e-4,'native decoder parity on identical tokens')
                with torch.no_grad():official=model._decode_frame(full_codes,full_scale).numpy()[0].T
                full_error=float(np.max(np.abs(official-output)))
                check(full_error<=2e-4,'native end-to-end waveform parity with official model')
                print(f'stereo {count*1.5:g} kb/s {name}: waveform_max_abs={error:g} full_max_abs={full_error:g} token_flips={row_mismatches}/{tokens.size} encode_ms={encode_ms:.3f} decode_ms={decode_ms:.3f}',flush=True)
                elapsed.append(decode_ms)
            # Out-of-range PCM and tokens cannot enter the model.
            pcm[0,0]=float('inf')
            check(lib.kenc_stereo_encode_frame(codec,pcm.ctypes.data_as(fp),48000,tokens.ctypes.data_as(up),tokens.size,c.byref(scale))==1,'nonfinite PCM refuses')
            tokens[0]=1024;output.fill(12345)
            check(lib.kenc_stereo_decode_frame(codec,tokens.ctypes.data_as(up),tokens.size,scale.value,output.ctypes.data_as(fp),output.size)==5 and np.all(output==12345),'out-of-range token refuses atomically')
        finally:lib.kenc_stereo_free(codec)
    print(f'stereo identical-latent token mismatches: {identical_mismatches}/{identical_population}',flush=True)
    print(f'stereo end-to-end token mismatches: {mismatches}/{population}',flush=True)
    check(identical_mismatches==0,'bit-exact native RVQ on identical reference latents')
    check(mismatches*100/population<=.1,'end-to-end token drift at most 0.1 percent')
    print(f'stereo controls: {checks}/{checks} PASS; worst waveform {worst:g}')
    print('stereo release qualification: not claimed; file container/overlap, frozen H1 and listening remain separate')

if __name__=='__main__':
    parser=argparse.ArgumentParser()
    for name in ('library','assets','model_dir'):parser.add_argument(name,type=Path)
    args=parser.parse_args();exercise(args.library,args.assets,args.model_dir)
