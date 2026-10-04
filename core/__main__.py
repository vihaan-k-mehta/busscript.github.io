from __future__ import annotations

import argparse
import threading
import time
import webbrowser
from pathlib import Path

import uvicorn

from .bus import Bus
from .demo import start_demo_traffic
from .models import ChannelConfig
from .server import create_app
from .store import Store
from .window import free_port, open_app_window, run_native_window

SAMPLE_DBC = Path(__file__).resolve().parents[1] / "samples" / "demo.dbc"


def main() -> None:
    ap = argparse.ArgumentParser(prog="core", description="Busscript: a local CAN bus tool with an MCP server")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--data-dir", type=Path, default=None)
    ap.add_argument("--demo", action="store_true", help="virtual bus with synthetic traffic and the sample DBC")
    ap.add_argument("--live", action="store_true", help="never start the demo; use only your own channels")
    ap.add_argument("--browser", action="store_true", help="open in your normal browser instead of a window of its own")
    ap.add_argument("--debug-port", type=int, default=None, help=argparse.SUPPRESS)
    ap.add_argument("--no-browser", action="store_true", help="do not open any window")
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
    saved = store.load_channels()
    use_demo = args.demo or (not args.live and not saved)   # first run with nothing set up: show the demo
    if use_demo:
        cfg = ChannelConfig(name="demo", interface="virtual", channel="demo0", listen_only=False)
        bus.set_channel(cfg)
        bus.load_database("demo", str(SAMPLE_DBC))
        bus.demo_stop = start_demo_traffic("demo0")
        bus.demo = True

    if args.mcp_stdio:
        from .files import FileSession
        from .mcp_server import McpPolicy, build_mcp
        store.set_setting("mcp.enabled", "true")
        build_mcp(bus, store, McpPolicy(store), FileSession(bus, store.data_dir)).run("stdio")
        return

    token = store.token()
    app = create_app(bus, store, token)
    port = free_port(args.port)
    url = f"http://127.0.0.1:{port}/?token={token}"
    print(f"Busscript: {url}")
    print(f"MCP endpoint: http://127.0.0.1:{port}/mcp  (Authorization: Bearer <token>; enable in Settings)")
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))

    def wait_started() -> bool:      # the window must never open on an error page
        for _ in range(300):
            if server.started:
                return True
            time.sleep(0.1)
        return False

    if args.no_browser:
        server.run()
    elif args.browser:
        threading.Thread(target=lambda: wait_started() and webbrowser.open(url), daemon=True, name="open-browser").start()
        server.run()
    else:
        # The server runs in the background while this thread shows the window; closing the window ends the program.
        runner = threading.Thread(target=server.run, daemon=True, name="server")
        runner.start()
        if wait_started():
            if not run_native_window(url, store.data_dir / "window", args.debug_port):
                # No embedded engine on this PC: use Edge or Chrome as an app window, else the normal browser.
                gone = threading.Event()
                if open_app_window(url, store.data_dir / "window", gone.set):
                    gone.wait()
                else:
                    webbrowser.open(url)
                    runner.join()
        server.should_exit = True
        runner.join(timeout=5)
    bus.stop()


if __name__ == "__main__":
    main()
