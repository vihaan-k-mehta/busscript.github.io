import time
import uuid
from pathlib import Path

import can
import pytest
from fastapi.testclient import TestClient

from core.bus import Bus
from core.server import create_app
from core.store import Store

DBC = str(Path(__file__).resolve().parents[1] / "samples" / "demo.dbc")
TOKEN = "secret-token"
H = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture
def env(tmp_path):
    store = Store(tmp_path)
    bus = Bus()
    app = create_app(bus, store, TOKEN)
    with TestClient(app, base_url="http://127.0.0.1") as c:
        yield c, bus, store
    bus.stop()


def chan(c, listen_only=False):
    name = f"t{uuid.uuid4().hex[:6]}"
    r = c.put("/api/channels", headers=H, json={"name": "can1", "interface": "virtual", "channel": name,
                                                 "listen_only": listen_only})
    assert r.status_code == 200, r.text
    return name


def test_auth_required(env):
    c, *_ = env
    assert c.get("/api/state").status_code == 401
    assert c.get("/api/state", headers={"Authorization": "Bearer nope"}).status_code == 401
    assert c.get("/api/state", headers=H).status_code == 200


def test_bad_host_rejected(env):
    c, *_ = env
    r = c.get("/api/state", headers={**H, "Host": "evil.example.com"})
    assert r.status_code == 403


def test_end_to_end_decode_and_transmit(env):
    c, bus, store = env
    name = chan(c)
    assert c.post("/api/databases", headers=H, json={"channel": "can1", "path": DBC}).json()["messages"] == 2
    assert c.post("/api/measurement/start", headers=H).json()["running"] is True
    p = can.Bus(interface="virtual", channel=name)
    try:
        p.send(can.Message(arbitration_id=512, is_extended_id=False, data=(1234).to_bytes(2, "little") + b"\0\0"))
        end = time.time() + 2
        while time.time() < end and not c.get("/api/signal-values", headers=H).json():
            time.sleep(0.02)
        vals = c.get("/api/signal-values", headers=H).json()
        assert vals[0]["signal"] == "Speed" and vals[0]["value"] == 12.34
        r = c.post("/api/transmit/once", headers=H, json={"channel": "can1", "id": 0x55, "data": "01 02"})
        assert r.status_code == 200
        m = p.recv(1.0)
        assert m.arbitration_id == 0x55
        bad = c.post("/api/transmit/once", headers=H, json={"channel": "can1", "id": 0x55, "data": "zz"})
        assert bad.status_code == 400
    finally:
        p.shutdown()
        c.post("/api/measurement/stop", headers=H)


def test_channels_persist(env):
    c, bus, store = env
    chan(c)
    assert store.load_channels()[0][0].name == "can1"


def test_websocket_streams_frames(env):
    c, bus, store = env
    name = chan(c)
    c.post("/api/measurement/start", headers=H)
    p = can.Bus(interface="virtual", channel=name)
    try:
        with c.websocket_connect(f"/ws?token={TOKEN}") as ws:
            p.send(can.Message(arbitration_id=0x7, is_extended_id=False, data=b"\x09"))
            seen = None
            for _ in range(40):
                msg = ws.receive_json()
                if msg["type"] == "frames":
                    seen = msg["frames"][0]
                    break
            assert seen and seen["id"] == 7 and seen["data"] == "09" and seen["dir"] == "rx"
    finally:
        p.shutdown()
        c.post("/api/measurement/stop", headers=H)


def test_websocket_requires_token(env):
    c, *_ = env
    with pytest.raises(Exception):
        with c.websocket_connect("/ws?token=bad"):
            pass


def test_settings_and_unknown_setting(env):
    c, *_ = env
    s = c.get("/api/settings", headers=H).json()
    assert s["mcp.enabled"] == "false" and "send_frame" in s["mcp.tools_all"]
    assert c.put("/api/settings/not.real", headers=H, json={"value": "x"}).status_code == 404


def test_store_can_reopen_existing_database(tmp_path):
    Store(tmp_path).set_setting("mcp.enabled", "true")
    again = Store(tmp_path)          # second start against the same data dir must not fail
    assert again.get_settings()["mcp.enabled"] == "true"


def test_setting_values_are_validated(env):
    c, *_ = env
    assert c.put("/api/settings/mcp.enabled", headers=H, json={"value": "maybe"}).status_code == 422
    assert c.put("/api/settings/mcp.enabled", headers=H, json={}).status_code == 422
    assert c.put("/api/settings/mcp.tools_disabled", headers=H, json={"value": ["not_a_tool"]}).status_code == 422
    assert c.put("/api/settings/ring_buffer_size", headers=H, json={"value": "5"}).status_code == 422
    assert c.put("/api/settings/mcp.tools_disabled", headers=H, json={"value": ["send_frame"]}).status_code == 200
    assert c.put("/api/settings/mcp.enabled", headers=H, json={"value": True}).status_code == 200
    assert c.get("/api/settings", headers=H).json()["mcp.enabled"] == "true"


def test_bad_origin_rejected(env):
    c, *_ = env
    r = c.get("/api/state", headers={**H, "Origin": "https://evil.example.com"})
    assert r.status_code == 403
    assert c.get("/api/state", headers={**H, "Origin": "http://127.0.0.1:8765"}).status_code == 200


def test_transmit_by_signal_over_rest(env):
    c, bus, store = env
    name = chan(c)
    c.post("/api/databases", headers=H, json={"channel": "can1", "path": DBC})
    c.post("/api/measurement/start", headers=H)
    p = can.Bus(interface="virtual", channel=name)
    try:
        r = c.post("/api/transmit/signal", headers=H,
                   json={"channel": "can1", "message": "VehicleSpeed", "signals": {"Speed": 55.5}})
        assert r.status_code == 200, r.text
        m = p.recv(1.0)
        assert m.arbitration_id == 512 and int.from_bytes(bytes(m.data)[:2], "little") == 5550
        bad = c.post("/api/transmit/signal", headers=H,
                     json={"channel": "can1", "message": "VehicleSpeed", "signals": {"Speed": 5000}})
        assert bad.status_code == 400 and "cannot encode" in bad.json()["detail"]
    finally:
        p.shutdown()
        c.post("/api/measurement/stop", headers=H)


def test_schema_ships_inside_the_package():
    """The download leaves out replica/, so the app must not read anything from there."""
    from core import store
    assert store.SCHEMA.exists()
    assert store.SCHEMA.parent == Path(store.__file__).resolve().parent


def test_doctor_reports_libraries(env):
    c, *_ = env
    r = c.get("/api/doctor", headers=H).json()
    assert r["ready"] is True and r["data_dir_writable"] is True
    assert {x["name"] for x in r["libraries"]} >= {"python-can", "asammdf", "pyserial"}
    assert any(d["interface"] == "pcan" for d in r["drivers"])
    assert c.get("/api/doctor").status_code == 401


def test_describe_and_diagnostics(env):
    c, bus, _ = env
    r = c.get("/api/describe", headers=H, params={"id": 0x7E8, "data": "04 41 0C 1A F8"}).json()
    assert r["text"] == "Engine speed 1726 rpm"
    assert c.get("/api/describe", headers=H, params={"id": 1, "data": "zz"}).status_code == 400
    assert c.get("/api/diagnostics", headers=H).json() == {"source": "live", "messages": []}


def test_statistics_find_and_export_on_an_open_file(env, tmp_path):
    from pathlib import Path
    c, bus, store = env
    sample = Path(__file__).resolve().parents[1] / "samples" / "demo_drive.asc"
    c.put("/api/files/demo_drive.asc", headers={**H, "Content-Type": "application/octet-stream"}, content=sample.read_bytes())
    assert c.post("/api/files/open", headers=H, json={"name": "demo_drive.asc"}).status_code == 200

    rep = c.get("/api/statistics/report", headers=H).json()
    assert rep["source"] == "file" and rep["frames"] == 2130
    eng = next(r for r in rep["rows"] if r["id"] == 0x100)
    assert eng["count"] == 1500 and 15 < eng["mean_ms"] < 25 and eng["sd_ms"] is not None

    hit = c.get("/api/file/find", headers=H, params={"cond": "id == 0x3A0"}).json()
    assert hit["index"] is not None and hit["matches"] > 0
    assert c.get("/api/file/find", headers=H, params={"cond": "nonsense"}).status_code == 400

    r = c.post("/api/export", headers=H, json={"name": "cut", "format": "csv", "condition": "id == 0x100"}).json()
    assert r["frames"] == 1500
    body = c.get(f"/api/exports/{r['name']}", headers=H).text.splitlines()
    assert body[0].startswith("time_s,channel,id") and len(body) == 1501
    assert c.get("/api/exports/..%2F..%2Ftoken", headers=H).status_code == 404

    r = c.post("/api/export", headers=H, json={"name": "around", "format": "asc", "trigger": "id == 0x3A0", "pre": 0.1, "post": 0.1}).json()
    assert 0 < r["frames"] < 2130
    assert any(x["name"] == r["name"] for x in c.get("/api/exports", headers=H).json())

    for fmt in ("blf", "mf4"):
        r = c.post("/api/export", headers=H, json={"name": "all", "format": fmt}).json()
        assert r["frames"] == 2130 and r["size"] > 0

    r = c.post("/api/export", headers=H, json={"name": "sig", "format": "signals", "signals": ["EngineData.EngineSpeed"]})
    assert r.status_code == 400                      # no database attached yet: the plain-words message, not a crash
    assert c.post("/api/export", headers=H, json={"format": "csv", "condition": "id == 0x7FF"}).status_code == 400


def test_signal_table_export(env):
    from pathlib import Path
    c, bus, store = env
    root = Path(__file__).resolve().parents[1] / "samples"
    for n in ("demo_drive.asc", "demo.dbc"):
        c.put(f"/api/files/{n}", headers={**H, "Content-Type": "application/octet-stream"}, content=(root / n).read_bytes())
    c.post("/api/files/open", headers=H, json={"name": "demo.dbc"})
    c.post("/api/files/open", headers=H, json={"name": "demo_drive.asc"})
    r = c.post("/api/export", headers=H, json={"name": "sig", "format": "signals", "delimiter": ";",
                                                "signals": ["EngineData.EngineSpeed", "VehicleSpeed.Speed"]})
    assert r.status_code == 200, r.text
    lines = c.get(f"/api/exports/{r.json()['name']}", headers=H).text.splitlines()
    assert lines[0] == "time_s;EngineData.EngineSpeed;VehicleSpeed.Speed" and len(lines) > 100
    assert c.post("/api/export", headers=H, json={"format": "signals", "signals": ["EngineData.EngineSpeed"], "delimiter": "x"}).status_code == 400
