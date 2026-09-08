"""Create the intentionally synthetic IPC test ZIP, never a model authority."""
import io
from pathlib import Path
import sys
import zipfile

output=Path(sys.argv[1])
output.mkdir(parents=True,exist_ok=True)
buffer=io.BytesIO()
with zipfile.ZipFile(buffer,'w',compression=zipfile.ZIP_STORED) as archive:
    entry=zipfile.ZipInfo('__main__.py',date_time=(1980,1,1,0,0,0))
    archive.writestr(entry,Path(__file__).with_name('content_ipc_peer.py').read_bytes())
payload=buffer.getvalue()
lines=['/* Synthetic C transport fixture, no receipt/model authority. */',
    '#define KENC_CONTENT_COMMIT "synthetic-test-only"',
    '#define KENC_CONTENT_BUNDLE_SHA256 "synthetic-test-only"',
    'static const unsigned char kenc_content_bundle[] = {']
for offset in range(0,len(payload),16):lines.append(','.join(str(v) for v in payload[offset:offset+16])+',')
lines+=['};','']
(output/'content_bundle.h').write_text('\n'.join(lines))
