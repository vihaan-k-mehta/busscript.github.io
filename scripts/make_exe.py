"""Build dist/Busscript.exe: one file, no Python or libraries needed on the target PC.

    python scripts/make_exe.py

Needs PyInstaller (pip install pyinstaller) and a built UI (ui/dist). Windows only.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SEP = ";" if sys.platform == "win32" else ":"


def main() -> None:
    if not (ROOT / "ui" / "dist" / "index.html").exists():
        raise SystemExit("ui/dist is missing: run `npm run build` in ui/ first")
    work = ROOT / "build"
    shutil.rmtree(work, ignore_errors=True)
    cmd = [
        sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onefile", "--noconsole", "--name", "Busscript", "--icon", str(ROOT / "assets" / "busscript.ico"),
        "--distpath", str(ROOT / "dist"), "--workpath", str(work), "--specpath", str(work),
        "--add-data", f"{ROOT / 'ui' / 'dist'}{SEP}ui/dist",
        "--add-data", f"{ROOT / 'samples'}{SEP}samples",
        "--add-data", f"{ROOT / 'core' / 'schema.sql'}{SEP}core",
        "--add-data", f"{ROOT / 'assets'}{SEP}assets",
        "--collect-all", "webview", "--collect-all", "clr_loader", "--collect-all", "pythonnet",
        "--collect-submodules", "can", "--collect-submodules", "uvicorn", "--collect-submodules", "mcp.server", "--collect-submodules", "mcp.shared", "--hidden-import", "mcp.types",
        "--collect-all", "asammdf", "--collect-all", "cantools",
        "--copy-metadata", "python-can", "--copy-metadata", "mcp", "--copy-metadata", "fastapi", "--copy-metadata", "uvicorn",
        "--copy-metadata", "cantools", "--copy-metadata", "asammdf", "--copy-metadata", "pyserial", "--copy-metadata", "pywebview",
        "--hidden-import", "serial", "--hidden-import", "serial.tools.list_ports",
        "--exclude-module", "matplotlib", "--exclude-module", "tkinter", "--exclude-module", "PyQt5", "--exclude-module", "PySide6",
        "--exclude-module", "IPython", "--exclude-module", "pytest",
        str(ROOT / "busscript_exe.py"),
    ]
    subprocess.run(cmd, check=True, cwd=ROOT)
    exe = ROOT / "dist" / "Busscript.exe"
    print(f"built {exe} ({exe.stat().st_size / 1e6:.0f} MB)")


def smoke(port: int = 8797) -> None:
    """Run the exe alone in an empty folder (no Python, no project files) and use it over HTTP."""
    import json
    import os
    import tempfile
    import time
    import urllib.error
    import urllib.request

    import socket
    with socket.socket() as probe:
        if probe.connect_ex(("127.0.0.1", port)) == 0:
            raise SystemExit(f"port {port} is already in use (an old Busscript still running?)")
    folder = Path(tempfile.mkdtemp(prefix="busscript exe test "))
    shutil.copy2(ROOT / "dist" / "Busscript.exe", folder / "Busscript.exe")
    data = folder / "data"
    env = {k: v for k, v in os.environ.items() if k.upper() not in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV")}
    env["PATH"] = os.environ.get("SystemRoot", r"C:\Windows") + r"\System32"       # no Python on the path
    env["BUSSCRIPT_DATA"] = str(data)
    proc = subprocess.Popen([str(folder / "Busscript.exe"), "--no-browser", "--port", str(port)], cwd=folder, env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        base = f"http://127.0.0.1:{port}"
        t0 = time.time()
        for _ in range(120):
            if proc.poll() is not None:
                raise SystemExit("exe smoke test FAILED: it exited: " + (proc.stdout.read() or "")[-2000:])
            try:
                if urllib.request.urlopen(base + "/", timeout=2).status == 200:
                    break
            except (urllib.error.URLError, OSError):
                time.sleep(0.5)
        else:
            raise SystemExit("exe smoke test FAILED: no answer in 60 s")
        started = time.time() - t0
        auth = {"Authorization": "Bearer " + (data / "token").read_text().strip()}
        get = lambda path: json.load(urllib.request.urlopen(urllib.request.Request(base + path, headers=auth), timeout=10))
        assert get("/api/state")["demo"] is True, "first run should start the demo"
        assert get("/api/doctor")["ready"] is True, get("/api/doctor")["problems"]
        assert b"<title>" in urllib.request.urlopen(base + "/", timeout=5).read()
        for sample in ("demo_drive.mf4", "demo_drive.asc"):
            body = (ROOT / "samples" / sample).read_bytes()
            up = urllib.request.Request(base + f"/api/files/{sample}", data=body, method="PUT",
                                        headers={**auth, "Content-Type": "application/octet-stream"})
            name = json.load(urllib.request.urlopen(up, timeout=30))["name"]
            op = urllib.request.Request(base + "/api/files/open", data=json.dumps({"name": name}).encode(), method="POST",
                                        headers={**auth, "Content-Type": "application/json"})
            assert json.load(urllib.request.urlopen(op, timeout=60))["frames"] == 2130, sample
        assert get("/api/statistics/report")["rows"], "statistics"
        print(f"exe smoke test passed: answered after {started:.0f} s, UI + API + MF4/ASC import + statistics, no Python installed")
    finally:
        # a one-file exe runs the real program as a child process: end the whole tree, not just the outer file
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True)
        time.sleep(1)
        shutil.rmtree(folder, ignore_errors=True)


if __name__ == "__main__":
    main()
    smoke()
