import time
import uuid

import can

from core.bus import Bus
from core.models import ChannelConfig


def test_ingest_keeps_up_with_a_burst():
    """A 1 Mbit classic CAN bus peaks near 9k frames/s. Ten thousand frames in a burst must all arrive."""
    b = Bus()
    chan = f"t{uuid.uuid4().hex[:6]}"
    b.set_channel(ChannelConfig(name="c", channel=chan))
    b.start()
    p = can.Bus(interface="virtual", channel=chan)
    n = 10_000
    try:
        t0 = time.time()
        for i in range(n):
            p.send(can.Message(arbitration_id=i % 0x7FF, is_extended_id=False, data=bytes(8)))
        end = time.time() + 10
        while len(b.ring) < n and time.time() < end:
            time.sleep(0.01)
        elapsed = time.time() - t0
        print(f"{n} frames in {elapsed:.2f}s = {n / elapsed:.0f} frames/s")
        assert len(b.ring) == n
    finally:
        p.shutdown()
        b.stop()
