import time
import uuid
from pathlib import Path

import can
import pytest
from fastapi.testclient import TestClient

import core.files as files
from core.bus import Bus, BusError
from core.files import FileSession, kind_of, safe_name
from core.models import ChannelConfig
from core.server import create_app
from core.store import Store

DBC = Path(__file__).resolve().parents[1] / "samples" / "demo.dbc"
FORMATS = [".asc", ".blf", ".csv", ".log", ".trc", ".db", ".mf4"]


def write_log(path: Path, n=100):
    """100 frames: EngineData (0x100) every 20 ms with rising speed, VehicleSpeed (0x200) every 40 ms, one extended id."""
    t0 = 1_700_000_000.0
    with can.Logger(str(path)) as lg:
        for i in range(n):
            t = t0 + i * 0.02
            rpm = 1000 + i * 10                                    # raw = rpm * 4 and EngineSpeed = raw * 0.25, so 1000 .. 1990 rpm
            lg.on_message_received(can.Message(timestamp=t, arbitration_id=0x100, is_extended_id=False, channel=1,
                                               data=(rpm * 4).to_bytes(2, "little") + bytes([130, 0, 2, 0, 0, 0])))
            if i % 2 == 0:
                lg.on_message_received(can.Message(timestamp=t + 0.001, arbitration_id=0x200, is_extended_id=False,
                                                   channel=1, data=(i * 100).to_bytes(2, "little") + b"\x00\x00"))
        lg.on_message_received(can.Message(timestamp=t0 + n * 0.02, arbitration_id=0x14FF2321, is_extended_id=True,
                                           channel=1, data=b"\xAA\xBB"))


@pytest.fixture
def session(tmp_path):
    bus = Bus()
    bus.set_channel(ChannelConfig(name="can1", interface="virtual", channel=f"f{uuid.uuid4().hex[:6]}"))
    bus.load_database("can1", str(DBC))
    return FileSession(bus, tmp_path), tmp_path


# ------------------------------------------------------------ every format
@pytest.mark.parametrize("ext", FORMATS)
def test_each_format_opens_and_decodes(session, ext):
    fs, tmp = session
    p = tmp / f"drive{ext}"
    write_log(p)
    s = fs.open(p)
    assert s["frames"] == 151 and s["ids"] == 3 and not s["truncated"]
    assert 1.9 < s["duration"] < 2.1                          # absolute timestamps became "seconds from the start"
    rows = fs.frame_dicts(0, 5)
    assert rows[0]["ts"] == 0.0 and rows[0]["name"] == "EngineData" and rows[0]["id"] == 0x100
    ov = {r["id"]: r for r in fs.overview()}
    assert ov[0x100]["count"] == 100 and ov[0x200]["count"] == 50 and ov[0x14FF2321]["ext"] is True
    assert ov[0x14FF2321]["name"] is None                     # not in the database: shown as unknown, not hidden
    vals = {r["signal"]: r["value"] for r in fs.signal_values()}
    assert vals["Gear"] == "Second" and vals["EngineSpeed"] == pytest.approx(1990.0)


def test_series_returns_the_whole_recording_for_one_signal(session):
    fs, tmp = session
    write_log(tmp / "a.asc")
    fs.open(tmp / "a.asc")
    pts = fs.series("EngineData.EngineSpeed")
    assert len(pts) == 100
    assert pts[0][1] == pytest.approx(1000.0) and pts[-1][1] == pytest.approx(1990.0)
    assert pts[1][0] - pts[0][0] == pytest.approx(0.02, abs=1e-3)


def test_series_is_thinned_for_big_files(session):
    fs, tmp = session
    write_log(tmp / "a.asc", n=400)
    fs.open(tmp / "a.asc")
    assert len(fs.series("EngineData.EngineSpeed", max_points=50)) == 50


def test_series_errors_are_explained(session):
    fs, tmp = session
    write_log(tmp / "a.asc")
    fs.open(tmp / "a.asc")
    with pytest.raises(BusError, match="no signal called"):
        fs.series("EngineData.Nope")
    with pytest.raises(BusError, match="No frames for a message"):
        fs.series("Nothing.There")


def test_paging(session):
    fs, tmp = session
    write_log(tmp / "a.asc")
    fs.open(tmp / "a.asc")
    assert len(fs.frame_dicts(0, 10)) == 10
    assert len(fs.frame_dicts(145, 1000)) == 6                 # the end of the file, no error
    assert fs.frame_dicts(10_000, 10) == []
    assert len(fs.frame_dicts(0, 99999)) == 151                # the limit is capped at 1000 but there are only 151
    assert fs.frame_dicts(-5, 2)[0]["ts"] == 0.0


def test_names_follow_the_database_attached_later(tmp_path):
    bus = Bus()
    bus.set_channel(ChannelConfig(name="can1", interface="virtual", channel="late"))
    fs = FileSession(bus, tmp_path)
    write_log(tmp_path / "a.asc")
    fs.open(tmp_path / "a.asc")
    assert fs.frame_dicts(0, 1)[0]["name"] is None             # no database yet
    bus.load_database("can1", str(DBC))
    assert fs.frame_dicts(0, 1)[0]["name"] == "EngineData"     # named without reopening the file


def test_the_file_is_not_the_live_bus(session):
    fs, tmp = session
    write_log(tmp / "a.asc")
    fs.open(tmp / "a.asc")
    assert len(fs.bus.ring) == 0 and not fs.bus.running        # opening a file never touches the live pipeline


def test_truncation_is_reported(session, monkeypatch):
    fs, tmp = session
    write_log(tmp / "a.asc")
    monkeypatch.setattr(files, "MAX_FRAMES", 40)
    s = fs.open(tmp / "a.asc")
    assert s["frames"] == 40 and s["truncated"] is True


# ------------------------------------------------------------ bad input
def test_empty_and_broken_files_are_explained(session):
    fs, tmp = session
    (tmp / "empty.asc").write_text("")
    with pytest.raises(BusError, match="no CAN frames"):
        fs.open(tmp / "empty.asc")
    (tmp / "junk.blf").write_bytes(b"this is not a blf file at all" * 20)
    with pytest.raises(BusError, match="Could not read"):
        fs.open(tmp / "junk.blf")
    (tmp / "junk.mf4").write_bytes(b"not mdf" * 50)
    with pytest.raises(BusError, match="Could not read"):
        fs.open(tmp / "junk.mf4")
    assert fs.view is None                                      # a failed open leaves nothing half-open


def test_a_failed_open_keeps_the_previous_file(session):
    fs, tmp = session
    write_log(tmp / "good.asc")
    fs.open(tmp / "good.asc")
    (tmp / "bad.blf").write_bytes(b"x" * 100)
    with pytest.raises(BusError):
        fs.open(tmp / "bad.blf")
    assert fs.summary()["name"] == "good.asc"


def test_unsupported_types_and_unsafe_names():
    assert kind_of("x.mf4") == "log" and kind_of("X.DBC") == "database" and kind_of("x.exe") is None
    for bad in ["virus.exe", "notes.txt", "noextension", ".asc", ""]:
        with pytest.raises(BusError, match="cannot open"):
            safe_name(bad)
    assert safe_name("..\\..\\Windows\\drive 1.asc") == "drive 1.asc"
    assert safe_name("a/b/c.blf") == "c.blf"
    assert safe_name("we<ird>:name?.mf4") == "we_ird__name_.mf4"


def test_stored_path_cannot_escape_the_uploads_folder(session):
    fs, tmp = session
    (tmp / "secret.asc").write_text("x")
    for sneaky in ["../secret.asc", "..\\secret.asc", str(tmp / "secret.asc"), "missing.asc"]:
        with pytest.raises(BusError, match="not in the uploads folder"):
            fs.stored_path(sneaky)


def test_nothing_open_gives_a_clear_message(session):
    fs, _ = session
    for call in (lambda: fs.frame_dicts(0, 5), fs.overview, fs.signal_values, lambda: fs.series("A.B")):
        with pytest.raises(BusError, match="No file is open"):
            call()


# ------------------------------------------------------------ API
TOKEN = "tok"
H = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture
def api(tmp_path):
    store = Store(tmp_path)
    bus = Bus()
    with TestClient(create_app(bus, store, TOKEN), base_url="http://127.0.0.1") as c:
        yield c, bus, tmp_path
    bus.stop()


def upload(c, name, data):
    return c.put(f"/api/files/{name}", headers={**H, "Content-Type": "application/octet-stream"}, content=data)


def test_upload_open_browse_and_close(api):
    c, bus, tmp = api
    src = tmp / "src.mf4"
    write_log(src)
    r = upload(c, "My Drive.mf4", src.read_bytes())
    assert r.status_code == 200 and r.json() == {"name": "My Drive.mf4", "size": src.stat().st_size, "kind": "log"}
    assert c.post("/api/files/open", headers=H, json={"name": "My Drive.mf4"}).json()["frames"] == 151
    assert c.get("/api/file", headers=H).json()["format"] == "MDF4"
    page = c.get("/api/file/frames?offset=0&limit=20", headers=H).json()
    assert page["total"] == 151 and len(page["frames"]) == 20
    assert c.get("/api/file/frames?id=512&limit=1000", headers=H).json()["total"] == 50
    assert c.get("/api/file/seek?t=1.0", headers=H).json()["index"] > 60
    assert len(c.get("/api/file/overview", headers=H).json()) == 3
    assert c.get("/api/files", headers=H).json()["stored"][0]["name"] == "My Drive.mf4"
    assert c.delete("/api/file", headers=H).json() == {"ok": True}
    assert c.get("/api/file", headers=H).json() is None


def test_a_database_with_no_channel_gets_a_quiet_one_and_names_appear(api):
    c, bus, tmp = api
    assert bus.channels == {}
    assert upload(c, "demo.dbc", DBC.read_bytes()).json()["kind"] == "database"
    r = c.post("/api/files/open", headers=H, json={"name": "demo.dbc"}).json()
    assert r["kind"] == "database" and r["created_channel"] is True and r["messages"] == 2
    ch = c.get("/api/channels", headers=H).json()[0]
    assert ch["name"] == "file" and ch["listen_only"] is True
    src = tmp / "src.asc"
    write_log(src)
    upload(c, "run.asc", src.read_bytes())
    c.post("/api/files/open", headers=H, json={"name": "run.asc"})
    assert c.get("/api/file/frames?limit=1", headers=H).json()["frames"][0]["name"] == "EngineData"
    assert c.get("/api/file/series?name=VehicleSpeed.Speed", headers=H).status_code == 200


def test_upload_refusals(api, monkeypatch):
    c, _, _ = api
    assert upload(c, "evil.exe", b"MZ").status_code == 400
    assert c.put("/api/files/x.asc", content=b"x").status_code == 401           # no token
    monkeypatch.setattr("core.server.MAX_UPLOAD", 100)
    big = upload(c, "big.asc", b"x" * 500)
    assert big.status_code == 400 and "limit" in big.json()["detail"]
    assert c.get("/api/files", headers=H).json()["stored"] == []                # the partial file was removed


def test_same_name_twice_does_not_overwrite(api):
    c, _, tmp = api
    write_log(tmp / "s.asc")
    data = (tmp / "s.asc").read_bytes()
    assert upload(c, "run.asc", data).json()["name"] == "run.asc"
    assert upload(c, "run.asc", data).json()["name"] == "run (2).asc"


def test_opening_a_bad_file_over_the_api_is_a_readable_400(api):
    c, _, _ = api
    upload(c, "broken.blf", b"garbage" * 30)
    r = c.post("/api/files/open", headers=H, json={"name": "broken.blf"})
    assert r.status_code == 400 and "Could not read" in r.json()["detail"]
    assert c.post("/api/files/open", headers=H, json={"name": "../x.asc"}).status_code == 400


def test_starting_a_measurement_closes_the_open_file(api):
    c, bus, tmp = api
    bus.set_channel(ChannelConfig(name="can1", interface="virtual", channel="fx"))
    write_log(tmp / "s.asc")
    upload(c, "run.asc", (tmp / "s.asc").read_bytes())
    c.post("/api/files/open", headers=H, json={"name": "run.asc"})
    assert c.get("/api/file", headers=H).json() is not None
    c.post("/api/measurement/start", headers=H)
    assert c.get("/api/file", headers=H).json() is None
    c.post("/api/measurement/stop", headers=H)


def test_delete_stored_file(api):
    c, _, tmp = api
    write_log(tmp / "s.asc")
    upload(c, "run.asc", (tmp / "s.asc").read_bytes())
    assert c.delete("/api/files/stored/run.asc", headers=H).status_code == 200
    assert c.get("/api/files", headers=H).json()["stored"] == []
    assert c.delete("/api/files/stored/..%2F..%2Fx.asc", headers=H).status_code != 200


def test_filter_by_message_and_seek(session):
    fs, tmp = session
    write_log(tmp / "a.asc")
    fs.open(tmp / "a.asc")
    assert fs.count() == 151 and fs.count(0x200) == 50 and fs.count(0x14FF2321, True) == 1 and fs.count(0x999) == 0
    only = fs.frame_dicts(0, 1000, can_id=0x200)
    assert len(only) == 50 and {r["id"] for r in only} == {0x200} and only[0]["name"] == "VehicleSpeed"
    assert fs.frame_dicts(0, 10, can_id=0x14FF2321, ext=True)[0]["data"] == "aabb"
    assert fs.frame_dicts(0, 10, can_id=0x14FF2321, ext=False) == []        # same number, standard id: a different message
    assert fs.seek(0.0) == 0 and fs.seek(99.0) == 151
    i = fs.seek(1.0)
    assert 1.0 <= fs.frame_dicts(i, 1)[0]["ts"] < 1.05 and fs.frame_dicts(i - 1, 1)[0]["ts"] < 1.0
    j = fs.seek(1.0, can_id=0x200)
    assert fs.frame_dicts(j, 1, can_id=0x200)[0]["ts"] >= 1.0


def test_diagnostic_sample_is_understood(tmp_path):
    from pathlib import Path
    from core.protocols import assemble
    from core.bus import Bus
    from core.files import FileSession
    fs = FileSession(Bus(), tmp_path)
    src = Path(__file__).resolve().parents[1] / "samples" / "demo_diag.asc"
    assert fs.open(src, "demo_diag.asc")["frames"] == 14
    texts = [m["text"] for m in assemble(fs.view.frames)]
    assert "Engine speed 1726 rpm" in texts
    assert any(t.startswith("Fault codes") for t in texts)
    assert any(t.startswith("No: Security access") for t in texts)
