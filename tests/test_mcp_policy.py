import pytest

from core.bus import Bus, BusError
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
    return bus, store, policy, build_mcp(bus, store, policy)


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
