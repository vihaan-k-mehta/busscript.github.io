"""Synthetic traffic on a virtual bus so the app works with no hardware."""
from __future__ import annotations

import math
import random
import threading
import time

import can


def start_demo_traffic(channel: str) -> threading.Event:
    stop = threading.Event()

    def run():
        peer = can.Bus(interface="virtual", channel=channel)
        t0 = time.time()
        n = 0
        try:
            while not stop.wait(0.01):
                t = time.time() - t0
                n += 1
                if n % 2 == 0:   # EngineData every 20 ms
                    rpm = int((2000 + 1500 * math.sin(t / 3) + random.uniform(-30, 30)) * 4)
                    temp = int(90 + 8 * math.sin(t / 20) + 40)
                    thr = int((40 + 35 * math.sin(t / 3 + 0.5)) / 0.4)
                    gear = 1 + int(t / 6) % 3
                    data = rpm.to_bytes(2, "little") + bytes([temp & 0xFF, thr & 0xFF, gear, 0, 0, 0])
                    peer.send(can.Message(arbitration_id=256, is_extended_id=False, data=data))
                if n % 5 == 0:   # VehicleSpeed every 50 ms
                    spd = int((60 + 50 * math.sin(t / 4)) * 100)
                    peer.send(can.Message(arbitration_id=512, is_extended_id=False,
                                          data=spd.to_bytes(2, "little") + b"\x00\x00"))
                if n % 50 == 0:  # an id that is not in the database
                    peer.send(can.Message(arbitration_id=0x3A0, is_extended_id=False,
                                          data=bytes(random.randrange(256) for _ in range(8))))
        finally:
            peer.shutdown()

    threading.Thread(target=run, daemon=True, name="demo-traffic").start()
    return stop
