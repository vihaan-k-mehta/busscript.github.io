import threading
import time
import uuid
from pathlib import Path

import can
import pytest
from fastapi.testclient import TestClient

from core.bus import Bus, BusError
from core.models import ChannelConfig
from core.script import TEMPLATES, ScriptError, ScriptLibrary, ScriptRunner, parse
from core.server import create_app
from core.store import Store

DBC = str(Path(__file__).resolve().parents[1] / "samples" / "demo.dbc")


def make(tmp_path, listen_only=False):
    b = Bus()
    chan = f"s{uuid.uuid4().hex[:6]}"
    b.set_channel(ChannelConfig(name="can1", interface="virtual", channel=chan, listen_only=listen_only))
    b.load_database("can1", DBC)
    return b, chan, ScriptRunner(b, tmp_path)


def wait_done(r, timeout=8.0):
    end = time.time() + timeout
    while r.running and time.time() < end:
        time.sleep(0.02)
    assert not r.running, "script did not finish"
    return r.state()


def texts(state):
    return [o["text"] for o in state["output"]]


# ------------------------------------------------------------------ parsing
@pytest.mark.parametrize("text,fragment", [
    ("sned VehicleSpeed Speed=5", "Did you mean 'send'"),
    ("wait 5", "Write it like 500ms, 2s or 1m"),
    ("wait", "needs a time"),
    ("repeat 3\nsend A B=1", "never closed"),
    ("end", "nothing to end"),
    ("send Msg Speed 50", "Speed=50"),
    ("send", "needs a message name"),
    ("wait until Engine > 3", "Message.Signal"),
    ("wait until A.B ~ 3", "not a comparison"),
    ("log start ../x.asc", "letters, digits"),
    ("log start x.txt", ".asc or .blf"),
    ("every 1ms for 5s\nend", "faster than"),
    ("repeat 0\nend", "from 1 to"),
    ("sendraw zz 01", "not an identifier"),
    ("sendraw 0x1 0g", "hex pairs"),
    ("start now", "does not take anything"),
])
def test_mistakes_get_a_plain_message_with_the_line(text, fragment):
    with pytest.raises(ScriptError) as e:
        parse(text)
    assert e.value.line >= 1 and fragment in str(e.value)
    assert str(e.value).startswith("Line ")


def test_error_line_points_at_the_right_line():
    with pytest.raises(ScriptError) as e:
        parse("# fine\nstart\n\nsned x\n")
    assert e.value.line == 4


def test_every_template_parses():
    assert len(TEMPLATES) >= 4
    for t in TEMPLATES:
        assert parse(t["text"]), t["id"]


def test_blocks_nest_but_not_forever():
    parse("repeat 2\n repeat 2\n  send A B=1\n end\nend")
    deep = "".join("repeat 2\n" for _ in range(6)) + "".join("end\n" for _ in range(6))
    with pytest.raises(ScriptError, match="nested"):
        parse(deep)


def test_too_long_script_refused():
    with pytest.raises(ScriptError, match="too long"):
        parse("# x\n" * 6000)


# ------------------------------------------------------------------ running
def test_send_by_name_and_print(tmp_path):
    b, chan, r = make(tmp_path)
    b.start()
    p = can.Bus(interface="virtual", channel=chan)
    try:
        r.start("send VehicleSpeed Speed=50\nprint all done")
        s = wait_done(r)
        assert s["ok"] and s["error"] is None
        m = p.recv(1.0)
        assert m.arbitration_id == 512 and int.from_bytes(bytes(m.data)[:2], "little") == 5000
        assert any("Sent VehicleSpeed (Speed=50)" in t for t in texts(s)) and "all done" in texts(s)
    finally:
        p.shutdown()
        b.stop()


def test_start_command_starts_the_measurement(tmp_path):
    b, chan, r = make(tmp_path)
    try:
        r.start("start\nsend VehicleSpeed Speed=1")
        assert wait_done(r)["ok"]
        assert b.running
    finally:
        b.stop()


def test_send_without_measurement_explains_what_to_do(tmp_path):
    b, _, r = make(tmp_path)
    r.start("send VehicleSpeed Speed=1")
    s = wait_done(r)
    assert not s["ok"] and s["error_line"] == 1
    assert "Press Start first" in s["error"]


def test_listen_only_channel_blocks_send_in_a_script(tmp_path):
    b, _, r = make(tmp_path, listen_only=True)
    b.start()
    try:
        r.start("send VehicleSpeed Speed=1")
        s = wait_done(r)
        assert "listen-only" in s["error"] and s["error_line"] == 1
    finally:
        b.stop()


def test_unknown_message_and_signal_and_range_are_explained(tmp_path):
    b, _, r = make(tmp_path)
    b.start()
    try:
        for script, frag in [("send Nope X=1", "no message named"), ("send VehicleSpeed Wrong=1", "unknown signal"),
                             ("send VehicleSpeed Speed=9999", "cannot encode")]:
            r.start(script)
            s = wait_done(r)
            assert frag in s["error"], (script, s["error"])
    finally:
        b.stop()


def test_repeat_runs_the_body_that_many_times(tmp_path):
    b, chan, r = make(tmp_path)
    b.start()
    p = can.Bus(interface="virtual", channel=chan)
    try:
        r.start("repeat 4\n  send VehicleSpeed Speed=10\n  wait 20ms\nend")
        assert wait_done(r)["ok"]
        got = []
        while (m := p.recv(0.2)) is not None:
            got.append(m)
        assert len(got) == 4
    finally:
        p.shutdown()
        b.stop()


def test_every_paces_itself_and_does_not_burst(tmp_path):
    b, chan, r = make(tmp_path)
    b.start()
    p = can.Bus(interface="virtual", channel=chan)
    try:
        t0 = time.time()
        r.start("every 50ms for 0.5s\n  send VehicleSpeed Speed=10\nend")
        assert wait_done(r)["ok"]
        n = 0
        while p.recv(0.1) is not None:
            n += 1
        assert 8 <= n <= 12, n
        assert time.time() - t0 >= 0.45
    finally:
        p.shutdown()
        b.stop()


def test_sendraw(tmp_path):
    b, chan, r = make(tmp_path)
    b.start()
    p = can.Bus(interface="virtual", channel=chan)
    try:
        r.start("sendraw 0x123 01 02 03")
        assert wait_done(r)["ok"]
        m = p.recv(1.0)
        assert m.arbitration_id == 0x123 and bytes(m.data) == b"\x01\x02\x03"
    finally:
        p.shutdown()
        b.stop()


def test_wait_until_and_print_interpolation(tmp_path):
    b, chan, r = make(tmp_path)
    b.start()
    p = can.Bus(interface="virtual", channel=chan)
    stop = threading.Event()

    def feed():  # engine speed climbs 1000 rpm per tick
        rpm = 1000
        while not stop.is_set():
            p.send(can.Message(arbitration_id=256, is_extended_id=False,
                               data=int(rpm * 4).to_bytes(2, "little") + b"\x00" * 6))
            rpm += 500
            time.sleep(0.05)

    t = threading.Thread(target=feed, daemon=True)
    t.start()
    try:
        r.start("wait until EngineData.EngineSpeed > 2500 timeout 5s\nprint reached {EngineData.EngineSpeed}")
        s = wait_done(r)
        assert s["ok"], s["error"]
        line = [x for x in texts(s) if x.startswith("reached")][0]
        assert float(line.split()[1]) > 2500
    finally:
        stop.set()
        t.join()
        p.shutdown()
        b.stop()


def test_wait_until_times_out_with_a_helpful_message(tmp_path):
    b, _, r = make(tmp_path)
    b.start()
    try:
        r.start("wait until EngineData.EngineSpeed > 99999 timeout 300ms")
        s = wait_done(r)
        assert not s["ok"] and "never became" in s["error"] and s["error_line"] == 1
    finally:
        b.stop()


def test_print_shows_question_mark_for_unseen_signal(tmp_path):
    b, _, r = make(tmp_path)
    r.start("print value is {EngineData.EngineSpeed}")
    assert "value is ?" in texts(wait_done(r))


def test_stop_ends_a_long_wait_quickly(tmp_path):
    b, _, r = make(tmp_path)
    r.start("wait 60s")
    time.sleep(0.2)
    assert r.state()["running"]
    t0 = time.time()
    r.stop()
    assert time.time() - t0 < 2.0
    s = r.state()
    assert not s["running"] and s["stopped"] and not s["ok"]


def test_only_one_script_at_a_time(tmp_path):
    b, _, r = make(tmp_path)
    r.start("wait 60s")
    try:
        with pytest.raises(BusError, match="already running"):
            r.start("print hi")
    finally:
        r.stop()


def test_mistakes_are_reported_before_anything_runs(tmp_path):
    b, chan, r = make(tmp_path)
    b.start()
    p = can.Bus(interface="virtual", channel=chan)
    try:
        with pytest.raises(ScriptError):
            r.start("send VehicleSpeed Speed=50\nsned oops")      # line 2 is wrong: line 1 must NOT run
        assert p.recv(0.3) is None
        assert not r.running
    finally:
        p.shutdown()
        b.stop()


def test_log_start_and_stop_write_a_file_in_the_logs_folder(tmp_path):
    b, chan, r = make(tmp_path)
    b.start()
    try:
        r.start("log start run1.asc\nsend VehicleSpeed Speed=5\nwait 100ms\nlog stop")
        s = wait_done(r)
        assert s["ok"], s["error"]
        f = tmp_path / "logs" / "run1.asc"
        assert f.is_file() and f.stat().st_size > 50
        assert any("Saved" in t for t in texts(s))
    finally:
        b.stop()


def test_a_log_left_open_is_closed_when_the_script_is_stopped(tmp_path):
    b, _, r = make(tmp_path)
    b.start()
    try:
        r.start("log start left_open.asc\nwait 60s")
        time.sleep(0.3)
        assert b.state()["logging"]
        r.stop()
        assert not b.state()["logging"]
    finally:
        b.stop()


def test_unknown_channel_is_explained(tmp_path):
    b, _, r = make(tmp_path)
    b.start()
    try:
        r.start("send VehicleSpeed Speed=1 on nowhere")
        assert "no channel called 'nowhere'" in wait_done(r)["error"]
    finally:
        b.stop()


# ------------------------------------------------------------------ saved scripts
def test_library_round_trip_and_name_rules(tmp_path):
    lib = ScriptLibrary(tmp_path)
    lib.save("My test", "print hi\n")
    assert lib.names() == ["My test"] and lib.get("My test") == "print hi\n"
    lib.delete("My test")
    assert lib.names() == []
    for bad in ["../evil", "a/b", "", "x" * 61, "con:ads"]:
        with pytest.raises(BusError):
            lib.save(bad, "x")
    with pytest.raises(BusError):
        lib.get("missing")


# ------------------------------------------------------------------ API
TOKEN = "t0ken"
H = {"Authorization": f"Bearer {TOKEN}"}


def store_dir(client):
    return client.app.state.scripts_dir


@pytest.fixture
def api(tmp_path):
    store = Store(tmp_path)
    bus = Bus()
    with TestClient(create_app(bus, store, TOKEN), base_url="http://127.0.0.1") as c:
        yield c, bus
    bus.stop()


def test_api_templates_check_and_run(api):
    c, bus = api
    assert len(c.get("/api/scripts/templates", headers=H).json()) >= 4
    ok = c.post("/api/scripts/check", headers=H, json={"text": "print hi"}).json()
    assert ok == {"ok": True, "steps": 1}
    bad = c.post("/api/scripts/check", headers=H, json={"text": "print hi\nsned x"}).json()
    assert bad["ok"] is False and bad["line"] == 2 and "Did you mean 'send'" in bad["message"]
    assert c.post("/api/scripts/run", headers=H, json={"text": "sned x"}).status_code == 400
    c.post("/api/scripts/run", headers=H, json={"text": "print hello from a script"})
    end = time.time() + 5
    s = {}
    while time.time() < end:
        s = c.get("/api/scripts/status", headers=H).json()
        if s["finished"]:
            break
        time.sleep(0.05)
    assert s["ok"] and "hello from a script" in [o["text"] for o in s["output"]]


def test_api_saved_scripts_and_auth(api):
    c, _ = api
    assert c.get("/api/scripts/saved").status_code == 401
    assert c.put("/api/scripts/saved/Mine", headers=H, json={"text": "print a"}).status_code == 200
    assert c.get("/api/scripts/saved", headers=H).json() == ["Mine"]
    assert c.get("/api/scripts/saved/Mine", headers=H).json()["text"] == "print a"
    r = c.put("/api/scripts/saved/..%2Fevil", headers=H, json={"text": "x"})
    assert r.status_code != 200                                  # refused, whichever way the router rejects it
    assert not list(Path(store_dir(c)).parent.glob("evil*"))     # and nothing was written outside the scripts folder
    assert c.delete("/api/scripts/saved/Mine", headers=H).status_code == 200
    assert c.get("/api/scripts/saved", headers=H).json() == []


def test_the_same_line_again_and_again_is_one_line_with_a_count(tmp_path):
    b, chan, r = make(tmp_path)
    b.start()
    try:
        r.start("repeat 6\n  send VehicleSpeed Speed=10\nend\nprint after")
        s = wait_done(r)
        lines = [(o["text"], o["count"]) for o in s["output"]]
        assert ("Sent VehicleSpeed (Speed=10)", 6) in lines
        assert lines.count(("Sent VehicleSpeed (Speed=10)", 6)) == 1 and ("after", 1) in lines
        assert all(o["t_last"] >= o["t"] for o in s["output"])
    finally:
        b.stop()
