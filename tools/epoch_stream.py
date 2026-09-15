#!/usr/bin/env python3
"""Epoch-reset streaming runtime for the 24 kHz stateful graphs.

An epoch is ``EPOCH_PACKETS`` packets (1 s); the stream start is an epoch
start too. Each stream selects one epoch-start profile (owner decision OD-AT):

- ``C0``: at an epoch start the encoder and the decoder set all streaming
  state to zero and process packet 0 from there. This is the 0.2.1 behaviour,
  and the profile of every stream, peer or file without a marker.
- ``C5-R4`` (owner decision OD-AL): after zeroing, each side runs
  ``PREROLL_PACKETS`` lead-in packets through the unchanged per-packet graph,
  discards the lead-in outputs, and then processes packet 0 from the primed
  state. Encoder lead-in: packet 0's 960 audio samples tiled
  ``PREROLL_PACKETS`` times. Decoder lead-in: packet 0's 3 code frames tiled
  ``PREROLL_PACKETS`` times, each lead-in packet decoded through the RVQ
  graph. Only the epoch's own packet 0 is used, so no packet is delayed and no
  future packet is read: the added algorithmic latency is zero.

Peers negotiate: C5-R4 is used only when both advertise it; otherwise both use
C0, so decoded audio always matches the encoder's. The marker values, the
advertisement bits and the file header fields below mirror the C API
(``kenc_epoch_start``) and FILE-FORMAT.md version 2.

The graphs themselves keep their all-zero initial-state contract.

This module needs only NumPy and ONNX Runtime sessions supplied by the caller.
The native C runtime implements the same behaviour in ``src/onnx.c``.
"""

from __future__ import annotations

import argparse
from typing import Any, Callable, Iterable, Sequence

EPOCH_PACKETS = 25
PREROLL_PACKETS = 4
PACKET_SAMPLES = 960
PACKET_LATENT_FRAMES = 3

EPOCH_START_C0 = "C0"
EPOCH_START_C5_R4 = "C5-R4"
#: Selected-profile marker values (``kenc_epoch_start``; file marker byte).
EPOCH_START_MARKERS = {EPOCH_START_C0: 0, EPOCH_START_C5_R4: 1}
#: Lead-in runs per side at every epoch start.
EPOCH_START_PREROLL = {EPOCH_START_C0: 0, EPOCH_START_C5_R4: PREROLL_PACKETS}
#: Advertisement with one bit per supported profile (``kenc_epoch_start_supported``).
SUPPORTED_EPOCH_STARTS = sum(1 << value for value in EPOCH_START_MARKERS.values())
FILE_HEADER_BYTES = 64
FILE_MARKER_OFFSET = 42

#: Machine-readable description of the C5-R4 profile, recorded in verification results.
EPOCH_START = {
    "name": "repeat-pre-roll",
    "profile": EPOCH_START_C5_R4,
    "marker": EPOCH_START_MARKERS[EPOCH_START_C5_R4],
    "epoch_packets": EPOCH_PACKETS,
    "preroll_packets": PREROLL_PACKETS,
    "state_before_preroll": "all-zero",
    "encoder_lead_in": "epoch packet 0 audio tiled preroll_packets times",
    "decoder_lead_in": "epoch packet 0 code frames tiled preroll_packets times",
    "lead_in_outputs": "discarded",
    "stream_start_is_epoch_start": True,
    "added_latency_packets": 0,
    "unmarked_streams": EPOCH_START_C0,
}


def epoch_start_preroll(profile: str) -> int:
    """Lead-in runs of a known profile; anything else is refused."""

    if not isinstance(profile, str) or profile not in EPOCH_START_PREROLL:
        raise ValueError(
            f"unknown epoch-start profile {profile!r}; supported: {', '.join(EPOCH_START_MARKERS)}"
        )
    return EPOCH_START_PREROLL[profile]


def epoch_start_from_marker(marker: int | None) -> str:
    """The profile a selected-profile marker names. An absent marker is C0."""

    if marker is None:
        return EPOCH_START_C0
    if isinstance(marker, bool) or not isinstance(marker, int):
        raise ValueError(f"malformed epoch-start profile marker {marker!r}")
    for name, value in EPOCH_START_MARKERS.items():
        if marker == value:
            return name
    raise ValueError(
        f"unknown epoch-start profile marker {marker}; supported: "
        + ", ".join(f"{name} ({value})" for name, value in EPOCH_START_MARKERS.items())
    )


def epoch_start_advertisement(profiles: Iterable[str]) -> int:
    """Advertisement bits for the given known profiles; C0 is always included."""

    mask = 1 << EPOCH_START_MARKERS[EPOCH_START_C0]
    for profile in profiles:
        epoch_start_preroll(profile)
        mask |= 1 << EPOCH_START_MARKERS[profile]
    return mask


def negotiate_epoch_start(local: int, peer: int | None) -> str:
    """Select the profile both sides use (mirrors ``kenc_epoch_start_negotiate``).

    ``local`` must include C0 and only supported profiles. A peer that sent no
    advertisement (``None`` or 0) gets C0. A nonzero peer advertisement without
    C0, or outside 32 bits, is malformed and refused. Peer bits for profiles
    unknown here are ignored. C5-R4 only when both advertise it.
    """

    c0 = 1 << EPOCH_START_MARKERS[EPOCH_START_C0]
    c5_r4 = 1 << EPOCH_START_MARKERS[EPOCH_START_C5_R4]
    if (
        isinstance(local, bool)
        or not isinstance(local, int)
        or local < 0
        or not local & c0
        or local & ~SUPPORTED_EPOCH_STARTS
    ):
        raise ValueError("local epoch-start advertisement must include C0 and only supported profiles")
    if peer is None or (not isinstance(peer, bool) and isinstance(peer, int) and peer == 0):
        return EPOCH_START_C0
    if isinstance(peer, bool) or not isinstance(peer, int) or not 0 < peer <= 0xFFFFFFFF or not peer & c0:
        raise ValueError(f"malformed peer epoch-start advertisement {peer!r}: every peer supports C0")
    return EPOCH_START_C5_R4 if local & peer & c5_r4 else EPOCH_START_C0


def epoch_start_from_file_header(header: bytes) -> str:
    """Epoch-start profile of a FILE-FORMAT.md version 1 or 2 header.

    Checks only the fields that carry the profile: magic, version, the marker
    byte and, for a marked header, the 24 kHz mono profile. The native reader
    validates the rest of the header.
    """

    if len(header) != FILE_HEADER_BYTES or header[:4] != b"KENC" or header[5:8] != b"\r\n\x1a":
        raise ValueError("not a kilix-encodec file header")
    if header[4] == 1:
        if header[FILE_MARKER_OFFSET] != 0:
            raise ValueError("version 1 header has a nonzero reserved byte where version 2 keeps its marker")
        return EPOCH_START_C0
    if header[4] != 2:
        raise ValueError(f"unsupported kilix-encodec file format version {header[4]}")
    profile = epoch_start_from_marker(header[FILE_MARKER_OFFSET])
    if profile == EPOCH_START_C0:
        raise ValueError("version 2 header must name a non-C0 epoch-start profile; C0 files are version 1")
    if header[8] != 1:
        raise ValueError("an epoch-start marker requires the 24 kHz mono file profile")
    return profile


def tile(value: Any, count: int) -> Any:
    """``count`` copies of ``value`` joined along the time (last) axis."""

    import numpy as np

    if count < 0:
        raise ValueError("lead-in count must not be negative")
    if count == 0:
        return value[..., :0]
    return np.ascontiguousarray(np.concatenate([value] * count, axis=-1))


def lead_in_audio(packet: Any, count: int = PREROLL_PACKETS) -> Any:
    if packet.shape[-1] != PACKET_SAMPLES:
        raise ValueError("encoder lead-in needs exactly one 960-sample packet")
    return tile(packet, count)


def lead_in_codes(codes: Any, count: int = PREROLL_PACKETS) -> Any:
    if codes.shape[-1] != PACKET_LATENT_FRAMES:
        raise ValueError("decoder lead-in needs exactly one 3-frame packet")
    return tile(codes, count)


def zero_states(record: dict[str, Any]) -> list[Any]:
    import numpy as np

    return [
        np.zeros(tuple(item["shape"]), dtype=np.float32)
        for item in record["state_inputs"]
    ]


def run_stateful(
    runtime: Any, record: dict[str, Any], value: Any, states: Sequence[Any]
) -> tuple[Any, list[Any]]:
    import numpy as np

    feed = {record["input_name"]: np.ascontiguousarray(value)}
    feed.update(
        {
            item["name"]: state
            for item, state in zip(record["state_inputs"], states)
        }
    )
    outputs = runtime.run(None, feed)
    return outputs[0], list(outputs[1:])


def run_stateless(runtime: Any, record: dict[str, Any], value: Any) -> Any:
    import numpy as np

    return runtime.run(None, {record["input_name"]: np.ascontiguousarray(value)})[0]


def _lead_in_runs(profile: str, preroll: int | None) -> int:
    runs = epoch_start_preroll(profile)
    if preroll is None:
        return runs
    # Graph-level override for controls; product streams select a profile.
    if isinstance(preroll, bool) or not isinstance(preroll, int) or preroll < 0:
        raise ValueError("pre-roll must not be negative")
    return preroll


class StreamEncoder:
    """Per-packet encoder: stateful encoder graph, then the RVQ encode graph."""

    def __init__(
        self,
        encoder: Any,
        encoder_record: dict[str, Any],
        rvq: Any,
        rvq_record: dict[str, Any],
        *,
        profile: str = EPOCH_START_C0,
        preroll: int | None = None,
    ):
        self.preroll = _lead_in_runs(profile, preroll)
        self.profile = profile
        self.encoder = encoder
        self.encoder_record = encoder_record
        self.rvq = rvq
        self.rvq_record = rvq_record
        self._states: list[Any] | None = None

    def reset(self) -> None:
        """Mark an epoch start; the next packet primes fresh zero state."""

        self._states = None

    def push(self, packet: Any) -> tuple[Any, Any]:
        if packet.shape[-1] != PACKET_SAMPLES:
            raise ValueError("encoder packet must hold 960 samples")
        if self._states is None:
            states = zero_states(self.encoder_record)
            lead = lead_in_audio(packet, self.preroll)
            for index in range(self.preroll):
                _, states = run_stateful(
                    self.encoder,
                    self.encoder_record,
                    lead[..., index * PACKET_SAMPLES : (index + 1) * PACKET_SAMPLES],
                    states,
                )
            self._states = states
        latent, self._states = run_stateful(
            self.encoder, self.encoder_record, packet, self._states
        )
        return latent, run_stateless(self.rvq, self.rvq_record, latent)


class StreamDecoder:
    """Per-packet decoder: the RVQ decode graph, then the stateful decoder graph."""

    def __init__(
        self,
        rvq: Any,
        rvq_record: dict[str, Any],
        decoder: Any,
        decoder_record: dict[str, Any],
        *,
        profile: str = EPOCH_START_C0,
        preroll: int | None = None,
    ):
        self.preroll = _lead_in_runs(profile, preroll)
        self.profile = profile
        self.rvq = rvq
        self.rvq_record = rvq_record
        self.decoder = decoder
        self.decoder_record = decoder_record
        self._states: list[Any] | None = None

    def reset(self) -> None:
        self._states = None

    def pull(self, codes: Any) -> Any:
        if codes.shape[-1] != PACKET_LATENT_FRAMES:
            raise ValueError("decoder packet must hold 3 code frames")
        if self._states is None:
            states = zero_states(self.decoder_record)
            lead = lead_in_codes(codes, self.preroll)
            for index in range(self.preroll):
                quantized = run_stateless(
                    self.rvq,
                    self.rvq_record,
                    lead[
                        ...,
                        index * PACKET_LATENT_FRAMES : (index + 1) * PACKET_LATENT_FRAMES,
                    ],
                )
                _, states = run_stateful(
                    self.decoder, self.decoder_record, quantized, states
                )
            self._states = states
        quantized = run_stateless(self.rvq, self.rvq_record, codes)
        audio, self._states = run_stateful(
            self.decoder, self.decoder_record, quantized, self._states
        )
        return audio


def epoch_starts(packets: int, epoch_packets: int | None) -> list[int]:
    """Packet indices where state restarts; ``None`` means the start only."""

    if packets <= 0:
        return []
    if epoch_packets is None:
        return [0]
    if epoch_packets <= 0:
        raise ValueError("epoch length must be positive")
    return list(range(0, packets, epoch_packets))


def encode_stream(
    encoder: StreamEncoder, audio: Any, epoch_packets: int | None = EPOCH_PACKETS
) -> tuple[Any, Any]:
    """Encode whole packets of ``audio`` [1, 1, N]; returns (latent, codes)."""

    import numpy as np

    packets = audio.shape[-1] // PACKET_SAMPLES
    starts = set(epoch_starts(packets, epoch_packets))
    latents, codes = [], []
    for index in range(packets):
        if index in starts:
            encoder.reset()
        latent, code = encoder.push(
            audio[..., index * PACKET_SAMPLES : (index + 1) * PACKET_SAMPLES]
        )
        latents.append(latent)
        codes.append(code)
    if not latents:
        raise ValueError("stream holds no whole packet")
    return np.concatenate(latents, axis=-1), np.concatenate(codes, axis=-1)


def decode_stream(
    decoder: StreamDecoder, codes: Any, epoch_packets: int | None = EPOCH_PACKETS
) -> Any:
    """Decode whole packets of ``codes`` [n_q, 1, T]; returns audio [1, 1, S]."""

    import numpy as np

    packets = codes.shape[-1] // PACKET_LATENT_FRAMES
    starts = set(epoch_starts(packets, epoch_packets))
    pieces = []
    for index in range(packets):
        if index in starts:
            decoder.reset()
        pieces.append(
            decoder.pull(
                codes[
                    ...,
                    index * PACKET_LATENT_FRAMES : (index + 1) * PACKET_LATENT_FRAMES,
                ]
            )
        )
    if not pieces:
        raise ValueError("stream holds no whole packet")
    return np.concatenate(pieces, axis=-1)


def added_lookahead_packets(
    render: Callable[[Any], Any],
    source: Any,
    probes: Sequence[int],
    noise: Callable[[int], Any],
    depths: Sequence[int] = (0, 1, 2, 3),
) -> int | None:
    """Smallest d such that output packets <= p0 ignore source packets > p0 + d.

    ``render`` maps source audio [1, 1, N] to decoded audio [1, 1, N]. Source
    packets from p0 + d + 1 onward are replaced by ``noise(count)``; the output
    up to and including packet p0 must stay bit-identical at every probe.
    """

    import numpy as np

    baseline = {}
    for p0 in probes:
        segment = np.ascontiguousarray(source[..., : (p0 + 8) * PACKET_SAMPLES])
        output = render(segment)
        if output.shape[-1] < (p0 + 1) * PACKET_SAMPLES:
            raise ValueError("rendering is too short for the probe")
        baseline[p0] = (segment, output)
    for depth in depths:
        identical = True
        for p0, (segment, output) in baseline.items():
            perturbed = segment.copy()
            start = (p0 + depth + 1) * PACKET_SAMPLES
            perturbed[..., start:] = noise(perturbed.shape[-1] - start)
            rendered = render(perturbed)
            identical = identical and bool(
                np.array_equal(
                    output[..., : (p0 + 1) * PACKET_SAMPLES],
                    rendered[..., : (p0 + 1) * PACKET_SAMPLES],
                )
            )
        if identical:
            return depth
    return None


class _FakeGraph:
    """Deterministic stand-in with the graph calling convention (self-test only)."""

    def __init__(self, kind: str):
        self.kind = kind
        self.calls: list[tuple[float, ...]] = []

    def run(self, _outputs: object, feed: dict[str, Any]) -> list[Any]:
        import numpy as np

        if self.kind == "encoder":
            value, state = feed["audio"], feed["state_in_context"]
            self.calls.append((float(value.sum()), float(state.sum())))
            summary = np.full((1, 1, 3), value.mean() + state.mean(), dtype=np.float32)
            return [summary, state * np.float32(0.5) + value.mean()]
        if self.kind == "decoder":
            value, state = feed["quantized"], feed["state_in_context"]
            self.calls.append((float(value.sum()), float(state.sum())))
            audio = np.full((1, 1, PACKET_SAMPLES), value.mean() + state.mean(), dtype=np.float32)
            return [audio, state * np.float32(0.5) + value.mean()]
        if self.kind == "rvq_encode":
            latent = feed["latent"]
            return [np.round(latent * 10).astype(np.int64).reshape(1, 1, 3)]
        codes = feed["codes"]
        self.calls.append((float(codes.sum()),))
        return [codes.astype(np.float32).reshape(1, 1, 3) / np.float32(10)]


def self_test() -> None:
    """Model-free checks of lead-in construction and epoch scheduling."""

    import numpy as np

    checks = 0

    def check(ok: bool, reason: str) -> None:
        nonlocal checks
        if not ok:
            raise AssertionError(reason)
        checks += 1

    packet = np.arange(PACKET_SAMPLES, dtype=np.float32).reshape(1, 1, -1)
    lead = lead_in_audio(packet)
    check(lead.shape == (1, 1, PACKET_SAMPLES * PREROLL_PACKETS), "audio lead-in length")
    check(
        all(
            np.array_equal(lead[..., i * PACKET_SAMPLES : (i + 1) * PACKET_SAMPLES], packet)
            for i in range(PREROLL_PACKETS)
        ),
        "audio lead-in repeats packet 0 in order",
    )
    codes = np.arange(24, dtype=np.int64).reshape(8, 1, 3)
    code_lead = lead_in_codes(codes)
    check(
        code_lead.shape == (8, 1, 12)
        and np.array_equal(code_lead[..., 9:12], codes)
        and np.array_equal(code_lead[..., 0:3], codes),
        "code lead-in repeats packet 0 frames in order",
    )
    check(epoch_starts(60, 25) == [0, 25, 50], "epoch starts every 25 packets")
    check(epoch_starts(60, None) == [0], "a stream without epochs starts once")

    state_record = [{"name": "state_in_context", "shape": [1, 1, 2]}]
    enc_record = {"input_name": "audio", "state_inputs": state_record}
    dec_record = {"input_name": "quantized", "state_inputs": state_record}
    rvq_enc_record = {"input_name": "latent"}
    rvq_dec_record = {"input_name": "codes"}
    audio = np.concatenate(
        [np.full((1, 1, PACKET_SAMPLES), float(i + 1), dtype=np.float32) for i in range(30)],
        axis=-1,
    )
    graph = _FakeGraph("encoder")
    encoder = StreamEncoder(
        graph, enc_record, _FakeGraph("rvq_encode"), rvq_enc_record, profile=EPOCH_START_C5_R4
    )
    encode_stream(encoder, audio)
    # 30 packets + 4 lead-in runs at packet 0 and at packet 25.
    check(len(graph.calls) == 30 + 2 * PREROLL_PACKETS, "encoder lead-in run count")
    first = [call for call in graph.calls[: PREROLL_PACKETS + 1]]
    check(
        all(call[0] == PACKET_SAMPLES * 1.0 for call in first) and first[0][1] == 0.0,
        "stream start primes from zero state with packet 0 audio",
    )
    reset_at = 25 + PREROLL_PACKETS
    boundary = graph.calls[reset_at : reset_at + PREROLL_PACKETS + 1]
    check(
        all(call[0] == PACKET_SAMPLES * 26.0 for call in boundary)
        and boundary[0][1] == 0.0,
        "epoch start primes from zero state with its own packet 0 audio",
    )
    # Successor of "zero pre-roll runs no lead-in": the default profile is C0.
    zero_graph = _FakeGraph("encoder")
    encode_stream(StreamEncoder(zero_graph, enc_record, _FakeGraph("rvq_encode"), rvq_enc_record), audio)
    check(
        len(zero_graph.calls) == 30
        and [call[1] for call in (zero_graph.calls[0], zero_graph.calls[25])] == [0.0, 0.0],
        "C0 (the default profile) runs no lead-in and restarts from zero state at every epoch",
    )
    c0_decoder = _FakeGraph("decoder")
    decode_stream(
        StreamDecoder(_FakeGraph("rvq_decode"), rvq_dec_record, c0_decoder, dec_record, profile=EPOCH_START_C0),
        np.concatenate([np.full((1, 1, 3), i + 1, dtype=np.int64) for i in range(30)], axis=-1),
    )
    check(len(c0_decoder.calls) == 30, "C0 decoder runs no lead-in")

    dec_graph = _FakeGraph("decoder")
    rvq_graph = _FakeGraph("rvq_decode")
    stream_codes = np.concatenate(
        [np.full((1, 1, 3), i + 1, dtype=np.int64) for i in range(30)], axis=-1
    )
    decode_stream(
        StreamDecoder(rvq_graph, rvq_dec_record, dec_graph, dec_record, profile=EPOCH_START_C5_R4),
        stream_codes,
    )
    check(len(dec_graph.calls) == 30 + 2 * PREROLL_PACKETS, "decoder lead-in run count")
    check(
        len(rvq_graph.calls) == 30 + 2 * PREROLL_PACKETS
        and all(call[0] == 3.0 for call in rvq_graph.calls[: PREROLL_PACKETS + 1])
        and all(call[0] == 26 * 3.0 for call in rvq_graph.calls[reset_at : reset_at + PREROLL_PACKETS + 1]),
        "decoder lead-in decodes packet 0 codes through RVQ at every epoch start",
    )

    # Profile markers, advertisement and negotiation (OD-AT).
    c0_bit, c5_bit = 1, 2
    check(SUPPORTED_EPOCH_STARTS == c0_bit | c5_bit, "supported advertisement is C0 and C5-R4")
    check(
        epoch_start_advertisement([EPOCH_START_C5_R4]) == c0_bit | c5_bit
        and epoch_start_advertisement([]) == c0_bit,
        "advertisements always include C0",
    )
    check(
        epoch_start_from_marker(None) == EPOCH_START_C0
        and epoch_start_from_marker(0) == EPOCH_START_C0
        and epoch_start_from_marker(1) == EPOCH_START_C5_R4,
        "absent marker is C0; markers 0 and 1 name C0 and C5-R4",
    )
    negotiated = [
        negotiate_epoch_start(SUPPORTED_EPOCH_STARTS, SUPPORTED_EPOCH_STARTS),
        negotiate_epoch_start(SUPPORTED_EPOCH_STARTS, None),
        negotiate_epoch_start(SUPPORTED_EPOCH_STARTS, 0),
        negotiate_epoch_start(SUPPORTED_EPOCH_STARTS, c0_bit),
        negotiate_epoch_start(c0_bit, SUPPORTED_EPOCH_STARTS),
        negotiate_epoch_start(SUPPORTED_EPOCH_STARTS, SUPPORTED_EPOCH_STARTS | 0x80000000),
    ]
    check(
        negotiated == [EPOCH_START_C5_R4, EPOCH_START_C0, EPOCH_START_C0, EPOCH_START_C0, EPOCH_START_C0, EPOCH_START_C5_R4],
        "new+new selects C5-R4; an old or C0-only peer selects C0 on both sides; future peer bits are ignored",
    )

    def refused(action: Callable[[], Any], text: str) -> bool:
        try:
            action()
        except ValueError as error:
            return text in str(error)
        return False

    check(
        all(
            refused(lambda marker=marker: epoch_start_from_marker(marker), "epoch-start profile marker")
            for marker in (2, 3, 255, -1, True, "1", 1.0)
        ),
        "unknown or malformed markers are refused with a clear error",
    )
    check(
        refused(lambda: negotiate_epoch_start(SUPPORTED_EPOCH_STARTS, c5_bit), "malformed peer")
        and refused(lambda: negotiate_epoch_start(SUPPORTED_EPOCH_STARTS, 1 << 32 | c0_bit), "malformed peer")
        and refused(lambda: negotiate_epoch_start(c5_bit, SUPPORTED_EPOCH_STARTS), "local")
        and refused(lambda: negotiate_epoch_start(SUPPORTED_EPOCH_STARTS | 4, SUPPORTED_EPOCH_STARTS), "local")
        and refused(lambda: StreamEncoder(None, enc_record, None, rvq_enc_record, profile="C5-R5"), "unknown epoch-start profile")
        and refused(lambda: StreamDecoder(None, rvq_dec_record, None, dec_record, profile=1), "unknown epoch-start profile"),
        "malformed advertisements and unknown profiles are refused",
    )
    header_v1 = bytearray(b"KENC\x01\r\n\x1a" + bytes(56))
    header_v1[8] = 1
    header_v2 = bytearray(header_v1)
    header_v2[4], header_v2[FILE_MARKER_OFFSET] = 2, 1
    check(
        epoch_start_from_file_header(bytes(header_v1)) == EPOCH_START_C0
        and epoch_start_from_file_header(bytes(header_v2)) == EPOCH_START_C5_R4,
        "file header version 1 is C0 and version 2 marker 1 is C5-R4",
    )

    def altered(base: bytearray, offset: int, value: int) -> bytes:
        copy = bytearray(base)
        copy[offset] = value
        return bytes(copy)

    check(
        refused(lambda: epoch_start_from_file_header(altered(header_v2, FILE_MARKER_OFFSET, 7)), "unknown epoch-start profile marker")
        and refused(lambda: epoch_start_from_file_header(altered(header_v2, FILE_MARKER_OFFSET, 0)), "non-C0")
        and refused(lambda: epoch_start_from_file_header(altered(header_v1, FILE_MARKER_OFFSET, 1)), "version 1")
        and refused(lambda: epoch_start_from_file_header(altered(header_v2, 8, 2)), "mono")
        and refused(lambda: epoch_start_from_file_header(altered(header_v2, 4, 3)), "version 3")
        and refused(lambda: epoch_start_from_file_header(bytes(header_v2[:63])), "not a kilix-encodec"),
        "malformed or unknown file markers are refused with a clear error",
    )

    def delayed(value: Any) -> Any:
        shifted = np.zeros_like(value)
        shifted[..., :-PACKET_SAMPLES] = value[..., PACKET_SAMPLES:]
        return shifted

    signal = np.random.default_rng(5).standard_normal((1, 1, 48 * PACKET_SAMPLES)).astype(np.float32)
    rng = lambda count: np.random.default_rng(777).standard_normal(count).astype(np.float32)
    check(
        added_lookahead_packets(lambda x: x.copy(), signal, (25, 37), rng) == 0,
        "lookahead probe reads 0 for a causal rendering",
    )
    check(
        added_lookahead_packets(delayed, signal, (25, 37), rng) == 1,
        "lookahead probe reads 1 for a one-packet lookahead plant",
    )
    print(f"epoch-start runtime self-test: {checks}/{checks} PASS")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true", required=True)
    parser.parse_args()
    self_test()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
