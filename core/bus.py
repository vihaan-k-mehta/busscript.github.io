"""The Bus: owns channels, databases, the ring buffer, statistics, transmit, logging and replay.

Both the HTTP/WebSocket API and the MCP tools call this one object.
"""
from __future__ import annotations

import threading
import time
import uuid
from collections import deque
from itertools import islice
from typing import Callable, Optional

import can
import cantools

from .models import ChannelConfig, ChannelStats, FilterRule, Frame

FD_LENGTHS = (0, 1, 2, 3, 4, 5, 6, 7, 8, 12, 16, 20, 24, 32, 48, 64)
HISTORY_LEN = 20000


class BusError(Exception):
    """A user-facing problem (bad input, wrong state)."""


def _frame_time_s(f: Frame, cfg: Optional[ChannelConfig]) -> float:
    """Rough on-wire time of a frame, used for the bus load estimate (no bit stuffing)."""
    bitrate = cfg.bitrate if cfg else 500000
    n = len(f.data)
    if f.fd and cfg and cfg.fd_enabled:
        data_rate = cfg.fd_data_bitrate or bitrate
        arb_bits = 29 if f.ext else 11
        return (arb_bits + 30) / bitrate + (8 * n + 30) / data_rate
    return ((67 if f.ext else 47) + 8 * n) / bitrate


class Bus:
    def __init__(self, ring_size: int = 500_000):
        self._lock = threading.RLock()
        self._ring_lock = threading.Lock()
        self.ring: deque[Frame] = deque(maxlen=ring_size)
        self.channels: dict[str, ChannelConfig] = {}
        self._can: dict[str, can.BusABC] = {}
        self._readers: dict[str, threading.Thread] = {}
        self._db_files: dict[str, list[str]] = {}
        self._msg_index: dict[str, dict[tuple[int, bool], object]] = {}
        self.filters: dict[str, FilterRule] = {}
        self.stats: dict[str, ChannelStats] = {}
        self._latest: dict[tuple[str, int, bool], Frame] = {}
        self._peaks: dict[tuple[str, str, str], list[float]] = {}     # (channel, message, signal) -> [lowest, highest] seen
        self._watched: set[str] = set()
        self._history: dict[str, deque] = {}
        self._subs: list[Callable[[Frame], None]] = []
        self.running = False
        self._t0 = 0.0
        self._t0_wall = 0.0
        self._stop = threading.Event()
        self._tick_thread: Optional[threading.Thread] = None
        self._cyclic: dict[str, tuple[threading.Thread, threading.Event, dict]] = {}
        self._writer = None
        self._log_info: Optional[dict] = None
        self._log_count = 0
        self._replay_thread: Optional[threading.Thread] = None
        self._replay_stop = threading.Event()
        self._replay_info: Optional[dict] = None
        self.dropped = 0
        self.demo = False  # True when running on the built-in synthetic traffic
        self.demo_stop = None  # set() to end the synthetic traffic

    # ------------------------------------------------------------------ config
    def set_channel(self, cfg: ChannelConfig) -> ChannelConfig:
        with self._lock:
            if self.running:
                raise BusError("stop the measurement before changing channels")
            if cfg.bitrate <= 0:
                raise BusError("bitrate must be positive")
            if cfg.fd_enabled and cfg.fd_data_bitrate is not None and cfg.fd_data_bitrate <= 0:
                raise BusError("data bitrate must be positive")
            self.channels[cfg.name] = cfg
            self.stats.setdefault(cfg.name, ChannelStats())
            self._db_files.setdefault(cfg.name, [])
            self._msg_index.setdefault(cfg.name, {})
            return cfg

    def remove_channel(self, name: str) -> None:
        with self._lock:
            if self.running:
                raise BusError("stop the measurement before changing channels")
            self.channels.pop(name, None)
            self.stats.pop(name, None)
            self._db_files.pop(name, None)
            self._msg_index.pop(name, None)

    def end_demo(self) -> None:
        """Leave the built-in demo: stop it, drop its channel, so the user can add a real adapter."""
        if not self.demo:
            return
        self.stop()
        if self.demo_stop is not None:
            self.demo_stop.set()
        self.remove_channel("demo")
        self.demo = False

    def list_channels(self) -> list[dict]:
        with self._lock:
            return [
                {**c.to_dict(), "open": c.name in self._can, "databases": list(self._db_files.get(c.name, []))}
                for c in self.channels.values()
            ]

    # --------------------------------------------------------------- databases
    def load_database(self, channel: str, path: str) -> dict:
        with self._lock:
            if channel not in self.channels:
                raise BusError(f"unknown channel {channel!r}")
            try:
                db = cantools.database.load_file(path, strict=False)
            except Exception as e:  # cantools raises several types
                raise BusError(f"could not read database: {e}") from e
            index = self._msg_index.setdefault(channel, {})
            for m in db.messages:
                index[(m.frame_id, bool(m.is_extended_frame))] = m
            if path not in self._db_files[channel]:
                self._db_files[channel].append(path)
            return {"channel": channel, "path": path, "messages": len(db.messages),
                    "signals": sum(len(m.signals) for m in db.messages)}

    def unload_databases(self, channel: str) -> None:
        with self._lock:
            self._db_files[channel] = []
            self._msg_index[channel] = {}

    def catalogue(self) -> list[dict]:
        out = []
        with self._lock:
            for ch, index in self._msg_index.items():
                for (fid, ext), m in sorted(index.items()):
                    out.append({
                        "channel": ch, "id": fid, "ext": ext, "name": m.name, "dlc": m.length,
                        "signals": [{"name": s.name, "unit": s.unit or "", "min": s.minimum, "max": s.maximum}
                                    for s in m.signals],
                    })
        return out

    def _lookup(self, channel: str, can_id: int, ext: bool):
        return self._msg_index.get(channel, {}).get((can_id, ext))

    def _message_by_name(self, channel: str, name: str):
        for m in self._msg_index.get(channel, {}).values():
            if m.name == name:
                return m
        raise BusError(f"no message named {name!r} in the database attached to {channel!r}")

    # ---------------------------------------------------------------- filters
    def set_filter(self, rule: FilterRule) -> FilterRule:
        if rule.mode not in ("pass", "stop"):
            raise BusError("mode must be pass or stop")
        if rule.id_to < rule.id_from:
            raise BusError("id_to must be >= id_from")
        if rule.direction not in ("rx", "tx", "both"):
            raise BusError("direction must be rx, tx or both")
        with self._lock:
            self.filters[rule.id] = rule
        return rule

    def delete_filter(self, rule_id: str) -> None:
        with self._lock:
            self.filters.pop(rule_id, None)

    def passes_filters(self, f: Frame) -> bool:
        rules = [r for r in self.filters.values() if r.enabled and (r.channel in (None, f.channel))]
        if not rules:
            return True
        if any(r.mode == "stop" and r.matches(f) for r in rules):
            return False
        pass_rules = [r for r in rules if r.mode == "pass"]
        return not pass_rules or any(r.matches(f) for r in pass_rules)

    # ------------------------------------------------------------ subscribers
    def subscribe(self, cb: Callable[[Frame], None]) -> Callable[[], None]:
        with self._lock:
            self._subs.append(cb)

        def unsubscribe():
            with self._lock:
                if cb in self._subs:
                    self._subs.remove(cb)
        return unsubscribe

    # ------------------------------------------------------------ measurement
    def start(self) -> dict:
        with self._lock:
            if self.running:
                return self.state()
            self._t0 = time.time()
            self._t0_wall = self._t0
            self._stop.clear()
            opened = []
            try:
                for name, cfg in self.channels.items():
                    kwargs = dict(interface=cfg.interface, channel=cfg.channel, bitrate=cfg.bitrate)
                    if cfg.fd_enabled:
                        kwargs.update(fd=True, data_bitrate=cfg.fd_data_bitrate or cfg.bitrate)
                    try:
                        bus = can.Bus(**kwargs)
                    except Exception as e:
                        raise BusError(f"could not open channel {name!r}: {e}") from e
                    self._can[name] = bus
                    opened.append(name)
            except BusError:
                for n in opened:
                    self._can.pop(n).shutdown()
                raise
            for s in self.stats.values():
                s.__dict__.update(ChannelStats().__dict__)
            with self._ring_lock:
                self.ring.clear()
            self._latest.clear()
            self._peaks.clear()
            self.dropped = 0
            self.running = True
            for name, bus in self._can.items():
                t = threading.Thread(target=self._read_loop, args=(name, bus), daemon=True, name=f"can-{name}")
                self._readers[name] = t
                t.start()
            self._tick_thread = threading.Thread(target=self._tick_loop, daemon=True, name="can-stats")
            self._tick_thread.start()
            return self.state()

    def stop(self) -> dict:
        with self._lock:
            if not self.running:
                return self.state()
            self.running = False
            self._stop.set()
        self.stop_replay()
        self.stop_logging()
        for tid in list(self._cyclic):
            self.stop_cyclic(tid)
        for t in list(self._readers.values()):
            t.join(timeout=1.0)
        with self._lock:
            for bus in self._can.values():
                try:
                    bus.shutdown()
                except Exception:
                    pass
            self._can.clear()
            self._readers.clear()
        return self.state()

    def state(self) -> dict:
        return {
            "demo": self.demo,
            "running": self.running,
            "elapsed": round(time.time() - self._t0, 3) if self.running else 0.0,
            "channels": len(self.channels),
            "frames_buffered": len(self.ring),
            "logging": self._log_info and {**self._log_info, "frames": self._log_count},
            "replay": self._replay_info,
            "cyclic": [info for (_, _, info) in self._cyclic.values()],
        }

    # ---------------------------------------------------------------- ingest
    def _read_loop(self, name: str, bus: can.BusABC) -> None:
        while not self._stop.is_set():
            try:
                msg = bus.recv(timeout=0.1)
            except Exception:
                if self._stop.is_set():
                    return
                time.sleep(0.1)
                continue
            if msg is None or not msg.is_rx:
                continue
            self._ingest(Frame(
                ts=time.time() - self._t0, channel=name, can_id=msg.arbitration_id,
                ext=bool(msg.is_extended_id), fd=bool(msg.is_fd), direction="rx",
                dlc=msg.dlc, data=bytes(msg.data), error=bool(msg.is_error_frame)))

    def _ingest(self, f: Frame) -> None:
        m = self._lookup(f.channel, f.can_id, f.ext)
        if m is not None:
            f.name = m.name
        st = self.stats.setdefault(f.channel, ChannelStats())
        if f.error:
            st.error_frames += 1
        elif f.direction == "tx":
            st.tx_frames += 1
            st.tx_bytes += len(f.data)
            st._tx_window += 1
        else:
            st.rx_frames += 1
            st.rx_bytes += len(f.data)
            st._rx_window += 1
        if not f.error:
            st.bits_window += _frame_time_s(f, self.channels.get(f.channel))
        with self._ring_lock:
            self.ring.append(f)
        self._latest[(f.channel, f.can_id, f.ext)] = f
        if m is not None and not f.error:
            self._note_peaks(f, m)
        if m is not None and self._watched and not f.error:
            self._record_history(f, m)
        if self._writer is not None:
            self._write_log(f)
        if self._subs and self.passes_filters(f):
            for cb in list(self._subs):
                try:
                    cb(f)
                except Exception:
                    pass

    def _note_peaks(self, f: Frame, m) -> None:
        """Remember the lowest and highest value of every signal, so a very short spike is not missed between screen updates."""
        try:
            values = m.decode(f.data, decode_choices=False, allow_truncated=True)
        except Exception:
            return
        for sig, v in values.items():
            if isinstance(v, bool) or not isinstance(v, (int, float)):
                continue
            pk = self._peaks.get((f.channel, m.name, sig))
            if pk is None:
                self._peaks[(f.channel, m.name, sig)] = [v, v]
            elif v < pk[0]:
                pk[0] = v
            elif v > pk[1]:
                pk[1] = v

    def _record_history(self, f: Frame, m) -> None:
        wanted = [s for s in m.signals if f"{m.name}.{s.name}" in self._watched]
        if not wanted:
            return
        try:
            phys = m.decode(f.data, decode_choices=False, allow_truncated=True)
        except Exception:
            return
        for s in wanted:
            if s.name in phys:
                self._history.setdefault(f"{m.name}.{s.name}", deque(maxlen=HISTORY_LEN)).append(
                    (f.ts, float(phys[s.name])))

    def _tick_loop(self) -> None:
        last = time.time()
        while not self._stop.wait(1.0):
            now = time.time()
            dt = max(now - last, 1e-6)
            last = now
            for st in self.stats.values():
                st.rx_rate = st._rx_window / dt
                st.tx_rate = st._tx_window / dt
                st.load_pct = min(100.0, st.bits_window / dt * 100.0)
                st.peak_load_pct = max(st.peak_load_pct, st.load_pct)
                st._rx_window = st._tx_window = 0
                st.bits_window = 0.0

    # --------------------------------------------------------------- queries
    def recent(self, n: int = 100, can_id: Optional[int] = None, channel: Optional[str] = None,
               direction: Optional[str] = None, hard_cap: int = 1000) -> list[dict]:
        n = max(1, min(int(n), hard_cap))
        out: list[Frame] = []
        with self._ring_lock:
            for f in reversed(self.ring):
                if can_id is not None and f.can_id != can_id:
                    continue
                if channel is not None and f.channel != channel:
                    continue
                if direction is not None and f.direction != direction:
                    continue
                if not self.passes_filters(f):
                    continue
                out.append(f)
                if len(out) >= n:
                    break
        out.reverse()
        return [f.to_dict() for f in out]

    def statistics(self) -> dict:
        return {name: st.to_dict() for name, st in self.stats.items()}

    def decode_frame(self, f: Frame) -> list[dict]:
        m = self._lookup(f.channel, f.can_id, f.ext)
        if m is None:
            return []
        try:
            phys = m.decode(f.data, decode_choices=True, allow_truncated=True)
            raw = m.decode(f.data, decode_choices=False, scaling=False, allow_truncated=True)
        except Exception:
            return []
        out = []
        for s in m.signals:
            if s.name in phys:
                out.append({"message": m.name, "signal": s.name, "value": _plain(phys[s.name]),
                            "unit": s.unit or "", "raw": _plain(raw.get(s.name))})
        return out

    def signal_values(self) -> list[dict]:
        rows = []
        for (ch, fid, ext), f in list(self._latest.items()):
            for r in self.decode_frame(f):
                pk = self._peaks.get((ch, r["message"], r["signal"]))
                rows.append({**r, "channel": ch, "ts": round(f.ts, 3), "min": pk[0] if pk else None, "max": pk[1] if pk else None})
        rows.sort(key=lambda r: (r["message"], r["signal"]))
        return rows

    def reset_peaks(self) -> None:
        self._peaks.clear()

    def current_signal(self, name: str):
        """Latest decoded value of 'Message.Signal', or None if it has not been seen yet."""
        msg, _, sig = name.partition(".")
        for f in list(self._latest.values()):
            if f.name == msg:
                for r in self.decode_frame(f):
                    if r["signal"] == sig:
                        return r["value"]
        return None

    def watch(self, names: list[str]) -> None:
        with self._lock:
            self._watched = set(names)
            for n in list(self._history):
                if n not in self._watched:
                    del self._history[n]

    def history(self, name: str, since: float = 0.0, max_points: int = 5000) -> list[list[float]]:
        pts = [p for p in list(self._history.get(name, ())) if p[0] >= since]
        if len(pts) > max_points:
            step = len(pts) / max_points
            pts = [pts[int(i * step)] for i in range(max_points)]
        return [[round(t, 6), v] for t, v in pts]

    def read_signal(self, name: str, history: int = 20) -> dict:
        latest = next((r for r in self.signal_values() if f"{r['message']}.{r['signal']}" == name), None)
        if latest is None:
            raise BusError(f"no value seen yet for {name!r} (is a database loaded and is the signal on the bus?)")
        hist = list(self._history.get(name, ()))[-max(0, min(history, 200)):]
        return {"name": name, **latest, "history": [[round(t, 6), v] for t, v in hist]}

    # -------------------------------------------------------------- transmit
    def _validate_frame(self, channel: str, can_id: int, data: bytes, ext: bool, fd: bool) -> ChannelConfig:
        cfg = self.channels.get(channel)
        if cfg is None:
            raise BusError(f"unknown channel {channel!r}")
        if not self.running or channel not in self._can:
            raise BusError("measurement is not running")
        if cfg.listen_only:
            raise BusError(f"channel {channel!r} is listen-only; turn that off in channel settings to transmit")
        if can_id < 0 or can_id > (0x1FFFFFFF if ext else 0x7FF):
            raise BusError("identifier out of range")
        if fd and not cfg.fd_enabled:
            raise BusError("CAN FD is not enabled on this channel")
        if fd:
            if len(data) not in FD_LENGTHS:
                raise BusError(f"CAN FD length must be one of {FD_LENGTHS}")
        elif len(data) > 8:
            raise BusError("classic CAN frames carry at most 8 bytes")
        return cfg

    def send(self, channel: str, can_id: int, data: bytes, ext: bool = False, fd: bool = False) -> dict:
        self._validate_frame(channel, can_id, data, ext, fd)
        msg = can.Message(arbitration_id=can_id, is_extended_id=ext, is_fd=fd, data=data,
                          bitrate_switch=fd)
        try:
            self._can[channel].send(msg)
        except Exception as e:
            raise BusError(f"send failed: {e}") from e
        f = Frame(ts=time.time() - self._t0, channel=channel, can_id=can_id, ext=ext, fd=fd,
                  direction="tx", dlc=len(data), data=bytes(data))
        self._ingest(f)
        return f.to_dict()

    def send_signals(self, channel: str, message: str, values: dict) -> dict:
        """Encode physical signal values (or choice names) with the database and transmit the frame.

        Signals not mentioned are sent at their initial value, or 0; unused bits are 0. Out-of-range values are refused.
        """
        m = self._message_by_name(channel, message)
        unknown = [k for k in values if k not in {s.name for s in m.signals}]
        if unknown:
            raise BusError(f"unknown signal(s) in {message}: {', '.join(unknown)}")
        full = {}
        for sig in m.signals:
            if sig.name in values:
                full[sig.name] = values[sig.name]
            else:
                init = sig.initial if sig.initial is not None else 0
                full[sig.name] = init
        try:
            data = bytes(m.encode(full, scaling=True, padding=False, strict=True))
        except Exception as e:  # cantools raises several types for range and choice errors
            raise BusError(f"cannot encode {message}: {e}") from e
        return self.send(channel, m.frame_id, data, bool(m.is_extended_frame))

    def start_cyclic(self, channel: str, can_id: int, data: bytes, period_ms: int,
                     ext: bool = False, fd: bool = False) -> dict:
        if period_ms < 1:
            raise BusError("period must be at least 1 ms")
        self._validate_frame(channel, can_id, data, ext, fd)
        tid = uuid.uuid4().hex[:8]
        ev = threading.Event()
        info = {"id": tid, "channel": channel, "can_id": can_id, "ext": ext, "fd": fd,
                "data": data.hex(), "period_ms": period_ms}

        def run():
            period = period_ms / 1000.0
            nxt = time.perf_counter()
            while not ev.is_set() and not self._stop.is_set():
                try:
                    self.send(channel, can_id, data, ext, fd)
                except BusError:
                    return
                nxt += period
                delay = nxt - time.perf_counter()
                if delay > 0:
                    ev.wait(delay)
                else:
                    nxt = time.perf_counter()  # fell behind: do not burst to catch up

        t = threading.Thread(target=run, daemon=True, name=f"cyclic-{tid}")
        self._cyclic[tid] = (t, ev, info)
        t.start()
        return info

    def stop_cyclic(self, tid: str) -> None:
        entry = self._cyclic.pop(tid, None)
        if entry:
            entry[1].set()
            entry[0].join(timeout=1.0)

    # --------------------------------------------------------------- logging
    def _chan_index(self, name: str) -> int:
        names = list(self.channels)
        return names.index(name) + 1 if name in names else 1

    def start_logging(self, path: str, fmt: str = "asc") -> dict:
        fmt = fmt.lower()
        if fmt not in ("asc", "blf"):
            raise BusError("format must be asc or blf")
        if not self.running:
            raise BusError("measurement is not running")
        with self._lock:
            if self._writer is not None:
                raise BusError("already logging; stop the current log first")
            try:
                self._writer = can.ASCWriter(path) if fmt == "asc" else can.BLFWriter(path)
            except Exception as e:
                raise BusError(f"cannot open log file: {e}") from e
            self._log_count = 0
            self._log_info = {"path": path, "format": fmt, "started": time.time()}
        return self._log_info

    def _write_log(self, f: Frame) -> None:
        w = self._writer
        if w is None:
            return
        try:
            if f.error:
                msg = can.Message(timestamp=self._t0_wall + f.ts, is_error_frame=True,
                                  channel=self._chan_index(f.channel))
            else:
                msg = can.Message(timestamp=self._t0_wall + f.ts, arbitration_id=f.can_id,
                                  is_extended_id=f.ext, is_fd=f.fd, bitrate_switch=f.fd,
                                  data=f.data, is_rx=(f.direction == "rx"),
                                  channel=self._chan_index(f.channel))
            w.on_message_received(msg)
            self._log_count += 1
        except Exception:
            pass

    def stop_logging(self) -> Optional[dict]:
        with self._lock:
            w, self._writer = self._writer, None
            info = self._log_info and {**self._log_info, "frames": self._log_count}
            self._log_info = None
        if w is not None:
            try:
                w.stop()
            except Exception:
                pass
        return info

    # ---------------------------------------------------------------- replay
    def start_replay(self, path: str, channel: Optional[str] = None, speed: float = 1.0,
                     loop: bool = False) -> dict:
        if not self.running:
            raise BusError("start the measurement first (offline replay needs no channels)")
        if speed <= 0:
            raise BusError("speed must be positive")
        if self._replay_thread and self._replay_thread.is_alive():
            raise BusError("a replay is already running")
        try:
            can.LogReader(path).stop()
        except Exception as e:
            raise BusError(f"cannot read log file: {e}") from e
        target = channel or next(iter(self.channels), "offline")
        live = target in self._can and not self.channels[target].listen_only
        self._replay_stop.clear()
        self._replay_info = {"path": path, "channel": target, "speed": speed, "loop": loop,
                             "to_bus": live, "position": 0}
        self._replay_thread = threading.Thread(
            target=self._replay_loop, args=(path, target, speed, loop, live), daemon=True, name="can-replay")
        self._replay_thread.start()
        return self._replay_info

    def _replay_loop(self, path: str, target: str, speed: float, loop: bool, live: bool) -> None:
        try:
            while not self._replay_stop.is_set() and not self._stop.is_set():
                first = None
                wall0 = time.perf_counter()
                reader = can.LogReader(path)
                try:
                    for i, msg in enumerate(reader):
                        if self._replay_stop.is_set() or self._stop.is_set():
                            return
                        if msg.is_error_frame:
                            continue
                        if first is None:
                            first = msg.timestamp
                        due = (msg.timestamp - first) / speed
                        delay = due - (time.perf_counter() - wall0)
                        if delay > 0 and self._replay_stop.wait(delay):
                            return
                        data = bytes(msg.data)
                        if live:
                            try:
                                self.send(target, msg.arbitration_id, data, bool(msg.is_extended_id),
                                          bool(msg.is_fd))
                            except BusError:
                                continue
                        else:
                            self._ingest(Frame(ts=time.time() - self._t0, channel=target,
                                               can_id=msg.arbitration_id, ext=bool(msg.is_extended_id),
                                               fd=bool(msg.is_fd), direction="rx", dlc=msg.dlc, data=data))
                        if self._replay_info is not None:
                            self._replay_info["position"] = i + 1
                finally:
                    reader.stop()
                if not loop:
                    return
        finally:
            self._replay_info = None

    def stop_replay(self) -> None:
        self._replay_stop.set()
        t = self._replay_thread
        if t is not None and t.is_alive() and t is not threading.current_thread():
            t.join(timeout=2.0)
        self._replay_info = None


def _plain(v):
    """Make cantools values (NamedSignalValue, numpy-ish) JSON-safe."""
    if v is None or isinstance(v, (int, float, str, bool)):
        return v if not isinstance(v, float) else round(v, 9)
    return str(v)
