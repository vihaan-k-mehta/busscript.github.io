"""Proof of concept: Busscript's Trace, Data and Graph as a real native Qt window (no web engine).

Not part of the shipped program. It answers one question: would a native rewrite be faster or smaller?
Run from the Qt environment:  .venv-qt\\Scripts\\python -m core.qt_poc [--bench 10] [--shot out.png]

It talks to the same Bus object the web app uses, in the same process (no server, no token, no JSON)."""
from __future__ import annotations

import argparse
import ctypes
import os
import sys
import threading
import time
from collections import deque
from pathlib import Path

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QPointF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (QApplication, QDockWidget, QLabel, QMainWindow, QTableView, QTableWidget,
                               QTableWidgetItem, QVBoxLayout, QWidget)

from .bus import Bus
from .demo import start_demo_traffic
from .models import ChannelConfig, Frame

SAMPLE_DBC = Path(__file__).resolve().parents[1] / "samples" / "demo.dbc"
KEEP = 200_000          # rows the trace keeps
COLUMNS = ["Time", "Chn", "ID", "Name", "Dir", "DLC", "Data"]


class TraceModel(QAbstractTableModel):
    """A virtual table: only the rows on screen are ever turned into text."""

    def __init__(self) -> None:
        super().__init__()
        self.rows: list[Frame] = []
        self._pending: deque[Frame] = deque()
        self._lock = threading.Lock()

    def push(self, f: Frame) -> None:           # called from the bus threads
        with self._lock:
            self._pending.append(f)

    def flush(self) -> int:                     # called by the GUI timer
        with self._lock:
            fresh, self._pending = list(self._pending), deque()
        if not fresh:
            return 0
        over = len(self.rows) + len(fresh) - KEEP
        if over > 0:
            self.beginRemoveRows(QModelIndex(), 0, over - 1)
            del self.rows[:over]
            self.endRemoveRows()
        first = len(self.rows)
        self.beginInsertRows(QModelIndex(), first, first + len(fresh) - 1)
        self.rows.extend(fresh)
        self.endInsertRows()
        return len(fresh)

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()) -> int:
        return len(COLUMNS)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role == Qt.DisplayRole and orientation == Qt.Horizontal:
            return COLUMNS[section]
        return None

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        f = self.rows[index.row()]
        c = index.column()
        if role == Qt.DisplayRole:
            if c == 0:
                return f"{f.ts:.4f}"
            if c == 1:
                return f.channel
            if c == 2:
                return f"0x{f.can_id:08X}" if f.ext else f"0x{f.can_id:03X}"
            if c == 3:
                return f.name or ""
            if c == 4:
                return f.direction
            if c == 5:
                return str(f.dlc)
            return " ".join(f"{b:02X}" for b in f.data)
        if role == Qt.ForegroundRole and f.error:
            return QColor("#b70032")
        if role == Qt.FontRole and c in (0, 2, 6):
            return QFont("Consolas", 9)
        return None


class Graph(QWidget):
    """A line plot of one signal, drawn directly with the painter."""

    def __init__(self, bus: Bus, name: str) -> None:
        super().__init__()
        self.bus, self.name, self.pts = bus, name, []
        self.setMinimumHeight(140)

    def refresh(self) -> None:
        self.pts = self.bus.history(self.name, 0.0, 2000)
        self.update()

    def paintEvent(self, _) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), QColor("#ffffff"))
        p.setPen(QColor("#555"))
        p.drawText(8, 16, self.name)
        if len(self.pts) < 2:
            return
        t0, t1 = self.pts[0][0], self.pts[-1][0]
        lo, hi = min(v for _, v in self.pts), max(v for _, v in self.pts)
        span_t, span_v = max(t1 - t0, 1e-9), max(hi - lo, 1e-9)
        w, h = self.width() - 16, self.height() - 34
        path = QPainterPath()
        for i, (t, v) in enumerate(self.pts):
            pt = QPointF(8 + (t - t0) / span_t * w, 24 + h - (v - lo) / span_v * h)
            path.moveTo(pt) if i == 0 else path.lineTo(pt)
        p.setPen(QPen(QColor("#0b6b5d"), 1.6))
        p.drawPath(path)
        p.setPen(QColor("#777"))
        p.drawText(8, self.height() - 4, f"{lo:.0f} .. {hi:.0f}")


class Main(QMainWindow):
    def __init__(self, bus: Bus) -> None:
        super().__init__()
        self.bus = bus
        self.setWindowTitle("Busscript (native Qt proof of concept)")
        self.resize(1300, 800)
        self.model = TraceModel()
        bus.subscribe(self.model.push)
        self.view = QTableView()
        self.view.setModel(self.model)
        self.view.verticalHeader().setDefaultSectionSize(20)
        self.view.verticalHeader().hide()
        self.view.setSelectionBehavior(QTableView.SelectRows)
        for i, w in enumerate((90, 60, 100, 130, 40, 40, 300)):
            self.view.setColumnWidth(i, w)
        self.follow = True
        self.setCentralWidget(self._wrap("Trace", self.view))

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Signal", "Value", "Lowest", "Highest"])
        self.table.verticalHeader().hide()
        self._dock("Data", self.table, Qt.RightDockWidgetArea)

        bus.watch(["EngineData.EngineSpeed"])
        self.graph = Graph(bus, "EngineData.EngineSpeed")
        self._dock("Graph", self.graph, Qt.BottomDockWidgetArea)
        self.info = QLabel("")
        self.statusBar().addWidget(self.info)

        self.paints = 0
        self.fast = QTimer(self)
        self.fast.timeout.connect(self.tick)
        self.fast.start(33)
        self.slow = QTimer(self)
        self.slow.timeout.connect(self.slow_tick)
        self.slow.start(200)

    def _wrap(self, title: str, w: QWidget) -> QWidget:
        box = QWidget()
        lay = QVBoxLayout(box)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(w)
        return box

    def _dock(self, title: str, w: QWidget, area) -> None:
        d = QDockWidget(title, self)            # docks can be dragged, floated, tabbed, closed: built in
        d.setWidget(w)
        self.addDockWidget(area, d)

    def tick(self) -> None:
        self.paints += 1
        if self.model.flush() and self.follow:
            self.view.scrollToBottom()
        self.graph.refresh()

    def slow_tick(self) -> None:
        rows = self.bus.signal_values()
        self.table.setRowCount(len(rows))
        for i, r in enumerate(rows):
            for j, v in enumerate((f"{r['message']}.{r['signal']}", r["value"], r.get("min"), r.get("max"))):
                self.table.setItem(i, j, QTableWidgetItem("" if v is None else str(v)))
        self.info.setText(f"{len(self.model.rows):,} rows   {self.bus.state()['frames_buffered']:,} buffered")


def rss_mb() -> float:
    class PMC(ctypes.Structure):
        _fields_ = [("cb", ctypes.c_ulong), ("PageFaultCount", ctypes.c_ulong), ("PeakWorkingSetSize", ctypes.c_size_t),
                    ("WorkingSetSize", ctypes.c_size_t), ("a", ctypes.c_size_t), ("b", ctypes.c_size_t),
                    ("c", ctypes.c_size_t), ("d", ctypes.c_size_t), ("PagefileUsage", ctypes.c_size_t),
                    ("PeakPagefileUsage", ctypes.c_size_t)]
    pmc = PMC()
    pmc.cb = ctypes.sizeof(pmc)
    k32, ps = ctypes.WinDLL("kernel32"), ctypes.WinDLL("psapi")
    k32.GetCurrentProcess.restype = ctypes.c_void_p
    ps.GetProcessMemoryInfo.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong]
    ps.GetProcessMemoryInfo(k32.GetCurrentProcess(), ctypes.byref(pmc), pmc.cb)
    return pmc.WorkingSetSize / 1e6


def flood(bus: Bus, per_second: int, stop: threading.Event, counter: list) -> None:
    """Pretend a very busy bus: frames go straight into the Bus, the same entry point real adapters use."""
    t0, n = time.perf_counter(), 0
    while not stop.is_set():
        due = int((time.perf_counter() - t0) * per_second)
        while n < due:
            n += 1
            bus._ingest(Frame(ts=time.perf_counter() - t0, channel="demo", can_id=0x100 + n % 8, ext=False, fd=False,
                              direction="rx", dlc=8, data=(n % (1 << 63)).to_bytes(8, "little")))
        counter[0] = n
        time.sleep(0.002)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bench", type=float, default=0, help="seconds to run a flood test, then print the numbers and quit")
    ap.add_argument("--rate", type=int, default=5000, help="frames per second for the flood test")
    ap.add_argument("--shot", default="", help="save a picture of the window after 3 s and quit")
    a = ap.parse_args()

    bus = Bus()
    bus.set_channel(ChannelConfig(name="demo", interface="virtual", channel="demo0", listen_only=False))
    bus.load_database("demo", str(SAMPLE_DBC))
    start_demo_traffic("demo0")
    bus.start()
    app = QApplication(sys.argv)
    win = Main(bus)
    win.show()
    stop, counter = threading.Event(), [0]
    t_start = time.perf_counter()
    if a.bench:
        threading.Thread(target=flood, args=(bus, a.rate, stop, counter), daemon=True).start()

        def report() -> None:
            secs = time.perf_counter() - t_start
            win.grab().save("qt_poc_bench.png")
            print(f"flood {a.rate}/s for {secs:.1f} s: {counter[0]:,} frames sent into the bus")
            print(f"trace rows held: {len(win.model.rows):,} (of {KEEP:,} kept)   GUI ticks: {win.paints} "
                  f"({win.paints / secs:.1f}/s, 30/s is the target)")
            print(f"memory: {rss_mb():.0f} MB")
            stop.set()
            app.quit()
        QTimer.singleShot(int(a.bench * 1000), report)
    if a.shot:
        def shot() -> None:
            win.grab().save(a.shot)
            app.quit()
        QTimer.singleShot(3500, shot)
    app.exec()
    bus.stop()


if __name__ == "__main__":
    main()
