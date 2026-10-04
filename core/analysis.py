"""Looking through a list of frames: a statistics report, search/trigger conditions, and cutting out the part
around an event. Pure functions over Frame objects, so they work the same for a recording and for live traffic."""
from __future__ import annotations

import math
import operator
import re
from collections import defaultdict
from typing import Callable, Iterable, Optional

from .bus import BusError
from .models import Frame

# ------------------------------------------------------------------ statistics report
def report(frames: Iterable[Frame]) -> list[dict]:
    """Per message and direction: how many, and how regularly. Spacing is the time between two frames in a row."""
    seen: dict[tuple, list[float]] = defaultdict(list)
    for f in frames:
        if f.error:
            continue
        seen[(f.channel, f.can_id, f.ext, f.direction)].append(f.ts)
    rows = []
    for (ch, cid, ext, direction), ts in seen.items():
        gaps = [b - a for a, b in zip(ts, ts[1:])]
        mean = sum(gaps) / len(gaps) if gaps else None
        sd = math.sqrt(sum((g - mean) ** 2 for g in gaps) / len(gaps)) if gaps and mean is not None else None
        rows.append({"channel": ch, "id": cid, "ext": ext, "dir": direction, "count": len(ts),
                     "mean_ms": None if mean is None else round(mean * 1000, 3),
                     "sd_ms": None if sd is None else round(sd * 1000, 3),
                     "min_ms": round(min(gaps) * 1000, 3) if gaps else None,
                     "max_ms": round(max(gaps) * 1000, 3) if gaps else None})
    rows.sort(key=lambda r: (r["channel"], r["ext"], r["id"], r["dir"]))
    return rows


# ------------------------------------------------------------------ conditions
_OPS: dict[str, Callable] = {"==": operator.eq, "!=": operator.ne, "<=": operator.le, ">=": operator.ge,
                             "<": operator.lt, ">": operator.gt}
_PRIM = re.compile(r"^\s*(id|dlc|time|dir|error|d\d{1,2})\s*(==|!=|<=|>=|<|>)\s*(\S+)\s*$", re.IGNORECASE)
_HELP = ("Write a condition like 'id == 0x100', 'id > 0x100 and d0 == 5' or 'error == 1'. "
         "You can use id, dlc, time (seconds), dir (rx or tx), error (0 or 1) and d0 to d63 (data bytes). "
         "Join with 'and' or 'or' (not both in one condition).")


def _number(text: str) -> float:
    try:
        return float(int(text, 16)) if text.lower().startswith("0x") else float(text)
    except ValueError:
        raise BusError(f"'{text}' is not a number. {_HELP}")


def parse_condition(text: str) -> Callable[[Frame], bool]:
    text = (text or "").strip()
    if not text:
        raise BusError(_HELP)
    has_and, has_or = re.search(r"\band\b", text, re.I), re.search(r"\bor\b", text, re.I)
    if has_and and has_or:
        raise BusError("Use either 'and' or 'or' in one condition, not both. " + _HELP)
    parts = re.split(r"\s+(?:and|or)\s+", text, flags=re.I)
    prims: list[Callable[[Frame], bool]] = []
    for part in parts:
        m = _PRIM.match(part)
        if not m:
            raise BusError(f"I do not understand '{part.strip()}'. {_HELP}")
        field, op, raw = m.group(1).lower(), _OPS[m.group(2)], m.group(3)
        if field == "dir":
            if raw.lower() not in ("rx", "tx"):
                raise BusError("dir must be rx or tx.")
            want = raw.lower()
            prims.append(lambda f, op=op, want=want: op(f.direction, want))
            continue
        val = _number(raw)
        if field == "id":
            prims.append(lambda f, op=op, v=val: op(f.can_id, v))
        elif field == "dlc":
            prims.append(lambda f, op=op, v=val: op(f.dlc, v))
        elif field == "time":
            prims.append(lambda f, op=op, v=val: op(f.ts, v))
        elif field == "error":
            prims.append(lambda f, op=op, v=val: op(1 if f.error else 0, v))
        else:
            n = int(field[1:])
            if n > 63:
                raise BusError("Data bytes go from d0 to d63.")
            prims.append(lambda f, op=op, v=val, n=n: n < len(f.data) and op(f.data[n], v))
    join = any if has_or else all
    return lambda f: join(p(f) for p in prims)


def find_matches(frames: list[Frame], cond: Callable[[Frame], bool], start: int = 0, cap: int = 100000) -> tuple[Optional[int], int]:
    """(index of the first match at or after start, how many frames match in total up to cap)."""
    first, n = None, 0
    for i, f in enumerate(frames):
        if cond(f):
            n += 1
            if first is None and i >= start:
                first = i
            if n >= cap:
                break
    return first, n


# ------------------------------------------------------------------ cutting out the part around an event
def trigger_windows(frames: list[Frame], start: Callable[[Frame], bool], pre: float, post: float,
                    end: Optional[Callable[[Frame], bool]] = None) -> list[tuple[float, float]]:
    """Time windows to keep. Single trigger: pre seconds before and post seconds after every match. Toggle: from
    the first condition (minus pre) to the next match of the end condition (plus post), then again."""
    pre, post = max(0.0, pre), max(0.0, post)
    wins: list[tuple[float, float]] = []
    if end is None:
        for f in frames:
            if start(f):
                a, b = f.ts - pre, f.ts + post
                if wins and a <= wins[-1][1]:
                    wins[-1] = (wins[-1][0], max(wins[-1][1], b))
                else:
                    wins.append((a, b))
        return wins
    begun: Optional[float] = None
    for f in frames:
        if begun is None and start(f):
            begun = f.ts
        elif begun is not None and end(f):
            wins.append((begun - pre, f.ts + post))
            begun = None
    if begun is not None:                       # never closed: keep until the last frame
        wins.append((begun - pre, frames[-1].ts if frames else begun))
    return wins


def cut(frames: list[Frame], wins: list[tuple[float, float]]) -> list[Frame]:
    out: list[Frame] = []
    for f in frames:
        if any(a <= f.ts <= b for a, b in wins):
            out.append(f)
    return out
