"""FastAPI app: REST + WebSocket for the UI, MCP at /mcp. Loopback only, bearer token required."""
from __future__ import annotations

import asyncio
import contextlib
import hmac
import json
import time
import uuid
from collections import deque
from pathlib import Path
from typing import Optional

import can
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.types import ASGIApp, Receive, Scope, Send

from .bus import Bus, BusError
from .mcp_server import ALL_TOOLS, McpPolicy, build_mcp
from .models import ChannelConfig, FilterRule, Frame
from .files import MAX_UPLOAD, FileSession, kind_of, safe_name, supported_text, unique_path
from .script import TEMPLATES, ScriptError, ScriptLibrary, ScriptRunner, parse as parse_script
from .store import Store

UI_DIST = Path(__file__).resolve().parents[1] / "ui" / "dist"
ALLOWED_HOSTS = {"127.0.0.1", "localhost", "[::1]", "testserver"}
BATCH_S = 0.05
QUEUE_MAX = 20000


class ChannelIn(BaseModel):
    name: str
    interface: str = "virtual"
    channel: str = "vcan0"
    bitrate: int = 500000
    fd_enabled: bool = False
    fd_data_bitrate: Optional[int] = None
    listen_only: bool = True


class DbIn(BaseModel):
    channel: str
    path: str


class FilterIn(BaseModel):
    id: Optional[str] = None
    mode: str
    id_from: int
    id_to: int
    channel: Optional[str] = None
    ext: bool = False
    direction: str = "both"
    enabled: bool = True


class TxIn(BaseModel):
    channel: str
    id: int
    data: str = ""
    ext: bool = False
    fd: bool = False
    period_ms: Optional[int] = None


class TxSignalIn(BaseModel):
    channel: str
    message: str
    signals: dict[str, float | str]


class LogIn(BaseModel):
    path: str
    format: str = "asc"


class ReplayIn(BaseModel):
    path: str
    channel: Optional[str] = None
    speed: float = 1.0
    loop: bool = False


class OpenIn(BaseModel):
    name: str
    channel: Optional[str] = None


class ScriptIn(BaseModel):
    text: str


class WatchIn(BaseModel):
    signals: list[str]


def _hex(s: str) -> bytes:
    try:
        return bytes.fromhex(s.replace(" ", ""))
    except ValueError:
        raise BusError("data must be hexadecimal bytes")


def _host_only(raw: str) -> str:
    if raw.startswith("["):
        return raw.split("]")[0] + "]"
    return raw.rsplit(":", 1)[0] if ":" in raw else raw


class GuardMiddleware:
    """Rejects non-loopback Host headers (DNS rebinding) and API requests without the token."""

    def __init__(self, app: ASGIApp, token: str):
        self.app, self.token = app, token

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] not in ("http", "websocket"):
            return await self.app(scope, receive, send)
        headers = {k.decode().lower(): v.decode() for k, v in scope["headers"]}
        if _host_only(headers.get("host", "")) not in ALLOWED_HOSTS:
            return await self._deny(scope, send, 403, "bad host")
        origin = headers.get("origin")
        if origin and _host_only(origin.split("://", 1)[-1]) not in ALLOWED_HOSTS:
            return await self._deny(scope, send, 403, "bad origin")
        if scope["path"].startswith(("/api", "/ws", "/mcp")):
            supplied = headers.get("authorization", "").removeprefix("Bearer ").strip()
            if not supplied and scope["type"] == "websocket":
                q = scope.get("query_string", b"").decode()
                supplied = next((p[6:] for p in q.split("&") if p.startswith("token=")), "")
            if not hmac.compare_digest(supplied.encode(), self.token.encode()):
                return await self._deny(scope, send, 401, "missing or wrong token")
        return await self.app(scope, receive, send)

    async def _deny(self, scope, send, status, msg):
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})
            return
        body = json.dumps({"detail": msg}).encode()
        await send({"type": "http.response.start", "status": status,
                    "headers": [(b"content-type", b"application/json"),
                                (b"content-length", str(len(body)).encode())]})
        await send({"type": "http.response.body", "body": body})


def create_app(bus: Bus, store: Store, token: str) -> FastAPI:
    policy = McpPolicy(store)
    fsession = FileSession(bus, store.data_dir)
    mcp = build_mcp(bus, store, policy, fsession)
    mcp_app = mcp.streamable_http_app()

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI):
        async with mcp_app.router.lifespan_context(mcp_app):
            yield
        runner.stop()
        bus.stop()

    app = FastAPI(title="Busscript", lifespan=lifespan)
    app.add_middleware(GuardMiddleware, token=token)

    runner = ScriptRunner(bus, store.data_dir)
    library = ScriptLibrary(store.data_dir)
    app.state.scripts_dir = library.dir

    @app.exception_handler(ScriptError)
    async def script_error(_: Request, e: ScriptError):
        return JSONResponse({"detail": str(e), "line": e.line}, status_code=400)

    @app.exception_handler(BusError)
    async def bus_error(_: Request, e: BusError):
        return JSONResponse({"detail": str(e)}, status_code=400)

    # ------------------------------------------------------------ adapters
    @app.get("/api/adapters")
    def adapters():
        out = [{"interface": "virtual", "channel": "vcan0", "label": "Virtual bus (no hardware)"}]
        try:
            for c in can.detect_available_configs():
                if c.get("interface") != "virtual":
                    out.append({**c, "label": f"{c.get('interface')} {c.get('channel')}"})
        except Exception:
            pass
        return out

    # ------------------------------------------------------------ channels
    @app.get("/api/channels")
    def channels():
        return bus.list_channels()

    @app.put("/api/channels")
    def put_channel(c: ChannelIn):
        cfg = bus.set_channel(ChannelConfig(**c.model_dump()))
        store.save_channel(cfg)
        return cfg.to_dict()

    @app.delete("/api/channels/{name}")
    def del_channel(name: str):
        bus.remove_channel(name)
        store.delete_channel(name)
        return {"ok": True}

    # --------------------------------------------------------- measurement
    @app.get("/api/state")
    def state():
        return bus.state()

    @app.post("/api/measurement/start")
    def start():
        fsession.close()
        return bus.start()

    @app.post("/api/measurement/stop")
    def stop():
        return bus.stop()

    # ----------------------------------------------------------- databases
    @app.post("/api/databases")
    def add_db(d: DbIn):
        res = bus.load_database(d.channel, d.path)
        store.add_database(d.channel, d.path)
        return res

    @app.delete("/api/databases/{channel}")
    def clear_db(channel: str):
        bus.unload_databases(channel)
        store.clear_databases(channel)
        return {"ok": True}

    @app.get("/api/signals")
    def signals():
        return bus.catalogue()

    @app.get("/api/signal-values")
    def signal_values():
        return bus.signal_values()

    @app.put("/api/watch")
    def watch(w: WatchIn):
        bus.watch(w.signals)
        return {"ok": True}

    @app.get("/api/signals/history")
    def history(name: str, since: float = 0.0):
        return bus.history(name, since)

    @app.get("/api/frames/recent")
    def recent(n: int = 100, id: Optional[int] = None, channel: Optional[str] = None):
        return bus.recent(n, can_id=id, channel=channel, hard_cap=5000)

    @app.get("/api/frames/decode")
    def decode(channel: str, id: int, data: str, ext: bool = False):
        d = _hex(data)
        f = Frame(ts=0, channel=channel, can_id=id, ext=ext, fd=False, direction="rx", dlc=len(d), data=d)
        m = fsession._message(f)          # tries this channel first, then any channel that has a database
        return fsession._decode(f, m) if m is not None else []

    @app.get("/api/statistics")
    def statistics():
        return bus.statistics()

    # ------------------------------------------------------------- filters
    @app.get("/api/filters")
    def get_filters():
        return [r.to_dict() for r in bus.filters.values()]

    @app.put("/api/filters")
    def put_filter(f: FilterIn):
        rule = FilterRule(id=f.id or uuid.uuid4().hex[:8], mode=f.mode, id_from=f.id_from, id_to=f.id_to,
                          channel=f.channel, ext=f.ext, direction=f.direction, enabled=f.enabled)
        return bus.set_filter(rule).to_dict()

    @app.delete("/api/filters/{rule_id}")
    def del_filter(rule_id: str):
        bus.delete_filter(rule_id)
        return {"ok": True}

    # ------------------------------------------------------------ transmit
    @app.post("/api/transmit/once")
    def tx_once(t: TxIn):
        return bus.send(t.channel, t.id, _hex(t.data), t.ext, t.fd)

    @app.post("/api/transmit/signal")
    def tx_signal(t: TxSignalIn):
        return bus.send_signals(t.channel, t.message, t.signals)

    @app.post("/api/transmit/cyclic")
    def tx_cyclic(t: TxIn):
        if not t.period_ms:
            raise BusError("period_ms is required for cyclic transmit")
        return bus.start_cyclic(t.channel, t.id, _hex(t.data), t.period_ms, t.ext, t.fd)

    @app.delete("/api/transmit/cyclic/{tid}")
    def tx_stop(tid: str):
        bus.stop_cyclic(tid)
        return {"ok": True}

    # ------------------------------------------------------ logging, replay
    @app.post("/api/log/start")
    def log_start(l: LogIn):
        return bus.start_logging(l.path, l.format)

    @app.post("/api/log/stop")
    def log_stop():
        return bus.stop_logging() or {"logging": False}

    @app.post("/api/replay/start")
    def replay_start(r: ReplayIn):
        return bus.start_replay(r.path, r.channel, r.speed, r.loop)

    @app.post("/api/replay/stop")
    def replay_stop():
        bus.stop_replay()
        return {"ok": True}

    # --------------------------------------------------------------- files
    @app.get("/api/files")
    def files_list():
        return {"stored": fsession.stored_files(), "supported": supported_text(), "open": fsession.summary()}

    @app.put("/api/files/{filename}")
    async def files_upload(filename: str, request: Request):
        name = safe_name(filename)
        declared = request.headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > MAX_UPLOAD:
            raise BusError(f"That file is larger than {MAX_UPLOAD // (1024 * 1024)} MB, which is the limit.")
        dest = unique_path(fsession.dir, name)
        size = 0
        try:
            with open(dest, "wb") as f:
                async for chunk in request.stream():
                    size += len(chunk)
                    if size > MAX_UPLOAD:
                        raise BusError(f"That file is larger than {MAX_UPLOAD // (1024 * 1024)} MB, which is the limit.")
                    f.write(chunk)
        except BaseException:
            dest.unlink(missing_ok=True)
            raise
        return {"name": dest.name, "size": size, "kind": kind_of(dest.name)}

    @app.post("/api/files/open")
    def files_open(o: OpenIn):
        path = fsession.stored_path(o.name)
        kind = kind_of(path.name)
        if kind == "database":
            channel = o.channel or next(iter(bus.channels), None)
            created = False
            if channel is None:   # a database with no channel yet: make a quiet one to hold it
                cfg = bus.set_channel(ChannelConfig(name="file", interface="virtual", channel="file", listen_only=True))
                store.save_channel(cfg)
                channel, created = cfg.name, True
            res = bus.load_database(channel, str(path))
            store.add_database(channel, str(path))
            return {"kind": "database", "channel": channel, "created_channel": created, **res}
        return {"kind": "log", **fsession.open(path, path.name)}

    @app.delete("/api/files/stored/{name}")
    def files_delete(name: str):
        fsession.stored_path(name).unlink(missing_ok=True)
        return {"ok": True}

    @app.get("/api/file")
    def file_summary():
        return fsession.summary()

    @app.delete("/api/file")
    def file_close():
        fsession.close()
        return {"ok": True}

    @app.get("/api/file/frames")
    def file_frames(offset: int = 0, limit: int = 200, id: Optional[int] = None, ext: bool = False):
        return {"total": fsession.count(id, ext), "frames": fsession.frame_dicts(offset, limit, id, ext)}

    @app.get("/api/file/seek")
    def file_seek(t: float, id: Optional[int] = None, ext: bool = False):
        return {"index": fsession.seek(t, id, ext)}

    @app.get("/api/file/overview")
    def file_overview():
        return fsession.overview()

    @app.get("/api/file/signal-values")
    def file_signal_values():
        return fsession.signal_values()

    @app.get("/api/file/signals")
    def file_signals():
        return fsession.catalogue()

    @app.get("/api/file/series")
    def file_series(name: str, max_points: int = 5000):
        return fsession.series(name, max_points)

    # ------------------------------------------------------------- scripts
    @app.get("/api/scripts/templates")
    def script_templates():
        return TEMPLATES

    @app.post("/api/scripts/check")
    def script_check(sc: ScriptIn):
        try:
            steps = parse_script(sc.text)
        except ScriptError as e:
            return {"ok": False, "line": e.line, "message": str(e)}
        return {"ok": True, "steps": len(steps)}

    @app.post("/api/scripts/run")
    def script_run(sc: ScriptIn):
        return runner.start(sc.text)

    @app.post("/api/scripts/stop")
    def script_stop():
        return runner.stop()

    @app.get("/api/scripts/status")
    def script_status():
        return runner.state()

    @app.get("/api/scripts/saved")
    def script_saved():
        return library.names()

    @app.get("/api/scripts/saved/{name}")
    def script_get(name: str):
        return {"name": name, "text": library.get(name)}

    @app.put("/api/scripts/saved/{name}")
    def script_put(name: str, sc: ScriptIn):
        library.save(name, sc.text)
        return {"ok": True}

    @app.delete("/api/scripts/saved/{name}")
    def script_delete(name: str):
        library.delete(name)
        return {"ok": True}

    # ------------------------------------------------------ settings, MCP
    @app.get("/api/settings")
    def get_settings():
        return {**store.get_settings(), "mcp.tools_all": ALL_TOOLS, "data_dir": str(store.data_dir)}

    @app.put("/api/settings/{key}")
    def put_setting(key: str, body: dict):
        if key not in ("mcp.enabled", "mcp.allow_transmit", "mcp.tools_disabled", "ring_buffer_size"):
            raise HTTPException(404, "unknown setting")
        v = body.get("value")
        if key in ("mcp.enabled", "mcp.allow_transmit"):
            if v not in ("true", "false", True, False):
                raise HTTPException(422, "value must be true or false")
            v = "true" if v in ("true", True) else "false"
        elif key == "ring_buffer_size":
            if not str(v).isdigit() or not 1000 <= int(v) <= 5_000_000:
                raise HTTPException(422, "ring_buffer_size must be 1000 to 5000000 (applies after restart)")
            v = str(int(v))
        else:  # mcp.tools_disabled: a list of known tool names
            if not isinstance(v, list) or not all(t in ALL_TOOLS for t in v):
                raise HTTPException(422, "tools_disabled must be a list of known tool names")
            v = json.dumps(v)
        store.set_setting(key, v)
        return {"ok": True}

    @app.get("/api/mcp/activity")
    def mcp_activity():
        return list(policy.activity)[::-1]

    @app.get("/api/workspaces")
    def list_ws():
        return store.list_workspaces()

    @app.put("/api/workspaces/{name}")
    def save_ws(name: str, layout: dict):
        store.save_workspace(name, layout)
        return {"ok": True}

    @app.get("/api/workspaces/{name}")
    def get_ws(name: str):
        w = store.get_workspace(name)
        if w is None:
            raise HTTPException(404, "no such workspace")
        return w

    # ----------------------------------------------------------- websocket
    @app.websocket("/ws")
    async def ws(sock: WebSocket):
        await sock.accept()
        q: deque = deque(maxlen=QUEUE_MAX)
        lost = 0

        def on_frame(f):
            nonlocal lost
            if len(q) == QUEUE_MAX:
                lost += 1
            q.append(f)

        unsub = bus.subscribe(on_frame)
        last_stats = 0.0
        try:
            while True:
                await asyncio.sleep(BATCH_S)
                if q:
                    batch = []
                    while q and len(batch) < 2000:
                        batch.append(q.popleft().to_dict())
                    await sock.send_json({"type": "frames", "frames": batch, "dropped": lost})
                now = time.time()
                if now - last_stats >= 1.0:
                    last_stats = now
                    await sock.send_json({"type": "tick", "state": bus.state(), "stats": bus.statistics()})
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            unsub()

    # The MCP app serves its own /mcp route; reuse that route inside this app (behind the same guard).
    app.router.routes.insert(0, next(r for r in mcp_app.routes if getattr(r, "path", "") == "/mcp"))

    if UI_DIST.exists():
        app.mount("/", StaticFiles(directory=UI_DIST, html=True), name="ui")
    return app
