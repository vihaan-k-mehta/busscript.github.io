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


def _terminal_streams() -> None:
    """The exe has no console of its own, but it can still be used from a terminal (busscript doctor, --mcp-stdio):
    take the pipes the caller gave us, or else write into the console that started us."""
    try:
        import ctypes
        import msvcrt
        k32 = ctypes.windll.kernel32
        k32.GetStdHandle.restype = ctypes.c_void_p
        k32.CreateFileW.restype = ctypes.c_void_p
        attached = False
        for name, std, mode, fd in (("stdin", -10, "r", 0), ("stdout", -11, "w", 1), ("stderr", -12, "w", 2)):
            if getattr(sys, name) is not None:
                continue
            h = k32.GetStdHandle(std)
            if h in (None, 0, ctypes.c_void_p(-1).value):          # nothing was handed to us: use the console
                if not attached:
                    attached = bool(k32.AttachConsole(-1))
                if not attached:
                    continue
                h = k32.CreateFileW("CONIN$" if name == "stdin" else "CONOUT$", 0xC0000000, 3, None, 3, 0, None)
                if h in (None, ctypes.c_void_p(-1).value):
                    continue
            f = os.fdopen(msvcrt.open_osfhandle(h, os.O_RDONLY if mode == "r" else 0), mode, encoding="utf-8",
                          errors="replace", buffering=1 if mode == "w" else -1, newline=None if mode == "r" else chr(10))
            setattr(sys, name, f)
    except Exception:
        pass


def _alert(text: str) -> None:
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, text, "Busscript", 0x10)
    except Exception:
        pass


if __name__ == "__main__":
    multiprocessing.freeze_support()
    if len(sys.argv) > 1:
        _terminal_streams()
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
