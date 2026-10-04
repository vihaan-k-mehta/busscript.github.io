"""Saving frames and signals to files other tools open: CSV, ASC, BLF, MF4, and signal tables for spreadsheets."""
from __future__ import annotations

import csv
from pathlib import Path
from typing import Callable, Iterable, Optional

import can

from .bus import BusError
from .models import Frame

FORMATS = {"csv": ".csv", "asc": ".asc", "blf": ".blf", "mf4": ".mf4"}
MAX_SIGNAL_ROWS = 500_000


def _message(f: Frame, t0: float) -> can.Message:
    if f.error:
        return can.Message(timestamp=t0 + f.ts, is_error_frame=True)
    return can.Message(timestamp=t0 + f.ts, arbitration_id=f.can_id, is_extended_id=f.ext, is_fd=f.fd,
                       bitrate_switch=f.fd, data=f.data, is_rx=(f.direction == "rx"), channel=0)


def write_frames(frames: Iterable[Frame], path: Path, fmt: str, t0: float = 1_700_000_000.0) -> int:
    """Write frames in the chosen format. Returns how many were written."""
    fmt = fmt.lower()
    if fmt not in FORMATS:
        raise BusError(f"I can save as: {', '.join(FORMATS)}.")
    n = 0
    if fmt == "csv":                          # a plain table anyone can open in a spreadsheet
        with open(path, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["time_s", "channel", "id", "extended", "direction", "dlc", "data_hex", "error", "name"])
            for f in frames:
                w.writerow([f"{f.ts:.6f}", f.channel, f"0x{f.can_id:X}", int(f.ext), f.direction, f.dlc,
                            f.data.hex(" ").upper(), int(f.error), f.name or ""])
                n += 1
        return n
    try:
        writer = {"asc": can.ASCWriter, "blf": can.BLFWriter}.get(fmt) or can.io.MF4Writer
        w = writer(str(path))
    except ImportError as e:
        raise BusError(f"Saving as {fmt.upper()} needs a library that is not installed ({e}). Run start.bat again.") from e
    try:
        for f in frames:
            w.on_message_received(_message(f, t0))
            n += 1
    finally:
        w.stop()
    return n


def write_signals(series: dict[str, list[list[float]]], path: Path, delimiter: str = ",") -> int:
    """One column per signal, one row per moment any of them changed. Blank where a signal has no value yet."""
    if delimiter not in (",", ";", "\t", " "):
        raise BusError("The separator can be a comma, semicolon, tab or space.")
    if not series:
        raise BusError("Choose at least one signal to save.")
    times = sorted({round(t, 6) for pts in series.values() for t, _ in pts})
    if len(times) > MAX_SIGNAL_ROWS:
        raise BusError(f"That is {len(times):,} rows, more than the {MAX_SIGNAL_ROWS:,} I will save. Pick fewer signals.")
    names = list(series)
    lookup = {n: {round(t, 6): v for t, v in pts} for n, pts in series.items()}
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, delimiter=delimiter)
        w.writerow(["time_s", *names])
        for t in times:
            w.writerow([f"{t:.6f}", *["" if lookup[n].get(t) is None else lookup[n][t] for n in names]])
    return len(times)
