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


def free_port(preferred: int, tries: int = 20) -> int:
    """The preferred port, or the next free one (a second copy of Busscript may already be running)."""
    for p in range(preferred, preferred + tries):
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", p)) != 0:
                return p
    return preferred
