from __future__ import annotations

import argparse
import webbrowser
from pathlib import Path

import uvicorn

from .bus import Bus
from .demo import start_demo_traffic
from .models import ChannelConfig
from .server import create_app
from .store import Store

SAMPLE_DBC = Path(__file__).resolve().parents[1] / "samples" / "demo.dbc"


def main() -> None:
    ap = argparse.ArgumentParser(prog="core", description="Busscript: a local CAN bus tool with an MCP server")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--data-dir", type=Path, default=None)
    ap.add_argument("--demo", action="store_true", help="virtual bus with synthetic traffic and the sample DBC")
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--mcp-stdio", action="store_true", help="serve MCP over stdio instead of the web app")
    args = ap.parse_args()

    store = Store(args.data_dir)
    bus = Bus(int(store.get_settings().get("ring_buffer_size", "500000")))
    for cfg, dbs in store.load_channels():
        bus.set_channel(cfg)
        for d in dbs:
            try:
                bus.load_database(cfg.name, d)
            except Exception:
                pass
    if args.demo:
        cfg = ChannelConfig(name="demo", interface="virtual", channel="demo0", listen_only=False)
        bus.set_channel(cfg)
        bus.load_database("demo", str(SAMPLE_DBC))
        start_demo_traffic("demo0")

    if args.mcp_stdio:
        from .mcp_server import McpPolicy, build_mcp
        store.set_setting("mcp.enabled", "true")
        build_mcp(bus, store, McpPolicy(store)).run("stdio")
        return

    token = store.token()
    app = create_app(bus, store, token)
    url = f"http://127.0.0.1:{args.port}/?token={token}"
    print(f"Busscript: {url}")
    print(f"MCP endpoint: http://127.0.0.1:{args.port}/mcp  (Authorization: Bearer <token>; enable in Settings)")
    if not args.no_browser:
        webbrowser.open(url)
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
