"""State-explicit fixed-packet EnCodec graph construction.

The development exporter receives a user-supplied checkpoint path.  It never
uses EnCodec's URL-loading path, and the checkpoint is loaded with PyTorch's
restricted ``weights_only`` loader after exact byte verification.
"""

from __future__ import annotations

import hashlib
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

import torch
from encodec import EncodecModel
from encodec.modules import SConv1d, SConvTranspose1d, SLSTM
from torch import Tensor, nn


SAMPLE_RATE = 24_000
PACKET_SAMPLES = 960
PACKET_LATENT_FRAMES = 3
BANDWIDTH_PROFILES = ((3.0, 4), (6.0, 8), (12.0, 16))
DEFAULT_BANDWIDTH = 6.0
OPSET = 17
CHECKPOINT_FILE = "encodec_24khz-d7cc33bc.th"
CHECKPOINT_BYTES = 93_171_529
CHECKPOINT_SHA256 = (
    "d7cc33bcf1aad7f2dad9836f36431530744abeace3ca033005e3290ed4fa47bf"
)


@dataclass(frozen=True)
class CheckpointIdentity:
    """Verified identity safe to place in an export manifest."""

    file: str
    bytes: int
    sha256: str


@dataclass(frozen=True)
class StateSpec:
    """One explicit state tensor in stable graph order."""

    name: str
    shape: tuple[int, ...]


def _sha256_file(handle: object) -> str:
    digest = hashlib.sha256()
    while True:
        chunk = handle.read(1024 * 1024)  # type: ignore[attr-defined]
        if not chunk:
            return digest.hexdigest()
        digest.update(chunk)


def load_model(checkpoint: Path) -> tuple[EncodecModel, CheckpointIdentity]:
    """Load the exact reviewed checkpoint without network or unrestricted pickle."""

    try:
        path_stat = checkpoint.lstat()
    except FileNotFoundError as error:
        raise ValueError(f"checkpoint does not exist: {checkpoint}") from error
    if stat.S_ISLNK(path_stat.st_mode) or not stat.S_ISREG(path_stat.st_mode):
        raise ValueError("checkpoint must be a non-symlink regular file")
    if checkpoint.name != CHECKPOINT_FILE:
        raise ValueError(f"checkpoint filename must be {CHECKPOINT_FILE}")
    if path_stat.st_size != CHECKPOINT_BYTES:
        raise ValueError(
            f"checkpoint size mismatch: {path_stat.st_size} != {CHECKPOINT_BYTES}"
        )

    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(checkpoint, flags)
    with os.fdopen(descriptor, "rb") as handle:
        opened_stat = os.fstat(handle.fileno())
        if not stat.S_ISREG(opened_stat.st_mode) or opened_stat.st_size != path_stat.st_size:
            raise ValueError("checkpoint identity changed while opening")
        actual_digest = _sha256_file(handle)
        if actual_digest != CHECKPOINT_SHA256:
            raise ValueError(
                f"checkpoint digest mismatch: {actual_digest} != {CHECKPOINT_SHA256}"
            )
        handle.seek(0)
        state_dict = torch.load(handle, map_location="cpu", weights_only=True)
        final_stat = os.fstat(handle.fileno())
        if (
            final_stat.st_size != opened_stat.st_size
            or final_stat.st_mtime_ns != opened_stat.st_mtime_ns
            or final_stat.st_ino != opened_stat.st_ino
        ):
            raise ValueError("checkpoint changed while loading")

    if not isinstance(state_dict, dict) or not state_dict:
        raise ValueError("checkpoint is not a non-empty state dictionary")
    if not all(isinstance(key, str) for key in state_dict):
        raise ValueError("checkpoint contains a non-string state key")
    if not all(isinstance(value, Tensor) for value in state_dict.values()):
        raise ValueError("checkpoint contains a non-tensor state value")

    model = EncodecModel.encodec_model_24khz(pretrained=False)
    model.load_state_dict(state_dict, strict=True)
    model.eval()
    model.set_target_bandwidth(DEFAULT_BANDWIDTH)
    return model, CheckpointIdentity(
        file=checkpoint.name,
        bytes=opened_stat.st_size,
        sha256=actual_digest,
    )


def constant_padding(module: nn.Module) -> None:
    """Select the reviewed causal padding policy before graph replacement."""

    for child in module.modules():
        if isinstance(child, SConv1d):
            child.pad_mode = "constant"


def _slug(path: str) -> str:
    return path.replace(".", "_").replace("-", "_")


class _StateAdapter(nn.Module):
    """Protocol shared by modules participating in explicit state threading."""

    state_specs: tuple[StateSpec, ...] = ()

    def bind_state(self, states: Sequence[Tensor]) -> None:
        if len(states) != len(self.state_specs):
            raise ValueError(
                f"{type(self).__name__} expected {len(self.state_specs)} states, "
                f"received {len(states)}"
            )
        self._bound_state = tuple(states)
        self._next_state: tuple[Tensor, ...] | None = None

    def take_next_state(self) -> tuple[Tensor, ...]:
        if self._next_state is None:
            raise RuntimeError(f"{type(self).__name__} did not produce state")
        return self._next_state

    def initial_state(self, batch: int) -> tuple[Tensor, ...]:
        raise NotImplementedError


class StatefulConv1d(_StateAdapter):
    """Causal convolution with an explicit left-context tensor."""

    def __init__(self, source: SConv1d, path: str):
        super().__init__()
        if not source.causal:
            raise ValueError(f"non-causal SConv1d is unsupported: {path}")
        convolution = source.conv.conv
        kernel = convolution.kernel_size[0]
        stride = convolution.stride[0]
        dilation = convolution.dilation[0]
        self.conv = source.conv
        self.carry = (kernel - 1) * dilation + 1 - stride
        self.stride = stride
        self.in_channels = convolution.in_channels
        self.state_specs = (
            (
                StateSpec(
                    f"{_slug(path)}_context",
                    (1, self.in_channels, self.carry),
                ),
            )
            if self.carry
            else ()
        )

    def initial_state(self, batch: int) -> tuple[Tensor, ...]:
        if not self.carry:
            return ()
        parameter = next(self.conv.parameters())
        return (
            torch.zeros(
                batch,
                self.in_channels,
                self.carry,
                dtype=parameter.dtype,
                device=parameter.device,
            ),
        )

    def forward(self, value: Tensor) -> Tensor:
        if value.shape[-1] % self.stride:
            raise ValueError("fixed packet does not align to convolution stride")
        if self.carry:
            context = self._bound_state[0]
            joined = torch.cat((context, value), dim=-1)
            self._next_state = (joined[..., -self.carry :],)
        else:
            joined = value
            self._next_state = ()
        return self.conv(joined)


class StatefulConvTranspose1d(_StateAdapter):
    """Causal transpose convolution with explicit overlap-add state."""

    def __init__(self, source: SConvTranspose1d, path: str):
        super().__init__()
        if not source.causal or source.trim_right_ratio != 1.0:
            raise ValueError(f"unsupported transpose-convolution trim: {path}")
        convolution = source.convtr.convtr
        self.convtr = source.convtr
        self.stride = convolution.stride[0]
        self.overlap = convolution.kernel_size[0] - self.stride
        self.out_channels = convolution.out_channels
        self.state_specs = (
            StateSpec(
                f"{_slug(path)}_overlap",
                (1, self.out_channels, self.overlap),
            ),
        )

    def initial_state(self, batch: int) -> tuple[Tensor, ...]:
        parameter = next(self.convtr.parameters())
        return (
            torch.zeros(
                batch,
                self.out_channels,
                self.overlap,
                dtype=parameter.dtype,
                device=parameter.device,
            ),
        )

    def forward(self, value: Tensor) -> Tensor:
        raw = self.convtr(value)
        finalized = value.shape[-1] * self.stride
        previous = self._bound_state[0]
        head = raw[..., : self.overlap] + previous
        bias = self.convtr.convtr.bias
        combined = torch.cat((head, raw[..., self.overlap :]), dim=-1)
        next_overlap = raw[..., finalized : finalized + self.overlap]
        if bias is not None:
            next_overlap = next_overlap - bias.reshape(1, -1, 1)
        self._next_state = (next_overlap,)
        return combined[..., :finalized]


class StatefulLSTM(_StateAdapter):
    """EnCodec SLSTM with explicit hidden and cell tensors."""

    def __init__(self, source: SLSTM, path: str):
        super().__init__()
        self.lstm = source.lstm
        self.skip = source.skip
        self.layers = source.lstm.num_layers
        self.hidden = source.lstm.hidden_size
        base = _slug(path)
        shape = (self.layers, 1, self.hidden)
        self.state_specs = (
            StateSpec(f"{base}_hidden", shape),
            StateSpec(f"{base}_cell", shape),
        )

    def initial_state(self, batch: int) -> tuple[Tensor, ...]:
        parameter = next(self.lstm.parameters())
        shape = (self.layers, batch, self.hidden)
        return (
            torch.zeros(shape, dtype=parameter.dtype, device=parameter.device),
            torch.zeros(shape, dtype=parameter.dtype, device=parameter.device),
        )

    def forward(self, value: Tensor) -> Tensor:
        time_major = value.permute(2, 0, 1)
        hidden, cell = self._bound_state
        output, (next_hidden, next_cell) = self.lstm(time_major, (hidden, cell))
        self._next_state = (next_hidden, next_cell)
        if self.skip:
            output = output + time_major
        return output.permute(1, 2, 0)


class StatefulNetwork(nn.Module):
    """Top-level explicit-state wrapper for a fixed encoder or decoder packet."""

    def __init__(self, network: nn.Module, prefix: str):
        super().__init__()
        self.network = network
        self.prefix = prefix
        adapters: list[_StateAdapter] = []

        def replace(parent: nn.Module, parent_path: str) -> None:
            for name, child in list(parent.named_children()):
                path = f"{parent_path}.{name}" if parent_path else name
                adapter: _StateAdapter | None = None
                if isinstance(child, SConv1d):
                    adapter = StatefulConv1d(child, path)
                elif isinstance(child, SConvTranspose1d):
                    adapter = StatefulConvTranspose1d(child, path)
                elif isinstance(child, SLSTM):
                    adapter = StatefulLSTM(child, path)
                if adapter is None:
                    replace(child, path)
                else:
                    parent._modules[name] = adapter
                    adapters.append(adapter)

        replace(self.network, prefix)
        self._state_adapters = adapters
        self.state_specs = tuple(
            spec for adapter in adapters for spec in adapter.state_specs
        )

    def initial_state(self, batch: int = 1) -> tuple[Tensor, ...]:
        return tuple(
            state
            for adapter in self._state_adapters
            for state in adapter.initial_state(batch)
        )

    @property
    def state_input_names(self) -> tuple[str, ...]:
        return tuple(f"state_in_{spec.name}" for spec in self.state_specs)

    @property
    def state_output_names(self) -> tuple[str, ...]:
        return tuple(f"state_out_{spec.name}" for spec in self.state_specs)

    def forward(self, value: Tensor, *states: Tensor) -> tuple[Tensor, ...]:
        if len(states) != len(self.state_specs):
            raise ValueError(
                f"{self.prefix} expected {len(self.state_specs)} state tensors, "
                f"received {len(states)}"
            )
        cursor = 0
        for adapter in self._state_adapters:
            count = len(adapter.state_specs)
            adapter.bind_state(states[cursor : cursor + count])
            cursor += count
        output = self.network(value)
        next_states = tuple(
            state
            for adapter in self._state_adapters
            for state in adapter.take_next_state()
        )
        return (output, *next_states)


def stateful_network(model: EncodecModel, side: str) -> StatefulNetwork:
    if side not in ("encoder", "decoder"):
        raise ValueError(f"unsupported graph side: {side}")
    network = getattr(model, side)
    constant_padding(network)
    return StatefulNetwork(network, side).eval()


def iter_state_rows(network: StatefulNetwork) -> Iterable[str]:
    for index, spec in enumerate(network.state_specs):
        yield f"{index:02d} {spec.name} shape={spec.shape}"
