"""Bounded native library capacity measurement; never whole-release acceptance."""
from __future__ import annotations
import argparse,ctypes as c,hashlib,json,os,platform,resource,sys,time
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tests'))
from test_native import Native,Info
from capacity_fixture import inspect_h1


def sha(path):
    digest=hashlib.sha256()
    with path.open('rb') as file:
        for block in iter(lambda:file.read(1024*1024),b''):digest.update(block)
    return digest.hexdigest()


def duration_statistics(durations):
    """Retain acquisition order and exact integer data for independent replay."""
    if not durations or any(type(value) is not int or value < 0 for value in durations):
        raise ValueError('durations must be a nonempty sequence of nonnegative integer nanoseconds')
    values=sorted(durations)
    rank=(99*len(values)+99)//100
    total=sum(values)
    return {'durations_ns':list(durations),'samples':len(values),
            'p99_nearest_rank':rank,'p99_ns':values[rank-1],
            'total_ns':total,'p99_ms':values[rank-1]/1e6,
            'mean_ms':total/len(values)/1e6}


def measure(args):
    if args.fixture_tier=='h1':
        if args.fixture_runner is None:raise ValueError('--fixture-runner is required for H1')
        fixture=inspect_h1(args.fixture_runner)
    else:fixture={'tier':'unfrozen','architecture':platform.machine(),'kernel':platform.release()}
    native=Native(args.library)
    rows=[]
    def row(mode,rate,durations,deadline):
        statistics=duration_statistics(durations)
        p99=statistics['p99_ms'];mean=statistics['mean_ms']
        record={'operation':mode,'bitrate_kbps':rate,'threads':args.threads,
                **statistics,'deadline_ms':deadline,'capacity_threshold_met':p99<deadline}
        rows.append(record)
        print(f'native {mode} {rate:g} kb/s: {len(durations)} calls p99={p99:.3f} ms mean={mean:.3f} ms threshold={deadline:g} ms',flush=True)
    def require(result):
        if result!=0:raise RuntimeError(f'native call refused with bounded result {result}')
    started=time.monotonic()
    if args.profile=='24k':
        pcm=(c.c_int16*960)(*[(i*137%22000)-11000 for i in range(960)])
        output=(c.c_int16*960)();packet=(c.c_uint8*160)()
        for books in (4,8,16):
            model,encoder,decoder=[c.c_void_p() for _ in range(3)]
            try:
                require(native.model_load(c.byref(model),os.fsencode(args.assets)))
                options=native.options_default();options.codebooks=books;options.threads=args.threads
                require(native.encoder_create(c.byref(encoder),model,c.byref(options)))
                require(native.decoder_create(c.byref(decoder),model,c.byref(options)))
                encoded=[];decoded=[];size=c.c_size_t();count=c.c_size_t();info=Info()
                for index in range(args.warmup+args.samples):
                    before=time.perf_counter_ns()
                    require(native.encoder_push_s16(encoder,pcm,960,index*40,packet,160,c.byref(size)))
                    middle=time.perf_counter_ns()
                    require(native.decoder_pull_s16(decoder,packet,size.value,output,960,c.byref(count),c.byref(info)))
                    after=time.perf_counter_ns()
                    if count.value!=960 or info.pts_ms!=index*40:raise RuntimeError('decoded metadata mismatch')
                    if index>=args.warmup:encoded.append(middle-before);decoded.append(after-middle)
                row('encode',books*.75,encoded,20.)
                row('decode',books*.75,decoded,20.)
            finally:
                native.encoder_free(encoder);native.decoder_free(decoder);native.model_free(model)
    else:
        lib=native.lib
        lib.kenc_stereo_create.argtypes=[c.POINTER(c.c_void_p),c.c_char_p,c.c_uint8,c.c_uint8];lib.kenc_stereo_create.restype=c.c_int
        lib.kenc_stereo_free.argtypes=[c.c_void_p];lib.kenc_stereo_free.restype=None
        lib.kenc_stereo_decode_frame.argtypes=[c.c_void_p,c.POINTER(c.c_uint16),c.c_size_t,c.c_float,c.POINTER(c.c_float),c.c_size_t];lib.kenc_stereo_decode_frame.restype=c.c_int
        output=(c.c_float*96000)()
        for books in (2,4,8,16):
            codec=c.c_void_p();codes=(c.c_uint16*(books*150))(*[(i*31)%1024 for i in range(books*150)])
            try:
                require(lib.kenc_stereo_create(c.byref(codec),os.fsencode(args.assets),books,args.threads))
                decoded=[]
                for index in range(args.warmup+args.samples):
                    before=time.perf_counter_ns()
                    require(lib.kenc_stereo_decode_frame(codec,codes,len(codes),.3,output,len(output)))
                    after=time.perf_counter_ns()
                    if index>=args.warmup:decoded.append(after-before)
                row('decode',books*1.5,decoded,990.)
            finally:lib.kenc_stereo_free(codec)
    runtime_files=sorted({line.split()[-1] for line in Path('/proc/self/maps').read_text().splitlines() if '/libonnxruntime.so.' in line and line.split()[-1].startswith('/')})
    result={'schema':'kilix.encodec.native-capacity/v1','scope':'native-library-pipelines','release_qualified':False,'fixture':fixture,'profile':args.profile,'library_sha256':sha(args.library),'manifest_sha256':sha(args.assets/'manifest.json'),'runtime_libraries':[{'file':Path(path).name,'sha256':sha(Path(path))} for path in runtime_files],'harness_sha256':sha(Path(__file__)),'ffi_helper_sha256':sha(Path(__file__).resolve().parents[1]/'tests'/'test_native.py'),'samples_per_row':args.samples,'warmup_per_row':args.warmup,'elapsed_seconds':time.monotonic()-started,'peak_rss_kib':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,'rows':rows,'capacity_thresholds_met':all(value['capacity_threshold_met'] for value in rows),'remaining_scope':['concurrent KMX workload','integrated transport/consumer latency and wire cost','four-hour healthy and fault soaks','listening acceptance']}
    if args.output:
        descriptor=os.open(args.output,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_CLOEXEC,0o600)
        with os.fdopen(descriptor,'w') as file:json.dump(result,file,indent=2,allow_nan=False,sort_keys=True);file.write('\n')
    print('native capacity scope only; no whole-release qualification claimed',flush=True)
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--library',type=Path,required=True)
    parser.add_argument('--assets',type=Path,required=True)
    parser.add_argument('--profile',choices=('24k','48k'),default='24k')
    parser.add_argument('--threads',type=int,choices=(1,2),default=1)
    parser.add_argument('--samples',type=int)
    parser.add_argument('--warmup',type=int)
    parser.add_argument('--fixture-tier',choices=('unfrozen','h1'),default='unfrozen')
    parser.add_argument('--fixture-runner',type=Path)
    parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    if args.samples is None:args.samples=1000 if args.profile=='24k' else 100
    if args.warmup is None:args.warmup=50 if args.profile=='24k' else 5
    minimum=1000 if args.profile=='24k' else 100
    if not minimum<=args.samples<=1000000 or not 1<=args.warmup<=1000:parser.error('sample/warmup count outside capacity contract')
    measured=measure(args)
    raise SystemExit(0 if measured['capacity_thresholds_met'] else 1)
