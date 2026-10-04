import pytest

from core.analysis import cut, find_matches, parse_condition, report, trigger_windows
from core.bus import BusError
from core.models import Frame


def fr(ts, cid=0x100, data=b"\0", direction="rx", error=False):
    return Frame(ts=ts, channel="can1", can_id=cid, ext=False, fd=False, direction=direction, dlc=len(data), data=data, error=error)


def test_report_mean_and_spread():
    rows = report([fr(0.0), fr(0.1), fr(0.2), fr(0.4)])
    r = rows[0]
    assert r["count"] == 4 and r["mean_ms"] == pytest.approx(133.333, abs=0.01)
    assert (r["min_ms"], r["max_ms"]) == (100.0, 200.0)
    assert r["sd_ms"] == pytest.approx(47.14, abs=0.01)


def test_report_splits_rx_and_tx_and_ignores_error_frames():
    rows = report([fr(0, direction="rx"), fr(1, direction="tx"), fr(2, error=True)])
    assert {(r["dir"], r["count"]) for r in rows} == {("rx", 1), ("tx", 1)}
    assert rows[0]["mean_ms"] is None


def test_conditions():
    f = fr(1.5, cid=0x123, data=bytes([5, 9]))
    assert parse_condition("id == 0x123")(f)
    assert parse_condition("id > 0x100 and d0 == 5")(f)
    assert not parse_condition("id > 0x200 and d0 == 5")(f)
    assert parse_condition("id > 0x200 or d1 == 9")(f)
    assert parse_condition("time >= 1.5 and dir == rx")(f)
    assert not parse_condition("d5 == 0")(f)                  # a byte that is not there never matches
    assert parse_condition("error == 1")(fr(0, error=True))


@pytest.mark.parametrize("bad", ["", "id", "id ?? 3", "id == zebra", "id == 1 and id == 2 or id == 3", "d64 == 1", "dir == up"])
def test_bad_conditions_say_what_to_write(bad):
    with pytest.raises(BusError) as e:
        parse_condition(bad)
    assert "condition" in str(e.value).lower() or "number" in str(e.value) or "dir" in str(e.value) or "Data bytes" in str(e.value)


def test_find_counts_and_starts_after():
    frames = [fr(i, cid=0x10 + (i % 2)) for i in range(6)]
    cond = parse_condition("id == 0x11")
    assert find_matches(frames, cond, 0) == (1, 3)
    assert find_matches(frames, cond, 2) == (3, 3)
    assert find_matches(frames, cond, 6) == (None, 3)


def test_single_trigger_window_merges_and_cuts():
    frames = [fr(t / 10) for t in range(0, 40)]
    frames[10] = fr(1.0, cid=0x7FF)
    frames[12] = fr(1.2, cid=0x7FF)
    wins = trigger_windows(frames, parse_condition("id == 0x7FF"), pre=0.05, post=0.2)
    assert len(wins) == 1 and wins[0][0] == pytest.approx(0.95) and wins[0][1] == pytest.approx(1.4)
    kept = {round(f.ts, 1) for f in cut(frames, wins)}
    assert {1.0, 1.1, 1.2, 1.3} <= kept and 0.9 not in kept and 1.5 not in kept
    two = trigger_windows(frames, parse_condition("id == 0x7FF"), pre=0.05, post=0.1)
    assert len(two) == 2                          # far enough apart to stay separate blocks


def test_toggle_trigger_window():
    frames = [fr(t / 10, cid=0x1) for t in range(30)]
    frames[5] = fr(0.5, cid=0xA)
    frames[12] = fr(1.2, cid=0xB)
    wins = trigger_windows(frames, parse_condition("id == 0xA"), 0.1, 0.1, parse_condition("id == 0xB"))
    assert wins == [(pytest.approx(0.4), pytest.approx(1.3))]
