"""Busscript from a terminal.

  busscript doctor                       is everything installed? (drivers, libraries)
  busscript describe 0x7E8 04 41 0C 1A F8   what a frame means (J1939, OBD-II, UDS)
  busscript stats recording.asc          how often each message arrives
  busscript find recording.asc "id == 0x100 and d0 == 5"
  busscript diag recording.asc           fault codes and diagnostic conversations
  busscript convert in.blf out.csv       change format (csv, asc, blf, mf4); --where / --around cut a part
  busscript status | frames | signals    ask a running Busscript what it sees
  busscript start | stop                 start or stop the measurement of a running Busscript
  busscript mcp                          connect Claude: prints the one prompt (add --setup to do it for you)

The first group works on files and needs nothing else running. The second group talks to the Busscript window
(or `busscript serve`) that is open on this PC, using the same private token the window uses."""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional

from . import analysis, protocols, quickstart
from .bus import BusError
from .doctor import check as check_setup
from .export import FORMATS, write_frames
from .files import read_log
from .store import Store

COMMANDS = {"doctor", "describe", "stats", "find", "diag", "convert", "status", "frames", "signals", "start", "stop",
            "mcp", "serve", "help"}
FILE_FORMATS = ", ".join(FORMATS)


def _hexid(text: str) -> int:
    try:
        return int(text, 16) if text.lower().startswith("0x") else int(text)
    except ValueError:
        raise BusError(f"'{text}' is not a message ID. Write it like 0x7E8 or 2024.")


def _load(path: str):
    p = Path(path)
    if not p.exists():
        raise BusError(f"There is no file called '{path}'.")
    return read_log(p, p.name, lambda n: f"CAN{n if n else 1}")


def _hex(i: int, ext: bool) -> str:
    return f"0x{i:08X}" if ext else f"0x{i:03X}"


def _table(head: list[str], rows: list[list[str]]) -> str:
    cols = [max(len(str(x)) for x in c) for c in zip(head, *rows)] if rows else [len(h) for h in head]
    line = lambda r: "  ".join(str(x).ljust(w) for x, w in zip(r, cols)).rstrip()
    return "\n".join([line(head), line(["-" * w for w in cols])] + [line(r) for r in rows])


def _frame_line(f) -> str:
    data = " ".join(f"{b:02X}" for b in f.data)
    return f"{f.ts:10.3f}  {f.channel:<6} {_hex(f.can_id, f.ext):<10} {f.direction}  [{f.dlc}]  {data}" + ("  ERROR" if f.error else "")


# ----------------------------------------------------------------------------- file commands
def cmd_doctor(a) -> int:
    d = check_setup(Store(a.data_dir).data_dir)
    print(f"Python {d['python']}   data folder: {d['data_dir']}")
    print("\nLibraries")
    for l in d["libraries"]:
        print(f"  {'ok     ' if l['ok'] else 'MISSING'} {l['name']:<10} {l['version'] or l['fix']}")
    print("\nAdapter drivers (only the one you own matters)")
    for x in d["drivers"]:
        print(f"  {'found  ' if x['found'] else '-      '} {x['name']:<22} {'' if x['found'] else x['fix']}")
    ports = d["serial_ports"]
    print("\nSerial ports: " + (", ".join(f"{p['port']} ({p['label']})" for p in ports) if ports else "none plugged in"))
    print("\n" + ("Everything Busscript needs is installed." if d["ready"] else "Problems: " + "; ".join(d["problems"])))
    return 0 if d["ready"] else 1


def cmd_describe(a) -> int:
    raw = bytes.fromhex("".join(a.data).replace(" ", "")) if a.data else b""
    text = protocols.describe(_hexid(a.id), a.ext, raw)
    print(text or "Nothing special: this is not a J1939, OBD-II or UDS message that Busscript knows.")
    return 0


def cmd_stats(a) -> int:
    v = _load(a.file)
    rows = analysis.report(v.frames)
    if a.json:
        print(json.dumps({"frames": len(v.frames), "rows": rows}, indent=2))
        return 0
    n = lambda x: "-" if x is None else f"{x:.2f}"
    print(f"{a.file}: {len(v.frames):,} frames, {v.duration:.3f} s, {len(rows)} messages\n")
    print(_table(["Channel", "ID", "Dir", "Frames", "Avg ms", "Spread ms", "Min ms", "Max ms"],
                 [[r["channel"], _hex(r["id"], r["ext"]), r["dir"], f"{r['count']:,}", n(r["mean_ms"]), n(r["sd_ms"]),
                   n(r["min_ms"]), n(r["max_ms"])] for r in rows]))
    return 0


def cmd_find(a) -> int:
    v = _load(a.file)
    cond = analysis.parse_condition(a.condition)
    hits = [i for i, f in enumerate(v.frames) if cond(f)]
    for i in hits[: a.max]:
        print(_frame_line(v.frames[i]))
    more = len(hits) - a.max
    print(f"\n{len(hits):,} matching frame(s)" + (f", showing the first {a.max}. Use --max to see more." if more > 0 else "."))
    return 0 if hits else 1


def cmd_diag(a) -> int:
    msgs = protocols.assemble(_load(a.file).frames)
    if not msgs:
        print("No diagnostic (OBD-II or UDS) messages in this file.")
        return 1
    for m in msgs:
        print(f"{m['ts']:10.3f}  {_hex(m['id'], m['ext']):<10} {m['text']}")
        for d in m["details"]:
            print(f"{'':24}{d}")
    return 0


def cmd_convert(a) -> int:
    fmt = Path(a.out).suffix.lower().lstrip(".")
    if fmt not in FORMATS:
        raise BusError(f"I can save as {FILE_FORMATS}. Give the output file one of those endings, like out.csv.")
    frames = _load(a.file).frames
    if a.around:
        wins = analysis.trigger_windows(frames, analysis.parse_condition(a.around), a.before, a.after,
                                        analysis.parse_condition(a.until) if a.until else None)
        frames = analysis.cut(frames, wins)
    if a.where:
        keep = analysis.parse_condition(a.where)
        frames = [f for f in frames if keep(f)]
    if not frames:
        raise BusError("Nothing to save: no frames matched.")
    out = Path(a.out)
    if out.exists() and not a.force:
        raise BusError(f"'{a.out}' already exists. Add --force to replace it.")
    n = write_frames(frames, out, fmt)
    print(f"Saved {n:,} frames to {out}")
    return 0


# ----------------------------------------------------------------------------- talking to a running Busscript
class Live:
    """A running Busscript on this PC: found on the usual port (or the next ones), reached with the private token."""

    def __init__(self, store: Store, port: int):
        self.token = store.token()
        self.base: Optional[str] = None
        for p in range(port, port + 20):
            try:
                self.base = f"http://127.0.0.1:{p}"
                self.call("GET", "/api/state")
                return
            except (urllib.error.URLError, OSError, ConnectionError):
                self.base = None
        raise BusError("Busscript is not running. Open the Busscript window (or run: busscript serve) and try again.")

    def call(self, method: str, path: str):
        req = urllib.request.Request(self.base + path, method=method, headers={"Authorization": f"Bearer {self.token}"})
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return json.loads(r.read() or b"null")
        except urllib.error.HTTPError as e:
            try:
                msg = json.loads(e.read()).get("detail") or e.reason
            except Exception:
                msg = e.reason
            if e.code in (401, 403):      # a different program is on this port, or a different data folder
                raise OSError("not busscript")
            raise BusError(str(msg))


def _live(a) -> Live:
    return Live(Store(a.data_dir), a.port)


def cmd_status(a) -> int:
    live = _live(a)
    s, ch, st = live.call("GET", "/api/state"), live.call("GET", "/api/channels"), live.call("GET", "/api/statistics")
    if a.json:
        print(json.dumps({"state": s, "channels": ch, "statistics": st}, indent=2))
        return 0
    print(f"Busscript at {live.base}")
    print(f"Measurement: {'running' if s['running'] else 'stopped'}" + ("   (demo traffic)" if s["demo"] else ""))
    print(f"Frames buffered: {s['frames_buffered']:,}")
    print("Channels: " + (", ".join(f"{c['name']} ({c['interface']} {c['channel']}{', listen-only' if c.get('listen_only') else ''})" for c in ch) or "none yet"))
    for name, x in st.items():
        print(f"  {name}: {x['rx_frames']:,} received, {x['tx_frames']:,} sent, {x['error_frames']} errors, bus load {x['load_pct']}%")
    return 0


def cmd_frames(a) -> int:
    for f in _live(a).call("GET", f"/api/frames/recent?n={a.n}"):
        data = " ".join(f"{b:02X}" for b in bytes.fromhex(f["data"]))
        print(f"{f['ts']:10.3f}  {f['ch']:<6} {_hex(f['id'], f['ext']):<10} {f['dir']}  [{f['dlc']}]  {data}")
    return 0


def cmd_signals(a) -> int:
    vals = _live(a).call("GET", "/api/signal-values")
    if not vals:
        print("No signals yet. Attach a database to a channel and start a measurement.")
        return 1
    print(_table(["Signal", "Value", "Unit"], [[f"{v['message']}.{v['signal']}", v["value"], v.get("unit") or ""] for v in vals]))
    return 0


def cmd_startstop(a) -> int:
    s = _live(a).call("POST", f"/api/measurement/{a.command}")
    print("Measurement " + ("running." if s["running"] else "stopped."))
    return 0


def cmd_mcp(a) -> int:
    store = Store(a.data_dir)
    try:
        live = Live(store, a.port)
        url = live.base + "/mcp"
    except BusError:
        url = f"http://127.0.0.1:{a.port}/mcp"
        live = None
    cmd = quickstart.http_command(url, store.token())
    if not a.setup:
        print("Paste this one prompt into Claude Code and it sets everything up:\n")
        print(quickstart.prompt(url, store.token()))
        print("\nOr let Busscript do the setup now:  busscript mcp --setup")
        if live is None:
            print("(Busscript is not open at the moment: open it before Claude connects.)")
        return 0
    claude = shutil.which("claude")
    if not claude:
        print("I could not find Claude Code on this PC (no 'claude' command). Paste the prompt instead: busscript mcp")
        return 1
    store.set_setting("mcp.enabled", "true")                   # MCP is off until you say so; --setup is you saying so
    import shlex
    r = subprocess.run([claude] + shlex.split(cmd, posix=False)[1:], capture_output=True, text=True, timeout=60)
    print((r.stdout or r.stderr or "").strip())
    if r.returncode == 0:
        print("\nDone. MCP is on. Restart Claude Code, then ask it: what is on the CAN bus?")
        print("Transmitting stays off until you allow it in the MCP window.")
    return r.returncode


def cmd_help(a) -> int:
    print(__doc__)
    return 0


HANDLERS = {"doctor": cmd_doctor, "describe": cmd_describe, "stats": cmd_stats, "find": cmd_find, "diag": cmd_diag,
            "convert": cmd_convert, "status": cmd_status, "frames": cmd_frames, "signals": cmd_signals,
            "start": cmd_startstop, "stop": cmd_startstop, "mcp": cmd_mcp, "help": cmd_help}


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="busscript", description="Busscript from a terminal. Run 'busscript help' for examples.")
    ap.add_argument("--data-dir", type=Path, default=None, help="where Busscript keeps its data (default: busscript-data in your home folder)")
    ap.add_argument("--port", type=int, default=8765, help="where to look for a running Busscript (default 8765)")
    sub = ap.add_subparsers(dest="command", required=True)
    sub.add_parser("doctor", help="check the libraries and adapter drivers")
    p = sub.add_parser("describe", help="say what a frame means")
    p.add_argument("id", help="message ID, like 0x7E8")
    p.add_argument("data", nargs="*", help="data bytes in hex, like 04 41 0C 1A F8")
    p.add_argument("--ext", action="store_true", help="the ID is a 29-bit extended ID")
    p = sub.add_parser("stats", help="how often each message arrives")
    p.add_argument("file")
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("find", help="list frames matching a condition")
    p.add_argument("file")
    p.add_argument("condition", help='like "id == 0x100 and d0 == 5"')
    p.add_argument("--max", type=int, default=20, help="how many to show (default 20)")
    p = sub.add_parser("diag", help="fault codes and diagnostic conversations in a file")
    p.add_argument("file")
    p = sub.add_parser("convert", help="save a recording in another format, or only a part of it")
    p.add_argument("file")
    p.add_argument("out", help=f"output file; the ending picks the format ({FILE_FORMATS})")
    p.add_argument("--where", help="only keep frames matching this condition")
    p.add_argument("--around", help="only keep the time around frames matching this condition")
    p.add_argument("--until", help="with --around: keep until a frame matching this")
    p.add_argument("--before", type=float, default=0.5, help="seconds to keep before (default 0.5)")
    p.add_argument("--after", type=float, default=0.5, help="seconds to keep after (default 0.5)")
    p.add_argument("--force", action="store_true", help="replace the output file if it exists")
    p = sub.add_parser("status", help="what the running Busscript is doing")
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("frames", help="the latest frames of the running Busscript")
    p.add_argument("-n", type=int, default=20)
    sub.add_parser("signals", help="live signal values (needs a database)")
    sub.add_parser("start", help="start the measurement")
    sub.add_parser("stop", help="stop the measurement")
    p = sub.add_parser("mcp", help="connect Claude: prints the one prompt to paste")
    p.add_argument("--setup", action="store_true", help="turn MCP on and register it with Claude Code for you")
    sub.add_parser("help", help="show examples")
    return ap


def _global_options_first(argv: list[str]) -> list[str]:
    """Let --data-dir and --port go anywhere on the line, not only before the command."""
    front, rest, i = [], [], 0
    while i < len(argv):
        if argv[i] in ("--data-dir", "--port") and i + 1 < len(argv):
            front += argv[i:i + 2]
            i += 2
        else:
            rest.append(argv[i])
            i += 1
    return front + rest


def run(argv: list[str]) -> int:
    if argv and argv[0] == "help":
        return cmd_help(None)
    for stream in (sys.stdout, sys.stderr):                      # the degree sign etc. must not crash an old console
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    args = build_parser().parse_args(_global_options_first(argv))
    try:
        return HANDLERS[args.command](args)
    except (BusError, OSError, ValueError, subprocess.SubprocessError) as e:
        print(f"busscript: {e}", file=sys.stderr)
        return 2
