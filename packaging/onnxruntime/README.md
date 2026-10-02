# Debian system-ONNX registration candidate

This dependency patch is for the Debian `onnxruntime 1.21.0+dfsg-1` source,
after its Debian patch series. It addresses duplicate built-in schema
registration when Debian links system ONNX. It is a development candidate;
the EnCodec runtime and selected dependency package remain unchanged.

Debian's [system-ONNX patch](https://sources.debian.org/patches/onnxruntime/1.21.0%2Bdfsg-1/system-onnx.patch/)
uses the distro library, whose built-in schema registry owns lazy registration.
The upstream ORT environment also registers those sets explicitly. The patch
asks the system registry for a known built-in schema instead, enforcing its
availability, while retaining ORT's custom and contributed registrations.
Apply it only to this system-ONNX build; bundled-ONNX builds require their own
registration path. Relevant sources are [ORT's environment](https://github.com/microsoft/onnxruntime/blob/v1.21.0/onnxruntime/core/session/environment.cc)
and [ONNX's schema registry](https://github.com/onnx/onnx/blob/v1.17.0/onnx/defs/schema.cc).

```sh
# Inside an unpacked, exact Debian source with its patch series already applied:
patch --fuzz=0 -p1 < /path/to/debian-system-onnx-single-registration.patch
```

A private Debian 13.5 H1 build of this exact source completed with the CPU
shared-library target, system dependencies, and Python/tests disabled.
Development inference against both receipt-backed installed EnCodec profiles
passed with zero duplicate-schema diagnostics, after verifying the corrected
library's actual process mapping and hash. The first loader experiment lacked
the SONAME symlink and loaded the original library; that failed observation is
retained separately. Library filenames alone are not loader identity evidence.

This is not a packaged dependency selection or qualification result. Before
promotion, build distinct runtime and development Debian packages with their
source/patch/build provenance; select their exact dependency identities in the
native EnCodec closure and OS delivery; then repeat ABI/oracle, strict capacity,
integration and sustained/fault gates. Do not substitute a private library via
`LD_LIBRARY_PATH` for package qualification. The strict C5-R4 mono capacity gate
and full F101 acceptance remain open; thread scheduling experiments still
exceeded the unchanged 20 ms budget. No graph, timing population, epoch,
pre-roll, thread contract, threshold or model terms change in this candidate.
