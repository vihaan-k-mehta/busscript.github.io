"""A small, safe scripting language for Busscript.

A script is a list of steps, one per line, that Busscript carries out for you:

    send VehicleSpeed Speed=50
    wait 2s
    log start run1.asc
    wait 10s
    log stop

It is deliberately NOT Python: there is no way to run arbitrary code from a script, only the commands below.
Sending frames obeys the same rules as everywhere else (the channel must not be listen-only).
"""
from __future__ import annotations

import difflib
import operator
import re
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .bus import Bus, BusError

COMMANDS = ["start", "stop", "wait", "send", "sendraw", "repeat", "every", "end", "log", "print"]
MAX_SECONDS = 1800        # a script is stopped after 30 minutes
MAX_WAIT = 3600           # one wait, in seconds
MAX_DEPTH = 4             # nested repeat / every blocks
MIN_PERIOD = 0.01         # at most 100 loops a second, so a script cannot flood the bus
MAX_REPEAT = 100_000
MAX_CHARS = 20_000
MAX_OUTPUT = 500

DURATION = re.compile(r"^(\d+(?:\.\d+)?)(ms|s|m)$", re.I)
SIGNAL = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*$")
IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
LOG_NAME = re.compile(r"^[A-Za-z0-9._-]{1,60}$")
SAVE_NAME = re.compile(r"^[A-Za-z0-9 _.-]{1,60}$")
OPS = {">": operator.gt, "<": operator.lt, ">=": operator.ge, "<=": operator.le, "==": operator.eq, "!=": operator.ne}


class ScriptError(Exception):
    """A mistake in a script, always with the line it is on."""

    def __init__(self, line: int, message: str):
        super().__init__(f"Line {line}: {message}")
        self.line = line
        self.message = message


class _Stopped(Exception):
    pass


@dataclass
class Step:
    kind: str
    line: int
    args: dict = field(default_factory=dict)
    body: Optional[list] = None


# ---------------------------------------------------------------- parsing
def _duration(token: str, line: int, what: str = "a time") -> float:
    m = DURATION.match(token)
    if not m:
        raise ScriptError(line, f"'{token}' is not {what}. Write it like 500ms, 2s or 1m.")
    secs = float(m.group(1)) * {"ms": 0.001, "s": 1.0, "m": 60.0}[m.group(2).lower()]
    if secs > MAX_WAIT:
        raise ScriptError(line, f"'{token}' is longer than {MAX_WAIT // 60} minutes.")
    return secs


def _value(token: str):
    t = token.strip("\"'")
    try:
        return float(t)
    except ValueError:
        return t


def _split_channel(tokens: list[str]) -> tuple[list[str], Optional[str]]:
    if len(tokens) >= 2 and tokens[-2].lower() == "on":
        return tokens[:-2], tokens[-1]
    return tokens, None


def parse(text: str) -> list[Step]:
    if len(text) > MAX_CHARS:
        raise ScriptError(1, f"This script is too long (more than {MAX_CHARS} characters).")
    root: list[Step] = []
    stack: list[tuple[Optional[Step], list[Step]]] = [(None, root)]
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        tokens = line.split()
        cmd, args = tokens[0].lower(), tokens[1:]
        cur = stack[-1][1]

        if cmd in ("start", "stop"):
            if args:
                raise ScriptError(n, f"'{cmd}' does not take anything after it.")
            cur.append(Step(cmd, n))
        elif cmd == "wait":
            if not args:
                raise ScriptError(n, "'wait' needs a time, like: wait 2s")
            if args[0].lower() == "until":
                if len(args) < 4:
                    raise ScriptError(n, "Write it like: wait until EngineData.EngineSpeed > 3000")
                sig, op, target = args[1], args[2], args[3]
                if not SIGNAL.match(sig):
                    raise ScriptError(n, f"'{sig}' is not a signal name. Use Message.Signal, like EngineData.EngineSpeed.")
                if op not in OPS:
                    raise ScriptError(n, f"'{op}' is not a comparison. Use one of: > < >= <= == !=")
                timeout = None
                rest = args[4:]
                if rest:
                    if len(rest) != 2 or rest[0].lower() != "timeout":
                        raise ScriptError(n, "After the value you can add: timeout 30s")
                    timeout = _duration(rest[1], n)
                cur.append(Step("wait_until", n, {"signal": sig, "op": op, "target": _value(target), "timeout": timeout}))
            else:
                if len(args) != 1:
                    raise ScriptError(n, "'wait' takes one time, like: wait 2s")
                cur.append(Step("wait", n, {"seconds": _duration(args[0], n)}))
        elif cmd == "send":
            args, ch = _split_channel(args)
            if not args:
                raise ScriptError(n, "'send' needs a message name, like: send VehicleSpeed Speed=50")
            msg, pairs = args[0], args[1:]
            if not IDENT.match(msg):
                raise ScriptError(n, f"'{msg}' is not a message name.")
            values = {}
            for p in pairs:
                k, eq, v = p.partition("=")
                if not eq or not IDENT.match(k) or v == "":
                    raise ScriptError(n, f"'{p}' should look like Speed=50 (a signal name, an equals sign and a value, with no spaces).")
                values[k] = _value(v)
            cur.append(Step("send", n, {"message": msg, "values": values, "channel": ch}))
        elif cmd == "sendraw":
            args, ch = _split_channel(args)
            ext = False
            if args and args[-1].lower() == "ext":
                ext, args = True, args[:-1]
            if not args:
                raise ScriptError(n, "'sendraw' needs an id and some bytes, like: sendraw 0x123 01 02 03")
            try:
                can_id = int(args[0], 0)
            except ValueError:
                raise ScriptError(n, f"'{args[0]}' is not an identifier. Write it like 0x123 or 291.") from None
            hexs = "".join(args[1:])
            if not re.fullmatch(r"(?:[0-9A-Fa-f]{2})*", hexs):
                raise ScriptError(n, "The data bytes must be hex pairs, like: 01 02 FF")
            cur.append(Step("sendraw", n, {"id": can_id, "data": bytes.fromhex(hexs), "ext": ext, "channel": ch}))
        elif cmd == "repeat":
            if len(args) != 1 or not args[0].isdigit() or not (1 <= int(args[0]) <= MAX_REPEAT):
                raise ScriptError(n, f"Write it like: repeat 5 (a number from 1 to {MAX_REPEAT}). End the block with a line that says: end")
            _open(stack, Step("repeat", n, {"count": int(args[0])}, []), n)
        elif cmd == "every":
            if len(args) != 3 or args[1].lower() != "for":
                raise ScriptError(n, "Write it like: every 100ms for 5s. End the block with a line that says: end")
            period = _duration(args[0], n)
            if period < MIN_PERIOD:
                raise ScriptError(n, f"'every' cannot be faster than every {int(MIN_PERIOD * 1000)}ms.")
            _open(stack, Step("every", n, {"period": period, "duration": _duration(args[2], n)}, []), n)
        elif cmd == "end":
            if args:
                raise ScriptError(n, "'end' does not take anything after it.")
            if len(stack) == 1:
                raise ScriptError(n, "This 'end' has nothing to end. Every 'end' closes a 'repeat' or 'every' above it.")
            stack.pop()
        elif cmd == "log":
            if len(args) == 1 and args[0].lower() == "stop":
                cur.append(Step("log_stop", n))
            elif len(args) == 2 and args[0].lower() == "start":
                name = args[1]
                if not LOG_NAME.match(name):
                    raise ScriptError(n, "Log file names can only use letters, digits, dots, dashes and underscores, like run1.asc")
                if "." not in name:
                    name += ".asc"
                if name.rsplit(".", 1)[1].lower() not in ("asc", "blf"):
                    raise ScriptError(n, "Log files end in .asc or .blf, like run1.asc")
                cur.append(Step("log_start", n, {"name": name}))
            else:
                raise ScriptError(n, "Write it like: log start run1.asc    or: log stop")
        elif cmd == "print":
            cur.append(Step("print", n, {"text": line[len(tokens[0]):].strip()}))
        else:
            close = difflib.get_close_matches(cmd, COMMANDS, n=1)
            hint = f" Did you mean '{close[0]}'?" if close else ""
            raise ScriptError(n, f"I do not know the command '{tokens[0]}'.{hint} The commands are: {', '.join(COMMANDS)}.")
    if len(stack) > 1:
        blk = stack[-1][0]
        raise ScriptError(blk.line, f"This '{blk.kind}' is never closed. Add a line that says: end")
    return root


def _open(stack, step: Step, n: int) -> None:
    if len(stack) > MAX_DEPTH:
        raise ScriptError(n, f"Blocks can only be nested {MAX_DEPTH} deep.")
    stack[-1][1].append(step)
    stack.append((step, step.body))


# ------------------------------------------------------------- running
class ScriptRunner:
    """Runs one script at a time on a background thread and keeps its output for the UI."""

    def __init__(self, bus: Bus, data_dir: Path):
        self.bus = bus
        self.data_dir = Path(data_dir)
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self.output: deque[dict] = deque(maxlen=MAX_OUTPUT)
        self.running = False
        self.line = 0
        self.error: Optional[str] = None
        self.error_line: Optional[int] = None
        self.finished = False
        self.stopped_by_user = False
        self._t0 = 0.0
        self._deadline = 0.0
        self._owns_log = False

    # -- public
    def start(self, text: str) -> dict:
        steps = parse(text)               # mistakes are reported before anything runs
        with self._lock:
            if self.running:
                raise BusError("A script is already running. Stop it first.")
            self.output.clear()
            self.running, self.finished, self.error, self.error_line = True, False, None, None
            self.stopped_by_user, self.line, self._owns_log = False, 0, False
            self._stop.clear()
            self._t0 = time.monotonic()
            self._deadline = self._t0 + MAX_SECONDS
            self._thread = threading.Thread(target=self._run, args=(steps,), daemon=True, name="busscript-script")
            self._thread.start()
        return self.state()

    def stop(self) -> dict:
        self._stop.set()
        t = self._thread
        if t is not None and t.is_alive() and t is not threading.current_thread():
            t.join(timeout=3.0)
        return self.state()

    def state(self) -> dict:
        return {
            "running": self.running, "finished": self.finished, "line": self.line,
            "ok": self.finished and self.error is None and not self.stopped_by_user,
            "stopped": self.stopped_by_user, "error": self.error, "error_line": self.error_line,
            "elapsed": round(time.monotonic() - self._t0, 1) if self.running else 0.0,
            "output": list(self.output),
        }

    # -- internals
    def _say(self, text: str, level: str = "info") -> None:
        t = round(time.monotonic() - self._t0, 2)
        last = self.output[-1] if self.output else None
        if last is not None and last["text"] == text and last["level"] == level:
            last["count"] += 1                      # the same thing again and again: one line with a count
            last["t_last"] = t
        else:
            self.output.append({"t": t, "t_last": t, "text": text, "level": level, "count": 1})

    def _run(self, steps: list[Step]) -> None:
        try:
            self._exec(steps)
            self._say("Finished.")
        except _Stopped:
            self.stopped_by_user = True
            self._say("Stopped.", "warn")
        except ScriptError as e:
            self.error, self.error_line = str(e), e.line
            self._say(str(e), "error")
        except Exception as e:  # never let a script thread die silently
            self.error = f"Something unexpected went wrong: {e}"
            self._say(self.error, "error")
        finally:
            if self._owns_log:
                info = self.bus.stop_logging()
                if info:
                    self._say(f"Closed the log file ({info['frames']} frames).")
            self.running, self.finished = False, True

    def _sleep(self, seconds: float, line: int) -> None:
        end = time.monotonic() + seconds
        while True:
            left = end - time.monotonic()
            if self._stop.is_set():
                raise _Stopped()
            if time.monotonic() > self._deadline:
                raise ScriptError(line, f"This script ran for more than {MAX_SECONDS // 60} minutes, so it was stopped.")
            if left <= 0:
                return
            self._stop.wait(min(left, 0.05))

    def _channel(self, name: Optional[str], line: int) -> str:
        if name:
            if name not in self.bus.channels:
                raise ScriptError(line, f"There is no channel called '{name}'. Channels: {', '.join(self.bus.channels) or 'none'}.")
            return name
        if not self.bus.channels:
            raise ScriptError(line, "There is no channel yet. Add one on the Setup tab.")
        return next(iter(self.bus.channels))

    def _fail(self, line: int, e: BusError) -> ScriptError:
        msg = str(e)
        if "not running" in msg:
            msg += " Press Start first, or begin the script with a line that says: start"
        return ScriptError(line, msg)

    def _text(self, text: str) -> str:
        def sub(m):
            v = self.bus.current_signal(m.group(1))
            return "?" if v is None else str(v)
        return re.sub(r"\{([A-Za-z_]\w*\.[A-Za-z_]\w*)\}", sub, text)

    def _exec(self, steps: list[Step]) -> None:
        for s in steps:
            if self._stop.is_set():
                raise _Stopped()
            self.line = s.line
            k, a = s.kind, s.args
            try:
                if k == "start":
                    self.bus.start()
                    self._say("Measurement started.")
                elif k == "stop":
                    self.bus.stop()
                    self._say("Measurement stopped.")
                elif k == "wait":
                    self._sleep(a["seconds"], s.line)
                elif k == "wait_until":
                    self._wait_until(s)
                elif k == "send":
                    ch = self._channel(a["channel"], s.line)
                    self.bus.send_signals(ch, a["message"], a["values"])
                    shown = ", ".join(f"{n}={v:g}" if isinstance(v, float) else f"{n}={v}" for n, v in a["values"].items())
                    self._say(f"Sent {a['message']}" + (f" ({shown})" if shown else ""))
                elif k == "sendraw":
                    ch = self._channel(a["channel"], s.line)
                    self.bus.send(ch, a["id"], a["data"], a["ext"], fd=len(a["data"]) > 8)
                    self._say(f"Sent frame 0x{a['id']:X}: {a['data'].hex(' ').upper() or '(no data)'}")
                elif k == "repeat":
                    for _ in range(a["count"]):
                        self._exec(s.body)
                elif k == "every":
                    end = time.monotonic() + a["duration"]
                    nxt = time.monotonic()
                    while time.monotonic() < end:
                        self._exec(s.body)
                        nxt += a["period"]
                        if nxt < time.monotonic():
                            nxt = time.monotonic()      # fell behind: do not burst to catch up
                        self._sleep(nxt - time.monotonic(), s.line)
                elif k == "log_start":
                    path = self.data_dir / "logs" / a["name"]
                    path.parent.mkdir(parents=True, exist_ok=True)
                    self.bus.start_logging(str(path), a["name"].rsplit(".", 1)[1].lower())
                    self._owns_log = True
                    self._say(f"Recording to {path}")
                elif k == "log_stop":
                    info = self.bus.stop_logging()
                    self._owns_log = False
                    self._say(f"Saved {info['frames']} frames to {info['path']}" if info else "No log was open.")
                elif k == "print":
                    self._say(self._text(a["text"]))
            except BusError as e:
                raise self._fail(s.line, e) from e

    def _wait_until(self, s: Step) -> None:
        a = s.args
        op = OPS[a["op"]]
        start = time.monotonic()
        self._say(f"Waiting until {a['signal']} {a['op']} {a['target']:g}" if isinstance(a["target"], float)
                  else f"Waiting until {a['signal']} {a['op']} {a['target']}")
        while True:
            v = self.bus.current_signal(a["signal"])
            if v is not None:
                try:
                    if isinstance(a["target"], float) and isinstance(v, (int, float)):
                        if op(float(v), a["target"]):
                            return
                    elif a["op"] in ("==", "!=") and op(str(v), str(a["target"])):
                        return
                except TypeError:
                    pass
            if a["timeout"] is not None and time.monotonic() - start > a["timeout"]:
                raise ScriptError(s.line, f"Waited {a['timeout']:g} seconds but {a['signal']} never became {a['op']} {a['target']}. "
                                          "Check the signal name, or that frames for it are arriving.")
            self._sleep(0.05, s.line)


# --------------------------------------------------- saved scripts and examples
class ScriptLibrary:
    def __init__(self, data_dir: Path):
        self.dir = Path(data_dir) / "scripts"
        self.dir.mkdir(parents=True, exist_ok=True)

    def _path(self, name: str) -> Path:
        if not SAVE_NAME.match(name):
            raise BusError("Script names can use letters, digits, spaces, dots, dashes and underscores (up to 60).")
        return self.dir / f"{name}.bus"

    def names(self) -> list[str]:
        return sorted(p.stem for p in self.dir.glob("*.bus"))

    def get(self, name: str) -> str:
        p = self._path(name)
        if not p.is_file():
            raise BusError("No script with that name.")
        return p.read_text(encoding="utf-8")

    def save(self, name: str, text: str) -> None:
        if len(text) > MAX_CHARS:
            raise BusError("That script is too long to save.")
        self._path(name).write_text(text, encoding="utf-8")

    def delete(self, name: str) -> None:
        self._path(name).unlink(missing_ok=True)


TEMPLATES = [
    {"id": "send-once", "title": "Send one message",
     "about": "Sends a vehicle speed of 50 km/h once.",
     "text": "# Sends one message using a signal name and a value.\n# Change VehicleSpeed and Speed to a message and signal from your own database.\nsend VehicleSpeed Speed=50\nprint Sent a speed of 50 km/h\n"},
    {"id": "every-100ms", "title": "Send again and again",
     "about": "Sends a speed every 100 milliseconds for 5 seconds.",
     "text": "# Do the lines between 'every' and 'end' every 100ms, for 5 seconds.\nevery 100ms for 5s\n  send VehicleSpeed Speed=42.5\nend\nprint Done\n"},
    {"id": "record", "title": "Record 10 seconds to a file",
     "about": "Saves everything on the bus for 10 seconds into your logs folder.",
     "text": "# Recordings are saved in the logs folder inside your busscript-data folder.\nlog start my_recording.asc\nprint Recording for 10 seconds...\nwait 10s\nlog stop\n"},
    {"id": "wait-high", "title": "Tell me when a value gets high",
     "about": "Waits (up to 30 seconds) until engine speed passes 3000, then says so.",
     "text": "# Works with the demo traffic. Use a signal from your own database as Message.Signal.\nprint Waiting for engine speed above 3000 rpm (up to 30 seconds)...\nwait until EngineData.EngineSpeed > 3000 timeout 30s\nprint Engine speed reached {EngineData.EngineSpeed} rpm\n"},
    {"id": "steps", "title": "Step a value up and down",
     "about": "Switches the speed between 20 and 60 km/h, five times.",
     "text": "# Repeat the lines between 'repeat' and 'end' five times.\nrepeat 5\n  send VehicleSpeed Speed=20\n  wait 1s\n  send VehicleSpeed Speed=60\n  wait 1s\nend\nprint Finished stepping\n"},
]
