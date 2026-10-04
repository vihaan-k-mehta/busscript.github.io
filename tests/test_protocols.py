from types import SimpleNamespace as F

from core.protocols import assemble, describe, dtc_text, j1939_parts


def test_dtc_letters():
    assert dtc_text(bytes([0x01, 0x33])) == "P0133"
    assert dtc_text(bytes([0xC1, 0x00])) == "U0100"
    assert dtc_text(bytes([0x41, 0x23, 0x1A])) == "C0123-1A"


def test_j1939_engine_speed_message():
    p = j1939_parts(0x0CF00400)          # EEC1 from address 0, priority 3
    assert (p["pgn"], p["source"], p["priority"], p["destination"]) == (61444, 0, 3, None)
    assert "Engine controller 1" in describe(0x0CF00400, True, b"\0" * 8)


def test_j1939_destination_specific():
    p = j1939_parts(0x18EAFF00)          # Request to everyone
    assert p["pgn"] == 59904 and p["destination"] == 255


def test_obd_engine_speed_and_coolant():
    assert describe(0x7E8, False, bytes([4, 0x41, 0x0C, 0x1A, 0xF8])) == "Engine speed 1726 rpm"
    assert describe(0x7E8, False, bytes([3, 0x41, 0x05, 0x7B])) == "Coolant temperature 83 °C"


def test_uds_negative_response_and_request():
    assert describe(0x7E8, False, bytes([3, 0x7F, 0x27, 0x33])) == "No: Security access refused. Security access denied"
    assert describe(0x7DF, False, bytes([2, 0x3E, 0])).startswith("Asks: Tester present")


def test_stored_codes_single_frame():
    # mode 3 answer: two codes P0133 and U0100
    assert describe(0x7E8, False, bytes([6, 0x43, 2, 0x01, 0x33, 0xC1, 0x00])).endswith("P0133, U0100")


def test_multiframe_uds_fault_code_report():
    payload = bytes([0x59, 0x02, 0xFF, 0x01, 0x33, 0x1A, 0x09, 0xC1, 0x00, 0x00, 0x2F])  # 11 bytes, 2 faults
    frames = [F(ts=0.0, channel="c", can_id=0x7E8, ext=False, data=bytes([0x10, 11]) + payload[:6]),
              F(ts=0.01, channel="c", can_id=0x7E8, ext=False, data=bytes([0x21]) + payload[6:] + b"\0\0")]
    out = assemble(frames)
    assert len(out) == 1 and out[0]["length"] == 11
    assert out[0]["details"] == ["P0133-1A: test failed, confirmed", "U0100-00: test failed, failed this cycle, pending, confirmed, failed since clear"]


def test_unrelated_frames_are_ignored():
    assert describe(0x123, False, b"\1\2\3") is None


def test_more_j1939_names_and_manufacturer_ranges():
    from core import protocols
    # taken from a real truck recording: these were unnamed before
    assert "Transmission controller 1" in protocols.describe_j1939(0x0CF00203)
    assert "Engine controller 3" in protocols.describe_j1939(0x18FEDF00)
    assert "Manufacturer-specific (proprietary B)" in protocols.describe_j1939(0x18FF1234)
    assert protocols.describe(0x18FF1234, True, b"\0") is not None
    assert protocols.pgn_name(0x1234) is None
