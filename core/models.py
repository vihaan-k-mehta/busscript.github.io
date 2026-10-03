from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Optional


@dataclass
class ChannelConfig:
    name: str
    interface: str = "virtual"
    channel: str = "vcan0"
    bitrate: int = 500000
    fd_enabled: bool = False
    fd_data_bitrate: Optional[int] = None
    listen_only: bool = True

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Frame:
    ts: float            # seconds since measurement start
    channel: str
    can_id: int
    ext: bool
    fd: bool
    direction: str       # "rx" | "tx"
    dlc: int
    data: bytes
    error: bool = False
    name: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "ts": round(self.ts, 6),
            "ch": self.channel,
            "id": self.can_id,
            "ext": self.ext,
            "fd": self.fd,
            "dir": self.direction,
            "dlc": self.dlc,
            "data": self.data.hex(),
            "error": self.error,
            "name": self.name,
        }


@dataclass
class FilterRule:
    id: str
    mode: str                       # "pass" | "stop"
    id_from: int
    id_to: int
    channel: Optional[str] = None   # None = all channels
    ext: bool = False
    direction: str = "both"         # "rx" | "tx" | "both"
    enabled: bool = True

    def matches(self, f: Frame) -> bool:
        if not self.enabled:
            return False
        if self.channel is not None and self.channel != f.channel:
            return False
        if self.ext != f.ext:
            return False
        if self.direction != "both" and self.direction != f.direction:
            return False
        return self.id_from <= f.can_id <= self.id_to

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ChannelStats:
    rx_frames: int = 0
    tx_frames: int = 0
    error_frames: int = 0
    rx_bytes: int = 0
    tx_bytes: int = 0
    bits_window: float = 0.0
    load_pct: float = 0.0
    peak_load_pct: float = 0.0
    rx_rate: float = 0.0
    tx_rate: float = 0.0
    _rx_window: int = 0
    _tx_window: int = 0

    def to_dict(self) -> dict:
        return {
            "rx_frames": self.rx_frames,
            "tx_frames": self.tx_frames,
            "error_frames": self.error_frames,
            "rx_bytes": self.rx_bytes,
            "tx_bytes": self.tx_bytes,
            "load_pct": round(self.load_pct, 2),
            "peak_load_pct": round(self.peak_load_pct, 2),
            "rx_rate": round(self.rx_rate, 1),
            "tx_rate": round(self.tx_rate, 1),
        }
