"""Open Busscript in its own window, like a normal program, instead of a browser tab.

It uses Microsoft Edge (every Windows 10 and 11 PC has it) or Chrome in "app mode": no address bar, no tabs, its own
taskbar icon. The window gets its own private profile folder, so it never touches the user's normal browsing.
When the window is closed, Busscript quits."""
from __future__ import annotations

import os
import socket
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable, Optional


def find_browser() -> Optional[Path]:
    roots = [os.environ.get(k) for k in ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA")]
    for sub in (r"Microsoft\Edge\Application\msedge.exe", r"Google\Chrome\Application\chrome.exe"):
        for r in roots:
            if r and (Path(r) / sub).exists():
                return Path(r) / sub
    return None


def open_app_window(url: str, profile: Path, on_closed: Callable[[], None]) -> bool:
    """Open the window. Returns False if there is no suitable browser (the caller then uses the default browser).
    on_closed runs once, when the user closes the window."""
    exe = find_browser()
    if exe is None:
        return False
    profile.mkdir(parents=True, exist_ok=True)
    try:
        proc = subprocess.Popen(
            [str(exe), f"--app={url}", f"--user-data-dir={profile}", "--window-size=1440,900", "--no-first-run",
             "--no-default-browser-check", "--disable-features=Translate,msEdgeSidebar", "--disable-sync"],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        return False

    def watch() -> None:
        started = time.time()
        proc.wait()
        # An instant exit means the browser handed the job to a window that was already open: keep running.
        if time.time() - started > 5:
            on_closed()

    threading.Thread(target=watch, daemon=True, name="app-window").start()
    return True


ICON = Path(__file__).resolve().parents[1] / "assets" / "busscript.ico"


PANES = {"trace": "Trace", "stat": "Bus statistic", "data": "Data", "write": "Write", "graphic": "Graphic"}
_popouts: dict = {}                 # pane id -> its window (module level so the JavaScript bridge does not expose it)


class _Bridge:
    """What the page may ask the program to do: open a pane in a real window of its own, or close that window again."""

    def __init__(self, base: str) -> None:
        self._base = base

    def pop_out(self, pane: str, x: int, y: int, w: int, h: int) -> bool:
        import webview
        if pane not in PANES or pane in _popouts:
            return False
        win = webview.create_window(f"Busscript - {PANES[pane]}", f"{self._base}/?pane={pane}", js_api=self,
                                    x=int(x), y=int(y), width=int(w), height=int(h), min_size=(360, 240), text_select=True)
        _popouts[pane] = win
        win.events.closed += lambda *_: _popouts.pop(pane, None)
        return True

    def close_popout(self, pane: str) -> bool:
        win = _popouts.pop(pane, None)
        if win is None:
            return False
        win.destroy()
        return True


def run_native_window(url: str, storage: Path, debug_port: Optional[int] = None) -> bool:
    """Show Busscript in a real window of its own (the Windows WebView2 engine embedded in this program, so there is
    no browser around it). Blocks until the window is closed and returns True. Returns False without opening anything
    if the window cannot be made (no pywebview, or no WebView2 on this PC). Panes dragged out of the window open as
    more windows of the same kind; closing the main window closes them too."""
    try:
        import webview
    except Exception:
        return False
    storage.mkdir(parents=True, exist_ok=True)
    webview.settings["ALLOW_DOWNLOADS"] = True            # the Analyze page saves files
    if debug_port:
        webview.settings["REMOTE_DEBUGGING_PORT"] = debug_port   # lets the tests drive this very window
    try:
        base = url.split("?", 1)[0].rstrip("/")
        main = webview.create_window("Busscript", url, width=1440, height=900, min_size=(900, 560), text_select=True,
                                     js_api=_Bridge(base))

        def closing() -> None:                              # the main window going away ends the whole program
            for w in list(_popouts.values()):
                try:
                    w.destroy()
                except Exception:
                    pass
            _popouts.clear()
        main.events.closed += closing
        webview.start(private_mode=False, storage_path=str(storage), icon=str(ICON) if ICON.exists() else None)
    except Exception:
        return False
    return True


def free_port(preferred: int, tries: int = 20) -> int:
    """The preferred port, or the next free one (a second copy of Busscript may already be running)."""
    for p in range(preferred, preferred + tries):
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", p)) != 0:
                return p
    return preferred
