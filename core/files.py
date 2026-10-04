"""Opening recorded data files so they can be looked at without any hardware.

A file is read once into memory (up to MAX_FRAMES frames) and then served to the UI in pages, so a long
recording scrolls as smoothly as live traffic. Signal names come from whatever database is attached.
"""
from __future__ import annotations

import re
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

import can

from .bus import BusError
from .models import Frame

MAX_FRAMES = 1_000_000
MAX_UPLOAD = 1024 * 1024 * 1024          # 1 GiB
LOG_EXTS = {".asc": "Vector ASC", ".blf": "Vector BLF", ".mf4": "MDF4", ".mdf": "MDF4", ".log": "candump log", ".txt": "candump log",
            ".trc": "PEAK trace", ".csv": "CSV", ".db": "SQLite log"}
DB_EXTS = {".dbc": "DBC", ".kcd": "KCD", ".sym": "SYM", ".arxml": "ARXML", ".cdd": "CDD"}
SAFE_CHARS = re.compile(r"[^A-Za-z0-9._ -]")


def kind_of(filename: str) -> Optional[str]:
    ext = Path(filename).suffix.lower()
    if ext in LOG_EXTS:
        return "log"
    if ext in DB_EXTS:
        return "database"
    return None


def supported_text() -> str:
    return ", ".join(sorted(list(LOG_EXTS) + list(DB_EXTS)))


def safe_name(filename: str) -> str:
    """Keep only the file's own name, with harmless characters, and refuse types we cannot open."""
    base = Path(filename.replace("\\", "/")).name.strip().strip(".")
    base = SAFE_CHARS.sub("_", base)[:100]
    if not base or kind_of(base) is None:
        raise BusError(f"Busscript cannot open '{Path(filename).name}'. It opens: {supported_text()}.")
    return base


def safe_stem(name: str) -> str:
    """A harmless file name (any extension is dropped by the caller) for files Busscript writes."""
    base = SAFE_CHARS.sub("_", Path(name.replace("\\", "/")).name.strip().strip("."))[:100]
    return base or "export"


def unique_path(folder: Path, name: str) -> Path:
    p = folder / name
    n = 2
    while p.exists():
        p = folder / f"{Path(name).stem} ({n}){Path(name).suffix}"
        n += 1
    return p


@dataclass
class FileView:
    name: str
    path: str
    format: str
    frames: list[Frame] = field(default_factory=list)
    total_read: int = 0
    truncated: bool = False
    errors: int = 0
    channels: set = field(default_factory=set)
    by_id: dict = field(default_factory=lambda: defaultdict(list))      # (id, ext) -> frame indexes
    opened_at: float = field(default_factory=time.time)

    @property
    def duration(self) -> float:
        return self.frames[-1].ts if self.frames else 0.0

    def summary(self) -> dict:
        return {
            "name": self.name, "format": self.format, "frames": len(self.frames),
            "truncated": self.truncated, "duration": round(self.duration, 3),
            "channels": sorted(self.channels), "ids": len(self.by_id), "error_frames": self.errors,
        }


_CANDUMP_LINE = re.compile(r"^\(\d+\.\d+\)\s+\S+\s+[0-9A-Fa-f]+(#|##)")


def _looks_like_candump(path: Path) -> bool:
    try:
        with open(path, "r", encoding="ascii", errors="ignore") as f:
            lines = [ln for ln in (f.readline() for _ in range(5)) if ln.strip()]
    except OSError:
        return False
    return bool(lines) and all(_CANDUMP_LINE.match(ln) for ln in lines)


class _MessageList:
    """Messages we parsed ourselves, shaped like the python-can readers so read_log treats them the same."""

    def __init__(self, messages): self._m = messages
    def __iter__(self): return iter(self._m)
    def stop(self) -> None: pass


def _csv_header(path: Path) -> str:
    try:
        with open(path, "r", encoding="utf-8-sig", errors="ignore") as f:
            return f.readline().strip().lower()
    except OSError:
        return ""


def _read_own_csv(path: Path) -> "_MessageList":
    """The CSV that Busscript itself saves (time_s, channel, id, extended, direction, dlc, data_hex, error, name)."""
    import csv
    out = []
    with open(path, "r", newline="", encoding="utf-8-sig") as f:
        for n, row in enumerate(csv.DictReader(f), start=2):
            try:
                ch = re.search(r"(\d+)$", row.get("channel") or "")
                out.append(can.Message(
                    timestamp=float(row["time_s"]), arbitration_id=int(row["id"], 0),
                    is_extended_id=row["extended"].strip() == "1", is_rx=(row.get("direction") or "rx").strip() != "tx",
                    is_error_frame=(row.get("error") or "0").strip() == "1", dlc=int(row["dlc"]),
                    data=bytes.fromhex((row.get("data_hex") or "").replace(" ", "")), channel=int(ch.group(1)) if ch else None))
            except (KeyError, ValueError) as e:
                raise BusError(f"line {n} of '{path.name}' is not a frame row ({e}).")
    return _MessageList(out)


def read_log(path: Path, display: str, channel_for: Callable[[Optional[int]], str]) -> FileView:
    """Read any log python-can understands. channel_for maps the file's channel number to a Busscript channel name."""
    ext = path.suffix.lower()
    if ext not in LOG_EXTS:
        raise BusError(f"Busscript cannot open '{path.name}'. It opens: {supported_text()}.")
    read_path = str(path)
    tmp = None
    if ext == ".mdf":                      # python-can picks its reader by extension; MDF4 logs use .mf4
        tmp = path.with_suffix(".mf4.tmp.mf4")
        tmp.write_bytes(path.read_bytes())
        read_path = str(tmp)
    view = FileView(name=display, path=str(path), format=LOG_EXTS[ext])
    first: Optional[float] = None
    try:
        if ext == ".csv":
            head = _csv_header(path)
            if head.startswith("time_s,channel,id"):
                reader = _read_own_csv(path)
            elif not head.startswith("timestamp") and ("time" not in head) and ("data" in head or "id" in head):
                raise BusError(f"'{path.name}' has the columns '{head[:60]}' but no time column, so Busscript cannot place "
                               "the frames in time. It opens CSV files that start with a time column "
                               "(the ones Busscript saves, or python-can's: timestamp, arbitration_id, extended, remote, error, dlc, data).")
            else:
                reader = can.LogReader(read_path)
        elif ext == ".txt":                # candump logs are often saved as .txt: accept the file only if it really is one
            if not _looks_like_candump(path):
                raise BusError(f"'{path.name}' is a text file, but not a candump log (lines like "
                               "'(1594702589.999073) can0 18F11031#0000FFFFFFFFFFFF').")
            reader = can.io.CanutilsLogReader(read_path)
        else:
            reader = can.LogReader(read_path)
        try:
            for msg in reader:
                view.total_read += 1
                if len(view.frames) >= MAX_FRAMES:
                    view.truncated = True
                    break
                if first is None:
                    first = msg.timestamp
                ch = channel_for(msg.channel if isinstance(msg.channel, int) else None)
                f = Frame(ts=max(0.0, msg.timestamp - first), channel=ch, can_id=msg.arbitration_id,
                          ext=bool(msg.is_extended_id), fd=bool(msg.is_fd),
                          direction="rx" if msg.is_rx else "tx", dlc=msg.dlc, data=bytes(msg.data),
                          error=bool(msg.is_error_frame))
                view.channels.add(ch)
                if f.error:
                    view.errors += 1
                else:
                    view.by_id[(f.can_id, f.ext)].append(len(view.frames))
                view.frames.append(f)
        finally:
            reader.stop()
    except BusError:
        raise
    except ImportError as e:
        raise BusError(f"Opening {LOG_EXTS[ext]} files needs an extra library that is not installed ({e}). "
                       "Run start.bat again to install it.") from e
    except Exception as e:
        raise BusError(f"Could not read '{path.name}' as a {LOG_EXTS[ext]} file: {e}. "
                       "Is it the right kind of file, and was the recording finished?") from e
    finally:
        if tmp is not None:
            tmp.unlink(missing_ok=True)
    if not view.frames:
        extra = (" MDF4 files can also hold already-decoded signals instead of recorded CAN frames; Busscript "
                 "opens the kind that contains the CAN frames.") if ext in (".mf4", ".mdf") else ""
        raise BusError(f"'{path.name}' opened, but no CAN frames were found in it.{extra}")
    return view


class FileSession:
    """The one file that is currently open for viewing (if any), plus the folder uploads land in."""

    def __init__(self, bus, data_dir: Path):
        self.bus = bus
        self.dir = Path(data_dir) / "imports"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.view: Optional[FileView] = None

    # ---- opening and closing
    def _channel_for(self, n: Optional[int]) -> str:
        names = list(self.bus.channels)
        if n and 1 <= n <= len(names):
            return names[n - 1]
        if names:
            return names[0]
        return f"ch{n}" if n else "file"

    def open(self, path: Path, display: Optional[str] = None) -> dict:
        self.view = read_log(path, display or path.name, self._channel_for)
        return self.summary()

    def close(self) -> None:
        self.view = None

    def summary(self) -> Optional[dict]:
        return self.view.summary() if self.view else None

    def need(self) -> FileView:
        if self.view is None:
            raise BusError("No file is open. Open a recording first.")
        return self.view

    def stored_files(self) -> list[dict]:
        out = []
        for p in sorted(self.dir.iterdir(), key=lambda q: q.stat().st_mtime, reverse=True):
            if p.is_file() and kind_of(p.name):
                out.append({"name": p.name, "kind": kind_of(p.name), "size": p.stat().st_size, "modified": p.stat().st_mtime})
        return out

    def stored_path(self, name: str) -> Path:
        """Resolve a name from the uploads folder; nothing outside it can be reached."""
        p = (self.dir / Path(name.replace("\\", "/")).name).resolve()
        if p.parent != self.dir.resolve() or not p.is_file():
            raise BusError("That file is not in the uploads folder. Choose it again with Open file.")
        return p

    # ---- decoding with whatever databases are attached
    def _message(self, f: Frame):
        m = self.bus._lookup(f.channel, f.can_id, f.ext)
        if m is not None:
            return m
        for index in self.bus._msg_index.values():
            m = index.get((f.can_id, f.ext))
            if m is not None:
                return m
        return None

    def _decode(self, f: Frame, m) -> list[dict]:
        from .bus import _plain
        try:
            phys = m.decode(f.data, decode_choices=True, allow_truncated=True)
            raw = m.decode(f.data, decode_choices=False, scaling=False, allow_truncated=True)
        except Exception:
            return []
        return [{"message": m.name, "signal": s.name, "value": _plain(phys[s.name]), "unit": s.unit or "",
                 "raw": _plain(raw.get(s.name))} for s in m.signals if s.name in phys]

    # ---- reading it back
    def _sequence(self, can_id: Optional[int], ext: bool) -> list[Frame]:
        v = self.need()
        if can_id is None:
            return v.frames
        return [v.frames[i] for i in v.by_id.get((can_id, ext), [])]

    def count(self, can_id: Optional[int] = None, ext: bool = False) -> int:
        v = self.need()
        return len(v.frames) if can_id is None else len(v.by_id.get((can_id, ext), []))

    def frame_dicts(self, offset: int, limit: int, can_id: Optional[int] = None, ext: bool = False) -> list[dict]:
        v = self.need()
        offset = max(0, int(offset))
        limit = max(1, min(int(limit), 1000))
        if can_id is None:
            chunk = v.frames[offset:offset + limit]
        else:
            idxs = v.by_id.get((can_id, ext), [])[offset:offset + limit]
            chunk = [v.frames[i] for i in idxs]
        out = []
        for f in chunk:
            d = f.to_dict()
            m = self._message(f)
            d["name"] = m.name if m is not None else None
            out.append(d)
        return out

    def seek(self, t: float, can_id: Optional[int] = None, ext: bool = False) -> int:
        """Index of the first frame at or after t seconds (within one message if can_id is given)."""
        import bisect
        seq = self._sequence(can_id, ext)
        return bisect.bisect_left([f.ts for f in seq], float(t))

    def overview(self) -> list[dict]:
        v = self.need()
        rows = []
        for (cid, ext), idxs in v.by_id.items():
            f0 = v.frames[idxs[0]]
            m = self._message(f0)
            first, last = v.frames[idxs[0]].ts, v.frames[idxs[-1]].ts
            rows.append({"id": cid, "ext": ext, "name": m.name if m is not None else None, "count": len(idxs),
                         "first": round(first, 3), "last": round(last, 3),
                         "rate": round((len(idxs) - 1) / (last - first), 1) if last > first and len(idxs) > 1 else None})
        rows.sort(key=lambda r: -r["count"])
        return rows

    def signal_values(self) -> list[dict]:
        """Decoded signals of the last frame of each message in the file (what the Data pane shows)."""
        v = self.need()
        rows = []
        for (cid, ext), idxs in v.by_id.items():
            f = v.frames[idxs[-1]]
            m = self._message(f)
            if m is None:
                continue
            for r in self._decode(f, m):
                rows.append({**r, "channel": f.channel, "ts": round(f.ts, 3)})
        rows.sort(key=lambda r: (r["message"], r["signal"]))
        return rows

    def catalogue(self) -> list[dict]:
        v = self.need()
        out = []
        for (cid, ext), idxs in v.by_id.items():
            m = self._message(v.frames[idxs[0]])
            if m is not None:
                out.append({"channel": v.frames[idxs[0]].channel, "id": cid, "ext": ext, "name": m.name, "dlc": m.length,
                            "signals": [{"name": s.name, "unit": s.unit or "", "min": s.minimum, "max": s.maximum} for s in m.signals]})
        return out

    def series(self, name: str, max_points: int = 5000) -> list[list[float]]:
        """[time, value] pairs for 'Message.Signal' across the whole file, thinned to max_points."""
        v = self.need()
        msg, _, sig = name.partition(".")
        pts: list[tuple[float, float]] = []
        for (cid, ext), idxs in v.by_id.items():
            m = self._message(v.frames[idxs[0]])
            if m is None or m.name != msg:
                continue
            if sig not in {s.name for s in m.signals}:
                raise BusError(f"{msg} has no signal called {sig!r}.")
            for i in idxs:
                f = v.frames[i]
                try:
                    val = m.decode(f.data, decode_choices=False, allow_truncated=True)[sig]
                except Exception:
                    continue
                pts.append((f.ts, float(val)))
            break
        else:
            raise BusError(f"No frames for a message called {msg!r} in this file, or no database is attached that describes it.")
        max_points = max(10, min(int(max_points), 20000))
        if len(pts) > max_points:
            step = len(pts) / max_points
            pts = [pts[int(i * step)] for i in range(max_points)]
        return [[round(t, 6), x] for t, x in pts]
