"""Plain-language meaning for the standard protocols that ride on CAN: J1939 (trucks, machines), OBD-II and UDS
diagnostics over ISO-TP (cars). Pure functions: bytes in, text out. Nothing here talks to a bus."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Optional

# ------------------------------------------------------------------ J1939
PGN_NAMES = {
    59904: "Request", 60928: "Address claimed", 60416: "Transport protocol: connection", 60160: "Transport protocol: data",
    61443: "Engine controller 2 (EEC2)", 61444: "Engine controller 1 (EEC1)", 65226: "Active fault codes (DM1)",
    65227: "Previously active fault codes (DM2)", 65248: "Vehicle distance", 65253: "Engine hours", 65259: "Component identification",
    65260: "Vehicle identification", 65262: "Engine temperature 1", 65263: "Engine fluid level and pressure",
    65265: "Cruise control and vehicle speed", 65266: "Fuel economy", 65269: "Ambient conditions", 65270: "Inlet and exhaust conditions",
    65271: "Vehicle electrical power",
}


def j1939_parts(can_id: int) -> dict:
    pf = (can_id >> 16) & 0xFF
    pgn = (can_id >> 8) & 0x3FFFF
    dest = None
    if pf < 240:                      # PDU1: the low byte is a destination address, not part of the PGN
        dest = pgn & 0xFF
        pgn &= 0x3FF00
    return {"priority": (can_id >> 26) & 7, "pgn": pgn, "source": can_id & 0xFF, "destination": dest}


def describe_j1939(can_id: int) -> str:
    p = j1939_parts(can_id)
    name = PGN_NAMES.get(p["pgn"])
    text = f"J1939 PGN {p['pgn']}" + (f" ({name})" if name else "")
    text += f", from address {p['source']}"
    if p["destination"] is not None:
        text += f" to {'everyone' if p['destination'] == 255 else p['destination']}"
    return text + f", priority {p['priority']}"


# ------------------------------------------------------------------ UDS and OBD-II
UDS_SERVICES = {
    0x10: "Change session", 0x11: "ECU reset", 0x14: "Clear fault codes", 0x19: "Read fault codes", 0x22: "Read data",
    0x23: "Read memory", 0x24: "Read scaling data", 0x27: "Security access", 0x28: "Communication control",
    0x2A: "Read data periodically", 0x2C: "Define data", 0x2E: "Write data", 0x2F: "Control I/O", 0x31: "Run routine",
    0x34: "Request download", 0x35: "Request upload", 0x36: "Transfer data", 0x37: "End transfer", 0x3D: "Write memory",
    0x3E: "Tester present", 0x85: "Control fault logging", 0x86: "Event response",
}
NRC = {
    0x10: "General reject", 0x11: "Service not supported", 0x12: "Sub-function not supported", 0x13: "Wrong message length",
    0x14: "Response too long", 0x21: "Busy, repeat later", 0x22: "Conditions not correct", 0x24: "Request sequence error",
    0x25: "No response from sub-component", 0x31: "Request out of range", 0x33: "Security access denied",
    0x35: "Invalid key", 0x36: "Too many attempts", 0x37: "Wait before trying again", 0x70: "Upload or download not accepted",
    0x71: "Transfer suspended", 0x72: "Programming failure", 0x78: "Still working, response pending",
    0x7E: "Sub-function not supported in this session", 0x7F: "Service not supported in this session",
}
OBD_MODES = {0x01: "Current data", 0x02: "Freeze-frame data", 0x03: "Stored fault codes", 0x04: "Clear fault codes",
             0x07: "Pending fault codes", 0x09: "Vehicle information", 0x0A: "Permanent fault codes"}
STATUS_BITS = ["test failed", "failed this cycle", "pending", "confirmed", "not completed since clear",
               "failed since clear", "not completed this cycle", "warning light on"]


def dtc_text(b: bytes) -> str:
    """Two bytes (OBD-II) or three (UDS) -> 'P0133' or 'P0133-1A'."""
    letter = "PCBU"[(b[0] >> 6) & 3]
    text = f"{letter}{(b[0] >> 4) & 3}{b[0] & 0xF:X}{b[1] >> 4:X}{b[1] & 0xF:X}"
    return text + (f"-{b[2]:02X}" if len(b) > 2 else "")


def dtc_status(s: int) -> str:
    on = [n for i, n in enumerate(STATUS_BITS) if s >> i & 1]
    return ", ".join(on) if on else "no flags set"


def _obd_pid(pid: int, a: bytes) -> Optional[str]:
    try:
        if pid == 0x0C:
            return f"Engine speed {(a[0] * 256 + a[1]) / 4:g} rpm"
        if pid == 0x0D:
            return f"Vehicle speed {a[0]} km/h"
        if pid == 0x05:
            return f"Coolant temperature {a[0] - 40} °C"
        if pid == 0x0F:
            return f"Intake air temperature {a[0] - 40} °C"
        if pid == 0x04:
            return f"Engine load {a[0] * 100 / 255:.0f} %"
        if pid == 0x11:
            return f"Throttle position {a[0] * 100 / 255:.0f} %"
        if pid == 0x2F:
            return f"Fuel level {a[0] * 100 / 255:.0f} %"
    except IndexError:
        pass
    return None


def is_diag_id(can_id: int, ext: bool) -> bool:
    if not ext:
        return 0x7DF <= can_id <= 0x7EF or 0x7E0 <= can_id <= 0x7E7
    return (can_id >> 16) in (0x18DA, 0x18DB, 0x18DC, 0x18DD) or (can_id >> 8) in (0x18DAF1, 0x18DB33)


def describe_payload(p: bytes) -> list[str]:
    """What a complete diagnostic message (after ISO-TP) says, as short lines."""
    if not p:
        return []
    sid = p[0]
    if sid == 0x7F and len(p) >= 3:
        return [f"No: {UDS_SERVICES.get(p[1], f'service 0x{p[1]:02X}')} refused. {NRC.get(p[2], f'code 0x{p[2]:02X}')}"]
    if sid in (1, 2, 9) and len(p) >= 2:
        return [f"Asks: {OBD_MODES[sid]}, item 0x{p[1]:02X}"]
    if sid in (0x03, 0x04, 0x07, 0x0A):
        return [f"Asks: {OBD_MODES[sid]}"]
    if sid - 0x40 in OBD_MODES and sid < 0x4B:
        mode = sid - 0x40
        if mode in (3, 7, 0x0A):
            body = p[1:]
            n = body[0] if body and len(body) % 2 == 1 else None
            codes = body[1:] if n is not None else body
            out = [dtc_text(codes[i:i + 2]) for i in range(0, len(codes) - 1, 2) if codes[i:i + 2] != b"\0\0"]
            return [f"{OBD_MODES[mode]}: " + (", ".join(out) if out else "none")]
        if mode == 4:
            return ["Fault codes cleared"]
        if mode == 1 and len(p) >= 3:
            v = _obd_pid(p[1], p[2:])
            return [v or f"Current data, item 0x{p[1]:02X}: {p[2:].hex(' ')}"]
        return [f"{OBD_MODES[mode]} answer: {p[1:].hex(' ')}"]
    if sid == 0x19 and len(p) >= 2:
        subs = {1: "how many", 2: "list by status", 3: "snapshot ids", 4: "snapshot", 6: "extended data", 0x0A: "all supported"}
        return [f"Asks: read fault codes ({subs.get(p[1], f'sub-function 0x{p[1]:02X}')})"]
    if sid == 0x59 and len(p) >= 3:
        mask, recs = p[2], p[3:]
        out = []
        for i in range(0, len(recs) - 3, 4):
            out.append(f"{dtc_text(recs[i:i + 3])}: {dtc_status(recs[i + 3])}")
        head = f"Fault codes (mask 0x{mask:02X}): " + (f"{len(out)} found" if out else "none")
        return [head] + out
    if sid in UDS_SERVICES:
        extra = f" {p[1:5].hex(' ')}" if len(p) > 1 else ""
        return [f"Asks: {UDS_SERVICES[sid]}{extra}"]
    if sid - 0x40 in UDS_SERVICES:
        return [f"Yes: {UDS_SERVICES[sid - 0x40]}" + (f" {p[1:5].hex(' ')}" if len(p) > 1 else "")]
    return [f"Diagnostic data starting 0x{sid:02X}"]


def describe(can_id: int, ext: bool, data: bytes) -> Optional[str]:
    """One-line meaning of a single frame, or None if it is not a protocol we know."""
    if is_diag_id(can_id, ext) and data:
        pci = data[0] >> 4
        if pci == 0:
            n = data[0] & 0xF
            lines = describe_payload(bytes(data[1:1 + n]))
            return lines[0] if lines else None
        if pci == 1 and len(data) >= 3:
            total = ((data[0] & 0xF) << 8) | data[1]
            lines = describe_payload(bytes(data[2:]))
            return f"Long message ({total} bytes) starts. " + (lines[0] if lines else "")
        if pci == 2:
            return f"Long message, part {data[0] & 0xF}"
        if pci == 3:
            return "Flow control: " + {0: "go on", 1: "wait", 2: "overflow"}.get(data[0] & 0xF, "?")
        return None
    if ext and not is_diag_id(can_id, ext) and j1939_parts(can_id)["pgn"] in PGN_NAMES:
        return describe_j1939(can_id)
    return None


# ------------------------------------------------------------------ whole messages from many frames
@dataclass
class _Open:
    total: int
    buf: bytearray
    next_sn: int
    ts: float


@dataclass
class Assembler:
    """Joins ISO-TP first/consecutive frames into whole diagnostic messages."""
    open: dict = field(default_factory=dict)

    def feed(self, ts: float, channel: str, can_id: int, ext: bool, data: bytes) -> Optional[dict]:
        if not data or not is_diag_id(can_id, ext):
            return None
        key = (channel, can_id, ext)
        pci = data[0] >> 4
        if pci == 0:
            n = data[0] & 0xF
            return self._done(ts, channel, can_id, ext, bytes(data[1:1 + n])) if 0 < n <= 7 else None
        if pci == 1 and len(data) >= 3:
            total = ((data[0] & 0xF) << 8) | data[1]
            self.open[key] = _Open(total, bytearray(data[2:]), 1, ts)
            return None
        if pci == 2 and key in self.open:
            o = self.open[key]
            if data[0] & 0xF != o.next_sn:
                del self.open[key]
                return None
            o.buf += data[1:]
            o.next_sn = (o.next_sn + 1) & 0xF
            if len(o.buf) >= o.total:
                del self.open[key]
                return self._done(o.ts, channel, can_id, ext, bytes(o.buf[:o.total]))
        return None

    @staticmethod
    def _done(ts, channel, can_id, ext, payload) -> dict:
        lines = describe_payload(payload)
        return {"ts": round(ts, 6), "ch": channel, "id": can_id, "ext": ext, "length": len(payload), "data": payload.hex(),
                "text": lines[0] if lines else "", "details": lines[1:]}


def assemble(frames: Iterable, limit: int = 2000) -> list[dict]:
    """frames: objects with ts, channel, can_id, ext, data."""
    a, out = Assembler(), []
    for f in frames:
        m = a.feed(f.ts, f.channel, f.can_id, f.ext, f.data)
        if m:
            out.append(m)
            if len(out) >= limit:
                break
    return out
