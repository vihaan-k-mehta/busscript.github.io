"""'Check my setup': what Busscript needs on this PC to talk to a real CAN adapter, and whether each piece is there.

Everything is read-only: it looks for libraries and driver files, it never installs or opens a bus."""
from __future__ import annotations

import ctypes
import ctypes.util
import importlib.metadata as md
import sys
from pathlib import Path

LIBRARIES = [  # (package, what it does, needed to start?)
    ("python-can", "Talks to CAN adapters", True),
    ("cantools", "Reads database files (DBC, KCD, SYM, ARXML, CDD)", True),
    ("fastapi", "Serves the window you are looking at", True),
    ("uvicorn", "Runs the local server", True),
    ("mcp", "Lets an AI assistant use Busscript", True),
    ("asammdf", "Opens MF4 (MDF4) recordings", True),
    ("pyserial", "Serial-port adapters (SLCAN, CANable, Arduino)", False),
]

# adapter family -> (python-can interface, driver file names to look for, where to get it)
DRIVERS = [
    ("PEAK PCAN", "pcan", ["PCANBasic"], "Install the PEAK-System driver package (free download from PEAK-System)."),
    ("Vector VN / VH", "vector", ["vxlapi64", "vxlapi"], "Install the Vector Driver Setup (the XL driver) from Vector."),
    ("Kvaser", "kvaser", ["canlib32"], "Install Kvaser Drivers for Windows (free from Kvaser)."),
    ("IXXAT", "ixxat", ["vcinpl2", "vcinpl"], "Install the IXXAT VCI driver from HMS Networks."),
    ("National Instruments", "nican", ["nican"], "Install NI-CAN from National Instruments."),
    ("NI-XNET", "nixnet", ["nixnet"], "Install NI-XNET from National Instruments."),
    ("Intrepid neoVI", "neovi", ["icsneo40", "icsneoc"], "Install Intrepid's Vehicle Spy or its driver package."),
    ("SYS TEC", "systec", ["USBCAN64", "usbcan32", "USBCAN32"], "Install the SYS TEC USB-CANmodul driver."),
]


def _version(pkg: str) -> str | None:
    try:
        return md.version(pkg)
    except md.PackageNotFoundError:
        return None


def _driver_found(names: list[str]) -> str | None:
    if sys.platform != "win32":
        return None
    for n in names:
        path = ctypes.util.find_library(n)
        if path:
            return path
    return None


def serial_ports() -> list[dict]:
    try:
        from serial.tools import list_ports
        return [{"port": p.device, "label": p.description} for p in list_ports.comports()]
    except Exception:
        return []


def check(data_dir: Path, port: int | None = None) -> dict:
    libs = []
    for pkg, why, required in LIBRARIES:
        v = _version(pkg)
        libs.append({"name": pkg, "why": why, "required": required, "version": v, "ok": v is not None,
                     "fix": None if v else "Run start.bat again, or: pip install -r requirements.txt"})
    drivers = []
    for label, iface, names, fix in DRIVERS:
        found = _driver_found(names)
        drivers.append({"name": label, "interface": iface, "found": bool(found), "path": found, "fix": None if found else fix})
    ports = serial_ports()
    writable = True
    try:
        probe = data_dir / ".write-test"
        probe.write_text("ok")
        probe.unlink()
    except OSError:
        writable = False
    problems = [f"{x['name']} is missing" for x in libs if x["required"] and not x["ok"]]
    if not writable:
        problems.append("Busscript cannot write to its data folder")
    return {
        "python": sys.version.split()[0],
        "libraries": libs,
        "drivers": drivers,
        "serial_ports": ports,
        "data_dir": str(data_dir),
        "data_dir_writable": writable,
        "ready": not problems,
        "problems": problems,
        "tip": "No adapter? The Virtual bus and the demo work with nothing plugged in.",
    }
