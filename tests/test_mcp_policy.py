import pytest

from core.bus import Bus, BusError
from core.files import FileSession
from core.mcp_server import SEND_PER_SECOND, McpPolicy, build_mcp
from core.models import ChannelConfig
from core.store import Store
from mcp.server.mcpserver.exceptions import ToolError


@pytest.fixture
def parts(tmp_path):
    store = Store(tmp_path)
    store.set_setting("mcp.enabled", "true")
    bus = Bus()
    policy = McpPolicy(store)
    return bus, store, policy, build_mcp(bus, store, policy, FileSession(bus, tmp_path))


async def call(mcp, name, args):
    return await mcp.call_tool(name, args)


def test_paths_must_stay_in_data_dir(parts, tmp_path):
    _, _, policy, _ = parts
    with pytest.raises(BusError):
        policy.resolve("../outside.asc", must_exist=False)
    with pytest.raises(BusError):
        policy.resolve("C:/Windows/win.ini", must_exist=False)
    assert policy.resolve("logs/a.asc", must_exist=False).parent.name == "logs"


@pytest.mark.parametrize("name", ["../evil.asc", "a.asc:stream", "x.txt", "", "a b.asc", "con\x00.asc"])
async def test_log_filename_rules(parts, name):
    bus, _, _, mcp = parts
    bus.set_channel(ChannelConfig(name="c", channel="p1"))
    bus.start()
    try:
        with pytest.raises(ToolError):
            await call(mcp, "start_logging", {"filename": name})
    finally:
        bus.stop()


async def test_send_frame_gate_and_rate_limit(parts):
    bus, store, policy, mcp = parts
    bus.set_channel(ChannelConfig(name="c", channel="p2", listen_only=False))
    bus.start()
    try:
        with pytest.raises(ToolError, match="transmitting from MCP is off"):
            await call(mcp, "send_frame", {"channel": "c", "can_id": 1, "data_hex": "00"})
        store.set_setting("mcp.allow_transmit", "true")
        for _ in range(SEND_PER_SECOND):
            await call(mcp, "send_frame", {"channel": "c", "can_id": 1, "data_hex": "00"})
        with pytest.raises(ToolError, match="rate limit"):
            await call(mcp, "send_frame", {"channel": "c", "can_id": 1, "data_hex": "00"})
    finally:
        bus.stop()


async def test_disabled_tool_and_off_switch(parts):
    _, store, _, mcp = parts
    store.set_setting("mcp.tools_disabled", '["get_statistics"]')
    with pytest.raises(ToolError, match="switched off"):
        await call(mcp, "get_statistics", {})
    store.set_setting("mcp.enabled", "false")
    with pytest.raises(ToolError, match="switched off"):
        await call(mcp, "list_channels", {})


async def test_send_signal_has_the_same_gates_as_send_frame(parts):
    from core.mcp_server import DB_SUFFIXES  # noqa: F401
    bus, store, policy, mcp = parts
    from pathlib import Path
    bus.set_channel(ChannelConfig(name="c", channel="p3", listen_only=False))
    bus.load_database("c", str(Path(__file__).resolve().parents[1] / "samples" / "demo.dbc"))
    bus.start()
    try:
        with pytest.raises(ToolError, match="transmitting from MCP is off"):
            await call(mcp, "send_signal", {"channel": "c", "message": "VehicleSpeed", "signals": {"Speed": 10}})
        store.set_setting("mcp.allow_transmit", "true")
        await call(mcp, "send_signal", {"channel": "c", "message": "VehicleSpeed", "signals": {"Speed": 10}})
        with pytest.raises(ToolError, match="cannot encode"):
            await call(mcp, "send_signal", {"channel": "c", "message": "VehicleSpeed", "signals": {"Speed": 9999}})
    finally:
        bus.stop()


# ----------------------------------------------------------------- analysing recordings
import json  # noqa: E402
import shutil  # noqa: E402
from pathlib import Path  # noqa: E402

SAMPLES = Path(__file__).resolve().parents[1] / "samples"


def payload(result):
    """The structured content of a tool result as a dict."""
    block = result[0] if isinstance(result, (list, tuple)) else result
    content = getattr(block, "content", None) or block
    first = content[0] if isinstance(content, (list, tuple)) else content
    return json.loads(getattr(first, "text", first))


async def test_a_recording_can_be_listed_opened_summarised_and_a_signal_read(parts, tmp_path):
    bus, store, _, mcp = parts
    bus.set_channel(ChannelConfig(name="can1", interface="virtual", channel="rec"))
    bus.load_database("can1", str(SAMPLES / "demo.dbc"))
    (tmp_path / "imports").mkdir(exist_ok=True)
    shutil.copy(SAMPLES / "demo_drive.mf4", tmp_path / "imports" / "demo_drive.mf4")

    listing = payload(await call(mcp, "list_files", {}))
    assert [f["name"] for f in listing["uploads"]] == ["demo_drive.mf4"] and listing["open"] is None

    opened = payload(await call(mcp, "open_file", {"name": "demo_drive.mf4"}))
    assert opened["frames"] == 2130 and opened["format"] == "MDF4"

    ov = payload(await call(mcp, "file_overview", {}))
    assert ov["file"]["name"] == "demo_drive.mf4" and ov["total_messages"] == 3
    assert {m["name"] for m in ov["messages"]} >= {"EngineData", "VehicleSpeed"}

    sig = payload(await call(mcp, "file_signal", {"name": "EngineData.EngineSpeed", "max_points": 50}))
    assert sig["samples"] == 1500 and 400 < sig["min"] < 700 and 3000 < sig["max"] < 3600
    assert sig["min"] <= sig["mean"] <= sig["max"] and 2 <= len(sig["points"]) <= 50
    assert sig["first"][0] < 0.1 and sig["last"][0] > 29


async def test_file_tools_cannot_reach_outside_the_data_folder(parts, tmp_path):
    bus, store, _, mcp = parts
    (tmp_path.parent / "outside.asc").write_text("x")
    for name in ["../outside.asc", r"..\outside.asc", str(tmp_path.parent / "outside.asc"), "nothing.asc"]:
        with pytest.raises(ToolError, match="No such file"):
            await call(mcp, "open_file", {"name": name})


async def test_file_tools_explain_themselves_when_nothing_is_open_or_wrong_type(parts, tmp_path):
    bus, store, _, mcp = parts
    with pytest.raises(ToolError, match="No file is open"):
        await call(mcp, "file_overview", {})
    with pytest.raises(ToolError, match="No file is open"):
        await call(mcp, "file_signal", {"name": "A.B"})
    (tmp_path / "imports").mkdir(exist_ok=True)
    shutil.copy(SAMPLES / "demo.dbc", tmp_path / "imports" / "demo.dbc")
    with pytest.raises(ToolError, match="not a recording"):
        await call(mcp, "open_file", {"name": "demo.dbc"})


async def test_file_tools_obey_the_off_switches(parts):
    _, store, _, mcp = parts
    store.set_setting("mcp.tools_disabled", '["open_file"]')
    with pytest.raises(ToolError, match="switched off"):
        await call(mcp, "open_file", {"name": "x.asc"})
    store.set_setting("mcp.enabled", "false")
    with pytest.raises(ToolError, match="switched off"):
        await call(mcp, "list_files", {})
