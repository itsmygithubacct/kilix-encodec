#!/usr/bin/env python3
"""Epoch-reset streaming runtime for the 24 kHz stateful graphs.

Every epoch starts from a repeat pre-roll (owner decision OD-AL, arm C5-R4):

- An epoch is ``EPOCH_PACKETS`` packets (1 s). The stream start is an epoch
  start too.
- At an epoch start the encoder and the decoder each set all of their streaming
  state to zero, run ``PREROLL_PACKETS`` lead-in packets through the unchanged
  per-packet graph, discard the lead-in outputs, and then process the epoch's
  packet 0 from the primed state.
- Encoder lead-in: packet 0's 960 audio samples tiled ``PREROLL_PACKETS``
  times. Decoder lead-in: packet 0's 3 code frames tiled ``PREROLL_PACKETS``
  times, each lead-in packet decoded through the RVQ graph.
- The lead-in uses only the epoch's own packet 0, so no packet is delayed and
  no future packet is read: the added algorithmic latency is zero.

The graphs themselves keep their all-zero initial-state contract. With
``preroll=0`` the same driver renders the graph-level zero cold start, which
is what a stream without any epoch-start behaviour produces.

This module needs only NumPy and ONNX Runtime sessions supplied by the caller.
The native C runtime implements the same behaviour in ``src/onnx.c``.
"""

from __future__ import annotations

import argparse
from typing import Any, Callable, Sequence

EPOCH_PACKETS = 25
PREROLL_PACKETS = 4
PACKET_SAMPLES = 960
PACKET_LATENT_FRAMES = 3

#: Machine-readable description recorded in verification results.
EPOCH_START = {
    "name": "repeat-pre-roll",
    "epoch_packets": EPOCH_PACKETS,
    "preroll_packets": PREROLL_PACKETS,
    "state_before_preroll": "all-zero",
    "encoder_lead_in": "epoch packet 0 audio tiled preroll_packets times",
    "decoder_lead_in": "epoch packet 0 code frames tiled preroll_packets times",
    "lead_in_outputs": "discarded",
    "stream_start_is_epoch_start": True,
    "added_latency_packets": 0,
}


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


class StreamEncoder:
    """Per-packet encoder: stateful encoder graph, then the RVQ encode graph."""

    def __init__(
        self,
        encoder: Any,
        encoder_record: dict[str, Any],
        rvq: Any,
        rvq_record: dict[str, Any],
        preroll: int = PREROLL_PACKETS,
    ):
        if preroll < 0:
            raise ValueError("pre-roll must not be negative")
        self.encoder = encoder
        self.encoder_record = encoder_record
        self.rvq = rvq
        self.rvq_record = rvq_record
        self.preroll = preroll
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
        preroll: int = PREROLL_PACKETS,
    ):
        if preroll < 0:
            raise ValueError("pre-roll must not be negative")
        self.rvq = rvq
        self.rvq_record = rvq_record
        self.decoder = decoder
        self.decoder_record = decoder_record
        self.preroll = preroll
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
    encoder = StreamEncoder(graph, enc_record, _FakeGraph("rvq_encode"), rvq_enc_record)
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
    zero_graph = _FakeGraph("encoder")
    encode_stream(StreamEncoder(zero_graph, enc_record, _FakeGraph("rvq_encode"), rvq_enc_record, preroll=0), audio)
    check(len(zero_graph.calls) == 30, "zero pre-roll runs no lead-in")

    dec_graph = _FakeGraph("decoder")
    rvq_graph = _FakeGraph("rvq_decode")
    stream_codes = np.concatenate(
        [np.full((1, 1, 3), i + 1, dtype=np.int64) for i in range(30)], axis=-1
    )
    decode_stream(StreamDecoder(rvq_graph, rvq_dec_record, dec_graph, dec_record), stream_codes)
    check(len(dec_graph.calls) == 30 + 2 * PREROLL_PACKETS, "decoder lead-in run count")
    check(
        len(rvq_graph.calls) == 30 + 2 * PREROLL_PACKETS
        and all(call[0] == 3.0 for call in rvq_graph.calls[: PREROLL_PACKETS + 1])
        and all(call[0] == 26 * 3.0 for call in rvq_graph.calls[reset_at : reset_at + PREROLL_PACKETS + 1]),
        "decoder lead-in decodes packet 0 codes through RVQ at every epoch start",
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
