import socket
import threading
import time
from pathlib import Path

import pytest
import uvicorn
from fastapi.testclient import TestClient

from core import cli, quickstart
from core.bus import Bus
from core.server import create_app
from core.store import Store

SAMPLES = Path(__file__).resolve().parents[1] / "samples"
DIAG = str(SAMPLES / "demo_diag.asc")
TOKEN = "secret-token"


def run(capsys, *argv):
    code = cli.run([str(a) for a in argv])
    out = capsys.readouterr()
    return code, out.out, out.err


def test_describe(capsys):
    code, out, _ = run(capsys, "describe", "0x7E8", "04", "41", "0C", "1A", "F8")
    assert code == 0 and "Engine speed 1726 rpm" in out
    code, out, _ = run(capsys, "describe", "0x123", "00")
    assert "Nothing special" in out


def test_stats_and_json(capsys):
    code, out, _ = run(capsys, "stats", DIAG)
    assert code == 0 and "0x7E8" in out and "14 frames" in out
    code, out, _ = run(capsys, "stats", DIAG, "--json")
    assert '"count": 6' in out


def test_find_and_exit_code(capsys):
    code, out, _ = run(capsys, "find", DIAG, "id == 0x7E8", "--max", "2")
    assert code == 0 and "6 matching frame(s), showing the first 2" in out
    code, out, _ = run(capsys, "find", DIAG, "id == 0x555")
    assert code == 1 and "0 matching" in out


def test_diag_lists_fault_codes(capsys):
    code, out, _ = run(capsys, "diag", DIAG)
    assert code == 0 and "P0133" in out


def test_convert_filters_and_refuses_overwrite(capsys, tmp_path):
    out_file = tmp_path / "o.csv"
    code, out, _ = run(capsys, "convert", DIAG, out_file, "--where", "id == 0x7E8")
    assert code == 0 and "Saved 6 frames" in out
    assert len(out_file.read_text().splitlines()) == 7
    code, _, err = run(capsys, "convert", DIAG, out_file)
    assert code == 2 and "--force" in err
    code, _, err = run(capsys, "convert", DIAG, tmp_path / "o.txt")
    assert code == 2 and "ending" in err


def test_friendly_errors(capsys):
    code, _, err = run(capsys, "stats", "no-such-file.asc")
    assert code == 2 and "no file called" in err
    code, _, err = run(capsys, "find", DIAG, "id ?? 5")
    assert code == 2 and err.startswith("busscript:")
    code, _, err = run(capsys, "describe", "zzz")
    assert code == 2


def test_global_options_work_after_the_command(capsys, tmp_path):
    code, out, _ = run(capsys, "doctor", "--data-dir", tmp_path)
    assert "Libraries" in out and str(tmp_path) in out


def test_help_lists_commands(capsys):
    code, out, _ = run(capsys, "help")
    assert code == 0 and "busscript mcp" in out


# ---------------------------------------------------------------- the one-prompt quickstart
def test_quickstart_text():
    p = quickstart.prompt("http://127.0.0.1:8765/mcp", TOKEN)
    assert "claude mcp add --scope user --transport http busscript http://127.0.0.1:8765/mcp" in p
    assert f"Bearer {TOKEN}" in p and "list_channels" in p and "untrusted" in p and "send_frame" in p
    s = quickstart.stdio_server()
    assert s["command"] and "--mcp-stdio" in s["args"]


def test_quickstart_endpoint_needs_the_token(tmp_path):
    bus = Bus()
    with TestClient(create_app(bus, Store(tmp_path), TOKEN), base_url="http://127.0.0.1:8765") as c:
        assert c.get("/api/mcp/quickstart").status_code == 401
        r = c.get("/api/mcp/quickstart", headers={"Authorization": f"Bearer {TOKEN}"}).json()
        assert "http://127.0.0.1:8765/mcp" in r["prompt"] and TOKEN in r["command"]
        assert "busscript" in r["stdio"]["mcpServers"]
    bus.stop()


# ---------------------------------------------------------------- talking to a running Busscript
@pytest.fixture
def live(tmp_path):
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    store = Store(tmp_path)
    bus = Bus()
    server = uvicorn.Server(uvicorn.Config(create_app(bus, store, store.token()), host="127.0.0.1", port=port, log_level="error"))
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    yield port, tmp_path
    server.should_exit = True
    t.join(timeout=5)
    bus.stop()


def test_live_status_and_start_stop(capsys, live):
    port, data = live
    base = ["--port", port, "--data-dir", data]
    code, out, _ = run(capsys, "status", *base)
    assert code == 0 and "Measurement: stopped" in out
    code, out, _ = run(capsys, "start", *base)
    assert code == 0 and "running" in out
    code, out, _ = run(capsys, "stop", *base)
    assert "stopped" in out


def test_live_not_running_is_a_friendly_error(capsys, tmp_path):
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    code, _, err = run(capsys, "status", "--port", port, "--data-dir", tmp_path)
    assert code == 2 and "not running" in err


def test_mcp_prints_prompt_with_the_real_token(capsys, live):
    port, data = live
    code, out, _ = run(capsys, "mcp", "--port", port, "--data-dir", data)
    assert code == 0 and Store(data).token() in out and f"127.0.0.1:{port}/mcp" in out
