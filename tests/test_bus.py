import time
import uuid
from pathlib import Path

import can
import pytest

from core.bus import Bus, BusError
from core.models import ChannelConfig, FilterRule

DBC = str(Path(__file__).resolve().parents[1] / "samples" / "demo.dbc")


def make_bus(listen_only=False, fd=False):
    b = Bus()
    chan = f"t{uuid.uuid4().hex[:6]}"
    b.set_channel(ChannelConfig(name="can1", interface="virtual", channel=chan,
                                listen_only=listen_only, fd_enabled=fd, fd_data_bitrate=2000000 if fd else None))
    return b, chan


def peer(chan):
    return can.Bus(interface="virtual", channel=chan)


def wait_for(cond, timeout=2.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.01)
    return False


def test_rx_frames_are_decoded():
    b, chan = make_bus()
    b.load_database("can1", DBC)
    b.start()
    p = peer(chan)
    try:
        # EngineSpeed raw 8000 -> 2000 rpm, CoolantTemp raw 130 -> 90 degC, Gear 2
        p.send(can.Message(arbitration_id=256, is_extended_id=False,
                           data=bytes([0x40, 0x1F, 130, 0, 2, 0, 0, 0])))
        assert wait_for(lambda: len(b.ring) == 1)
        f = b.ring[0]
        assert f.name == "EngineData" and f.direction == "rx"
        vals = {r["signal"]: r for r in b.signal_values()}
        assert vals["EngineSpeed"]["value"] == 2000.0
        assert vals["EngineSpeed"]["raw"] == 8000
        assert vals["CoolantTemp"]["value"] == 90
        assert vals["Gear"]["value"] == "Second"
    finally:
        p.shutdown()
        b.stop()


def test_unknown_id_has_no_name():
    b, chan = make_bus()
    b.start()
    p = peer(chan)
    try:
        p.send(can.Message(arbitration_id=0x7FF, is_extended_id=False, data=b"\x01"))
        assert wait_for(lambda: len(b.ring) == 1)
        assert b.ring[0].name is None
    finally:
        p.shutdown()
        b.stop()


def test_transmit_blocked_when_listen_only():
    b, _ = make_bus(listen_only=True)
    b.start()
    try:
        with pytest.raises(BusError, match="listen-only"):
            b.send("can1", 0x100, b"\x00")
    finally:
        b.stop()


def test_send_reaches_peer_and_is_recorded_as_tx():
    b, chan = make_bus()
    b.start()
    p = peer(chan)
    try:
        b.send("can1", 0x123, b"\x01\x02")
        m = p.recv(1.0)
        assert m is not None and m.arbitration_id == 0x123 and bytes(m.data) == b"\x01\x02"
        assert b.ring[-1].direction == "tx"
        assert b.stats["can1"].tx_frames == 1
    finally:
        p.shutdown()
        b.stop()


def test_validation():
    b, _ = make_bus()
    b.start()
    try:
        with pytest.raises(BusError):
            b.send("can1", 0x800, b"")                 # std id too large
        with pytest.raises(BusError):
            b.send("can1", 0x1, bytes(9))              # classic > 8
        with pytest.raises(BusError):
            b.send("can1", 0x1, b"\x00", fd=True)      # FD not enabled
        with pytest.raises(BusError):
            b.send("nope", 0x1, b"")
    finally:
        b.stop()


def test_fd_lengths():
    b, _ = make_bus(fd=True)
    b.start()
    try:
        b.send("can1", 0x1, bytes(12), fd=True)
        with pytest.raises(BusError):
            b.send("can1", 0x1, bytes(13), fd=True)
    finally:
        b.stop()


def test_filters_pass_and_stop():
    b, chan = make_bus()
    b.start()
    p = peer(chan)
    try:
        b.set_filter(FilterRule(id="a", mode="pass", id_from=0x100, id_to=0x1FF))
        for i in (0x100, 0x200, 0x150):
            p.send(can.Message(arbitration_id=i, is_extended_id=False, data=b"\x00"))
        assert wait_for(lambda: len(b.ring) == 3)
        assert [f["id"] for f in b.recent(10)] == [0x100, 0x150]
        b.set_filter(FilterRule(id="b", mode="stop", id_from=0x150, id_to=0x150))
        assert [f["id"] for f in b.recent(10)] == [0x100]
        b.delete_filter("a")
        b.delete_filter("b")
        assert len(b.recent(10)) == 3
    finally:
        p.shutdown()
        b.stop()


def test_cyclic_transmit_rate():
    b, chan = make_bus()
    b.start()
    try:
        b.start_cyclic("can1", 0x10, b"\x01", 20)
        time.sleep(0.5)
        n = b.stats["can1"].tx_frames
        assert 15 <= n <= 30, n
        for tid in [i["id"] for i in b.state()["cyclic"]]:
            b.stop_cyclic(tid)
        n2 = b.stats["can1"].tx_frames
        time.sleep(0.1)
        assert b.stats["can1"].tx_frames == n2
    finally:
        b.stop()


@pytest.mark.parametrize("fmt", ["asc", "blf"])
def test_log_and_offline_replay(tmp_path, fmt):
    b, chan = make_bus()
    b.start()
    path = str(tmp_path / f"out.{fmt}")
    b.start_logging(path, fmt)
    for i in range(5):
        b.send("can1", 0x100 + i, bytes([i]))
        time.sleep(0.01)
    info = b.stop_logging()
    assert info["frames"] == 5
    b.stop()

    # offline replay: no channels needed
    b2 = Bus()
    b2.start()
    try:
        b2.start_replay(path, channel="offline", speed=50.0)
        assert wait_for(lambda: len(b2.ring) == 5, timeout=3)
        ids = [f.can_id for f in b2.ring]
        assert ids == [0x100, 0x101, 0x102, 0x103, 0x104]
        assert all(f.direction == "rx" for f in b2.ring)
    finally:
        b2.stop()


def test_watch_and_history():
    b, chan = make_bus()
    b.load_database("can1", DBC)
    b.start()
    p = peer(chan)
    try:
        b.watch(["VehicleSpeed.Speed"])
        for raw in (1000, 2000, 3000):
            p.send(can.Message(arbitration_id=512, is_extended_id=False,
                               data=raw.to_bytes(2, "little") + b"\x00\x00"))
            time.sleep(0.01)
        assert wait_for(lambda: len(b.history("VehicleSpeed.Speed")) == 3)
        assert [v for _, v in b.history("VehicleSpeed.Speed")] == [10.0, 20.0, 30.0]
        assert b.read_signal("VehicleSpeed.Speed")["value"] == 30.0
        with pytest.raises(BusError):
            b.read_signal("Nope.Nope")
    finally:
        p.shutdown()
        b.stop()


def test_stats_load_and_rate():
    b, chan = make_bus()
    b.start()
    p = peer(chan)
    try:
        for _ in range(50):
            p.send(can.Message(arbitration_id=0x1, is_extended_id=False, data=bytes(8)))
        assert wait_for(lambda: len(b.ring) == 50)
        assert wait_for(lambda: b.stats["can1"].rx_rate > 0, timeout=2.5)
        assert 0 < b.stats["can1"].load_pct < 100
    finally:
        p.shutdown()
        b.stop()


def test_cannot_change_channels_while_running():
    b, _ = make_bus()
    b.start()
    try:
        with pytest.raises(BusError):
            b.set_channel(ChannelConfig(name="x"))
    finally:
        b.stop()


def test_fd_frame_received_and_decoded_with_full_length():
    b, chan = make_bus(fd=True)
    b.start()
    p = can.Bus(interface="virtual", channel=chan, fd=True)
    try:
        payload = bytes(range(64))
        p.send(can.Message(arbitration_id=0x1ABCDE, is_extended_id=True, is_fd=True,
                           bitrate_switch=True, data=payload))
        assert wait_for(lambda: len(b.ring) == 1)
        f = b.ring[0]
        assert f.fd and f.ext and f.can_id == 0x1ABCDE and f.data == payload and f.dlc == 64
        assert b.list_channels()[0]["fd_data_bitrate"] == 2000000
        # FD frames take less wire time than the same bytes at the arbitration rate
        assert b.stats["can1"].bits_window > 0
    finally:
        p.shutdown()
        b.stop()


def test_send_signals_encodes_with_the_database():
    b, chan = make_bus()
    b.load_database("can1", DBC)
    b.start()
    p = peer(chan)
    try:
        # physical values in, raw bytes out: speed 123.45 km/h -> raw 12345 (little endian)
        b.send_signals("can1", "VehicleSpeed", {"Speed": 123.45})
        m = p.recv(1.0)
        assert m.arbitration_id == 512 and bytes(m.data)[:2] == (12345).to_bytes(2, "little")
        # choice names work, and unmentioned signals go out at 0
        b.send_signals("can1", "EngineData", {"Gear": "Second", "EngineSpeed": 2000})
        m = p.recv(1.0)
        d = bytes(m.data)
        assert int.from_bytes(d[0:2], "little") == 8000 and d[4] == 2 and d[2] == 0 + 40  # CoolantTemp 0 -> raw 40
        # each frame is also recorded as Tx and decodes back to what we asked for
        vals = {r["signal"]: r["value"] for r in b.signal_values()}
        assert vals["Gear"] == "Second" and vals["EngineSpeed"] == 2000.0
    finally:
        p.shutdown()
        b.stop()


def test_send_signals_refusals():
    b, _ = make_bus()
    b.load_database("can1", DBC)
    b.start()
    try:
        with pytest.raises(BusError, match="no message named"):
            b.send_signals("can1", "Nope", {"x": 1})
        with pytest.raises(BusError, match="unknown signal"):
            b.send_signals("can1", "VehicleSpeed", {"Wrong": 1})
        with pytest.raises(BusError, match="cannot encode"):
            b.send_signals("can1", "VehicleSpeed", {"Speed": 9999})      # above the 300 km/h maximum
        with pytest.raises(BusError, match="cannot encode"):
            b.send_signals("can1", "EngineData", {"Gear": "Ninth"})      # not a named choice
    finally:
        b.stop()


def test_send_signals_blocked_when_listen_only():
    b, _ = make_bus(listen_only=True)
    b.load_database("can1", DBC)
    b.start()
    try:
        with pytest.raises(BusError, match="listen-only"):
            b.send_signals("can1", "VehicleSpeed", {"Speed": 1})
    finally:
        b.stop()


@pytest.mark.parametrize("fmt", ["asc", "blf"])
def test_logs_keep_extended_identifiers(tmp_path, fmt):
    """The MDF4 converter bug in the research was an extended id written without its flag."""
    b, chan = make_bus()
    b.start()
    path = str(tmp_path / f"ext.{fmt}")
    b.start_logging(path, fmt)
    b.send("can1", 0x14FF2321, b"\x01\x02", ext=True)
    b.send("can1", 0x123, b"\x03", ext=False)
    b.stop_logging()
    b.stop()
    msgs = list(can.LogReader(path))
    got = {(m.arbitration_id, bool(m.is_extended_id)) for m in msgs}
    assert got == {(0x14FF2321, True), (0x123, False)}
    assert all(m.arbitration_id <= 0x7FF for m in msgs if not m.is_extended_id)


def test_state_reports_demo_flag():
    b = Bus()
    assert b.state()["demo"] is False        # a real session is never labelled demo
    b.demo = True
    assert b.state()["demo"] is True


def test_peaks_catch_a_spike_between_reads(tmp_path):
    import time
    import uuid
    from pathlib import Path
    import can
    import cantools
    from core.bus import Bus
    from core.models import ChannelConfig
    dbc = str(Path(__file__).resolve().parents[1] / "samples" / "demo.dbc")
    msg = cantools.database.load_file(dbc).get_message_by_name("VehicleSpeed")
    b = Bus()
    chan = f"k{uuid.uuid4().hex[:6]}"
    b.set_channel(ChannelConfig(name="can1", interface="virtual", channel=chan))
    b.load_database("can1", dbc)
    b.start()
    p = can.Bus(interface="virtual", channel=chan)
    try:
        for kmh in (40.0, 120.0, 41.0, 39.0):                  # the 120 is gone again by the next read
            p.send(can.Message(arbitration_id=msg.frame_id, is_extended_id=msg.is_extended_frame, data=msg.encode({"Speed": kmh}, padding=False)))
        end = time.time() + 3
        while len(b.ring) < 4 and time.time() < end:
            time.sleep(0.02)
        row = next(r for r in b.signal_values() if r["signal"] == "Speed")
        assert row["value"] == 39.0 and row["min"] == 39.0 and row["max"] == 120.0
        b.reset_peaks()
        assert next(r for r in b.signal_values() if r["signal"] == "Speed")["max"] is None
    finally:
        p.shutdown()
        b.stop()


def test_graph_history_is_cleared_when_the_clock_restarts():
    """After Stop and Start the time axis begins at 0 again. Old points would zigzag through the new line."""
    b, chan = make_bus()
    b.load_database("can1", DBC)
    b.start()
    p = peer(chan)
    try:
        b.watch(["VehicleSpeed.Speed"])
        p.send(can.Message(arbitration_id=512, is_extended_id=False, data=(5000).to_bytes(2, "little") + b"\x00\x00"))
        assert wait_for(lambda: len(b.history("VehicleSpeed.Speed")) == 1)
        b.stop()
        b.start()
        assert b.history("VehicleSpeed.Speed") == []
        p.send(can.Message(arbitration_id=512, is_extended_id=False, data=(1000).to_bytes(2, "little") + b"\x00\x00"))
        assert wait_for(lambda: len(b.history("VehicleSpeed.Speed")) == 1)
        assert [v for _, v in b.history("VehicleSpeed.Speed")] == [10.0]       # only the new run
    finally:
        p.shutdown()
        b.stop()
