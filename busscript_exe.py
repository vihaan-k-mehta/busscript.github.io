"""Entry point for the single-file Windows program (built by scripts/make_exe.py). Same as: python -m core

The exe has no console window, so errors are written to busscript.log in the data folder and also shown in a message box."""
import multiprocessing
import os
import sys
import traceback
from pathlib import Path


def _quiet_streams() -> Path:
    data = Path(os.environ.get("BUSSCRIPT_DATA") or Path.home() / "busscript-data")
    data.mkdir(parents=True, exist_ok=True)
    log = data / "busscript.log"
    if sys.stdout is None or sys.stderr is None:        # windowed program: nowhere to print
        stream = open(log, "a", encoding="utf-8", buffering=1)
        sys.stdout = sys.stdout or stream
        sys.stderr = sys.stderr or stream
    return log


def _alert(text: str) -> None:
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, text, "Busscript", 0x10)
    except Exception:
        pass


if __name__ == "__main__":
    multiprocessing.freeze_support()
    log = _quiet_streams()
    try:
        from core.__main__ import main
        main()
    except SystemExit:
        raise
    except BaseException:
        log.write_text(traceback.format_exc(), encoding="utf-8")
        _alert("Busscript could not start.\n\n" + traceback.format_exc().strip().splitlines()[-1] + f"\n\nDetails: {log}")
        sys.exit(1)
